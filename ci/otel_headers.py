#!/usr/bin/env python3
"""Print the OTLP auth header for Claude Code, reading the key from a GitHub Actions VARIABLE.

`otelHeadersHelper` in .claude/settings.json points here. Claude Code runs it at session start and
again every ~29 min (`CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS`), so a rotated key is picked up by
a running session without reopening it. Dynamic headers only work on the `http/*` OTLP protocols.

The key lives in a repo *variable*, not a secret, because a secret is write-only and no `gh` command
can read its value back. Use an **intake-only** key — one that can write telemetry and read nothing —
so "readable by anyone with collaborator read on the repo" is the intended clearance, not a leak.
Never the application's key.

Which variable, which header and which repo come from `## Telemetry` in `docs/agents/skills-config.md`:

    - **Key variable:** `CLAUDE_CODE_OTEL_API_KEY`   (default)
    - **Key header:** `dd-api-key`                   (default; `Authorization` for a bearer intake)
    - **Key repo:** `owner/name`                     (default: whatever `gh` infers from the checkout)

Contract with the harness: print ONE json object of headers on stdout and exit 0 — always. Any failure
(no `gh`, not logged in, offline, variable absent) prints `{}` and stays quiet: a telemetry helper must
never delay or break a session start, and must never write to stderr.

    python3 .github/scripts/otel_headers.py

Python 3 stdlib only, 3.9-compatible.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skills_config  # noqa: E402

TIMEOUT_SECONDS = 10


def read_key(variable: str, repo: str | None = None) -> str | None:
    """The variable's value, or None on any failure. `gh` exits non-zero and stays on stderr when absent."""
    command = ["gh", "variable", "get", variable]
    if repo:
        command += ["--repo", repo]
    try:
        proc = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except Exception:  # noqa: BLE001 — gh missing, timeout, anything: the answer is the same
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    # A key is one opaque token. Anything with whitespace is a message, not a value — never a header.
    if not value or len(value.split()) != 1:
        return None
    return value


def headers(config: skills_config.Config) -> dict:
    key = read_key(config.otel_key_variable, config.otel_key_repo)
    return {config.otel_key_header: key} if key else {}


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the OTLP auth header for Claude Code.")
    parser.add_argument("--repo", default=None, help="repository root (default: the enclosing git repo)")
    parser.add_argument("--config", default=None, help="path to the skills config")
    try:
        args = parser.parse_args(argv)
        config = skills_config.load(args.repo or skills_config.default_repo(), args.config)
        print(json.dumps(headers(config)))
    except Exception:  # noqa: BLE001 — stdout must still be valid json
        print("{}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
