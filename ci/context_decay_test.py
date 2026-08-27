#!/usr/bin/env python3
"""Red cases for every context_decay signal — a scan never seen naming a candidate proves nothing.

Each test builds a throwaway git repository (an instructions file, two rules, a skill, a premises file,
a dated ephemeral doc and a valid recurring-failure-modes file), breaks exactly one thing, and asserts
which signal comes back on which path. `--today` is injected so every run is deterministic.

    python3 -m unittest discover -s ci -p '*_test.py'
"""

from __future__ import annotations

import atexit
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import context_decay  # noqa: E402

TODAY = dt.date(2020, 2, 1)  # 31 days after the dated ephemeral doc: inside the 90-day window

CONFIG = """# Agent skills config

## Docs layout

- **Premises:** `docs/{domain}/premises.md`
- **Premises index:** `docs/{domain}/premises-index.md`
- **Planning** (optional): recurring-failure-modes `docs/planning/recurring-failure-modes.md`

## Context toolkit

- **Agent instructions file:** `CLAUDE.md`
- **Source globs:** `*.ts`

## Intent

- **Intent dir:** `intent`

## Domains

| Domain | Detect (path globs) | Prompt terms |
|---|---|---|
| catalog | `src/catalog/**` | catalogue, sku |
| shipping | `src/shipping/**` | |

## Sensitive domains

catalog
"""

FIXTURE = {
    "CLAUDE.md": "# Fixture kernel\n\nThe docs index is `docs/index.md`.\n",
    "docs/agents/skills-config.md": CONFIG,
    ".claude/rules/scoped.md": (
        "---\ndescription: Scoped fixture rule\npaths:\n"
        '  - "src/catalog/**"\n  - "docs/**/*.md"\n---\n\nDo the thing.\n'
    ),
    ".claude/rules/always.md": "---\ndescription: Always-on fixture rule\n---\n\nDo the other thing.\n",
    ".claude/skills/x/SKILL.md": "---\nname: x\ndescription: Fixture skill\n---\n\nDo nothing.\n",
    "docs/index.md": (
        "# Index\n\n"
        "- [catalog premises](catalog/premises.md)\n"
        "- [the config](agents/skills-config.md)\n"
        "- [failure modes](planning/recurring-failure-modes.md)\n"
        "- [old plan](work/plans/2020-01-01-old.md)\n"
    ),
    "docs/catalog/premises.md": (
        "# Catalog Premises\n\n"
        "## CatalogService writes only under lock\n\n"
        "`CatalogService` writes only under the row lock.\n"
        "**Why:** the invariant would be unenforced otherwise.\n"
        "**Breaks:** the ledger of reserved stock drifts.\n"
        "**Tests:** `CatalogServiceTest`\n\n"
        "## A price is stored in minor units\n\n"
        "One line of rationale.\n"
        "**Why:** it holds.\n"
        "**Breaks:** nothing downstream.\n"
        "**Tests:** `CatalogServiceTest`\n"
    ),
    "docs/planning/recurring-failure-modes.md": (
        "# Recurring failure modes\n\n## How entries get here\n\nProse.\n\n"
        "## FM-1 — Fixture class one\n\n**Mined from:** #201, #202.\n**Occurrences:** 1 · **State:** advisory\n"
        "**Trigger:** anything.\n"
    ),
    "docs/work/plans/2020-01-01-old.md": "# Old plan\n\nIt described a rollout of the day.\n",
    "src/catalog/CatalogService.ts": "export class CatalogService {\n  run() {}\n}\n",
    "src/catalog/CatalogServiceTest.ts": "export class CatalogServiceTest {\n  runs() {}\n}\n",
}


