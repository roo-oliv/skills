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
| [`hooks/deep-plan-pr-gate.sh`](../hooks/deep-plan-pr-gate.sh) | `.claude/hooks/` | blocks a session commit with no premise trailers, and a PR on a sensitive branch with no premises read and no complete plan-contract |
| [`rules/context.md`](../rules/context.md), [`rules/premises.md`](../rules/premises.md) | `.claude/rules/` | the directives an author follows — the charter and the premise format |

`scripts/install.sh` vendors all of it and merges `settings/hooks.json` and `settings/env.json` into the
target's `.claude/settings.json`.
Nothing is stack-specific: every path, glob and ceiling comes from `docs/agents/skills-config.md` in the
consuming repo (schema: `skills/setup/skills-config.template.md`), with the defaults quoted below.

The installer vendors two more script families that read the same config but answer different questions:
[`ci/agent_telemetry.py`](../ci/agent_telemetry.py) + [`ci/otel_headers.py`](../ci/otel_headers.py)
([`docs/telemetry.md`](telemetry.md) — it is what feeds decay signals D3 and D8), and
[`ci/lint_ratchet.py`](../ci/lint_ratchet.py) + [`ci/refactor_ratio.py`](../ci/refactor_ratio.py)
([`docs/lint-ratchet.md`](lint-ratchet.md)).

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
python3 .github/scripts/context_lint.py --near-duplicates    # premise pairs to merge (report, feeds D9)
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
| D3 `unloaded` | the telemetry report ([`docs/telemetry.md`](telemetry.md)) marks the surface, or one premise of a file that does load, `zero-load` |
| D4 `dated` | an ephemeral doc past the window with no supersession banner |
| D5 `dead-scope` | a rule whose `paths:` globs match no tracked file — it never loads |
| D6 `unreachable` | a doc not reachable from the docs index by links or backticked paths |
| D7 `promotion-due` | a failure-mode entry with `Occurrences ≥ 2` still `advisory` — a gate is owed |
| D8 `quality` | the commit trailers class the premise `confusing` (read **and** violated ≥ 2x) or `undiscoverable` (violated, never read) — telemetry `schema_version: 3` ([`docs/telemetry.md`](telemetry.md)) |
| D9 `near-duplicate` | the premise is paired with another by `context_lint.py --near-duplicates`, passed in with `--duplicates` |

D8 and D9 are decided **before** the rule that keeps a doc cited by a stable surface: neither is a
deletion — rewriting a confusing premise and merging two near-identical ones are things you do to a doc
that is alive. Without `--telemetry` at v3 the scan says so and skips D8; without `--duplicates` it says
so and skips D9, and it distinguishes "no report given" from "report read, zero pairs".

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
absent file or any exception exits 0 with no stdout. The commit-trailers gate below is the only one that
exits 2, and even it allows on an unreadable log, a missing `git` or any exception.

**State** lives in empty sentinel files under `$CLAUDE_PROJECT_DIR/.claude/.context-hooks/{session_id}/`, where
the mtime is the clock: `engaged-{domain}` (someone consulted that domain, TTL 240 min), `reminded-{domain}` /
`nudged-{domain}` (once per session), `charter-shown`. The commit gate's own sentinel lives beside the
load log, in `.claude/telemetry/session-<id>.head`. It is **per worktree**, not per machine: two worktrees
never mix state, and it survives a reboot inside the TTL. Sessions older than 24 h are pruned on write.

**The gates** (`deep-plan-pr-gate.sh`, PreToolUse on Bash) run in order: the *commit-trailers gate* (its
own section below, the only one that blocks a `git commit`), the *premises-index
gate* (`gh pr create` and `gh pr ready`: the branch touches a sensitive domain whose `engaged-{domain}` sentinel
is missing or stale in **every** session of this worktree → blocked, with the indices to read) and the
*deep-plan gate* (`gh pr create` only: a sensitive branch needs a complete plan-contract). Worktree-wide, not
per session, because the pipeline opens its PRs from a headless session of its own. Both escape hatches are
audited to stderr: `CONTEXT_HOOKS_DISABLE=gate` for the first, `[deep-plan-override: <reason>]` or
`DEEP_PLAN_OVERRIDE=<reason>` for the second.

