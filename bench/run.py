#!/usr/bin/env python3
"""Sloth benchmark harness: one prompt, one schema, one model at a time.

Sequential by design. No concurrency, no retries, no magic: clean latency
numbers and a row per attempt. Importable without performing any request.
"""

import sys

sys.dont_write_bytecode = True

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

from baseline import baseline_message
from conformance import check_message
from schema import build_schema
from style import mine_style

BENCH_DIR = Path(__file__).resolve().parent
DEFAULT_CASES = BENCH_DIR / "cases.jsonl"
DEFAULT_PROMPT = BENCH_DIR / "prompts" / "commit.md"
DEFAULT_SCHEMA = BENCH_DIR / "schema.json"
DEFAULT_OUT = BENCH_DIR / "results.jsonl"
DEFAULT_BASE_URL = "http://127.0.0.1:11434"

TOKEN_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
# A diff can legitimately contain `${{ secrets.NAME }}` - that is a GitHub Actions
# expression in the code, not an unsubstituted template variable. Only flag a
# placeholder that is not preceded by a dollar sign.
UNSUBSTITUTED_RE = re.compile(r"(?<!\$)\{\{[A-Z0-9_]+\}\}")
USER_MARKER_RE = re.compile(r"(?m)^--- USER ---[ \t]*$")
# The user half already carries files/diffstat/diff; drop the duplicated block
# from the system half at render time (the template file is owned elsewhere).
SYSTEM_PAYLOAD_RE = re.compile(
    r"(?ms)^## Files changed[ \t]*\n.*?^--- END DIFF ---[ \t]*\n(?:[ \t]*\n)*"
)
CHARS_PER_TOKEN = 3.5
LENGTH_CEILING = 72
STATUSES = ("ok", "over_budget", "bad_output", "error", "skipped")


# --------------------------------------------------------------------------- #
# Budget and templating (pure, importable, no network)
# --------------------------------------------------------------------------- #
def is_over_budget(case, prompt_overhead, max_diff_bytes, num_ctx):
    """True when a case must be discarded before a model call.

    ``prompt_overhead`` is the prompt size outside the diff, in characters
    (files list + fixed boilerplate). The estimate deliberately over-counts:
    it is a gate, not a measurement.
    """
    case = case or {}
    diff_chars = len(case.get("diff") or "")
    estimated_chars = diff_chars + int(prompt_overhead)
    if estimated_chars > int(max_diff_bytes):
        return True
    return (estimated_chars / CHARS_PER_TOKEN) > float(num_ctx)


def render_prompt(template, values, warnings=None):
    """Replace ``{{NAME}}`` tokens. Unknown tokens stay literal and are reported."""
    missing = warnings if warnings is not None else []

    def replace(match):
        name = match.group(1)
        if name in values:
            return str(values[name])
        if name not in missing:
            missing.append(name)
        return match.group(0)

    return TOKEN_RE.sub(replace, template)


def split_template(template):
    """Split on a literal ``--- USER ---`` line; no marker means system-only."""
    parts = USER_MARKER_RE.split(template, maxsplit=1)
    if len(parts) == 1:
        return template, ""
    return parts[0].rstrip(), parts[1].strip()


def strip_system_payload(template):
    """Drop the files/diffstat/diff block from the system half.

    The prompt template carries the change twice; the user half is the one that
    keeps it. Removing it halves prompt size and prefill time on CPU.
    """
    return SYSTEM_PAYLOAD_RE.sub("", template)


def _top_vocab(vocab):
    return sorted((vocab or {}).items(), key=lambda kv: (-kv[1], kv[0]))


def _render_vocab(vocab):
    return ", ".join(f"{name} ({count})" for name, count in _top_vocab(vocab))


def _type_names(style):
    """The mined type names, capped at twelve, with a standard fallback."""
    mined = [name for name, _ in _top_vocab(style.get("type_vocab"))][:12]
    return mined or ["feat", "fix", "chore", "docs", "ci", "test"]


def _round_half_up(value):
    """Rust's `f64::round()`: halfway cases go away from zero."""
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


