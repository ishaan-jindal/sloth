#!/usr/bin/env python3
"""Capture real commits as benchmark fixtures.

Mechanical only: filtering, capture, redaction. No judgement, no model calls.
Every skip reason is recorded in skips.jsonl so the corpus is auditable.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
OUT = Path(__file__).resolve().parent

# name, path, since, quota
REPOS = [
    ("matcha", HOME / "Dev/matcha", "2026-06-01", 30),
    ("runnix", HOME / "Dev/runnix", "2026-06-01", 15),
    ("vaults-hub", HOME / "Dev/vaults-hub", "2026-06-01", 15),
    ("termchat", HOME / "Dev/Termchat/termchat", "2026-06-01", 10),
    ("portfolio-website", HOME / "Dev/portfolio-website", "2026-06-01", 10),
]

POST_CUTOFF = 1788220800  # 2026-09-01T00:00:00Z — must match PROTOCOL.md
MAX_DIFF = 60_000
BOT = re.compile(r"dependabot|renovate|github-actions|\bbot\b|update submodules", re.I)
SECRETS = [
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), "gh_TOKEN"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "sk_KEY"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS_KEY"),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
        ),
        "PRIVATE_KEY",
    ),
    (
        re.compile(
            r"(?i)(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*[\"']?([^\s\"',;]{8,})"
        ),
        r"\1=REDACTED",
    ),
]


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def redact(s):
    for pat, rep in SECRETS:
        s = pat.sub(rep, s)
    return s


def main():
    cases, skips = [], []
    for name, repo, since, quota in REPOS:
        if not repo.is_dir():
            skips.append({"repo": name, "reason": "missing_repo", "sha": None})
            continue
        raw = git(
            repo,
            "log",
            "--no-merges",
            f"--since={since}",
            "--format=%H%x1f%ct%x1f%an%x1f%s",
        )
        rows = [ln.split("\x1f", 3) for ln in raw.splitlines() if ln.count("\x1f") == 3]
        taken = 0
        for sha, ct, author, subject in rows:  # newest first
            if taken >= quota:
                break
            if len(subject.strip()) < 11:
                skips.append({"repo": name, "sha": sha, "reason": "short_subject"})
                continue
            if BOT.search(subject) or BOT.search(author):
                skips.append({"repo": name, "sha": sha, "reason": "bot"})
                continue
            diff = git(repo, "show", "--format=", "--no-color", "--unified=3", sha)
            if not diff.strip():
                skips.append({"repo": name, "sha": sha, "reason": "empty_diff"})
                continue
            if len(diff) > MAX_DIFF:
                skips.append(
                    {
                        "repo": name,
                        "sha": sha,
                        "reason": "diff_too_large",
                        "bytes": len(diff),
                    }
                )
                continue
            numstat = git(repo, "diff-tree", "--no-commit-id", "--numstat", "-r", sha)
            files = []
            for line in numstat.splitlines():
                parts = line.split("\t")
                if len(parts) == 3:
                    files.append({"path": parts[2], "add": parts[0], "del": parts[1]})
            body = git(repo, "log", "-1", "--format=%b", sha).strip()
            cases.append(
                {
                    "id": f"{name}:{sha[:12]}",
                    "repo": name,
                    "sha": sha,
                    "date": int(ct),
                    "author": author,
                    "subject": subject.strip(),
                    "body": redact(body),
                    "files": files,
                    "diff": redact(diff),
                    "diff_bytes": len(diff),
                    "post_cutoff_safe": int(ct) > POST_CUTOFF,
                }
            )
            taken += 1
        if taken < quota:
            skips.append(
                {"repo": name, "sha": None, "reason": "quota_unmet", "got": taken}
            )

    with (OUT / "cases.jsonl").open("w") as f:
        for c in cases:
            f.write(json.dumps(c) + "\n")
    with (OUT / "skips.jsonl").open("w") as f:
        for s in skips:
            f.write(json.dumps(s) + "\n")

    print(f"cases: {len(cases)}  skips: {len(skips)}")
    by_repo = {}
    for c in cases:
        b = by_repo.setdefault(c["repo"], {"n": 0, "safe": 0, "bytes": 0})
        b["n"] += 1
        b["safe"] += bool(c["post_cutoff_safe"])
        b["bytes"] += c["diff_bytes"]
    for r, b in by_repo.items():
        print(
            f"  {r:20} n={b['n']:3}  post_cutoff_safe={b['safe']:3}  "
            f"mean_diff={b['bytes'] // max(b['n'], 1):6}B"
        )
    reasons = {}
    for s in skips:
        reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
    print("  skips:", reasons)


if __name__ == "__main__":
    sys.exit(main())
