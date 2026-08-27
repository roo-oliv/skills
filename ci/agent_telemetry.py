#!/usr/bin/env python3
"""Log WHICH instruction surfaces a Claude Code session loaded, and report on the window.

Two subcommands, one file:

  hook    reads a hook payload on stdin and appends ONE json line to .claude/telemetry/loads-<UTC date>.jsonl.
          Wired to InstructionsLoaded (every load_reason) and to PostToolUse on Read|Skill|Bash in
          .claude/settings.json. Fail-open by contract: it never writes to stdout and always exits 0 —
          a broken logger must never break a session.

  report  aggregates those files over a window: loads, distinct sessions, session share, bytes on disk,
          bytes loaded, reasons, last load, and a decay/demotion verdict — plus, for premises files, the
          same count PER PREMISE, and a histogram of distinct sessions per effort level.

Line format (one per load, all thirteen keys always present):

  {"agent_type": null, "bytes": 6947, "effort": "high", "event": "InstructionsLoaded",
   "memory_type": "Project", "path": "CLAUDE.md", "premise": null, "premise_id": null, "read": null,
   "reason": "session_start", "session_id": "...", "trigger": null,
   "ts": "2026-08-27T18:20:31.004Z"}

`path` is relative to the repository root when the file lives inside it, and absolute with `~` for the
home directory otherwise (user memory). `bytes` is the file size at load time, `null` when unreadable.
`effort` is the reasoning level the session runs at, `null` when the payload carries none or carries one
outside the grammar — the local counterpart of the `effort` attribute the OTel metrics
`claude_code.token.usage`/`cost.usage` already carry on their own.
`premise`/`premise_id`/`read` are null on every line but the extra ones a premise load adds: one per
premise the Read's `offset`/`limit` window touched (`read` being `range` or `whole`), or one per id
fetched through the configured premise fetch command in a Bash call (`read` being `fetch`).

    echo '<payload>' | python3 .github/scripts/agent_telemetry.py hook
    python3 .github/scripts/agent_telemetry.py report --since 2026-08-20 .claude/telemetry
    python3 .github/scripts/agent_telemetry.py report --format json ~/work/*/.claude/telemetry

The JSON output (`schema_version` 2) is the contract `context_decay.py --telemetry` reads to decide what
decayed. Nothing here is stack-specific: which paths are surfaces, where premises live and how a premise
is fetched all come from `docs/agents/skills-config.md` (see `skills_config.py`). Python 3 stdlib only,
3.9-compatible — CI may have 3.12 but the machines emitting these logs may not.
"""

from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import premise  # noqa: E402
import skills_config  # noqa: E402

LOG_DIR = ".claude/telemetry"
LOW_LOAD_SESSION_SHARE = 0.05
MIN_SESSIONS_FOR_LOW_LOAD = 20
SCHEMA_VERSION = 2
DEFAULT_WINDOW_DAYS = 14

# The reasoning levels a session can run at. The OTel metrics carry `effort` on their own; this is the
# same axis on the local log, so a load can be read against what it cost.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
EFFORT_ENV = "CLAUDE_EFFORT"
EFFORT_UNKNOWN = "unknown"
INTERPRETERS = ("python", "python3", "node", "ruby", "perl", "bash", "sh")
SHELL_OPERATORS = ("&&", "||", "|", ";", "&", ">", ">>", "<")

INSTRUCTIONS = "instructions"
README = "readme"
RULE_ALWAYS_ON = "rule-always-on"
RULE_SCOPED = "rule-scoped"
SKILL = "skill"
DOC = "doc"
EXTERNAL = "external"
# A surface Claude cannot stop loading is never a decay candidate: cutting it is a different decision.
CANDIDATE_KINDS = (RULE_SCOPED, SKILL, DOC)


# ── hook ──────────────────────────────────────────────────────────────────────────────────────────


def project_root(payload: dict) -> str:
    """The repository the session runs in: the harness variable, the payload cwd, then this process."""
    for candidate in (os.environ.get("CLAUDE_PROJECT_DIR"), payload.get("cwd")):
        if candidate:
            return os.path.abspath(candidate)
    return os.getcwd()


