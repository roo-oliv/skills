#!/usr/bin/env python3
"""Gate the LINT RATCHET on the delta: what a branch adds is judged, what the repo already carries is not.

Rationale, the maintenance policy and the equivalents outside the JVM: docs/lint-ratchet.md of the
skills repo (vendored here as a pointer, not a copy).

Subcommands:

  config-rides-alone  a change that loosens a lint config, or that GROWS a suppression baseline, may
                      not also touch production code. Loosening a threshold is a trade; a trade that
                      rides along in a feature PR is a trade nobody reviewed.
  cpd-delta           reads a CPD (PMD copy-paste detector) report and fails on a duplication THIS
                      BRANCH wrote — an occurrence whose lines are more than half new. Legacy
                      duplications pass untouched. There is no committed CPD baseline on purpose: a
                      CPD baseline is per line, and any shift invalidates it. The diff IS the baseline.

DELTA SEMANTICS. Head is the WORKTREE (untracked files included, so the gate runs before a commit);
base is `git merge-base $LINT_RATCHET_BASE_REF HEAD`. A baseline that SHRINKS is always fine — the
ratchet only turns one way. With NO base ref the subcommand prints a skip line and exits 0, so a push
to the default branch stays green; a base ref that was passed and does NOT resolve is a broken setup,
not an absent one, and exits 1 rather than disarming the gate in silence.

CONFIG-DRIVEN, and OFF until configured. Everything the gates look at comes from `## Lint ratchet` in
`docs/agents/skills-config.md`:

    - **Lint config files:** `config/detekt/detekt.yml`     # the thresholds; empty = that half is off
    - **Baseline files:** `config/detekt/baseline-*.xml`     # fnmatch globs; empty = that half is off
    - **Baseline entry pattern:** `<ID>`                     # regex counted per file (default)
    - **Production globs:** `*/src/main/kotlin/*.kt`         # EMPTY = both gates skip and pass
    - **CPD command:** `./gradlew cpd`                       # documentation only; CI runs it
    - **CPD report:** `build/reports/cpd/cpd.xml`

`*` in a production glob crosses `/` (fnmatch, and git's own pathspec matching) — `*/src/main/*.kt`
already covers every subpackage. Do not write `**`: it would describe a semantics this file does not
implement.

    python3 .github/scripts/lint_ratchet.py config-rides-alone --base origin/main
    ./gradlew cpd && python3 .github/scripts/lint_ratchet.py cpd-delta --base origin/main

Python 3 stdlib only; `git` is the one external tool.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from fnmatch import fnmatch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skills_config  # noqa: E402

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
DOC = "docs/lint-ratchet.md (skills repo)"
OFF = "lint-ratchet: %s skipped — no `Production globs` in `## Lint ratchet` of the config"


def git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False
    ).stdout


def merge_base(repo: str, base: str) -> str | None:
    out = git(repo, "merge-base", base, "HEAD").strip()
    return out or None


def changed_paths(repo: str, base_sha: str) -> list:
    """Everything this branch touches: committed diff against the base PLUS the dirty worktree."""
    tracked = git(repo, "diff", "--name-only", "--no-renames", base_sha).split("\n")
    untracked = git(repo, "ls-files", "--others", "--exclude-standard").split("\n")
    return sorted({path for path in tracked + untracked if path})


def matches_any(path: str, globs: list) -> bool:
    return any(fnmatch(path, glob) for glob in globs)


def count_entries(text: str, pattern: str) -> int:
    """How many suppressed findings a baseline carries — the config's regex, counted per file."""
    try:
        return len(re.findall(pattern, text))
    except re.error:
        return text.count(pattern)


def entries_in_worktree(repo: str, path: str, pattern: str) -> int:
    try:
        with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as handle:
            return count_entries(handle.read(), pattern)
    except OSError:
        return 0


