#!/usr/bin/env python3
"""Red cases for every context_lint check — a gate never seen red proves nothing.

Each test builds a throwaway git repository (an instructions file, three rules, a skill, one premises
domain split in two part-files, two source files and one migration), commits it as the BASE, mutates
the worktree, and asserts which check codes come back and at which severity.

    python3 -m unittest discover -s ci -p '*_test.py'
"""

from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import context_lint  # noqa: E402
import premise as premise_reader  # noqa: E402
import skills_config  # noqa: E402

FILLER = "Filler line that carries no reference at all."

CONFIG = """# Agent skills config

## Docs layout

- **Premises:** `docs/{domain}/premises.md`
- **Premises index:** `docs/{domain}/premises-index.md`
- **Premise fetch command:** `python3 ci/premise.py <id>`
- **Planning** (optional): recurring-failure-modes `docs/planning/recurring-failure-modes.md`

## Context toolkit

- **Agent instructions file:** `CLAUDE.md`
- **Source globs:** `*.ts`, `*.yml`
- **Migration dirs:** `db/migration`
- **Path resolution roots:** `src/resources`

## Domains

| Domain | Detect (path globs) | Prompt terms |
|---|---|---|
| catalog | `src/catalog/**` | catalogue, sku |
| shipping | `src/shipping/**` | |

## Sensitive domains

catalog
"""


def rule(description: str, paths: str | None = None, body: str = "Do the thing.") -> str:
    head = f"---\ndescription: {description}\n"
    if paths is not None:
        head += f"paths: {paths}\n"
    return f"{head}---\n\n{body}\n"


def premise_id(title: str) -> str:
    """A fixture id: stable across runs (so diffs are readable) and unique per title (so C17 stays green)."""
    return "p-" + hashlib.md5(title.encode()).hexdigest()[:8]


def premise(title: str, body: str, tests: str = "`CatalogServiceTest`", identifier: str | None = None) -> str:
    return (
        f"## {title}\n**Id:** {identifier or premise_id(title)}\n\n{body}\n"
        "**Why:** the invariant would be unenforced otherwise.\n"
        "**Breaks:** the ledger of reserved stock drifts.\n"
        f"**Tests:** {tests}\n"
        "**Depends on:** —\n\n"
    )


FIXTURE = {
    "CLAUDE.md": (
        "# Fixture kernel\n\n"
        "Read `.claude/rules/a.md` before touching anything.\n\n"
        "The docs index is `docs/index.md`.\n"
    ),
    "docs/agents/skills-config.md": CONFIG,
    ".claude/rules/a.md": rule(
        "Scoped fixture rule", '["src/**", "docs/**/*.md"]', "Read `docs/index.md` before editing src."
    ),
    ".claude/rules/b.md": rule("Always-on fixture rule"),
    ".claude/rules/legacy.md": rule("Legacy oversized rule", '["src/**"]', "\n".join([FILLER] * 158)),
    ".claude/skills/x/SKILL.md": "---\nname: x\ndescription: Fixture skill\n---\n\nDo nothing.\n",
    "docs/index.md": (
        "# Index\n\n"
        "- [catalog premises](catalog/premises.md)\n"
        "- [the config](agents/skills-config.md)\n"
        "- The baseline migration is `V001__init.sql`.\n"
    ),
    "docs/catalog/premises.md": (
        "# Catalog Premises\n\n"
        + premise("CatalogService writes only under lock", "`CatalogService` writes only under the row lock.")
    ),
    "docs/catalog/premises-pricing.md": (
        "# Catalog Premises — pricing\n\n"
        + premise("Pricing keeps a very long rationale", "\n".join([FILLER] * 90))
        + premise("A price is stored in minor units", "One line of rationale.")
    ),
    "docs/planning/recurring-failure-modes.md": (
        "# Recurring failure modes\n\n## How entries get here\n\nProse.\n\n"
        "## FM-1 — Fixture class one\n\n**Mined from:** #101, #102.\n**Occurrences:** 2 · **State:** advisory\n"
        "**Trigger:** anything.\n**The failure:** it fails.\n**Artifact response:** Artifact 1.\n\n"
        "## FM-2 — Fixture class two\n\n**Mined from:** #103.\n"
        "**Occurrences:** 1 · **State:** deterministic (`CatalogServiceTest`)\n**Trigger:** anything.\n"
    ),
    # A real top-level `vendor/` directory: without it the `vendor/nowhere/x` coordinate test would
    # prove nothing (an unknown first segment is skipped before the owner check ever runs).
    "vendor/versions.toml": '[versions]\nnode = "22"\n',
    "src/resources/messages.yml": "greeting: hello\n",
    "src/catalog/CatalogService.ts": "export class CatalogService {\n  run() {}\n}\n",
    "src/catalog/CatalogServiceTest.ts": "export class CatalogServiceTest {\n  runs() {}\n}\n",
    "db/migration/V001__init.sql": "SELECT 1;\n",
}


