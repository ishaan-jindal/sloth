#!/usr/bin/env python3
"""Runnable self-check for the benchmark harness. No framework, no network.

python3 bench/selftest.py
"""

import sys

sys.dont_write_bytecode = True

from pathlib import Path

from baseline import baseline_message
from conformance import check_message
from run import (
    MAX_NUM_CTX,
    build_digest,
    build_map_user,
    build_payload,
    choose_level,
    chunk_diff,
    compress_diff,
    context_budget,
    estimate_tokens,
    fallback_line,
    map_phase,
    render_prompt,
    split_diff_blocks,
    split_template,
    strip_system_payload,
)
from schema import build_schema, subject_pattern
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


# --------------------------------------------------------------------------- #
# The diff strategy. The harness must send the prompt the Rust CLI sends, so
# these checks pin the compression, the level boundaries and the chunking.
# --------------------------------------------------------------------------- #
UNIFIED3_SAMPLE = (
    "diff --git a/app.go b/app.go\n"
    "index 1111111..2222222 100644\n"
    "--- a/app.go\n"
    "+++ b/app.go\n"
    "@@ -10,6 +10,7 @@ func (m *Model) Update(msg tea.Msg) {\n"
    " \tctx10\n"
    " \tctx11\n"
    "-\told line\n"
    "+\tnew line\n"
    "+\textra line\n"
    " \tctx13\n"
    " \tctx14\n"
    " \tctx15\n"
)

UNIFIED0_SAMPLE = (
    "diff --git a/app.go b/app.go\n"
    "index 1111111..2222222 100644\n"
    "--- a/app.go\n"
    "+++ b/app.go\n"
    "@@ -12 +12,2 @@ func (m *Model) Update(msg tea.Msg) {\n"
    "-\told line\n"
    "+\tnew line\n"
    "+\textra line\n"
)


def test_compression_drops_context_and_keeps_hunk_headers():
    compressed = compress_diff(UNIFIED3_SAMPLE)
    assert compressed == UNIFIED0_SAMPLE, compressed
    assert len(compressed) < len(UNIFIED3_SAMPLE), (
        len(compressed),
        len(UNIFIED3_SAMPLE),
    )
    assert UNIFIED0_SAMPLE.splitlines()[4] in compressed
    for line in compressed.splitlines():
        assert not line.startswith(" "), line
        assert line.strip(), line


def test_compression_splits_runs_and_renumbers_hunks():
    u3 = (
        "diff --git a/f.txt b/f.txt\n"
        "--- a/f.txt\n"
        "+++ b/f.txt\n"
        "@@ -1,7 +1,7 @@\n"
        " ctx1\n"
        "-old2\n"
        "+new2\n"
        " ctx3\n"
        " ctx4\n"
        "-old5\n"
        "+new5\n"
        " ctx6\n"
        " ctx7\n"
    )
    expected = (
        "diff --git a/f.txt b/f.txt\n"
        "--- a/f.txt\n"
        "+++ b/f.txt\n"
        "@@ -2 +2 @@\n"
        "-old2\n"
        "+new2\n"
        "@@ -5 +5 @@\n"
        "-old5\n"
        "+new5\n"
    )
    assert compress_diff(u3) == expected, compress_diff(u3)


def test_level_a_digest_fits_and_has_no_context_lines():
    case = {
        "files": [{"path": "app.go", "add": "2", "del": "1"}],
        "diff": UNIFIED3_SAMPLE,
    }
    digest = build_digest(case)
    assert digest["diff"] == UNIFIED0_SAMPLE, digest["diff"]
    assert digest["files"] == "app.go", digest["files"]
    assert digest["diffstat"] == "2/1\tapp.go", digest["diffstat"]

    system = "convention rules\n" * 20
    user = (
        f"Files:\n{digest['files']}\nDiffstat:\n{digest['diffstat']}\n{digest['diff']}"
    )
    level, num_ctx = choose_level(system, user, 8192)
    assert (level, num_ctx) == ("A", 8192), (level, num_ctx)
    assert estimate_tokens(user) <= context_budget(num_ctx, system)


def test_budget_arithmetic():
    assert estimate_tokens("x" * 350) == 100
    assert estimate_tokens("x" * 351) == 101
    assert context_budget(8192, "x" * 350) == 8192 - 100 - 800
    assert context_budget(32768, "") == 32768 - 800


