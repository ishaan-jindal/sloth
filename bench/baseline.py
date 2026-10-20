#!/usr/bin/env python3
"""The no-model baseline: a deterministic template from the diffstat.

Free, instant and deliberately fair: it uses the repository's mined type,
scope and emoji vocabulary. It is the floor every model must clear.
"""

import argparse
import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True

DEFAULT_CASES = Path(__file__).resolve().parent / "cases.jsonl"
LENGTH_CEILING = 72


def _top(counts):
    if not counts:
        return None
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _basename(path):
    return str(path or "").rstrip("/").rsplit("/", 1)[-1]


def _truncate(text, limit=LENGTH_CEILING):
    text = text.strip()
    while text.endswith("."):
        text = text[:-1].rstrip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0].rstrip(" ,;:-")
    cut = cut.rstrip().rstrip(".")
    return cut or text[:limit].rstrip()


def _description(case):
    files = case.get("files") or []
    count = len(files)
    if count == 1:
        name = _basename(files[0].get("path"))
        return name or "update file"
    if count > 1:
        return f"update {count} files"
    return "update files"


def baseline_message_with_body(style, case):
    """Return (subject, body). Body is always empty: the baseline has none."""
    style = style or {}
    description = _description(case or {})
    shape = style.get("shape", "freeform")

    if shape == "conventional":
        commit_type = _top(style.get("type_vocab") or {}) or "chore"
        scope = _top(style.get("scope_vocab") or {})
        message = (
            f"{commit_type}({scope}): {description}"
            if scope
            else f"{commit_type}: {description}"
        )
    elif shape == "gitmoji":
        emoji = _top(style.get("emoji_vocab") or {}) or "sparkles"
        message = f":{emoji}: {description}"
    else:
        message = description

    return _truncate(message), ""


def baseline_message(style, case):
    return baseline_message_with_body(style, case)[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--repo", required=True, help="path to the case's git repo")
    args = parser.parse_args(argv)

    try:
        with open(args.cases) as fh:
            cases = [json.loads(line) for line in fh if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        print(f"baseline: cannot read cases: {exc}", file=sys.stderr)
        return 1
    case = next((c for c in cases if c.get("id") == args.case_id), None)
    if case is None:
        print(f"baseline: no case with id {args.case_id!r}", file=sys.stderr)
        return 1

    from style import mine_style

    try:
        style = mine_style(args.repo)
    except Exception as exc:  # noqa: BLE001 - CLI must not traceback
        print(f"baseline: cannot mine style: {exc}", file=sys.stderr)
        return 1

    print(baseline_message(style, case))
    return 0


if __name__ == "__main__":
    sys.exit(main())