def build_template() -> str:
    """Build the base repository once; every test copies it (git init + commit costs more than a copy)."""
    repo = tempfile.mkdtemp(prefix="context-lint-template-")
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
    # The index is what the generator emits for the fixture's premises — GreenTest stays green by
    # construction, and any drift in the renderer shows up as a C15 on the untouched fixture.
    context_lint.write_indices(repo)
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    return repo


TEMPLATE = build_template()


class ContextLintTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp(prefix="context-lint-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        # `*.lock`: git's background maintenance can create and delete `.git/objects/maintenance.lock`
        # under the template while copytree walks it, and the vanished entry aborts the copy.
        shutil.copytree(
            TEMPLATE, self.repo, dirs_exist_ok=True, symlinks=True, ignore=shutil.ignore_patterns("*.lock")
        )

    # ── helpers ───────────────────────────────────────────────────────────────────────────────────

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True
        ).stdout

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

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def lint(self, base: str | None = "main") -> context_lint.Result:
        return context_lint.run(self.repo, base)

    def assertFails(self, result: context_lint.Result, code: str) -> None:
        self.assertIn(code, result.failure_codes, self.render(result))

    def assertWarnsOnly(self, result: context_lint.Result, code: str) -> None:
        self.assertIn(code, result.warning_codes, self.render(result))
        self.assertNotIn(code, result.failure_codes, self.render(result))

    @staticmethod
    def render(result: context_lint.Result) -> str:
        return "\n".join(f"{f.level} {f.path}:{f.line}: [{f.code}] {f.message}" for f in result.findings) or "<clean>"


class GreenTest(ContextLintTestCase):
    def test_untouched_fixture_is_clean(self) -> None:
        result = self.lint()
        self.assertEqual([], result.findings, self.render(result))
        self.assertGreater(result.always_on_bytes, 0)


class AbsoluteCeilingTest(ContextLintTestCase):
    def test_c1_instructions_file_over_the_line_ceiling(self) -> None:
        self.append("CLAUDE.md", "\n".join([FILLER] * 250))
        self.assertFails(self.lint(), "C1")

    def test_c2_always_on_package_over_the_byte_ceiling(self) -> None:
        # One enormous line: blows the byte ceiling without also tripping the rule line ceiling.
        self.write(".claude/rules/b.md", rule("Always-on fixture rule", None, "x" * 40000))
        result = self.lint()
        self.assertFails(result, "C2")
        self.assertNotIn("C3", result.failure_codes, self.render(result))

    def test_c2_counts_an_import_outside_code_spans_only(self) -> None:
        baseline = self.lint().always_on_bytes
        self.write("docs/extra.md", "# Extra\n\n" + "\n".join([FILLER] * 40) + "\n")
        extra = len(self.read("docs/extra.md").encode())

        self.append("CLAUDE.md", "\nAlso read @docs/extra.md before starting.\n")
        self.assertGreaterEqual(self.lint().always_on_bytes, baseline + extra)

        self.write("CLAUDE.md", self.read("CLAUDE.md").replace("@docs/extra.md", "`@docs/extra.md`"))
        self.assertLess(self.lint().always_on_bytes, baseline + extra)

    def test_c2_counts_an_imported_always_on_rule_once(self) -> None:
        # `.claude/rules/b.md` has no `paths:` (always-on) — an explicit @import must not count it twice.
        baseline = self.lint().always_on_bytes
        line = "\nAlso read @.claude/rules/b.md before starting.\n"
        self.append("CLAUDE.md", line)
        self.assertEqual(self.lint().always_on_bytes, baseline + len(line.encode()))


