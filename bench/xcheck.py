#!/usr/bin/env python3
"""Compare the Rust CLI with the Python benchmark harness, field by field.

Two independent implementations of the same rules. If they disagree, the
benchmark is not measuring the tool.

The Rust side is `sloth prompt --json`, which renders the prompt, the schema and
the mined style without contacting a model. The Python side is the harness's own
code: `style.mine_style`, `run.build_values` and `schema.build_schema`.

Compared:
  - the mined style fields (the miner)
  - the four generated prompt texts
  - the schema fields the model server's grammar enforces
  - the prompt template file, which must be byte-identical

Not compared: the rendered `system`/`user` halves. They embed the diff, and the
two sides legitimately differ there: the Rust digest asks git for a `--unified=0`
diff and filters lockfiles and generated files, while the harness sends the
stored case diff and reconstructs that diff from a `--unified=3` patch. That
difference is documented in DIFF_STRATEGY.md; the style-derived texts above do
not depend on the diff, so they are compared directly.

python3 bench/xcheck.py
"""

import difflib
import json
import os
import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent

sys.path.insert(0, str(BENCH))

from run import build_values  # noqa: E402 - needs the path above
from schema import build_schema  # noqa: E402
from style import mine_style  # noqa: E402

REPOS = {
    "matcha": "~/Dev/matcha",
    "runnix": "~/Dev/runnix",
    "vaults-hub": "~/Dev/vaults-hub",
    "termchat": "~/Dev/Termchat/termchat",
    "portfolio-website": "~/Dev/portfolio-website",
}

PROMPT_VARIABLES = ["CONVENTION", "EXEMPLARS", "LENGTH_RULE", "BODY_RULE"]
SCHEMA_FIELDS = [
    "properties.subject.pattern",
    "properties.subject.maxLength",
    "properties.body.maxLength",
    "required",
    "properties.body.description",
]
# (what to show, path in the Rust style JSON, path in the Python style dict)
MINER_FIELDS = [
    ("shape", "shape", "shape"),
    ("confidence", "confidence", "confidence"),
    ("pr_suffix", "pr_suffix", "pr_suffix"),
    ("body_usage", "body_usage", "body_usage"),
    ("scope_rate", "scope_rate", "scope_rate"),
    ("subject_len.median", "subject_len.median", "subject_len.median"),
    ("subject_len.p90", "subject_len.p90", "subject_len.p90"),
    ("body_median_words", "body_median_words", "body_words.median"),
    ("type_vocab", "type_vocab", "type_vocab"),
    ("scope_vocab", "scope_vocab", "scope_vocab"),
    ("body_headings", "body_headings", "body_sections"),
    ("exemplars", "exemplars", "exemplars"),
]
# The miner omits a vocabulary it never saw; the Rust one serialises it empty.
EMPTY_COLLECTION = {
    "type_vocab": {},
    "scope_vocab": {},
    "body_headings": [],
    "exemplars": [],
}
TOLERANCE = 0.006


def build_binary():
    """Build the CLI the check is about, so it cannot test a stale binary."""
    binary = ROOT / "target" / "release" / "sloth"
    proc = subprocess.run(
        ["cargo", "build", "--release", "--quiet"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-800:] or "cargo build failed")
    return binary


def rust_prompt(binary, repo):
    """`sloth prompt --json` renders everything without a model. No network."""
    env = {key: value for key, value in os.environ.items() if key != "SLOTH_STYLE"}
    proc = subprocess.run(
        [str(binary), "prompt", "--json"],
        cwd=str(Path(repo).expanduser()),
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "sloth prompt failed")
    return json.loads(proc.stdout)


def dig(value, dotted):
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def same_scalar(rust, python, tol=TOLERANCE):
    if is_number(rust) and is_number(python):
        return abs(float(rust) - float(python)) <= tol
    return rust == python


def text_problem(rust, python):
    """A readable unified diff of two generated texts."""
    diff = list(
        difflib.unified_diff(
            (python or "").splitlines(keepends=True),
            (rust or "").splitlines(keepends=True),
            fromfile="python",
            tofile="rust",
            n=1,
        )
    )
    if len(diff) > 24:
        diff = diff[:24] + ["... (truncated)\n"]
    return "".join(diff).rstrip()


