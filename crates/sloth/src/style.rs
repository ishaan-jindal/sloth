//! Port of `bench/style.py`: deterministic commit-convention mining.
//!
//! The pure classifier (`classify_subjects`) mirrors the Python reference's
//! regexes and thresholds without pulling in a regex crate. The git-backed
//! miner supplies subjects and bodies.

use crate::git::{Commit, Repo};
use serde::Serialize;
use std::collections::BTreeMap;

pub const SHAPES: [&str; 4] = ["conventional", "gitmoji", "ticket", "freeform"];
pub const MAJORITY_THRESHOLD: f64 = 0.60;
pub const PR_SUFFIX_THRESHOLD: f64 = 0.20;
pub const PR_SUFFIX_PATTERN: &str = r"\(#\d+\)$";

const BREAKING_TYPES: [&str; 2] = ["breaking", "breaking-change"];

/// Git trailer keys that do not make a body "prose". A body made only of these
/// is mechanical metadata, not an explanation of the change.
const TRAILERS: [&str; 6] = [
    "signed-off-by",
    "co-authored-by",
    "reviewed-by",
    "tested-by",
    "cc",
    "acked-by",
];

pub const BODY_HEADING_THRESHOLD: f64 = 0.30;
/// Below this prose-body rate, the model's body is dropped.
pub const BODY_USAGE_THRESHOLD: f64 = 0.35;
/// A missing scope is only a violation when the repo scopes at least this
/// share of its conventional commits.
pub const SCOPE_MANDATORY_RATE: f64 = 0.90;
/// Types accepted when a user forces conventional output on a repo that has no
/// mined conventional vocabulary.
pub const STANDARD_TYPES: [&str; 6] = ["feat", "fix", "chore", "docs", "ci", "test"];

