//! Release mechanics: tag scheme inference, deterministic version bump,
//! changelog rendering/insertion, and optional model-written highlights.

use crate::git::Commit;
use crate::model::Client;
use crate::style::parse_conventional;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Version {
    pub major: u64,
    pub minor: u64,
    pub patch: u64,
}

impl Version {
    pub fn new(major: u64, minor: u64, patch: u64) -> Self {
        Self {
            major,
            minor,
            patch,
        }
    }
}

#[derive(Clone, Debug)]
pub struct TagScheme {
    pub prefix: String,
    pub widths: [usize; 3],
}

impl TagScheme {
    pub fn default_v() -> Self {
        Self {
            prefix: "v".to_string(),
            widths: [1, 1, 1],
        }
    }

    fn pad(value: u64, width: usize) -> String {
        format!("{value:0width$}")
    }

    pub fn render_version(&self, version: &Version) -> String {
        format!(
            "{}.{}.{}",
            Self::pad(version.major, self.widths[0]),
            Self::pad(version.minor, self.widths[1]),
            Self::pad(version.patch, self.widths[2])
        )
    }

    pub fn render_tag(&self, version: &Version) -> String {
        format!("{}{}", self.prefix, self.render_version(version))
    }
}

#[derive(Clone, Debug)]
pub struct ParsedTag {
    pub tag: String,
    pub prefix: String,
    pub version: Version,
    pub widths: [usize; 3],
}

/// Parse the trailing `MAJOR.MINOR.PATCH` of a tag, allowing any prefix.
/// `v1.2.3`, `1.2.3`, `pkg-v1.2.3` and `pkg@1.2.3` all parse; `preview-pr1011`
/// and `nightlyv0` do not.
pub fn parse_tag(tag: &str) -> Option<ParsedTag> {
    let bytes = tag.as_bytes();
    let mut numbers = [0u64; 3];
    let mut widths = [1usize; 3];
    let mut end = bytes.len();
    let mut version_start = bytes.len();

    for index in (0..3).rev() {
        let token_end = end;
        let mut token_start = token_end;
        while token_start > 0 && bytes[token_start - 1].is_ascii_digit() {
            token_start -= 1;
        }
        if token_start == token_end {
            return None;
        }
        let token = tag.get(token_start..token_end)?;
        numbers[index] = token.parse().ok()?;
        widths[index] = token.len();
        if index > 0 {
            if token_start == 0 || bytes[token_start - 1] != b'.' {
                return None;
            }
            end = token_start - 1;
        } else {
            version_start = token_start;
        }
    }

    if version_start > 0 {
        let previous = bytes[version_start - 1];
        // Reject four-component versions such as `1.2.3.4`.
        if previous.is_ascii_digit() || previous == b'.' {
            return None;
        }
    }

    Some(ParsedTag {
        tag: tag.to_string(),
        prefix: tag.get(..version_start)?.to_string(),
        version: Version::new(numbers[0], numbers[1], numbers[2]),
        widths,
    })
}

#[derive(Clone, Debug)]
pub struct SchemeInference {
    pub scheme: Option<TagScheme>,
    pub latest: Option<ParsedTag>,
    pub ignored: usize,
    pub total: usize,
}

fn latest_in(tags: &[ParsedTag]) -> Option<&ParsedTag> {
    tags.iter()
        .max_by(|a, b| a.version.cmp(&b.version).then_with(|| b.tag.cmp(&a.tag)))
}

