#!/usr/bin/env python3
"""Per-agent timeline and token usage of a Claude Code Workflow run (``wf_*``).

WHAT IT MEASURES
    One row per Workflow subagent of a run: role, model, start/end, duration,
    assistant turns, tool calls and the four token counters (input, output,
    cache read, cache creation). Per run: wall-clock, agent count and totals.
    With ``--stages`` the agents collapse into the stages of their workflow —
    deep-plan (enumerate, analyze, refute, gate, synthesize) or implement
    (setup, implement, verify, pr). A stage costs the SUM of its agents'
    tokens; its duration is read off its WINDOW (``start_min``/``end_min``,
    minutes from the run start) rather than a per-stage parallel-or-serial
    label, because neither workflow is cleanly one or the other: deep-plan
    fires enumerate and analyze inside a single ``parallel()`` (overlapping
    windows), and implement's verify stage mixes a parallel first pair with
    serial retries (a wide window, a small ``max_min``).

WHY THE JOURNAL IS THE INSTRUMENT
    Per-skill cost telemetry cannot attribute these agents to the skill:
    Workflow subagents arrive tagged as a generic agent type with no skill
    name, so a ``cost.usage{skill.name:deep-plan}`` widget only ever sees the
    main thread while the run journals of the same runs show tens of millions
    of cache-read tokens. The run directory (``journal.jsonl`` +
    ``agent-<id>.jsonl`` + ``agent-<id>.meta.json``) is the only per-run source
    that attributes tokens and wall-clock to a role, which makes it the
    "measure before cutting" tool of docs/workflow-calibration.md.

    Nothing here is repo-specific — the run directory is an argument and the
    stages come from the workflow the run's own prompts declare — so there is
    no skills-config input to read.

USAGE
    wf_timeline.py <wf_dir> [<wf_dir> ...] [--json] [--stages]

    Run directories live under
    ~/.claude/projects/<project>/<session>/subagents/workflows/wf_<id>/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

#: Canonical deep-plan stages, in the order the workflow runs them. Its labels carry the stage as
#: a `stage:role` prefix; the implement stages come from the prefix table below, since its labels
#: do not name their stage.
DEEP_PLAN_STAGES = ("enumerate", "analyze", "refute", "gate", "synthesize")

#: The marker every workflow writes as the first line of an agent's first prompt:
#: ``[<workflow> role: <label>]``.
ROLE_MARKER_RE = re.compile(r"^\s*\[([\w-]+) role:\s*([^\]]+)\]")

#: Legacy fallbacks, for runs recorded before the marker existed: the role file an agent was told
#: to read, and the refuter's round/lens coordinates, both taken from the first user prompt.
ROLE_FILE_RE = re.compile(r"deep-plan/agents/([a-z-]+)\.md")
LENS_RE = re.compile(r"round (\d+).*?LENS #(\d+)", re.S)

#: Wave id inside the JSON block of a pre-marker implement wave prompt.
WAVE_ID_RE = re.compile(r'"id":\s*"?([\w.]+)"?')

#: Legacy deep-plan role-file -> stage. Keeps pre-marker runs comparable with marked ones, so a
#: calibration can diff across the convention change.
LEGACY_STAGE_BY_PREFIX = (
    ("dimension-table", "analyze"),
    ("matrix-columns", "enumerate"),
    ("state-mutation-seams", "enumerate"),
    ("lifecycle-matrix", "analyze"),
    ("test-coverage", "analyze"),
    ("fill", "analyze"),
    ("refute", "refute"),
    ("gate", "gate"),
    ("consolidate", "synthesize"),
)

#: Implement label prefix -> stage. ``recon-fix`` is a wave agent under another name, so it
#: belongs to ``implement``, not to ``verify``.
IMPLEMENT_STAGE_BY_PREFIX = (
    ("setup", "setup"),
    ("wave-", "implement"),
    ("recon-fix", "implement"),
    ("verify", "verify"),
    ("pr-author", "pr"),
)

MODEL_FAMILIES = ("opus", "sonnet", "fable", "haiku")

TOKEN_KEYS = ("input", "output", "cache_read", "cache_create")


def parse_ts(value):
    """Parse an ISO-8601 transcript timestamp, tolerating a trailing ``Z``."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def read_jsonl(path):
    """Yield the objects of a JSONL file, skipping blank/malformed lines."""
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def message_text(message):
    """Flatten a transcript message's content into plain text."""
    content = (message or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def first_user_text(messages):
    """Return the text of the first user message of a transcript."""
    for entry in messages:
        if entry.get("type") == "user":
            text = message_text(entry.get("message"))
            if text:
                return text
    return ""


def legacy_implement_label(prompt):
    """Derive an implement label from a prompt written before the marker.

    Each branch matches the sentence one prompt builder of ``implement.js`` opens with, so runs
    measured before the marker stay comparable.
    """
    if "You are the SETUP agent" in prompt:
        return "setup"
    if "verify-plan findings below" in prompt:
        return "recon-fix"
    if "Your wave:" in prompt:
        match = WAVE_ID_RE.search(prompt[prompt.index("Your wave:"):])
        return "wave-{}".format(match.group(1)) if match else "wave"
    if "VERIFY-PLAN agent (RE-CHECK" in prompt:
        return "verify-plan-recheck"
    if "VERIFY-PLAN agent" in prompt:
        return "verify-plan"
    if "You are the VERIFY agent" in prompt:
        return "verify"
    if "pr-author.md" in prompt:
        return "pr-author"
    return None


def legacy_label(prompt):
    """Derive a deep-plan role label from a pre-marker prompt."""
    if "deep-plan RESOLVER" in prompt:
        if "Complete the draft" in prompt:
            return "fill (resolver)"
        if "Integrate the refutations" in prompt:
            return "refute:resolve"
        if "The gate found" in prompt:
            return "gate:justify"
        return "resolver:?"
    match = ROLE_FILE_RE.search(prompt)
    if not match:
        return None
    name = match.group(1)
    if name == "refuter":
        lens = LENS_RE.search(prompt)
        return "refute r{}-{}".format(lens.group(1), lens.group(2)) if lens else "refute"
    return name


def role_of(prompt):
    """Return ``(workflow, label, stage)`` for an agent, from its first prompt.

    The ``[<workflow> role: <label>]`` marker wins; otherwise the legacy heuristics do, and an
    unrecognized prompt keeps its first line as the label.
    """
    prompt = prompt or ""
    marker = ROLE_MARKER_RE.match(prompt)
    if marker:
        workflow = marker.group(1).strip()
        label = marker.group(2).strip()
        return workflow, label, stage_of(workflow, label)
    label = legacy_label(prompt)
    if label:
        return "deep-plan", label, stage_of("deep-plan", label)
    label = legacy_implement_label(prompt)
    if label:
        return "implement", label, stage_of("implement", label)
    head = prompt.strip().splitlines()[0] if prompt.strip() else "?"
    return "unknown", ((head[:44] + "…") if len(head) > 44 else head), "other"


def stage_of(workflow, label):
    """Map a role label onto one of its workflow's stages (or ``other``)."""
    if workflow == "implement":
        for prefix, stage in IMPLEMENT_STAGE_BY_PREFIX:
            if label.startswith(prefix):
                return stage
        return "other"
    prefix = label.split(":", 1)[0].strip()
    if prefix in DEEP_PLAN_STAGES:
        return prefix
    for legacy_prefix, stage in LEGACY_STAGE_BY_PREFIX:
        if label.startswith(legacy_prefix):
            return stage
    return "other"


def short_model(model):
    """Shorten a full model id to its family name."""
    if not model:
        return "?"
    for family in MODEL_FAMILIES:
        if family in model:
            return family
    return model


def read_meta(agent_path):
    """Read ``agent-<id>.meta.json``; an absent/broken file yields ``{}``."""
    meta_path = agent_path.with_name(agent_path.stem + ".meta.json")
    try:
        with open(meta_path, encoding="utf-8") as handle:
            meta = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return meta if isinstance(meta, dict) else {}


def analyze_agent(agent_path):
    """Summarize one agent transcript into a row of the timeline."""
    messages = list(read_jsonl(agent_path))
    meta = read_meta(agent_path)
    stamps = [
        stamp
        for stamp in (parse_ts(entry.get("timestamp")) for entry in messages)
        if stamp is not None
    ]
    agent_id = agent_path.stem
    if agent_id.startswith("agent-"):
        agent_id = agent_id[len("agent-"):]
    row = {
        "agent_id": agent_id,
        "agent_type": meta.get("agentType"),
        "spawn_depth": meta.get("spawnDepth"),
        "turns": 0,
        "tools": 0,
    }
    row.update({key: 0 for key in TOKEN_KEYS})
    model = None
    for entry in messages:
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message") or {}
        usage = message.get("usage") or {}
        model = model or message.get("model")
        row["turns"] += 1
        row["input"] += usage.get("input_tokens", 0) or 0
        row["output"] += usage.get("output_tokens", 0) or 0
        row["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
        row["cache_create"] += usage.get("cache_creation_input_tokens", 0) or 0
        for part in message.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "tool_use":
                row["tools"] += 1
    workflow, label, stage = role_of(first_user_text(messages))
    row["workflow"] = workflow
    row["role"] = label
    row["stage"] = stage
    row["model"] = short_model(model or meta.get("model"))
    row["start"] = min(stamps) if stamps else None
    row["end"] = max(stamps) if stamps else None
    row["minutes"] = (row["end"] - row["start"]).total_seconds() / 60 if stamps else 0.0
    return row


def read_journal(wf_dir):
    """Count the journal's ``started``/``result``/``failed`` entries.

    An agent started but neither resolved nor failed is still in flight — that is how a partial
    run is told apart from a finished one.
    """
    entries = list(read_jsonl(wf_dir / "journal.jsonl"))
    if not entries:
        return None
    started = sum(1 for entry in entries if entry.get("type") == "started")
    done = sum(1 for entry in entries if entry.get("type") == "result")
    failed = sum(1 for entry in entries if entry.get("type") == "failed")
    return {
        "started": started,
        "result": done,
        "failed": failed,
        "in_flight": started - done - failed,
    }


def offset_minutes(stamp, origin):
    """Minutes from the run start; ``None`` when either stamp is missing."""
    if stamp is None or origin is None:
        return None
    return (stamp - origin).total_seconds() / 60


def stage_rows(agents, run_start):
    """Collapse agents into stages, ordered by the start of their window.

    Each row carries the stage's window relative to the run start (``start_min``/``end_min``).
    Overlapping windows are stages that ran concurrently; a window much wider than
    ``max_minutes`` is a stage whose agents ran in series. Reading the shape off the timestamps
    is what a per-stage parallel-or-serial label cannot do: deep-plan's enumerate and analyze
    share one ``parallel()``, and implement's verify stage holds both a parallel pair and its
    serial retries.
    """
    grouped = {}
    for agent in agents:
        grouped.setdefault(agent["stage"], []).append(agent)
    rows = []
    for stage, members in grouped.items():
        starts = [a["start"] for a in members if a["start"]]
        ends = [a["end"] for a in members if a["end"]]
        start = min(starts) if starts else None
        end = max(ends) if ends else None
        row = {
            "stage": stage,
            "agents": len(members),
            "max_minutes": max(a["minutes"] for a in members),
            "sum_minutes": sum(a["minutes"] for a in members),
            "start": start,
            "end": end,
            "start_min": offset_minutes(start, run_start),
            "end_min": offset_minutes(end, run_start),
            "turns": sum(a["turns"] for a in members),
            "tools": sum(a["tools"] for a in members),
        }
        row.update({key: sum(a[key] for a in members) for key in TOKEN_KEYS})
        rows.append(row)
    rows.sort(key=lambda r: (r["start_min"] is None, r["start_min"] or 0.0))
    return rows


def workflow_of(agents):
    """The run's workflow: the one most of its agents were labelled with."""
    seen = Counter(a["workflow"] for a in agents if a["workflow"] != "unknown")
    return seen.most_common(1)[0][0] if seen else "unknown"


def analyze_run(wf_dir):
    """Build the full summary of one ``wf_*`` run directory."""
    paths = sorted(wf_dir.glob("agent-*.jsonl"))
    agents = [analyze_agent(path) for path in paths]
    agents.sort(key=lambda a: (a["start"] is None, a["start"] or datetime.min))
    starts = [a["start"] for a in agents if a["start"]]
    ends = [a["end"] for a in agents if a["end"]]
    run_start = min(starts) if starts else None
    totals = {key: sum(a[key] for a in agents) for key in TOKEN_KEYS}
    totals["turns"] = sum(a["turns"] for a in agents)
    totals["tools"] = sum(a["tools"] for a in agents)
    return {
        "run": wf_dir.name,
        "path": str(wf_dir),
        "workflow": workflow_of(agents),
        "agents": agents,
        "agent_count": len(agents),
        "start": run_start,
        "end": max(ends) if ends else None,
        "wall_minutes": (
            (max(ends) - min(starts)).total_seconds() / 60 if starts and ends else 0.0
        ),
        "totals": totals,
        "stages": stage_rows(agents, run_start),
        "journal": read_journal(wf_dir),
    }


def clock(stamp):
    return stamp.strftime("%H:%M:%S") if stamp else "-"


def minutes(value):
    """Format a window bound; a stage whose agents carry no stamps has none."""
    return "-" if value is None else "{:.1f}".format(value)


def print_header(run):
    journal = run["journal"]
    notes = []
    if journal is None:
        notes.append("no journal.jsonl")
    else:
        if journal["in_flight"] > 0:
            notes.append("IN PROGRESS ({} agent(s) running)".format(journal["in_flight"]))
        if journal["failed"] > 0:
            notes.append("{} agent(s) failed".format(journal["failed"]))
    note = "  [{}]".format("; ".join(notes)) if notes else ""
    print(
        "\n=== {}  workflow={}  agents={}  wall={:.1f} min{}".format(
            run["run"], run["workflow"], run["agent_count"], run["wall_minutes"], note
        )
    )


def print_table(run):
    """Print the per-agent timeline of a run."""
    print_header(run)
    print(
        f"{'role':<26}{'model':<8}{'start':<9}{'end':<9}{'min':>6}"
        f"{'turns':>6}{'tools':>6}{'in':>10}{'out':>9}"
        f"{'cache_rd':>13}{'cache_cr':>12}"
    )
    for agent in run["agents"]:
        print(
            f"{agent['role'][:25]:<26}{agent['model'][:7]:<8}"
            f"{clock(agent['start']):<9}{clock(agent['end']):<9}"
            f"{agent['minutes']:>6.1f}{agent['turns']:>6}{agent['tools']:>6}"
            f"{agent['input']:>10,}{agent['output']:>9,}"
            f"{agent['cache_read']:>13,}{agent['cache_create']:>12,}"
        )
    totals = run["totals"]
    print(
        f"{'TOTAL':<26}{'':<8}{'':<9}{'':<9}{'':>6}"
        f"{totals['turns']:>6}{totals['tools']:>6}"
        f"{totals['input']:>10,}{totals['output']:>9,}"
        f"{totals['cache_read']:>13,}{totals['cache_create']:>12,}"
    )


def print_stages(run):
    """Print the per-stage roll-up of a run, each stage with its window."""
    print_header(run)
    print(
        f"{'stage':<14}{'agents':>7}{'start_min':>10}{'end_min':>9}"
        f"{'max_min':>9}{'sum_min':>9}"
        f"{'turns':>7}{'tools':>7}{'in':>10}{'out':>9}"
        f"{'cache_rd':>13}{'cache_cr':>12}"
    )
    for row in run["stages"]:
        print(
            f"{row['stage']:<14}{row['agents']:>7}"
            f"{minutes(row['start_min']):>10}{minutes(row['end_min']):>9}"
            f"{row['max_minutes']:>9.1f}{row['sum_minutes']:>9.1f}"
            f"{row['turns']:>7}{row['tools']:>7}"
            f"{row['input']:>10,}{row['output']:>9,}"
            f"{row['cache_read']:>13,}{row['cache_create']:>12,}"
        )
    print("wall: {:.1f} min".format(run["wall_minutes"]))
    print(
        "  wall = run end - run start; each stage's window shows what ran in "
        "parallel (overlapping windows) and what ran in series (a wide window "
        "with a small max_min)."
    )


def jsonable(value):
    """Render datetimes as ISO strings so a run can be dumped to JSON."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="wf_timeline.py",
        description="Per-agent timeline and token usage of Claude Code Workflow runs.",
    )
    parser.add_argument("wf_dir", nargs="+", help="wf_<id> run directory")
    parser.add_argument("--json", action="store_true", help="emit one JSON object per run")
    parser.add_argument(
        "--stages",
        action="store_true",
        help="collapse agents into stages and print each stage's window",
    )
    args = parser.parse_args(argv)

    status = 0
    for raw in args.wf_dir:
        wf_dir = Path(raw).expanduser()
        if not wf_dir.is_dir():
            print("not a directory: {}".format(wf_dir), file=sys.stderr)
            status = 1
            continue
        run = analyze_run(wf_dir)
        if not run["agents"]:
            print("\n=== {}: no agent transcripts".format(run["run"]), file=sys.stderr)
            status = 1
            continue
        if args.json:
            print(json.dumps(jsonable(run), ensure_ascii=False))
        elif args.stages:
            print_stages(run)
        else:
            print_table(run)
    return status


if __name__ == "__main__":
    sys.exit(main())