def build_template() -> str:
    repo = tempfile.mkdtemp(prefix="context-decay-template-")
    atexit.register(shutil.rmtree, repo, True)

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)

    run("init", "-q")
    run("symbolic-ref", "HEAD", "refs/heads/main")
    for key, value in (
        ("user.name", "Fixture"),
        ("user.email", "fixture@example.com"),
        ("commit.gpgsign", "false"),
        ("core.excludesFile", os.devnull),
        ("gc.auto", "0"),
        ("maintenance.auto", "false"),
    ):
        run("config", "--local", key, value)
    for path, content in FIXTURE.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    return repo


TEMPLATE = build_template()


class ContextDecayTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp(prefix="context-decay-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        # `*.lock`: git's background maintenance can create and delete `.git/objects/maintenance.lock`
        # under the template while copytree walks it, and the vanished entry aborts the copy.
        shutil.copytree(
            TEMPLATE, self.repo, dirs_exist_ok=True, symlinks=True, ignore=shutil.ignore_patterns("*.lock")
        )

    # ── helpers ───────────────────────────────────────────────────────────────────────────────────

    def write(self, path: str, content: str) -> None:
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)

    def read(self, path: str) -> str:
        with open(os.path.join(self.repo, path), encoding="utf-8") as handle:
            return handle.read()

    def append(self, path: str, text: str) -> None:
        self.write(path, self.read(path) + text)

    def commit(self, message: str = "change") -> None:
        """`git ls-files` only sees tracked paths — every mutation is committed before a scan.
        A no-op commit is fine (a scan may follow another scan with nothing in between)."""
        subprocess.run(["git", "add", "-A"], cwd=self.repo, stdout=subprocess.DEVNULL, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", message], cwd=self.repo, stdout=subprocess.DEVNULL, check=False
        )

    def telemetry_file(self, payload: dict) -> str:
        # Outside the repo on purpose: the report is an input to the scan, never a surface of it.
        outside = tempfile.mkdtemp(prefix="context-decay-telemetry-")
        self.addCleanup(shutil.rmtree, outside, True)
        path = os.path.join(outside, "telemetry.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def scan(self, since: int = 90, telemetry: str | None = None, today: dt.date = TODAY):
        self.commit()
        return context_decay.scan(self.repo, since, telemetry, today)

    @staticmethod
    def signals(report, label: str) -> list[str]:
        for candidate in report.candidates:
            if candidate.label == label:
                return candidate.signals
        return []

    @staticmethod
    def render(report) -> str:
        return context_decay.render(report)


class GreenTest(ContextDecayTestCase):
    def test_green_fixture_lists_nothing(self) -> None:
        report = self.scan()
        self.assertEqual([], report.candidates, self.render(report))
        self.assertEqual([], report.promotions, self.render(report))
        self.assertIn("telemetry: absent", self.render(report))


class SignalTest(ContextDecayTestCase):
    def test_d1_superseded_banner_is_listed(self) -> None:
        self.write(
            "docs/work/plans/2020-01-01-old.md",
            "# Old plan\n\n> **2020-01-20:** superseded — docs/catalog/premises.md\n\nBody.\n",
        )
        report = self.scan()
        self.assertIn("D1", self.signals(report, "docs/work/plans/2020-01-01-old.md"), self.render(report))
        self.assertIn("delete (facts in: docs/catalog/premises.md)", self.render(report))

    def test_d2_broken_reference_is_listed_but_c10_framework_symbol_is_not(self) -> None:
        # Control first: a symbol the identifier index cannot see is C10 — WARN by design, never decay.
        self.append("docs/index.md", "\nThe runtime gives us a `ThreadPoolTaskScheduler`.\n")
        report = self.scan()
        self.assertEqual([], report.candidates, self.render(report))

        self.append("docs/index.md", "\nThe entry point is `src/catalog/Missing.ts`.\n")
        report = self.scan()
        self.assertIn("D2", self.signals(report, "docs/index.md"), self.render(report))

        # The root surfaces are in the universe too: context_lint checks C6–C14 on them, so a broken
        # reference there is a decay candidate like any other.
        self.append("CLAUDE.md", "\nThe kernel points at `src/catalog/Absent.ts`.\n")
        report = self.scan()
        self.assertIn("D2", self.signals(report, "CLAUDE.md"), self.render(report))

    def test_d3_zero_loads_only_with_a_report(self) -> None:
        payload = {
            "schema_version": 1,
            "window": {"since": "2020-01-01", "until": "2020-02-01", "sessions": 30},
            "surfaces": [{"path": "docs/catalog/premises.md", "kind": "doc", "loads": 0, "candidate": "zero-load"}],
        }
        report = self.scan()  # no --telemetry: no D3 at all, and the summary says so
        self.assertEqual([], report.candidates, self.render(report))
        self.assertIn("telemetry: absent", self.render(report))
        self.assertIn("D3 not evaluated", self.render(report))

        report = self.scan(telemetry=self.telemetry_file(payload))
        self.assertIn("D3", self.signals(report, "docs/catalog/premises.md"), self.render(report))

        payload["schema_version"] = 7
        report = self.scan(telemetry=self.telemetry_file(payload))
        self.assertEqual([], report.candidates, self.render(report))
        self.assertIn("outside", self.render(report))

    def test_d3_premise_zero_load_only_when_file_is_loaded(self) -> None:
        title = "A price is stored in minor units"
        path = "docs/catalog/premises.md"
        payload = {
            "schema_version": 2,
            "window": {"since": "2020-01-01", "until": "2020-02-01", "sessions": 30},
            "surfaces": [{"path": path, "kind": "doc", "loads": 12, "sessions": 9, "candidate": "loaded"}],
            "premises": [
                {"path": path, "title": title, "loads_range": 0, "loads_whole": 0, "candidate": "zero-load"},
                {
                    "path": path,
                    "title": "CatalogService writes only under lock",
                    "loads_range": 4,
                    "candidate": "loaded",
                },
            ],
        }
        report = self.scan(telemetry=self.telemetry_file(payload))
        self.assertIn("D3", self.signals(report, f"{path} › {title}"), self.render(report))
        self.assertEqual([], self.signals(report, path), self.render(report))

        # The file itself is never loaded: the file is the candidate, its sections would only repeat it.
        payload["surfaces"][0].update({"loads": 0, "candidate": "zero-load"})
        report = self.scan(telemetry=self.telemetry_file(payload))
        self.assertIn("D3", self.signals(report, path), self.render(report))
        self.assertEqual([], self.signals(report, f"{path} › {title}"), self.render(report))

    def test_telemetry_v1_has_no_premise_rows(self) -> None:
        path = "docs/catalog/premises.md"
        title = "A price is stored in minor units"
        payload = {
            "schema_version": 1,
            "window": {"since": "2020-01-01", "until": "2020-02-01", "sessions": 30},
            "surfaces": [{"path": path, "kind": "doc", "loads": 12, "candidate": "loaded"}],
            "premises": [{"path": path, "title": title, "loads_range": 0, "candidate": "zero-load"}],
        }
        report = self.scan(telemetry=self.telemetry_file(payload))
        self.assertEqual([], report.candidates, self.render(report))
        self.assertIn("per-premise D3 not evaluated", self.render(report))

        # v2 without ids: the row is matched by (path, title), and "loads" is range/whole/fetch.
        payload["schema_version"] = 2
        payload["premises"][0].update({"loads_whole": 0, "loads_fetch": 0})
        report = self.scan(telemetry=self.telemetry_file(payload))
        self.assertIn("D3", self.signals(report, f"{path} › {title}"), self.render(report))
        self.assertIn("| 0/0/0 |", self.render(report))

        # v2 with ids: the id wins over the title, so a premise renamed since the report still matches.
        self.write(path, self.read(path).replace(f"## {title}", f"## {title}, restated"))
        self.write(path, self.read(path).replace("**Why:** it holds.", "**Id:** `p-4b1c9a20`\n**Why:** it holds."))
        payload["premises"][0].update({"id": "p-4b1c9a20", "loads_range": 0, "loads_whole": 0, "loads_fetch": 0})
        report = self.scan(telemetry=self.telemetry_file(payload))
        self.assertIn("D3", self.signals(report, f"{path} › {title}, restated"), self.render(report))

    def test_generated_premises_index_is_not_a_candidate(self) -> None:
        # The premises index is generated from the premises (context-lint C15 gates committed ==
        # generated): unreachable and never loaded by construction, so it must stay out of the universe.
        generated = "docs/catalog/premises-index.md"
        body = "# catalog — premises index\n\nThe entry point is `src/catalog/Missing.ts`.\n"
        self.write("docs/catalog/orphan.md", body)  # control: same content, not generated
        self.write(generated, body)
        payload = {
            "schema_version": 2,
            "window": {"since": "2020-01-01", "until": "2020-02-01", "sessions": 30},
            "surfaces": [{"path": generated, "kind": "doc", "loads": 0, "candidate": "zero-load"}],
            "premises": [
                {"path": generated, "title": "catalog — premises index", "loads_range": 0, "candidate": "zero-load"}
            ],
        }
        report = self.scan(telemetry=self.telemetry_file(payload))
        self.assertEqual([], self.signals(report, generated), self.render(report))
        self.assertNotIn(generated, self.render(report))
        self.assertEqual(["D2", "D6"], sorted(self.signals(report, "docs/catalog/orphan.md")), self.render(report))

    def test_d4_dated_doc_past_the_window_unless_kept_today(self) -> None:
        far = dt.date(2020, 6, 1)  # 152 days after the doc's name date
        report = self.scan(today=far)
        self.assertIn("D4", self.signals(report, "docs/work/plans/2020-01-01-old.md"), self.render(report))

        self.write(
            "docs/work/plans/2020-01-01-old.md",
            "# Old plan\n\n> **2020-06-01:** kept — the rollout it guards is still live.\n\nBody.\n",
        )
        report = self.scan(today=far)
        self.assertEqual([], self.signals(report, "docs/work/plans/2020-01-01-old.md"), self.render(report))

    def test_d5_rule_glob_matching_nothing_is_listed(self) -> None:
        # The multiline YAML list is the point: a naive `paths:` regex reads a block-style rule as
        # always-on, so D5 has to go through context_lint.parse_frontmatter.
        self.write(
            ".claude/rules/scoped.md",
            "---\ndescription: Scoped fixture rule\npaths:\n"
            '  - "src/catalog/**"\n  - "src/nowhere/**/*.ts"\n---\n\nDo the thing.\n',
        )
        report = self.scan()
        self.assertIn("D5", self.signals(report, ".claude/rules/scoped.md"), self.render(report))
        # The exact rendered row, not a substring: "signals" carries the dead globs and "action" the
        # phrasing — an `assertIn` would also accept a nested `D5 (remove the dead glob (…))` format.
        row = next(line for line in self.render(report).split("\n") if ".claude/rules/scoped.md" in line)
        self.assertTrue(row.startswith("| 1 | `.claude/rules/scoped.md` | D5 (src/nowhere/**/*.ts) |"), row)
        self.assertTrue(row.endswith("| n/a | — | remove the dead glob |"), row)

    def test_d5_every_glob_dead_means_the_rule_never_loads(self) -> None:
        self.write(
            ".claude/rules/scoped.md",
            '---\ndescription: Scoped fixture rule\npaths: ["src/nowhere/**"]\n---\n\nDo the thing.\n',
        )
        report = self.scan()
        self.assertIn("D5", self.signals(report, ".claude/rules/scoped.md"), self.render(report))
        self.assertIn("rule never loads: delete or fix paths:", self.render(report))

    def test_d6_doc_unreachable_from_index(self) -> None:
        self.write("docs/catalog/orphan.md", "# Orphan\n\nNothing links here.\n")
        report = self.scan()
        self.assertIn("D6", self.signals(report, "docs/catalog/orphan.md"), self.render(report))

        self.append("docs/index.md", "\n- [orphan](catalog/orphan.md)\n")
        report = self.scan()
        self.assertEqual([], self.signals(report, "docs/catalog/orphan.md"), self.render(report))

    def test_d7_advisory_entry_with_two_occurrences_is_promotion_due(self) -> None:
        self.write(
            "docs/planning/recurring-failure-modes.md",
            self.read("docs/planning/recurring-failure-modes.md").replace(
                "**Occurrences:** 1", "**Occurrences:** 2"
            ),
        )
        report = self.scan()
        self.assertEqual([("FM-1", 2, "advisory")], report.promotions, self.render(report))

        self.write(
            "docs/planning/recurring-failure-modes.md",
            self.read("docs/planning/recurring-failure-modes.md").replace(
                "**State:** advisory", "**State:** advisory (no gate: plan-time work)"
            ),
        )
        report = self.scan()
        self.assertEqual([], report.promotions, self.render(report))


class IntentTest(ContextDecayTestCase):
    """Intent artifacts live until `Status: implemented` — their lifecycle is that line, not this scan."""

    LOUD = "# Plan\n\n> **2020-01-05:** superseded — nothing\n\nIt points at `src/catalog/Missing.ts`.\n"

    def test_intent_dir_at_the_root_is_not_a_candidate(self) -> None:
        self.write("docs/catalog/loud.md", self.LOUD)  # control: the same content under docs/ IS
        self.write("intent/catalog-refresh/plan.md", self.LOUD)
        report = self.scan()
        self.assertIn("D1", self.signals(report, "docs/catalog/loud.md"), self.render(report))
        self.assertEqual([], self.signals(report, "intent/catalog-refresh/plan.md"), self.render(report))
        self.assertNotIn("intent/catalog-refresh", self.render(report))

    def test_an_intent_dir_inside_docs_is_filtered_too(self) -> None:
        self.write(
            "docs/agents/skills-config.md",
            self.read("docs/agents/skills-config.md").replace(
                "- **Intent dir:** `intent`", "- **Intent dir:** `docs/intent`"
            ),
        )
        self.write("docs/catalog/loud.md", self.LOUD)  # control
        self.write("docs/intent/catalog-refresh/plan.md", self.LOUD)
        report = self.scan()
        self.assertIn("D1", self.signals(report, "docs/catalog/loud.md"), self.render(report))
        self.assertEqual([], self.signals(report, "docs/intent/catalog-refresh/plan.md"), self.render(report))
        self.assertNotIn("docs/intent/catalog-refresh", self.render(report))


class CitationTest(ContextDecayTestCase):
    """A citation only argues for keeping a doc when it comes from a live surface."""

    FAR = dt.date(2020, 6, 1)
    DATED = "docs/work/plans/2020-01-01-old.md"

    def action(self, report) -> str:
        return next(c for c in report.candidates if c.label == self.DATED).action

    def test_an_ephemeral_citer_does_not_keep_a_dated_doc_but_a_stable_one_does(self) -> None:
        report = self.scan(today=self.FAR)
        self.assertEqual("finish: distil + banner, or delete", self.action(report), self.render(report))

        # An ephemeral citer is dated by definition — it cannot vouch for anything.
        self.write("docs/work/research/2020-05-02-note.md", f"# Note\n\nSee `{self.DATED}`.\n")
        report = self.scan(today=self.FAR)
        self.assertEqual("finish: distil + banner, or delete", self.action(report), self.render(report))

        self.write("docs/planning/note.md", f"# Note\n\nEvidence: `{self.DATED}`.\n")
        report = self.scan(today=self.FAR)
        self.assertEqual("keep (evidence cited by 1 stable doc(s))", self.action(report), self.render(report))


if __name__ == "__main__":
    unittest.main()
