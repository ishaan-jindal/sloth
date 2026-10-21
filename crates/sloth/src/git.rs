//! All git interaction. Shells out to `git`; never links a git library.

use std::fmt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

#[derive(Clone, Debug, Default)]
pub struct Commit {
    pub sha: String,
    pub subject: String,
    pub body: String,
}

#[derive(Debug)]
pub struct GitError {
    pub command: String,
    pub status: Option<i32>,
    pub stderr: String,
}

impl fmt::Display for GitError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let status = self
            .status
            .map(|code| code.to_string())
            .unwrap_or_else(|| "spawn error".to_string());
        if self.stderr.trim().is_empty() {
            write!(f, "git {} failed (exit {status})", self.command)
        } else {
            write!(
                f,
                "git {} failed (exit {status}): {}",
                self.command,
                self.stderr.trim_end()
            )
        }
    }
}

pub struct Repo {
    root: PathBuf,
}

impl Repo {
    /// Resolve the current directory to the root of a git work tree.
    pub fn discover() -> Result<Self, String> {
        let cwd = std::env::current_dir()
            .map_err(|err| format!("cannot read current directory: {err}"))?;
        let inside = Command::new("git")
            .args(["rev-parse", "--is-inside-work-tree"])
            .current_dir(&cwd)
            .output()
            .map_err(|err| format!("cannot run git: {err}"))?;
        let inside_text = String::from_utf8_lossy(&inside.stdout);
        if !inside.status.success() || inside_text.trim() != "true" {
            return Err(
                "not inside a git work tree; run sloth from a repository checkout".to_string(),
            );
        }
        let top = Command::new("git")
            .args(["rev-parse", "--show-toplevel"])
            .current_dir(&cwd)
            .output()
            .map_err(|err| format!("cannot run git: {err}"))?;
        if !top.status.success() {
            return Err("cannot find the repository root with `git rev-parse`".to_string());
        }
        let root = String::from_utf8_lossy(&top.stdout).trim().to_string();
        if root.is_empty() {
            return Err("git reported an empty repository root".to_string());
        }
        Ok(Self {
            root: PathBuf::from(root),
        })
    }

    /// A repo rooted at a known path, used by tests and integration helpers.
    pub fn at(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    fn output(&self, args: &[&str]) -> Result<(i32, String, String), GitError> {
        let output = Command::new("git")
            .args(args)
            .current_dir(&self.root)
            .output()
            .map_err(|err| GitError {
                command: args.join(" "),
                status: None,
                stderr: err.to_string(),
            })?;
        Ok((
            output.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&output.stdout).to_string(),
            String::from_utf8_lossy(&output.stderr).to_string(),
        ))
    }

    /// Run git and fail on a non-zero exit.
    pub fn run(&self, args: &[&str]) -> Result<String, GitError> {
        let (code, stdout, stderr) = self.output(args)?;
        if code != 0 {
            return Err(GitError {
                command: args.join(" "),
                status: Some(code),
                stderr,
            });
        }
        Ok(stdout)
    }

    fn status(&self, args: &[&str]) -> Result<(i32, String, String), GitError> {
        self.output(args)
    }

    fn parse_commits(raw: &str) -> Vec<Commit> {
        let mut commits = Vec::new();
        for record in raw.split('\x1e') {
            let record = record.strip_prefix('\n').unwrap_or(record);
            if record.trim().is_empty() {
                continue;
            }
            let mut parts = record.splitn(3, '\x1f');
            let sha = parts.next().unwrap_or("").trim().to_string();
            let subject = parts.next().unwrap_or("").to_string();
            let body = parts.next().unwrap_or("").trim().to_string();
            if sha.is_empty() {
                continue;
            }
            commits.push(Commit { sha, subject, body });
        }
        commits
    }

    /// Recent non-merge commits, newest first, with subjects and bodies.
    pub fn recent_commits(&self, limit: usize) -> Result<Vec<Commit>, String> {
        let limit_arg = format!("-n{limit}");
        let raw = self
            .run(&[
                "log",
                "--no-merges",
                &limit_arg,
                "--format=%H%x1f%s%x1f%b%x1e",
            ])
            .map_err(|err| err.to_string())?;
        Ok(Self::parse_commits(&raw))
    }

    /// Commits in `<tag>..HEAD`, or all of HEAD when `since` is None.
    pub fn commits_since(&self, since: Option<&str>) -> Result<Vec<Commit>, String> {
        let range = since
            .map(|tag| format!("{tag}..HEAD"))
            .unwrap_or_else(|| "HEAD".to_string());
        let raw = self
            .run(&["log", "--no-merges", "--format=%H%x1f%s%x1f%b%x1e", &range])
            .map_err(|err| err.to_string())?;
        Ok(Self::parse_commits(&raw))
    }