/// Pick the release scheme used by most tags; ties go to the component with
/// the highest latest version, then to the lexicographically first prefix.
pub fn infer_scheme(tags: &[String]) -> SchemeInference {
    let total = tags.len();
    let parsed: Vec<ParsedTag> = tags.iter().filter_map(|tag| parse_tag(tag)).collect();
    let ignored = total.saturating_sub(parsed.len());
    if parsed.is_empty() {
        return SchemeInference {
            scheme: None,
            latest: None,
            ignored,
            total,
        };
    }

    let mut groups: BTreeMap<String, Vec<ParsedTag>> = BTreeMap::new();
    for tag in parsed {
        groups.entry(tag.prefix.clone()).or_default().push(tag);
    }
    let chosen = groups
        .into_iter()
        .max_by(|(prefix_a, tags_a), (prefix_b, tags_b)| {
            let latest_a = latest_in(tags_a)
                .map(|tag| tag.version)
                .unwrap_or(Version::new(0, 0, 0));
            let latest_b = latest_in(tags_b)
                .map(|tag| tag.version)
                .unwrap_or(Version::new(0, 0, 0));
            tags_a
                .len()
                .cmp(&tags_b.len())
                .then_with(|| latest_a.cmp(&latest_b))
                .then_with(|| prefix_b.cmp(prefix_a))
        });

    match chosen {
        Some((prefix, tags)) => {
            let latest = latest_in(&tags).cloned();
            let widths = latest.as_ref().map(|tag| tag.widths).unwrap_or([1, 1, 1]);
            SchemeInference {
                scheme: Some(TagScheme { prefix, widths }),
                latest,
                ignored,
                total,
            }
        }
        None => SchemeInference {
            scheme: None,
            latest: None,
            ignored,
            total,
        },
    }
}

