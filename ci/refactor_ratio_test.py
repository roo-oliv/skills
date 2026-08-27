#!/usr/bin/env python3
"""Cases for refactor_ratio.py — a tripwire that reports the wrong number is worse than none.

A throwaway git repository is built with commits dated into two different months, one of them a
`refactor:`, one a pure `git mv`, and one touching a file outside the production globs.

    python3 -m unittest discover -s ci -p 'refactor_ratio_test.py'
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import refactor_ratio  # noqa: E402

CONFIG = (
    "# Agent skills config\n"
    "\n"
    "## Lint ratchet\n"
    "\n"
    "- **Production globs:** `*/src/main/*.kt`\n"
)


class Harness(unittest.TestCase):
    config_text: str | None = CONFIG

    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp(prefix="ratio-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        if self.config_text is not None:
            self.write("docs/agents/skills-config.md", self.config_text)
        self.write("core/src/main/A.kt", "\n".join("a%d" % i for i in range(10)) + "\n")
        self.commit("feat: ten lines", "2026-06-10T12:00:00")

    def git(self, *args: str, env: dict | None = None) -> str:
        environment = dict(os.environ, **(env or {}))
        return subprocess.run(
            ["git", *args], cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=False, env=environment,
        ).stdout

    def write(self, relative: str, content: str) -> None:
        path = os.path.join(self.repo, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def commit(self, message: str, when: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message,
                 env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})

    def collect(self) -> tuple:
        globs = ["*/src/main/*.kt"] if self.config_text else []
        return refactor_ratio.collect(self.repo, None, globs)

    def invoke(self, *argv: str) -> str:
        stdout, sys.stdout = sys.stdout, io.StringIO()
        try:
            self.assertEqual(refactor_ratio.main(["--repo", self.repo, *argv]), 0)
            return sys.stdout.getvalue()
        finally:
            sys.stdout = stdout


class Ratio(Harness):
    def test_lines_are_bucketed_by_committer_month(self) -> None:
        self.write("core/src/main/A.kt", "\n".join("a%d" % i for i in range(14)) + "\n")
        self.commit("feat: four more", "2026-07-10T12:00:00")
        every, _ = self.collect()
        self.assertEqual((every["2026-06"].added, every["2026-06"].deleted), (10, 0))
        self.assertEqual((every["2026-07"].added, every["2026-07"].deleted), (4, 0))

    def test_a_refactor_commit_lands_in_both_columns(self) -> None:
        self.write("core/src/main/A.kt", "\n".join("a%d" % i for i in range(4)) + "\n")
        self.commit("refactor: drop six", "2026-07-10T12:00:00")
        every, refactors = self.collect()
        self.assertEqual((every["2026-07"].added, every["2026-07"].deleted), (0, 6))
        self.assertEqual((refactors["2026-07"].added, refactors["2026-07"].deleted), (0, 6))
        self.assertEqual(every["2026-06"].added, 10)
        self.assertNotIn("2026-06", refactors)

    def test_a_file_outside_the_production_globs_is_not_counted(self) -> None:
        self.write("core/src/test/ATest.kt", "\n".join("t%d" % i for i in range(30)) + "\n")
        self.commit("test: thirty lines", "2026-07-10T12:00:00")
        every, _ = self.collect()
        self.assertNotIn("2026-07", every)

    def test_a_pure_move_is_not_counted_as_writing(self) -> None:
        self.git("mv", "core/src/main/A.kt", "core/src/main/B.kt")
        self.commit("refactor: move it", "2026-07-10T12:00:00")
        every, _ = self.collect()
        # `-M` reports the rename as a 0/0 row: the month exists, the writing does not.
        self.assertEqual((every["2026-07"].added, every["2026-07"].deleted), (0, 0))

    def test_the_report_marks_the_current_month_partial_and_always_exits_zero(self) -> None:
        printed = self.invoke("--months", "3", "--until", "2026-07-15")
        self.assertIn("2026-07 (partial)", printed)
        self.assertIn("2026-06", printed)
        self.assertIn("2026-05", printed)
        self.assertNotIn("2026-08", printed)
        self.assertIn("Tripwire, not a KPI", printed)

    def test_the_scope_line_names_the_configured_globs(self) -> None:
        self.assertIn("`*/src/main/*.kt`", self.invoke("--months", "1", "--until", "2026-07-15"))

    def test_a_ratio_with_no_deletions_renders_as_infinity(self) -> None:
        bucket = refactor_ratio.Bucket()
        bucket.add(10, 0)
        self.assertEqual(refactor_ratio.cell(bucket), ("10", "0", "∞"))
        self.assertEqual(refactor_ratio.cell(None), ("—", "—", "—"))

    def test_months_ending_at_walks_backwards_over_a_year_boundary(self) -> None:
        self.assertEqual(
            refactor_ratio.months_ending_at("2026-02", 4), ["2025-11", "2025-12", "2026-01", "2026-02"]
        )


class NoProductionGlobs(Harness):
    config_text = None

    def test_with_no_globs_the_whole_tree_is_measured_and_the_report_says_so(self) -> None:
        self.write("notes/todo.md", "one line\n")
        self.commit("docs: a note", "2026-07-10T12:00:00")
        every, _ = self.collect()
        self.assertEqual(every["2026-07"].added, 1)
        self.assertIn("no production globs configured", self.invoke("--until", "2026-07-15"))


if __name__ == "__main__":
    unittest.main()
