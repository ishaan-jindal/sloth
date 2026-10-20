#!/usr/bin/env python3
"""Runnable self-check for the benchmark harness. No framework, no network.

python3 bench/selftest.py
"""

import sys

sys.dont_write_bytecode = True

from baseline import baseline_message
from conformance import check_message
from run import (
    build_payload,
    is_over_budget,
    render_prompt,
    split_template,
    strip_system_payload,
)
from schema import build_schema, format_rule, subject_pattern
from style import classify_subjects

CONVENTIONAL_SUBJECTS = [
    "feat(ui): add thing",
    "feat(core): add parser",
    "fix(ui): fix button",
    "feat(ui): add menu",
    "docs(readme): update docs",
    "fix(core): handle null",
]


def test_style_conventional():
    style = classify_subjects(CONVENTIONAL_SUBJECTS)
    assert style["shape"] == "conventional", style["shape"]
    assert style["confidence"] == "high", style["confidence"]
    assert style["type_vocab"] == {"feat": 3, "fix": 2, "docs": 1}, style["type_vocab"]
    assert style["scope_vocab"] == {"ui": 3, "core": 2, "readme": 1}, style[
        "scope_vocab"
    ]
    assert style["subject_len"]["median"] > 0
    assert style["match_rates"]["conventional"] == 1.0
    return style


def test_style_gitmoji():
    style = classify_subjects(
        [":sparkles: add thing", ":bug: fix crash", ":memo: update docs"]
    )
    assert style["shape"] == "gitmoji", style["shape"]
    assert style["emoji_vocab"]["sparkles"] == 1, style["emoji_vocab"]
    return style


def test_style_mixed_is_freeform_low():
    subjects = [
        "feat(ui): add thing",
        "fix(ui): fix button",
        "tidy things up",
        "misc cleanup",
        ":sparkles: add glow",
    ]
    style = classify_subjects(subjects)
    assert style["shape"] == "freeform", style["shape"]
    assert style["confidence"] == "low", style["confidence"]
    # The vocabularies are reported for every shape, so the Rust miner and this
    # one can be compared field for field. A freeform repository never uses them
    # to build a pattern.
    assert style["type_vocab"] == {"feat": 1, "fix": 1}, style.get("type_vocab")
    assert style["scope_vocab"] == {"ui": 2}, style.get("scope_vocab")


def test_conformance_passes():
    style = classify_subjects(CONVENTIONAL_SUBJECTS)
    result = check_message(style, "feat(ui): add thing", "")
    assert result["ok"] is True, result
    assert result["score"] == 1.0, result["score"]
    names = [c["name"] for c in result["checks"]]
    assert names[0] == "prefix_shape", names
    assert "imperative_mood" in names and "no_pr_number" in names, names


def test_conformance_unknown_type_fails():
    style = classify_subjects(CONVENTIONAL_SUBJECTS)
    result = check_message(style, "wibble(ui): add thing", "")
    type_check = next(c for c in result["checks"] if c["name"] == "type_known")
    assert type_check["pass"] is False, type_check
    assert result["ok"] is False, result


def test_conformance_trailing_period_fails():
    style = classify_subjects(CONVENTIONAL_SUBJECTS)
    result = check_message(style, "feat(ui): add thing.", "")
    period = next(c for c in result["checks"] if c["name"] == "no_trailing_period")
    assert period["pass"] is False, period
    assert result["ok"] is False, result


def test_conformance_too_long_fails():
    style = classify_subjects(CONVENTIONAL_SUBJECTS)
    subject = "feat(ui): " + "a " * 50
    result = check_message(style, subject, "")
    length = next(c for c in result["checks"] if c["name"] == "length")
    assert length["pass"] is False, length
    assert result["ok"] is False, result


def test_conformance_unknown_scope_fails():
    style = classify_subjects(CONVENTIONAL_SUBJECTS)
    result = check_message(style, "feat(zzz): add thing", "")
    scope = next(c for c in result["checks"] if c["name"] == "scope_known")
    assert scope["pass"] is False, scope
    assert result["ok"] is False, result


