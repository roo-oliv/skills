#!/usr/bin/env python3
"""Red cases for hooks/context_hooks.py and hooks/deep-plan-pr-gate.sh — a hook never seen red
proves nothing, and a silent-by-default hook least of all.

Every test builds a throwaway fixture repository (a config declaring two domains, one of them
sensitive, a premises file per domain, one premises index and the context charter), pipes a hook
payload to the script as the harness does, and asserts the injected context, the sentinel files and
the exit code. Black-box on purpose: the contract is the JSON on stdout, not a Python function.

    python3 -m unittest discover -s ci -p '*_test.py'
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from collections import namedtuple

def hooks_dir() -> str:
    """`hooks/` in this repo; `.claude/hooks/` once install.sh has vendored the suite into a consuming
    repo, where this file sits two levels down in `.github/scripts/` — hence the walk up."""
    here = os.path.dirname(os.path.abspath(__file__))
    for _ in range(4):
        for relative in (("hooks",), (".claude", "hooks")):
            candidate = os.path.join(here, *relative)
            if os.path.exists(os.path.join(candidate, "context_hooks.py")):
                return candidate
        here = os.path.dirname(here)
    raise RuntimeError("context_hooks.py not found next to this suite")


HOOK = os.path.join(hooks_dir(), "context_hooks.py")
GATE = os.path.join(hooks_dir(), "deep-plan-pr-gate.sh")

CONFIG = """# Agent skills config

## Docs layout

- **Premises:** `docs/{domain}/premises.md`
- **Premises index:** `docs/{domain}/premises-index.md`
- **Schema:** `docs/schema/{domain}.md`

## Domains

| Domain | Detect (path globs) | Prompt terms |
|---|---|---|
| catalog | `src/catalog/**` | catalogue, sku, catalog_item |
| shipping | `src/shipping/**` | shipment |

## Sensitive domains

catalog
"""

CATALOG_INDEX = """# catalog — premises index

## A price is stored in minor units
## Stock is reserved before it is charged
"""

CHARTER = """---
description: Admission test, ceilings and anchor rules for every context surface
paths: ["CLAUDE.md", ".claude/rules/**", "docs/**/*.md"]
---

