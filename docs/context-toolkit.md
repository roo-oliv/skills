# The context toolkit

Skills are what an agent *runs*. The context toolkit is what keeps what it *reads* honest: three Python scripts
and a set of hooks that gate, retire and route the surfaces an agent loads — the instructions file, the rules,
the skills and the docs.

| Piece | Vendored to | What it does |
|---|---|---|
| [`ci/context_lint.py`](../ci/context_lint.py) | `.github/scripts/` | CI gate: ceilings, frontmatter, and every path/link/symbol/premise reference that must still resolve |
| [`ci/premise.py`](../ci/premise.py) | `.github/scripts/` | reads ONE premise by its stable id, mints ids, backfills missing ones |
| [`ci/context_decay.py`](../ci/context_decay.py) | `.github/scripts/` | monthly report: which surfaces may have stopped describing anything alive |
| [`ci/skills_config.py`](../ci/skills_config.py) | `.github/scripts/` + `.claude/hooks/` | the one config reader all of the above share |
| [`hooks/context_hooks.py`](../hooks/context_hooks.py) | `.claude/hooks/` | puts a domain's premises index on the *reasoning* path, not just the file path |
| [`hooks/deep-plan-pr-gate.sh`](../hooks/deep-plan-pr-gate.sh) | `.claude/hooks/` | blocks a PR on a sensitive branch with no premises read and no complete plan-contract |
| [`rules/context.md`](../rules/context.md), [`rules/premises.md`](../rules/premises.md) | `.claude/rules/` | the directives an author follows — the charter and the premise format |

`scripts/install.sh` vendors all of it and merges `settings/hooks.json` into the target's `.claude/settings.json`.
Nothing is stack-specific: every path, glob and ceiling comes from `docs/agents/skills-config.md` in the
consuming repo (schema: `skills/setup/skills-config.template.md`), with the defaults quoted below.

## What the linter checks

| # | Check | Mode | Verdict |
|---|---|---|---|
| C1 | the instructions file within its line ceiling | absolute | FAIL |
| C2 | always-on package (instructions + `@imports` + rules without `paths:`) within its byte ceiling | absolute | FAIL |
| C3 | rule within its line ceiling | ratchet | FAIL on crossing · WARN if already over and growing |
| C4 | rule frontmatter: `description` required, only `description`/`paths`, `paths` a list | absolute | FAIL · WARN on a glob prefix that matches nothing |
| C5 | `SKILL.md` frontmatter: `name` + `description`, known keys | absolute | FAIL on the required ones · WARN on an unknown key |
| C6 | line-number anchor (`file.ext:42`) | delta | FAIL if new · WARN if legacy |
| C7 | relative markdown link resolves in the tree | delta | FAIL if new/regression · WARN if legacy |
| C8 | backticked path exists (root → relative to the citing doc → configured path roots → unique suffix; a gitignored path resolves, an action coordinate is not a path) | delta | FAIL with a known extension · WARN without |
| C9 | a cited migration version has a file under the configured migration dirs | delta | **off** unless `Migration dirs` is configured |
| C10 | a backticked PascalCase symbol exists in the identifier index | delta | **off** unless `Source globs` is configured |
| C11 | a **new** premise carries `**Id:**`, `**Why:**`, `**Breaks:**`, `**Tests:**` | delta | FAIL |
| C12 | a **new** premise's body within the line/byte ceiling (field lines excluded) | delta/ratchet | FAIL over the hard ceiling · WARN over the soft one, or if one already over grew |
| C13 | the classes named in `**Tests:**` exist; a named member appears in that class's file | delta | FAIL if new/regression · WARN if legacy, unnamed or a missing member |
| C14 | a `[[wiki-link]]` between premises matches an H2 title **or** a `p-` id | advisory | WARN |
| C15 | the committed premises index equals what `--write-indices` generates; no orphan index | absolute | FAIL |
| C16 | a `recurring-failure-modes.md` entry has `Mined from`/`Occurrences`/`State`/`Trigger`; `Occurrences` ≤ the changes cited; a `deterministic` gate exists; ids unique | absolute | FAIL |
| C17 | every premise has a unique, well-formed `**Id:** p-xxxxxxxx` | absolute | FAIL on absent/malformed/duplicate · WARN if out of position |

C10's FAIL is restricted to suffixes that **cannot** come from a framework (`*Test`, `*Spec`, `*DTO`,
`*RequestBody`, `*Calculator`, …): if one of those does not exist, the doc is wrong. A framework type cited in a
doc is only a WARN, because the identifier index sees only what this repo defines.

## Delta semantics, by string

