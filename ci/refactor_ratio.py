#!/usr/bin/env python3
"""Report the add:delete ratio of production code, month by month — a tripwire, never a KPI.

Rationale and how to read it: docs/lint-ratchet.md of the skills repo. Baselines and duplication gates
catch defects per file. What they do not measure is the direction of the whole repository: code that
only grows is code nobody is moving, and an agent that copies instead of moving pushes the ratio up
without producing a single lint finding. ~2x is the commonly cited norm.

The `refactor:` columns are the same measurement restricted to commits whose subject claims to be a
refactor — if even those add more than they delete, the label is decoration.

It is deliberately NOT a gate: a ratio target is gamed by renaming commits, and a deletion quota is
worse than the disease. The script ALWAYS exits 0.

    python3 .github/scripts/refactor_ratio.py                    # last 7 months, markdown
    python3 .github/scripts/refactor_ratio.py --months 12
    python3 .github/scripts/refactor_ratio.py --until 2026-08-16 # reproduce a past report

Which files count comes from `## Lint ratchet` › **Production globs** in `docs/agents/skills-config.md`
(they are handed to `git log` as pathspecs). With none configured it measures the whole tree and says
so. Python 3 stdlib only; `git` is the one external tool.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skills_config  # noqa: E402

REFACTOR_SUBJECT = re.compile(r"^refactor[(!:]")
RECORD = "\x01"
FIELD = "\x02"
FOOTER = (
    "Tripwire, not a KPI: a ratio far above ~2x is a reason to look, never a target — a ratio goal is "
    "gamed by renaming a commit, and a deletion quota is worse than the disease."
)


class Bucket:
    __slots__ = ("added", "deleted")

    def __init__(self) -> None:
        self.added = 0
        self.deleted = 0

    def add(self, added: int, deleted: int) -> None:
        self.added += added
        self.deleted += deleted

    @property
    def ratio(self) -> float | None:
        return self.added / self.deleted if self.deleted else None


def git_log(repo: str, until: str | None, globs: list) -> str:
    cmd = ["git", "log", "-M", "--numstat", f"--format={RECORD}%cd{FIELD}%s", "--date=format:%Y-%m"]
    if until:
        cmd.append(f"--until={until}")
    cmd += ["--", *globs] if globs else []
    return subprocess.run(
        cmd, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False
    ).stdout


def collect(repo: str, until: str | None, globs: list) -> tuple:
    """Per month: every commit, and only the commits whose subject claims to be a refactor."""
    every: dict = {}
    refactors: dict = {}
    month, is_refactor = None, False
    for line in git_log(repo, until, globs).split("\n"):
        if line.startswith(RECORD):
            month, _, subject = line[1:].partition(FIELD)
            is_refactor = bool(REFACTOR_SUBJECT.match(subject))
            continue
        parts = line.split("\t")
        # A binary file is reported as "-\t-\t<path>": the parse must not blow up on one.
        if len(parts) != 3 or month is None or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        added, deleted = int(parts[0]), int(parts[1])
        every.setdefault(month, Bucket()).add(added, deleted)
        if is_refactor:
            refactors.setdefault(month, Bucket()).add(added, deleted)
    return every, refactors


def months_ending_at(last: str, count: int) -> list:
    year, month = (int(part) for part in last.split("-"))
    out = []
    for _ in range(count):
        out.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(out))


def cell(bucket: Bucket | None) -> tuple:
    if bucket is None or (bucket.added == 0 and bucket.deleted == 0):
        return "—", "—", "—"
    ratio = bucket.ratio
    return f"{bucket.added:,}", f"{bucket.deleted:,}", f"{ratio:.1f}x" if ratio is not None else "∞"


def render(every: dict, refactors: dict, months: list, partial: str, globs: list) -> str:
    scope = ", ".join(f"`{glob}`" for glob in globs) if globs else "the whole tree (no production globs configured)"
    lines = [
        f"### Refactor ratio — {scope}",
        "",
        "| Month | Lines + | Lines − | Ratio | `refactor:` + | `refactor:` − | `refactor:` ratio |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for month in months:
        label = f"{month} (partial)" if month == partial else month
        added, deleted, ratio = cell(every.get(month))
        r_added, r_deleted, r_ratio = cell(refactors.get(month))
        lines.append(f"| {label} | {added} | {deleted} | {ratio} | {r_added} | {r_deleted} | {r_ratio} |")
    lines += ["", FOOTER]
    return "\n".join(lines)


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monthly add:delete ratio of production code.")
    parser.add_argument("--months", type=int, default=7)
    parser.add_argument("--until", default=None, help="YYYY-MM-DD; the report ends at this date")
    parser.add_argument("--repo", default=None)
    parser.add_argument("--config", default=None, help="path to the skills config")
    args = parser.parse_args(argv)

    repo = args.repo or skills_config.default_repo()
    globs = skills_config.load(repo, args.config).production_globs
    today = datetime.date.fromisoformat(args.until) if args.until else datetime.date.today()
    every, refactors = collect(repo, args.until, globs)
    months = months_ending_at(today.strftime("%Y-%m"), max(args.months, 1))
    # `today` already honours --until: the "(partial)" marker must move with the report's own horizon,
    # or re-running a past report on a later day silently drops it.
    print(render(every, refactors, months, today.strftime("%Y-%m"), globs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
