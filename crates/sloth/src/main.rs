use clap::{Parser, Subcommand};
use sloth::diff::Digest;
use sloth::git::Repo;
use sloth::model::{self, Client};
use sloth::msg::{self, Check, Conformance};
use sloth::release::{self, Bump, TagScheme};
use sloth::server;
use sloth::strategy::{self, Level};
use sloth::style::{self, Style};
use sloth::ui;
use std::fs;
use std::process::ExitCode;

const ENV_HELP: &str = "\
Environment:
  SLOTH_MODEL         model name (default: qwen3:0.6b; reasoning is disabled)
  SLOTH_URL           Ollama base URL (default: http://127.0.0.1:11434)
  SLOTH_STYLE         force a commit shape: conventional, gitmoji, ticket, freeform
  SLOTH_ALLOW_REMOTE  set to 1 to permit a non-loopback endpoint (privacy opt-in)

Sloth never pushes. It prints the push command for you to run.";

#[derive(Parser)]
#[command(
    name = "sloth",
    version,
    about = "Local-only commit messages and release changelogs",
    after_help = ENV_HELP
)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Propose a commit message for the staged change
    #[command(after_help = ENV_HELP)]
    Commit {
        /// Amend the previous commit's message instead of creating a new commit
        #[arg(long)]
        amend: bool,
        /// Print the proposal and the git command, then change nothing
        #[arg(long)]
        dry_run: bool,
    },
    /// Create a changelog section and an annotated tag
    #[command(after_help = ENV_HELP)]
    Release {
        /// Explicit MAJOR.MINOR.PATCH, required without conventional history
        #[arg(long, value_name = "VERSION")]
        version: Option<String>,
        /// Print the release and the git commands, then change nothing
        #[arg(long)]
        dry_run: bool,
    },
    /// Print the prompt, schema and mined style without calling a model
    #[command(after_help = ENV_HELP)]
    Prompt {
        /// Emit one JSON object instead of labelled sections
        #[arg(long)]
        json: bool,
    },
}

struct Config {
    model: String,
    url: String,
    style: Option<String>,
    allow_remote: bool,
}

impl Config {
    fn load() -> Result<Self, String> {
        let model = std::env::var("SLOTH_MODEL")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| model::DEFAULT_MODEL.to_string());
        let url = std::env::var("SLOTH_URL")
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| model::DEFAULT_URL.to_string());
        let style = std::env::var("SLOTH_STYLE")
            .ok()
            .map(|value| value.trim().to_ascii_lowercase())
            .filter(|value| !value.is_empty());
        if let Some(shape) = &style {
            if !style::SHAPES.contains(&shape.as_str()) {
                return Err(format!(
                    "SLOTH_STYLE must be one of {} (got {shape:?})",
                    style::SHAPES.join(", ")
                ));
            }
        }
        let allow_remote = std::env::var("SLOTH_ALLOW_REMOTE").as_deref() == Ok("1");
        Ok(Self {
            model,
            url,
            style,
            allow_remote,
        })
    }
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(message) => {
            eprintln!("sloth: {message}");
            ExitCode::FAILURE
        }
    }
}

fn run() -> Result<(), String> {
    let cli = Cli::parse();
    let config = Config::load()?;
    match cli.command {
        Command::Commit { amend, dry_run } => {
            // Fail closed before anything can reach a model: a non-loopback
            // endpoint still refuses unless SLOTH_ALLOW_REMOTE=1.
            model::validate_endpoint(&config.url, config.allow_remote)?;
            commit(&config, amend, dry_run)
        }
        Command::Release { version, dry_run } => {
            model::validate_endpoint(&config.url, config.allow_remote)?;
            release(&config, version, dry_run)
        }
        // No model is involved, so no endpoint is needed or contacted.
        Command::Prompt { json } => prompt(&config, json),
    }
}

struct Suggestion {
    message: String,
    used_fallback: bool,
    stripped_refs: usize,
    scope_warning: Option<String>,
    names_file_path: bool,
    body_note: Option<String>,
}

enum AttemptError {
    Parse(String),
    Rejected { repair: String, summary: String },
}