class RuleTest(ContextLintTestCase):
    def test_c3_crossing_the_ceiling_fails(self) -> None:
        self.write(".claude/rules/a.md", rule("Scoped fixture rule", '["src/**"]', "\n".join([FILLER] * 200)))
        self.assertFails(self.lint(), "C3")

    def test_c3_already_over_the_ceiling_and_growing_only_warns(self) -> None:
        self.append(".claude/rules/legacy.md", "\n".join([FILLER] * 20))
        self.assertWarnsOnly(self.lint(), "C3")

    def test_c4_unknown_frontmatter_key(self) -> None:
        self.write(".claude/rules/b.md", '---\ndescription: Rule\nglobs: ["**/*.ts"]\n---\n\nBody.\n')
        result = self.lint()
        self.assertFails(result, "C4")
        self.assertTrue(any("globs" in f.message for f in result.failures), self.render(result))

    def test_c4_missing_description(self) -> None:
        self.write(".claude/rules/b.md", '---\npaths: ["src/**"]\n---\n\nBody.\n')
        self.assertFails(self.lint(), "C4")

    def test_c4_dead_glob_prefix_only_warns(self) -> None:
        self.write(".claude/rules/a.md", rule("Scoped fixture rule", '[".github/CODEOWNERS"]'))
        self.assertWarnsOnly(self.lint(), "C4")

    def test_c5_skill_missing_name(self) -> None:
        self.write(".claude/skills/x/SKILL.md", "---\ndescription: Fixture skill\n---\n\nDo nothing.\n")
        self.assertFails(self.lint(), "C5")

    def test_c5_unknown_skill_key_only_warns(self) -> None:
        self.write(
            ".claude/skills/x/SKILL.md",
            "---\nname: x\ndescription: Fixture skill\ntools: everything\n---\n\nDo nothing.\n",
        )
        self.assertWarnsOnly(self.lint(), "C5")


