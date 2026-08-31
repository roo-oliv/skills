#!/usr/bin/env python3
"""Red cases for agent_telemetry.py — a logger that miscounts is worse than no logger.

Each test builds a throwaway repo (an instructions file, a rules dir, a skills dir, a docs tree with
one premises file), feeds one hook payload through the script's own entry point, and asserts the JSON
lines that came out — then aggregates them and asserts the report. The config-driven cases move every
path (`## Context toolkit`, `## Docs layout`) and assert the same behaviour follows.

    python3 -m unittest discover -s ci -p 'agent_telemetry_test.py'
"""

from __future__ import annotations

import datetime
import io
import glob
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_telemetry  # noqa: E402
import skills_config  # noqa: E402

NOW = datetime.datetime(2026, 8, 27, 12, 30, 5, 123000, tzinfo=datetime.timezone.utc)
SESSION = "s-1"

PREMISES = (
    "# Catalog premises\n"
    "\n"
    "Context paragraph.\n"
    "\n"
    "## First invariant\n"
    "**Id:** p-0000000a\n"
    "\n"
    "Body of the first.\n"
    "\n"
    "## Second invariant\n"
    "**Id:** p-0000000b\n"
    "\n"
    "Body of the second.\n"
    "\n"
    "## Third invariant\n"
    "**Id:** p-0000000c\n"
    "\n"
    "Body of the third.\n"
)

FIXTURE = {
    "CLAUDE.md": "# Instructions\n\nkernel\n",
    "README.md": "# Repo\n",
    ".claude/rules/always.md": "---\ndescription: always on\n---\n\nbody\n",
    ".claude/rules/scoped.md": "---\ndescription: scoped\npaths:\n  - docs/**\n---\n\nbody\n",
    ".claude/skills/verify/SKILL.md": "---\nname: verify\ndescription: verify\n---\n\nbody\n",
    "docs/index.md": "# Index\n",
    "docs/catalog/premises.md": PREMISES,
    "docs/work/2026-08-01-note.md": "# Ephemeral\n",
    "src/main.py": "print('not a surface')\n",
}


def write(root: str, files: dict) -> None:
    for relative, content in files.items():
        path = os.path.join(root, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)


class Harness(unittest.TestCase):
    extra: dict = {}

    def setUp(self) -> None:
        # The ambient environment must not decide what a test asserts: the effort cases set it themselves.
        ambient = os.environ.pop(agent_telemetry.EFFORT_ENV, None)
        if ambient is not None:
            self.addCleanup(os.environ.__setitem__, agent_telemetry.EFFORT_ENV, ambient)
        self.repo = tempfile.mkdtemp(prefix="telemetry-")
        write(self.repo, dict(FIXTURE, **self.extra))
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.config = skills_config.load(self.repo)

    # ── helpers ───────────────────────────────────────────────────────────────────────────────

    def payload(self, **fields) -> dict:
        base = {"session_id": SESSION, "cwd": self.repo}
        base.update(fields)
        return base

    def records(self, payload: dict, now: datetime.datetime = NOW) -> list:
        return agent_telemetry.build_records(payload, self.repo, self.config, now)

    def run_hook(self, payload: dict) -> list:
        stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
        try:
            code = agent_telemetry.main(["hook"])
        finally:
            sys.stdin = stdin
        self.assertEqual(code, 0)
        return self.log_lines()

    def log_lines(self) -> list:
        directory = os.path.join(self.repo, agent_telemetry.LOG_DIR)
        lines = []
        for name in sorted(os.listdir(directory)) if os.path.isdir(directory) else []:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                lines += [json.loads(line) for line in handle if line.strip()]
        return lines

    def read(self, path: str, **tool_input) -> dict:
        return self.payload(
            hook_event_name="PostToolUse",
            tool_name="Read",
            tool_input=dict({"file_path": os.path.join(self.repo, path)}, **tool_input),
        )

    def report(self, records: list, dirs: list | None = None) -> dict:
        return agent_telemetry.build_report(
            records,
            agent_telemetry.surface_universe(self.repo, self.config),
            self.repo,
            datetime.date(2026, 8, 20),
            datetime.date(2026, 8, 27),
            dirs or [],
            config=self.config,
        )