def entries_at_base(repo: str, base_sha: str, path: str, pattern: str) -> int:
    return count_entries(git(repo, "show", f"{base_sha}:{path}"), pattern)


def resolve_base(repo: str, base: str | None, command: str) -> tuple:
    """(merge-base sha, early exit code) — the two "no base" cases are NOT the same thing.

    No base ref at all is the push-to-default-branch run: nothing to diff against, skip and pass. A
    base ref that was PASSED and does not resolve is a broken setup (shallow checkout, ref never
    fetched), and it must be loud: a gate that disarms itself in silence is the failure this file
    exists for.
    """
    if not base:
        print(f"lint-ratchet: no base ref — {command} skipped")
        return None, 0
    base_sha = merge_base(repo, base)
    if not base_sha:
        print(
            f"lint-ratchet: FAIL — base ref '{base}' does not resolve against HEAD "
            "(shallow checkout? ref never fetched?). "
            f"{command} refuses to pass without a base to diff against."
        )
        return None, 1
    return base_sha, None


def config_rides_alone(repo: str, base: str | None, config: skills_config.Config) -> int:
    if not config.production_globs:
        print(OFF % "config-rides-alone")
        return 0
    if not config.lint_config_files and not config.baseline_globs:
        print("lint-ratchet: config-rides-alone skipped — no lint config files and no baseline files configured")
        return 0

    base_sha, early = resolve_base(repo, base, "config-rides-alone")
    if early is not None or base_sha is None:
        return early or 0

    changed = changed_paths(repo, base_sha)
    ratchet: list = []
    for path in changed:
        if path in config.lint_config_files or matches_any(path, config.lint_config_files):
            ratchet.append(f"{path} (thresholds)")
            continue
        if not matches_any(path, config.baseline_globs):
            continue
        before = entries_at_base(repo, base_sha, path, config.baseline_entry_pattern)
        after = entries_in_worktree(repo, path, config.baseline_entry_pattern)
        if after > before:
            ratchet.append(f"{path} ({before} -> {after} entries)")

    production = [path for path in changed if matches_any(path, config.production_globs)]
    if not ratchet or not production:
        print(
            f"lint-ratchet: config-rides-alone OK "
            f"({len(ratchet)} ratchet change(s), {len(production)} production file(s), base {base_sha[:12]})"
        )
        return 0

    print("lint-ratchet: FAIL — a lint threshold/baseline change is riding along with production code.")
    print("  Ratchet side:")
    for item in ratchet:
        print(f"    {item}")
    print(f"  Production side ({len(production)} file(s)):")
    for path in production[:10]:
        print(f"    {path}")
    if len(production) > 10:
        print(f"    ... and {len(production) - 10} more")
    print(f"  Split it: the threshold/baseline move is its OWN change, with the justification in the body ({DOC}).")
    print("  Tightening a rule AND fixing the code it flags is the same split — land the fix first.")
    return 1


# ── cpd-delta ─────────────────────────────────────────────────────────────────────────────────────


def added_lines(repo: str, base_sha: str, globs: list) -> dict:
    """Line numbers this branch ADDED to each production file, head-side.

    `-M` keeps a PURE move from counting as new code — a 100%-similar rename emits no hunk at all. The
    filter must still include `R`: a rename that also gained content is reported as `R60`/`R70`, and
    dropping it would hide a pasted block behind a `git mv` in the same commit. `D` is the only status
    left out, and a deletion has no head-side line to count. An untracked file contributes every line.
    """
    added: dict = {}
    diff = git(repo, "diff", "-U0", "-M", "--diff-filter=AMR", base_sha, "--", *globs)
    path = None
    for line in diff.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if line.startswith("+++ "):  # /dev/null
            path = None
            continue
        match = HUNK_RE.match(line)
        if not match or path is None:
            continue
        start = int(match.group(1))
        count = int(match.group(2)) if match.group(2) is not None else 1
        added.setdefault(path, set()).update(range(start, start + count))

    for candidate in git(repo, "ls-files", "--others", "--exclude-standard").split("\n"):
        if not candidate or not matches_any(candidate, globs):
            continue
        try:
            with open(os.path.join(repo, candidate), encoding="utf-8", errors="replace") as handle:
                total = sum(1 for _ in handle)
        except OSError:
            continue
        added.setdefault(candidate, set()).update(range(1, total + 1))
    return added