impl AttemptError {
    fn repair_text(&self) -> String {
        match self {
            Self::Parse(error) => format!(
                "\n\n## Correction required\n\
                 The previous response was not a valid JSON object for the schema: {error}\n\
                 Return only the JSON object required by the schema."
            ),
            Self::Rejected { repair, .. } => repair.clone(),
        }
    }

    fn summary(&self) -> String {
        match self {
            Self::Parse(error) => format!("invalid JSON: {error}"),
            Self::Rejected { summary, .. } => summary.clone(),
        }
    }
}

struct Attempt {
    message: String,
    stripped_refs: usize,
    scope_warning: Option<String>,
    names_file_path: bool,
    body_note: Option<String>,
}

fn interpret(content: &str, mined: &Style, digest: &Digest) -> Result<Attempt, AttemptError> {
    let (raw_subject, raw_body) = msg::parse_model_message(content).map_err(AttemptError::Parse)?;
    let (subject, subject_refs) = msg::strip_fabricated_refs(&raw_subject);
    let (body, body_refs) = msg::strip_fabricated_refs(&raw_body);
    let (body, body_note) = msg::prepare_body(mined, &subject, &body);
    let conformance: Conformance = msg::check_message(mined, &subject, &body);
    let failures: Vec<&Check> = conformance.blocking_failures();
    if !failures.is_empty() {
        let summary = failures
            .iter()
            .map(|failure| format!("{}: {}", failure.name, failure.detail))
            .collect::<Vec<_>>()
            .join("; ");
        let repair = msg::repair_instruction(&failures);
        return Err(AttemptError::Rejected { repair, summary });
    }
    Ok(Attempt {
        message: msg::assemble_message(&subject, &body),
        stripped_refs: subject_refs + body_refs,
        scope_warning: msg::scope_warning(mined, &subject, &conformance),
        names_file_path: msg::subject_names_file_path(&subject, &digest.files),
        body_note,
    })
}

fn fallback(mined: &Style, digest: &Digest) -> Suggestion {
    Suggestion {
        message: msg::fallback_message(mined, &digest.files),
        used_fallback: true,
        stripped_refs: 0,
        scope_warning: None,
        names_file_path: false,
        body_note: None,
    }
}

/// Print what the model is doing, then the elapsed time when it returns.
/// `label` is the whole action phrase, e.g. "asking qwen3:0.6b".
fn ask_model(
    client: &Client,
    system: &str,
    user: &str,
    schema: &serde_json::Value,
    temperature: f64,
    num_ctx: usize,
    label: &str,
) -> Result<String, String> {
    eprint!("sloth: {label}...");
    let started = std::time::Instant::now();
    let result = client.chat_with(
        system,
        user,
        Some(schema),
        temperature,
        num_ctx,
        model::NUM_PREDICT,
    );
    eprintln!(" done in {:.1}s", started.elapsed().as_secs_f64());
    result
}

fn generate(
    client: &Client,
    schema: &serde_json::Value,
    prompt: &msg::Prompt,
    num_ctx: usize,
    mined: &Style,
    digest: &Digest,
    nudge: Option<&str>,
) -> Suggestion {
    let mut user = prompt.user.clone();
    if let Some(nudge) = nudge {
        user = msg::add_nudge(&user, nudge);
    }

    let first = match ask_model(
        client,
        &prompt.system,
        &user,
        schema,
        model::TEMPERATURE,
        num_ctx,
        &format!("asking {}", client.model()),
    ) {
        Ok(content) => content,
        Err(error) => {
            eprintln!("sloth: warning: model call failed: {error}");
            return fallback(mined, digest);
        }
    };

    match interpret(&first, mined, digest) {
        Ok(attempt) => Suggestion {
            message: attempt.message,
            used_fallback: false,
            stripped_refs: attempt.stripped_refs,
            scope_warning: attempt.scope_warning,
            names_file_path: attempt.names_file_path,
            body_note: attempt.body_note,
        },
        Err(first_error) => {
            eprintln!(
                "sloth: model output failed the checks; making one repair call ({})",
                first_error.summary()
            );
            let repair_user = format!("{user}{}", first_error.repair_text());
            match ask_model(
                client,
                &prompt.system,
                &repair_user,
                schema,
                model::REPAIR_TEMPERATURE,
                num_ctx,
                "repairing the message",
            ) {
                Ok(content) => match interpret(&content, mined, digest) {
                    Ok(attempt) => Suggestion {
                        message: attempt.message,
                        used_fallback: false,
                        stripped_refs: attempt.stripped_refs,
                        scope_warning: attempt.scope_warning,
                        names_file_path: attempt.names_file_path,
                        body_note: attempt.body_note,
                    },
                    Err(second_error) => {
                        eprintln!(
                            "sloth: warning: model output rejected after one repair: {}",
                            second_error.summary()
                        );
                        fallback(mined, digest)
                    }
                },
                Err(error) => {
                    eprintln!("sloth: warning: repair call failed: {error}");
                    fallback(mined, digest)
                }
            }
        }
    }
}

