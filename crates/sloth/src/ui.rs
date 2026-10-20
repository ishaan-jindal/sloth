//! Inline confirmation, editor hand-off, regenerate nudges.

use crate::diff::Digest;
use std::env;
use std::fs;
use std::io::{self, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Accept,
    Edit,
    Regenerate,
    Cancel,
}

fn read_line() -> Option<String> {
    let mut line = String::new();
    match io::stdin().read_line(&mut line) {
        Ok(0) => None,
        Ok(_) => Some(line.trim().to_string()),
        Err(_) => None,
    }
}

pub fn ask_action() -> Action {
    loop {
        print!("Accept [y], edit [e], regenerate [r], cancel [q]: ");
        let _ = io::stdout().flush();
        let Some(input) = read_line() else {
            return Action::Cancel;
        };
        match input.to_ascii_lowercase().as_str() {
            "y" | "accept" => return Action::Accept,
            "e" | "edit" => return Action::Edit,
            "r" | "regenerate" => return Action::Regenerate,
            "q" | "n" | "cancel" => return Action::Cancel,
            _ => {
                println!("Unrecognised choice. Use y, e, r or q.");
            }
        }
    }
}

pub fn ask_release() -> Action {
    loop {
        print!("Accept release [y], cancel [q]: ");
        let _ = io::stdout().flush();
        let Some(input) = read_line() else {
            return Action::Cancel;
        };
        match input.to_ascii_lowercase().as_str() {
            "y" | "accept" => return Action::Accept,
            "q" | "n" | "cancel" => return Action::Cancel,
            _ => println!("Unrecognised choice. Use y or q."),
        }
    }
}

pub fn print_proposal(
    digest: &Digest,
    message: &str,
    used_fallback: bool,
    stripped_refs: usize,
    scope_warning: Option<&str>,
    names_file_path: bool,
    body_note: Option<&str>,
) {
    println!();
    println!("{}", digest.render_summary());
    println!();
    println!("--- proposed message ---");
    println!("{message}");
    println!("------------------------");
    if used_fallback {
        eprintln!(
            "sloth: warning: model output was rejected or unavailable; this is a deterministic \
             template message."
        );
    }
    if stripped_refs > 0 {
        eprintln!(
            "sloth: note: removed {stripped_refs} fabricated issue reference(s) from the model output."
        );
    }
    if let Some(warning) = scope_warning {
        eprintln!("sloth: note: {warning}.");
    }
    if names_file_path {
        eprintln!("sloth: note: subject names a file path.");
    }
    if let Some(note) = body_note {
        eprintln!("sloth: note: {note}.");
    }
    println!();
}

pub fn print_release_proposal(tag: &str, section: &str) {
    println!();
    println!("Proposed release tag: {tag}");
    println!("--- CHANGELOG.md section ---");
    println!("{section}");
    println!("----------------------------");
    println!();
}

/// Best-effort terminal restoration. Sloth never puts the terminal into raw
/// mode itself, but an editor or a hook may leave it altered. This is called
/// before spawn and on every exit path after the editor.
pub fn restore_terminal() {
    let _ = Command::new("stty")
        .arg("sane")
        .stdin(Stdio::inherit())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

fn editor_command() -> Option<String> {
    for key in ["VISUAL", "EDITOR"] {
        if let Ok(value) = env::var(key) {
            let value = value.trim();
            if !value.is_empty() {
                return Some(value.to_string());
            }
        }
    }
    None
}

fn temp_path(suffix: &str) -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    env::temp_dir().join(format!("sloth-{}-{nanos}-{suffix}.txt", std::process::id()))
}

/// Path used for `git commit -F`. Also printed verbatim in `--dry-run`.
pub fn message_file_path() -> PathBuf {
    temp_path("commit-message")
}

pub fn write_message_file(message: &str) -> Result<PathBuf, String> {
    let path = message_file_path();
    let mut content = message.trim_end().to_string();
    content.push('\n');
    fs::write(&path, content)
        .map_err(|err| format!("cannot write the message file {}: {err}", path.display()))?;
    Ok(path)
}

pub fn remove_message_file(path: &std::path::Path) {
    let _ = fs::remove_file(path);
}

/// Open `$VISUAL`/`$EDITOR`. Returns `None` when the user kept the current
/// message (including when no editor is configured).
pub fn edit_message(current: &str) -> Result<Option<String>, String> {
    let Some(editor) = editor_command() else {
        println!(
            "No editor configured. Set $VISUAL or $EDITOR, then try [e] again; press Enter to \
             keep the current message."
        );
        let _ = read_line();
        return Ok(None);
    };

    let path = temp_path("edit");
    let mut initial = current.trim_end().to_string();
    initial.push('\n');
    fs::write(&path, initial)
        .map_err(|err| format!("cannot write the edit file {}: {err}", path.display()))?;

    let mut parts = editor.split_whitespace();
    let Some(program) = parts.next() else {
        let _ = fs::remove_file(&path);
        return Ok(None);
    };
    let mut command = Command::new(program);
    command.args(parts).arg(&path);

    restore_terminal();
    let status = command.status();
    restore_terminal();

    let content = fs::read_to_string(&path);
    let _ = fs::remove_file(&path);

    match status {
        Ok(status) if !status.success() => {
            eprintln!(
                "sloth: editor exited with {}; keeping the current message.",
                status.code().unwrap_or(-1)
            );
            return Ok(None);
        }
        Ok(_) => {}
        Err(err) => {
            eprintln!("sloth: cannot start the editor {program:?}: {err}");
            return Ok(None);
        }
    }

    let content = match content {
        Ok(content) => content.trim_end().to_string(),
        Err(err) => {
            eprintln!("sloth: cannot read the edited message: {err}");
            return Ok(None);
        }
    };
    if content.trim().is_empty() {
        eprintln!("sloth: edited message is empty; keeping the current message.");
        return Ok(None);
    }
    Ok(Some(content))
}

pub fn shell_quote(value: &str) -> String {
    if !value.is_empty()
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '/' | '.' | '_' | '-' | ':' | '@'))
    {
        return value.to_string();
    }
    format!("'{}'", value.replace('\'', "'\\''"))
}

pub fn commit_command(amend: bool, path: &str) -> String {
    let amend = if amend { "--amend " } else { "" };
    format!("git commit {amend}-F {}", shell_quote(path))
}
