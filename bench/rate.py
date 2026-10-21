#!/usr/bin/env python3
"""Blind rating harness for the Sloth benchmark.

Three artefacts:
  1. `tasks.jsonl`   - blinded pairwise tasks for an automatic judge (A vs B, which is human?)
  2. `judge.py`      - runs a local model over those tasks
  3. `rating_sheet.md` - the same tasks laid out for a human rater

Plus deterministic fidelity checks (axis B proxy) computed here, with no LLM:
a subject that names a file, symbol, scope or issue number that the *filtered
digest the model received* does not support is an unsupported claim. The tool
removes lockfiles, vendored/generated paths, minified and snapshot files before
the model sees them, so the check runs against that filtered digest, not the
full case diff. These are the two failure modes the pilot actually produced, and
they are checkable without a model.
"""

import argparse, json, re, sys
from pathlib import Path

from run import build_digest

BENCH = Path(__file__).resolve().parent
ISSUE = re.compile(r"\(#\d+\)|#\d+")
# A commit subject is allowed to use ordinary English that never appears in the
# diff ("add option", "enable feature"). Flagging those is noise. Only tokens that
# look like they are naming something specific in the codebase can be checked
# against the diff without guessing.
CODE_TOKEN = re.compile(
    r"""(?x)
    \b[A-Za-z_][A-Za-z0-9]*_[A-Za-z0-9_]+\b              # snake_case
    | \b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b               # CamelCase
    | \b[A-Za-z_][A-Za-z0-9]*(?:::[A-Za-z_][A-Za-z0-9]*)+\b   # rust::path
    | \b[A-Za-z_][A-Za-z0-9]*(?:\.[A-Za-z_][A-Za-z0-9]*)+\b   # dotted.path
    """
)
FILE_TOKEN = re.compile(r"\b[\w./-]*\.[A-Za-z]{1,5}\b")
# words that legitimately appear in a subject without appearing verbatim in the diff
STOP = {
    "feat",
    "fix",
    "chore",
    "docs",
    "ci",
    "test",
    "refactor",
    "perf",
    "style",
    "build",
    "revert",
    "add",
    "adds",
    "added",
    "adding",
    "remove",
    "removed",
    "removing",
    "update",
    "updated",
    "updates",
    "use",
    "used",
    "using",
    "make",
    "makes",
    "support",
    "supports",
    "drop",
    "drops",
    "bump",
    "bumped",
    "handle",
    "handles",
    "return",
    "returns",
    "allow",
    "allows",
    "move",
    "moves",
    "rename",
    "renames",
    "extract",
    "extracts",
    "expose",
    "exposes",
    "replace",
    "replaces",
    "introduce",
    "introduces",
    "improve",
    "improves",
    "improve",
    "avoid",
    "avoids",
    "prevent",
    "prevents",
    "fixes",
    "fixed",
    "save",
    "saves",
    "set",
    "sets",
    "send",
    "sends",
    "read",
    "reads",
    "write",
    "writes",
    "parse",
    "parses",
    "load",
    "loads",
    "show",
    "shows",
    "with",
    "from",
    "into",
    "over",
    "when",
    "that",
    "this",
    "these",
    "those",
    "and",
    "the",
    "for",
    "not",
    "now",
    "only",
    "also",
    "into",
    "back",
    "out",
    "off",
    "via",
    "per",
    "all",
    "any",
    "can",
    "will",
    "should",
    "must",
    "does",
    "done",
    "make",
    "made",
    "get",
    "gets",
    "its",
    "it's",
    "has",
    "have",
    "was",
    "were",
    "are",
    "but",
    "you",
    "your",
    "our",
    "their",
    "github",
    "issue",
    "issues",
    "pull",
    "request",
    "requests",
    "type",
    "scope",
    "subject",
    "body",
    "commit",
    "commits",
    "message",
    "messages",
    "save",
    "format",
    "formats",
}


def norm_words(s):
    return {w.lower() for w in WORD.findall(s)}


def diff_tokens(diff):
    """Tokens the diff actually supports: identifiers appearing anywhere in the diff."""
    return {w.lower() for w in WORD.findall(diff)}


RECEIVED = "filtered digest (FILES + DIFFSTAT + filtered DIFF)"


def received_text(digest):
    """The change material the model was shown, in the prompt's own order.

    The file list and diffstat are the tool's full ones; the diff body is the
    filtered one. That is the only text a subject can be supported by.
    """
    return "\n".join(
        part
        for part in (digest.get("files"), digest.get("diffstat"), digest.get("diff"))
        if part
    )


