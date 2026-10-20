#!/usr/bin/env python3
"""Capture the machine and the model inventory, so the report states its own setup."""

import json, platform, subprocess, sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent


def sh(*cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def main():
    cpu = ""
    for line in sh("lscpu").splitlines():
        if line.startswith("Model name:"):
            cpu = line.split(":", 1)[1].strip()
            break
    mem = ""
    for line in sh("free", "-h").splitlines():
        if line.startswith("Mem:"):
            mem = line.split()[1]
            break
    gpu = sh("nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader")
    ollama = sh("ollama", "--version") or sh("ollama", "-v")

    models = []
    raw = sh("ollama", "list")
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 4:
            models.append({"name": parts[0], "id": parts[1], "size": parts[2]})

    env = {
        "captured": subprocess.run(
            ["date", "-Is"], capture_output=True, text=True
        ).stdout.strip(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu,
        "cores": sh("nproc"),
        "memory": mem,
        "gpu": gpu or "none discrete",
        "ollama": ollama,
        "models": models,
    }
    (BENCH / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    print(json.dumps(env, indent=2))


if __name__ == "__main__":
    sys.exit(main())
