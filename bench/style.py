#!/usr/bin/env python3
"""Mine a repository's commit-message convention.

Deterministic only: no model calls, no network. The pure classifier
(``classify_subjects``) is importable and testable without a git repo; the
miner (``mine_style``) supplies subjects and bodies from ``git log``.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SHAPES = ("conventional", "gitmoji", "ticket", "freeform")
MAJORITY_THRESHOLD = 0.60
PR_SUFFIX_THRESHOLD = 0.20
# A body made only of git trailers (sign-off, co-author, review) is not a commit
# body in any meaningful sense. Counting it as one makes a repo that never writes
# prose bodies look like one that always does.
TRAILER_RE = re.compile(
    r"^(Signed-off-by|Co-authored-by|Reviewed-by|Tested-by|CC|Acked-by):", re.I
)
HEADING_RE = re.compile(r"^(#{1,6}\s+\S.*)$", re.M)
BODY_USAGE_THRESHOLD = 0.35
BODY_SECTION_THRESHOLD = 0.30

CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-z][a-z0-9]*)(?:\((?P<scope>[^)]+)\))?(?P<bang>!)?:\s(?P<subject>.*)$"
)
GITMOJI_RE = re.compile(r"^:(?P<emoji>[a-z0-9_+-]+):")
TICKET_RE = re.compile(r"^(?P<ticket>[A-Z][A-Z0-9]+-\d+)")
PR_SUFFIX_RE = re.compile(r"\(#\d+\)$")
PR_STRIP_RE = re.compile(r"\s*\(#\d+\)\s*$")
PR_SUFFIX_PATTERN = r"\(#\d+\)$"

BREAKING_TYPES = {"breaking", "breaking-change"}


def classify_subject(subject):
    """Return (shape, match) for one subject. First matching shape wins."""
    m = CONVENTIONAL_RE.match(subject)
    if m:
        return "conventional", m
    m = GITMOJI_RE.match(subject)
    if m:
        return "gitmoji", m
    m = TICKET_RE.match(subject)
    if m:
        return "ticket", m
    return "freeform", None


def _most_common(counts):
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _median_int(values):
    if not values:
        return 0
    s = sorted(values)
    n = len(s)
    if n % 2:
        return int(s[n // 2])
    return int((s[n // 2 - 1] + s[n // 2]) // 2)


def _percentile(values, p):
    if not values:
        return 0
    s = sorted(values)
    if len(s) == 1:
        return int(s[0])
    idx = int(round((p / 100.0) * (len(s) - 1)))
    return int(s[max(0, min(len(s) - 1, idx))])


def classify_subjects(subjects):
    """Classify a list of subject lines (pure, no git, no bodies).

    Returns the same subject-derived keys as ``mine_style`` minus bodies,
    exemplars and raw samples.
    """
    subjects = [s or "" for s in subjects]
    n = len(subjects)

    classified = [classify_subject(s) for s in subjects]
    parsed = [shape for shape, _ in classified]
    counts = {shape_name: 0 for shape_name in SHAPES}
    for shape in parsed:
        counts[shape] += 1

    rates = {shape: (counts[shape] / n if n else 0.0) for shape in SHAPES}
    majority = _most_common(counts) if n else None
    majority_rate = rates[majority] if majority else 0.0

    if majority and majority_rate >= MAJORITY_THRESHOLD:
        shape = majority
        confidence = (
            "high"
            if majority_rate >= 0.85
            else "medium"
            if majority_rate >= 0.60
            else "low"
        )
    else:
        shape = "freeform"
        confidence = "low"

    type_vocab = {}
    scope_vocab = {}
    emoji_vocab = {}
    breaking_markers = []
    conventional_total = 0
    conventional_scoped = 0
    for subject, (parsed_shape, m) in zip(subjects, classified):
        if subject.lower().startswith("breaking-change"):
            if "breaking-change" not in breaking_markers:
                breaking_markers.append("breaking-change")
        if parsed_shape == "conventional" and m:
            conventional_total += 1
            type_vocab[m.group("type")] = type_vocab.get(m.group("type"), 0) + 1
            scope = m.group("scope")
            if scope:
                scope_vocab[scope] = scope_vocab.get(scope, 0) + 1
                conventional_scoped += 1
            if m.group("bang") and "!" not in breaking_markers:
                breaking_markers.append("!")
            if m.group("type") in BREAKING_TYPES:
                marker = m.group("type")
                if marker not in breaking_markers:
                    breaking_markers.append(marker)
        elif parsed_shape == "gitmoji" and m:
            emoji_vocab[m.group("emoji")] = emoji_vocab.get(m.group("emoji"), 0) + 1

    pr_matches = sum(1 for s in subjects if PR_SUFFIX_RE.search(s))
    pr_suffix = bool(n) and (pr_matches / n) >= PR_SUFFIX_THRESHOLD

    lengths = [len(s.strip()) for s, p in zip(subjects, parsed) if p == shape]
    if not lengths:
        lengths = [len(s.strip()) for s in subjects]

    result = {
        "shape": shape,
        "confidence": confidence,
        "match_rates": {
            shape_name: round(rate, 3) for shape_name, rate in rates.items()
        },
        "breaking_markers": breaking_markers,
        "pr_suffix": pr_suffix,
        "pr_suffix_pattern": PR_SUFFIX_PATTERN,
        "subject_len": {
            "median": _median_int(lengths),
            "p90": _percentile(lengths, 90),
        },
        "n_subjects": n,
    }
    # The vocabularies are reported for every shape, not only for conventional
    # ones, so the Rust implementation and this one can be compared field for
    # field. A freeform repository never uses them to build a pattern, so this
    # changes no behaviour.
    result["type_vocab"] = dict(
        sorted(type_vocab.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    if scope_vocab:
        result["scope_vocab"] = dict(
            sorted(scope_vocab.items(), key=lambda kv: (-kv[1], kv[0]))
        )
    # How much of the repo's conventional history carries a scope at all.
    # A repo that scopes almost everything expects a scope; one that mixes
    # scoped and unscoped subjects does not.
    result["scope_rate"] = (
        round(conventional_scoped / conventional_total, 3)
        if conventional_total
        else 0.0
    )
    if emoji_vocab:
        result["emoji_vocab"] = dict(
            sorted(emoji_vocab.items(), key=lambda kv: (-kv[1], kv[0]))
        )
    return result


def _git(repo, *args):
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo}: {proc.stderr.strip()[:300]}"
        )
    return proc.stdout


def list_commits(repo_path, commits=300):
    """Return [(sha, subject)] newest first, non-merges only."""
    raw = _git(
        repo_path, "log", "--no-merges", f"-n{int(commits)}", "--format=%H%x1f%s"
    )
    rows = []
    for line in raw.splitlines():
        if "\x1f" not in line:
            continue
        sha, subject = line.split("\x1f", 1)
        rows.append((sha, subject))
    return rows


def read_bodies(repo_path, shas):
    """Read the body of each commit with ``git log -1 --format=%b <sha>``."""
    return [_git(repo_path, "log", "-1", "--format=%b", sha).strip() for sha in shas]


def is_trailer_only(body):
    """True when every non-blank line of ``body`` is a git trailer."""
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    return bool(lines) and all(TRAILER_RE.match(ln) for ln in lines)


def prose_body(body):
    """The body with trailer lines removed, or '' when nothing but trailers remain."""
    keep = [
        ln
        for ln in body.splitlines()
        if ln.strip() and not TRAILER_RE.match(ln.strip())
    ]
    return "\n".join(keep).strip()


def body_sections(body):
    """Markdown headings appearing in a body, in order.

    The leading hashes are stripped: the heading text is what the prompt
    quotes, and the Rust implementation stores it the same way.
    """
    return [h.strip().lstrip("#").strip() for h in HEADING_RE.findall(body)]


def mine_style(repo_path, commits=300):
    """Mine a repository's convention. Importable, deterministic, no network."""
    commits = int(commits)
    pairs = list_commits(repo_path, commits)
    subjects = [subject for _, subject in pairs]
    style = classify_subjects(subjects)

    bodies = read_bodies(repo_path, [sha for sha, _ in pairs]) if pairs else []
    prose = [prose_body(b) for b in bodies]
    prose = [p for p in prose if p]
    style["body_usage"] = round(len(prose) / len(bodies), 3) if bodies else 0.0
    style["body_trailer_rate"] = (
        round(
            sum(1 for b in bodies if b.strip() and is_trailer_only(b)) / len(bodies), 3
        )
        if bodies
        else 0.0
    )
    # How long this repository's prose bodies actually are, in words. A model told
    # the median will not pad a sixteen-word body out to four hundred tokens.
    # Markdown heading markers are not words.
    word_counts = sorted(
        len([w for w in p.split() if not all(ch == "#" for ch in w)]) for p in prose
    )
    style["body_words"] = {
        "median": _median_int(word_counts) if word_counts else 0,
        "p90": _percentile(word_counts, 90) if word_counts else 0,
    }
    # The dominant heading sequence across prose bodies, e.g. ("## What?", "## Why?").
    seqs = {}
    for p in prose:
        heads = body_sections(p)
        if heads:
            seqs[tuple(heads)] = seqs.get(tuple(heads), 0) + 1
    style["body_sections"] = []
    style["body_section_rate"] = 0.0
    if prose and seqs:
        best_heads, best_count = max(seqs.items(), key=lambda kv: (kv[1], -len(kv[0])))
        rate = best_count / len(prose)
        if rate >= BODY_SECTION_THRESHOLD:
            style["body_sections"] = list(best_heads)
            style["body_section_rate"] = round(rate, 3)

    markers = list(style.get("breaking_markers", []))
    for body in bodies:
        if "BREAKING CHANGE" in body or "BREAKING-CHANGE" in body:
            if "BREAKING CHANGE" not in markers:
                markers.append("BREAKING CHANGE")
            break
    style["breaking_markers"] = markers

    shape = style["shape"]
    parsed = [classify_subject(s)[0] for s in subjects]
    candidates = [s for s, p in zip(subjects, parsed) if p == shape][:5]
    if not candidates:
        candidates = subjects[:5]
    if style["pr_suffix"]:
        style["exemplar_raw"] = candidates
        style["exemplars"] = [PR_STRIP_RE.sub("", s).strip() for s in candidates]
    else:
        style["exemplars"] = candidates
    style["sample_subjects"] = subjects[:12]
    style["repo"] = str(repo_path)
    style["n_commits"] = len(subjects)
    return style


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="path to the git repository")
    parser.add_argument("--commits", type=int, default=300)
    parser.add_argument("--json", action="store_true", help="compact single-line JSON")
    args = parser.parse_args(argv)

    try:
        style = mine_style(args.repo, args.commits)
    except Exception as exc:  # noqa: BLE001 - CLI must not traceback
        print(f"style: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(style))
    else:
        print(json.dumps(style, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
