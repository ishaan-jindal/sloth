#!/usr/bin/env python3
"""Diff the Rust miner's view against the Python benchmark miner's view.

Two independent implementations of the same rules. If they disagree, the
benchmark is not measuring the tool.
"""

import json, subprocess, sys
from pathlib import Path

BENCH = Path("/home/sacred/Dev/hacktoberfest/bench")
REPOS = {
    "matcha": "~/Dev/matcha",
    "runnix": "~/Dev/runnix",
    "vaults-hub": "~/Dev/vaults-hub",
    "termchat": "~/Dev/Termchat/termchat",
    "portfolio-website": "~/Dev/portfolio-website",
}


def rust_dump():
    out = subprocess.run(
        ["cargo", "run", "--quiet", "--release"],
        cwd="/tmp/opencode/xcheck",
        capture_output=True,
        text=True,
        timeout=600,
    ).stdout
    rust = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        repo, key, val = parts
        rust.setdefault(repo, {})[key] = val
    return rust


def vocab(s):
    d = {}
    for part in (s or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            d[k] = int(v)
    return d


def main():
    rust = rust_dump()
    styles = json.loads((BENCH / "styles.json").read_text())
    problems = []
    for name in REPOS:
        r = rust.get(name, {})
        p = styles.get(name, {})
        if not r:
            problems.append(f"{name}: rust produced nothing")
            continue

        def cmp(label, rv, pv, tol=0.006):
            if rv is None or pv is None:
                return
            if isinstance(rv, str):
                low = rv.strip().lower()
                if low in ("true", "false"):
                    rv = low == "true"
                else:
                    try:
                        rv = float(rv)
                    except ValueError:
                        pass
            if isinstance(pv, bool):
                if isinstance(rv, bool) and rv != pv:
                    problems.append(f"{name}.{label}: rust={rv} python={pv}")
                return
            if isinstance(rv, float) or isinstance(pv, float):
                if abs(float(rv) - float(pv)) > tol:
                    problems.append(f"{name}.{label}: rust={rv} python={pv}")
            elif rv != pv:
                problems.append(f"{name}.{label}: rust={rv!r} python={pv!r}")

        cmp("shape", r.get("shape"), p.get("shape"))
        cmp("confidence", r.get("confidence"), p.get("confidence"))
        cmp("pr_suffix", r.get("pr_suffix"), p.get("pr_suffix"))
        cmp("body_usage", r.get("body_usage"), p.get("body_usage"))
        cmp("scope_rate", r.get("scope_rate"), p.get("scope_rate"))
        cmp("median", r.get("median"), (p.get("subject_len") or {}).get("median"))
        cmp("p90", r.get("p90"), (p.get("subject_len") or {}).get("p90"))
        cmp(
            "body_median_words",
            r.get("body_median_words"),
            (p.get("body_words") or {}).get("median"),
        )
        if vocab(r.get("types")) != (p.get("type_vocab") or {}):
            problems.append(f"{name}.type_vocab differs")
        if vocab(r.get("scopes")) != (p.get("scope_vocab") or {}):
            problems.append(f"{name}.scope_vocab differs")
        rh = [h for h in (r.get("body_headings") or "").split(" | ") if h]
        ph = [h.lstrip("#").strip() for h in (p.get("body_sections") or [])]
        if rh != ph:
            problems.append(f"{name}.body_headings: rust={rh} python={ph}")
        if (r.get("exemplar") or "") != ((p.get("exemplars") or [""])[0]):
            problems.append(
                f"{name}.exemplar: rust={r.get('exemplar')!r} python={(p.get('exemplars') or [''])[0]!r}"
            )

    if problems:
        print(f"{len(problems)} disagreement(s):")
        for x in problems:
            print("  " + x)
        return 1
    print("rust and python miners agree on every checked field")
    return 0


if __name__ == "__main__":
    sys.exit(main())