class HookRecords(Harness):
    def test_instructions_loaded_carries_reason_memory_type_and_trigger(self) -> None:
        records = self.records(
            self.payload(
                hook_event_name="InstructionsLoaded",
                file_path=os.path.join(self.repo, ".claude/rules/scoped.md"),
                load_reason="path_glob_match",
                memory_type="Project",
                trigger_file_path=os.path.join(self.repo, "docs/index.md"),
            )
        )
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["path"], ".claude/rules/scoped.md")
        self.assertEqual(record["reason"], "path_glob_match")
        self.assertEqual(record["memory_type"], "Project")
        self.assertEqual(record["trigger"], "docs/index.md")
        self.assertEqual(record["ts"], "2026-08-27T12:30:05.123Z")
        self.assertEqual(sorted(record), sorted(
            ["agent_type", "bytes", "effort", "event", "memory_type", "path", "premise", "premise_id",
             "read", "reason", "session_id", "trigger", "ts"]
        ))

    def test_user_memory_outside_the_repo_is_logged_with_a_tilde(self) -> None:
        home = os.path.expanduser("~")
        records = self.records(
            self.payload(
                hook_event_name="InstructionsLoaded",
                file_path=os.path.join(home, ".claude", "CLAUDE.md"),
                load_reason="session_start",
            )
        )
        self.assertEqual(records[0]["path"], "~/.claude/CLAUDE.md")

    def test_read_of_a_surface_is_a_load_and_a_read_of_source_is_not(self) -> None:
        self.assertEqual(self.records(self.read("docs/index.md"))[0]["reason"], "read")
        self.assertEqual(self.records(self.read("CLAUDE.md"))[0]["path"], "CLAUDE.md")
        self.assertEqual(self.records(self.read("src/main.py")), [])

    def test_read_outside_the_repo_is_not_a_surface_load(self) -> None:
        other = tempfile.mkdtemp(prefix="other-")
        self.addCleanup(shutil.rmtree, other, True)
        write(other, {"docs/index.md": "# Elsewhere\n"})
        payload = self.payload(
            hook_event_name="PostToolUse",
            tool_name="Read",
            tool_input={"file_path": os.path.join(other, "docs/index.md")},
        )
        self.assertEqual(self.records(payload), [])

    def test_skill_invocation_logs_the_skill_file_only_when_the_repo_owns_it(self) -> None:
        def skill(name: str) -> dict:
            return self.payload(hook_event_name="PostToolUse", tool_name="Skill", tool_input={"skill": name})

        records = self.records(skill("verify"))
        self.assertEqual(records[0]["path"], ".claude/skills/verify/SKILL.md")
        self.assertEqual(records[0]["reason"], "skill")
        self.assertEqual(self.records(skill("not-installed")), [])
        # A separator would escape the skills dir: the payload is data, not a location to trust.
        self.assertEqual(self.records(skill("../../etc/passwd")), [])

    def test_effort_comes_from_the_payload_then_the_environment_and_never_invents_a_bucket(self) -> None:
        self.assertEqual(self.records(self.read("docs/index.md"))[0]["effort"], None)
        payload = dict(self.read("docs/index.md"), effort={"level": "high"})
        self.assertEqual(self.records(payload)[0]["effort"], "high")
        payload = dict(self.read("docs/index.md"), effort={"level": "galaxy"})
        self.assertEqual(self.records(payload)[0]["effort"], None)
        os.environ["CLAUDE_EFFORT"] = "medium"
        self.addCleanup(os.environ.pop, "CLAUDE_EFFORT", None)
        self.assertEqual(self.records(self.read("docs/index.md"))[0]["effort"], "medium")

    def test_an_unknown_event_or_tool_writes_nothing(self) -> None:
        self.assertEqual(self.records(self.payload(hook_event_name="SessionStart")), [])
        self.assertEqual(
            self.records(self.payload(hook_event_name="PostToolUse", tool_name="Grep", tool_input={})), []
        )


class PremiseLoads(Harness):
    def test_whole_read_credits_every_premise_plus_one_file_line(self) -> None:
        records = self.records(self.read("docs/catalog/premises.md"))
        self.assertEqual(records[0]["read"], None)
        self.assertEqual([r["premise"] for r in records[1:]],
                         ["First invariant", "Second invariant", "Third invariant"])
        self.assertEqual({r["read"] for r in records[1:]}, {"whole"})
        self.assertEqual([r["premise_id"] for r in records[1:]],
                         ["p-0000000a", "p-0000000b", "p-0000000c"])

    def test_a_ranged_read_credits_only_the_premises_the_window_touched(self) -> None:
        records = self.records(self.read("docs/catalog/premises.md", offset=10, limit=3))
        self.assertEqual([r["premise"] for r in records[1:]], ["Second invariant"])
        self.assertEqual(records[1]["read"], "range")

    def test_the_premises_index_is_a_plain_surface_not_a_container(self) -> None:
        write(self.repo, {"docs/catalog/premises-index.md": "## First invariant\n"})
        records = self.records(self.read("docs/catalog/premises-index.md"))
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["read"])


class PremiseFetches(Harness):
    def bash(self, command: str) -> list:
        return self.records(
            self.payload(hook_event_name="PostToolUse", tool_name="Bash", tool_input={"command": command})
        )

    def test_a_fetch_by_id_resolves_the_path_and_the_title(self) -> None:
        records = self.bash("python3 .github/scripts/premise.py p-0000000b --deps")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["premise"], "Second invariant")
        self.assertEqual(records[0]["path"], "docs/catalog/premises.md")
        self.assertEqual(records[0]["read"], "fetch")
        self.assertEqual(records[0]["reason"], "premise_fetch")

    def test_an_id_the_tree_lost_is_still_logged_with_a_null_path(self) -> None:
        records = self.bash("python3 .github/scripts/premise.py p-deadbeef")
        self.assertEqual(records[0]["premise_id"], "p-deadbeef")
        self.assertIsNone(records[0]["path"])
        self.assertIsNone(records[0]["premise"])

    def test_the_id_has_to_be_in_argv_of_the_script_not_anywhere_in_the_string(self) -> None:
        self.assertEqual(self.bash('echo "premise.py p-0000000a"'), [])
        self.assertEqual(self.bash("grep -r 'premise.py p-0000000a' docs"), [])
        self.assertEqual(self.bash("cat docs/index.md"), [])

    def test_a_bash_call_is_never_a_surface_load_of_its_own(self) -> None:
        self.assertEqual(self.bash("python3 .github/scripts/premise.py mint"), [])