    pub fn tags(&self) -> Result<Vec<String>, String> {
        let raw = self.run(&["tag", "--list"]).map_err(|e| e.to_string())?;
        Ok(raw
            .lines()
            .map(|line| line.trim().to_string())
            .filter(|line| !line.is_empty())
            .collect())
    }

    pub fn has_staged_changes(&self) -> Result<bool, String> {
        let (code, _, stderr) = self
            .status(&["diff", "--cached", "--quiet"])
            .map_err(|e| e.to_string())?;
        match code {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(format!(
                "cannot inspect the index with `git diff --cached --quiet`: {}",
                stderr.trim_end()
            )),
        }
    }

    pub fn head_exists(&self) -> Result<bool, String> {
        let (code, _, _) = self
            .status(&["rev-parse", "--verify", "--quiet", "HEAD"])
            .map_err(|e| e.to_string())?;
        Ok(code == 0)
    }

    fn parent_exists(&self) -> Result<bool, String> {
        let (code, _, _) = self
            .status(&["rev-parse", "--verify", "--quiet", "HEAD^"])
            .map_err(|e| e.to_string())?;
        Ok(code == 0)
    }

    fn staged_base(&self, amend: bool) -> Result<Vec<String>, String> {
        if !amend {
            return Ok(vec!["diff".to_string(), "--cached".to_string()]);
        }
        if !self.head_exists()? {
            return Err("cannot amend: HEAD has no commits yet".to_string());
        }
        if self.parent_exists()? {
            Ok(vec![
                "diff".to_string(),
                "--cached".to_string(),
                "HEAD^".to_string(),
            ])
        } else {
            Ok(vec![
                "show".to_string(),
                "HEAD".to_string(),
                "--format=".to_string(),
            ])
        }
    }

    /// The staged diff, or for `--amend`, the diff the amended commit would contain.
    pub fn staged_diff(&self, amend: bool) -> Result<String, String> {
        let mut args = self.staged_base(amend)?;
        args.push("--no-color".to_string());
        let refs: Vec<&str> = args.iter().map(String::as_str).collect();
        self.run(&refs).map_err(|e| e.to_string())
    }

    /// Numstat for the same change set as `staged_diff`.
    pub fn staged_numstat(&self, amend: bool) -> Result<String, String> {
        let mut args = self.staged_base(amend)?;
        args.push("--numstat".to_string());
        args.push("-z".to_string());
        let refs: Vec<&str> = args.iter().map(String::as_str).collect();
        self.run(&refs).map_err(|e| e.to_string())
    }

    /// Diff restricted to the given paths, for the filtered digest.
    ///
    /// `--unified=0`: the hunk header carries the enclosing function name,
    /// which is the context a commit subject needs, and context lines would
    /// triple the prompt for the same information (see `bench/DIFF_STRATEGY.md`).
    pub fn staged_diff_for_paths(&self, amend: bool, paths: &[String]) -> Result<String, String> {
        if paths.is_empty() {
            return Ok(String::new());
        }
        let mut args = self.staged_base(amend)?;
        args.push("--no-color".to_string());
        args.push("--unified=0".to_string());
        args.push("--".to_string());
        for path in paths {
            args.push(format!(":(literal){path}"));
        }
        let refs: Vec<&str> = args.iter().map(String::as_str).collect();
        self.run(&refs).map_err(|e| e.to_string())
    }

    pub fn tag_exists(&self, tag: &str) -> Result<bool, String> {
        let verify = format!("refs/tags/{tag}");
        let (code, _, _) = self
            .status(&["rev-parse", "--verify", "--quiet", &verify])
            .map_err(|e| e.to_string())?;
        Ok(code == 0)
    }

    pub fn create_tag(&self, tag: &str, message: &str) -> Result<(), String> {
        self.run(&["tag", "-a", tag, "-m", message])
            .map(|_| ())
            .map_err(|e| e.to_string())
    }

    pub fn remote_origin(&self) -> Option<String> {
        let (code, stdout, _) = self.status(&["remote", "get-url", "origin"]).ok()?;
        if code != 0 {
            return None;
        }
        let url = stdout.trim();
        if url.is_empty() {
            None
        } else {
            Some(url.to_string())
        }
    }

    /// Run `git commit` with inherited stdio so `commit-msg` hook output is shown verbatim.
    pub fn commit_file(&self, amend: bool, message_path: &Path) -> Result<i32, String> {
        let mut command = Command::new("git");
        command.arg("commit");
        if amend {
            command.arg("--amend");
        }
        command.arg("-F").arg(message_path);
        command
            .current_dir(&self.root)
            .stdin(Stdio::inherit())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit());
        let status = command
            .status()
            .map_err(|err| format!("cannot run git commit: {err}"))?;
        Ok(status.code().unwrap_or(-1))
    }
}
