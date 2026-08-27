#!/usr/bin/env python3
"""Red cases for lint_ratchet.py — a gate never seen red proves nothing.

Every test builds a throwaway git repository with a base commit, moves the branch, and runs the
script's own entry point. The red cases are the three shapes that shipped in the wild (a loosened
threshold riding along with production code, a baseline growing in the same change, and the same
thing in an UNTRACKED file), plus a pasted block for the CPD gate.

    python3 -m unittest discover -s ci -p 'lint_ratchet_test.py'
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

import lint_ratchet  # noqa: E402

CONFIG = (
    "# Agent skills config\n"
    "\n"
    "## Lint ratchet\n"
    "\n"
    "- **Lint config files:** `config/lint.yml`\n"
    "- **Baseline files:** `config/baseline-*.xml`\n"
    "- **Production globs:** `*/src/main/*.kt`\n"
    "- **CPD command:** `./gradlew cpd`\n"
    "- **CPD report:** `build/reports/cpd/cpd.xml`\n"
)

BASELINE_TWO = "<SmellBaseline>\n  <ID>A</ID>\n  <ID>B</ID>\n</SmellBaseline>\n"
BASELINE_THREE = "<SmellBaseline>\n  <ID>A</ID>\n  <ID>B</ID>\n  <ID>C</ID>\n</SmellBaseline>\n"
BASELINE_ONE = "<SmellBaseline>\n  <ID>A</ID>\n</SmellBaseline>\n"

PMD7 = 'https://pmd-code.org/schema/cpd-report'


def duplication_report(repo: str, occurrences: list, namespace: str | None = PMD7) -> str:
    """A CPD XML report with one duplication over the given (path, line, endline) occurrences."""
    open_tag = "<pmd-cpd" + (f' xmlns="{namespace}"' if namespace else "") + ">"
    files = "".join(
        '<file path="%s" line="%d" endline="%d"/>' % (os.path.join(repo, path), line, endline)
        for path, line, endline in occurrences
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + open_tag
        + '<duplication lines="6" tokens="120">'
        + files
        + "<codefragment>fun copy() {}</codefragment>"
        + "</duplication></pmd-cpd>\n"
    )


class Repo(unittest.TestCase):
    config_text: str | None = CONFIG

    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp(prefix="ratchet-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        if self.config_text is not None:
            self.write("docs/agents/skills-config.md", self.config_text)
        self.write("config/lint.yml", "threshold: 15\n")
        self.write("config/baseline-core.xml", BASELINE_TWO)
        self.write("core/src/main/Kernel.kt", "fun kernel() = 1\n")
        self.write("core/src/test/KernelTest.kt", "fun test() = 1\n")
        self.commit("base")

    # ── helpers ───────────────────────────────────────────────────────────────────────────────

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, check=False,
        ).stdout

    def write(self, relative: str, content: str) -> None:
        path = os.path.join(self.repo, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def invoke(self, *argv: str) -> tuple:
        stdout, sys.stdout = sys.stdout, io.StringIO()
        try:
            code = lint_ratchet.main(["--repo", self.repo, *argv])
            printed = sys.stdout.getvalue()
        finally:
            sys.stdout = stdout
        return code, printed

    def rides(self, *argv: str) -> tuple:
        return self.invoke("config-rides-alone", "--base", "main", *argv)


class ConfigRidesAlone(Repo):
    def test_red_loosened_threshold_plus_production_code(self) -> None:
        self.git("checkout", "-q", "-b", "feature")
        self.write("config/lint.yml", "threshold: 30\n")
        self.write("core/src/main/Kernel.kt", "fun kernel() = 2\n")
        self.commit("feature")
        code, printed = self.rides()
        self.assertEqual(code, 1)
        self.assertIn("config/lint.yml (thresholds)", printed)
        self.assertIn("core/src/main/Kernel.kt", printed)

    def test_red_baseline_growing_plus_production_code(self) -> None:
        self.git("checkout", "-q", "-b", "feature")
        self.write("config/baseline-core.xml", BASELINE_THREE)
        self.write("core/src/main/Kernel.kt", "fun kernel() = 2\n")
        self.commit("feature")
        code, printed = self.rides()
        self.assertEqual(code, 1)
        self.assertIn("config/baseline-core.xml (2 -> 3 entries)", printed)

    def test_red_when_the_production_file_is_still_untracked(self) -> None:
        # The head is the worktree: the gate has to bite before the commit exists.
        self.git("checkout", "-q", "-b", "feature")
        self.write("config/lint.yml", "threshold: 30\n")
        self.write("core/src/main/New.kt", "fun brand() = 1\n")
        code, printed = self.rides()
        self.assertEqual(code, 1)
        self.assertIn("core/src/main/New.kt", printed)

    def test_green_when_the_baseline_shrinks(self) -> None:
        self.git("checkout", "-q", "-b", "feature")
        self.write("config/baseline-core.xml", BASELINE_ONE)
        self.write("core/src/main/Kernel.kt", "fun kernel() = 2\n")
        self.commit("feature")
        code, printed = self.rides()
        self.assertEqual(code, 0)
        self.assertIn("config-rides-alone OK", printed)

    def test_green_when_the_ratchet_change_travels_alone(self) -> None:
        self.git("checkout", "-q", "-b", "feature")
        self.write("config/lint.yml", "threshold: 30\n")
        self.commit("feature")
        self.assertEqual(self.rides()[0], 0)

    def test_green_when_only_tests_change_beside_the_threshold(self) -> None:
        self.git("checkout", "-q", "-b", "feature")
        self.write("config/lint.yml", "threshold: 30\n")
        self.write("core/src/test/KernelTest.kt", "fun test() = 2\n")
        self.commit("feature")
        self.assertEqual(self.rides()[0], 0)

    def test_green_with_no_base_ref_at_all(self) -> None:
        code, printed = self.invoke("config-rides-alone")
        self.assertEqual(code, 0)
        self.assertIn("no base ref", printed)

    def test_red_when_a_base_ref_was_passed_and_does_not_resolve(self) -> None:
        code, printed = self.invoke("config-rides-alone", "--base", "origin/never-fetched")
        self.assertEqual(code, 1)
        self.assertIn("does not resolve", printed)


class NotConfigured(Repo):
    config_text = None

    def test_both_gates_are_off_without_production_globs(self) -> None:
        self.git("checkout", "-q", "-b", "feature")
        self.write("config/lint.yml", "threshold: 30\n")
        self.write("core/src/main/Kernel.kt", "fun kernel() = 2\n")
        self.commit("feature")
        code, printed = self.rides()
        self.assertEqual(code, 0)
        self.assertIn("no `Production globs`", printed)
        code, printed = self.invoke("cpd-delta", "--base", "main")
        self.assertEqual(code, 0)
        self.assertIn("no `Production globs`", printed)


class NoBaselineFiles(Repo):
    config_text = (
        "# Agent skills config\n"
        "\n"
        "## Lint ratchet\n"
        "\n"
        "- **Production globs:** `*/src/main/*.kt`\n"
    )

    def test_the_gate_says_it_has_nothing_to_watch(self) -> None:
        code, printed = self.rides()
        self.assertEqual(code, 0)
        self.assertIn("no lint config files and no baseline files", printed)


class CustomEntryPattern(Repo):
    config_text = (
        "# Agent skills config\n"
        "\n"
        "## Lint ratchet\n"
        "\n"
        "- **Baseline files:** `config/suppressions.txt`\n"
        "- **Baseline entry pattern:** `(?m)^\\S`\n"
        "- **Production globs:** `*/src/main/*.kt`\n"
    )

    def test_a_line_per_entry_baseline_is_counted_by_the_configured_regex(self) -> None:
        self.write("config/suppressions.txt", "rule-a\nrule-b\n")
        self.commit("suppressions")
        self.git("checkout", "-q", "-b", "feature")
        self.write("config/suppressions.txt", "rule-a\nrule-b\nrule-c\n")
        self.write("core/src/main/Kernel.kt", "fun kernel() = 2\n")
        self.commit("feature")
        code, printed = self.invoke("config-rides-alone", "--base", "main")
        self.assertEqual(code, 1)
        self.assertIn("(2 -> 3 entries)", printed)


class CpdDelta(Repo):
    def report(self, occurrences: list, namespace: str | None = PMD7) -> str:
        path = os.path.join(self.repo, "build/reports/cpd/cpd.xml")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(duplication_report(self.repo, occurrences, namespace))
        return "build/reports/cpd/cpd.xml"

    def test_a_legacy_duplication_passes_untouched(self) -> None:
        self.write("core/src/main/Legacy.kt", "\n".join("line %d" % i for i in range(1, 40)) + "\n")
        self.write("core/src/main/Other.kt", "\n".join("line %d" % i for i in range(1, 40)) + "\n")
        self.commit("legacy duplication")
        self.git("checkout", "-q", "-b", "feature")
        self.write("core/src/main/Kernel.kt", "fun kernel() = 2\n")
        self.commit("feature")
        self.report([("core/src/main/Legacy.kt", 1, 20), ("core/src/main/Other.kt", 1, 20)])
        code, printed = self.invoke("cpd-delta", "--base", "main")
        self.assertEqual(code, 0)
        self.assertIn("cpd-delta OK", printed)

    def test_red_when_this_branch_pasted_the_block(self) -> None:
        self.write("core/src/main/Legacy.kt", "\n".join("line %d" % i for i in range(1, 40)) + "\n")
        self.commit("legacy")
        self.git("checkout", "-q", "-b", "feature")
        self.write("core/src/main/Pasted.kt", "\n".join("line %d" % i for i in range(1, 40)) + "\n")
        self.commit("paste")
        self.report([("core/src/main/Legacy.kt", 1, 20), ("core/src/main/Pasted.kt", 1, 20)])
        code, printed = self.invoke("cpd-delta", "--base", "main")
        self.assertEqual(code, 1)
        self.assertIn("core/src/main/Pasted.kt:1-20 (20/20 lines new)", printed)
        self.assertIn("fun copy() {}", printed)

    def test_a_one_line_fix_inside_a_legacy_block_is_not_a_new_duplication(self) -> None:
        body = ["line %d" % i for i in range(1, 40)]
        self.write("core/src/main/Legacy.kt", "\n".join(body) + "\n")
        self.write("core/src/main/Other.kt", "\n".join(body) + "\n")
        self.commit("legacy")
        self.git("checkout", "-q", "-b", "feature")
        body[4] = "line five fixed"
        self.write("core/src/main/Legacy.kt", "\n".join(body) + "\n")
        self.commit("one-line fix")
        self.report([("core/src/main/Legacy.kt", 1, 20), ("core/src/main/Other.kt", 1, 20)])
        self.assertEqual(self.invoke("cpd-delta", "--base", "main")[0], 0)

    def test_a_pure_move_is_not_new_code(self) -> None:
        body = "\n".join("line %d" % i for i in range(1, 40)) + "\n"
        self.write("core/src/main/Legacy.kt", body)
        self.write("core/src/main/Other.kt", body)
        self.commit("legacy")
        self.git("checkout", "-q", "-b", "feature")
        self.git("mv", "core/src/main/Legacy.kt", "core/src/main/Moved.kt")
        self.commit("move")
        self.report([("core/src/main/Moved.kt", 1, 20), ("core/src/main/Other.kt", 1, 20)])
        self.assertEqual(self.invoke("cpd-delta", "--base", "main")[0], 0)

    def test_a_pmd6_report_without_a_namespace_parses_too(self) -> None:
        self.write("core/src/main/Legacy.kt", "\n".join("line %d" % i for i in range(1, 40)) + "\n")
        self.commit("legacy")
        self.git("checkout", "-q", "-b", "feature")
        self.write("core/src/main/Pasted.kt", "\n".join("line %d" % i for i in range(1, 40)) + "\n")
        self.commit("paste")
        self.report([("core/src/main/Legacy.kt", 1, 20), ("core/src/main/Pasted.kt", 1, 20)], namespace=None)
        self.assertEqual(self.invoke("cpd-delta", "--base", "main")[0], 1)

    def test_a_missing_report_fails_loudly_and_names_the_command(self) -> None:
        code, printed = self.invoke("cpd-delta", "--base", "main")
        self.assertEqual(code, 1)
        self.assertIn("CPD report not found", printed)
        self.assertIn("./gradlew cpd", printed)


if __name__ == "__main__":
    unittest.main()