def local_name(tag: str) -> str:
    """The tag without its namespace — PMD 7 namespaces the CPD report, PMD 6 does not."""
    return tag.rsplit("}", 1)[-1]


def children(node, name: str) -> list:
    return [child for child in node if local_name(child.tag) == name]


def cpd_delta(repo: str, base: str | None, report_path: str, config: skills_config.Config) -> int:
    if not config.production_globs:
        print(OFF % "cpd-delta")
        return 0
    report = report_path if os.path.isabs(report_path) else os.path.join(repo, report_path)
    if not os.path.isfile(report):
        hint = config.cpd_command or "your CPD task"
        print(f"lint-ratchet: FAIL — CPD report not found at {report_path}. Run `{hint}` first.")
        return 1

    base_sha, early = resolve_base(repo, base, "cpd-delta")
    if early is not None or base_sha is None:
        return early or 0

    added = added_lines(repo, base_sha, config.production_globs)
    root = ElementTree.parse(report).getroot()
    duplications = children(root, "duplication")
    offenders = []
    for duplication in duplications:
        occurrences = []
        fresh = False
        for occurrence in children(duplication, "file"):
            path = os.path.relpath(occurrence.get("path", ""), repo).replace(os.sep, "/")
            start, end = int(occurrence.get("line", 0)), int(occurrence.get("endline", 0))
            span = range(start, end + 1)
            new = len(added.get(path, set()).intersection(span))
            # More than HALF the occurrence is new: a one-line fix inside a legacy block is not a new
            # duplication, a wholesale paste is.
            fresh = fresh or 2 * new > len(span)
            occurrences.append((path, start, end, new, len(span)))
        if fresh:
            offenders.append((duplication, occurrences))

    if not offenders:
        print(
            f"lint-ratchet: cpd-delta OK — {len(duplications)} legacy duplication(s) ignored "
            f"(base {base_sha[:12]})"
        )
        return 0

    print(f"lint-ratchet: FAIL — {len(offenders)} duplication(s) written by this branch:")
    for duplication, occurrences in offenders:
        print(f"  lines={duplication.get('lines')} tokens={duplication.get('tokens')}")
        for path, start, end, new, total in occurrences:
            print(f"    {path}:{start}-{end} ({new}/{total} lines new)")
        fragment = children(duplication, "codefragment")
        for line in (fragment[0].text or "").strip().split("\n")[:3] if fragment else []:
            print(f"      | {line.strip()}")
    print("  Extract the shared block, or move it — a copy is the defect this gate exists for.")
    print(f"  Genuine coincidence: wrap it in `CPD-OFF` / `CPD-ON` WITH the reason ({DOC}).")
    return 1


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint-ratchet delta gates.")
    parser.add_argument("command", choices=["config-rides-alone", "cpd-delta"])
    parser.add_argument("--base", default=os.environ.get("LINT_RATCHET_BASE_REF") or None)
    parser.add_argument("--repo", default=None)
    parser.add_argument("--config", default=None, help="path to the skills config")
    parser.add_argument("--report", default=None, help="cpd-delta only: the CPD XML report")
    args = parser.parse_args(argv)

    repo = args.repo or skills_config.default_repo()
    config = skills_config.load(repo, args.config)
    if args.command == "cpd-delta":
        return cpd_delta(repo, args.base, args.report or config.cpd_report, config)
    return config_rides_alone(repo, args.base, config)


if __name__ == "__main__":
    sys.exit(main())
