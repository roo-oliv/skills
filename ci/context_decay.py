#!/usr/bin/env python3
"""List the context surfaces that may have stopped describing something alive — the monthly decay queue.

A surface is a CANDIDATE only when it carries at least one naming signal. Age qualifies, it never names:

  D1 superseded    a `> **YYYY-MM-DD:** superseded — …` banner in the first 15 lines
  D2 broken-ref    a context-lint finding of C6/C7/C8/C9/C13/C14 on the file — C10 NEVER counts (what is
                   left of it in WARN is framework types the identifier index cannot see, by design)
  D3 unloaded      a telemetry report marks the surface — or one premise H2 of a loaded file — `zero-load`
                   (a premise row is matched by its `**Id:**` when it has one, by (path, title) otherwise)
  D4 dated         an ephemeral doc whose effective date is older than `--since` and has no banner
  D5 dead-scope    a rule whose `paths:` glob matches no tracked file (all globs dead = the rule never loads)
  D6 unreachable   a doc not reachable from the docs index by links or backticked paths
  D7 promotion-due a recurring-failure-modes entry with `Occurrences >= 2` still `advisory` (a gate owed)
  D8 quality      a premise the commit trailers class as `confusing` (read AND violated twice) or
                  `undiscoverable` (violated without ever being read) — telemetry schema_version 3
  D9 near-duplicate  a premise paired with another by `context_lint.py --near-duplicates` (`--duplicates`)

This is a REPORT, not a gate: it always exits 0. The human decides per candidate and one PR effects the
round — the routine, the decision rules and the banner grammar live in the decay runbook.

    python3 .github/scripts/context_decay.py --since 90 [--telemetry report.json] [--duplicates dups.json]

Out of the universe by design: the generated premises index (rebuilt from the premises themselves, so
every H2 of it would read as permanently `zero-load` and the file as permanently unreachable) and the
intent dir — those artifacts live until `Status: implemented` and are reviewed in the PR that
implements them, so their lifecycle is the `Status:` line, not this scan.

Every path, glob and ceiling comes from `docs/agents/skills-config.md` (`skills_config.py`), never from
a constant in here. Python 3 stdlib only; `git` is the one external tool. It imports `context_lint`
from the same directory (the reference checks and the failure-mode entry format have exactly one
implementation).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import context_lint  # noqa: E402
import skills_config  # noqa: E402

# C10 is deliberately absent: a backticked symbol that does not resolve with one of our own suffixes
# is already a FAIL of the linter and never reaches the main branch; what is left of C10 in WARN is
# framework types the identifier index cannot see, by design — noise, not staleness.
DECAY_CODES = {"C6", "C7", "C8", "C9", "C13", "C14"}
BANNER_RE = re.compile(r"^>\s*\*\*(\d{4}-\d{2}-\d{2}):?\*\*:?\s*(superseded|kept)\b(.*)$")
DATED_NAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
BANNER_WINDOW = 15
RENAME_BRACE_RE = re.compile(r"^(.*)\{(.*) => (.*)\}(.*)$")
TELEMETRY_VERSIONS = (1, 2, 3)
# The classes of `agent_telemetry.py commits` that name a premise as a PROBLEM, and nothing else:
# `working`/`redundant`/`decay-candidate`/`watch`/`unclassified` are states, not queues.
QUALITY_CLASSES = ("confusing", "undiscoverable")
PREMISE_ID_RE = re.compile(r"^\*\*Id:\*\*\s*`?(p-[A-Za-z0-9]+)`?")


def git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, check=False
    ).stdout


# ── surfaces, ages and citations ──────────────────────────────────────────────────────────────────


def surface_roots(config: skills_config.Config) -> list[str]:
    """The same universe context-lint gates, root surfaces included: a broken reference or a supersession
    banner in the instructions file is a decay candidate like any other (D4/D5/D6 simply never match it)."""
    roots = [config.docs_root, config.rules_dir, config.skills_dir, *config.root_surfaces]
    return [r for r in roots if r]


def existing_roots(repo: str, roots: list[str]) -> list[str]:
    """git treats a pathspec matching nothing as an error and drops the whole invocation — filter first."""
    return [r for r in roots if os.path.exists(os.path.join(repo, r))]


def list_surfaces(repo: str, config: skills_config.Config) -> list[str]:
    roots = existing_roots(repo, surface_roots(config))
    if not roots:
        return []
    out = git(repo, "ls-files", "-z", *roots)
    intent = config.intent_dir.rstrip("/") + "/" if config.intent_dir else None
    found = []
    for path in out.split("\0"):
        if not path.endswith(".md") or config.is_excluded(path):
            continue
        if intent and path.startswith(intent):
            continue
        found.append(path)
    return sorted(found)


def read(repo: str, path: str) -> str:
    try:
        with open(os.path.join(repo, path), encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def resolve_rename(spec: str) -> tuple[str, str]:
    """`a/{old => new}/c.md` and `old.md => new.md` both name two paths; anything else names one."""
    match = RENAME_BRACE_RE.match(spec)
    if match:
        prefix, old, new, suffix = match.groups()
        return re.sub(r"//+", "/", prefix + old + suffix), re.sub(r"//+", "/", prefix + new + suffix)
    if " => " in spec:
        old, new = spec.split(" => ", 1)
        return old.strip(), new.strip()
    return spec, spec


def weighted_median(samples: list[tuple[int, int]]) -> int:
    """Commit timestamp at the 50th percentile of ADDED LINES — the age of the content, not of the file.

    Last-commit date measures the last sweep (a repo-wide reorg touches every surface at once); `git
    blame` per file measures the same thing as this and costs ~100x more."""
    ordered = sorted(samples)
    total = sum(weight for _, weight in ordered)
    if not ordered:
        return 0
    if total == 0:
        return ordered[-1][0]
    seen = 0
    for stamp, weight in ordered:
        seen += weight
        if seen * 2 >= total:
            return stamp
    return ordered[-1][0]


def content_ages(repo: str, config: skills_config.Config) -> dict[str, tuple[int, int]]:
    """path -> (added-line-weighted median commit time, last commit time), from ONE `git log --numstat`."""
    roots = existing_roots(repo, surface_roots(config))
    if not roots:
        return {}
    out = git(repo, "log", "-M", "--numstat", "--format=@%ct", "--", *roots)
    samples: dict[str, list[tuple[int, int]]] = {}
    last: dict[str, int] = {}
    alias: dict[str, str] = {}
    stamp = 0
    for line in out.split("\n"):
        if line.startswith("@"):
            stamp = int(line[1:] or 0)
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, _deleted, spec = parts
        old, new = resolve_rename(spec)
        key = alias.get(new, new)
        if old != new:
            alias[old] = key
        if added == "-":
            continue
        samples.setdefault(key, []).append((stamp, int(added)))
        last[key] = max(last.get(key, 0), stamp)
    return {path: (weighted_median(values), last.get(path, 0)) for path, values in samples.items()}


def cited_by(repo: str, path: str, config: skills_config.Config) -> list[str]:
    roots = existing_roots(repo, citer_roots(config))
    if not roots:
        return []
    out = git(repo, "grep", "-l", "-F", "--", path, "--", *roots)
    return sorted(p for p in out.split("\n") if p and p != path)


def citer_roots(config: skills_config.Config) -> list[str]:
    """Where a citation can come from: the docs tree, the agent-context tree and the root surfaces."""
    roots = [config.docs_root]
    for directory in (config.rules_dir, config.skills_dir):
        parent = os.path.dirname(directory.rstrip("/"))
        roots.append(parent or directory)
    roots += list(config.root_surfaces)
    seen: list[str] = []
    for root in roots:
        if root and root not in seen:
            seen.append(root)
    return seen


# ── signals ───────────────────────────────────────────────────────────────────────────────────────


def banners(text: str) -> list[tuple[str, str, str]]:
    found = []
    for line in text.split("\n")[:BANNER_WINDOW]:
        match = BANNER_RE.match(line)
        if match:
            found.append((match.group(1), match.group(2), summarize(match.group(3).strip(" —-"))))
    return found


def summarize(text: str, width: int = 90) -> str:
    """One table cell holds a pointer, not the whole banner."""
    return text if len(text) <= width else text[: width - 1].rstrip() + "…"


def lint_findings(repo: str, config: skills_config.Config) -> dict[str, list[context_lint.Finding]]:
    result = context_lint.run(repo, None, config)
    by_path: dict[str, list[context_lint.Finding]] = {}
    for finding in result.findings:
        if finding.code in DECAY_CODES:
            by_path.setdefault(finding.path, []).append(finding)
    return by_path


def dead_globs(repo: str, rule_path: str, files: list[str]) -> tuple[list[str], int]:
    """(globs matching nothing, total globs). A rule without `paths:` is always-on — not evaluated."""
    data, error = context_lint.parse_frontmatter(read(repo, rule_path))
    if error is not None or data is None:
        return [], 0
    paths = data.get("paths")
    if not isinstance(paths, list) or not paths:
        return [], 0
    dead = [glob for glob in paths if not any(skills_config.glob_match(f, glob) for f in files)]
    return dead, len(paths)


def link_targets(repo: str, path: str, doc_span_re: re.Pattern[str]) -> set[str]:
    doc = context_lint.parse_doc(path, read(repo, path))
    base = os.path.dirname(path)
    targets: set[str] = set()
    for match in context_lint.LINK_RE.finditer(doc.clean_text):
        target = match.group(2)
        if "://" in target or target.startswith(("#", "/", "mailto:")):
            continue
        targets.add(os.path.normpath(os.path.join(base, target)).replace(os.sep, "/"))
    for span in doc.spans:
        text = span.text.strip()
        if doc_span_re.match(text):
            targets.add(text)
    return targets


def unreachable_docs(repo: str, files: list[str], config: skills_config.Config) -> set[str]:
    docs_root = config.docs_root.rstrip("/") + "/"
    docs = {p for p in files if p.startswith(docs_root) and p.endswith(".md")}
    start = config.docs_index
    if start not in docs:
        return set()
    doc_span_re = re.compile(r"^" + re.escape(docs_root) + r"[A-Za-z0-9_./-]+\.md$")
    seen = {start}
    frontier = [start]
    while frontier:
        following: list[str] = []
        for path in frontier:
            for target in link_targets(repo, path, doc_span_re):
                if target in docs and target not in seen:
                    seen.add(target)
                    following.append(target)
        frontier = following
    return docs - seen


# ── telemetry (schema_version 1 or 2) ─────────────────────────────────────────────────────────────


@dataclass
class Telemetry:
    version: int
    sessions: int
    surfaces: dict[str, dict]
    premises: dict[tuple[str, str], dict]
    premises_by_id: dict[str, dict]
    quality_by_id: dict[str, dict]  # v3 only: the `commits` section, one row per premise id


def telemetry(path: str | None) -> tuple[Telemetry | None, str]:
    if not path:
        return None, "absent"
    if not os.path.exists(path):
        return None, f"{path} (missing — D3 not evaluated)"
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as error:
        return None, f"{path} (unreadable: {error} — D3 not evaluated)"
    version = payload.get("schema_version")
    if version not in TELEMETRY_VERSIONS:
        return None, f"{path} (schema_version {version!r} outside {TELEMETRY_VERSIONS} — D3 not evaluated)"
    surfaces = {row["path"]: row for row in payload.get("surfaces", []) if row.get("path")}
    rows = [row for row in payload.get("premises", []) if row.get("path")]
    # `id` (`**Id:** p-xxxxxxxx`) is the stable identity of a premise — a title is edited, an id is not.
    # It is absent in the v2 reports written before the ids existed, hence the (path, title) fallback.
    premises = {(row["path"], row["title"]): row for row in rows if row.get("title")}
    premises_by_id = {row["id"]: row for row in rows if row.get("id")}
    sessions = int((payload.get("window") or {}).get("sessions") or 0)
    quality = {
        row["id"]: row
        for row in ((payload.get("commits") or {}).get("premises") or [])
        if row.get("id")
    }
    return Telemetry(int(version), sessions, surfaces, premises, premises_by_id, quality), path


def duplicates(path: str | None) -> tuple[list | None, str]:
    """The pairs of `context_lint.py --near-duplicates --format json`, plus the note.

    `None` means D9 was NOT EVALUATED (no file, missing, unreadable); `[]` means it WAS evaluated and
    the tree has no pair over the threshold — the plausible, desirable outcome. Conflating the two
    would print "D9 not evaluated" next to a summary line naming the file that was read, which is the
    same distinction `telemetry()` draws with `None` vs. an empty report.
    """
    if not path:
        return None, "absent"
    if not os.path.exists(path):
        return None, f"{path} (missing — D9 not evaluated)"
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as error:
        return None, f"{path} (unreadable: {error} — D9 not evaluated)"
    pairs = [
        pair
        for pair in (payload.get("pairs") or [])
        if isinstance(pair, dict) and (pair.get("a") or {}).get("path") and (pair.get("b") or {}).get("path")
    ]
    return pairs, path


# ── failure-mode entries (D7) ─────────────────────────────────────────────────────────────────────


def failure_mode_entries(text: str, config: skills_config.Config) -> list[tuple[str, int, str]]:
    """(id, occurrences, state) per entry, parsed with the same regexes C16 enforces."""
    doc = context_lint.parse_doc(config.failure_modes, text)
    entries = []
    for entry_id, _heading, body in context_lint.failure_mode_entries(doc):
        for _line, line_text in body:
            match = context_lint.OCCURRENCES_STATE_RE.match(line_text)
            if match:
                entries.append((entry_id, int(match.group(1)), match.group(2)))
                break
    return entries


# ── scan ──────────────────────────────────────────────────────────────────────────────────────────


DEAD_GLOB_ACTION = "remove the dead glob"
DEAD_RULE_ACTION = "rule never loads: delete or fix paths:"


@dataclass
class Candidate:
    path: str
    label: str
    signals: list[str] = field(default_factory=list)
    evidence: dict[str, str] = field(default_factory=dict)  # per signal, what the table shows in "signals"
    actions: dict[str, str] = field(default_factory=dict)  # per signal, the phrasing the "action" column uses
    content_age: int | None = None
    last_commit: int | None = None
    loads: str = "n/a"
    citers: list[str] = field(default_factory=list)
    stable: list[str] = field(default_factory=list)  # citers that argue for keeping it — computed at scan time
    action: str = ""


@dataclass
class Report:
    candidates: list[Candidate] = field(default_factory=list)
    promotions: list[tuple[str, int, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    since_days: int = 90
    telemetry_note: str = "absent"
    duplicates_note: str = "absent"
    docs_index: str = "docs/index.md"


def days_since(stamp: int | None, today: dt.date) -> int | None:
    if not stamp:
        return None
    return (today - dt.datetime.fromtimestamp(stamp).date()).days


def stable_citers(citers: list[str], config: skills_config.Config) -> list[str]:
    """A citation only argues for keeping a doc when it comes from a live surface: an ephemeral doc is
    dated by definition and an excluded path is generated per branch (neither is a context-lint surface)."""
    return [c for c in citers if not config.is_ephemeral(c) and not config.is_excluded(c)]


def suggest_action(candidate: Candidate, config: skills_config.Config) -> str:
    signals = set(candidate.signals)
    if "D1" in signals:
        target = candidate.evidence.get("D1", "")
        return f"delete (facts in: {target})" if target else "delete"
    # A partially dead glob is a frontmatter fix, not a deletion — it comes before the citation rule,
    # which exists to stop a cited doc from being DELETED.
    if candidate.actions.get("D5") == DEAD_GLOB_ACTION:
        return DEAD_GLOB_ACTION
    # D8 and D9 are there for the same reason: rewriting a confusing premise and merging two
    # near-identical ones are not deletions, so being cited by a stable doc is no argument against them.
    if "D8" in signals:
        return candidate.actions["D8"]
    if "D9" in signals:
        return candidate.actions["D9"]
    stable = candidate.stable
    if stable:
        return f"keep (evidence cited by {len(stable)} stable doc(s))"
    if "D4" in signals:
        return "finish: distil + banner, or delete"
    if "D5" in signals:
        return candidate.actions.get("D5", DEAD_GLOB_ACTION)
    if "D6" in signals:
        return f"index it in {config.docs_index} or delete"
    if "D3" in signals:
        return "zero loads: check paths:/index, else delete"
    if "D2" in signals:
        return "fix or delete the reference"
    return "decide"


def scan(
    repo: str,
    since_days: int,
    telemetry_path: str | None,
    today: dt.date,
    config: skills_config.Config | None = None,
    duplicates_path: str | None = None,
) -> Report:
    repo = os.path.abspath(repo)
    config = config or skills_config.load(repo)
    report = Report(since_days=since_days, docs_index=config.docs_index)
    surfaces = list_surfaces(repo, config)
    tracked = [p for p in git(repo, "ls-files", "-z").split("\0") if p]
    ages = content_ages(repo, config)
    findings = lint_findings(repo, config)
    unreachable = unreachable_docs(repo, surfaces, config)
    telemetry_data, report.telemetry_note = telemetry(telemetry_path)
    if telemetry_data is None:
        report.notes.append("D3 not evaluated (no telemetry report)")
    elif telemetry_data.version < 2:
        report.notes.append("per-premise D3 not evaluated (schema_version 1 report)")
    if telemetry_data is not None and telemetry_data.version < 3:
        report.notes.append(
            f"D8 not evaluated (schema_version {telemetry_data.version} report, no `commits` section)"
        )
    duplicate_pairs, report.duplicates_note = duplicates(duplicates_path)
    if duplicate_pairs is None:
        report.notes.append("D9 not evaluated (no near-duplicates report)")
        duplicate_pairs = []

    by_path: dict[str, Candidate] = {}

    def candidate_for(path: str, label: str | None = None) -> Candidate:
        key = label or path
        if key not in by_path:
            median, last = ages.get(path, (0, 0))
            by_path[key] = Candidate(
                path=path,
                label=key,
                content_age=days_since(median, today),
                last_commit=days_since(last, today),
            )
        return by_path[key]

    for path in surfaces:
        if config.is_premises_index(path):
            continue
        text = read(repo, path)
        marks = banners(text)
        superseded = [b for b in marks if b[1] == "superseded"]
        if superseded:
            entry = candidate_for(path)
            entry.signals.append("D1")
            entry.evidence["D1"] = superseded[-1][2] or "no destination declared"

        if path in findings:
            entry = candidate_for(path)
            entry.signals.append("D2")
            entry.evidence["D2"] = ", ".join(sorted({f.code for f in findings[path]}))

        if config.is_ephemeral(path) and not superseded:
            effective = max([b[0] for b in marks], default=None)
            if effective is None:
                name = DATED_NAME_RE.search(os.path.basename(path))
                effective = name.group(1) if name else None
            if effective is not None:
                age = (today - dt.date.fromisoformat(effective)).days
                if age > since_days:
                    entry = candidate_for(path)
                    entry.signals.append("D4")
                    entry.evidence["D4"] = f"effective date {effective} ({age}d)"

        if context_lint.Surfaces(config).is_rule(path):
            dead, total = dead_globs(repo, path, tracked)
            if dead:
                entry = candidate_for(path)
                entry.signals.append("D5")
                entry.evidence["D5"] = ", ".join(dead)
                entry.actions["D5"] = DEAD_RULE_ACTION if len(dead) == total else DEAD_GLOB_ACTION

        if path in unreachable:
            entry = candidate_for(path)
            entry.signals.append("D6")
            entry.evidence["D6"] = f"not reachable from {config.docs_index}"

        if telemetry_data is not None:
            row = telemetry_data.surfaces.get(path)
            if row is None:
                pass  # outside the telemetry universe (an ephemeral doc, e.g.) — "not covered", not D3
            else:
                loads = int(row.get("loads") or 0)
                if row.get("candidate") == "zero-load":
                    entry = candidate_for(path)
                    entry.signals.append("D3")
                    entry.evidence["D3"] = "zero loads in the window"
                    entry.loads = str(loads)
                elif path in by_path:
                    by_path[path].loads = str(loads)

    premises = live_premises(repo, surfaces, config)
    if telemetry_data is not None and telemetry_data.version >= 2:
        add_premise_candidates(premises, telemetry_data, candidate_for)
    if telemetry_data is not None and telemetry_data.version >= 3:
        add_quality_candidates(premises, telemetry_data, candidate_for)
    add_duplicate_candidates(premises, duplicate_pairs, candidate_for)

    for candidate in by_path.values():
        candidate.citers = cited_by(repo, candidate.path, config)
        candidate.stable = stable_citers(candidate.citers, config)
        candidate.action = suggest_action(candidate, config)

    report.candidates = sorted(by_path.values(), key=lambda c: (-len(c.signals), -(c.content_age or 0), c.label))

    modes = read(repo, config.failure_modes)
    if modes:
        for entry_id, occurrences, state in failure_mode_entries(modes, config):
            if occurrences >= 2 and state == "advisory":
                report.promotions.append((entry_id, occurrences, state))
    return report


def live_premises(repo: str, surfaces: list[str], config: skills_config.Config) -> list:
    """Every premise the tree carries today — the join target of the per-premise signals D3, D8 and D9."""
    lint_surfaces = context_lint.Surfaces(config)
    docs = {
        path: context_lint.parse_doc(path, read(repo, path))
        for path in surfaces
        if lint_surfaces.is_premises(path) and not config.is_premises_index(path)
    }
    return context_lint.collect_premises(docs, lint_surfaces)


def premise_label(premise: context_lint.Premise) -> str:
    """One label per premise, shared by D3, D8 and D9 so their signals land on the SAME row."""
    return f"{premise.path} › {premise.title}"


def add_premise_candidates(premises: list, data: Telemetry, candidate_for) -> None:
    """D3 per premise: the FILE is loaded and this H2 never is — a section nobody reads inside a doc
    everybody reads. A file with zero loads is already a D3 row; its premises would only repeat it."""
    for premise in premises:
        row = None
        identifier = premise_id(premise)
        if identifier:
            row = data.premises_by_id.get(identifier)
        if row is None:
            row = data.premises.get((premise.path, premise.title))
        if row is None or row.get("candidate") != "zero-load":
            continue
        surface = data.surfaces.get(premise.path)
        if surface is None or int(surface.get("loads") or 0) <= 0:
            continue
        entry = candidate_for(premise.path, premise_label(premise))
        entry.signals.append("D3")
        entry.evidence["D3"] = "premise never loaded inside a file that loads"
        entry.loads = "/".join(
            str(int(row.get(field_name) or 0)) for field_name in ("loads_range", "loads_whole", "loads_fetch")
        )


def add_quality_candidates(premises: list, data: Telemetry, candidate_for) -> None:
    """D8: the commit trailers say this premise was read AND violated twice (`confusing`), or violated
    without ever being read (`undiscoverable`). Both are counts of what happened, never a judgement of
    the text — the action the row carries is the one `agent_telemetry.py` prints for the class."""
    for premise in premises:
        row = data.quality_by_id.get(premise_id(premise) or "")
        if row is None or row.get("class") not in QUALITY_CLASSES:
            continue
        entry = candidate_for(premise.path, premise_label(premise))
        entry.signals.append("D8")
        entry.evidence["D8"] = (
            f"{row['class']} (read {int((row.get('reads') or {}).get('commits') or 0)}x, "
            f"violated {int((row.get('violations') or {}).get('commits') or 0)}x)"
        )
        entry.actions["D8"] = row.get("action") or "rewrite it, or promote it to a gate"


def add_duplicate_candidates(premises: list, pairs: list, candidate_for) -> None:
    """D9: two premises whose rationales are near-identical. ONE candidate per pair, on the first of the
    two — the decision is "merge these", taken once, not twice."""
    by_key = {(premise.path, premise.title): premise for premise in premises}
    for pair in pairs:
        left, right = pair["a"], pair["b"]
        premise = by_key.get((left["path"], left.get("title")))
        if premise is None:  # the pair names a premise the tree no longer carries: nothing to merge
            continue
        entry = candidate_for(premise.path, premise_label(premise))
        entry.signals.append("D9")
        entry.evidence["D9"] = (
            "near-duplicate of %s › %s (%.2f)" % (right["path"], right.get("title") or "—",
                                                  float(pair.get("ratio") or 0.0))
        )
        entry.actions["D9"] = "merge the two into one premise"


def premise_id(premise: context_lint.Premise) -> str | None:
    for line in premise.body:
        match = PREMISE_ID_RE.match(line)
        if match:
            return match.group(1)
    return None


# ── rendering ─────────────────────────────────────────────────────────────────────────────────────


def render(report: Report) -> str:
    lines = [
        "| # | file | signals | content age | last commit | loads | cited by | action |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for position, candidate in enumerate(report.candidates, 1):
        citers = candidate.stable
        cited = f"{len(citers)}: {', '.join(citers[:2])}" + ("…" if len(citers) > 2 else "") if citers else "—"
        signals = " ".join(
            f"{code} ({candidate.evidence[code]})" if code in candidate.evidence else code
            for code in candidate.signals
        )
        lines.append(
            f"| {position} | `{candidate.label}` | {signals} "
            f"| {fmt_days(candidate.content_age)} | {fmt_days(candidate.last_commit)} "
            f"| {candidate.loads} | {cited} | {candidate.action} |"
        )
    if not report.candidates:
        lines.append("| — | no candidate | — | — | — | — | — | — |")

    lines += ["", "| entry | occurrences | state | action |", "|---|---|---|---|"]
    for entry_id, occurrences, state in report.promotions:
        lines.append(f"| {entry_id} | {occurrences} | {state} | gate owed: gate + red case, or `no gate:` |")
    if not report.promotions:
        lines.append("| — | — | — | no promotion due |")

    counts: dict[str, int] = {}
    for candidate in report.candidates:
        for code in candidate.signals:
            counts[code] = counts.get(code, 0) + 1
    breakdown = " · ".join(f"{code} {counts[code]}" for code in sorted(counts)) or "no signal"
    lines += [
        "",
        f"context-decay: {len(report.candidates)} candidate(s) ({breakdown}) · "
        f"{len(report.promotions)} promotion(s) due · window {report.since_days}d · "
        f"telemetry: {report.telemetry_note} · duplicates: {report.duplicates_note}",
    ]
    for note in report.notes:
        lines.append(f"context-decay: {note}")
    return "\n".join(lines)


def fmt_days(value: int | None) -> str:
    return "—" if value is None else f"{value}d"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List decay candidates among the context surfaces.")
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--since", type=int, default=90, help="window in days for the dated signal (D4)")
    parser.add_argument("--telemetry", default=None, help="JSON report of the telemetry collector (optional)")
    parser.add_argument("--duplicates", default=None,
                        help="JSON of `context_lint.py --near-duplicates --format json` (optional, D9)")
    parser.add_argument("--today", default=None, help="ISO date overriding today (deterministic runs)")
    parser.add_argument(
        "--config", default=None, help=f"path to the config (default: <repo>/{skills_config.CONFIG_PATH})"
    )
    args = parser.parse_args(argv)

    config = skills_config.load(args.repo, args.config)
    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    print(render(scan(args.repo, args.since, args.telemetry, today, config, args.duplicates)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