def relative_to(path: str, root: str) -> str | None:
    """The path relative to root, or None when it lives outside it."""
    if not path:
        return None
    absolute = os.path.abspath(path)
    try:
        if os.path.commonpath([absolute, root]) != root:
            return None
    except ValueError:  # different drives on Windows, or a mix of absolute and relative
        return None
    return os.path.relpath(absolute, root).replace(os.sep, "/")


def normalize_path(path: str, root: str) -> str:
    """Repo-relative when inside the repo; otherwise absolute with the home directory as `~`."""
    inside = relative_to(path, root)
    if inside is not None:
        return inside
    absolute = os.path.abspath(path)
    home = os.path.expanduser("~")
    if absolute == home or absolute.startswith(home + os.sep):
        return "~" + absolute[len(home) :].replace(os.sep, "/")
    return absolute.replace(os.sep, "/")


def file_size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def timestamp(now: datetime.datetime) -> str:
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)


def effort_level(payload: dict) -> str | None:
    """The reasoning level of the session — the payload's `effort.level`, else `$CLAUDE_EFFORT`.

    Only the five levels of the grammar are logged. A value outside it is a level this build does not
    know (a newer CLI, a typo in the env): recording it would invent a bucket in the histogram, so it
    logs as `null` — the same as a build that emits no effort at all.
    """
    effort = payload.get("effort")
    candidate = effort.get("level") if isinstance(effort, dict) else None
    if not candidate:
        candidate = os.environ.get(EFFORT_ENV)
    return candidate if candidate in EFFORT_LEVELS else None


def surface_prefixes(config: skills_config.Config) -> tuple:
    """The path prefixes a Read has to land on to count as an instruction load."""
    return (
        config.rules_dir.rstrip("/") + "/",
        config.skills_dir.rstrip("/") + "/",
        config.docs_root.rstrip("/") + "/",
    )


def is_surface_read(path: str, config: skills_config.Config) -> bool:
    if path in config.root_surfaces:
        return True
    if config.is_excluded(path):
        return False
    return path.startswith(surface_prefixes(config))


def build_record(payload: dict, root: str, config: skills_config.Config, now: datetime.datetime) -> dict | None:
    """One log line, or None when the event carries no instruction load worth recording."""
    event = payload.get("hook_event_name")
    memory_type = None
    trigger = None

    if event == "InstructionsLoaded":
        source = payload.get("file_path")
        if not source:
            return None
        reason = payload.get("load_reason")
        memory_type = payload.get("memory_type")
        raw_trigger = payload.get("trigger_file_path")
        trigger = normalize_path(raw_trigger, root) if raw_trigger else None
        path = normalize_path(source, root)
    elif event == "PostToolUse":
        tool_input = payload.get("tool_input") or {}
        if payload.get("tool_name") == "Read":
            source = tool_input.get("file_path")
            if not source:
                return None
            path = relative_to(source, root)
            # A Read only counts as an instruction load when it lands on a context surface of THIS repo.
            if path is None or not is_surface_read(path, config):
                return None
            reason = "read"
        elif payload.get("tool_name") == "Skill":
            name = tool_input.get("skill")
            # A skill name is one path segment. Anything else (a separator, `..`) would build a path
            # outside the skills dir — the payload is data, not a location to trust.
            if not name or os.path.basename(name) != name or name in (".", ".."):
                return None
            if any(token in name for token in ("/", "\\", "\0")):
                return None
            path = "%s/%s/SKILL.md" % (config.skills_dir.rstrip("/"), name)
            source = os.path.join(root, path)
            # User- and plugin-level skills are not surfaces of this repo: no file, no line.
            if not os.path.isfile(source):
                return None
            reason = "skill"
        else:
            return None
    else:
        return None

    return {
        "ts": timestamp(now),
        "session_id": payload.get("session_id"),
        "agent_type": payload.get("agent_type"),
        "event": event,
        "reason": reason,
        "path": path,
        "bytes": file_size(source),
        "effort": effort_level(payload),
        "memory_type": memory_type,
        "trigger": trigger,
        # Set only on the extra lines a premises Read produces (see build_records).
        "premise": None,
        "premise_id": None,
        "read": None,
    }


