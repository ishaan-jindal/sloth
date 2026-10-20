#!/usr/bin/env python3
"""Analyse the benchmark run against the pre-registered thresholds.

Reads results.jsonl (the run), fidelity.jsonl (deterministic unsupported-claim
checks) and judgments.jsonl (blind pairwise judge verdicts), and writes
summary.json + REPORT.md.

The pass/fail logic here is the pre-registered rubric from PROTOCOL.md. It is
deliberately mechanical: the thresholds were fixed before any model ran, so this
script does not get to reinterpret them.
"""

import argparse, json, statistics as st
from pathlib import Path

BENCH = Path(__file__).resolve().parent

# Pre-registered thresholds (PROTOCOL.md). Do not change without invalidating the run.
T1_CONFORMANCE = 0.95
T2_FIDELITY = 0.90
T3_WIN_RATE = 0.50
T4_LATENCY_MEDIAN = 8.0
T4_LATENCY_P95 = 20.0
T5_WIN_RATE_VS_BASELINE = 0.65
# "smallest model that is good enough" - the thesis lives at or below this size
SMALL_MODEL_CEILING_B = 4.0


def read_jsonl(p):
    p = Path(p)
    if not p.exists():
        return []
    out = []
    for line in p.open():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def pct(x):
    return round(100 * x, 1)


def latency_stats(vals):
    if not vals:
        return {}
    s = sorted(vals)
    return {
        "n": len(s),
        "p50": round(st.median(s), 2),
        "p90": round(s[min(len(s) - 1, int(len(s) * 0.90))], 2),
        "p95": round(s[min(len(s) - 1, int(len(s) * 0.95))], 2),
        "mean": round(sum(s) / len(s), 2),
        "max": round(s[-1], 2),
    }