pub fn parse_explicit_version(input: &str, scheme: &TagScheme) -> Result<Version, String> {
    let text = input.trim();
    let without_prefix = if !scheme.prefix.is_empty() && text.starts_with(&scheme.prefix) {
        &text[scheme.prefix.len()..]
    } else if let Some(rest) = text.strip_prefix('v') {
        rest
    } else {
        text
    };
    let parts: Vec<&str> = without_prefix.split('.').collect();
    if parts.len() != 3 {
        return Err(format!(
            "invalid --version {input:?}: expected MAJOR.MINOR.PATCH (for example 1.4.0)"
        ));
    }
    let mut numbers = [0u64; 3];
    for (index, part) in parts.iter().enumerate() {
        if part.is_empty() || !part.chars().all(|c| c.is_ascii_digit()) {
            return Err(format!(
                "invalid --version {input:?}: {part:?} is not a number"
            ));
        }
        numbers[index] = part
            .parse()
            .map_err(|_| format!("invalid --version {input:?}: {part:?} is out of range"))?;
    }
    Ok(Version::new(numbers[0], numbers[1], numbers[2]))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Bump {
    Major,
    Minor,
    Patch,
    None,
}

impl Bump {
    pub fn apply(self, version: &Version) -> Version {
        match self {
            Bump::Major => Version::new(version.major.saturating_add(1), 0, 0),
            Bump::Minor => Version::new(version.major, version.minor.saturating_add(1), 0),
            Bump::Patch => Version::new(
                version.major,
                version.minor,
                version.patch.saturating_add(1),
            ),
            Bump::None => *version,
        }
    }
}

fn is_breaking(commit: &Commit) -> bool {
    if commit
        .subject
        .find(':')
        .is_some_and(|colon| commit.subject[..colon].contains('!'))
    {
        return true;
    }
    commit.body.contains("BREAKING CHANGE:") || commit.body.contains("BREAKING-CHANGE:")
}

/// Deterministic bump. The model never touches a version number.
pub fn bump_for_commits(commits: &[Commit]) -> Bump {
    let mut minor = false;
    let mut patch = false;
    for commit in commits {
        if is_breaking(commit) {
            return Bump::Major;
        }
        if let Some(parsed) = parse_conventional(&commit.subject) {
            match parsed.type_ {
                "feat" => minor = true,
                "fix" => patch = true,
                _ => {}
            }
        }
    }
    if minor {
        Bump::Minor
    } else if patch {
        Bump::Patch
    } else {
        Bump::None
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ChangeGroup {
    Added,
    Fixed,
    Changed,
    Removed,
}

impl ChangeGroup {
    fn title(self) -> &'static str {
        match self {
            ChangeGroup::Added => "Added",
            ChangeGroup::Fixed => "Fixed",
            ChangeGroup::Changed => "Changed",
            ChangeGroup::Removed => "Removed",
        }
    }
}

fn group_for(subject: &str) -> ChangeGroup {
    match parse_conventional(subject).map(|parsed| parsed.type_) {
        Some("feat") => ChangeGroup::Added,
        Some("fix") => ChangeGroup::Fixed,
        Some("remove") | Some("revert") => ChangeGroup::Removed,
        Some("breaking") | Some("breaking-change") => ChangeGroup::Changed,
        _ => ChangeGroup::Changed,
    }
}

pub fn group_commits(commits: &[Commit]) -> Vec<(ChangeGroup, Vec<String>)> {
    let mut groups: BTreeMap<u8, Vec<String>> = BTreeMap::new();
    for commit in commits {
        let group = group_for(&commit.subject);
        let key = match group {
            ChangeGroup::Added => 0,
            ChangeGroup::Fixed => 1,
            ChangeGroup::Changed => 2,
            ChangeGroup::Removed => 3,
        };
        groups.entry(key).or_default().push(commit.subject.clone());
    }
    groups
        .into_iter()
        .map(|(key, subjects)| {
            let group = match key {
                0 => ChangeGroup::Added,
                1 => ChangeGroup::Fixed,
                2 => ChangeGroup::Changed,
                _ => ChangeGroup::Removed,
            };
            (group, subjects)
        })
        .collect()
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HeadingStyle {
    Bracketed,
    Plain,
}

fn is_unreleased_heading(line: &str) -> bool {
    let rest = line.trim_start_matches('#').trim();
    rest.eq_ignore_ascii_case("unreleased") || rest.to_ascii_lowercase().starts_with("[unreleased")
}

/// Detect `## [1.2.3] - YYYY-MM-DD` versus `## 1.2.3`. Unreleased headings do
/// not decide the style.
pub fn detect_heading_style(existing: Option<&str>) -> HeadingStyle {
    let Some(existing) = existing else {
        return HeadingStyle::Bracketed;
    };
    for line in existing.lines() {
        if !line.starts_with("## ") || is_unreleased_heading(line) {
            continue;
        }
        let rest = line[3..].trim();
        if rest.starts_with('[') {
            return HeadingStyle::Bracketed;
        }
        if rest.chars().next().is_some_and(|c| c.is_ascii_digit()) {
            return HeadingStyle::Plain;
        }
    }
    HeadingStyle::Bracketed
}

fn civil_from_days(days: i64) -> (i64, u32, u32) {
    // Howard Hinnant's civil_from_days, with 1970-01-01 as day zero.
    let z = days + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let mut year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    if month <= 2 {
        year += 1;
    }
    (year, month, day)
}

pub fn today_utc() -> String {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0) as i64;
    let (year, month, day) = civil_from_days(seconds.div_euclid(86_400));
    format!("{year:04}-{month:02}-{day:02}")
}

fn clean_highlights(text: &str) -> Option<String> {
    let mut text = text.trim();
    if text.starts_with("###") {
        if let Some(newline) = text.find('\n') {
            text = text[newline + 1..].trim();
        } else {
            text = "";
        }
    }
    if text.is_empty() {
        None
    } else {
        Some(text.to_string())
    }
}

pub fn render_section_with_style(
    style: HeadingStyle,
    version_label: &str,
    date: &str,
    groups: &[(ChangeGroup, Vec<String>)],
    highlights: Option<&str>,
) -> String {
    match style {
        HeadingStyle::Bracketed => {
            let bracketed = format!("[{}]", version_label);
            render_section_with_heading(&bracketed, date, groups, highlights, true)
        }
        HeadingStyle::Plain => {
            render_section_with_heading(version_label, date, groups, highlights, false)
        }
    }
}

fn render_section_with_heading(
    label: &str,
    date: &str,
    groups: &[(ChangeGroup, Vec<String>)],
    highlights: Option<&str>,
    with_date: bool,
) -> String {
    let mut text = if with_date {
        format!("## {label} - {date}\n\n")
    } else {
        format!("## {label}\n\n")
    };
    for (group, subjects) in groups {
        text.push_str(&format!("### {}\n\n", group.title()));
        for subject in subjects {
            text.push_str(&format!("- {subject}\n"));
        }
        text.push('\n');
    }
    if let Some(highlights) = highlights.and_then(clean_highlights) {
        text.push_str("### Highlights\n\n");
        text.push_str(&highlights);
        text.push_str("\n\n");
    }
    text.trim_end().to_string() + "\n"
}

fn insertion_index(existing: &str) -> usize {
    let mut offset = 0usize;
    for line in existing.split_inclusive('\n') {
        let trimmed = line.trim_end_matches(['\n', '\r']);
        if trimmed.starts_with("## ") && !is_unreleased_heading(trimmed) {
            return offset;
        }
        offset += line.len();
    }
    existing.len()
}

fn ensure_blank_line(text: &mut String) {
    if text.is_empty() {
        return;
    }
    if !text.ends_with('\n') {
        text.push_str("\n\n");
    } else if !text.ends_with("\n\n") {
        text.push('\n');
    }
}

/// Insert a new section after the title/preamble (and after an `Unreleased`
/// block if present), leaving all existing bytes intact around the insert.
pub fn insert_section(existing: &str, section: &str) -> String {
    let index = insertion_index(existing);
    let (prefix, suffix) = existing.split_at(index);
    let mut out = String::with_capacity(existing.len() + section.len() + 4);
    out.push_str(prefix);
    ensure_blank_line(&mut out);
    out.push_str(section.trim_end_matches('\n'));
    out.push_str("\n\n");
    out.push_str(suffix);
    out
}

pub fn new_changelog(section: &str) -> String {
    format!(
        "# Changelog\n\n\
         All notable changes to this project will be documented in this file.\n\n\
         The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).\n\n\
         {}",
        section.trim_end_matches('\n')
    ) + "\n"
}

fn parse_ref_line(line: &str) -> Option<(String, String)> {
    let rest = line.strip_prefix('[')?;
    let close = rest.find("]:")?;
    let label = &rest[..close];
    let url = rest[close + 2..].trim();
    if !url.starts_with("http") {
        return None;
    }
    Some((label.to_string(), url.to_string()))
}

/// Update Keep-a-Changelog link references when the file uses them.
pub fn update_link_refs(
    content: &str,
    new_label: &str,
    new_tag: &str,
    previous_tag: Option<&str>,
    repo_url: Option<&str>,
) -> String {
    let lines: Vec<&str> = content.lines().collect();
    let refs: Vec<(usize, String, String)> = lines
        .iter()
        .enumerate()
        .filter_map(|(index, line)| parse_ref_line(line).map(|(label, url)| (index, label, url)))
        .collect();
    if refs.is_empty() {
        return content.to_string();
    }

    let base = refs
        .iter()
        .find_map(|(_, _, url)| url.find("/compare/").map(|at| url[..at + 9].to_string()))
        .or_else(|| {
            repo_url.filter(|url| url.starts_with("http")).map(|url| {
                format!(
                    "{}/compare/",
                    url.trim_end_matches('/').trim_end_matches(".git")
                )
            })
        });
    let Some(base) = base else {
        return content.to_string();
    };

    let new_url = if let Some(previous) = previous_tag {
        format!("{base}{previous}...{new_tag}")
    } else if let Some(repo_url) = repo_url.filter(|url| url.starts_with("http")) {
        format!(
            "{}/releases/tag/{new_tag}",
            repo_url.trim_end_matches('/').trim_end_matches(".git")
        )
    } else {
        return content.to_string();
    };

    let use_tag_label =
        previous_tag.is_some_and(|previous| refs.iter().any(|(_, label, _)| label == previous));
    let label = if use_tag_label {
        new_tag.to_string()
    } else {
        new_label.to_string()
    };
    let new_ref = format!("[{label}]: {new_url}");

    let ends_with_newline = content.ends_with('\n');
    let mut out = String::with_capacity(content.len() + new_ref.len() + 2);
    let mut inserted = false;
    for (index, line) in lines.iter().enumerate() {
        if let Some((_, ref_label, _)) = refs.iter().find(|(at, _, _)| *at == index) {
            if ref_label.eq_ignore_ascii_case("unreleased") {
                out.push_str(&format!("[{ref_label}]: {base}{new_tag}...HEAD\n"));
                if !inserted {
                    out.push_str(&new_ref);
                    out.push('\n');
                    inserted = true;
                }
                continue;
            }
            if !inserted {
                out.push_str(&new_ref);
                out.push('\n');
                inserted = true;
            }
        }
        out.push_str(line);
        out.push('\n');
    }
    if !ends_with_newline {
        out.pop();
    }
    out
}

pub fn generate_highlights(
    client: &Client,
    commits: &[Commit],
    tag: &str,
) -> Result<String, String> {
    if commits.is_empty() {
        return Err("there are no commits to summarise".to_string());
    }
    let subjects = commits
        .iter()
        .map(|commit| format!("- {}", commit.subject))
        .collect::<Vec<_>>()
        .join("\n");
    let schema = json!({
        "type": "object",
        "properties": {
            "highlights": {
                "type": "string",
                "description": "One short paragraph of plain prose, no heading and no bullets."
            }
        },
        "required": ["highlights"],
        "additionalProperties": false
    });
    let system = "You write release highlights from commit subjects. \
                  State only what the subjects support. Reply with one short paragraph of plain \
                  prose: no heading, no bullet list, no markdown.";
    let user = format!("Release {tag}. Commit subjects:\n{subjects}");
    let estimated = crate::diff::estimate_tokens(&format!("{system}\n{user}"));
    let budget = crate::model::NUM_CTX
        .saturating_sub(crate::model::NUM_PREDICT)
        .saturating_sub(crate::diff::CONTEXT_MARGIN);
    if estimated > budget {
        return Err(format!(
            "{estimated} estimated tokens of commit subjects exceed the {budget}-token budget; \
             highlights omitted rather than truncated"
        ));
    }
    let content = client.chat(system, &user, &schema, crate::model::TEMPERATURE)?;
    let parsed: Value = serde_json::from_str(content.trim())
        .map_err(|err| format!("highlights response is not valid JSON: {err}"))?;
    let highlights = parsed
        .get("highlights")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .ok_or_else(|| "highlights response had no non-empty `highlights` field".to_string())?;
    Ok(highlights.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn commit(subject: &str, body: &str) -> Commit {
        Commit {
            sha: "0".repeat(40),
            subject: subject.to_string(),
            body: body.to_string(),
        }
    }

    #[test]
    fn tag_schemes_are_inferred_and_ignored_tags_reported() {
        let tags: Vec<String> = ["v1.2.3", "preview-pr1011", "nightlyv0"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        let inference = infer_scheme(&tags);
        let scheme = inference.scheme.expect("scheme");
        assert_eq!(scheme.prefix, "v");
        assert_eq!(inference.ignored, 2);
        assert_eq!(
            inference.latest.expect("latest").version,
            Version::new(1, 2, 3)
        );
    }

    #[test]
    fn all_supported_tag_formats_parse() {
        for tag in ["v1.2.3", "1.2.3", "pkg-v1.2.3", "pkg@1.2.3"] {
            let parsed = parse_tag(tag).unwrap_or_else(|| panic!("{tag} should parse"));
            assert_eq!(parsed.version, Version::new(1, 2, 3));
        }
        assert_eq!(parse_tag("pkg-v1.2.3").unwrap().prefix, "pkg-v");
        assert_eq!(parse_tag("pkg@1.2.3").unwrap().prefix, "pkg@");
        assert!(parse_tag("preview-pr1011").is_none());
        assert!(parse_tag("nightlyv0").is_none());
    }

    #[test]
    fn version_is_rendered_with_original_prefix_and_padding() {
        let cases = [
            ("v1.2.3", "v1.2.4"),
            ("1.2.3", "1.2.4"),
            ("pkg-v1.2.3", "pkg-v1.2.4"),
            ("pkg@1.2.3", "pkg@1.2.4"),
            ("v01.02.03", "v01.02.04"),
        ];
        for (tag, expected) in cases {
            let inference = infer_scheme(&[tag.to_string()]);
            let scheme = inference.scheme.expect("scheme");
            let latest = inference.latest.expect("latest");
            let next = Bump::Patch.apply(&latest.version);
            assert_eq!(scheme.render_tag(&next), expected);
        }
    }

    #[test]
    fn bump_rules_are_deterministic() {
        assert_eq!(
            bump_for_commits(&[commit("feat!: drop node 16", "")]),
            Bump::Major
        );
        assert_eq!(
            bump_for_commits(&[commit(
                "feat(api): add endpoint",
                "BREAKING CHANGE: response shape"
            )]),
            Bump::Major
        );
        assert_eq!(
            bump_for_commits(&[commit("feat(api): add endpoint", "")]),
            Bump::Minor
        );
        assert_eq!(
            bump_for_commits(&[commit("fix(api): handle empty input", "")]),
            Bump::Patch
        );
        assert_eq!(
            bump_for_commits(&[commit("chore: tidy", ""), commit("docs: explain", "")]),
            Bump::None
        );
        assert_eq!(
            bump_for_commits(&[
                commit("fix(api): handle empty input", ""),
                commit("feat(api): add endpoint", ""),
            ]),
            Bump::Minor
        );
    }

    #[test]
    fn explicit_version_accepts_full_tag_and_bare_version() {
        let scheme = TagScheme {
            prefix: "pkg-v".to_string(),
            widths: [1, 1, 1],
        };
        assert_eq!(
            parse_explicit_version("pkg-v1.4.0", &scheme).unwrap(),
            Version::new(1, 4, 0)
        );
        assert_eq!(
            parse_explicit_version("v1.4.0", &scheme).unwrap(),
            Version::new(1, 4, 0)
        );
        assert!(parse_explicit_version("1.4", &scheme).is_err());
        assert!(parse_explicit_version("banana", &scheme).is_err());
    }

    #[test]
    fn changelog_insert_preserves_existing_bytes() {
        let old =
            "# Changelog\n\nPreamble stays.\n\n## [1.0.0] - 2020-01-01\n\n### Added\n\n- one\n";
        let section = render_section_with_style(
            HeadingStyle::Bracketed,
            "1.1.0",
            "2026-10-20",
            &[(ChangeGroup::Added, vec!["feat(api): add two".to_string()])],
            None,
        );
        let new = insert_section(old, &section);
        assert!(new.starts_with("# Changelog\n\nPreamble stays.\n\n## [1.1.0] - 2026-10-20"));
        let old_tail = &old[old.find("## [1.0.0]").unwrap()..];
        assert!(new.ends_with(old_tail));
        assert!(new.contains("- feat(api): add two"));
    }

    #[test]
    fn changelog_insert_goes_after_unreleased_block() {
        let old =
            "# Changelog\n\n## [Unreleased]\n\n- pending\n\n## [1.0.0] - 2020-01-01\n\n- old\n";
        let section = "## [1.1.0] - 2026-10-20\n\n### Fixed\n\n- fix: x\n";
        let new = insert_section(old, section);
        let unreleased = new.find("## [Unreleased]").unwrap();
        let new_section = new.find("## [1.1.0]").unwrap();
        let old_section = new.find("## [1.0.0]").unwrap();
        assert!(unreleased < new_section && new_section < old_section);
        assert!(new.ends_with("## [1.0.0] - 2020-01-01\n\n- old\n"));
    }

    #[test]
    fn heading_style_is_detected_from_existing_sections() {
        assert_eq!(
            detect_heading_style(Some("## [1.0.0] - 2020-01-01\n")),
            HeadingStyle::Bracketed
        );
        assert_eq!(
            detect_heading_style(Some("## 1.0.0\n")),
            HeadingStyle::Plain
        );
        assert_eq!(
            detect_heading_style(Some("## [Unreleased]\n\n## 1.0.0\n")),
            HeadingStyle::Plain
        );
        assert_eq!(detect_heading_style(None), HeadingStyle::Bracketed);
    }

    #[test]
    fn plain_heading_render_has_no_brackets_or_date() {
        let section = render_section_with_style(
            HeadingStyle::Plain,
            "1.1.0",
            "2026-10-20",
            &[(ChangeGroup::Fixed, vec!["fix: x".to_string()])],
            None,
        );
        assert!(section.starts_with("## 1.1.0\n"));
        assert!(!section.contains("2026-10-20"));
    }

    #[test]
    fn link_references_are_updated_when_present() {
        let content = "# Changelog\n\n## [1.1.0] - 2026-01-01\n\n[Unreleased]: https://github.com/u/r/compare/v1.1.0...HEAD\n[1.1.0]: https://github.com/u/r/compare/v1.0.0...v1.1.0\n[1.0.0]: https://github.com/u/r/compare/v0.9.0...v1.0.0\n";
        let updated = update_link_refs(
            content,
            "1.2.0",
            "v1.2.0",
            Some("v1.1.0"),
            Some("https://github.com/u/r"),
        );
        assert!(updated.contains("[Unreleased]: https://github.com/u/r/compare/v1.2.0...HEAD"));
        assert!(updated.contains("[1.2.0]: https://github.com/u/r/compare/v1.1.0...v1.2.0"));
        assert!(updated.contains("[1.1.0]: https://github.com/u/r/compare/v1.0.0...v1.1.0"));
    }

    #[test]
    fn civil_from_days_matches_known_dates() {
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(civil_from_days(19_782), (2024, 2, 29));
    }
}