class ReferenceTest(ContextLintTestCase):
    def test_c6_new_line_anchor(self) -> None:
        self.append("docs/index.md", "\nSee src/catalog/CatalogService.ts:12 for the idiom.\n")
        self.assertFails(self.lint(), "C6")

    def test_c7_new_broken_relative_link(self) -> None:
        self.append("docs/index.md", "\n- [gone](catalog/gone.md)\n")
        self.assertFails(self.lint(), "C7")

    def test_c8_new_broken_backticked_path(self) -> None:
        self.append("docs/index.md", "\nThe entry point is `src/catalog/Missing.ts`.\n")
        self.assertFails(self.lint(), "C8")

    def test_c8_extensionless_path_only_warns(self) -> None:
        self.append("docs/index.md", "\nEverything lives under `src/nowhere`.\n")
        self.assertWarnsOnly(self.lint(), "C8")

    def test_c8_resolves_through_a_configured_path_root(self) -> None:
        self.append("docs/index.md", "\nThe template is `templates/mail.html`.\n")
        self.assertFails(self.lint(), "C8")  # control: no such file under any root
        self.write("src/resources/templates/mail.html", "<p>hi</p>\n")
        self.assertNotIn("C8", self.lint().codes, self.render(self.lint()))

    def test_c8_gitignored_runtime_path_resolves(self) -> None:
        self.append("docs/index.md", "\nThe ledger lives in `.claude/.implement/`.\n")
        self.assertWarnsOnly(self.lint(), "C8")  # control: without the ignore rule it is just a dead path
        self.write(".gitignore", ".claude/.implement/\n")
        self.assertNotIn("C8", self.lint().codes, self.render(self.lint()))

    def test_c8_action_coordinate_is_not_a_path(self) -> None:
        self.append("docs/index.md", "\nThe pinning policy covers `vendor/nowhere/setup-node`.\n")
        self.assertWarnsOnly(self.lint(), "C8")  # control: same shape, owner that is not an action vendor
        self.write("docs/index.md", self.read("docs/index.md").replace("vendor/nowhere", "actions/nowhere"))
        self.assertNotIn("C8", self.lint().codes, self.render(self.lint()))

    def test_c9_new_unknown_migration(self) -> None:
        self.append("docs/index.md", "\nThe backfill landed in `V777`.\n")
        self.assertFails(self.lint(), "C9")

    def test_c10_new_unknown_test_class_fails(self) -> None:
        self.append("docs/index.md", "\nCovered by `NoSuchServiceTest`.\n")
        self.assertFails(self.lint(), "C10")

    def test_c10_new_unknown_framework_symbol_only_warns(self) -> None:
        self.append("docs/index.md", "\nThe runtime gives us a `ThreadPoolTaskScheduler`.\n")
        self.assertWarnsOnly(self.lint(), "C10")

    def test_c10_name_defined_only_in_yaml_resolves_but_sql_never_vouches(self) -> None:
        self.append("docs/index.md", "\nThe reminder template is `CatalogReminderTemplate`.\n")
        self.assertWarnsOnly(self.lint(), "C10")  # control: undefined anywhere
        self.write("src/resources/templates.yml", "templates:\n  - name: CatalogReminderTemplate\n")
        self.assertNotIn("C10", self.lint().codes, self.render(self.lint()))

        # `*.sql` is not in the configured source globs: a commented-out statement must not vouch for a name.
        self.append("docs/index.md", "\nThe backfill used `GhostSqlService`.\n")
        self.write("db/migration/V002__x.sql", "-- GhostSqlService ran here once\nSELECT 1;\n")
        self.assertWarnsOnly(self.lint(), "C10")

    def test_c10_agent_tool_name_is_never_a_source_symbol(self) -> None:
        self.append("docs/index.md", "\nAsk with `AskUserQuestions` instead of guessing.\n")
        self.assertWarnsOnly(self.lint(), "C10")  # control: near-miss name, not the tool
        self.write("docs/index.md", self.read("docs/index.md").replace("AskUserQuestions", "AskUserQuestion"))
        self.assertNotIn("C10", self.lint().codes, self.render(self.lint()))

    def test_c10_regression_when_the_class_is_renamed_in_source(self) -> None:
        self.write("src/catalog/CatalogService.ts", "export class OfferService {\n  run() {}\n}\n")
        os.remove(os.path.join(self.repo, "src/catalog/CatalogService.ts"))
        self.write("src/catalog/OfferService.ts", "export class OfferService {\n  run() {}\n}\n")
        result = self.lint()
        self.assertFails(result, "C10")
        self.assertTrue(any("regression" in f.message for f in result.failures), self.render(result))

    def test_legacy_broken_reference_only_warns(self) -> None:
        self.append("docs/index.md", "\nHistory lives in `docs/catalog/gone.md`.\n")
        self.commit("legacy reference already on base")
        self.assertWarnsOnly(self.lint(), "C8")

    def test_doc_rename_keeps_a_legacy_reference_a_warning(self) -> None:
        self.write("docs/legacy.md", "# Legacy\n\nHistory lives in `docs/catalog/gone.md`.\n")
        self.commit("legacy reference already on base")
        self.git("mv", "docs/legacy.md", "docs/catalog/moved.md")
        self.assertWarnsOnly(self.lint(), "C8")

    def test_annotations_cover_only_the_files_this_branch_touched(self) -> None:
        self.append("docs/index.md", "\nHistory lives in `docs/catalog/gone.md`.\n")
        self.commit("legacy reference already on base")
        self.write("docs/extra.md", "# Extra\n\nThe entry point is `src/catalog/Missing.ts`.\n")
        result = self.lint()
        self.assertEqual({"docs/extra.md", "docs/index.md"}, {f.path for f in result.findings}, self.render(result))
        self.assertEqual({"docs/extra.md"}, {f.path for f in result.annotations}, self.render(result))

    def test_annotations_cover_everything_without_a_base(self) -> None:
        self.append("docs/index.md", "\nHistory lives in `docs/catalog/gone.md`.\n")
        self.commit("legacy reference already on base")
        result = self.lint(base=None)
        self.assertEqual(result.findings, result.annotations, self.render(result))

    def test_no_base_fails_absolutes_and_degrades_the_delta(self) -> None:
        self.append("CLAUDE.md", "\n".join([FILLER] * 250))
        self.append("docs/index.md", "\nCovered by `NoSuchServiceTest`.\n")
        result = self.lint(base=None)
        self.assertFails(result, "C1")
        self.assertWarnsOnly(result, "C10")
        self.assertIsNone(result.base)