class Report(Harness):
    def load_of(self, path: str, session: str, **fields) -> dict:
        record = self.records(dict(self.read(path), session_id=session, **fields))[0]
        return record

    def test_surfaces_count_one_load_per_read_and_premise_lines_do_not_inflate_them(self) -> None:
        records = self.records(self.read("docs/catalog/premises.md"))
        report = self.report(records)
        row = next(r for r in report["surfaces"] if r["path"] == "docs/catalog/premises.md")
        self.assertEqual(row["loads"], 1)
        self.assertEqual(report["window"]["loads"], 1)
        self.assertEqual(row["bytes_loaded"], len(PREMISES))

    def test_every_owned_surface_appears_even_with_zero_loads_and_only_some_are_candidates(self) -> None:
        report = self.report(self.records(self.read("docs/index.md")))
        kinds = {row["path"]: row["kind"] for row in report["surfaces"]}
        self.assertEqual(kinds["CLAUDE.md"], "instructions")
        self.assertEqual(kinds["README.md"], "readme")
        self.assertEqual(kinds[".claude/rules/always.md"], "rule-always-on")
        self.assertEqual(kinds[".claude/rules/scoped.md"], "rule-scoped")
        self.assertEqual(kinds[".claude/skills/verify/SKILL.md"], "skill")
        self.assertEqual(kinds["docs/index.md"], "doc")
        self.assertNotIn("docs/work/2026-08-01-note.md", kinds)  # ephemeral by config
        candidates = {row["path"] for row in report["candidates"]}
        self.assertIn(".claude/rules/scoped.md", candidates)
        self.assertNotIn("CLAUDE.md", candidates)
        self.assertNotIn("README.md", candidates)
        self.assertNotIn(".claude/rules/always.md", candidates)

    def test_a_path_outside_the_universe_is_external_and_never_a_candidate(self) -> None:
        record = self.records(
            self.payload(
                hook_event_name="InstructionsLoaded",
                file_path=os.path.expanduser("~/.claude/CLAUDE.md"),
                load_reason="session_start",
            )
        )[0]
        report = self.report([record])
        row = next(r for r in report["surfaces"] if r["path"] == "~/.claude/CLAUDE.md")
        self.assertEqual(row["kind"], "external")
        self.assertIsNone(row["candidate"])

    def test_premises_rows_split_range_whole_and_fetch(self) -> None:
        records = self.records(self.read("docs/catalog/premises.md", offset=10, limit=3))
        records += self.records(
            self.payload(
                hook_event_name="PostToolUse",
                tool_name="Bash",
                tool_input={"command": "python3 .github/scripts/premise.py p-0000000b"},
            )
        )
        report = self.report(records)
        rows = {row["title"]: row for row in report["premises"]}
        self.assertEqual(rows["Second invariant"]["loads_range"], 1)
        self.assertEqual(rows["Second invariant"]["loads_fetch"], 1)
        self.assertEqual(rows["Second invariant"]["id"], "p-0000000b")
        self.assertEqual(rows["First invariant"]["loads_range"], 0)
        self.assertEqual(rows["First invariant"]["candidate"], None)  # window under the session floor

    def test_zero_load_premise_is_a_candidate_once_the_window_has_enough_sessions(self) -> None:
        records = [
            self.records(dict(self.read("docs/index.md"), session_id="s-%d" % i))[0]
            for i in range(agent_telemetry.MIN_SESSIONS_FOR_LOW_LOAD)
        ]
        report = self.report(records)
        rows = {row["title"]: row for row in report["premises"]}
        self.assertEqual(rows["First invariant"]["candidate"], "zero-load")

    def test_sessions_by_effort_counts_sessions_not_loads(self) -> None:
        records = []
        for path in ("docs/index.md", "CLAUDE.md"):
            records.append(self.records(dict(self.read(path), effort={"level": "high"}))[0])
        records.append(
            self.records(dict(self.read("docs/index.md"), session_id="s-2", effort={"level": "low"}))[0]
        )
        records.append(self.records(dict(self.read("docs/index.md"), session_id="s-3"))[0])
        report = self.report(records)
        self.assertEqual(report["window"]["sessions_by_effort"], {"high": 1, "low": 1, "unknown": 1})

    def test_the_json_report_is_schema_version_2_with_the_keys_decay_reads(self) -> None:
        report = self.report(self.records(self.read("docs/index.md")))
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(
            sorted(report), ["candidates", "premises", "schema_version", "surfaces", "window"]
        )
        self.assertEqual(
            sorted(report["window"]),
            ["dirs", "loads", "sessions", "sessions_by_effort", "since", "until"],
        )
        self.assertTrue(all({"path", "title", "id", "candidate"} <= set(row) for row in report["premises"]))

    def test_markdown_render_carries_every_section(self) -> None:
        text = agent_telemetry.render_markdown(self.report(self.records(self.read("docs/index.md"))))
        self.assertIn("# Context load per surface", text)
        self.assertIn("## Premises", text)
        self.assertIn("## Decay / demotion candidates", text)