def build_convention(style):
    """The convention paragraph, worded exactly as the Rust CLI words it.

    The benchmark must measure the prompt the tool actually ships. `xcheck.py`
    compares this text against the Rust implementation for every benchmark
    repository.
    """
    lines = []
    shape = style.get("shape", "freeform")
    if shape == "conventional":
        lines.append(
            "The repository uses Conventional Commits: `<type>(<scope>): <description>`."
        )
        if not style.get("type_vocab"):
            lines.append(
                "No type vocabulary was mined; use a standard type such as feat, fix or chore."
            )
        else:
            lines.append(
                f"Types in use, by frequency: {_render_vocab(style['type_vocab'])}."
            )
            type_list = ", ".join(f"`{name}`" for name in _type_names(style))
            lines.append(
                "The subject MUST begin with one of these type prefixes, followed by a colon: "
                f"{type_list}. A subject without that prefix is invalid."
            )
        if not style.get("scope_vocab"):
            lines.append(
                "The repository does not use scopes; write `type: description`."
            )
        else:
            scope_rate = float(style.get("scope_rate") or 0.0)
            scope_pct = _round_half_up(scope_rate * 100)
            scope_rule = (
                "a scope is required"
                if scope_rate >= 0.90
                else "a scope is optional, so use one only when it fits"
            )
            lines.append(
                f"Scopes in use, by frequency: {_render_vocab(style['scope_vocab'])}. "
                f"{scope_pct}% of conventional commits are scoped; {scope_rule}. "
                "Do not invent a new scope."
            )
        if "!" in (style.get("breaking_markers") or []):
            lines.append(
                "Breaking changes use `!` before the colon, e.g. `feat(api)!: drop v1`."
            )
    elif shape == "gitmoji":
        lines.append(
            "The repository prefixes every subject with a gitmoji shortcode, "
            "e.g. `:sparkles: add export`."
        )
        if style.get("emoji_vocab"):
            lines.append(
                f"Emojis in use, by frequency: {_render_vocab(style['emoji_vocab'])}."
            )
    elif shape == "ticket":
        lines.append(
            "The repository prefixes every subject with an uppercase ticket key, "
            "e.g. `PROJ-123 add export`."
        )
    else:
        lines.append(
            "The repository does not use a structured prefix. Write a plain, "
            "sentence-style subject with no `type:` prefix and no emoji."
        )
    if style.get("pr_suffix"):
        lines.append(
            "The history's subjects end with `(#1234)` pull-request tokens. Those numbers were "
            "stripped from the examples and you cannot know them; never add one."
        )
    if style.get("forced"):
        lines.append("This shape was selected explicitly by SLOTH_STYLE.")
    if not style.get("n_subjects"):
        lines.append("There is no commit history to mine yet.")
    else:
        rate = (style.get("match_rates") or {}).get(shape, 0.0)
        pct = _round_half_up(rate * 100)
        lines.append(
            f"Mined confidence: {style.get('confidence', 'low')} "
            f"({pct}% of {style['n_subjects']} non-merge subjects match this shape)."
        )
    return "\n".join(lines)


def build_exemplars(style):
    exemplars = style.get("exemplars") or []
    if not exemplars:
        return "(no examples of this shape in history)"
    return "\n".join(f"- {subject}" for subject in exemplars)


def build_length_rule(style):
    if not style.get("n_subjects"):
        return "Keep the subject to 72 characters or fewer."
    lengths = style.get("subject_len") or {}
    p90 = int(lengths.get("p90") or 0)
    median = int(lengths.get("median") or 0)
    limit = max(p90, LENGTH_CEILING)
    return (
        f"Keep the subject to {limit} characters or fewer "
        f"(repository median {median}, p90 {p90})."
    )


def build_body_rule(style):
    usage = float(style.get("body_usage") or 0.0)
    pct = _round_half_up(usage * 100)
    words = int((style.get("body_words") or {}).get("median") or 0)
    length_note = f" Its bodies are around {words} words long." if words > 0 else ""
    if usage >= 0.35:
        headings = list(style.get("body_sections") or [])
        if not headings:
            return (
                f"This repository writes prose commit bodies ({pct}% of commits): include a short "
                f"body of plain prose (2-4 lines) only when the change needs explaining."
                f"{length_note}"
            )
        structure = ", then ".join(f"`## {heading}`" for heading in headings)
        return (
            f"This repository writes prose commit bodies ({pct}% of commits) using this "
            f"heading structure: {structure}. Follow that structure exactly, with a short "
            f"paragraph under each heading.{length_note}"
        )
    return (
        f"This repository rarely writes prose commit bodies ({pct}% of commits): leave `body` "
        "as an empty string."
    )