CHARTER BODY
"""

FIXTURE = {
    "docs/agents/skills-config.md": CONFIG,
    "docs/schema/catalog.md": "# catalog schema\n",
    "docs/catalog/premises-index.md": CATALOG_INDEX,
    "docs/catalog/premises.md": "# catalog premises\n\n## A price is stored in minor units\n",
    "docs/shipping/premises.md": "# shipping premises\n\n## A shipment is never re-dispatched\n",
    ".claude/rules/context.md": CHARTER,
    "src/catalog/Price.ts": "export const price = 1;\n",
}

Result = namedtuple("Result", "code stdout stderr elapsed")


def make_repo(config: bool = True, oversized_index: bool = False) -> str:
    repo = tempfile.mkdtemp(prefix="context-hooks-")
    for rel, body in FIXTURE.items():
        if rel == "docs/agents/skills-config.md" and not config:
            continue
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
    if oversized_index:
        with open(os.path.join(repo, "docs/catalog/premises-index.md"), "w", encoding="utf-8") as handle:
            handle.write(CATALOG_INDEX + "x" * 9216)
    return repo


def payload(event: str, repo: str, session_id: str = "s1", **fields) -> dict:
    data = {"hook_event_name": event, "cwd": repo}
    if session_id is not None:
        data["session_id"] = session_id
    data.update(fields)
    return data


def run_hook(repo: str, data, env=None, argv=()) -> Result:
    environ = dict(os.environ)
    environ["CLAUDE_PROJECT_DIR"] = repo
    environ.pop("CONTEXT_HOOKS_DISABLE", None)
    if env:
        environ.update(env)
    stdin = data if isinstance(data, str) else json.dumps(data)
    start = time.time()
    proc = subprocess.run(
        [sys.executable, HOOK] + list(argv),
        input=stdin,
        capture_output=True,
        text=True,
        cwd=repo,
        env=environ,
    )
    return Result(proc.returncode, proc.stdout, proc.stderr, time.time() - start)


def context(result: Result) -> str:
    if not result.stdout.strip():
        return ""
    try:
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    except (ValueError, KeyError, TypeError):
        return ""


class HookTestCase(unittest.TestCase):
    """Fixture lifecycle plus the assertions every case repeats."""

    config = True
    oversized_index = False

    def setUp(self) -> None:
        self.repo = make_repo(config=self.config, oversized_index=self.oversized_index)
        self.addCleanup(shutil.rmtree, self.repo, True)

    def state(self, name: str, session: str = "s1") -> str:
        return os.path.join(self.repo, ".claude", ".context-hooks", session, name)

    def assertSilent(self, result: Result) -> None:
        self.assertEqual(0, result.code, result.stderr)
        self.assertEqual("", result.stdout.strip())

    def prompt(self, text: str, session: str = "s1", env=None) -> Result:
        return run_hook(self.repo, payload("UserPromptSubmit", self.repo, session, prompt=text), env=env)

    def post(self, tool: str, session: str = "s1", env=None, **tool_input) -> Result:
        data = payload("PostToolUse", self.repo, session, tool_name=tool, tool_input=tool_input)
        return run_hook(self.repo, data, env=env)

    def pre(self, tool: str, session: str = "s1", env=None, **tool_input) -> Result:
        data = payload("PreToolUse", self.repo, session, tool_name=tool, tool_input=tool_input)
        return run_hook(self.repo, data, env=env)


class RemindTest(HookTestCase):
    def test_prompt_naming_a_sensitive_domain_injects_the_index_and_engages(self) -> None:
        result = self.prompt("why did the catalog totals drop yesterday?")
        body = context(result)
        self.assertIn("A price is stored in minor units", body)
        self.assertIn("docs/catalog/premises-index.md", body)
        self.assertTrue(os.path.exists(self.state("engaged-catalog")))
        self.assertTrue(os.path.exists(self.state("reminded-catalog")))

    def test_prompt_without_trigger_is_silent(self) -> None:
        self.assertSilent(self.prompt("fix the typo in the README"))

    def test_second_prompt_same_domain_is_silent(self) -> None:
        self.assertIn("A price is stored in minor units", context(self.prompt("the catalog of july")))
        self.assertSilent(self.prompt("and the catalog of august?"))

    def test_domain_without_index_falls_back_to_one_line_and_does_not_engage(self) -> None:
        body = context(self.prompt("a shipment went out twice"))
        self.assertIn("docs/shipping/premises.md", body)
        self.assertNotIn("A price is stored in minor units", body)
        self.assertFalse(os.path.exists(self.state("engaged-shipping")))
        self.assertTrue(os.path.exists(self.state("reminded-shipping")))

    def test_slash_command_prompt_is_silent(self) -> None:
        self.assertSilent(self.prompt("/verify catalog"))

    def test_a_configured_term_maps_to_its_domain(self) -> None:
        body = context(self.prompt("the sku list is stale"))
        self.assertIn("docs/catalog/premises-index.md", body)

    def test_a_bare_word_of_a_non_sensitive_domain_is_prose(self) -> None:
        # `shipping` is declared but not sensitive: only its explicit terms fire.
        self.assertSilent(self.prompt("the shipping team asked for a report"))

    def test_at_most_three_domains_per_prompt(self) -> None:
        text = "catalog sku catalog_item shipment"
        self.assertLessEqual(context(self.prompt(text)).count("context-hooks:"), 3)

    def test_oversized_index_falls_back_to_pointer(self) -> None:
        self.oversized_index = True
        self.setUp()
        body = context(self.prompt("the catalog of july"))
        self.assertIn("docs/catalog/premises.md", body)
        self.assertNotIn("A price is stored in minor units", body)
        self.assertFalse(os.path.exists(self.state("engaged-catalog")))


class NudgeTest(HookTestCase):
    catalog_file = "src/catalog/Price.ts"
    shipping_file = "src/shipping/Label.ts"

    def test_first_edit_in_a_domain_nudges_once(self) -> None:
        body = context(self.post("Edit", file_path=os.path.join(self.repo, self.shipping_file)))
        self.assertIn("docs/shipping/premises.md", body)
        self.assertTrue(os.path.exists(self.state("nudged-shipping")))
        self.assertSilent(self.post("Edit", file_path=os.path.join(self.repo, self.shipping_file)))

    def test_the_schema_pointer_only_shows_when_the_doc_exists(self) -> None:
        body = context(self.post("Edit", file_path=os.path.join(self.repo, self.catalog_file)))
        self.assertIn("docs/schema/catalog.md", body)
        other = context(self.post("Edit", session="s2", file_path=os.path.join(self.repo, self.shipping_file)))
        self.assertNotIn("docs/schema", other)

    def test_read_of_index_suppresses_nudge(self) -> None:
        self.post("Read", file_path=os.path.join(self.repo, "docs/catalog/premises-index.md"))
        self.assertTrue(os.path.exists(self.state("engaged-catalog")))
        self.assertSilent(self.post("Edit", file_path=os.path.join(self.repo, self.catalog_file)))

    def test_bash_cat_of_premises_marks_engaged(self) -> None:
        self.post("Bash", command="sed -n 1,80p docs/catalog/premises.md")
        self.assertTrue(os.path.exists(self.state("engaged-catalog")))

    def test_remind_engagement_suppresses_nudge(self) -> None:
        self.prompt("the catalog of july")
        self.assertSilent(self.post("Edit", file_path=os.path.join(self.repo, self.catalog_file)))

    def test_stale_engagement_nudges_again(self) -> None:
        os.makedirs(os.path.dirname(self.state("engaged-catalog")), exist_ok=True)
        open(self.state("engaged-catalog"), "w").close()
        stale = time.time() - 300 * 60
        os.utime(self.state("engaged-catalog"), (stale, stale))
        body = context(self.post("Edit", file_path=os.path.join(self.repo, self.catalog_file)))
        self.assertIn("docs/catalog/premises-index.md", body)

    def test_edit_under_docs_is_silent(self) -> None:
        self.assertSilent(self.post("Edit", file_path=os.path.join(self.repo, "docs/adr/001-x.md")))

    def test_edit_of_premises_file_marks_engaged(self) -> None:
        result = self.post("Edit", file_path=os.path.join(self.repo, "docs/shipping/premises.md"))
        self.assertSilent(result)
        self.assertTrue(os.path.exists(self.state("engaged-shipping")))

    def test_read_never_reads_the_tool_response(self) -> None:
        data = payload("PostToolUse", self.repo, tool_name="Read", tool_input={"file_path": "src/catalog/Price.ts"})
        data["tool_response"] = {"file": {"content": "catalog " * 1000}}
        self.assertSilent(run_hook(self.repo, data))


class QueryTest(HookTestCase):
    def test_mcp_query_naming_a_term_injects_the_index_before_the_call(self) -> None:
        result = self.pre("mcp__db__run_query", sql="select * from catalog_item where id = 1")
        body = context(result)
        self.assertIn("A price is stored in minor units", body)
        self.assertIn("docs/schema/catalog.md", body)

    def test_mcp_text_with_a_bare_domain_word_is_silent(self) -> None:
        self.assertSilent(self.pre("mcp__chat__send_message", text="the catalog of july is closed"))

    def test_bash_command_naming_a_term_injects_after_the_call(self) -> None:
        body = context(self.post("Bash", command='psql -c "select 1 from catalog_item"'))
        self.assertIn("→ domain catalog", body)
        self.assertIn("A price is stored in minor units", body)


class CharterTest(HookTestCase):
    def test_write_of_new_rule_injects_the_charter_once(self) -> None:
        body = context(self.pre("Write", file_path=os.path.join(self.repo, ".claude/rules/new.md"), content="x"))
        self.assertIn("CHARTER BODY", body)
        self.assertTrue(os.path.exists(self.state("charter-shown")))
        self.assertSilent(self.pre("Write", file_path=os.path.join(self.repo, "docs/infra/new.md"), content="x"))

    def test_write_of_existing_file_is_silent(self) -> None:
        path = os.path.join(self.repo, "docs/catalog/premises.md")
        self.assertSilent(self.pre("Write", file_path=path, content="x"))

    def test_write_outside_charter_paths_is_silent(self) -> None:
        path = os.path.join(self.repo, "src/catalog/New.ts")
        self.assertSilent(self.pre("Write", file_path=path, content="x"))


class FailOpenTest(HookTestCase):
    def test_malformed_payload_exits_zero_silently(self) -> None:
        self.assertSilent(run_hook(self.repo, "not json"))

    def test_empty_stdin_exits_zero(self) -> None:
        self.assertSilent(run_hook(self.repo, ""))

    def test_missing_session_id_writes_no_state(self) -> None:
        data = payload("UserPromptSubmit", self.repo, session_id=None, prompt="the catalog of july")
        self.assertSilent(run_hook(self.repo, data))
        self.assertFalse(os.path.exists(os.path.join(self.repo, ".claude", ".context-hooks")))

    def test_a_repo_without_config_is_silent(self) -> None:
        self.config = False
        self.setUp()
        self.assertSilent(self.prompt("the catalog of july"))
        self.assertSilent(self.post("Edit", file_path=os.path.join(self.repo, "src/catalog/Price.ts")))

    def test_disable_all_env_silences_everything(self) -> None:
        self.assertSilent(self.prompt("the catalog of july", env={"CONTEXT_HOOKS_DISABLE": "all"}))

    def test_disable_single_hook(self) -> None:
        env = {"CONTEXT_HOOKS_DISABLE": "nudge"}
        path = os.path.join(self.repo, "src/catalog/Price.ts")
        self.assertSilent(self.post("Edit", env=env, file_path=path))
        self.post("Read", env=env, file_path=os.path.join(self.repo, "docs/catalog/premises.md"))
        self.assertTrue(os.path.exists(self.state("engaged-catalog")))


class StateTest(HookTestCase):
    def test_session_dirs_older_than_24h_are_pruned(self) -> None:
        old = os.path.join(self.repo, ".claude", ".context-hooks", "s0")
        os.makedirs(old)
        stale = time.time() - 25 * 3600
        os.utime(old, (stale, stale))
        self.prompt("the catalog of july")
        self.assertFalse(os.path.exists(old))

    def test_state_dir_lives_under_the_project_claude_dir(self) -> None:
        self.prompt("the catalog of july")
        self.assertTrue(os.path.isdir(os.path.join(self.repo, ".claude", ".context-hooks", "s1")))


class TimingTest(HookTestCase):
    def test_each_hook_path_runs_under_250ms(self) -> None:
        edited = os.path.join(self.repo, "src/catalog/Price.ts")
        big = payload("PostToolUse", self.repo, tool_name="Read", tool_input={"file_path": "src/catalog/Price.ts"})
        big["tool_response"] = {"file": {"content": "x" * (1024 * 1024)}}
        paths = [
            ("remind", lambda: self.prompt("why did the catalog totals drop?")),
            ("nudge", lambda: self.post("Edit", file_path=edited)),
            ("mark", lambda: self.post("Read", file_path=os.path.join(self.repo, "docs/catalog/premises.md"))),
            ("charter", lambda: self.pre("Write", file_path=os.path.join(self.repo, ".claude/rules/n.md"))),
            ("query", lambda: self.pre("mcp__db__run_query", sql="select 1 from catalog_item")),
            ("gate-input", lambda: run_hook(self.repo, {"tool_input": {"command": GH_CREATE}}, argv=("gate-input",))),
            ("read-1mb", lambda: run_hook(self.repo, big)),
        ]
        for name, call in paths:
            result = call()
            print("  {:<10} {:>5.0f} ms".format(name, result.elapsed * 1000))
            self.assertLess(result.elapsed, 0.25, "{} took {:.3f}s".format(name, result.elapsed))


class ArgvTest(HookTestCase):
    def subcommand(self, command: str) -> str:
        result = run_hook(self.repo, {"tool_input": {"command": command}}, argv=("gate-input",))
        self.assertEqual(0, result.code, result.stderr)
        return result.stdout.splitlines()[0]

    def test_gate_input_recognises_gh_pr_create_and_ready(self) -> None:
        self.assertEqual("create", self.subcommand("gh pr create --fill"))
        self.assertEqual("ready", self.subcommand("gh pr ready 123"))

    def test_gate_input_ignores_echo_and_commit_message(self) -> None:
        self.assertEqual("", self.subcommand('echo "gh pr create"'))
        self.assertEqual("", self.subcommand('git commit -m "gh pr create"'))

    def test_gate_input_ignores_heredoc_body(self) -> None:
        command = "gh issue comment 1 --body \"$(cat <<'EOF'\ngh pr create x\nEOF\n)\""
        self.assertEqual("", self.subcommand(command))

    def test_gate_input_skips_env_prefix_and_chained_segments(self) -> None:
        self.assertEqual("create", self.subcommand("cd src && GH_TOKEN=x gh pr create"))
        self.assertEqual("create", self.subcommand('bash -c "gh pr create"'))

    def test_gate_input_sees_through_common_command_wrappers(self) -> None:
        # A wrapper must not hide the command from the gate: with argv matching, an unrecognised
        # prefix means NO gate runs at all — the failure mode the substring matcher did not have.
        self.assertEqual("create", self.subcommand("nohup gh pr create --fill"))
        self.assertEqual("create", self.subcommand("timeout 60 gh pr create --fill"))
        self.assertEqual("create", self.subcommand("env GH_TOKEN=x gh pr create"))
        self.assertEqual("ready", self.subcommand("sudo gh pr ready 123"))
        self.assertEqual("create", self.subcommand("xargs -r gh pr create"))

    def test_gate_input_prints_the_ttl_second(self) -> None:
        result = run_hook(self.repo, {"tool_input": {"command": "gh pr create"}}, argv=("gate-input",))
        self.assertEqual("240", result.stdout.splitlines()[1])

    def test_sensitive_domains_names_only_the_sensitive_ones(self) -> None:
        result = run_hook(self.repo, "src/catalog/Price.ts\nsrc/shipping/Label.ts\n", argv=("sensitive-domains",))
        self.assertEqual(["catalog"], result.stdout.split())

    def test_sensitive_domains_is_empty_for_an_untouched_domain(self) -> None:
        result = run_hook(self.repo, "README.md\n", argv=("sensitive-domains",))
        self.assertEqual("", result.stdout.strip())

    def test_premises_paths_prints_index_and_fallback(self) -> None:
        result = run_hook(self.repo, "catalog\n", argv=("premises-paths",))
        self.assertEqual(
            ["catalog", "docs/catalog/premises-index.md", "docs/catalog/premises.md"],
            result.stdout.strip().split("\t"),
        )


# ── deep-plan-pr-gate.sh ──────────────────────────────────────────────────────────────────────────

SENSITIVE_FILE = "src/catalog/Price.ts"
CONTRACT = "## c\n**Gate**: PASS\n**Residual GAPs**: 0\n## Contract\n1. x\n| RESERVED | handled @ Price.ts |\n"
GH_CREATE = "gh pr create --fill"


def git(repo: str, *args: str) -> None:
    subprocess.run(["git"] + list(args), cwd=repo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def write(repo: str, rel: str, body: str) -> None:
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)


def new_repo() -> str:
    """A git repo with origin/main at the initial commit — the miniature every gate case starts from."""
    repo = make_repo()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.com")
    git(repo, "config", "user.name", "t")
    write(repo, "README.md", "base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def sensitive_change(repo: str, rel: str = SENSITIVE_FILE, branch: str = "feat/price") -> None:
    git(repo, "checkout", "-q", "-b", branch)
    write(repo, rel, "export const price = 2;\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "price")


def engage(repo: str, domain: str, minutes_ago: int = 0, session: str = "s1") -> None:
    path = os.path.join(repo, ".claude", ".context-hooks", session, "engaged-" + domain)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "a").close()
    stamp = time.time() - minutes_ago * 60
    os.utime(path, (stamp, stamp))


def run_gate(repo: str, command: str, env=None, cwd=None, project_dir=True) -> Result:
    environ = dict(os.environ)
    environ.pop("DEEP_PLAN_OVERRIDE", None)
    environ.pop("CONTEXT_HOOKS_DISABLE", None)
    if project_dir:
        environ["CLAUDE_PROJECT_DIR"] = repo
    else:
        environ.pop("CLAUDE_PROJECT_DIR", None)
    if env:
        environ.update(env)
    start = time.time()
    proc = subprocess.run(
        ["bash", GATE],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        cwd=cwd or repo,
        env=environ,
    )
    return Result(proc.returncode, proc.stdout, proc.stderr, time.time() - start)


class GateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = new_repo()
        self.addCleanup(shutil.rmtree, self.repo, True)

    def home(self) -> str:
        home = tempfile.mkdtemp(prefix="pr-gate-home-")
        self.addCleanup(shutil.rmtree, home, True)
        os.makedirs(os.path.join(home, ".claude", "plans"))
        return home

    # ── the premises-index gate ───────────────────────────────────────────────────────────────────

    def test_non_gh_command_allows(self) -> None:
        self.assertEqual(0, run_gate(self.repo, "git status").code)

    def test_echo_gh_pr_create_does_not_block(self) -> None:
        sensitive_change(self.repo)
        self.assertEqual(0, run_gate(self.repo, 'echo "gh pr create"').code)

    def test_heredoc_body_in_issue_comment_does_not_block(self) -> None:
        sensitive_change(self.repo)
        command = "gh issue comment 1 --body \"$(cat <<'EOF'\ngh pr create x\nEOF\n)\""
        self.assertEqual(0, run_gate(self.repo, command).code)

    def test_sensitive_branch_without_index_read_blocks_naming_the_index(self) -> None:
        sensitive_change(self.repo)
        result = run_gate(self.repo, GH_CREATE)
        self.assertEqual(2, result.code)
        self.assertIn("premises-index gate", result.stderr)
        self.assertIn("docs/catalog/premises-index.md", result.stderr)
        self.assertIn("docs/catalog/premises.md", result.stderr)

    def test_fresh_engagement_in_any_session_passes_the_index_gate(self) -> None:
        sensitive_change(self.repo)
        write(self.repo, ".claude/.plans/cur.md", CONTRACT + "1. changes " + SENSITIVE_FILE + "\n")
        engage(self.repo, "catalog", session="other")
        self.assertEqual(0, run_gate(self.repo, GH_CREATE).code)

    def test_stale_engagement_blocks(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog", minutes_ago=300)
        result = run_gate(self.repo, GH_CREATE)
        self.assertEqual(2, result.code)
        self.assertIn("premises-index gate", result.stderr)

    def test_gh_pr_ready_only_checks_the_index(self) -> None:
        sensitive_change(self.repo)
        self.assertEqual(2, run_gate(self.repo, "gh pr ready 123").code)
        engage(self.repo, "catalog")
        self.assertEqual(0, run_gate(self.repo, "gh pr ready 123").code)

    def test_disable_gate_env_skips_the_index_gate_audited(self) -> None:
        sensitive_change(self.repo)
        result = run_gate(self.repo, GH_CREATE, env={"CONTEXT_HOOKS_DISABLE": "gate"})
        self.assertIn("disabled via", result.stderr)
        self.assertEqual(2, result.code)  # still blocked, now by the deep-plan gate
        self.assertIn("deep-plan PR gate", result.stderr)

    def test_malformed_payload_allows(self) -> None:
        sensitive_change(self.repo)
        proc = subprocess.run(
            ["bash", GATE],
            input="not json",
            capture_output=True,
            text=True,
            cwd=self.repo,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=self.repo),
        )
        self.assertEqual(0, proc.returncode)

    # ── the deep-plan gate ────────────────────────────────────────────────────────────────────────

    def test_non_sensitive_branch_allows(self) -> None:
        sensitive_change(self.repo, rel="src/shipping/Label.ts", branch="feat/tooling")
        self.assertEqual(0, run_gate(self.repo, GH_CREATE).code)

    def test_override_token_allows_deep_plan_gate(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        self.assertEqual(0, run_gate(self.repo, 'gh pr create --body "[deep-plan-override: x]"').code)

    def test_override_env_allows_deep_plan_gate(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        self.assertEqual(0, run_gate(self.repo, GH_CREATE, env={"DEEP_PLAN_OVERRIDE": "x"}).code)

    def test_sensitive_without_contract_blocks(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        result = run_gate(self.repo, GH_CREATE)
        self.assertEqual(2, result.code)
        self.assertIn("deep-plan PR gate", result.stderr)

    def test_branch_contract_allows(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        write(self.repo, ".claude/deep-plan/feat-price.md", CONTRACT)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "contract")
        self.assertEqual(0, run_gate(self.repo, GH_CREATE).code)

    def test_intent_plan_on_the_branch_allows(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        write(self.repo, "intent/price-fix/plan.md", CONTRACT + "1. changes " + SENSITIVE_FILE + "\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "plan")
        self.assertEqual(0, run_gate(self.repo, GH_CREATE).code)

    def test_bare_gap_cell_blocks(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        write(self.repo, ".claude/deep-plan/c.md", "## c\n## Contract\n1. x\n| RESERVED | GAP |\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "c")
        self.assertEqual(2, run_gate(self.repo, GH_CREATE).code)

    def test_fail_marker_in_contract_blocks(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        write(self.repo, ".claude/deep-plan/c.md", "## c\n**Gate**: FAIL\n## Contract\n1. x\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "c")
        self.assertEqual(2, run_gate(self.repo, GH_CREATE).code)

    def test_gateway_fail_prose_does_not_block(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        body = "## c\n**Gate**: PASS\n**Residual GAPs**: 0\n## Contract\n1. the gateway path must not fail silently\n"
        write(self.repo, ".claude/deep-plan/c.md", body + "| RESERVED | handled @ Price.ts |\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "c")
        self.assertEqual(0, run_gate(self.repo, GH_CREATE).code)

    def test_stale_contract_on_main_does_not_bypass(self) -> None:
        write(self.repo, ".claude/deep-plan/old-feature.md", CONTRACT)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "old contract on main")
        git(self.repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        self.assertEqual(2, run_gate(self.repo, GH_CREATE).code)

    def test_stale_home_plan_does_not_bypass(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        home = self.home()
        write(home, ".claude/plans/old.md", CONTRACT + "1. touches README.md\n")
        self.assertEqual(2, run_gate(self.repo, GH_CREATE, env={"HOME": home}).code)

    def test_home_plan_referencing_changed_file_allows(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        home = self.home()
        write(home, ".claude/plans/cur.md", CONTRACT + "1. changes " + SENSITIVE_FILE + "\n")
        self.assertEqual(0, run_gate(self.repo, GH_CREATE, env={"HOME": home}).code)

    def test_repo_local_plan_referencing_changed_file_allows(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        write(self.repo, ".claude/.plans/cur.md", CONTRACT + "1. changes " + SENSITIVE_FILE + "\n")
        self.assertEqual(0, run_gate(self.repo, GH_CREATE).code)

    def test_repo_local_stale_plan_blocks(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        write(self.repo, ".claude/.plans/old.md", CONTRACT + "1. touches README.md\n")
        self.assertEqual(2, run_gate(self.repo, GH_CREATE).code)

    def test_subdir_run_without_project_dir_resolves_root(self) -> None:
        sensitive_change(self.repo)
        engage(self.repo, "catalog")
        write(self.repo, ".claude/deep-plan/feat-price.md", CONTRACT)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-qm", "contract")
        subdir = os.path.join(self.repo, "src")
        self.assertEqual(0, run_gate(self.repo, GH_CREATE, cwd=subdir, project_dir=False).code)


if __name__ == "__main__":
    unittest.main()