class ConfigDriven(Harness):
    extra = {
        "docs/agents/skills-config.md": (
            "# Agent skills config\n"
            "\n"
            "## Docs layout\n"
            "\n"
            "- **Premises:** `documentation/{domain}/invariants.md`\n"
            "- **Premise fetch command:** `node tools/read-premise.js <id>`\n"
            "\n"
            "## Context toolkit\n"
            "\n"
            "- **Agent instructions file:** `AGENTS.md`\n"
            "- **Skills dir:** `.agents/skills/`\n"
            "- **Rules dir:** `.agents/rules/`\n"
            "- **Docs root:** `documentation/`\n"
            "- **Ephemeral docs:** `documentation/scratch/**`\n"
        ),
        "AGENTS.md": "# Instructions\n",
        ".agents/rules/scoped.md": "---\ndescription: r\npaths:\n  - '**'\n---\n\nbody\n",
        ".agents/skills/build/SKILL.md": "---\nname: build\ndescription: b\n---\n\nbody\n",
        "documentation/guide.md": "# Guide\n",
        "documentation/scratch/note.md": "# Scratch\n",
        "documentation/catalog/invariants.md": PREMISES,
    }

    def test_the_surface_universe_follows_the_config_not_the_defaults(self) -> None:
        universe = agent_telemetry.surface_universe(self.repo, self.config)
        self.assertEqual(universe["AGENTS.md"], "instructions")
        self.assertEqual(universe[".agents/skills/build/SKILL.md"], "skill")
        self.assertEqual(universe["documentation/guide.md"], "doc")
        self.assertNotIn("documentation/scratch/note.md", universe)
        self.assertNotIn("docs/index.md", universe)

    def test_a_read_under_the_configured_docs_root_is_a_load_and_the_default_one_is_not(self) -> None:
        self.assertEqual(self.records(self.read("documentation/guide.md"))[0]["reason"], "read")
        self.assertEqual(self.records(self.read("docs/index.md")), [])
        self.assertEqual(self.records(self.read("AGENTS.md"))[0]["path"], "AGENTS.md")

    def test_premises_are_found_at_the_configured_pattern(self) -> None:
        records = self.records(self.read("documentation/catalog/invariants.md"))
        self.assertEqual(len(records), 4)
        self.assertEqual(records[1]["premise"], "First invariant")

    def test_the_fetch_script_is_the_one_the_config_names(self) -> None:
        def bash(command: str) -> list:
            return self.records(
                self.payload(hook_event_name="PostToolUse", tool_name="Bash", tool_input={"command": command})
            )

        records = bash("node tools/read-premise.js p-0000000c")
        self.assertEqual(records[0]["premise"], "Third invariant")
        self.assertEqual(records[0]["path"], "documentation/catalog/invariants.md")
        # The default script is not the configured one: no line.
        self.assertEqual(bash("python3 .github/scripts/premise.py p-0000000c"), [])


class HookContract(Harness):
    def test_the_hook_writes_one_file_per_day_and_prints_nothing(self) -> None:
        stdout, sys.stdout = sys.stdout, io.StringIO()
        try:
            lines = self.run_hook(
                self.payload(
                    hook_event_name="InstructionsLoaded",
                    file_path=os.path.join(self.repo, "CLAUDE.md"),
                    load_reason="session_start",
                )
            )
            printed = sys.stdout.getvalue()
        finally:
            sys.stdout = stdout
        self.assertEqual(printed, "")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["path"], "CLAUDE.md")
        names = os.listdir(os.path.join(self.repo, agent_telemetry.LOG_DIR))
        self.assertEqual(len(names), 1)
        self.assertTrue(names[0].startswith("loads-") and names[0].endswith(".jsonl"))

    def test_a_broken_payload_exits_zero_and_writes_nothing(self) -> None:
        stdin, sys.stdin = sys.stdin, io.StringIO("not json at all")
        try:
            self.assertEqual(agent_telemetry.main(["hook"]), 0)
        finally:
            sys.stdin = stdin
        self.assertFalse(os.path.isdir(os.path.join(self.repo, agent_telemetry.LOG_DIR)))

    def test_load_records_skips_malformed_lines_and_files_outside_the_window(self) -> None:
        directory = os.path.join(self.repo, agent_telemetry.LOG_DIR)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "loads-2026-08-25.jsonl"), "w", encoding="utf-8") as handle:
            handle.write('{"path": "CLAUDE.md"}\n{ broken\n\n{"no": "path"}\n')
        with open(os.path.join(directory, "loads-2026-01-01.jsonl"), "w", encoding="utf-8") as handle:
            handle.write('{"path": "README.md"}\n')
        with open(os.path.join(directory, "loads-nonsense.jsonl"), "w", encoding="utf-8") as handle:
            handle.write('{"path": "README.md"}\n')
        records = agent_telemetry.load_records(
            [directory], datetime.date(2026, 8, 20), datetime.date(2026, 8, 27)
        )
        self.assertEqual([r["path"] for r in records], ["CLAUDE.md"])