class StackOptionalTest(ContextLintTestCase):
    """C9 and C10 are stack-specific: without `Migration dirs` / `Source globs` they never run."""

    def strip_config(self) -> None:
        text = self.read("docs/agents/skills-config.md")
        for line in ("- **Source globs:** `*.ts`, `*.yml`\n", "- **Migration dirs:** `db/migration`\n"):
            text = text.replace(line, "")
        self.write("docs/agents/skills-config.md", text)

    def test_symbol_and_migration_checks_are_off_without_config(self) -> None:
        self.append("docs/index.md", "\nCovered by `NoSuchServiceTest`, landed in `V777`.\n")
        result = self.lint()
        self.assertFails(result, "C10")  # control: with the config, both fire
        self.assertFails(result, "C9")

        self.strip_config()
        result = self.lint()
        self.assertNotIn("C10", result.codes, self.render(result))
        self.assertNotIn("C9", result.codes, self.render(result))

    def test_tests_field_still_resolves_by_file_basename(self) -> None:
        """With no source globs the identifier index is basenames only — enough for C13."""
        self.strip_config()
        self.append("docs/catalog/premises.md", premise("A brand new invariant", "Short.", "`GhostServiceTest`"))
        context_lint.write_indices(self.repo)
        self.assertFails(self.lint(), "C13")

    def test_a_repo_with_no_config_at_all_still_lints(self) -> None:
        os.remove(os.path.join(self.repo, "docs/agents/skills-config.md"))
        self.append("CLAUDE.md", "\n".join([FILLER] * 250))
        result = self.lint()
        self.assertFails(result, "C1")
        self.assertFalse(result.config.present if result.config else True)


class ConfigTest(ContextLintTestCase):
    def test_the_instructions_file_can_be_renamed(self) -> None:
        self.git("mv", "CLAUDE.md", "AGENTS.md")
        text = self.read("docs/agents/skills-config.md").replace(
            "- **Agent instructions file:** `CLAUDE.md`", "- **Agent instructions file:** `AGENTS.md`"
        )
        self.write("docs/agents/skills-config.md", text)
        self.append("AGENTS.md", "\n".join([FILLER] * 250))
        result = self.lint()
        self.assertFails(result, "C1")
        self.assertEqual(["AGENTS.md"], [f.path for f in result.failures if f.code == "C1"], self.render(result))

    def test_a_ceiling_from_the_config_overrides_the_default(self) -> None:
        self.write(
            "docs/agents/skills-config.md",
            self.read("docs/agents/skills-config.md").replace(
                "- **Path resolution roots:** `src/resources`\n",
                "- **Path resolution roots:** `src/resources`\n\n"
                "| Ceiling | Value |\n|---|---|\n| agent instructions lines | 3 |\n",
            ),
        )
        result = self.lint()
        self.assertFails(result, "C1")
        self.assertIn("> 3", self.render(result))


