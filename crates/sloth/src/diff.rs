//! Staged diff digest: file list, numstat, filtered diff, and context budget.

use crate::git::Repo;

/// Rough local-model token estimate. Deliberately conservative for UTF-8:
/// the point is to fail before sending, never to truncate.
pub const BYTES_PER_TOKEN: usize = 4;
/// Room left for the model's answer and chat framing.
pub const CONTEXT_MARGIN: usize = 256;

const LOCKFILES: [&str; 8] = [
    "Cargo.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Gemfile.lock",
    "go.sum",
    "bun.lockb",
];
const GENERATED_DIRS: [&str; 4] = ["node_modules", "vendor", "dist", "build"];

#[derive(Clone, Debug)]
pub struct FileStat {
    pub path: String,
    pub added: String,
    pub deleted: String,
    pub binary: bool,
}

#[derive(Clone, Debug, Default)]
pub struct Digest {
    pub files: Vec<FileStat>,
    /// Diff with lockfiles, generated directories, minified and snapshot files removed.
    pub diff: String,
}

/// Why a path is excluded from the textual diff, if it is.
pub fn is_noise_path(path: &str) -> bool {
    let path = path.trim_end_matches('/');
    let base = path.rsplit('/').next().unwrap_or(path);
    if LOCKFILES.iter().any(|lock| base.eq_ignore_ascii_case(lock)) {
        return true;
    }
    if path
        .split('/')
        .any(|part| GENERATED_DIRS.contains(&part) || part == "target")
    {
        return true;
    }
    if base.ends_with(".min.js")
        || base.ends_with(".min.css")
        || base.ends_with(".min.mjs")
        || base.ends_with(".min.cjs")
    {
        return true;
    }
    base.ends_with(".snap") || path.split('/').any(|part| part == "__snapshots__")
}

fn parse_numstat(raw: &str) -> Vec<FileStat> {
    let mut files = Vec::new();
    let mut fields = raw.split('\0').peekable();
    while let Some(record) = fields.next() {
        if record.is_empty() {
            continue;
        }
        let mut cols = record.splitn(3, '\t');
        let added = cols.next().unwrap_or("").to_string();
        let deleted = cols.next().unwrap_or("").to_string();
        let mut path = cols.next().unwrap_or("").to_string();
        if path.is_empty() {
            // `-z` rename/copy record: "<add>\t<del>\t\0<old>\0<new>\0".
            let _old = fields.next();
            path = fields.next().unwrap_or("").to_string();
        }
        if path.is_empty() {
            continue;
        }
        let binary = added == "-" && deleted == "-";
        files.push(FileStat {
            path,
            added,
            deleted,
            binary,
        });
    }
    files
}

impl Digest {
    pub fn build(repo: &Repo, amend: bool) -> Result<Self, String> {
        let numstat = repo.staged_numstat(amend)?;
        let files = parse_numstat(&numstat);
        let paths: Vec<String> = files
            .iter()
            .filter(|file| !file.binary && !is_noise_path(&file.path))
            .map(|file| file.path.clone())
            .collect();
        let diff = repo.staged_diff_for_paths(amend, &paths)?;
        Ok(Self { files, diff })
    }

    pub fn render_files(&self) -> String {
        if self.files.is_empty() {
            return "(none)".to_string();
        }
        self.files
            .iter()
            .map(|file| file.path.clone())
            .collect::<Vec<_>>()
            .join("\n")
    }

    /// Raw numstat-style lines plus a total.
    pub fn render_diffstat(&self) -> String {
        if self.files.is_empty() {
            return "(none)".to_string();
        }
        let mut lines: Vec<String> = self
            .files
            .iter()
            .map(|file| format!("{}\t{}\t{}", file.added, file.deleted, file.path))
            .collect();
        let mut added = 0usize;
        let mut deleted = 0usize;
        for file in &self.files {
            added += file.added.parse::<usize>().unwrap_or(0);
            deleted += file.deleted.parse::<usize>().unwrap_or(0);
        }
        lines.push(format!(
            "total: {} file(s), {} insertion(s)(+), {} deletion(s)(-)",
            self.files.len(),
            added,
            deleted
        ));
        lines.join("\n")
    }

    /// Human-facing diffstat shown before the confirmation prompt.
    pub fn render_summary(&self) -> String {
        let mut lines: Vec<String> = self
            .files
            .iter()
            .map(|file| {
                if file.binary {
                    format!("  {} | binary", file.path)
                } else {
                    format!("  {} | +{} -{}", file.path, file.added, file.deleted)
                }
            })
            .collect();
        let added: usize = self
            .files
            .iter()
            .map(|file| file.added.parse::<usize>().unwrap_or(0))
            .sum();
        let deleted: usize = self
            .files
            .iter()
            .map(|file| file.deleted.parse::<usize>().unwrap_or(0))
            .sum();
        lines.push(format!(
            "  {} file(s) changed, +{added} -{deleted}",
            self.files.len()
        ));
        lines.join("\n")
    }
}

pub fn estimate_tokens(text: &str) -> usize {
    text.len().div_ceil(BYTES_PER_TOKEN)
}

/// Fail-closed context check. Returns the estimate on success.
pub fn enforce_budget(prompt: &str, num_ctx: usize, num_predict: usize) -> Result<usize, String> {
    let budget = num_ctx
        .saturating_sub(num_predict)
        .saturating_sub(CONTEXT_MARGIN);
    let estimated = estimate_tokens(prompt);
    if estimated > budget {
        return Err(format!(
            "staged change is too large for the model: the prompt is ~{estimated} tokens, \
             but the context budget is {budget} (num_ctx {num_ctx}, num_predict {num_predict}, \
             margin {CONTEXT_MARGIN}). Narrow the stage with `git add -p`, or split this into \
             smaller commits. Sloth never truncates or summarises a diff."
        ));
    }
    Ok(estimated)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn noise_filter_covers_lockfiles_generated_and_snapshots() {
        assert!(is_noise_path("Cargo.lock"));
        assert!(is_noise_path("web/package-lock.json"));
        assert!(is_noise_path("node_modules/left-pad/index.js"));
        assert!(is_noise_path("crates/sloth/target/debug/sloth"));
        assert!(is_noise_path("assets/app.min.js"));
        assert!(is_noise_path("src/__snapshots__/view.snap"));
        assert!(!is_noise_path("src/main.rs"));
        assert!(!is_noise_path("docs/release.md"));
    }

    #[test]
    fn numstat_parser_handles_renames_and_binaries() {
        let raw = "12\t3\tsrc/main.rs\0-\t-\tlogo.png\0\
                   4\t2\t\0old/name.rs\0new/name.rs\0";
        let files = parse_numstat(raw);
        assert_eq!(files.len(), 3);
        assert_eq!(files[0].path, "src/main.rs");
        assert!(files[1].binary);
        assert_eq!(files[2].path, "new/name.rs");
    }
}
