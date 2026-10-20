//! End-to-end (but offline) check against a throwaway git repository.

use sloth::git::Repo;
use sloth::release::{bump_for_commits, Bump};
use sloth::style;
use std::fs;
use std::path::Path;
use std::process::Command;
use std::time::{SystemTime, UNIX_EPOCH};

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