class PremiseTest(ContextLintTestCase):
    def test_c11_new_premise_without_the_mandatory_fields(self) -> None:
        self.append("docs/catalog/premises.md", "## A brand new invariant\n\nIt holds because it holds.\n")
        self.assertFails(self.lint(), "C11")

    def test_c12_new_premise_over_the_body_ceiling(self) -> None:
        self.append("docs/catalog/premises.md", premise("A brand new long invariant", "\n".join([FILLER] * 50)))
        self.assertFails(self.lint(), "C12")

    def test_c12_new_premise_over_the_warn_ceiling_only_warns(self) -> None:
        self.append("docs/catalog/premises.md", premise("A brand new chatty invariant", "\n".join([FILLER] * 30)))
        self.assertWarnsOnly(self.lint(), "C12")

    def test_c12_retitled_legacy_premise_is_not_new(self) -> None:
        self.write(
            "docs/catalog/premises-pricing.md",
            self.read("docs/catalog/premises-pricing.md").replace(
                "## Pricing keeps a very long rationale", "## Pricing keeps a very long rationale, restated"
            ),
        )
        result = self.lint()
        self.assertNotIn("C12", result.failure_codes, self.render(result))
        self.assertNotIn("C11", result.failure_codes, self.render(result))

    def test_c13_new_premise_naming_a_dead_test_class(self) -> None:
        self.append("docs/catalog/premises.md", premise("A brand new invariant", "Short.", "`GhostServiceTest`"))
        self.assertFails(self.lint(), "C13")

    def test_c13_dead_member_only_warns(self) -> None:
        self.append(
            "docs/catalog/premises.md", premise("A brand new invariant", "Short.", "`CatalogServiceTest.GhostNested`")
        )
        self.assertWarnsOnly(self.lint(), "C13")

    def test_c13_legacy_dead_test_class_only_warns(self) -> None:
        self.append("docs/catalog/premises.md", premise("A legacy invariant", "Short.", "`GhostServiceTest`"))
        context_lint.write_indices(self.repo)
        self.commit("legacy premise already on base")
        self.assertWarnsOnly(self.lint(), "C13")

    def test_c14_unresolved_wiki_link_only_warns(self) -> None:
        self.append("docs/catalog/premises.md", premise("Another invariant", "See [[No such premise title]]."))
        self.assertWarnsOnly(self.lint(), "C14")

    def test_c14_resolves_a_link_by_premise_id(self) -> None:
        target = premise_id("CatalogService writes only under lock")
        self.append("docs/catalog/premises.md", premise("Another invariant", f"See [[{target}]]."))
        result = self.lint()
        self.assertNotIn("C14", result.warning_codes, self.render(result))