def deterministic_fidelity(subject, scope, case, style, digest=None):
    """Return (unsupported_claims, notes). No model involved.

    The comparison text is the *filtered digest the model received*
    (``received_text``), never the full case diff. The tool removes lockfiles,
    vendored and generated paths, minified and snapshot files before the model
    sees them; a subject that names a term living only in one of those blocks
    was not making a claim the model could support, and a claim about a file
    the tool kept must not be judged against a text the tool altered.

    Only claims that can be checked without judgement:
      - an issue or pull-request number (never knowable from a local diff)
      - a file name named in the subject (the prompt forbids it)
      - a code identifier that does not occur anywhere in what was shown
    An unseen scope is a note, not a claim: a new subsystem can legitimately need
    a scope the repository has never used.
    """
    claims, notes = [], []
    digest = digest if digest is not None else build_digest(case)
    seen = received_text(digest).lower()
    files = [line for line in (digest.get("files") or "").splitlines() if line]
    bases = {Path(f).name.lower() for f in files} | {
        Path(f).stem.lower() for f in files
    }

    for m in ISSUE.findall(subject):
        claims.append(f"issue reference {m!r} is not knowable from a local diff")

    for m in FILE_TOKEN.findall(subject):
        low = m.lower()
        if low in bases or any(low in b for b in bases):
            continue
        if low in seen:
            continue
        claims.append(f"file name {m!r} is not part of the change")

    for m in CODE_TOKEN.findall(subject):
        low = m.lower()
        if low in seen:
            continue
        if any(low in b for b in bases):
            continue
        claims.append(f"identifier {m!r} does not appear in the diff")

    vocab = style.get("scope_vocab") or {}
    if scope and vocab and scope not in vocab:
        notes.append(f"scope {scope!r} not in this repo's scope vocabulary")

    return claims, notes


def body_convention(body, subject, style):
    """Does the body respect the repository's body habit?

    Two failure modes worth naming separately:
      - a small model asked for two fields often fills both with the same string
      - a model writes a body in a repository whose commits do not have one
    """
    body = (body or "").strip()
    if not body:
        return "ok", []
    notes = []
    usage = float(style.get("body_usage") or 0.0)
    if usage < 0.35:
        notes.append(
            "a body was written but this repository does not use commit bodies"
        )
    if body == (subject or "").strip():
        notes.append("the body duplicates the subject")
    return ("ok" if not notes else "violation"), notes