# ── premises: one Read of a premises file is N loads of premises ──────────────────────────────────


def premise_sections(path: str, relative: str) -> tuple:
    """`([(title, start, end, id)], total_lines)`, 1-based with `end` exclusive; `id` may be None.

    Parsed by `premise.parse_sections` — the same H2 rule `context_lint.collect_premises` uses — so a
    title logged here is the title the linter and the generated index use.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            content = handle.read()
    except OSError:
        return [], 0
    sections = [
        (section.title, section.start, section.start + len(section.lines), section.id)
        for section in premise.parse_sections(relative, content)
    ]
    return sections, len(content.split("\n"))


def fetch_script(config: skills_config.Config) -> str | None:
    """The script the configured fetch command runs — `premise.py` by default, whatever the repo set."""
    try:
        tokens = shlex.split(config.premise_fetch)
    except ValueError:
        return None
    for token in tokens:
        base = os.path.basename(token)
        if base and base != "<id>" and not base.startswith("-") and "." in base:
            return base
    return None


def run_by_interpreter(tokens: list, index: int) -> bool:
    """Is the token at `index` the script an interpreter runs? Flags in between (`python3 -u x.py`) skipped."""
    position = index - 1
    while position >= 0 and tokens[position].startswith("-"):
        position -= 1
    return position >= 0 and os.path.basename(tokens[position]).split(".")[0] in INTERPRETERS


def premise_ids_from_command(command: str, script: str | None) -> list:
    """The ids the fetch script was invoked WITH — argv tokens, so `echo "premise.py p-deadbeef"` is not one."""
    if not command or not script or script not in command:
        return []
    try:
        tokens = shlex.split(command)
    except ValueError:  # unbalanced quotes — not a command we can read
        return []

    ids: list = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        # In command position: the script itself, or the argument of an interpreter.
        invoked = os.path.basename(token) == script and (index == 0 or run_by_interpreter(tokens, index))
        if not invoked:
            index += 1
            continue
        index += 1
        while index < len(tokens) and tokens[index] not in SHELL_OPERATORS:
            if premise.ID_TOKEN_RE.fullmatch(tokens[index]) and tokens[index] not in ids:
                ids.append(tokens[index])
            index += 1
    return ids


def resolve_premise_ids(repo: str, config: skills_config.Config, ids: list) -> dict:
    """`{id: (path, title)}` for the ids the tree still carries — one pass, stopping once all are found."""
    found: dict = {}
    wanted = set(ids)
    for relative in premise.premises_files(repo, config):
        sections, _ = premise_sections(os.path.join(repo, relative), relative)
        for title, _start, _end, premise_id in sections:
            if premise_id in wanted:
                found[premise_id] = (relative, title)
                wanted.discard(premise_id)
                if not wanted:
                    return found
    return found


def premise_fetch_records(payload: dict, root: str, config: skills_config.Config, now: datetime.datetime) -> list:
    """One line per id an agent fetched through the fetch command — the path a Read never touches."""
    command = (payload.get("tool_input") or {}).get("command") or ""
    ids = premise_ids_from_command(command, fetch_script(config))
    if not ids:
        return []
    resolved = resolve_premise_ids(root, config, ids)
    records = []
    for premise_id in ids:
        path, title = resolved.get(premise_id, (None, None))
        records.append(
            {
                "ts": timestamp(now),
                "session_id": payload.get("session_id"),
                "agent_type": payload.get("agent_type"),
                "event": payload.get("hook_event_name"),
                "reason": "premise_fetch",
                "path": path,
                "bytes": None,
                "effort": effort_level(payload),
                "memory_type": None,
                "trigger": None,
                "premise": title,
                "premise_id": premise_id,
                "read": "fetch",
            }
        )
    return records


def positive_int(value) -> int | None:
    """A payload number, or None when it is absent or not a usable line count."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def premise_records(base: dict, payload: dict, root: str) -> list:
    """One extra line per premise whose section the Read window touched."""
    sections, total_lines = premise_sections(os.path.join(root, base["path"]), base["path"])
    if not sections:
        return []
    tool_input = payload.get("tool_input") or {}
    offset = positive_int(tool_input.get("offset"))
    limit = positive_int(tool_input.get("limit"))
    whole = offset is None and limit is None
    start = offset if offset is not None else 1
    end = start + limit if limit is not None else total_lines + 1

    records = []
    for title, section_start, section_end, premise_id in sections:
        # Half-open intervals: the section is read when it overlaps [start, end).
        if whole or (section_start < end and section_end > start):
            record = dict(base)
            record["premise"] = title
            record["premise_id"] = premise_id
            record["read"] = "whole" if whole else "range"
            records.append(record)
    return records