def build_values(style, case):
    case = case or {}
    files = case.get("files") or []
    paths = [str(f.get("path", "")) for f in files]
    files_text = "\n".join(paths)
    diffstat = "\n".join(
        f"{f.get('add', '0')}/{f.get('del', '0')}\t{str(f.get('path', ''))}"
        for f in files
    )
    return {
        "CONVENTION": build_convention(style),
        "EXEMPLARS": build_exemplars(style),
        "LENGTH_RULE": build_length_rule(style),
        "BODY_RULE": build_body_rule(style),
        "FILES": files_text,
        "DIFFSTAT": diffstat,
        "DIFF": case.get("diff") or "",
    }


# --------------------------------------------------------------------------- #
# Repos and styles
# --------------------------------------------------------------------------- #
def resolve_repo(name, repo_root):
    """Locate a repo by name, searching one level of subdirectories."""
    root = Path(repo_root).expanduser()
    direct = root / name
    if (direct / ".git").is_dir():
        return direct
    if direct.is_dir():
        return direct
    try:
        children = sorted(root.iterdir())
    except OSError:
        return None
    for child in children:
        if child.is_dir() and (child / name / ".git").is_dir():
            return child / name
    return None


def load_style(repo_name, repo_root, cache):
    if repo_name in cache:
        cached = cache[repo_name]
        if isinstance(cached, Exception):
            raise cached
        return cached
    path = resolve_repo(repo_name, repo_root)
    if path is None:
        error = RuntimeError(f"repo not found: {repo_name} (searched {repo_root})")
        cache[repo_name] = error
        raise error
    try:
        style = mine_style(path)
    except Exception as exc:  # noqa: BLE001 - surfaced as a row error
        error = RuntimeError(f"style mining failed for {repo_name}: {exc}")
        cache[repo_name] = error
        raise error
    cache[repo_name] = style
    return style


# --------------------------------------------------------------------------- #
# Ollama
# --------------------------------------------------------------------------- #
def build_settings(num_ctx):
    """The fixed per-run settings, recorded in every model row for reproducibility."""
    return {
        "temperature": 0.2,
        "top_p": 0.9,
        "num_ctx": int(num_ctx),
        "seed": 42,
        "num_predict": 768,
        "keep_alive": -1,
        "think": False,
    }


def build_payload(model, system, user, schema, num_ctx):
    settings = build_settings(num_ctx)
    return {
        "model": model,
        "stream": False,
        "format": schema,
        "keep_alive": settings["keep_alive"],
        "think": settings["think"],
        "options": {
            "temperature": settings["temperature"],
            "top_p": settings["top_p"],
            "num_ctx": settings["num_ctx"],
            "seed": settings["seed"],
            "num_predict": settings["num_predict"],
        },
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


def call_ollama(base_url, model, system, user, schema, num_ctx, timeout):
    """POST /api/chat. Returns (content_or_raw, latency_s, error_or_None)."""
    payload = build_payload(model, system, user, schema, num_ctx)
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return None, time.monotonic() - started, f"HTTP {exc.code}: {detail}"
    except urllib.error.URLError as exc:
        return (
            None,
            time.monotonic() - started,
            f"connection failed ({exc.reason}); the model server may not be "
            f"running at {base_url}",
        )
    except (TimeoutError, OSError) as exc:
        return None, time.monotonic() - started, f"request failed: {exc}"

    latency = time.monotonic() - started
    try:
        envelope = json.loads(raw)
    except json.JSONDecodeError as exc:
        return raw[:2000], latency, f"Ollama response is not JSON: {exc}"
    content = (envelope.get("message") or {}).get("content")
    if not isinstance(content, str):
        return raw[:2000], latency, "Ollama response has no message.content string"
    return content, latency, None


def parse_model_json(content):
    """Return (subject, body, error)."""
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, TypeError) as exc:
        return None, None, f"model output is not JSON: {exc}"
    if not isinstance(obj, dict):
        return None, None, "model output JSON is not an object"
    subject = obj.get("subject")
    if not isinstance(subject, str):
        return None, None, "model output has no string 'subject'"
    body = obj.get("body", "")
    if body is None:
        body = ""
    if not isinstance(body, str):
        body = str(body)
    return subject.strip(), body.strip(), None


def assemble_message(subject, body):
    return subject + ("\n\n" + body if body else "")