/// Level C map phase. It cannot fail the run: a piece whose call fails becomes
/// a deterministic line built from its paths and hunk headers.
fn summarise(client: &Client, chunks: &[strategy::Chunk], num_ctx: usize) -> Vec<String> {
    let total = chunks.len();
    eprintln!("sloth: summarising {total} piece(s) of the diff...");
    let mut lines = Vec::with_capacity(total);
    for (index, chunk) in chunks.iter().enumerate() {
        let result = client.chat_with(
            strategy::MAP_SYSTEM,
            &chunk.map_user(),
            None,
            model::TEMPERATURE,
            num_ctx,
            strategy::MAP_LINE_TOKENS,
        );
        if let Err(error) = &result {
            eprintln!(
                "sloth: warning: piece {}/{total} could not be summarised: {error}",
                index + 1
            );
        }
        lines.push(strategy::map_line(chunk, result));
    }
    lines
}

/// `sloth prompt`: the exact material a model would receive, with no model.
fn prompt(config: &Config, json: bool) -> Result<(), String> {
    let repo = Repo::discover()?;
    let digest = Digest::build(&repo, false)?;
    let mut mined = style::mine(&repo, 300)?;
    if let Some(shape) = &config.style {
        mined.set_shape(shape)?;
    }
    let rendered = msg::build_prompt(&mined, &digest);
    let schema = msg::schema_for(&mined);
    let style = serde_json::to_value(&mined)
        .map_err(|error| format!("cannot encode the mined style: {error}"))?;

    if json {
        let payload = serde_json::json!({
            "system": rendered.system,
            "user": rendered.user,
            "schema": schema,
            "style": style,
        });
        println!(
            "{}",
            serde_json::to_string(&payload)
                .map_err(|error| format!("cannot encode the prompt as JSON: {error}"))?
        );
        return Ok(());
    }

    println!("--- SYSTEM ---");
    println!("{}", rendered.system);
    println!("--- USER ---");
    println!("{}", rendered.user);
    println!("--- SCHEMA ---");
    println!(
        "{}",
        serde_json::to_string_pretty(&schema)
            .map_err(|error| format!("cannot encode the schema: {error}"))?
    );
    println!("--- STYLE ---");
    println!(
        "{}",
        serde_json::to_string_pretty(&style)
            .map_err(|error| format!("cannot encode the mined style: {error}"))?
    );
    Ok(())
}