class PremiseIdTest(ContextLintTestCase):
    """C17 — the id is the handle `premise.py` resolves; absent, malformed or shared, it resolves nothing."""

    def test_c17_premise_without_an_id_fails(self) -> None:
        self.append(
            "docs/catalog/premises.md",
            "## An invariant nobody minted an id for\n\nIt holds.\n"
            "**Why:** because.\n**Breaks:** the ledger drifts.\n**Tests:** `CatalogServiceTest`\n\n",
        )
        result = self.lint()
        self.assertFails(result, "C17")
        self.assertFails(result, "C11")  # a new premise without an id is incomplete on both counts

    def test_c17_malformed_id_fails(self) -> None:
        self.append("docs/catalog/premises.md", premise("An invariant with a bad id", "Short.", identifier="p-XYZ"))
        result = self.lint()
        self.assertFails(result, "C17")
        self.assertIn("malformed", self.render(result))

    def test_c17_duplicate_id_across_two_files_fails_on_both(self) -> None:
        taken = premise_id("CatalogService writes only under lock")
        self.append(
            "docs/catalog/premises-pricing.md", premise("An invariant reusing an id", "Short.", identifier=taken)
        )
        result = self.lint()
        self.assertFails(result, "C17")
        paths = {f.path for f in result.failures if f.code == "C17"}
        self.assertEqual({"docs/catalog/premises.md", "docs/catalog/premises-pricing.md"}, paths, self.render(result))

    def test_c17_id_out_of_position_only_warns(self) -> None:
        self.append(
            "docs/catalog/premises.md",
            "## An invariant whose id drifted down\n\nIt holds.\n"
            f"**Id:** {premise_id('An invariant whose id drifted down')}\n"
            "**Why:** because.\n**Breaks:** the ledger drifts.\n**Tests:** `CatalogServiceTest`\n\n",
        )
        self.assertWarnsOnly(self.lint(), "C17")

    def test_c17_is_green_after_assign_missing(self) -> None:
        self.append(
            "docs/catalog/premises.md",
            "## An invariant nobody minted an id for\n\nIt holds.\n"
            "**Why:** because.\n**Breaks:** the ledger drifts.\n**Tests:** `CatalogServiceTest`\n\n",
        )
        self.assertFails(self.lint(), "C17")
        self.assertEqual(1, premise_reader.assign_missing(self.repo))
        result = self.lint()
        self.assertNotIn("C17", result.failure_codes, self.render(result))
        self.assertNotIn("C17", result.warning_codes, self.render(result))
        self.assertEqual(0, premise_reader.assign_missing(self.repo))


class IndexTest(ContextLintTestCase):
    """C15 — the committed premises index is exactly what `--write-indices` emits, or the PR fails."""

    INDEX = "docs/catalog/premises-index.md"

    @staticmethod
    def make(body: str) -> context_lint.Premise:
        return context_lint.Premise(
            path="docs/catalog/premises.md",
            domain="docs/catalog",
            title="T",
            line=1,
            body=body.split("\n"),
            body_start=2,
        )

    def test_c15_premise_edited_without_regenerating(self) -> None:
        self.append("docs/catalog/premises.md", premise("A brand new invariant", "It holds because it holds."))
        self.assertFails(self.lint(), "C15")

    def test_c15_missing_index_fails(self) -> None:
        os.remove(os.path.join(self.repo, self.INDEX))
        self.assertFails(self.lint(), "C15")

    def test_c15_hand_edited_index_fails(self) -> None:
        self.append(self.INDEX, "- **Hand-written entry**\n")
        self.assertFails(self.lint(), "C15")

    def test_c15_orphan_index_fails(self) -> None:
        self.write("docs/shipping/premises-index.md", "# shipping — premises index\n")
        result = self.lint()
        self.assertFails(result, "C15")
        self.assertIn("orphan", self.render(result))

    def test_write_indices_round_trip(self) -> None:
        self.append("docs/catalog/premises.md", premise("A brand new invariant", "It holds because it holds."))
        self.assertEqual([self.INDEX], context_lint.write_indices(self.repo))
        result = self.lint()
        self.assertNotIn("C15", result.failure_codes, self.render(result))
        self.assertEqual([], context_lint.write_indices(self.repo))
        self.assertIn(
            f"- **A brand new invariant** — It holds because it holds. · `{premise_id('A brand new invariant')}`",
            self.read(self.INDEX),
        )

    def test_the_index_recipe_names_the_configured_fetch_command(self) -> None:
        self.assertIn("python3 ci/premise.py <id>", self.read(self.INDEX))

    def test_index_is_neither_premises_file_nor_surface(self) -> None:
        surfaces = context_lint.Surfaces(skills_config.load(self.repo))
        self.assertFalse(surfaces.is_premises(self.INDEX))
        self.assertFalse(surfaces.is_surface(self.INDEX))
        self.assertTrue(surfaces.is_index(self.INDEX))

    def test_essence_rules(self) -> None:
        fenced = (
            "**Id:** p-11111111\n\n> A blockquote first.\n\n```sql\nSELECT 1;\n```\n\nReal sentence here. And more."
        )
        self.assertEqual("Real sentence here.", context_lint.essence(self.make(fenced)))

        fields_only = "**Id:** p-22222222\n**Why:** the ledger would drift.\n**Breaks:** stock moves twice.\n"
        self.assertEqual("the ledger would drift.", context_lint.essence(self.make(fields_only)))

        listed = "**Id:** p-33333333\n\n- First item of the list.\n- Second item.\n"
        self.assertEqual("First item of the list.", context_lint.essence(self.make(listed)))

        abbreviated = "**Id:** p-44444444\n\nUses e.g. `Foo.bar`, i.e. the seam. Next sentence.\n"
        self.assertEqual("Uses e.g. `Foo.bar`, i.e. the seam.", context_lint.essence(self.make(abbreviated)))

        long_span = "**Id:** p-55555555\n\n" + ("word " * 30) + "`OpenSpanThatWouldBeCutInHalf` and more text.\n"
        cut = context_lint.essence(self.make(long_span))
        self.assertTrue(cut.endswith(" …"), cut)
        self.assertEqual(0, cut.count("`") % 2, cut)
        self.assertLessEqual(len(cut), context_lint.INDEX_ESSENCE_CHARS + 2)

    def test_the_id_line_never_becomes_the_essence(self) -> None:
        # `**Id:**` is in ALL_FIELDS and opens every body: read as a field-first body, every essence in
        # the repository would collapse into its `**Why:**`.
        with_id = "**Id:** p-66666666\n\nThe paragraph that must win.\n\n**Why:** the fallback text.\n"
        self.assertEqual("The paragraph that must win.", context_lint.essence(self.make(with_id)))


