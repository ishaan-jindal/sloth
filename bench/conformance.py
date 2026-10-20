#!/usr/bin/env python3
"""Axis A: deterministic conformance checks for a generated commit message.

No model, no network. ``check_message(style, subject, body)`` is importable;
the CLI reads a mined style JSON and prints the check result.
"""

import argparse
import json
import re
import sys

sys.dont_write_bytecode = True

from style import CONVENTIONAL_RE, GITMOJI_RE, PR_SUFFIX_RE, TICKET_RE

# Hard checks decide ``ok``. Soft checks are reported and only affect ``score``.
HARD_CHECKS = frozenset(
    {
        "prefix_shape",
        "type_known",
        "scope_known",
        "no_trailing_period",
        "length",
        "non_empty",
    }
)
SOFT_CHECKS = frozenset({"no_pr_number", "imperative_mood"})

LENGTH_CEILING = 72

# Common past-tense / gerund openers. Deliberately small and hardcoded: this is
# a heuristic, and it is a soft check that never decides ``ok``.
PAST_TENSE = {
    "added",
    "adjusted",
    "bumped",
    "changed",
    "cleaned",
    "created",
    "decreased",
    "deleted",
    "disabled",
    "documented",
    "enabled",
    "extracted",
    "fixed",
    "handled",
    "implemented",
    "improved",
    "increased",
    "introduced",
    "made",
    "merged",
    "moved",
    "refactored",
    "removed",
    "renamed",
    "replaced",
    "reverted",
    "simplified",
    "started",
    "stopped",
    "supported",
    "tweaked",
    "updated",
    "used",
}
# Words ending in "ed"/"ing" that are not past tense or gerunds.
MOOD_EXCEPTIONS = {
    "need",
    "feed",
    "seed",
    "embed",
    "shed",
    "speed",
    "red",
    "bed",
    "bring",
    "string",
    "ping",
    "ring",
    "sing",
    "wing",
    "spring",
    "thing",
    "during",
}


def _check(name, passed, detail):
    return {"name": name, "pass": bool(passed), "detail": detail}


def subject_text(style, subject):
    """Strip the shape's prefix and return the description part."""
    shape = (style or {}).get("shape", "freeform")
    if shape == "conventional":
        m = CONVENTIONAL_RE.match(subject)
        if m:
            return m.group("subject").strip()
    elif shape == "gitmoji":
        m = GITMOJI_RE.match(subject)
        if m:
            return subject[m.end() :].strip()
    elif shape == "ticket":
        m = TICKET_RE.match(subject)
        if m:
            return subject[m.end() :].strip()
    return subject.strip()


def _prefix_check(style, subject):
    shape = (style or {}).get("shape", "freeform")
    type_vocab = (style or {}).get("type_vocab") or {}
    if shape == "conventional":
        m = CONVENTIONAL_RE.match(subject)
        if not m:
            return _check(
                "prefix_shape",
                False,
                "expected `type(scope)!: description` (conventional)",
            )
        known = m.group("type") in type_vocab
        detail = f"type={m.group('type')!r}"
        if m.group("scope"):
            detail += f", scope={m.group('scope')!r}"
        detail += " (known type)" if known else " (type not in repo vocabulary)"
        return _check("prefix_shape", known, detail)
    if shape == "gitmoji":
        return _check(
            "prefix_shape",
            subject.startswith(":"),
            "expected a leading `:emoji:`"
            if not subject.startswith(":")
            else "leading `:` present",
        )
    if shape == "ticket":
        m = TICKET_RE.match(subject)
        return _check(
            "prefix_shape",
            bool(m),
            "expected a leading TICKET-123 token"
            if not m
            else f"ticket={m.group('ticket')!r}",
        )
    return _check("prefix_shape", True, "freeform repo: no prefix required")


def _type_known_check(style, subject):
    m = CONVENTIONAL_RE.match(subject)
    vocab = (style or {}).get("type_vocab") or {}
    if not m:
        return _check("type_known", False, "no conventional type to check")
    found = m.group("type")
    return _check(
        "type_known",
        found in vocab,
        f"type={found!r}; known types: {', '.join(sorted(vocab)) or '(none)'}",
    )


