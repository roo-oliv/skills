#!/usr/bin/env python3
"""Red cases for premise.py — a reader that prints the wrong section is worse than no reader.

Each test builds a throwaway premises tree (two domains, one split in two part-files, a fenced `## `
that must NOT end a section, a link heading that is not a premise), runs the script's own entry point
and asserts what came out of stdout and what exit code came back.

    python3 -m unittest discover -s ci -p 'premise_test.py'
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import premise  # noqa: E402

FIRST = "p-0000000a"
MIDDLE = "p-0000000b"
LAST = "p-0000000c"
OTHER = "p-0000000d"

FIXTURE = {
    "docs/catalog/premises.md": (
        "# Catalog Premises\n"
        "\n"
        "Domain context.\n"
        "\n"
        "## First invariant\n"
        f"**Id:** {FIRST}\n"
        "\n"
        "The first one holds.\n"
        "\n"
        "**Why:** because.\n"
        "**Breaks:** the ledger drifts.\n"
        "**Tests:** `CatalogServiceTest`\n"
        f"**Depends on:** [[Second invariant]] and {OTHER}\n"
        "\n"
        "## [A part map, not a premise](premises-pricing.md)\n"
        "\n"
        "- **First invariant** — the list a link heading introduces.\n"
        "\n"
        "## Second invariant\n"
        f"**Id:** {MIDDLE}\n"
        "\n"
        "The second one holds, and it quotes a heading:\n"
        "\n"
        "```markdown\n"
        "## Not a premise, it is inside a fence\n"
        "```\n"
        "\n"
        "Still the second one.\n"
        "\n"
        "**Why:** because.\n"
        "**Breaks:** the ledger drifts.\n"
        "**Tests:** `CatalogServiceTest`\n"
        "**Depends on:** —\n"
        "\n"
        "## Third invariant, last in the file\n"
        f"**Id:** {LAST}\n"
        "\n"
        "The third one runs to EOF.\n"
        "\n"
        "**Why:** because.\n"
        "**Breaks:** the ledger drifts.\n"
        "**Tests:** `CatalogServiceTest`\n"
        "**Depends on:** —\n"
    ),
    "docs/shipping/premises.md": (
        "# Shipping Premises\n"
        "\n"
        "## A shipping invariant\n"
        f"**Id:** {OTHER}\n"
        "\n"
        "The shipping one holds.\n"
        "\n"
        "**Why:** because.\n"
        "**Breaks:** the ledger drifts.\n"
        "**Tests:** `ShippingServiceTest`\n"
        "**Depends on:** —\n"
    ),
    "docs/shipping/premises-extra.md": (
        "# Shipping Premises — extra\n"
        "\n"
        "## An invariant with no id at all\n"
        "\n"
        "Nobody minted an id for this one.\n"
        "\n"
        "**Why:** because.\n"
        "**Breaks:** the ledger drifts.\n"
        "**Tests:** `ShippingServiceTest`\n"
        "**Depends on:** —\n"
    ),
}


class PremiseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp(prefix="premise-")
        self.addCleanup(shutil.rmtree, self.repo, True)
        for path, content in FIXTURE.items():
            self.write(path, content)

    # ── helpers ───────────────────────────────────────────────────────────────────────────────────

    def write(self, path: str, content: str) -> None:
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(content)

    def read(self, path: str) -> str:
        with open(os.path.join(self.repo, path), encoding="utf-8") as handle:
            return handle.read()

    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = premise.main([*argv, "--repo", self.repo])
        return code, out.getvalue(), err.getvalue()


class FetchTest(PremiseTestCase):
    def test_fetch_in_the_middle_of_the_file_stops_at_the_next_heading(self) -> None:
        code, out, _ = self.run_main(MIDDLE)
        self.assertEqual(0, code, out)
        self.assertTrue(out.startswith("# docs/catalog/premises.md › Second invariant\n"), out)
        self.assertIn("## Second invariant", out)
        self.assertIn("Still the second one.", out)
        self.assertIn("## Not a premise, it is inside a fence", out)  # the fenced heading belongs to the body
        self.assertNotIn("Third invariant", out)
        self.assertNotIn("The first one holds.", out)

    def test_fetch_of_the_last_section_runs_to_eof(self) -> None:
        code, out, _ = self.run_main(LAST)
        self.assertEqual(0, code, out)
        self.assertIn("The third one runs to EOF.", out)
        self.assertIn("**Tests:** `CatalogServiceTest`", out)
        self.assertFalse(out.endswith("\n\n"), repr(out[-20:]))

    def test_a_link_heading_is_not_a_premise(self) -> None:
        titles = [section.title for section in premise.load(self.repo)]
        self.assertNotIn("[A part map, not a premise](premises-pricing.md)", titles)
        self.assertEqual(5, len(titles), titles)

    def test_the_generated_index_is_never_read_as_premises(self) -> None:
        self.write("docs/catalog/premises-index.md", "# catalog — premises index\n\n## Not a premise\n")
        self.assertNotIn("Not a premise", [section.title for section in premise.load(self.repo)])

    def test_unknown_id_exits_one(self) -> None:
        code, out, err = self.run_main("p-deadbeef")
        self.assertEqual(1, code, out)
        self.assertIn("no premise holds the id p-deadbeef", err)
        self.assertEqual("", out)

    def test_a_title_is_not_an_id(self) -> None:
        code, _, err = self.run_main("Second invariant")
        self.assertEqual(1, code)
        self.assertIn("is not a premise id", err)

    def test_duplicate_id_across_two_files_is_ambiguous(self) -> None:
        self.write(
            "docs/shipping/premises-extra.md",
            self.read("docs/shipping/premises-extra.md").replace(
                "## An invariant with no id at all\n", f"## An invariant with no id at all\n**Id:** {MIDDLE}\n"
            ),
        )
        code, _, err = self.run_main(MIDDLE)
        self.assertEqual(1, code)
        self.assertIn("ambiguous", err)
        self.assertIn("docs/shipping/premises-extra.md", err)
        self.assertIn("docs/catalog/premises.md", err)


class DepsTest(PremiseTestCase):
    def test_deps_resolves_by_title_and_by_id_without_repeating(self) -> None:
        code, out, _ = self.run_main(FIRST, "--deps")
        self.assertEqual(0, code, out)
        self.assertEqual(1, out.count("# docs/catalog/premises.md › First invariant"), out)
        self.assertEqual(1, out.count("# docs/catalog/premises.md › Second invariant"), out)  # by [[title]]
        self.assertEqual(1, out.count("# docs/shipping/premises.md › A shipping invariant"), out)  # by p- id
        self.assertNotIn("Third invariant", out)

    def test_deps_is_one_level_only(self) -> None:
        # Second invariant depends on nothing; fetching First must not chase past it.
        _, out, _ = self.run_main(FIRST, "--deps")
        self.assertEqual(3, out.count("\n# docs/") + 1, out)

    def test_without_deps_only_the_section_comes_out(self) -> None:
        _, out, _ = self.run_main(FIRST)
        self.assertNotIn("A shipping invariant", out)


class MintTest(PremiseTestCase):
    def test_mint_never_collides_with_the_tree(self) -> None:
        taken = {section.id for section in premise.load(self.repo)}
        minted = {premise.mint(premise.load(self.repo)) for _ in range(50)}
        self.assertEqual(set(), minted & taken)
        for value in minted:
            self.assertRegex(value, r"^p-[0-9a-f]{8}$")

    def test_mint_prints_a_usable_id(self) -> None:
        code, out, _ = self.run_main("mint")
        self.assertEqual(0, code)
        self.assertRegex(out.strip(), r"^p-[0-9a-f]{8}$")


class AssignMissingTest(PremiseTestCase):
    PATH = "docs/shipping/premises-extra.md"

    def test_assign_missing_adds_one_line_per_premise_and_nothing_else(self) -> None:
        before = self.read(self.PATH)
        self.assertEqual(1, premise.assign_missing(self.repo))
        after = self.read(self.PATH)
        added = [line for line in after.split("\n") if line not in before.split("\n")]
        self.assertEqual(1, len(added), added)
        self.assertRegex(added[0], r"^\*\*Id:\*\* p-[0-9a-f]{8}$")
        self.assertEqual(before.split("\n"), [line for line in after.split("\n") if line != added[0]])
        self.assertEqual(
            "## An invariant with no id at all", after.split("\n")[after.split("\n").index(added[0]) - 1]
        )

    def test_assign_missing_is_idempotent(self) -> None:
        self.assertEqual(1, premise.assign_missing(self.repo))
        first = self.read(self.PATH)
        self.assertEqual(0, premise.assign_missing(self.repo))
        self.assertEqual(first, self.read(self.PATH))

    def test_assign_missing_leaves_a_malformed_id_alone(self) -> None:
        self.write(
            self.PATH,
            self.read(self.PATH).replace(
                "## An invariant with no id at all\n", "## An invariant with no id at all\n**Id:** p-NOTHEX\n"
            ),
        )
        self.assertEqual(0, premise.assign_missing(self.repo))
        self.assertEqual(1, self.read(self.PATH).count("**Id:**"))  # C17 reports it; the writer never doubles it

    def test_the_assigned_id_is_the_one_the_reader_fetches(self) -> None:
        premise.assign_missing(self.repo)
        new_id = [s.id for s in premise.load(self.repo) if s.title == "An invariant with no id at all"][0]
        code, out, _ = self.run_main(str(new_id))
        self.assertEqual(0, code)
        self.assertIn("Nobody minted an id for this one.", out)


class ColocatedLayoutTest(PremiseTestCase):
    """A repo that colocates premises with the module it documents — same reader, different pattern."""

    def setUp(self) -> None:
        super().setUp()
        self.write(
            "modules/checkout/docs/premises.md",
            f"# Checkout premises\n\n## A colocated invariant\n**Id:** p-0000000e\n\nIt holds here.\n",
        )
        self.write(
            "docs/agents/skills-config.md",
            "# config\n\n## Docs layout\n\n- **Premises:** `{module}/docs/premises.md`\n",
        )

    def test_the_reader_follows_the_configured_pattern(self) -> None:
        code, out, _ = self.run_main("p-0000000e")
        self.assertEqual(0, code, out)
        self.assertIn("It holds here.", out)
        # The docs/{domain} tree is not premises under this pattern — the reader must not find it.
        code, _, err = self.run_main(FIRST)
        self.assertEqual(1, code)
        self.assertIn("no premise holds", err)


if __name__ == "__main__":
    unittest.main()
