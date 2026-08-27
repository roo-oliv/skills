#!/usr/bin/env python3
"""Red cases for skills_config — the reader every other script trusts.

A misread config is the worst failure this toolkit has: it does not error, it silently lints the wrong
tree. So the template that ships with `setup` is parsed here as a fixture, and every value that is
still a `<placeholder>` must come back as the documented default.

    python3 -m unittest discover -s ci -p 'skills_config_test.py'
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import skills_config  # noqa: E402

def find_template() -> str:
    """`skills/` in this repo; `.claude/skills/` once install.sh has vendored the suite into a consuming
    repo, where this file sits two levels down in `.github/scripts/` — hence the walk up."""
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        for relative in (("skills",), (".claude", "skills")):
            candidate = os.path.join(here, *relative, "setup", "skills-config.template.md")
            if os.path.exists(candidate):
                return candidate
        here = os.path.dirname(here)
    return ""


TEMPLATE = find_template()

FILLED = """# config

## Docs layout

- **Premises:** `docs/domain/{domain}/premises.md`
- **Premises index:** `docs/domain/{domain}/premises-index.md`
- **Schema:** `docs/schema/{domain}.md`
- **Planning** (optional): plan-contract spec `docs/planning/plan-contract.md`; recurring-failure-modes
  `docs/planning/failures.md`

## Context toolkit

- **Agent instructions file:** `AGENTS.md`
- **Ephemeral docs:** `docs/scratch/**`, `notes/**`
- **Source globs:** `*.cs`, `*.csproj`

| Ceiling | Value |
|---|---|
| agent instructions lines | 120 |
| rule lines | 80 |

## Intent

- **Intent dir:** `work/intent/`

## Domains

| Domain | Detect (path globs) | Prompt terms |
|---|---|---|
| catalog | `src/catalog/**`, `api/catalog/**` | catalogue, sku |
| shipping | `src/shipping/**` | |

## Sensitive domains