def build_records(payload: dict, root: str, config: skills_config.Config, now: datetime.datetime) -> list:
    """The file-level line, plus one line per premise the Read window touched or the fetch command fetched."""
    if payload.get("hook_event_name") == "PostToolUse" and payload.get("tool_name") == "Bash":
        # A Bash call is not a surface load: only the premise fetches inside it are.
        return premise_fetch_records(payload, root, config, now)
    record = build_record(payload, root, config, now)
    if record is None:
        return []
    if record["reason"] == "read" and config.is_premises(record["path"]):
        return [record] + premise_records(record, payload, root)
    return [record]


def append_record(root: str, record: dict, now: datetime.datetime) -> str:
    """Append one line to today's file. Single write, well under the atomic-append size."""
    directory = os.path.join(root, LOG_DIR)
    os.makedirs(directory, exist_ok=True)
    target = os.path.join(directory, "loads-%s.jsonl" % now.strftime("%Y-%m-%d"))
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
    return target


def cmd_hook(stream) -> int:
    """Never raises, never prints, always 0 — a hook that fails must not fail the session."""
    try:
        payload = json.load(stream)
        now = datetime.datetime.now(datetime.timezone.utc)
        root = project_root(payload)
        config = skills_config.load(root)
        for record in build_records(payload, root, config, now):
            append_record(root, record, now)
    except Exception:  # noqa: BLE001 — fail-open is the whole point
        pass
    return 0


# ── report ────────────────────────────────────────────────────────────────────────────────────────


def parse_date(value: str) -> datetime.date:
    return datetime.datetime.strptime(value, "%Y-%m-%d").date()