class CheckoutResolution(Harness):
    """Which checkout the log lands in. An agent isolated in a git worktree inherits
    `CLAUDE_PROJECT_DIR` from the session that spawned it: writing there would put the load lines in a
    repository the command never touched — and `hooks/context_hooks.py` READS this same `LOG_DIR` to
    decide which premise trailers a commit owes, so a writer and a reader that disagree on the root
    drop the signal in silence."""

    def setUp(self) -> None:
        super().setUp()
        previous = os.environ.get("CLAUDE_PROJECT_DIR")
        os.environ["CLAUDE_PROJECT_DIR"] = self.repo
        self.addCleanup(
            lambda: os.environ.__setitem__("CLAUDE_PROJECT_DIR", previous)
            if previous is not None
            else os.environ.pop("CLAUDE_PROJECT_DIR", None)
        )

    def git_repo(self) -> str:
        repo = tempfile.mkdtemp(prefix="telemetry-worktree-")
        self.addCleanup(shutil.rmtree, repo, True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        # `git rev-parse --show-toplevel` answers the physical path, and on macOS $TMPDIR is a
        # symlink — resolve it here so the fixture compares like with like.
        return os.path.realpath(repo)

    @staticmethod
    def logs_in(root: str) -> list:
        return sorted(glob.glob(os.path.join(root, agent_telemetry.LOG_DIR, "loads-*.jsonl")))

    def instructions(self, path: str, cwd: str) -> dict:
        return {"session_id": SESSION, "cwd": cwd, "hook_event_name": "InstructionsLoaded",
                "file_path": path, "load_reason": "session_start"}

    def test_the_log_lands_in_the_checkout_of_the_payload_cwd(self) -> None:
        other = self.git_repo()
        write(other, {"CLAUDE.md": "# other\n"})
        self.run_hook(self.instructions(os.path.join(other, "CLAUDE.md"), other))
        self.assertEqual([], self.logs_in(self.repo))  # NOT under $CLAUDE_PROJECT_DIR
        written = self.logs_in(other)
        self.assertEqual(1, len(written))
        with open(written[0], encoding="utf-8") as handle:
            record = json.loads(handle.read().splitlines()[0])
        self.assertEqual("CLAUDE.md", record["path"])  # normalized against the right root

    def test_a_cwd_that_is_not_a_checkout_keeps_the_old_precedence(self) -> None:
        plain = tempfile.mkdtemp(prefix="telemetry-plain-")
        self.addCleanup(shutil.rmtree, plain, True)
        self.run_hook(self.instructions(os.path.join(self.repo, "CLAUDE.md"), plain))
        self.assertEqual(1, len(self.logs_in(self.repo)))
        self.assertEqual([], self.logs_in(plain))


# ── commits: the trailers, mined ──────────────────────────────────────────────────────────────────

# A SYNTHETIC body in the exact shape a squash-merge produces: N commit messages concatenated under one
# subject, each original commit's own `Co-Authored-By:` trailer paragraph in the MIDDLE of the body, and
# the merge's `Co-authored-by:` as the last paragraph — the only paragraph `git interpret-trailers`
# would ever look at, which is why the miner never uses it.
PLAIN_SQUASH_BODY = """feat(catalog): reserve stock before charging (#412)

* feat(catalog): reserve stock before charging

The reservation is written under the row lock, so a second checkout of the
same sku cannot read a stale quantity.

Co-Authored-By: Assistant <noreply@example.com>

* fix(catalog): the reservation expires with the cart, not with the session

Co-Authored-By: Assistant <noreply@example.com>
Some-Other-Trailer: an unrelated key git already writes

* test(shipping): a shipment is never re-dispatched

Co-Authored-By: Assistant <noreply@example.com>

---------

Co-authored-by: Assistant <noreply@example.com>
"""

# The same body once the commit gate is on: the trailers are appended to the trailer paragraph of each
# ORIGINAL commit, exactly where `git commit --trailer` puts them.
SQUASH_BODY = (
    PLAIN_SQUASH_BODY.replace(
        "Co-Authored-By: Assistant <noreply@example.com>\n\n* fix(catalog)",
        "Co-Authored-By: Assistant <noreply@example.com>\n"
        "Premises-Read: p-1a2b3c4d\n"
        "Agent-Session: session-alpha\n\n* fix(catalog)",
    )
    .replace(
        "Some-Other-Trailer: an unrelated key git already writes\n\n* test(shipping)",
        "Some-Other-Trailer: an unrelated key git already writes\n"
        "Premises-Read: p-1a2b3c4d, p-99887766\n"
        "Premises-Files-Read: docs/shipping/premises.md\n"
        "Agent-Session: session-alpha\n\n* test(shipping)",
    )
    .replace(
        "Co-Authored-By: Assistant <noreply@example.com>\n\n---------",
        "Co-Authored-By: Assistant <noreply@example.com>\n"
        "Premises-Read: p-99887766\n"
        "Premises-Violated: p-99887766\n"
        "Agent-Session: session-beta\n\n---------",
    )
)

UNIVERSE = [
    ("docs/catalog/premises.md", "Indexed premise", "p-1a2b3c4d", True),
    ("docs/catalog/premises.md", "Another indexed premise", "p-99887766", False),
    ("docs/shipping/premises.md", "First premise", "p-cafed00d", True),
    ("docs/shipping/premises.md", "Second premise", "p-beefbeef", False),
]

# A window wide enough to hold any fixture commit. Not year 2100: git's date parser is bounded by
# time_t, and `--until=2100-01-01` silently matches NOTHING (found by this very test).
WIDE_SINCE = datetime.date(2000, 1, 1)
WIDE_UNTIL = datetime.date(2030, 1, 1)


def block(session: str, read: str = "", violated: str = "", files: str = "",
          author: str = "Fixture", date: str = "2026-08-28") -> dict:
    return {"session": session, "read": [i for i in read.split() if i], "files": [f for f in files.split() if f],
            "violated": [i for i in violated.split() if i], "author": author, "date": date, "origin": "test"}


class TrailerParsing(unittest.TestCase):
    def test_a_squash_body_without_the_gate_has_no_block(self) -> None:
        # The honest baseline: a repository that predates the trailers mines nothing. Positive control
        # that the parser is not simply blind — the annotated copy below finds three.
        self.assertEqual([], agent_telemetry.trailer_blocks(PLAIN_SQUASH_BODY))

    def test_one_block_per_original_commit_in_the_middle_of_a_squash(self) -> None:
        blocks = agent_telemetry.trailer_blocks(SQUASH_BODY)
        self.assertEqual(3, len(blocks))
        self.assertEqual(["session-alpha", "session-alpha", "session-beta"],
                         [b["Agent-Session"] for b in blocks])
        self.assertEqual("p-1a2b3c4d, p-99887766", blocks[1]["Premises-Read"])
        self.assertEqual("docs/shipping/premises.md", blocks[1]["Premises-Files-Read"])
        self.assertEqual("p-99887766", blocks[2]["Premises-Violated"])

    def test_the_trailers_are_not_in_the_last_paragraph(self) -> None:
        """Why `git interpret-trailers` is banned: it reads the last paragraph, and the last paragraph
        of a squash belongs to the MERGE, not to any of the commits that carried a session."""
        last_paragraph = SQUASH_BODY.strip().split("\n\n")[-1]
        self.assertIn("Co-authored-by:", last_paragraph)
        for key in ("Agent-Session", "Premises-Read", "Premises-Violated"):
            self.assertNotIn(key, last_paragraph)

    def test_a_block_without_agent_session_is_not_a_commit_of_the_population(self) -> None:
        self.assertEqual([], agent_telemetry.trailer_blocks("subject\n\nPremises-Read: p-1a2b3c4d\n"))

    def test_values_are_split_deduplicated_and_ordered_as_written(self) -> None:
        blocks = agent_telemetry.trailer_blocks(
            "subject\n\nPremises-Read: p-1a2b3c4d,  p-99887766 , p-1a2b3c4d\nAgent-Session: s1\n"
        )
        self.assertEqual(["p-1a2b3c4d", "p-99887766"],
                         agent_telemetry.trailer_values(blocks[0], "Premises-Read"))

    def test_only_well_formed_ids_reach_the_premise_axis(self) -> None:
        blocks = agent_telemetry.trailer_blocks(
            "subject\n\nPremises-Read: p-1a2b3c4d, junk, p-XYZ\nAgent-Session: s1\n"
        )
        record = agent_telemetry.commit_record(blocks[0], "Fixture", "2026-08-28", "test")
        self.assertEqual(["p-1a2b3c4d"], record["read"])

    def test_two_runs_glued_by_a_stray_key_line_still_count_twice(self) -> None:
        body = "subject\n\nAgent-Session: s1\nNote: just a line with a colon\nAgent-Session: s2\n"
        self.assertEqual(["s1", "s2"], [b["Agent-Session"] for b in agent_telemetry.trailer_blocks(body)])


class Classification(unittest.TestCase):
    FLOOR = agent_telemetry.MIN_COMMITS_FOR_SILENCE

    def rows(self, records: list) -> dict:
        aggregated = agent_telemetry.aggregate_commits(records, UNIVERSE)
        return {item["id"]: item for item in aggregated["premises"]}

    def padding(self, count: int) -> list:
        """Annotated commits that touch no premise of the universe — they only fill the window."""
        return [block("s-pad-%d" % index, read="p-nobody") for index in range(count)]

    def test_read_and_never_violated_is_working(self) -> None:
        rows = self.rows([block("s1", read="p-1a2b3c4d")])
        self.assertEqual("working", rows["p-1a2b3c4d"]["class"])
        self.assertEqual(1, rows["p-1a2b3c4d"]["reads"]["commits"])
        self.assertEqual("keep", rows["p-1a2b3c4d"]["action"])

    def test_read_and_violated_twice_is_confusing(self) -> None:
        rows = self.rows([
            block("s1", read="p-1a2b3c4d"),
            block("s2", read="p-1a2b3c4d", violated="p-1a2b3c4d", date="2026-08-29"),
            block("s3", violated="p-1a2b3c4d", date="2026-08-30"),
        ])
        row = rows["p-1a2b3c4d"]
        self.assertEqual("confusing", row["class"])
        self.assertEqual("rewrite it, or promote it to a gate", row["action"])
        self.assertEqual(2, row["reads"]["commits"])
        self.assertEqual(2, row["reads"]["sessions"])  # the commit that only violated it read nothing
        self.assertEqual("2026-08-30", row["violations"]["last"])

    def test_read_and_violated_once_is_watch_not_working(self) -> None:
        rows = self.rows([block("s1", read="p-1a2b3c4d", violated="p-1a2b3c4d")])
        self.assertEqual("watch", rows["p-1a2b3c4d"]["class"])

    def test_violated_without_ever_being_read_is_undiscoverable(self) -> None:
        rows = self.rows([block("s1", violated="p-99887766")])
        self.assertEqual("undiscoverable", rows["p-99887766"]["class"])
        self.assertIn("discovery problem", rows["p-99887766"]["action"])

    def test_silence_over_the_floor_splits_by_tests(self) -> None:
        rows = self.rows(self.padding(self.FLOOR))
        self.assertEqual("redundant", rows["p-1a2b3c4d"]["class"])        # has `**Tests:**`
        self.assertEqual("decay-candidate", rows["p-99887766"]["class"])  # prose only

    def test_silence_under_the_floor_is_unclassified(self) -> None:
        rows = self.rows(self.padding(self.FLOOR - 1))
        self.assertEqual("unclassified", rows["p-1a2b3c4d"]["class"])
        self.assertEqual("unclassified", rows["p-99887766"]["class"])

    def test_an_id_the_tree_lost_is_its_own_row(self) -> None:
        rows = self.rows([block("s1", read="p-deadbeef", violated="p-deadbeef")])
        self.assertIsNone(rows["p-deadbeef"]["path"])
        self.assertEqual("watch", rows["p-deadbeef"]["class"])

    def test_co_read_pairs_need_two_commits(self) -> None:
        pairs = agent_telemetry.aggregate_commits(
            [block("s1", read="p-1a2b3c4d p-99887766"), block("s2", read="p-1a2b3c4d p-cafed00d")], UNIVERSE
        )["co_read_pairs"]
        self.assertEqual([], pairs)
        pairs = agent_telemetry.aggregate_commits(
            [block("s1", read="p-1a2b3c4d p-99887766"), block("s2", read="p-99887766 p-1a2b3c4d")], UNIVERSE
        )["co_read_pairs"]
        self.assertEqual([{"ids": ["p-1a2b3c4d", "p-99887766"],
                           "titles": ["Indexed premise", "Another indexed premise"], "commits": 2}], pairs)

    def test_whole_file_reads_are_counted_by_path_not_credited_to_every_premise(self) -> None:
        aggregated = agent_telemetry.aggregate_commits(
            [block("s1", files="docs/shipping/premises.md")], UNIVERSE
        )
        self.assertEqual([{"path": "docs/shipping/premises.md", "commits": 1}], aggregated["files"])
        self.assertTrue(all(item["reads"]["commits"] == 0 for item in aggregated["premises"]))


class CommitSources(unittest.TestCase):
    """The two sources, on a throwaway git repository: the branch log and `gh pr list`."""

    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp(prefix="telemetry-commits-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.bin = tempfile.mkdtemp(prefix="telemetry-bin-")
        self.addCleanup(shutil.rmtree, self.bin, True)
        # PATH is narrowed to this one directory while mining, so `test_gh_absent…` cannot reach the
        # real `gh` of the machine. `git` is linked in because the miner needs it — the fake dir is the
        # whole world, not a prefix of it.
        os.symlink(shutil.which("git"), os.path.join(self.bin, "git"))
        self.git("init", "-q")
        self.git("symbolic-ref", "HEAD", "refs/heads/main")
        for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.com"),
                           ("commit.gpgsign", "false"), ("gc.auto", "0"), ("maintenance.auto", "false")):
            self.git("config", "--local", key, value)

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.repo, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)

    def commit(self, message: str) -> None:
        with open(os.path.join(self.repo, "file.txt"), "a", encoding="utf-8") as handle:
            handle.write(message[:20] + "\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def fake_gh(self, payload) -> None:
        """A fake `gh` answering the two calls the miner makes: `pr list --json number` (the numbers)
        and `pr view <n> --json commits` (that PR's commits). Absolute shebang and only SHELL BUILTINS
        in the body — no `cat`, no `printf` from /usr/bin — so it runs with PATH = the fake dir alone,
        which is what makes `test_gh_absent…` honest."""
        path = os.path.join(self.bin, "gh")
        numbers = json.dumps([{"number": pull["number"]} for pull in payload]).replace("'", "'\\''")
        script = ["#!/bin/sh", 'case "$2" in', "list)"]
        # `printf '%s'`, never `echo`: /bin/sh on macOS expands the `\n` inside the JSON strings and
        # the payload stops being valid JSON (this test caught it).
        script.append("printf '%s\\n' '" + numbers + "' ;;")
        script.append("view)")
        script.append('case "$3" in')
        for pull in payload:
            body = json.dumps({"commits": pull["commits"]}).replace("'", "'\\''")
            script.append("%s) printf '%%s\\n' '%s' ;;" % (pull["number"], body))
        script += ["*) echo 'null' ;;", "esac ;;", "esac"]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(script) + "\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    def mine(self, prs: str = "none") -> dict:
        previous = os.environ["PATH"]
        os.environ["PATH"] = self.bin
        try:
            return agent_telemetry.build_commits(
                self.repo, "main", WIDE_SINCE, WIDE_UNTIL, prs, universe=UNIVERSE
            )
        finally:
            os.environ["PATH"] = previous

    @staticmethod
    def pr_payload(message: str, number: int = 42, oid: str = "abc123") -> list:
        headline, _, body = message.partition("\n\n")
        return [{"number": number, "commits": [{"oid": oid, "messageHeadline": headline, "messageBody": body,
                                                "authors": [{"name": "Fixture"}],
                                                "committedDate": "2026-08-29T10:00:00Z"}]}]

    def test_branch_log_reads_the_trailers_out_of_a_squash_body(self) -> None:
        self.commit(SQUASH_BODY)
        mined = self.mine()
        self.assertEqual(3, mined["totals"]["commits"])
        self.assertEqual(2, mined["totals"]["sessions"])
        self.assertEqual(1, mined["totals"]["violations"])
        rows = {item["id"]: item for item in mined["premises"]}
        self.assertEqual(2, rows["p-1a2b3c4d"]["reads"]["commits"])
        self.assertEqual("watch", rows["p-99887766"]["class"])

    def test_a_missing_branch_is_a_note_not_a_crash(self) -> None:
        self.commit(SQUASH_BODY)
        mined = agent_telemetry.build_commits(
            self.repo, "nope", WIDE_SINCE, WIDE_UNTIL, "none", universe=UNIVERSE
        )
        self.assertEqual(0, mined["totals"]["commits"])
        self.assertIn("does not exist", mined["sources"][0]["note"])

    def test_open_pr_commits_join_the_branch_log(self) -> None:
        self.commit("chore: nothing here\n")
        self.fake_gh(self.pr_payload(
            "fix(catalog): fix the split\n\nBody.\n\nPremises-Violated: p-1a2b3c4d\nAgent-Session: session-pr\n"
        ))
        mined = self.mine("open")
        self.assertEqual(1, mined["totals"]["commits"])
        self.assertEqual({item["id"]: item for item in mined["premises"]}["p-1a2b3c4d"]["violations"]["commits"], 1)
        self.assertEqual("prs", mined["sources"][1]["source"])
        self.assertEqual(1, mined["sources"][1]["commits"])

    def test_the_same_commit_in_both_sources_counts_once(self) -> None:
        """The squash-double-count risk: `--prs open` is the mitigation, this is the proof it holds even
        when a branch commit and a PR commit carry the very same trailer block."""
        self.commit("feat: first\n\nPremises-Read: p-1a2b3c4d\nAgent-Session: session-dup\n")
        self.fake_gh(self.pr_payload("feat: first\n\nPremises-Read: p-1a2b3c4d\nAgent-Session: session-dup\n"))
        mined = self.mine("open")
        self.assertEqual(1, mined["totals"]["commits"])
        self.assertEqual(1, mined["sources"][1]["duplicates_skipped"])
        self.assertEqual(1, {item["id"]: item for item in mined["premises"]}["p-1a2b3c4d"]["reads"]["commits"])

    def test_two_commits_of_one_session_in_the_same_pr_both_count(self) -> None:
        """The dedupe is ACROSS sources, never inside one. Two commits of a session that read no
        premise carry an identical trailer block — collapsing them undercounts the window, which is
        what the first live run of this miner did to its own first two annotated commits."""
        self.commit("chore: nothing here\n")
        payload = self.pr_payload("feat: one\n\nAgent-Session: s-same\n", number=7)
        payload[0]["commits"].append({"oid": "def456", "messageHeadline": "feat: two",
                                      "messageBody": "Agent-Session: s-same\n",
                                      "authors": [{"name": "Fixture"}],
                                      "committedDate": "2026-08-30T10:00:00Z"})
        self.fake_gh(payload)
        mined = self.mine("open")
        self.assertEqual(2, mined["totals"]["commits"])
        self.assertEqual(0, mined["sources"][1]["duplicates_skipped"])
        self.assertEqual(1, mined["totals"]["sessions"])

    def test_a_pr_whose_commits_cannot_be_read_is_a_note_not_a_crash(self) -> None:
        """The numbers listed fine and one `pr view` failed — the other PRs still count. The live run
        hit the sibling of this: `pr list --json commits` asks GitHub for `PRs x commits x authors`
        nodes in ONE query and is rejected over 500 000, which is why the numbers and the commits are
        two calls."""
        self.commit("chore: nothing here\n")
        self.fake_gh(self.pr_payload("feat: x\n\nAgent-Session: s-pr\n", number=7))
        # The list is patched to name a PR the fake cannot answer `pr view` for.
        path = os.path.join(self.bin, "gh")
        with open(path, encoding="utf-8") as handle:
            script = handle.read()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script.replace('[{"number": 7}]', '[{"number": 7}, {"number": 8}]'))
        mined = self.mine("open")
        self.assertEqual(1, mined["totals"]["commits"])   # PR 7 still counted
        self.assertIn("#8", mined["sources"][1]["note"])  # PR 8 named in the note

    def test_the_note_names_the_real_gh_subcommand(self) -> None:
        """A note is read by a human deciding whether to re-run: it has to name a command that EXISTS.
        `gh list`/`gh view` are not commands; `gh pr list`/`gh pr view` are."""
        path = os.path.join(self.bin, "gh")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\necho 'boom' >&2\nexit 1\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
        self.commit("chore: nothing here\n")
        note = self.mine("open")["sources"][1]["note"]
        self.assertIn("`gh pr list` failed", note)
        self.assertNotIn("`gh list`", note)

    def test_gh_absent_from_the_path_is_a_note_not_a_failure(self) -> None:
        self.commit("feat: first\n\nPremises-Read: p-1a2b3c4d\nAgent-Session: session-1\n")
        mined = self.mine("open")  # self.bin holds no `gh`
        self.assertEqual(1, mined["totals"]["commits"])
        self.assertIn("not on the PATH", mined["sources"][1]["note"])

    def test_markdown_carries_the_matrix_and_the_flagged_lists(self) -> None:
        self.commit(SQUASH_BODY)
        rendered = agent_telemetry.render_commits(self.mine())
        self.assertIn("| class | premises | action |", rendered)
        self.assertIn("| watch | 1 |", rendered)
        self.assertIn("## Confusing and undiscoverable", rendered)
        self.assertIn("`p-99887766`", rendered)
        self.assertIn("## Co-read pairs", rendered)