def test_level_b_raises_context_but_never_past_the_cap():
    system = "s" * 700  # 200 tokens
    payload = "d" * 40_000  # 11,429 tokens: too big for 8192, fine for 32768
    level, num_ctx = choose_level(system, payload, 8192)
    assert level == "B", level
    assert 8192 < num_ctx <= MAX_NUM_CTX, num_ctx
    assert estimate_tokens(payload) <= context_budget(num_ctx, system)
    assert estimate_tokens(payload) > context_budget(8192, system)

    # An explicit default above the cap is not shrunk.
    level, num_ctx = choose_level(system, payload, MAX_NUM_CTX * 2)
    assert (level, num_ctx) == ("A", MAX_NUM_CTX * 2), (level, num_ctx)


def _large_case(blocks=12, lines_per_block=8):
    """Synthetic case whose file blocks are all the same size."""
    files = []
    parts = []
    for i in range(blocks):
        path = f"src/file{i:03d}.go"
        files.append({"path": path, "add": str(lines_per_block), "del": "0"})
        parts.append(
            f"diff --git a/{path} b/{path}\n"
            f"--- a/{path}\n"
            f"+++ b/{path}\n"
            f"@@ -1,0 +2,{lines_per_block} @@ func f{i:03d}() {{"
        )
        parts.append(
            "\n".join(f"+line {j:03d} of file {i:03d}" for j in range(lines_per_block))
        )
    return {"files": files, "diff": "\n".join(parts) + "\n"}


def test_level_c_chunks_each_fit_and_the_count_is_right():
    case = _large_case()
    digest = build_digest(case)
    assert len(split_diff_blocks(digest["diff"])) == 12

    # Tune the budget to three file blocks per chunk (plus one token of slack
    # for the longer two-digit paths): 12 blocks then need four chunks, and no
    # arbitrary constant decides that.
    blocks = split_diff_blocks(digest["diff"])
    sample = {
        "paths": [f"src/file{i:03d}.go" for i in range(3)],
        "headers": [f"@@ -1,0 +2,8 @@ func f{i:03d}() {{" for i in range(3)],
        "diff": "\n".join("\n".join(block) for block in blocks[:3]),
    }
    budget = estimate_tokens(build_map_user(sample)) + 1
    chunks = chunk_diff(digest["diff"], budget)
    assert len(chunks) == 4, (len(chunks), [len(c["diff"]) for c in chunks])
    for index, chunk in enumerate(chunks, 1):
        prompt = build_map_user(chunk)
        assert estimate_tokens(prompt) <= budget, (index, estimate_tokens(prompt))
    covered = [path for chunk in chunks for path in chunk["paths"]]
    assert covered == [f"src/file{i:03d}.go" for i in range(12)], covered


def test_level_c_is_chosen_when_even_the_cap_cannot_hold_the_digest():
    case = _large_case(blocks=300, lines_per_block=30)
    digest = build_digest(case)
    system = "s" * 1000
    user = digest["files"] + digest["diffstat"] + digest["diff"]
    level, num_ctx = choose_level(system, user, 8192)
    assert level == "C", level
    assert num_ctx == MAX_NUM_CTX, num_ctx

    budget = context_budget(num_ctx, system)
    chunks = chunk_diff(digest["diff"], budget)
    assert len(chunks) >= 2, len(chunks)
    for index, chunk in enumerate(chunks, 1):
        prompt = build_map_user(chunk)
        assert estimate_tokens(prompt) <= budget, (index, estimate_tokens(prompt))
    covered = [path for chunk in chunks for path in chunk["paths"]]
    assert covered == [f"src/file{i:03d}.go" for i in range(300)], len(covered)


def test_map_failure_yields_a_deterministic_line_and_level_c_terminates():
    chunks = [
        {"paths": ["src/a.go"], "headers": ["@@ -1 +1 @@ func a() {"], "diff": "+a\n"},
        {"paths": ["src/b.go"], "headers": ["@@ -5 +5 @@ func b() {"], "diff": "+b\n"},
    ]
    calls = []

    def ask(index, chunk):
        calls.append(index)
        if index == 1:
            raise RuntimeError("the model server went away")
        return "  \n"  # an empty answer is a failure too

    lines, failures = map_phase(chunks, ask)
    assert calls == [1, 2], calls
    assert failures == 2, failures
    assert lines == [fallback_line(chunk) for chunk in chunks], lines
    assert "src/a.go" in lines[0], lines[0]
    assert "@@ -1 +1 @@ func a() {" in lines[0], lines[0]

    # Deterministic: the same failed chunk always produces the same line.
    assert map_phase(chunks, ask) == (lines, failures)

    # The reduce phase has the file list and one line per chunk to work from.
    summary = "\n".join(lines)
    assert summary.count("\n") == 1, summary
    assert "src/a.go" in summary and "src/b.go" in summary, summary