fn commit(config: &Config, amend: bool, dry_run: bool) -> Result<(), String> {
    let repo = Repo::discover()?;
    if !amend && !repo.has_staged_changes()? {
        return Err(
            "nothing is staged; stage changes with `git add` (or `git add -p`) first".to_string(),
        );
    }
    if amend && !repo.head_exists()? {
        return Err("cannot amend: HEAD has no commits yet".to_string());
    }

    let digest = Digest::build(&repo, amend)?;
    if digest.files.is_empty() && digest.diff.trim().is_empty() {
        return Err("nothing is staged; nothing to describe".to_string());
    }

    let mut mined = style::mine(&repo, 300)?;
    if let Some(shape) = &config.style {
        mined.set_shape(shape)?;
    }
    eprintln!(
        "sloth: convention: {} ({}, {:.0}% of {} commits); prose bodies {:.0}%, trailers {:.0}%, \
         scopes {:.0}%{}",
        mined.shape,
        mined.confidence,
        mined.match_rate() * 100.0,
        mined.n_subjects,
        mined.body_usage * 100.0,
        mined.trailer_usage * 100.0,
        mined.scope_rate * 100.0,
        if mined.forced { " [forced]" } else { "" }
    );

    let prompt = msg::build_prompt(&mined, &digest);
    let plan = strategy::plan(&prompt, &digest, model::NUM_CTX, strategy::MAX_NUM_CTX);
    // Never let the user believe they read a full diff when they did not.
    if let Some(note) = plan.report() {
        eprintln!("sloth: {note}");
    }
    if let Err(warning) = server::ensure_running(&config.url) {
        // Never block the commit on the model: the template fallback still works.
        eprintln!("sloth: warning: {warning}");
    }
    let client = Client::new(&config.url, &config.model);
    // Level C writes the subject from the map lines plus the file list and
    // diffstat, so the prompt is rebuilt over a digest whose "diff" is those
    // lines. The answer is judged by the same checks either way.
    let prompt = if plan.level == Level::C {
        let lines = summarise(&client, &plan.chunks, plan.num_ctx);
        let reduced = Digest {
            files: digest.files.clone(),
            diff: lines.join("\n"),
        };
        msg::build_prompt(&mined, &reduced)
    } else {
        prompt
    };
    let schema = msg::schema_for(&mined);
    let mut nudge_index = 0usize;
    let mut suggestion = generate(
        &client,
        &schema,
        &prompt,
        plan.num_ctx,
        &mined,
        &digest,
        None,
    );

    if dry_run {
        ui::print_proposal(
            &digest,
            &suggestion.message,
            suggestion.used_fallback,
            suggestion.stripped_refs,
            suggestion.scope_warning.as_deref(),
            suggestion.names_file_path,
            suggestion.body_note.as_deref(),
        );
        println!(
            "dry run: nothing was committed. Would run: {}",
            ui::commit_command(amend, &ui::message_file_path().display().to_string())
        );
        return Ok(());
    }

    loop {
        ui::print_proposal(
            &digest,
            &suggestion.message,
            suggestion.used_fallback,
            suggestion.stripped_refs,
            suggestion.scope_warning.as_deref(),
            suggestion.names_file_path,
            suggestion.body_note.as_deref(),
        );
        match ui::ask_action() {
            ui::Action::Accept => {
                let path = ui::write_message_file(&suggestion.message)?;
                let result = repo.commit_file(amend, &path);
                ui::remove_message_file(&path);
                return match result {
                    Ok(0) => {
                        println!("committed.");
                        Ok(())
                    }
                    Ok(code) => Err(format!(
                        "git commit failed (exit {code}); the hook output above is verbatim and \
                         no commit was created by sloth"
                    )),
                    Err(error) => Err(error),
                };
            }
            ui::Action::Edit => {
                if let Some(edited) = ui::edit_message(&suggestion.message)? {
                    suggestion = Suggestion {
                        message: edited,
                        used_fallback: false,
                        stripped_refs: 0,
                        scope_warning: None,
                        names_file_path: false,
                        body_note: None,
                    };
                }
            }
            ui::Action::Regenerate => {
                let nudge = msg::NUDGES[nudge_index % msg::NUDGES.len()];
                nudge_index += 1;
                suggestion = generate(
                    &client,
                    &schema,
                    &prompt,
                    plan.num_ctx,
                    &mined,
                    &digest,
                    Some(nudge),
                );
            }
            ui::Action::Cancel => {
                println!("cancelled; nothing was committed.");
                return Ok(());
            }
        }
    }
}