class CommitsDocument(Harness):
    def commits_section(self) -> dict:
        return agent_telemetry.build_commits(
            self.repo, "main", datetime.date(2026, 6, 1), datetime.date(2026, 8, 30), "none", universe=UNIVERSE
        )

    def v2_report(self) -> dict:
        payload = self.payload(
            hook_event_name="InstructionsLoaded",
            file_path=os.path.join(self.repo, "CLAUDE.md"),
            load_reason="session_start",
        )
        return self.report(self.records(payload))

    def test_v3_is_a_superset_of_v2(self) -> None:
        report = self.v2_report()
        path = os.path.join(self.repo, "report.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle)
        document = agent_telemetry.commits_document(self.commits_section(), path)
        self.assertEqual(3, document["schema_version"])
        for key in ("window", "surfaces", "premises", "candidates"):
            self.assertEqual(report[key], document[key], key)
        self.assertEqual(sorted(document["commits"]),
                         ["classes", "co_read_pairs", "files", "premises", "sources", "totals", "window"])
        json.loads(json.dumps(document))

    def test_v3_without_a_report_leaves_the_v2_half_empty(self) -> None:
        document = agent_telemetry.commits_document(self.commits_section(), None)
        self.assertEqual(3, document["schema_version"])
        self.assertEqual([], document["surfaces"])
        self.assertEqual(0, document["window"]["sessions"])

    def test_the_report_subcommand_still_emits_2(self) -> None:
        self.assertEqual(2, self.v2_report()["schema_version"])

    def test_render_markdown_of_a_v3_document_appends_the_matrix(self) -> None:
        document = dict(self.v2_report())
        document["commits"] = self.commits_section()
        rendered = agent_telemetry.render_markdown(document)
        self.assertIn("## Decay / demotion candidates", rendered)
        self.assertIn("# Premise quality — read x violated", rendered)

    def test_premise_sections_carry_the_tests_flag(self) -> None:
        write(self.repo, {"docs/catalog/premises-tested.md":
                          "# Catalog\n\n## Gated one\n**Id:** p-11112222\n**Tests:** `CatalogTest`\n\n"
                          "## Prose one\n**Id:** p-33334444\nJust prose.\n"})
        universe = {item[2]: item[3] for item in agent_telemetry.premises_universe(self.repo, self.config)}
        self.assertTrue(universe["p-11112222"])
        self.assertFalse(universe["p-33334444"])


if __name__ == "__main__":
    unittest.main()