#[derive(Clone, Debug, Default)]
pub struct BodyMetrics {
    /// Fraction of commits with a non-empty body after trailer stripping.
    pub prose_rate: f64,
    /// Fraction of commits whose body contains at least one known trailer.
    pub trailer_rate: f64,
    /// Dominant markdown heading sequence, when it covers >= 30% of prose bodies.
    pub headings: Vec<String>,
    /// Coverage of the dominant sequence among prose bodies.
    pub heading_rate: f64,
    /// Median word count of prose bodies (0 when there are none).
    pub median_words: usize,
    /// 90th percentile word count of prose bodies (0 when there are none).
    pub p90_words: usize,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct SubjectLen {
    pub median: usize,
    pub p90: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct Style {
    pub shape: String,
    pub confidence: String,
    pub match_rates: BTreeMap<String, f64>,
    pub breaking_markers: Vec<String>,
    pub pr_suffix: bool,
    pub pr_suffix_pattern: String,
    pub subject_len: SubjectLen,
    pub n_subjects: usize,
    pub type_vocab: BTreeMap<String, usize>,
    pub scope_vocab: BTreeMap<String, usize>,
    /// Fraction of conventional commits that carry a scope.
    pub scope_rate: f64,
    pub emoji_vocab: BTreeMap<String, usize>,
    pub exemplars: Vec<String>,
    pub exemplar_raw: Vec<String>,
    pub sample_subjects: Vec<String>,
    /// Prose-body rate; git trailers alone do not count as a body.
    pub body_usage: f64,
    /// Fraction of commits carrying at least one known git trailer.
    pub trailer_usage: f64,
    /// Dominant markdown heading structure in prose bodies, if any.
    pub body_headings: Vec<String>,
    /// Coverage of `body_headings` among prose bodies.
    pub body_heading_usage: f64,
    /// Median word count across prose bodies.
    pub body_median_words: usize,
    /// 90th percentile word count across prose bodies.
    pub body_p90_words: usize,
    pub repo: String,
    pub n_commits: usize,
    /// True when `SLOTH_STYLE` forced the shape.
    pub forced: bool,
    subjects: Vec<String>,
}

impl Default for Style {
    fn default() -> Self {
        let mut match_rates = BTreeMap::new();
        for shape in SHAPES {
            match_rates.insert(shape.to_string(), 0.0);
        }
        Self {
            shape: "freeform".to_string(),
            confidence: "low".to_string(),
            match_rates,
            breaking_markers: Vec::new(),
            pr_suffix: false,
            pr_suffix_pattern: PR_SUFFIX_PATTERN.to_string(),
            subject_len: SubjectLen::default(),
            n_subjects: 0,
            type_vocab: BTreeMap::new(),
            scope_vocab: BTreeMap::new(),
            scope_rate: 0.0,
            emoji_vocab: BTreeMap::new(),
            exemplars: Vec::new(),
            exemplar_raw: Vec::new(),
            sample_subjects: Vec::new(),
            body_usage: 0.0,
            trailer_usage: 0.0,
            body_headings: Vec::new(),
            body_heading_usage: 0.0,
            body_median_words: 0,
            body_p90_words: 0,
            repo: String::new(),
            n_commits: 0,
            forced: false,
            subjects: Vec::new(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct Conventional<'a> {
    pub type_: &'a str,
    pub scope: Option<&'a str>,
    pub bang: bool,
    pub subject: &'a str,
}

/// `^(?P<type>[a-z][a-z0-9]*)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s(?P<subject>.*)$`
pub fn parse_conventional(subject: &str) -> Option<Conventional<'_>> {
    let bytes = subject.as_bytes();
    if bytes.is_empty() || !bytes[0].is_ascii_lowercase() {
        return None;
    }
    let mut i = 1;
    while i < bytes.len() && (bytes[i].is_ascii_lowercase() || bytes[i].is_ascii_digit()) {
        i += 1;
    }
    let type_ = &subject[..i];

    let mut scope = None;
    if i < bytes.len() && bytes[i] == b'(' {
        let close = subject[i + 1..].find(')')?;
        if close == 0 {
            return None;
        }
        scope = Some(&subject[i + 1..i + 1 + close]);
        i += close + 2;
    }

    let mut bang = false;
    if i < bytes.len() && bytes[i] == b'!' {
        bang = true;
        i += 1;
    }
    if i >= bytes.len() || bytes[i] != b':' {
        return None;
    }
    i += 1;
    let rest = subject.get(i..)?;
    let mut chars = rest.char_indices();
    let (_, sep) = chars.next()?;
    if !sep.is_whitespace() {
        return None;
    }
    let subject_start = i + sep.len_utf8();
    Some(Conventional {
        type_,
        scope,
        bang,
        subject: &subject[subject_start..],
    })
}

/// `^:(?P<emoji>[a-z0-9_+-]+):`
pub fn parse_gitmoji(subject: &str) -> Option<&str> {
    let rest = subject.strip_prefix(':')?;
    let end = rest.find(':')?;
    let emoji = &rest[..end];
    if emoji.is_empty()
        || !emoji
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '_' | '+' | '-'))
    {
        return None;
    }
    Some(emoji)
}

/// `^(?P<ticket>[A-Z][A-Z0-9]+-\d+)`
pub fn parse_ticket(subject: &str) -> Option<&str> {
    let bytes = subject.as_bytes();
    if bytes.is_empty() || !bytes[0].is_ascii_uppercase() {
        return None;
    }
    let mut i = 1;
    while i < bytes.len() && (bytes[i].is_ascii_uppercase() || bytes[i].is_ascii_digit()) {
        i += 1;
    }
    if i < 2 || i >= bytes.len() || bytes[i] != b'-' {
        return None;
    }
    i += 1;
    let digits_start = i;
    while i < bytes.len() && bytes[i].is_ascii_digit() {
        i += 1;
    }
    if i == digits_start {
        return None;
    }
    Some(&subject[..i])
}

/// `\(#\d+\)$`
pub fn has_pr_suffix(subject: &str) -> bool {
    if !subject.ends_with(')') {
        return false;
    }
    let Some(open) = subject.rfind('(') else {
        return false;
    };
    if open + 1 >= subject.len() {
        return false;
    }
    let inner = &subject[open + 1..subject.len() - 1];
    inner
        .strip_prefix('#')
        .map(|digits| !digits.is_empty() && digits.chars().all(|c| c.is_ascii_digit()))
        .unwrap_or(false)
}

/// `\s*\(#\d+\)\s*$` replaced with the empty string.
pub fn strip_pr_suffix(subject: &str) -> String {
    let text = subject.trim_end();
    if let Some(open) = text.rfind('(') {
        if has_pr_suffix(text) {
            return subject[..open].trim_end().to_string();
        }
    }
    subject.to_string()
}

fn classify_shape(subject: &str) -> &'static str {
    if parse_conventional(subject).is_some() {
        "conventional"
    } else if parse_gitmoji(subject).is_some() {
        "gitmoji"
    } else if parse_ticket(subject).is_some() {
        "ticket"
    } else {
        "freeform"
    }
}

fn most_common(counts: &BTreeMap<String, usize>) -> Option<String> {
    counts
        .iter()
        .max_by(|a, b| a.1.cmp(b.1).then_with(|| b.0.cmp(a.0)))
        .map(|(key, _)| key.clone())
}

fn median_int(values: &[usize]) -> usize {
    if values.is_empty() {
        return 0;
    }
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    let n = sorted.len();
    if n % 2 == 1 {
        sorted[n / 2]
    } else {
        (sorted[n / 2 - 1] + sorted[n / 2]) / 2
    }
}

/// Python's `round()`: round half to even.
fn python_round(value: f64) -> i64 {
    let floor = value.floor();
    let fraction = value - floor;
    if fraction > 0.5 || (fraction == 0.5 && (floor as i64) % 2 != 0) {
        floor as i64 + 1
    } else {
        floor as i64
    }
}

fn percentile(values: &[usize], p: f64) -> usize {
    if values.is_empty() {
        return 0;
    }
    let mut sorted = values.to_vec();
    sorted.sort_unstable();
    if sorted.len() == 1 {
        return sorted[0];
    }
    let idx = python_round((p / 100.0) * (sorted.len() - 1) as f64);
    let idx = idx.clamp(0, sorted.len() as i64 - 1) as usize;
    sorted[idx]
}

/// Python's `round(value, 3)`, for parity with the reference JSON output.
pub fn round3(value: f64) -> f64 {
    python_round(value * 1000.0) as f64 / 1000.0
}

/// Pure classifier, identical in behaviour to `style.classify_subjects`.
pub fn classify_subjects(input: &[String]) -> Style {
    let subjects: Vec<String> = input.to_vec();
    let n = subjects.len();

    let mut counts: BTreeMap<String, usize> = SHAPES.iter().map(|s| (s.to_string(), 0)).collect();
    let mut type_vocab: BTreeMap<String, usize> = BTreeMap::new();
    let mut scope_vocab: BTreeMap<String, usize> = BTreeMap::new();
    let mut emoji_vocab: BTreeMap<String, usize> = BTreeMap::new();
    let mut breaking_markers: Vec<String> = Vec::new();
    let mut lengths: Vec<usize> = Vec::new();
    let mut conventional_count = 0usize;
    let mut scoped_count = 0usize;

    for subject in &subjects {
        let shape = classify_shape(subject);
        *counts.entry(shape.to_string()).or_insert(0) += 1;
        match shape {
            "conventional" => {
                if let Some(parsed) = parse_conventional(subject) {
                    conventional_count += 1;
                    *type_vocab.entry(parsed.type_.to_string()).or_insert(0) += 1;
                    if let Some(scope) = parsed.scope {
                        scoped_count += 1;
                        *scope_vocab.entry(scope.to_string()).or_insert(0) += 1;
                    }
                    if parsed.bang && !breaking_markers.iter().any(|m| m == "!") {
                        breaking_markers.push("!".to_string());
                    }
                    if BREAKING_TYPES.contains(&parsed.type_)
                        && !breaking_markers.iter().any(|m| m == parsed.type_)
                    {
                        breaking_markers.push(parsed.type_.to_string());
                    }
                }
            }
            "gitmoji" => {
                if let Some(emoji) = parse_gitmoji(subject) {
                    *emoji_vocab.entry(emoji.to_string()).or_insert(0) += 1;
                }
            }
            _ => {}
        }
    }

    let rates: BTreeMap<String, f64> = counts
        .iter()
        .map(|(shape, count)| {
            let rate = if n == 0 {
                0.0
            } else {
                *count as f64 / n as f64
            };
            (shape.clone(), round3(rate))
        })
        .collect();

    let majority: Option<String> = if n == 0 { None } else { most_common(&counts) };

    let majority_rate = majority
        .as_ref()
        .map(|shape| counts.get(shape).copied().unwrap_or(0) as f64 / n as f64)
        .unwrap_or(0.0);

    let (shape, confidence) = match &majority {
        Some(majority) if majority_rate >= MAJORITY_THRESHOLD => {
            let confidence = if majority_rate >= 0.85 {
                "high"
            } else if majority_rate >= 0.60 {
                "medium"
            } else {
                "low"
            };
            (majority.clone(), confidence)
        }
        _ => ("freeform".to_string(), "low"),
    };

    for subject in &subjects {
        if classify_shape(subject) == shape {
            lengths.push(subject.trim().chars().count());
        }
    }
    if lengths.is_empty() {
        lengths = subjects.iter().map(|s| s.trim().chars().count()).collect();
    }

    let pr_matches = subjects.iter().filter(|s| has_pr_suffix(s)).count();
    let pr_suffix = n > 0 && (pr_matches as f64 / n as f64) >= PR_SUFFIX_THRESHOLD;

    let mut style = Style {
        shape,
        confidence: confidence.to_string(),
        match_rates: rates,
        breaking_markers,
        pr_suffix,
        subject_len: SubjectLen {
            median: median_int(&lengths),
            p90: percentile(&lengths, 90.0),
        },
        n_subjects: n,
        type_vocab,
        scope_vocab,
        scope_rate: if conventional_count == 0 {
            0.0
        } else {
            round3(scoped_count as f64 / conventional_count as f64)
        },
        emoji_vocab,
        ..Style::default()
    };
    style.n_commits = n;
    style.subjects = subjects;
    style.sample_subjects = style.subjects.iter().take(12).cloned().collect();
    style.refresh_exemplars();
    style
}

impl Style {
    fn refresh_exemplars(&mut self) {
        let mut candidates: Vec<String> = self
            .subjects
            .iter()
            .filter(|s| classify_shape(s) == self.shape)
            .take(5)
            .cloned()
            .collect();
        if candidates.is_empty() && !self.forced {
            candidates = self.subjects.iter().take(5).cloned().collect();
        }
        self.exemplar_raw = candidates.clone();
        if self.pr_suffix {
            self.exemplars = candidates
                .iter()
                .map(|s| strip_pr_suffix(s).trim().to_string())
                .collect();
        } else {
            self.exemplars = candidates;
        }
    }