def template_problem():
    """The two copies of the prompt template must be one template, not two."""
    bench = (BENCH / "prompts" / "commit.md").read_bytes()
    rust = (ROOT / "crates" / "sloth" / "prompts" / "commit.md").read_bytes()
    if bench == rust:
        return None
    bench_lines = bench.splitlines()
    rust_lines = rust.splitlines()
    for index, (a, b) in enumerate(zip(bench_lines, rust_lines), 1):
        if a != b:
            return (
                "template: bench/prompts/commit.md and "
                f"crates/sloth/prompts/commit.md first differ at line {index}: "
                f"bench={a!r} rust={b!r}"
            )
    return (
        f"template: same first {min(len(bench_lines), len(rust_lines))} lines but "
        f"bench has {len(bench_lines)} lines and rust has {len(rust_lines)}"
    )


def check_repo(binary, name, path):
    """Compare one repository. Returns (problems, fields compared)."""
    problems = []
    python_style = mine_style(path, 300)
    python_values = build_values(
        python_style, {"files": "", "diffstat": "", "diff": ""}
    )
    python_schema = build_schema(python_style)

    rust = rust_prompt(binary, path)
    rust_style = rust.get("style") or {}
    rust_variables = rust.get("variables") or {}
    rust_schema = rust.get("schema") or {}

    for label, rust_path, python_path in MINER_FIELDS:
        rust_value = dig(rust_style, rust_path)
        python_value = dig(python_style, python_path)
        if label in EMPTY_COLLECTION:
            rust_value = rust_value or EMPTY_COLLECTION[label]
            python_value = python_value or EMPTY_COLLECTION[label]
        if not same_scalar(rust_value, python_value):
            problems.append(
                f"{name}.miner.{label}: rust={rust_value!r} python={python_value!r}"
            )

    for variable in PROMPT_VARIABLES:
        rust_text = rust_variables.get(variable)
        python_text = python_values.get(variable)
        if rust_text != python_text:
            problems.append(
                f"{name}.prompt.{variable} differs:\n"
                f"{text_problem(rust_text, python_text)}"
            )

    for field in SCHEMA_FIELDS:
        rust_value = dig(rust_schema, field)
        python_value = dig(python_schema, field)
        if rust_value != python_value:
            problems.append(
                f"{name}.schema.{field}: rust={rust_value!r} python={python_value!r}"
            )

    return problems, len(MINER_FIELDS) + len(PROMPT_VARIABLES) + len(SCHEMA_FIELDS)


def main():
    try:
        binary = build_binary()
    except (RuntimeError, OSError) as exc:
        print(f"xcheck: cannot build the CLI: {exc}", file=sys.stderr)
        return 1

    problems = []
    template = template_problem()
    if template:
        problems.append(template)

    repositories = 0
    checked = 0
    for name, path in REPOS.items():
        repo = Path(path).expanduser()
        if not repo.is_dir():
            problems.append(f"{name}: {repo} is not a repository")
            continue
        try:
            found, fields = check_repo(binary, name, repo)
        except Exception as exc:  # noqa: BLE001 - a check reports, it does not traceback
            problems.append(f"{name}: {exc}")
            continue
        repositories += 1
        checked += fields
        problems.extend(found)
        print(f"{'ok  ' if not found else 'FAIL'} {name}")

    print()
    print(f"repositories: {repositories}/{len(REPOS)}")
    print(
        f"compared: {checked} fields ({len(MINER_FIELDS)} style, "
        f"{len(PROMPT_VARIABLES)} prompt texts, {len(SCHEMA_FIELDS)} schema) "
        "plus the prompt template"
    )
    print(
        "not compared: the rendered system/user halves (they embed the diff; the "
        "Rust digest and the stored case diff differ by design)"
    )

    if problems:
        print(f"\n{len(problems)} disagreement(s):")
        for problem in problems:
            print("  " + problem.replace("\n", "\n  "))
        print("\nverdict: rust and python DISAGREE")
        return 1

    print("\nverdict: rust and python agree on every compared field")
    return 0


if __name__ == "__main__":
    sys.exit(main())