def _scope_known_check(style, subject):
    m = CONVENTIONAL_RE.match(subject)
    vocab = (style or {}).get("scope_vocab") or {}
    if not m:
        return _check("scope_known", False, "no conventional scope to check")
    found = m.group("scope")
    if found is None:
        # A missing scope is only wrong when this repository scopes nearly every
        # commit. Most repos mix scoped and unscoped subjects, and an unscoped
        # subject there is perfectly normal.
        rate = float((style or {}).get("scope_rate") or 0.0)
        if rate >= 0.90:
            return _check(
                "scope_known",
                False,
                f"missing scope; {rate:.0%} of this repo's conventional commits are scoped",
            )
        return _check(
            "scope_known",
            True,
            f"no scope; {rate:.0%} of this repo's conventional commits are scoped, "
            "so an unscoped subject is normal here",
        )
    return _check(
        "scope_known",
        found in vocab,
        f"scope={found!r}; known scopes: {', '.join(sorted(vocab)) or '(none)'}",
    )


def _length_check(style, subject):
    p90 = int(((style or {}).get("subject_len") or {}).get("p90") or 0)
    limit = max(LENGTH_CEILING, p90)
    length = len(subject.strip())
    return _check(
        "length",
        length <= limit,
        f"length {length}; limit {limit} (72 ceiling, repo p90 {p90})",
    )


def _pr_number_check(style, subject):
    found = PR_SUFFIX_RE.search(subject)
    pr_suffix = bool((style or {}).get("pr_suffix"))
    if not found:
        return _check("no_pr_number", True, "no `(#NNNN)` token")
    if pr_suffix:
        return _check(
            "no_pr_number",
            True,
            "contains `(#NNNN)`; repo uses PR suffixes, but a local tool cannot know the number",
        )
    return _check(
        "no_pr_number",
        False,
        "contains `(#NNNN)`; repo does not use PR suffixes",
    )


def _imperative_check(style, subject):
    text = subject_text(style, subject)
    if not text:
        return _check("imperative_mood", True, "empty subject text")
    word = re.sub(r"[^A-Za-z]", "", text.split()[0]).lower()
    if not word:
        return _check("imperative_mood", True, "first token has no letters")
    bad = word in PAST_TENSE
    if not bad and word not in MOOD_EXCEPTIONS and len(word) > 4:
        bad = word.endswith("ed") or (len(word) > 5 and word.endswith("ing"))
    return _check(
        "imperative_mood",
        not bad,
        f"first word={word!r}"
        + (" is past tense/gerund" if bad else " looks imperative"),
    )


def check_message(style, subject, body=""):
    """Return {"ok", "checks", "score"} for one candidate message."""
    subject = subject or ""
    body = body or ""
    style = style or {}
    shape = style.get("shape", "freeform")
    scope_vocab = style.get("scope_vocab") or {}

    checks = [_prefix_check(style, subject)]
    if shape == "conventional":
        checks.append(_type_known_check(style, subject))
        if scope_vocab:
            checks.append(_scope_known_check(style, subject))
    checks.append(
        _check(
            "no_trailing_period",
            not subject.strip().endswith("."),
            "subject ends with '.'"
            if subject.strip().endswith(".")
            else "no trailing period",
        )
    )
    checks.append(_length_check(style, subject))
    checks.append(
        _check(
            "non_empty",
            bool(subject.strip()),
            "subject is non-empty" if subject.strip() else "subject is empty",
        )
    )
    checks.append(_pr_number_check(style, subject))
    checks.append(_imperative_check(style, subject))

    passed = sum(1 for c in checks if c["pass"])
    ok = all(c["pass"] for c in checks if c["name"] in HARD_CHECKS)
    return {
        "ok": ok,
        "checks": checks,
        "score": round(passed / len(checks), 3) if checks else 0.0,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--style", required=True, help="path to mined style JSON")
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", default="")
    args = parser.parse_args(argv)

    try:
        with open(args.style) as fh:
            style = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"conformance: cannot read style: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(check_message(style, args.subject, args.body), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