def test_map_prompt_carries_paths_and_headers():
    chunk = {
        "paths": ["src/a.go"],
        "headers": ["@@ -1 +1 @@ func a() {"],
        "diff": "+a\n",
    }
    prompt = build_map_user(chunk)
    assert "src/a.go" in prompt, prompt
    assert "@@ -1 +1 @@ func a() {" in prompt, prompt
    assert "+a" in prompt, prompt
    assert prompt.endswith("--- END DIFF ---"), prompt


def test_generate_commit_level_c_terminates_with_a_subject():
    """End to end for level C with every map call failing: no network involved."""
    import run as runmod

    class Args:
        base_url = "http://127.0.0.1:1"
        timeout = 1.0

    chunks = [
        {"paths": ["src/a.go"], "headers": ["@@ -1 +1 @@ func a() {"], "diff": "+a\n"},
        {"paths": ["src/b.go"], "headers": ["@@ -5 +5 @@ func b() {"], "diff": "+b\n"},
    ]
    item = {
        "system": "rules",
        "user": "",
        "level": "C",
        "num_ctx": 32768,
        "chunk_plans": chunks,
        "files_text": "src/a.go\nsrc/b.go",
        "diffstat": "1/0\tsrc/a.go\n1/0\tsrc/b.go",
        "style": {"shape": "freeform"},
        "case": {
            "files": [
                {"path": "src/a.go", "add": "1", "del": "0"},
                {"path": "src/b.go", "add": "1", "del": "0"},
            ]
        },
    }
    user_template = "Files:\n{{FILES}}\n\nDiff:\n{{DIFF}}\n"
    schema = {"type": "object"}
    calls = []

    def fake_call(
        base_url, model, system, user, schema_, num_ctx, timeout, num_predict=768
    ):
        calls.append(
            {
                "system": system,
                "user": user,
                "schema": schema_,
                "num_predict": num_predict,
            }
        )
        if num_predict == 40:
            return "", 0.01, "the model server went away"
        return '{"subject": "feat(core): add a thing", "body": ""}', 0.5, None

    real = runmod.call_ollama
    runmod.call_ollama = fake_call
    try:
        content, latency, error, prompt_chars = runmod.generate_commit(
            item, "model", Args(), schema, user_template
        )
    finally:
        runmod.call_ollama = real

    assert error is None, error
    assert latency == 0.52, latency
    expected_chars = (
        len(runmod.MAP_SYSTEM)
        + len(calls[0]["user"])
        + len(runmod.MAP_SYSTEM)
        + len(calls[1]["user"])
        + len(item["system"])
        + len(calls[2]["user"])
    )
    assert prompt_chars == expected_chars, (prompt_chars, expected_chars)
    assert len(calls) == 3, calls  # two map calls, one reduce call
    assert all(c["system"] == runmod.MAP_SYSTEM for c in calls[:2]), calls
    assert all(c["schema"] is None and c["num_predict"] == 40 for c in calls[:2]), calls
    assert calls[2]["system"] == item["system"], calls[2]
    assert calls[2]["schema"] is schema and calls[2]["num_predict"] == 768, calls[2]
    # The reduce prompt is the normal template, with the map lines as the diff.
    assert "Files:\nsrc/a.go\nsrc/b.go" in calls[2]["user"], calls[2]["user"]
    assert "changed src/a.go" in calls[2]["user"], calls[2]["user"]
    assert "changed src/b.go" in calls[2]["user"], calls[2]["user"]
    subject, body, parse_error = runmod.parse_model_json(content)
    assert (subject, body, parse_error) == ("feat(core): add a thing", "", None)
    assert "feat(core)" in subject, subject