def model_params(name):
    """Best-effort parameter count from the name, for the 'smallest model' claim."""
    import re

    m = re.search(r"(\d+(?:\.\d+)?)b", name.lower())
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)b", name.lower().replace("-", ""))
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(BENCH / "results.jsonl"))
    ap.add_argument("--fidelity", default=str(BENCH / "fidelity.jsonl"))
    ap.add_argument("--judgments", default=str(BENCH / "judgments.jsonl"))
    ap.add_argument("--cases", default=str(BENCH / "cases.jsonl"))
    ap.add_argument("--out-json", default=str(BENCH / "summary.json"))
    ap.add_argument("--out-md", default=str(BENCH / "REPORT.md"))
    ap.add_argument(
        "--safe-only",
        action="store_true",
        help="restrict the headline to post_cutoff_safe cases",
    )
    ap.add_argument("--label", default="headline")
    a = ap.parse_args()

    results = read_jsonl(a.results)
    fidelity = read_jsonl(a.fidelity)
    judgments = read_jsonl(a.judgments)
    cases = {c["id"]: c for c in read_jsonl(a.cases)}

    if a.safe_only:
        keep = {cid for cid, c in cases.items() if c.get("post_cutoff_safe")}
        results = [r for r in results if r.get("case_id") in keep]
        fidelity = [f for f in fidelity if f.get("case_id") in keep]
        judgments = [
            j
            for j in judgments
            if cases.get(j.get("case_id"), {}).get("post_cutoff_safe")
        ]

    by_model = {}
    for r in results:
        by_model.setdefault(r.get("model", "?"), []).append(r)

    fid_by = {}
    for f in fidelity:
        fid_by.setdefault(f.get("model", "?"), []).append(f)

    jud_by = {}
    for j in judgments:
        if j.get("model_won") is None and "winner" not in j:
            continue
        jud_by.setdefault(j.get("model_under_test", "?"), []).append(j)

    summary = {
        "label": a.label,
        "models": {},
        "thresholds": {
            "T1_conformance": T1_CONFORMANCE,
            "T2_fidelity": T2_FIDELITY,
            "T3_win_rate": T3_WIN_RATE,
            "T4_latency_p50": T4_LATENCY_MEDIAN,
            "T4_latency_p95": T4_LATENCY_P95,
            "T5_win_rate_vs_baseline": T5_WIN_RATE_VS_BASELINE,
        },
    }

    for model, rows in sorted(by_model.items()):
        ok = [r for r in rows if r.get("status") == "ok"]
        conf_pass = [r for r in ok if (r.get("conformance") or {}).get("ok")]
        prefix_pass = [
            r
            for r in ok
            if any(
                c.get("name") == "prefix_shape" and c.get("pass")
                for c in (r.get("conformance") or {}).get("checks", [])
            )
        ]
        scope_fail = [
            r
            for r in ok
            if any(
                c.get("name") == "scope_known" and not c.get("pass")
                for c in (r.get("conformance") or {}).get("checks", [])
            )
        ]
        lat = [r["latency_s"] for r in ok if r.get("latency_s") is not None]
        fid = fid_by.get(model, [])
        clean = [f for f in fid if f.get("clean")]
        body_ok = [f for f in fid if f.get("body_status") == "ok"]
        dup_body = [
            f
            for f in fid
            if any("duplicates" in n for n in (f.get("body_notes") or []))
        ]
        j = jud_by.get(model, [])
        decided = [x for x in j if x.get("model_won") is not None]
        wins = [x for x in decided if x.get("model_won") is True]
        unsupported_flagged = [
            x for x in decided if x.get("model_has_unsupported_claim") is True
        ]

        m = {
            "cases_attempted": len(rows),
            "cases_ok": len(ok),
            "cases_bad_output": sum(1 for r in rows if r.get("status") == "bad_output"),
            "cases_error": sum(1 for r in rows if r.get("status") == "error"),
            "cases_over_budget": sum(
                1 for r in rows if r.get("status") == "over_budget"
            ),
            "conformance_pass_rate": round(len(conf_pass) / len(ok), 4) if ok else None,
            "prefix_conformance_pass_rate": round(len(prefix_pass) / len(ok), 4)
            if ok
            else None,
            "scope_vocab_failure_rate": round(len(scope_fail) / len(ok), 4)
            if ok
            else None,
            "fidelity_clean_rate": round(len(clean) / len(fid), 4) if fid else None,
            "fidelity_rows": len(fid),
            "body_convention_rate": round(len(body_ok) / len(fid), 4) if fid else None,
            "body_duplicates_subject_rate": round(len(dup_body) / len(fid), 4)
            if fid
            else None,
            "latency": latency_stats(lat),
            "judged_pairs": len(decided),
            "win_rate_vs_human": round(len(wins) / len(decided), 4)
            if decided
            else None,
            "judge_unsupported_flag_rate": round(
                len(unsupported_flagged) / len(decided), 4
            )
            if decided
            else None,
            "params_b": model_params(model),
        }
        m["T1"] = (
            m["conformance_pass_rate"] is not None
            and m["conformance_pass_rate"] >= T1_CONFORMANCE
        )
        m["T2"] = (
            m["fidelity_clean_rate"] is not None
            and m["fidelity_clean_rate"] >= T2_FIDELITY
        )
        m["T3"] = (
            m["win_rate_vs_human"] is not None and m["win_rate_vs_human"] >= T3_WIN_RATE
        )
        m["T4"] = (
            bool(lat)
            and m["latency"].get("p50", 1e9) <= T4_LATENCY_MEDIAN
            and m["latency"].get("p95", 1e9) <= T4_LATENCY_P95
        )
        summary["models"][model] = m

    base = summary["models"].get("baseline")
    for model, m in summary["models"].items():
        if model == "baseline":
            m["T5"] = None
            continue
        if (
            base
            and m.get("win_rate_vs_human") is not None
            and base.get("win_rate_vs_human") is not None
        ):
            m["T5"] = m["win_rate_vs_human"] > base["win_rate_vs_human"]
        else:
            m["T5"] = None

    qualifying = [
        mo
        for mo, m in summary["models"].items()
        if mo != "baseline"
        and all([m.get("T1"), m.get("T2"), m.get("T3"), m.get("T4")])
    ]
    summary["qualifying_models"] = sorted(
        qualifying, key=lambda mo: summary["models"][mo].get("params_b") or 1e9
    )
    summary["smallest_qualifying"] = (
        summary["qualifying_models"][0] if summary["qualifying_models"] else None
    )

    Path(a.out_json).write_text(json.dumps(summary, indent=2))

    # markdown
    L = []
    L.append(f"# Sloth benchmark report - {a.label}\n")
    L.append(
        "Pre-registered protocol: `bench/PROTOCOL.md`. The thresholds and the rubric "
        "were written before any model was run; every amendment made on the way is "
        "recorded there.\n"
    )
    env_path = BENCH / "environment.json"
    if env_path.exists():
        env = json.loads(env_path.read_text())
        L.append("## Setup\n")
        L.append("| | |")
        L.append("|---|---|")
        L.append(f"| captured | {env.get('captured')} |")
        L.append(f"| os | {env.get('os')} |")
        L.append(f"| cpu | {env.get('cpu')} ({env.get('cores')} threads) |")
        L.append(f"| memory | {env.get('memory')} |")
        L.append(f"| gpu | {env.get('gpu')} |")
        L.append(f"| runtime | {env.get('ollama')} |")
        L.append(
            "| models | "
            + ", ".join(f"`{m['name']}`" for m in env.get("models", []))
            + " |"
        )
        L.append("")
    L.append(
        f"Cases: {len(results)} rows | fidelity rows: {len(fidelity)} | judged pairs: {len(judgments)}\n"
    )
    L.append("\n## Per model\n")
    L.append(
        "| model | params | ok/tried | conformance | fidelity clean | body ok | win vs human | p50 s | p95 s | T1 | T2 | T3 | T4 | T5 |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for model, m in sorted(
        summary["models"].items(),
        key=lambda kv: (kv[0] == "baseline", kv[1].get("params_b") or 1e9),
    ):

        def f(v, scale=100):
            return "-" if v is None else f"{round(v * scale, 1)}"

        L.append(
            f"| `{model}` | {m.get('params_b') or '-'} | {m['cases_ok']}/{m['cases_attempted']} | "
            f"{f(m['conformance_pass_rate'])}% | {f(m['fidelity_clean_rate'])}% | "
            f"{f(m['body_convention_rate'])}% | "
            f"{f(m['win_rate_vs_human'])}% | {m['latency'].get('p50', '-')} | {m['latency'].get('p95', '-')} | "
            f"{'PASS' if m.get('T1') else 'FAIL'} | {'PASS' if m.get('T2') else 'FAIL'} | "
            f"{'PASS' if m.get('T3') else 'FAIL'} | {'PASS' if m.get('T4') else 'FAIL'} | "
            f"{'-' if m.get('T5') is None else ('PASS' if m.get('T5') else 'FAIL')} |"
        )
    L.append("\n## Failure modes\n")
    L.append("| model | bad_output | error | over_budget |")
    L.append("|---|---|---|---|")
    for model, m in sorted(summary["models"].items()):
        L.append(
            f"| `{model}` | {m['cases_bad_output']} | {m['cases_error']} | {m['cases_over_budget']} |"
        )
    L.append(
        f"\n**Smallest qualifying model: `{summary['smallest_qualifying'] or 'NONE'}`**"
    )
    if not summary["smallest_qualifying"]:
        L.append(
            "\nNo model cleared every pre-registered threshold. That is a finding, not a bug."
        )
    else:
        L.append(
            "\nQualifying models, smallest first: "
            + ", ".join(f"`{m}`" for m in summary["qualifying_models"])
        )

    # Per-repository breakdown: the honest answer is not the same for every repo.
    L.append("\n## By repository\n")
    L.append("Conformance% / clean-fidelity% for each model on each repository.\n")
    repos = sorted({r.get("repo") for r in results if r.get("repo")})
    models_sorted = sorted(
        summary["models"].items(),
        key=lambda kv: (kv[0] == "baseline", kv[1].get("params_b") or 1e9),
    )
    L.append("| repo | " + " | ".join(f"`{m}`" for m, _ in models_sorted) + " |")
    L.append("|---" * (len(models_sorted) + 1) + "|")
    for repo in repos:
        cells = []
        for model, _ in models_sorted:
            rows = [
                r
                for r in results
                if r.get("repo") == repo
                and r.get("model") == model
                and r.get("status") == "ok"
            ]
            frows = [
                f for f in fidelity if f.get("repo") == repo and f.get("model") == model
            ]
            if not rows:
                cells.append("-")
                continue
            conf = sum(1 for r in rows if (r.get("conformance") or {}).get("ok")) / len(
                rows
            )
            clean = (
                (sum(1 for f in frows if f.get("clean")) / len(frows))
                if frows
                else None
            )
            cell = f"{round(conf * 100)}%"
            if clean is not None:
                cell += f" / {round(clean * 100)}%"
            cells.append(cell)
        L.append(f"| `{repo}` | " + " | ".join(cells) + " |")

    L.append(
        "\nThresholds are fixed in PROTOCOL.md and were written before any model was run.\n"
    )
    Path(a.out_md).write_text("\n".join(L))
    print("\n".join(L))
    print(f"\nwrote {a.out_json} and {a.out_md}")


if __name__ == "__main__":
    main()