def test_conformance_soft_failure_keeps_ok():
    style = classify_subjects(CONVENTIONAL_SUBJECTS)
    result = check_message(style, "feat(ui): adding a thing", "")
    mood = next(c for c in result["checks"] if c["name"] == "imperative_mood")
    assert mood["pass"] is False, mood
    assert result["ok"] is True, result
    assert result["score"] < 1.0, result["score"]


def test_baseline_single_file():
    style = classify_subjects(CONVENTIONAL_SUBJECTS)
    case = {"files": [{"path": "src/app.go", "add": "1", "del": "0"}]}
    message = baseline_message(style, case)
    assert "app.go" in message, message
    assert len(message) <= 72, message
    assert not message.endswith("."), message


def test_baseline_multi_file():
    style = classify_subjects(CONVENTIONAL_SUBJECTS)
    case = {
        "files": [
            {"path": "a.go", "add": "1", "del": "0"},
            {"path": "b.go", "add": "2", "del": "1"},
            {"path": "c.go", "add": "0", "del": "3"},
        ]
    }
    message = baseline_message(style, case)
    assert "update 3 files" in message, message
    assert len(message) <= 72, message
    assert not message.endswith("."), message


def test_baseline_truncates_long_path():
    style = classify_subjects(CONVENTIONAL_SUBJECTS)
    case = {"files": [{"path": "src/" + "x" * 120 + ".go", "add": "1", "del": "0"}]}
    message = baseline_message(style, case)
    assert len(message) <= 72, (len(message), message)
    assert not message.endswith("."), message


def test_budget_oversized():
    case = {"diff": "x" * 100_000}
    assert is_over_budget(case, 2000, 40000, 8192) is True
    assert is_over_budget({"diff": "x" * 100}, 2000, 40000, 8192) is False
    assert is_over_budget({"diff": "x" * 1000}, 2000, 10**9, 100) is True


def test_render_prompt_leaves_unknown_token():
    warnings = []
    rendered = render_prompt("A {{X}} B {{UNKNOWN}} {{X}}", {"X": "y"}, warnings)
    assert rendered == "A y B {{UNKNOWN}} y", rendered
    assert warnings == ["UNKNOWN"], warnings


def test_render_prompt_reports_all_missing():
    warnings = []
    rendered = render_prompt("{{ONE}} {{TWO}}", {}, warnings)
    assert rendered == "{{ONE}} {{TWO}}", rendered
    assert warnings == ["ONE", "TWO"], warnings


def test_split_template():
    system, user = split_template("SYSTEM\n--- USER ---\nUSER")
    assert system == "SYSTEM", system
    assert user == "USER", user
    system, user = split_template("ONLY SYSTEM")
    assert system == "ONLY SYSTEM", system
    assert user == "", user


def test_strip_system_payload():
    template = (
        "INSTRUCTIONS\n\n## Files changed\n{{FILES}}\n\n## Diffstat\n"
        "{{DIFFSTAT}}\n\n## Diff\n--- BEGIN DIFF ---\n{{DIFF}}\n"
        "--- END DIFF ---\n\n## Output\nJSON\n"
    )
    stripped = strip_system_payload(template)
    assert "## Files changed" not in stripped, stripped
    assert "{{DIFF}}" not in stripped, stripped
    assert "{{FILES}}" not in stripped, stripped
    assert "INSTRUCTIONS" in stripped, stripped
    assert "## Output" in stripped, stripped


def test_payload_pins_think_off_and_schema():
    schema = {"type": "object"}
    payload = build_payload("m", "sys", "user", schema, 4096)
    assert payload["think"] is False, payload
    assert payload["keep_alive"] == -1, payload
    assert payload["format"] is schema, payload
    assert payload["stream"] is False, payload
    assert payload["options"]["num_ctx"] == 4096, payload
    assert [m["role"] for m in payload["messages"]] == ["system", "user"], payload