# --------------------------------------------------------------------------- #
# Rows
# --------------------------------------------------------------------------- #
def make_row(case, model, settings=None):
    return {
        "case_id": case.get("id"),
        "repo": case.get("repo"),
        "model": model,
        "status": "error",
        "latency_s": 0.0,
        "subject": "",
        "body": "",
        "message": "",
        "conformance": None,
        "error": None,
        "raw": "",
        "prompt_chars": 0,
        "post_cutoff_safe": bool(case.get("post_cutoff_safe")),
        "human_subject": case.get("subject", ""),
        "settings": settings,
    }


def prepare_cases(cases, args, system_template, user_template):
    """Mine styles, render prompts and apply the budget gate once per case."""
    prepared = []
    cache = {}
    unknown_tokens = []
    literal_leftovers = 0

    for case in cases:
        item = {
            "case": case,
            "over_budget": False,
            "error": None,
            "style": None,
            "system": "",
            "user": "",
            "prompt_chars": 0,
        }
        files_block = "\n".join(str(f.get("path", "")) for f in case.get("files") or [])
        overhead = len(files_block) + 2000
        estimated_chars = len(case.get("diff") or "") + overhead

        try:
            item["style"] = load_style(case.get("repo", ""), args.repo_root, cache)
        except Exception as exc:  # noqa: BLE001 - one bad repo must not kill the run
            item["error"] = str(exc)

        if is_over_budget(case, overhead, args.max_diff_bytes, args.num_ctx):
            item["over_budget"] = True
            item["prompt_chars"] = estimated_chars
            prepared.append(item)
            continue

        if item["error"]:
            prepared.append(item)
            continue

        values = build_values(item["style"], case)
        warnings = []
        system = render_prompt(system_template, values, warnings)
        user = render_prompt(user_template, values, warnings)
        for name in warnings:
            if name not in unknown_tokens:
                unknown_tokens.append(name)
        if UNSUBSTITUTED_RE.search(system + user):
            literal_leftovers += 1
            print(
                f"warning: prompt for {case.get('id')} still contains an unsubstituted "
                "template variable",
                file=sys.stderr,
            )
        item["system"] = system
        item["user"] = user
        item["prompt_chars"] = len(system) + len(user)
        prepared.append(item)

    if unknown_tokens:
        print(
            "warning: template variables with no replacement: "
            + ", ".join(sorted(unknown_tokens)),
            file=sys.stderr,
        )
    return prepared, literal_leftovers


# --------------------------------------------------------------------------- #
# Summary
# --------------------------------------------------------------------------- #
def percentile(values, p):
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    index = int(round((p / 100.0) * (len(ordered) - 1)))
    return float(ordered[max(0, min(len(ordered) - 1, index))])