catalog, accounts
"""


@unittest.skipUnless(TEMPLATE, "the setup skill's template is not vendored next to this suite")
class TemplateTest(unittest.TestCase):
    """The shipped template must parse to exactly the defaults — a placeholder is never a value."""

    def setUp(self) -> None:
        self.config = skills_config.load(".", TEMPLATE)

    def test_every_placeholder_falls_back_to_its_documented_default(self) -> None:
        self.assertTrue(self.config.present)
        self.assertEqual("CLAUDE.md", self.config.agent_instructions)
        self.assertEqual(("CLAUDE.md", "README.md"), self.config.root_surfaces)
        self.assertEqual(".claude/rules", self.config.rules_dir)
        self.assertEqual(".claude/skills", self.config.skills_dir)
        self.assertEqual("docs", self.config.docs_root)
        self.assertEqual("docs/index.md", self.config.docs_index)
        self.assertEqual("docs/{domain}/premises.md", self.config.premises)
        self.assertEqual("docs/{domain}/premises-index.md", self.config.premises_index)
        self.assertEqual("python3 .github/scripts/premise.py <id>", self.config.premise_fetch)
        self.assertEqual("intent", self.config.intent_dir)
        self.assertEqual("docs/flows", self.config.flows_dir)
        self.assertEqual(["docs/work/**"], self.config.ephemeral_globs)
        self.assertEqual([".claude/deep-plan/**", "**/eval/**"], self.config.excluded_globs)
        self.assertIsNone(self.config.schema)
        self.assertEqual(skills_config.CEILINGS, self.config.ceilings)

    def test_the_stack_specific_checks_stay_off(self) -> None:
        self.assertEqual([], self.config.source_globs)
        self.assertEqual([], self.config.migration_dirs)
        self.assertEqual([], self.config.path_roots)

    def test_a_placeholder_row_is_not_a_domain(self) -> None:
        self.assertEqual([], self.config.domains)
        self.assertEqual([], self.config.sensitive)

    def test_a_comment_carrying_a_colon_does_not_become_the_value(self) -> None:
        # `- **X:** `<path>`  <!-- default: CLAUDE.md -->` — splitting on the first colon before stripping
        # the comment reads "CLAUDE.md -->" as the value, and every default silently disappears.
        section = skills_config.parse_sections(
            "## S\n\n- **Docs root:** `<path>`  <!-- default: docs/ — the tree we lint -->\n"
        )["s"]
        self.assertEqual("<path>", section.bullets["docs root"])


class FilledTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="skills-config-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        path = os.path.join(self.dir, "config.md")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(FILLED)
        self.config = skills_config.load(self.dir, path)

    def test_values_override_defaults(self) -> None:
        self.assertEqual("AGENTS.md", self.config.agent_instructions)
        self.assertEqual(("AGENTS.md", "README.md"), self.config.root_surfaces)
        self.assertEqual(["docs/scratch/**", "notes/**"], self.config.ephemeral_globs)
        self.assertEqual(["*.cs", "*.csproj"], self.config.source_globs)
        self.assertEqual("work/intent", self.config.intent_dir)
        self.assertEqual("docs/planning/failures.md", self.config.failure_modes)

    def test_only_the_named_ceilings_move(self) -> None:
        self.assertEqual(120, self.config.ceilings["agent_instructions_lines"])
        self.assertEqual(80, self.config.ceilings["rule_lines"])
        self.assertEqual(skills_config.CEILINGS["premise_bytes"], self.config.ceilings["premise_bytes"])

    def test_domains_carry_globs_and_terms(self) -> None:
        names = [d.name for d in self.config.domains]
        self.assertEqual(["catalog", "shipping"], names)
        catalog = self.config.domains[0]
        self.assertEqual(["src/catalog/**", "api/catalog/**"], catalog.globs)
        self.assertEqual(["catalogue", "sku"], catalog.terms)
        self.assertEqual([], self.config.domains[1].terms)
        self.assertEqual(["catalog", "accounts"], self.config.sensitive)

    def test_domain_of_path_uses_the_globs(self) -> None:
        self.assertEqual("catalog", self.config.domain_of_path("api/catalog/Handler.cs"))
        self.assertEqual("shipping", self.config.domain_of_path("src/shipping/Rates.cs"))
        self.assertIsNone(self.config.domain_of_path("src/reporting/Export.cs"))

    def test_premises_paths_follow_the_configured_pattern(self) -> None:
        self.assertTrue(self.config.is_premises("docs/domain/catalog/premises.md"))
        self.assertTrue(self.config.is_premises("docs/domain/catalog/premises-pricing.md"))
        self.assertFalse(self.config.is_premises("docs/domain/catalog/premises-index.md"))
        self.assertTrue(self.config.is_premises_index("docs/domain/catalog/premises-index.md"))
        self.assertEqual(
            "docs/domain/catalog/premises-index.md",
            self.config.premises_index_for("docs/domain/catalog/premises-pricing.md"),
        )
        self.assertEqual("docs/schema/catalog.md", self.config.schema_of_domain("catalog"))


class MissingConfigTest(unittest.TestCase):
    def test_no_file_is_not_an_error(self) -> None:
        config = skills_config.load(tempfile.gettempdir(), "/nowhere/skills-config.md")
        self.assertFalse(config.present)
        self.assertEqual("CLAUDE.md", config.agent_instructions)
        self.assertTrue(config.notes)


class GlobTest(unittest.TestCase):
    def test_the_paths_dialect(self) -> None:
        self.assertTrue(skills_config.glob_match("src/catalog/a/b.ts", "src/**"))
        self.assertTrue(skills_config.glob_match("docs/a/b.md", "docs/**/*.md"))
        self.assertTrue(skills_config.glob_match("docs/b.md", "docs/**/*.md"))  # `**/` may match nothing
        self.assertFalse(skills_config.glob_match("docs/a/b.md", "docs/*.md"))
        self.assertTrue(skills_config.glob_match("db/data/x.sql", "db/{migration,data}/*.sql"))
        self.assertFalse(skills_config.glob_match("db/journal/x.sql", "db/{migration,data}/*.sql"))


if __name__ == "__main__":
    unittest.main()