The reference checks (C6–C10, C13) compare two trees: **head** is the worktree (untracked files included, so the
gate runs before a commit) and **base** is `git merge-base $CONTEXT_LINT_BASE_REF HEAD`. A reference broken on
head is classified by its **string** — no file or line identity, so a rename, a split or a reflow of the
surrounding doc changes nothing:

- **new** — the string is absent from the base surfaces → **FAIL** (WARN for the advisory checks);
- **regression** — the string was there and **resolved** in every base occurrence → **FAIL**; this is the
  mechanised version of the manual reference sweep;
- **legacy** — the string was there and was already broken → **WARN**, so the gate is green on the day it lands
  and the cleanup happens in a wave of its own.

With no base (`--base` omitted, or a push to the default branch) the absolute ceilings still fail and every delta
check degrades to a warning — the summary line says so. Annotations (`::error`/`::warning`) are emitted only for
files the branch touched; the legacy backlog stays in the log and the count, but never becomes an inline comment
on a file the author never opened.

## What is deliberately out of scope

- Ephemeral docs (`Ephemeral docs`, default `docs/work/**`), generated plan contracts and evaluation fixtures
  (`Never a surface`, default `.claude/deep-plan/**`, `**/eval/**`) are not surfaces.
- The generated premises index is derived: not a surface for C6–C10, not a premises file for C11–C14/C17. C15 is
  the only check that reads it.
- Fenced blocks are ignored everywhere. Code spans are the *source* of C8/C10 and are blanked before looking for
  links (C7) and `@imports` (C2).
- Prose without backticks is not verifiable — a file mentioned without a code span passes on purpose. That
  residue is what a manual reference sweep is for.
- The identifier index never reads `*.sql` or `*.md`: a commented-out statement once resurrected a deleted class,
  and one doc must never vouch for another.

## Running it

```bash
python3 .github/scripts/context_lint.py --base origin/main   # what CI runs: the delta against the base
python3 .github/scripts/context_lint.py                      # ceilings only; the delta degrades to WARN
python3 .github/scripts/context_lint.py --write-indices      # regenerate every premises index (C15)
python3 .github/scripts/premise.py mint                      # an id for a new premise (C17)
python3 .github/scripts/premise.py <id> --deps               # read ONE premise, plus what it depends on
python3 .github/scripts/context_decay.py --since 90          # the monthly decay report
python3 -m unittest discover -s .github/scripts -p '*_test.py'
```

Exit 1 only when there is a FAIL. The CI step `install.sh` prints runs the suite first and the linter second, on purpose: a linter whose own tests are red proves nothing about the tree.

## Adding a check

1. The constant or regex goes in `context_lint.py`, in that check's section.
2. **A red case goes into `context_lint_test.py` in the same commit** — the test builds a throwaway git repo,
   breaks exactly that, and asserts the finding's code and severity.
3. **Run the red case with the check disabled and show it failing**, then restore the check and show it passing.
   A gate nobody has seen red proves nothing: it can be structurally empty (a condition that never fires, a
   `noClasses()` rule that matches nothing) and no one finds out for months. "Seen red, executed" is the
   standard, not "the test is written".
4. A new check lands as WARN while legacy occurrences remain; the promotion to FAIL comes after the cleanup,
   with the before/after count in the PR.

Seen red, for the two checks that write files (the ones most likely to be structurally empty): with the
`check_c15(...)` and `check_c17(...)` calls commented out, `context_lint_test.py` goes
`Ran 64 tests … FAILED (failures=9)`, every failure reading `'C15' not found in set()` / `'C17' not found in
set()`; with the calls restored, `Ran 64 tests … OK`.

## The decay cycle

The linter keeps surfaces *correct*; it says nothing about surfaces that are correct and **dead**. That is the
decay scan (`context_decay.py`), a report — never a gate, always exit 0 — that lists candidates carrying at least
one naming signal. Age qualifies; it never names.

| Signal | What it is |
|---|---|
| D1 `superseded` | a `> **YYYY-MM-DD:** superseded — …` banner in the first 15 lines |
| D2 `broken-ref` | a C6/C7/C8/C9/C13/C14 finding on the file (**never C10** — what is left of it is framework types by design) |
| D3 `unloaded` | the telemetry report marks the surface, or one premise of a file that does load, `zero-load` |
| D4 `dated` | an ephemeral doc past the window with no supersession banner |
| D5 `dead-scope` | a rule whose `paths:` globs match no tracked file — it never loads |
| D6 `unreachable` | a doc not reachable from the docs index by links or backticked paths |
| D7 `promotion-due` | a failure-mode entry with `Occurrences ≥ 2` still `advisory` — a gate is owed |

The human decides per candidate and one PR effects the round; the routine, the decision rules and the banner
grammar are the runbook `bootstrap` scaffolds from `skills/bootstrap/context-decay.template.md`. The rule that
matters most: **a doc that protects a live risk migrates, it does not decay** — the anchor moves to the code or
the premise that carries the risk, and only then does the doc go.