def test_chunking_slices_one_giant_hunk():
    lines = "".join(f"+line {i:04d} of the giant hunk\n" for i in range(3000))
    diff = (
        "diff --git a/big.go b/big.go\n"
        "--- a/big.go\n"
        "+++ b/big.go\n"
        f"@@ -1,0 +2,3000 @@ func Giant() {{\n{lines}"
    )
    case = {
        "files": [{"path": "big.go", "add": "3000", "del": "0"}],
        "diff": diff,
    }
    digest = build_digest(case)
    budget = 5000  # tokens for one map call: forces the single hunk to be sliced
    chunks = chunk_diff(digest["diff"], budget)
    assert len(chunks) > 1, len(chunks)
    for index, chunk in enumerate(chunks, 1):
        prompt = build_map_user(chunk)
        assert estimate_tokens(prompt) <= budget, (index, estimate_tokens(prompt))
        assert chunk["paths"] == ["big.go"], chunk["paths"]
        assert chunk["headers"], chunk


def test_prepare_cases_selects_a_level_and_sends_the_digest():
    """Every level is wired into the real prompt template, with no network."""
    import run as runmod

    style = {
        "shape": "conventional",
        "type_vocab": {"feat": 3, "fix": 2},
        "scope_vocab": {"ui": 2},
        "scope_rate": 0.9,
        "subject_len": {"median": 40, "p90": 60},
        "body_usage": 0.4,
        "body_words": {"median": 40, "p90": 80},
        "exemplars": ["feat(ui): add x"],
        "n_subjects": 10,
        "confidence": "high",
        "match_rates": {"conventional": 1.0},
    }
    cases = [
        {
            "id": "a",
            "repo": "fake",
            "files": [{"path": "app.go", "add": "2", "del": "1"}],
            "diff": UNIFIED3_SAMPLE,
        },
        {
            **_large_case(blocks=1, lines_per_block=1500),
            "id": "b",
            "repo": "fake",
        },
        {
            **_large_case(blocks=1, lines_per_block=5000),
            "id": "c",
            "repo": "fake",
        },
    ]
    template = (Path(__file__).resolve().parent / "prompts" / "commit.md").read_text()
    system_template, user_template = split_template(template)
    args = type("Args", (), {"repo_root": "~/Dev", "num_ctx": 8192})()

    real = runmod.load_style
    runmod.load_style = lambda repo, root, cache: style
    try:
        prepared, leftovers = runmod.prepare_cases(
            cases, args, strip_system_payload(system_template), user_template
        )
    finally:
        runmod.load_style = real

    assert [item["level"] for item in prepared] == ["A", "B", "C"], [
        item["level"] for item in prepared
    ]
    assert leftovers == 0, leftovers
    for item in prepared:
        assert not item["error"], item["error"]
        assert item["num_ctx"] <= MAX_NUM_CTX, item["num_ctx"]

    a, b, c = prepared
    assert a["user"].count("@@ -12 +12,2 @@") == 1, a["user"]
    assert "\n \tctx10" not in a["user"], "a context line survived into the prompt"
    assert a["digest_chars"] < a["diff_chars"], (a["digest_chars"], a["diff_chars"])
    assert a["prompt_chars"] == len(a["system"]) + len(a["user"]), a["prompt_chars"]

    assert b["num_ctx"] > 8192, b["num_ctx"]
    assert b["level"] == "B" and b["chunks"] == 0, (b["level"], b["chunks"])

    assert c["num_ctx"] == MAX_NUM_CTX, c["num_ctx"]
    assert c["chunks"] > 1, c["chunks"]
    assert len(c["chunk_plans"]) == c["chunks"], c["chunks"]
    for index, chunk in enumerate(c["chunk_plans"], 1):
        prompt = build_map_user(chunk)
        budget = min(
            context_budget(c["num_ctx"], c["system"]),
            context_budget(c["num_ctx"], runmod.MAP_SYSTEM),
        )
        assert estimate_tokens(prompt) <= budget, (index, estimate_tokens(prompt))


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
        test_compression_drops_context_and_keeps_hunk_headers,
        test_compression_splits_runs_and_renumbers_hunks,
        test_level_a_digest_fits_and_has_no_context_lines,
        test_budget_arithmetic,
        test_level_b_raises_context_but_never_past_the_cap,
        test_level_c_chunks_each_fit_and_the_count_is_right,
        test_level_c_is_chosen_when_even_the_cap_cannot_hold_the_digest,
        test_map_failure_yields_a_deterministic_line_and_level_c_terminates,
        test_map_prompt_carries_paths_and_headers,
        test_generate_commit_level_c_terminates_with_a_subject,
        test_chunking_slices_one_giant_hunk,
        test_prepare_cases_selects_a_level_and_sends_the_digest,
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