    /// Use a shape forced by `SLOTH_STYLE` instead of the mined majority.
    pub fn set_shape(&mut self, shape: &str) -> Result<(), String> {
        if !SHAPES.contains(&shape) {
            return Err(format!(
                "SLOTH_STYLE must be one of {} (got {shape:?})",
                SHAPES.join(", ")
            ));
        }
        self.shape = shape.to_string();
        self.confidence = "high".to_string();
        self.forced = true;
        self.refresh_exemplars();
        Ok(())
    }

    /// The chosen shape's match rate, as reported by the miner.
    pub fn match_rate(&self) -> f64 {
        self.match_rates.get(&self.shape).copied().unwrap_or(0.0)
    }
}

/// True when the line is one of the known git trailers.
pub fn is_trailer_line(line: &str) -> bool {
    let text = line.trim_start();
    let Some((key, _)) = text.split_once(':') else {
        return false;
    };
    TRAILERS.contains(&key.trim().to_ascii_lowercase().as_str())
}

pub fn has_trailer(body: &str) -> bool {
    body.lines().any(is_trailer_line)
}

/// Remove trailer lines (and their indented continuations), leaving only prose.
pub fn strip_trailers(body: &str) -> String {
    let mut kept: Vec<&str> = Vec::new();
    let mut previous_was_trailer = false;
    for line in body.lines() {
        if is_trailer_line(line) {
            previous_was_trailer = true;
            continue;
        }
        if previous_was_trailer && line.chars().next().is_some_and(|c| c == ' ' || c == '\t') {
            // Continuation of the trailer value.
            continue;
        }
        previous_was_trailer = false;
        kept.push(line);
    }
    kept.join("\n").trim().to_string()
}

/// Markdown ATX headings in order of appearance.
pub fn body_headings(body: &str) -> Vec<String> {
    let mut headings = Vec::new();
    for line in body.lines() {
        let text = line.trim_start();
        let hashes = text.chars().take_while(|c| *c == '#').count();
        if hashes == 0 || hashes > 6 {
            continue;
        }
        let rest = &text[hashes..];
        if !rest.chars().next().is_some_and(|c| c == ' ' || c == '\t') {
            continue;
        }
        let heading = rest.trim().trim_end_matches('#').trim();
        if !heading.is_empty() {
            headings.push(heading.to_string());
        }
    }
    headings
}

/// Mine prose-body usage, trailer usage and the dominant heading structure.
pub fn body_metrics(bodies: &[String]) -> BodyMetrics {
    if bodies.is_empty() {
        return BodyMetrics::default();
    }
    let mut prose_count = 0usize;
    let mut trailer_count = 0usize;
    let mut sequences: BTreeMap<Vec<String>, usize> = BTreeMap::new();
    let mut word_counts: Vec<usize> = Vec::new();
    for body in bodies {
        if has_trailer(body) {
            trailer_count += 1;
        }
        let prose = strip_trailers(body);
        if prose.trim().is_empty() {
            continue;
        }
        prose_count += 1;
        word_counts.push(
            prose
                .split_whitespace()
                .filter(|word| !word.chars().all(|c| c == '#'))
                .count(),
        );
        let headings = body_headings(&prose);
        if !headings.is_empty() {
            *sequences.entry(headings).or_insert(0) += 1;
        }
    }

    let total = bodies.len() as f64;
    let (headings, heading_count) = sequences
        .into_iter()
        .max_by(|(sequence_a, count_a), (sequence_b, count_b)| {
            count_a
                .cmp(count_b)
                .then_with(|| sequence_b.cmp(sequence_a))
        })
        .unwrap_or_default();
    let heading_rate = if prose_count == 0 {
        0.0
    } else {
        heading_count as f64 / prose_count as f64
    };
    let headings = if heading_rate >= BODY_HEADING_THRESHOLD {
        headings
    } else {
        Vec::new()
    };

    BodyMetrics {
        prose_rate: round3(prose_count as f64 / total),
        trailer_rate: round3(trailer_count as f64 / total),
        headings,
        heading_rate: round3(heading_rate),
        median_words: median_int(&word_counts),
        p90_words: percentile(&word_counts, 90.0),
    }
}

/// Mine a repository's convention from its recent non-merge commits.
pub fn mine(repo: &Repo, commits: usize) -> Result<Style, String> {
    if !repo.head_exists()? {
        return Ok(Style {
            repo: repo.root().to_string_lossy().to_string(),
            ..Style::default()
        });
    }
    let history: Vec<Commit> = repo.recent_commits(commits)?;
    let subjects: Vec<String> = history.iter().map(|c| c.subject.clone()).collect();
    let bodies: Vec<String> = history.iter().map(|c| c.body.clone()).collect();

    let mut style = classify_subjects(&subjects);
    style.n_commits = subjects.len();
    style.repo = repo.root().to_string_lossy().to_string();
    let metrics = body_metrics(&bodies);
    style.body_usage = metrics.prose_rate;
    style.trailer_usage = metrics.trailer_rate;
    style.body_headings = metrics.headings;
    style.body_heading_usage = metrics.heading_rate;
    style.body_median_words = metrics.median_words;
    style.body_p90_words = metrics.p90_words;

    for body in &bodies {
        if body.contains("BREAKING CHANGE") || body.contains("BREAKING-CHANGE") {
            if !style
                .breaking_markers
                .iter()
                .any(|m| m == "BREAKING CHANGE")
            {
                style.breaking_markers.push("BREAKING CHANGE".to_string());
            }
            break;
        }
    }
    Ok(style)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn subjects(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn conventional_subjects_are_mined_with_vocab() {
        let style = classify_subjects(&subjects(&[
            "feat(api): add endpoint",
            "fix(api): handle empty input",
            "feat(cli): add flag",
            "feat(api): document endpoint",
            "fix(cli): print usage",
        ]));
        assert_eq!(style.shape, "conventional");
        assert_eq!(style.confidence, "high");
        assert_eq!(style.type_vocab.get("feat"), Some(&3));
        assert_eq!(style.type_vocab.get("fix"), Some(&2));
        assert_eq!(style.scope_vocab.get("api"), Some(&3));
        assert_eq!(style.scope_vocab.get("cli"), Some(&2));
        assert_eq!(style.exemplars.len(), 5);
    }

    #[test]
    fn gitmoji_subjects_are_mined() {
        let style = classify_subjects(&subjects(&[
            ":sparkles: add export",
            ":bug: fix crash",
            ":sparkles: add palette",
            ":memo: document flags",
        ]));
        assert_eq!(style.shape, "gitmoji");
        assert_eq!(style.emoji_vocab.get("sparkles"), Some(&2));
        assert_eq!(style.confidence, "high");
    }

    #[test]
    fn mixed_history_falls_back_to_freeform_low() {
        let style = classify_subjects(&subjects(&[
            "feat: one",
            "fix: two",
            "plain words here",
            "more plain words",
            ":sparkles: three",
            "PROJ-12 four",
        ]));
        assert_eq!(style.shape, "freeform");
        assert_eq!(style.confidence, "low");
    }

    #[test]
    fn majority_floor_is_sixty_percent() {
        let at_floor = classify_subjects(&subjects(&[
            "feat: one",
            "fix: two",
            "chore: three",
            "plain words",
            "other words",
        ]));
        assert_eq!(at_floor.shape, "conventional");
        assert_eq!(at_floor.confidence, "medium");

        let below = classify_subjects(&subjects(&[
            "feat: one",
            "fix: two",
            "plain words",
            ":sparkles: three",
            "PROJ-12 four",
        ]));
        assert_eq!(below.shape, "freeform");
        assert_eq!(below.confidence, "low");
    }

    #[test]
    fn pr_suffix_threshold_is_twenty_percent() {
        let five = classify_subjects(&subjects(&[
            "feat: one (#1234)",
            "feat: two",
            "fix: three",
            "docs: four",
            "chore: five",
        ]));
        assert!(five.pr_suffix);

        let six = classify_subjects(&subjects(&[
            "feat: one (#1234)",
            "feat: two",
            "feat: three",
            "fix: four",
            "docs: five",
            "chore: six",
        ]));
        assert!(!six.pr_suffix);
    }

    #[test]
    fn exemplars_have_pr_suffixes_stripped() {
        let style = classify_subjects(&subjects(&[
            "feat: one (#1234)",
            "feat: two",
            "fix: three",
            "docs: four",
            "chore: five",
        ]));
        assert!(style.pr_suffix);
        assert_eq!(style.exemplar_raw[0], "feat: one (#1234)");
        assert_eq!(style.exemplars[0], "feat: one");
        assert!(!style.exemplars.iter().any(|s| s.contains("(#")));
    }

    #[test]
    fn unparseable_as_tag_style_stays_freeform() {
        let style = classify_subjects(&subjects(&["update the thing", "improve the other thing"]));
        assert_eq!(style.shape, "freeform");
        // A unanimous freeform history is a confident freeform convention.
        assert_eq!(style.confidence, "high");
    }

    #[test]
    fn rates_use_python_half_even_rounding() {
        assert_eq!(round3(1.0 / 16.0), 0.062);
        assert_eq!(round3(1.0 / 6.0), 0.167);
        assert_eq!(round3(2.0 / 6.0), 0.333);
    }

    #[test]
    fn scope_rate_counts_only_conventional_subjects() {
        let style = classify_subjects(&subjects(&[
            "feat(api): one",
            "fix(api): two",
            "feat(cli): three",
            "docs(api): four",
            "chore: five",
            "plain words",
        ]));
        assert_eq!(style.shape, "conventional");
        assert_eq!(style.scope_rate, 0.8);
    }

    #[test]
    fn trailers_are_not_prose() {
        let trailer_only = "Signed-off-by: A <a@example.com>\nCo-authored-by: B <b@example.com>";
        assert!(strip_trailers(trailer_only).is_empty());

        let prose_and_trailer =
            "Explain the change.\n\nSigned-off-by: A <a@example.com>\n  with continuation";
        assert_eq!(strip_trailers(prose_and_trailer), "Explain the change.");

        assert!(is_trailer_line("CC: team"));
        assert!(is_trailer_line("Reviewed-by: C"));
        assert!(!is_trailer_line("This is prose: really"));
    }

    #[test]
    fn body_metrics_filter_trailers_and_detect_heading_structure() {
        let bodies: Vec<String> = [
            "## What?\n\nAdd the export.\n\n## Why?\n\nNeeded for scripts.\n\nSigned-off-by: A",
            "## What?\n\nAdd one.\n\n## Why?\n\nBecause.\n",
            "## What?\n\nAdd two.\n\n## Why?\n\nBecause too.\n",
            "A plain prose body.",
            "Signed-off-by: A\nCo-authored-by: B",
            "",
        ]
        .iter()
        .map(|body| body.to_string())
        .collect();
        let metrics = body_metrics(&bodies);
        assert_eq!(metrics.prose_rate, 0.667);
        assert_eq!(metrics.trailer_rate, 0.333);
        assert_eq!(metrics.headings, vec!["What?", "Why?"]);
        assert_eq!(metrics.heading_rate, 0.75);
        assert_eq!(metrics.median_words, 5);
    }

    #[test]
    fn low_coverage_heading_sequence_is_not_reported() {
        let bodies: Vec<String> = [
            "## What?\n\nOne.",
            "Plain prose one.",
            "Plain prose two.",
            "Plain prose three.",
        ]
        .iter()
        .map(|body| body.to_string())
        .collect();
        let metrics = body_metrics(&bodies);
        assert!(metrics.headings.is_empty());
        assert_eq!(metrics.heading_rate, 0.25);
    }
}
