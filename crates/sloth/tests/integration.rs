//! End-to-end (but offline) check against a throwaway git repository.

use sloth::diff::Digest;
use sloth::git::Repo;
use sloth::release::{bump_for_commits, Bump};
use sloth::style;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

/// A throwaway repository, isolated from the user's git config.
fn repo_dir(tag: &str) -> PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir = std::env::temp_dir().join(format!("sloth-{tag}-{}-{nanos}", std::process::id()));
    fs::create_dir_all(&dir).expect("create fixture dir");
    git_env(&dir, &["init", "-q"]);
    git_env(&dir, &["config", "user.email", "sloth@example.test"]);
    git_env(&dir, &["config", "user.name", "Sloth Test"]);
    git_env(&dir, &["config", "commit.gpgsign", "false"]);
    dir
}

fn git(dir: &Path, args: &[&str]) {
    let output = Command::new("git")
        .args(args)
        .current_dir(dir)
        .output()
        .expect("run git");
    assert!(
        output.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&output.stderr)
    );
}

fn git_env(dir: &Path, args: &[&str]) {
    // Keep the user's global config out of the fixture.
    let output = Command::new("git")
        .args(args)
        .current_dir(dir)
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .output()
        .expect("run git");
    assert!(
        output.status.success(),
        "git {:?} failed: {}",
        args,
        String::from_utf8_lossy(&output.stderr)
    );
}

#[test]
fn mines_style_and_bump_from_a_real_repository() {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock")
        .as_nanos();
    let dir =
        std::env::temp_dir().join(format!("sloth-integration-{}-{nanos}", std::process::id()));
    fs::create_dir_all(&dir).expect("create fixture dir");

    git_env(&dir, &["init", "-q"]);
    git_env(&dir, &["config", "user.email", "sloth@example.test"]);
    git_env(&dir, &["config", "user.name", "Sloth Test"]);
    git_env(&dir, &["config", "commit.gpgsign", "false"]);

    fs::write(dir.join("api.rs"), "pub fn one() {}\n").expect("write file");
    git(&dir, &["add", "api.rs"]);
    git(&dir, &["commit", "-q", "-m", "feat(api): add endpoint"]);

    fs::write(
        dir.join("api.rs"),
        "pub fn one() {}\npub fn two(input: &str) -> bool { input.is_empty() }\n",
    )
    .expect("write file");
    git(&dir, &["add", "api.rs"]);
    git(
        &dir,
        &[
            "commit",
            "-q",
            "-m",
            "fix(api): handle empty input",
            "-m",
            "Signed-off-by: Sloth Test <sloth@example.test>",
        ],
    );

    let repo = Repo::at(&dir);
    let mined = style::mine(&repo, 300).expect("mine style");
    assert_eq!(mined.shape, "conventional");
    assert_eq!(mined.confidence, "high");
    assert_eq!(mined.type_vocab.get("feat"), Some(&1));
    assert_eq!(mined.type_vocab.get("fix"), Some(&1));
    assert_eq!(mined.scope_vocab.get("api"), Some(&2));
    // The only body is a trailer, so it is not a prose body.
    assert_eq!(mined.body_usage, 0.0);
    assert_eq!(mined.trailer_usage, 0.5);

    let commits = repo.commits_since(None).expect("read commits");
    assert_eq!(commits.len(), 2);
    assert_eq!(bump_for_commits(&commits), Bump::Minor);

    fs::remove_dir_all(&dir).expect("remove fixture");
}

#[test]
fn the_digest_uses_no_context_lines() {
    let dir = repo_dir("digest");
    let before: String = (1..=20).map(|line| format!("line {line}\n")).collect();
    fs::write(dir.join("numbered.txt"), &before).expect("write file");
    git(&dir, &["add", "numbered.txt"]);
    git(
        &dir,
        &["commit", "-q", "-m", "feat(docs): add numbered lines"],
    );

    let after = before.replace("line 10\n", "line ten\n");
    fs::write(dir.join("numbered.txt"), after).expect("write file");
    git(&dir, &["add", "numbered.txt"]);

    let digest = Digest::build(&Repo::at(&dir), false).expect("build digest");
    let added = digest
        .diff
        .lines()
        .filter(|line| line.starts_with('+') && !line.starts_with("+++"))
        .count();
    let removed = digest
        .diff
        .lines()
        .filter(|line| line.starts_with('-') && !line.starts_with("---"))
        .count();
    assert_eq!((added, removed), (1, 1), "{}", digest.diff);
    assert!(
        !digest.diff.lines().any(|line| line.starts_with(' ')),
        "context lines leaked into the digest:\n{}",
        digest.diff
    );
    assert!(digest.diff.contains("@@ -10"), "{}", digest.diff);

    fs::remove_dir_all(&dir).expect("remove fixture");
}

#[test]
fn prompt_json_emits_every_half_without_a_model() {
    let dir = repo_dir("prompt");
    fs::write(dir.join("api.rs"), "pub fn one() {}\n").expect("write file");
    git(&dir, &["add", "api.rs"]);
    git(&dir, &["commit", "-q", "-m", "feat(api): add endpoint"]);

    fs::write(dir.join("api.rs"), "pub fn one() {}\npub fn two() {}\n").expect("write file");
    git(&dir, &["add", "api.rs"]);

    // `prompt` resolves and loads no model at all: a bogus SLOTH_MODEL must not
    // matter, which is what keeps this command usable on a machine with no GGUF.
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_sloth"))
            .args(args)
            .current_dir(&dir)
            .env("SLOTH_MODEL", "/nonexistent/sloth-test-model.gguf")
            .output()
            .expect("run the sloth binary")
    };

    let output = run(&["prompt", "--json"]);
    assert!(
        output.status.success(),
        "sloth prompt --json failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let parsed: serde_json::Value =
        serde_json::from_slice(&output.stdout).expect("stdout is one JSON object");
    for key in ["system", "user", "schema", "style"] {
        assert!(parsed.get(key).is_some(), "missing {key}: {parsed}");
    }
    assert!(parsed["user"].as_str().expect("user").contains("Diff:"));
    assert!(parsed["schema"]["required"]
        .as_array()
        .expect("schema required")
        .contains(&serde_json::json!("subject")));
    assert_eq!(parsed["style"]["shape"], "conventional");

    let text = run(&["prompt"]);
    assert!(text.status.success());
    let text = String::from_utf8_lossy(&text.stdout);
    for heading in [
        "--- SYSTEM ---",
        "--- USER ---",
        "--- SCHEMA ---",
        "--- STYLE ---",
    ] {
        assert!(text.contains(heading), "missing {heading}");
    }

    fs::remove_dir_all(&dir).expect("remove fixture");
}