def build_tasks(results, cases, sample, seed=0):
    by_case = {c["id"]: c for c in cases}
    tasks = []
    for r in results:
        if r.get("status") != "ok" or r.get("model") in (None, "baseline"):
            continue
        c = by_case.get(r["case_id"])
        if not c:
            continue
        tasks.append(
            {
                "case_id": r["case_id"],
                "repo": r["repo"],
                "model": r["model"],
                "subject": r.get("subject", ""),
                "diff": c["diff"],
            }
        )
    # Stratified sample: spread across repositories, newest case per repo first.
    tasks.sort(key=lambda t: (t["repo"], t["case_id"]))
    per = max(1, sample // max(len({t["repo"] for t in tasks}), 1))
    picked, seen = [], {}
    for t in tasks:
        k = seen.get(t["repo"], 0)
        if k < per:
            picked.append(t)
            seen[t["repo"]] = k + 1
    for t in tasks:
        if len(picked) >= sample:
            break
        if t not in picked:
            picked.append(t)
    return picked[:sample]


def build_opponent_tasks(results, cases, styles, per_model, opponent, rng_seed=7):
    """One blinded task per (model, case) pair, model against `opponent`.

    `opponent` is either the human's own message or the no-model template. The
    judge never sees which side is which, and never sees which model wrote the
    subject it is judging.
    """
    import random

    from baseline import baseline_message

    rng = random.Random(rng_seed)
    by_case = {c["id"]: c for c in cases}
    by_model = {}
    for r in results:
        if r.get("status") != "ok" or r.get("model") in (None, "baseline"):
            continue
        by_model.setdefault(r["model"], []).append(r)

    tasks = []
    for model, rows in sorted(by_model.items()):
        rows.sort(key=lambda r: (r.get("repo", ""), r.get("case_id", "")))
        picked = []
        per_repo = max(1, per_model // max(len({r.get("repo") for r in rows}), 1))
        seen = {}
        for r in rows:
            k = seen.get(r.get("repo"), 0)
            if k < per_repo:
                picked.append(r)
                seen[r.get("repo")] = k + 1
        for r in rows:
            if len(picked) >= per_model:
                break
            if r not in picked:
                picked.append(r)
        picked = picked[:per_model]

        for r in picked:
            c = by_case.get(r["case_id"])
            if not c:
                continue
            model_subject = r.get("subject", "")
            if opponent == "human":
                other = c["subject"]
            else:
                st = styles.get(r["repo"], {})
                try:
                    other = baseline_message(st, c)
                except Exception:
                    continue
            a_is_model = rng.random() < 0.5
            A, B = (model_subject, other) if a_is_model else (other, model_subject)
            tasks.append(
                {
                    "task_id": f"{model.replace(':', '_')}__{opponent}__{r['case_id']}",
                    "repo": r["repo"],
                    "case_id": r["case_id"],
                    "model": model,
                    "opponent": opponent,
                    "a_is_model": a_is_model,
                    "model_side": "A" if a_is_model else "B",
                    "subject_a": A,
                    "subject_b": B,
                    "model_subject": model_subject,
                    "opponent_subject": other,
                    "human_subject": c["subject"],
                    "diff": c["diff"],
                    "files": [f["path"] for f in c["files"]],
                }
            )
    return tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(BENCH / "results.jsonl"))
    ap.add_argument("--cases", default=str(BENCH / "cases.jsonl"))
    ap.add_argument("--styles", default=str(BENCH / "styles.json"))
    ap.add_argument(
        "--per-model",
        type=int,
        default=20,
        help="blinded comparison tasks per model, per opponent",
    )
    ap.add_argument("--diff-chars", type=int, default=6000)
    ap.add_argument("--out-tasks", default=str(BENCH / "tasks_human.jsonl"))
    ap.add_argument("--out-tasks-baseline", default=str(BENCH / "tasks_baseline.jsonl"))
    ap.add_argument("--out-fidelity", default=str(BENCH / "fidelity.jsonl"))
    a = ap.parse_args()

    results = (
        [json.loads(l) for l in open(a.results)] if Path(a.results).exists() else []
    )
    cases = [json.loads(l) for l in open(a.cases)]
    styles = json.load(open(a.styles)) if Path(a.styles).exists() else {}
    by_case = {c["id"]: c for c in cases}

    # axis B proxy: deterministic, over every result
    with open(a.out_fidelity, "w") as f:
        for r in results:
            if r.get("status") != "ok":
                continue
            c = by_case.get(r["case_id"])
            if not c:
                continue
            st = styles.get(r["repo"], {})
            scope = None
            m = re.match(r"^[a-z]+\(([^)]+)\):", r.get("subject", ""))
            if m:
                scope = m.group(1)
            digest = build_digest(c)
            claims, notes = deterministic_fidelity(
                r.get("subject", ""), scope, c, st, digest
            )
            body_status, body_notes = body_convention(
                r.get("body", ""), r.get("subject", ""), st
            )
            f.write(
                json.dumps(
                    {
                        "case_id": r["case_id"],
                        "repo": r["repo"],
                        "model": r["model"],
                        "subject": r.get("subject", ""),
                        "body": r.get("body", ""),
                        "unsupported_claims": claims,
                        "notes": notes,
                        "clean": not claims,
                        # Which text the claim check ran against. A reader of
                        # the report needs this to know that no claim is judged
                        # against a block the tool never showed the model.
                        "compared_against": RECEIVED,
                        "digest_chars": len(digest["diff"]),
                        "body_status": body_status,
                        "body_notes": body_notes,
                    }
                )
                + "\n"
            )

    # Blinded pairwise tasks: model against the human's own message (T3) and
    # model against the no-model template (T5).
    for opponent, out_path in (
        ("human", a.out_tasks),
        ("baseline", a.out_tasks_baseline),
    ):
        built = build_opponent_tasks(results, cases, styles, a.per_model, opponent)
        with open(out_path, "w") as f:
            for t in built:
                t["diff"] = t["diff"][: a.diff_chars]
                f.write(json.dumps(t) + "\n")
        print(f"tasks ({opponent}): {len(built)} -> {out_path}")

    # human-usable sheet
    with open(BENCH / "rating_sheet.md", "w") as f:
        f.write("# Sloth benchmark - blind rating sheet\n\n")
        f.write(
            "For each pair, pick the message you would commit unedited, and note any "
            "claim the message makes that the diff does not support.\n\n"
        )
        for t in build_opponent_tasks(results, cases, styles, a.per_model, "human"):
            f.write(f"## {t['task_id']}  ({t['repo']})\n\n")
            f.write(f"**A** `{t['subject_a']}`\n\n**B** `{t['subject_b']}`\n\n")
            f.write(
                "Winner: A / B / neither    Unsupported claim? yes / no    Note: ____\n\n"
            )
            f.write("<details><summary>diff</summary>\n\n```\n")
            f.write(t["diff"][:4000])
            f.write("\n```\n\n</details>\n\n")

    print(f"fidelity rows -> {a.out_fidelity}")
    print(f"  claim check ran against the {RECEIVED}, not the full case diff")
    print(f"human sheet -> {BENCH / 'rating_sheet.md'}")


if __name__ == "__main__":
    sys.exit(main())