## The hooks

A `paths:` rule fires when a file is **read or edited**. The reasoning path — a prompt naming a domain, a SQL
query naming one of its tables, the first edit in an area nobody consulted — carries no context at all. That gap
is the whole reason the hooks exist.

| Hook | Event (matcher) | Trigger | Output |
|---|---|---|---|
| **remind** | UserPromptSubmit | the prompt names a sensitive domain, or any domain's configured prompt term | one header line + that domain's premises index, ≤ 3 domains per event |
| **area-nudge** | PostToolUse (`Edit`/`Write`) | the path belongs to a domain (config globs), outside the docs tree | one line pointing at the index (and the schema doc, when configured) |
| **mark** | PostToolUse (`Read`/`Edit`/`Write`/`Bash`) | a `file_path` or command naming a premises file | nothing — it writes the `engaged` sentinel |
| **query-remind** | PreToolUse (`mcp__.*`), PostToolUse (`Bash`) | the input names a domain's prompt term (a table name, typically) | the schema pointer + the premises index |
| **charter** | PreToolUse (`Write`) | a file that does not exist yet and matches the charter's `paths:` | the charter body, once per session |

**What a domain is named by** comes from the config's `Domains` table: the domain's own name (bare, and only
for a *sensitive* domain — otherwise every prose mention would fire) plus its **Prompt terms** column, which is
where a repo puts synonyms, other-language words and its table names. That column is what makes query-remind
work at all: it replaces a schema-index parser that only one repo's docs layout would have fitted.

**Contract**: the harness pipes the tool-input JSON on stdin; the hook prints
`{"hookSpecificOutput": {"hookEventName": …, "additionalContext": …}}` on stdout, or nothing. They inform, they
never block. **Fail-open is the design, not a side effect**: a malformed payload, a missing `session_id`, an
absent file or any exception exits 0 with no stdout.

**State** lives in empty sentinel files under `$CLAUDE_PROJECT_DIR/.claude/.context-hooks/{session_id}/`, where
the mtime is the clock: `engaged-{domain}` (someone consulted that domain, TTL 240 min), `reminded-{domain}` /
`nudged-{domain}` (once per session), `charter-shown`. It is **per worktree**, not per machine: two worktrees
never mix state, and it survives a reboot inside the TTL. Sessions older than 24 h are pruned on write.

**The PR gate** (`deep-plan-pr-gate.sh`, PreToolUse on Bash) carries two gates, in order: the *premises-index
gate* (`gh pr create` and `gh pr ready`: the branch touches a sensitive domain whose `engaged-{domain}` sentinel
is missing or stale in **every** session of this worktree → blocked, with the indices to read) and the
*deep-plan gate* (`gh pr create` only: a sensitive branch needs a complete plan-contract). Worktree-wide, not
per session, because the pipeline opens its PRs from a headless session of its own. Both escape hatches are
audited to stderr: `CONTEXT_HOOKS_DISABLE=gate` for the first, `[deep-plan-override: <reason>]` or
`DEEP_PLAN_OVERRIDE=<reason>` for the second.

**Recognition is by argv, not substring.** The command is split on `;`, `&&`, `||`, `|`, `$(`, `(` and newlines;
heredoc bodies are dropped first; `NAME=value` prefixes are skipped; one level of `bash -c` is re-parsed; the
usual wrappers (`nohup`, `sudo`, `env`, `timeout`, `xargs`, `time`, `command`, `stdbuf`, `setsid`) are peeled.
It matches only when argv is literally `gh pr create` / `gh pr ready`. This is not cosmetic: an argv the gate
does not recognise means **no gate runs at all**, which a substring matcher did not risk — so the wrapper list is
part of the contract, and every case is a test.

## Three traps this toolkit exists to avoid

1. **`Read` truncates at 2.000 lines.** "Read the domain's premises file" is an instruction a large domain makes
   impossible to obey — the tail is silently lost and nobody notices. Hence the generated index (one line per
   premise, ~1:13 the size of the bodies), the stable `p-` id, and a fetch command that prints exactly one
   section. Every skill hands agents the index, never the file.
2. **A textual merge of `settings.json` loses hooks in silence.** A duplicate key is *valid* JSON where the last
   one wins, so appending a `"hooks"` block to a file that already has one deletes the existing hooks with no
   error anywhere. `install.sh` merges per event and per matcher in Python, skipping commands already wired —
   which also makes a second run a no-op.
3. **`paths:` only fires on a file read.** The rule that would have saved the session never loads when the
   session is *reasoning*, not reading. That is why the premises index gets injected on the prompt and on the
   query, and why the PR gate asks for the read before the PR, not for a rule that was never triggered.