def print_summary(rows, model_labels, case_count, over_budget_cases, literal_leftovers):
    print()
    print(
        f"cases selected: {case_count}  models: {len(model_labels)}  "
        f"over-budget cases: {over_budget_cases}  rows: {len(rows)}"
    )
    header = (
        f"{'model':26} {'ok':>4} {'bad':>4} {'err':>4} {'budget':>6} "
        f"{'pass%':>6} {'p50s':>6} {'p95s':>6}"
    )
    print(header)
    print("-" * len(header))
    for model in model_labels:
        model_rows = [r for r in rows if r["model"] == model]
        statuses = Counter(r["status"] for r in model_rows)
        ok_rows = [r for r in model_rows if r["status"] == "ok"]
        passed = sum(1 for r in ok_rows if (r["conformance"] or {}).get("ok"))
        pass_rate = (100.0 * passed / len(ok_rows)) if ok_rows else 0.0
        latencies = [
            r["latency_s"] for r in model_rows if r["status"] in ("ok", "bad_output")
        ]
        print(
            f"{model:26} {statuses.get('ok', 0):>4} {statuses.get('bad_output', 0):>4} "
            f"{statuses.get('error', 0):>4} {statuses.get('over_budget', 0):>6} "
            f"{pass_rate:>5.1f}% {percentile(latencies, 50):>6.2f} "
            f"{percentile(latencies, 95):>6.2f}"
        )
    total_statuses = Counter(r["status"] for r in rows if r["model"] != "baseline")
    print("model status counts:", dict(total_statuses))
    if literal_leftovers:
        print(
            f"WARNING: {literal_leftovers} prompt(s) still contain a literal '{{{{' "
            "after substitution"
        )
    else:
        print("prompts: no literal '{{' left after substitution")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def load_cases(path):
    cases = []
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"warning: skipping bad case line: {exc}", file=sys.stderr)
    return cases


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--limit", type=int, default=None, help="cases per model")
    parser.add_argument(
        "--repo", nargs="+", default=None, help="restrict to these repository names"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--repo-root", default="~/Dev")
    parser.add_argument("--no-baseline", action="store_true")
    parser.add_argument("--prompt", default=str(DEFAULT_PROMPT))
    parser.add_argument("--schema", default=str(DEFAULT_SCHEMA))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-diff-bytes", type=int, default=40000)
    parser.add_argument("--num-ctx", type=int, default=8192)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)

    try:
        cases = load_cases(args.cases)
    except OSError as exc:
        print(f"run: cannot read cases: {exc}", file=sys.stderr)
        return 2
    if not cases:
        print("run: no cases loaded", file=sys.stderr)
        return 2
    cases.sort(key=lambda c: c.get("date", 0), reverse=True)
    if args.repo:
        wanted = set(args.repo)
        cases = [c for c in cases if c.get("repo") in wanted]
        if not cases:
            print(f"run: no cases for repo(s) {sorted(wanted)}", file=sys.stderr)
            return 2
    if args.limit is not None and args.limit > 0:
        cases = cases[: args.limit]

    try:
        template = Path(args.prompt).read_text()
    except OSError as exc:
        print(f"run: cannot read prompt template: {exc}", file=sys.stderr)
        return 2
    try:
        with open(args.schema) as handle:
            schema = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"run: cannot read schema: {exc}", file=sys.stderr)
        return 2

    system_template, user_template = split_template(template)
    if user_template:
        system_template = strip_system_payload(system_template)
    prepared, literal_leftovers = prepare_cases(
        cases, args, system_template, user_template
    )
    over_budget_cases = sum(1 for item in prepared if item["over_budget"])
    settings = build_settings(args.num_ctx)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out_path.open("a") as output:

        def emit(row):
            rows.append(row)
            output.write(json.dumps(row) + "\n")
            output.flush()

        for model in args.models:
            for item in prepared:
                case = item["case"]
                row = make_row(case, model, settings)
                row["prompt_chars"] = item["prompt_chars"]
                try:
                    if item["over_budget"]:
                        row["status"] = "over_budget"
                    elif item["error"]:
                        row["status"] = "error"
                        row["error"] = item["error"]
                    else:
                        case_schema = (
                            build_schema(item["style"]) if item.get("style") else schema
                        )
                        content, latency, error = call_ollama(
                            args.base_url,
                            model,
                            item["system"],
                            item["user"],
                            case_schema,
                            args.num_ctx,
                            args.timeout,
                        )
                        row["subject_pattern"] = (
                            case_schema.get("properties", {})
                            .get("subject", {})
                            .get("pattern")
                        )
                        row["latency_s"] = round(latency, 3)
                        if error:
                            row["status"] = "error"
                            row["error"] = error
                            row["raw"] = (content or "")[:4000]
                        else:
                            subject, body, parse_error = parse_model_json(content)
                            if parse_error:
                                row["status"] = "bad_output"
                                row["error"] = parse_error
                                row["raw"] = content
                            else:
                                row["status"] = "ok"
                                row["subject"] = subject
                                row["body"] = body
                                row["message"] = assemble_message(subject, body)
                                row["conformance"] = check_message(
                                    item["style"], subject, body
                                )
                except Exception as exc:  # noqa: BLE001 - never kill the run
                    row["status"] = "error"
                    row["error"] = f"{type(exc).__name__}: {exc}"
                emit(row)

        if not args.no_baseline:
            for item in prepared:
                case = item["case"]
                row = make_row(case, "baseline", settings)
                row["prompt_chars"] = 0
                try:
                    if item["error"]:
                        row["status"] = "error"
                        row["error"] = item["error"]
                    else:
                        started = time.monotonic()
                        message = baseline_message(item["style"], case)
                        row["latency_s"] = round(time.monotonic() - started, 3)
                        row["status"] = "ok"
                        row["subject"] = message
                        row["body"] = ""
                        row["message"] = message
                        row["conformance"] = check_message(item["style"], message, "")
                except Exception as exc:  # noqa: BLE001
                    row["status"] = "error"
                    row["error"] = f"{type(exc).__name__}: {exc}"
                emit(row)

    model_labels = list(args.models)
    if not args.no_baseline:
        model_labels.append("baseline")
    print_summary(
        rows,
        model_labels,
        len(cases),
        over_budget_cases,
        literal_leftovers,
    )
    print(f"results written to {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