class FailureModeTest(ContextLintTestCase):
    PATH = "docs/planning/recurring-failure-modes.md"

    def test_c16_entry_missing_a_mandatory_field_fails(self) -> None:
        self.append(self.PATH, "\n## FM-3 — Fixture class three\n\n**Mined from:** #104.\n**Trigger:** anything.\n")
        self.assertFails(self.lint(), "C16")

    def test_c16_occurrences_above_the_provenance_fails(self) -> None:
        self.write(self.PATH, self.read(self.PATH).replace("**Occurrences:** 2", "**Occurrences:** 3"))
        self.assertFails(self.lint(), "C16")

    def test_c16_state_outside_the_grammar_fails(self) -> None:
        self.write(self.PATH, self.read(self.PATH).replace("**State:** advisory", "**State:** promoted"))
        self.assertFails(self.lint(), "C16")

    def test_c16_deterministic_gate_that_does_not_exist_fails(self) -> None:
        self.write(self.PATH, self.read(self.PATH).replace("`CatalogServiceTest`", "`GhostArchitectureTest`"))
        self.assertFails(self.lint(), "C16")

    def test_c16_duplicate_entry_id_fails(self) -> None:
        self.append(
            self.PATH,
            "\n## FM-1 — Fixture class one, again\n\n**Mined from:** #105.\n"
            "**Occurrences:** 1 · **State:** advisory\n**Trigger:** anything.\n",
        )
        self.assertFails(self.lint(), "C16")

    def test_c16_conforming_entries_are_clean(self) -> None:
        # The `## Review exclusions` section: an H2 without an `XX-N` id whose payload is a fenced JSON
        # block. Neither the heading nor the field-shaped strings inside the fence may reach the check.
        self.append(
            self.PATH,
            '\n## Review exclusions — calibration\n\n```json review-exclusions\n{"pattern": "**State:** bogus"}\n```\n',
        )
        result = self.lint()
        self.assertNotIn("C16", result.codes, self.render(result))


if __name__ == "__main__":
    unittest.main()
