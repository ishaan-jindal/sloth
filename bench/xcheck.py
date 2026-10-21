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
  - the noise filter: the keep-or-drop decision for every path in the corpus
    plus the filter's own edge cases, taken from the real Rust function by
    compiling a four-line probe against the crate. Behaviour, not a list of
    strings, so a rewrite on either side does not break the check.

Not compared: the rendered `system`/`user` halves. They embed the diff, and the
two sides legitimately differ there: the Rust digest asks git for a `--unified=0`
diff, while the harness sends the stored case diff and reconstructs that diff
from a `--unified=3` patch. The noise filter each side applies to the diff body
*is* compared now (see above); the file list and diffstat are unfiltered on both
sides, as `Digest::build` keeps every staged path in `files`.

python3 bench/xcheck.py
"""

import difflib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BENCH = Path(__file__).resolve().parent
ROOT = BENCH.parent

sys.path.insert(0, str(BENCH))

from run import build_values, is_noise_path  # noqa: E402 - needs the path above
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


# The filter's own edges: every rule, both sides of it, and ordinary sources
# that must stay. The corpus is real paths from real commits; these add the
# paths it may not contain, and any duplicate is dropped by corpus_paths().
NOISE_PROBES = [
    "Cargo.lock",
    "crates/sloth/Cargo.lock",
    "package-lock.json",
    "web/package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Gemfile.lock",
    "go.sum",
    "service/go.sum",
    "bun.lockb",
    "BUN.LOCKB",
    "CARGO.LOCK",
    # Non-ASCII lookalikes: Rust folds ASCII only, so these must stay in.
    "Cargo.loc\u212a",
    "go.\u017fum",
    "go.mod",
    "go.sum.bak",
    "node_modules/left-pad/index.js",
    "node_modules/",
    "vendor/github.com/x/y.go",
    "dist/app.js",
    "build/output.js",
    "crates/sloth/target/debug/sloth",
    "assets/app.min.js",
    "assets/app.min.css",
    "assets/app.min.mjs",
    "assets/app.min.cjs",
    "src/__snapshots__/view.snap",
    "tui/__snapshots__/view.snap",
    "src/view.snap",
    "Node_modules/left-pad/index.js",
    "src/build/main.rs",
    "targets/main.rs",
    "src/main.rs",
    "src/main.rs/",
    "docs/release.md",
    "README.md",
    "build.rs",
    "vendor.js",
    "min.js",
    "src/__snapshots__x/view.ts",
]

PROBE_SOURCE = """\
use std::io::{self, BufRead};

fn main() {
    let stdin = io::stdin();
    for line in stdin.lock().lines() {
        let path = line.unwrap_or_default();
        let decision = if sloth::diff::is_noise_path(&path) {
            "drop"
        } else {
            "keep"
        };
        println!("{decision}");
    }
}
"""


def corpus_paths():
    """Every path in bench/cases.jsonl, then the filter's own edge cases."""
    paths = []
    with open(BENCH / "cases.jsonl") as handle:
        for line in handle:
            if not line.strip():
                continue
            case = json.loads(line)
            for file in case.get("files") or []:
                path = str(file.get("path", ""))
                if path and path not in paths:
                    paths.append(path)
    for path in NOISE_PROBES:
        if path not in paths:
            paths.append(path)
    return paths


def rust_filter(binary, paths):
    """The Rust filter's decision for each path, keyed by path. True = drop.

    Compiles a four-line probe against the crate that ships, so the comparison
    is between the two behaviours rather than between two lists of strings.
    Reaches no network and starts no model.
    """
    rlib = binary.parent / "libsloth.rlib"
    if not rlib.exists():
        raise RuntimeError(f"{rlib} is missing; cargo build produced no library")
    if any("\n" in path or "\r" in path for path in paths):
        raise RuntimeError("a path contains a line break; the probe reads one per line")
    with tempfile.TemporaryDirectory(prefix="sloth-xcheck-") as workdir:
        source = Path(workdir) / "probe.rs"
        source.write_text(PROBE_SOURCE)
        probe = Path(workdir) / "probe"
        proc = subprocess.run(
            [
                "rustc",
                "--edition",
                "2021",
                "-L",
                f"dependency={binary.parent / 'deps'}",
                "--extern",
                f"sloth={rlib}",
                str(source),
                "-o",
                str(probe),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "cannot compile the filter probe: "
                + (proc.stderr.strip()[-600:] or "?")
            )
        run = subprocess.run(
            [str(probe)],
            input="\n".join(paths) + "\n",
            capture_output=True,
            text=True,
            timeout=600,
        )
    if run.returncode != 0:
        raise RuntimeError("the filter probe failed: " + run.stderr.strip()[-600:])
    decisions = run.stdout.splitlines()
    if len(decisions) != len(paths):
        raise RuntimeError(
            f"filter probe answered {len(decisions)} of {len(paths)} paths"
        )
    return {
        path: decision == "drop"
        for path, decision in zip(paths, decisions)
        if decision in ("keep", "drop")
    }


def filter_disagreements(paths, rust):
    """Paths where the two filters disagree. `rust` maps path -> drop?."""
    problems = []
    for path in paths:
        if path not in rust:
            problems.append(f"filter: the Rust side gave no decision for {path!r}")
            continue
        py_drop = is_noise_path(path)
        if rust[path] != py_drop:
            problems.append(
                f"filter: {path!r} rust={'drop' if rust[path] else 'keep'} "
                f"python={'drop' if py_drop else 'keep'}"
            )
    return problems


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

    # The filter, behaviourally: same paths in, same keep-or-drop out.
    paths = corpus_paths()
    filter_compared = 0
    try:
        rust = rust_filter(binary, paths)
        found = filter_disagreements(paths, rust)
        filter_compared = len(paths)
        dropped = sum(1 for path in paths if rust.get(path))
        problems.extend(found)
        print(
            f"{'ok  ' if not found else 'FAIL'} filter: {filter_compared} paths, "
            f"{dropped} dropped by both, {len(found)} disagreement(s)"
        )
    except (RuntimeError, OSError) as exc:
        problems.append(f"filter: {exc}")

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
        f"compared: the noise filter on {filter_compared} paths "
        f"({len(NOISE_PROBES)} filter edge cases plus every path "
        "in bench/cases.jsonl, deduplicated), behaviourally"
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