# --------------------------------------------------------------------------- #
# The subject pattern is a generation constraint, not a check. These tests exist
# because two of the compiler's failure modes are SILENT: a nested alternation
# is accepted and not enforced, and a negated character class corrupts
# generation outright. A regression here would quietly destroy conformance.
# --------------------------------------------------------------------------- #
def _pattern_violations(pattern):
    """Compiler constraints that fail silently or corrupt generation.

    Established by experiment against the model server, not guessed:
      - an anchor (`^`/`$`) inside a group is accepted and then NOT enforced
      - an alternation nested two groups deep is accepted and NOT enforced
      - a negated character class corrupts generation entirely
    A top-level alternation of alternatives is fine and is enforced.
    """
    problems = []
    depth = 0
    starts = []
    for i, ch in enumerate(pattern):
        if ch == "(":
            starts.append(i)
            depth += 1
        elif ch == ")":
            start = starts.pop() if starts else 0
            depth -= 1
            inner = pattern[start + 1 : i]
            if "^" in inner or "$" in inner:
                problems.append(f"anchor inside a group: {inner!r}")
    depth = 0
    for ch in pattern:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "|" and depth >= 2:
            problems.append("alternation nested inside a group")
    if "[^" in pattern:
        problems.append("negated character class")
    return problems


def test_schema_pattern_forms_are_compiler_safe():
    styles = [
        {"shape": "conventional", "type_vocab": {"feat": 3, "fix": 2}},
        {"shape": "gitmoji", "emoji_vocab": {":sparkles:": 1}},
        {"shape": "ticket"},
        {"shape": "freeform"},
    ]
    for style in styles:
        pattern = subject_pattern(style)
        problems = _pattern_violations(pattern)
        assert not problems, f"{style['shape']}: {problems} in {pattern}"


def test_schema_pattern_conventional():
    style = {"shape": "conventional", "type_vocab": {"feat": 3, "fix": 2}}
    pattern = subject_pattern(style)
    import re

    rx = re.compile(pattern)
    assert rx.match("feat: add thing"), pattern
    assert rx.match("feat(tui): add thing"), pattern
    assert rx.match("fix(core)!: drop the old parser"), pattern
    assert not rx.match("add thing"), pattern
    assert not rx.match("chore: bump the version"), (
        pattern
    )  # chore is not in this repo's vocabulary
    assert not rx.match("feat:add thing"), (
        pattern
    )  # the space after the colon is required
    assert not rx.match("feat(): add thing"), pattern  # an empty scope is not a scope


def test_schema_pattern_freeform():
    import re

    rx = re.compile(subject_pattern({"shape": "freeform"}))
    assert rx.match("update the readme")
    assert not rx.match("x" * 73)


def test_schema_pattern_gitmoji_and_ticket():
    import re

    rx = re.compile(subject_pattern({"shape": "gitmoji"}))
    assert rx.match(":sparkles: add the thing")
    assert not rx.match("add the thing")
    rx = re.compile(subject_pattern({"shape": "ticket"}))
    assert rx.match("PROJ-123: add the thing")
    assert not rx.match("add the thing")


def test_schema_shape_is_complete():
    schema = build_schema({"shape": "conventional", "type_vocab": {"feat": 1}})
    assert schema["required"] == ["subject", "body"]
    assert schema["additionalProperties"] is False
    assert "pattern" in schema["properties"]["subject"]
    assert "pattern" not in schema["properties"]["body"]


def test_format_rule_names_the_types():
    rule = format_rule({"shape": "conventional", "type_vocab": {"feat": 3, "fix": 2}})
    assert "`feat`" in rule and "`fix`" in rule, rule
    assert "MUST" in rule, rule
    assert "MUST" in format_rule({"shape": "gitmoji"}), "gitmoji needs a stated rule"
    assert "MUST" in format_rule({"shape": "ticket"}), "ticket needs a stated rule"


def test_scope_rate_is_mined():
    style = classify_subjects(
        [
            "feat(ui): add thing",
            "feat(core): add parser",
            "fix: handle null",
            "chore: bump",
        ]
    )
    # 2 of 4 conventional subjects carry a scope
    assert abs(style["scope_rate"] - 0.5) < 1e-9, style["scope_rate"]