def load_records(dirs: list, since: datetime.date, until: datetime.date) -> list:
    """Every record whose FILE date falls in [since, until]. A malformed line is skipped, not fatal."""
    records: list = []
    for directory in dirs:
        for path in sorted(glob.glob(os.path.join(directory, "loads-*.jsonl"))):
            stem = os.path.basename(path)[len("loads-") : -len(".jsonl")]
            try:
                day = parse_date(stem)
            except ValueError:
                continue
            if not since <= day <= until:
                continue
            try:
                with open(path, encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(record, dict) and record.get("path"):
                            records.append(record)
            except OSError:
                continue
    return records


def rule_kind(path: str) -> str:
    """A rule with no `paths:` key in its frontmatter is always-on — it loads in every session."""
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
    except OSError:
        return RULE_SCOPED
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return RULE_ALWAYS_ON
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("paths:"):
            return RULE_SCOPED
    return RULE_ALWAYS_ON


def surface_universe(repo: str, config: skills_config.Config) -> dict:
    """Every context surface the repo owns today, mapped to its kind. Loads outside it are `external`."""
    universe = {}
    if os.path.isfile(os.path.join(repo, config.agent_instructions)):
        universe[config.agent_instructions] = INSTRUCTIONS
    # An extra root surface (the README) is logged but is never a decay candidate: nobody demotes the
    # front door. Its own kind keeps `external` meaning "not a surface of this repo".
    for extra in config.root_surfaces[1:]:
        if os.path.isfile(os.path.join(repo, extra)):
            universe[extra] = README
    for path in sorted(glob.glob(os.path.join(repo, config.rules_dir.rstrip("/"), "*.md"))):
        universe[os.path.relpath(path, repo).replace(os.sep, "/")] = rule_kind(path)
    for path in sorted(glob.glob(os.path.join(repo, config.skills_dir.rstrip("/"), "*", "SKILL.md"))):
        universe[os.path.relpath(path, repo).replace(os.sep, "/")] = SKILL
    for path in sorted(glob.glob(os.path.join(repo, config.docs_root.rstrip("/"), "**", "*.md"), recursive=True)):
        relative = os.path.relpath(path, repo).replace(os.sep, "/")
        # The ephemeral docs are dated by design: never a standing context surface.
        if config.is_ephemeral(relative) or config.is_excluded(relative):
            continue
        universe[relative] = DOC
    return universe


def candidate_for(kind: str, loads: int, session_share: float, sessions_total: int) -> str | None:
    if kind not in CANDIDATE_KINDS:
        return None
    if loads == 0:
        return "zero-load"
    if session_share < LOW_LOAD_SESSION_SHARE and sessions_total >= MIN_SESSIONS_FOR_LOW_LOAD:
        return "low-load"
    return None


def premises_universe(repo: str, config: skills_config.Config) -> list:
    """Every premise title of every premises file in the tree, as `(path, title, id)`."""
    universe = []
    for relative in premise.premises_files(repo, config):
        sections, _ = premise_sections(os.path.join(repo, relative), relative)
        for title, _start, _end, premise_id in sections:
            universe.append((relative, title, premise_id))
    return universe


def aggregate_premises(records: list, universe: list, sessions_total: int) -> list:
    """One row per premise — every premise in the tree, plus any logged title or id no longer there."""
    rows: dict = {}

    def row(key: tuple, premise_id: str | None = None) -> dict:
        current = rows.setdefault(
            key,
            {"path": key[0], "title": key[1], "id": premise_id, "loads_range": 0, "loads_whole": 0,
             "loads_fetch": 0, "sessions": set(), "last_loaded": None},
        )
        if current["id"] is None and premise_id is not None:
            current["id"] = premise_id
        return current

    for path, title, premise_id in universe:
        row((path, title), premise_id)
    for record in records:
        if not record.get("read"):
            continue
        title = record.get("premise")
        premise_id = record.get("premise_id")
        if not title and not premise_id:
            continue
        # An id the tree no longer carries has no (path, title) to merge into: it is its own row, and
        # a dangling reference is exactly what the lifecycle wants to see.
        current = row((record["path"], title) if title else (None, premise_id), premise_id)
        if record.get("read") == "whole":
            current["loads_whole"] += 1
        elif record.get("read") == "fetch":
            current["loads_fetch"] += 1
        else:
            current["loads_range"] += 1
        if record.get("session_id"):
            current["sessions"].add(record["session_id"])
        ts = record.get("ts")
        if ts and (current["last_loaded"] is None or ts > current["last_loaded"]):
            current["last_loaded"] = ts

    premises = []
    for key in sorted(rows, key=lambda item: (item[0] or "", item[1] or "")):
        current = rows[key]
        loads = current["loads_range"] + current["loads_whole"] + current["loads_fetch"]
        premises.append(
            {
                "path": current["path"],
                "title": current["title"],
                "id": current["id"],
                "loads_range": current["loads_range"],
                "loads_whole": current["loads_whole"],
                "loads_fetch": current["loads_fetch"],
                "sessions": len(current["sessions"]),
                "last_loaded": current["last_loaded"],
                # Below the session floor the window is too thin for "nobody read it" to mean anything.
                "candidate": "zero-load" if loads == 0 and sessions_total >= MIN_SESSIONS_FOR_LOW_LOAD else None,
            }
        )
    premises.sort(
        key=lambda item: (
            -(item["loads_range"] + item["loads_whole"] + item["loads_fetch"]),
            item["path"] or "",
            item["title"] or "",
        )
    )
    return premises


def aggregate(records: list, universe: dict, repo: str) -> dict:
    """One row per surface — every path seen in the window UNION every surface the repo owns."""
    sessions_total = len({r.get("session_id") for r in records if r.get("session_id")})
    # Premise lines ride along with the file-level line of the same Read: counting both would charge
    # one Read as N loads and N times the file size. `surfaces[]` keeps its v1 meaning. The marker is
    # `read`, not `premise` — a fetch of an id the tree lost has no title but is still not a file load.
    records = [record for record in records if not record.get("read")]
    per_path: dict = {}
    for path in universe:
        per_path[path] = {"loads": 0, "sessions": set(), "bytes_loaded": 0, "reasons": {}, "last_loaded": None,
                          "logged_bytes": None}
    for record in records:
        path = record["path"]
        row = per_path.setdefault(
            path,
            {"loads": 0, "sessions": set(), "bytes_loaded": 0, "reasons": {}, "last_loaded": None,
             "logged_bytes": None},
        )
        row["loads"] += 1
        if record.get("session_id"):
            row["sessions"].add(record["session_id"])
        size = record.get("bytes")
        if isinstance(size, int):
            row["bytes_loaded"] += size
            row["logged_bytes"] = size
        reason = record.get("reason") or "unknown"
        row["reasons"][reason] = row["reasons"].get(reason, 0) + 1
        ts = record.get("ts")
        if ts and (row["last_loaded"] is None or ts > row["last_loaded"]):
            row["last_loaded"] = ts

    surfaces = []
    for path, row in per_path.items():
        kind = universe.get(path, EXTERNAL)
        sessions = len(row["sessions"])
        share = sessions / sessions_total if sessions_total else 0.0
        current = file_size(os.path.join(repo, path)) if path in universe else None
        surfaces.append(
            {
                "path": path,
                "kind": kind,
                "bytes": current if current is not None else row["logged_bytes"],
                "loads": row["loads"],
                "sessions": sessions,
                "session_share": round(share, 4),
                "bytes_loaded": row["bytes_loaded"],
                "reasons": dict(sorted(row["reasons"].items())),
                "last_loaded": row["last_loaded"],
                "candidate": candidate_for(kind, row["loads"], share, sessions_total),
            }
        )
    surfaces.sort(key=lambda item: (-item["bytes_loaded"], item["path"]))
    return {"surfaces": surfaces, "sessions": sessions_total, "loads": len(records)}


def sessions_by_effort(records: list) -> dict:
    """Distinct sessions per effort level, `unknown` for the lines that carried none.

    Counted over EVERY record, premise lines included: the axis is the session, not the load, so
    filtering the extra lines out would drop a session whose only records were premise fetches. A
    session that changed level inside the window counts in each level it logged — the histogram
    answers "which levels ran here", not "one bucket per session", so the totals may exceed
    `window.sessions`.
    """
    buckets: dict = {}
    for record in records:
        session = record.get("session_id")
        if not session:
            continue
        buckets.setdefault(record.get("effort") or EFFORT_UNKNOWN, set()).add(session)
    return {level: len(sessions) for level, sessions in sorted(buckets.items())}


def build_report(records: list, universe: dict, repo: str, since: datetime.date, until: datetime.date,
                 dirs: list, premises_index: list | None = None,
                 config: skills_config.Config | None = None) -> dict:
    config = config or skills_config.load(repo)
    aggregated = aggregate(records, universe, repo)
    surfaces = aggregated["surfaces"]
    premises = aggregate_premises(
        records,
        premises_universe(repo, config) if premises_index is None else premises_index,
        aggregated["sessions"],
    )
    candidates = [
        {"path": item["path"], "kind": item["kind"], "bytes": item["bytes"], "candidate": item["candidate"]}
        for item in surfaces
        if item["candidate"]
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "window": {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "sessions": aggregated["sessions"],
            "sessions_by_effort": sessions_by_effort(records),
            "loads": aggregated["loads"],
            "dirs": dirs,
        },
        "surfaces": surfaces,
        "premises": premises,
        "candidates": candidates,
    }


def render_markdown(report: dict) -> str:
    window = report["window"]
    lines = [
        "# Context load per surface",
        "",
        "Window `%s..%s` — %d session(s), %d load(s), %d log directory(ies)."
        % (window["since"], window["until"], window["sessions"], window["loads"], len(window["dirs"])),
    ]
    effort = window.get("sessions_by_effort") or {}
    if effort:
        lines.append(
            "Sessions by effort: " + " · ".join("%s %d" % (level, count) for level, count in effort.items())
        )
    lines += [
        "",
        "| path | kind | bytes | loads | sessions | share | bytes_loaded | candidate |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["surfaces"]:
        lines.append(
            "| `%s` | %s | %s | %d | %d | %.0f%% | %d | %s |"
            % (
                item["path"],
                item["kind"],
                item["bytes"] if item["bytes"] is not None else "—",
                item["loads"],
                item["sessions"],
                item["session_share"] * 100,
                item["bytes_loaded"],
                item["candidate"] or "—",
            )
        )
    lines += ["", "## Premises", ""]
    if report.get("premises"):
        lines += [
            "| path | premise | id | range | whole | fetch | sessions | last_loaded | candidate |",
            "|---|---|---|---:|---:|---:|---:|---|---|",
        ]
        for item in report["premises"]:
            lines.append(
                "| `%s` | %s | %s | %d | %d | %d | %d | %s | %s |"
                % (
                    item["path"] or "—",
                    item["title"] or "(id with no premise in the tree)",
                    item["id"] or "—",
                    item["loads_range"],
                    item["loads_whole"],
                    item["loads_fetch"],
                    item["sessions"],
                    item["last_loaded"] or "—",
                    item["candidate"] or "—",
                )
            )
    else:
        lines.append("No premises in the tree.")

    lines += ["", "## Decay / demotion candidates", ""]
    if report["candidates"]:
        for item in report["candidates"]:
            lines.append(
                "- `%s` (%s, %s bytes) — %s"
                % (item["path"], item["kind"], item["bytes"] if item["bytes"] is not None else "—", item["candidate"])
            )
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def cmd_report(args: argparse.Namespace, stdout) -> int:
    until = parse_date(args.until) if args.until else datetime.datetime.now(datetime.timezone.utc).date()
    since = parse_date(args.since) if args.since else until - datetime.timedelta(days=DEFAULT_WINDOW_DAYS - 1)
    repo = os.path.abspath(args.repo)
    config = skills_config.load(repo, args.config)
    records = load_records(args.dirs, since, until)
    report = build_report(records, surface_universe(repo, config), repo, since, until, args.dirs, config=config)
    if args.format == "json":
        stdout.write(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    else:
        stdout.write(render_markdown(report))
    return 0


# ── entry point ───────────────────────────────────────────────────────────────────────────────────


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description="Instruction-load log and context-usage report for Claude Code.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hook", help="read one hook payload on stdin and append a log line")

    report = sub.add_parser("report", help="aggregate the log files of a window by surface")
    report.add_argument("dirs", nargs="+", help="one or more .claude/telemetry directories")
    report.add_argument("--since", help="first day of the window, YYYY-MM-DD (default: until - 13 days)")
    report.add_argument("--until", help="last day of the window, YYYY-MM-DD (default: today, UTC)")
    report.add_argument("--format", choices=("md", "json"), default="md", help="output format (default: md)")
    report.add_argument("--repo", default=skills_config.default_repo(), help="repository root for the universe")
    report.add_argument(
        "--config", default=None, help="path to the config (default: <repo>/%s)" % skills_config.CONFIG_PATH
    )

    args = parser.parse_args(argv)
    if args.command == "hook":
        return cmd_hook(sys.stdin)
    return cmd_report(args, sys.stdout)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