**Recognition is by argv, not substring.** The command is split on `;`, `&&`, `||`, `|`, `$(`, `(` and newlines;
heredoc bodies are dropped first; `NAME=value` prefixes are skipped; one level of `bash -c` is re-parsed; the
usual wrappers (`nohup`, `sudo`, `env`, `timeout`, `xargs`, `time`, `command`, `stdbuf`, `setsid`) are peeled.
A gate matches only when argv is literally `gh pr create` / `gh pr ready` — or, for the trailers gate,
`git commit` with no message-reusing flag. This is not cosmetic: an argv the gate
does not recognise means **no gate runs at all**, which a substring matcher did not risk — so the wrapper list is
part of the contract, and every case is a test.

### The commit-trailers gate

Section 0 of the same script, and the one hook in this toolkit that **blocks**. A `git commit` made
inside an agent session must carry the trailers the session's load log dictates — `Agent-Session`
always, `Premises-Read` for the ids read by `fetch`/`range` since the sentinel (ceiling 40 ids; the tail
degrades to its file), `Premises-Files-Read` for whole-file reads. Why a commit message is the right
store, and what is mined out of it, is [`docs/telemetry.md`](telemetry.md) › Lane A.

The block message hands over the exact strings to copy, because a remediation the model has to
re-derive defeats the point of deriving it from the log:

```
🚫 commit-trailers gate: blocked.

This `git commit` runs inside an agent session and does not carry the premise-quality
trailers. The values below are computed by the hook from this session's load log — they are
not a judgement call, so copy them as they are.

Add to the `git commit`:
  --trailer "Premises-Read: p-1a2b3c4d, p-5e6f7a8b"
  --trailer "Agent-Session: 8f21…"

Same command with them appended (move the flags onto the `git commit` if it is
not the last one):

git commit -m "feat: x" --trailer "Premises-Read: …" --trailer "Agent-Session: …"

Escape (audited): CONTEXT_HOOKS_DISABLE=trailers.
```

Only the **missing** trailers enter the remediation. `git commit --trailer` needs git ≥ 2.32; below it
the remediation becomes text to append to the message. `DEEP_PLAN_OVERRIDE` does **not** disable this
gate — it is not the deep-plan gate.

**What is not a commit** (same argv recognition as the PR gate, wrappers peeled and heredoc bodies
dropped): `echo "git commit"`, `git merge`, `git revert`, and every message-reusing form — `--amend`,
`--fixup`, `--squash`, `-C`/`--reuse-message`, `-c`/`--reedit-message`. Rewriting a commit the agent
already saw — or already pushed — to give it a trailer is exactly the magic this toolkit rules out; a
silent `--amend` was the rejected alternative.

**The window** is the session's load log since the last commit this session annotated, marked by the
sentinel `.claude/telemetry/session-<id>.head` (`{head, ts}`), written on PostToolUse of `Bash` only
when `git rev-parse HEAD` actually moved.

**Which checkout every gate measures** is the repo of the payload's `cwd` (`git rev-parse
--show-toplevel` from there), and only then `$CLAUDE_PROJECT_DIR`, the process toplevel, the cwd. This
is not a detail: an agent isolated in a **git worktree** inherits the mother session's
`CLAUDE_PROJECT_DIR`, so a gate that trusts the variable reads the wrong checkout — the load log, the
sentinel and the branch diff all come from a repository the command never touched. In one production
repo that shipped as a `gh pr create` blocked over six sensitive domains that were not on the branch at
all. It cannot be corrected from the command line either: a hook's environment comes from the harness,
so neither `CLAUDE_PROJECT_DIR=` nor `CONTEXT_HOOKS_DISABLE=` as a command prefix reaches it. The
telemetry logger resolves the root by the same rule on purpose — the writer of the log and the reader of
it disagreeing on the root would lose the signal in silence.

**Known limits.** A commit made outside a session (a human at the terminal) gets no trailer, and that is
the right population: the trailers measure what the agent produced. `git -c x=y commit` escapes the
gate, because recognition requires `argv[0:2] == git commit` — and escaping means *no* gate ran, never
an extra block. And `Premises-Read` is in practice the `fetch` path: a windowed read logs the premise's
title, not its id, so directed fetches by id are what fill the trailer while a whole-file read falls
into `Premises-Files-Read`.


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