def test_conformance_missing_scope_is_fine_when_repo_rarely_scopes():
    style = {
        "shape": "conventional",
        "type_vocab": {"feat": 3},
        "scope_vocab": {"ui": 3},
        "scope_rate": 0.32,
        "subject_len": {"median": 30, "p90": 60},
        "pr_suffix": False,
    }
    result = check_message(style, "feat: add a thing")
    assert result["ok"] is True, result
    # an invented scope is still a violation
    result = check_message(style, "feat(nope): add a thing")
    assert result["ok"] is False, result
    # but a repo that always scopes does expect one
    style["scope_rate"] = 0.95
    result = check_message(style, "feat: add a thing")
    assert result["ok"] is False, result


def test_schema_pattern_template_matches_the_rust_implementation():
    """The Rust CLI builds the same pattern from the same mined style.

    If these two drift apart, the benchmark and the shipped tool stop measuring
    the same thing, and the numbers in the report stop describing the tool.
    Compare the template and the type set, not the order of the alternatives.
    """
    style = {"shape": "conventional", "type_vocab": {"feat": 1, "fix": 1, "docs": 1}}
    pattern = subject_pattern(style)
    types = pattern.split("(")[1].split(")")[0].split("|")
    assert sorted(types) == ["docs", "feat", "fix"], pattern
    assert pattern.endswith(r"(\([a-z0-9._/-]+\))?!?: .+$"), pattern
    for shape, expected in [
        ("gitmoji", r"^:[a-z0-9_+-]+: .+$"),
        ("ticket", r"^[A-Z][A-Z0-9]+-[0-9]+:? .+$"),
        ("freeform", r"^.{1,72}$"),
    ]:
        assert subject_pattern({"shape": shape}) == expected, shape


def test_schema_body_length_is_capped():
    """The body cap comes from the repository, and is zero where the repository
    does not use commit bodies. The grammar compiler enforces it, so a body of
    length zero is not a request, it is an impossibility."""
    style = {
        "shape": "conventional",
        "type_vocab": {"feat": 1},
        "body_usage": 0.14,
        "body_words": {"median": 85, "p90": 138},
    }
    assert build_schema(style)["properties"]["body"]["maxLength"] == 0

    style["body_usage"] = 0.81
    style["body_words"] = {"median": 45, "p90": 191}
    assert build_schema(style)["properties"]["body"]["maxLength"] == 1146

    style["body_words"] = {"median": 1, "p90": 10_000}
    assert build_schema(style)["properties"]["body"]["maxLength"] == 2000

    style["body_words"] = {"median": 1, "p90": 1}
    assert build_schema(style)["properties"]["body"]["maxLength"] == 300


def test_schema_subject_length_matches_the_conformance_bound():
    style = {
        "shape": "conventional",
        "type_vocab": {"feat": 1},
        "subject_len": {"median": 30, "p90": 60},
    }
    assert build_schema(style)["properties"]["subject"]["maxLength"] == 72
    style["subject_len"]["p90"] = 100
    assert build_schema(style)["properties"]["subject"]["maxLength"] == 100


def main():
    tests = [
        test_style_conventional,
        test_style_gitmoji,
        test_style_mixed_is_freeform_low,
        test_conformance_passes,
        test_conformance_unknown_type_fails,
        test_conformance_trailing_period_fails,
        test_conformance_too_long_fails,
        test_conformance_unknown_scope_fails,
        test_conformance_soft_failure_keeps_ok,
        test_baseline_single_file,
        test_baseline_multi_file,
        test_baseline_truncates_long_path,
        test_budget_oversized,
        test_render_prompt_leaves_unknown_token,
        test_render_prompt_reports_all_missing,
        test_split_template,
        test_strip_system_payload,
        test_payload_pins_think_off_and_schema,
        test_schema_pattern_forms_are_compiler_safe,
        test_schema_pattern_conventional,
        test_schema_pattern_freeform,
        test_schema_pattern_gitmoji_and_ticket,
        test_schema_shape_is_complete,
        test_format_rule_names_the_types,
        test_scope_rate_is_mined,
        test_conformance_missing_scope_is_fine_when_repo_rarely_scopes,
        test_schema_pattern_template_matches_the_rust_implementation,
        test_schema_body_length_is_capped,
        test_schema_subject_length_matches_the_conformance_bound,
    ]
    for test in tests:
        test()
        print(f"ok   {test.__name__}")
    print(f"OK: {len(tests)} checks passed")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