fn release(config: &Config, explicit_version: Option<String>, dry_run: bool) -> Result<(), String> {
    let repo = Repo::discover()?;
    if !repo.head_exists()? {
        return Err("the repository has no commits; there is nothing to release".to_string());
    }
    let tags = repo.tags()?;
    let inference = release::infer_scheme(&tags);

    let (scheme, latest) = match inference.scheme.clone() {
        Some(scheme) => (scheme, inference.latest.clone()),
        None => {
            if explicit_version.is_none() {
                if tags.is_empty() {
                    return Err(
                        "no tags found; cannot infer a release scheme — pass --version <v>"
                            .to_string(),
                    );
                }
                return Err(format!(
                    "found {} tag(s) but none parse as a version; cannot infer a release scheme — \
                     pass --version <v>",
                    tags.len()
                ));
            }
            (TagScheme::default_v(), None)
        }
    };
    let latest_tag = latest.as_ref().map(|tag| tag.tag.clone());
    eprintln!(
        "sloth: tag scheme {}; latest {}; ignored {} non-version tag(s)",
        scheme.render_tag(&release::Version::new(1, 2, 3)),
        latest_tag.as_deref().unwrap_or("(none)"),
        inference.ignored
    );

    let commits = repo.commits_since(latest_tag.as_deref())?;
    let new_version = match explicit_version {
        Some(input) => release::parse_explicit_version(&input, &scheme)?,
        None => {
            let mined = style::mine(&repo, 300)?;
            if mined.type_vocab.is_empty() {
                return Err(
                    "repository history has no conventional commit types; cannot infer a bump — \
                     pass --version <v>"
                        .to_string(),
                );
            }
            let bump = release::bump_for_commits(&commits);
            if bump == Bump::None {
                return Err(format!(
                    "no release-worthy commits since {}; pass --version to force a release",
                    latest_tag.as_deref().unwrap_or("the start of history")
                ));
            }
            let Some(latest) = latest.as_ref() else {
                return Err(
                    "no release tag found; cannot apply a bump — pass --version <v>".to_string(),
                );
            };
            bump.apply(&latest.version)
        }
    };

    let new_tag = scheme.render_tag(&new_version);
    if repo.tag_exists(&new_tag)? {
        return Err(format!(
            "tag {new_tag} already exists; choose another version with --version"
        ));
    }

    let changelog_path = repo.root().join("CHANGELOG.md");
    let existing = match fs::read_to_string(&changelog_path) {
        Ok(content) => Some(content),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
        Err(error) => return Err(format!("cannot read {}: {error}", changelog_path.display())),
    };
    let heading = release::detect_heading_style(existing.as_deref());
    let date = release::today_utc();
    let groups = release::group_commits(&commits);

    let client = Client::new(&config.url, &config.model);
    let highlights = if commits.is_empty() {
        None
    } else {
        if let Err(warning) = server::ensure_running(&config.url) {
            eprintln!("sloth: warning: {warning}");
        }
        match release::generate_highlights(&client, &commits, &new_tag) {
            Ok(highlights) => Some(highlights),
            Err(error) => {
                eprintln!("sloth: warning: highlights omitted: {error}");
                None
            }
        }
    };
    let version_label = scheme.render_version(&new_version);
    let section = release::render_section_with_style(
        heading,
        &version_label,
        &date,
        &groups,
        highlights.as_deref(),
    );
    let content = match existing.as_deref() {
        Some(old) => {
            let inserted = release::insert_section(old, &section);
            release::update_link_refs(
                &inserted,
                &version_label,
                &new_tag,
                latest_tag.as_deref(),
                repo.remote_origin().as_deref(),
            )
        }
        None => release::new_changelog(&section),
    };

    ui::print_release_proposal(&new_tag, &section);
    let tag_message = format!("Release {new_tag}");
    let tag_command = format!(
        "git tag -a {} -m {}",
        ui::shell_quote(&new_tag),
        ui::shell_quote(&tag_message)
    );
    if dry_run {
        println!("Would write {}", changelog_path.display());
        println!("Would run: {tag_command}");
        println!(
            "Then push with: git push origin {}",
            ui::shell_quote(&new_tag)
        );
        return Ok(());
    }

    if ui::ask_release() == ui::Action::Cancel {
        println!("cancelled; no changelog was written and no tag was created.");
        return Ok(());
    }

    fs::write(&changelog_path, content)
        .map_err(|error| format!("cannot write {}: {error}", changelog_path.display()))?;
    if let Err(error) = repo.create_tag(&new_tag, &tag_message) {
        return Err(format!(
            "wrote {}, but creating the tag failed: {error}. The tag was not created.",
            changelog_path.display()
        ));
    }
    println!("wrote {}", changelog_path.display());
    println!("created annotated tag {new_tag}");
    println!(
        "never pushed by sloth; run: git push origin {}",
        ui::shell_quote(&new_tag)
    );
    Ok(())
}
