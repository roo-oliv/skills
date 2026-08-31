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


if __name__ == "__main__":
    unittest.main()
