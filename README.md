# skills

Claude Code skills I use daily as a software engineer — a planning → implementation → review
pipeline, the context toolkit that keeps what an agent reads honest, and the measurement that tells
you whether any of it is worth its cost. They're meant to be **portable**: everything reads one
per-repo config file instead of hardcoding a stack, so the same files work on a Kotlin/Spring
backend, a TypeScript/Supabase app, and a C# game engine.

## The skills

**Pipeline (the core loop):**

| Skill | What it does |
|---|---|
| [`refine`](skills/refine) | Turn a raw request (text, a plan file, or a GitHub/Jira/Slack link) into an *approved* plan with a verifiable Contract block, written to `intent/<slug>/{intent,spec,plan}.md`. Replaces interactive plan mode. |
| [`deep-plan`](skills/deep-plan) | Fill and adversarially refute a plan's contract (interaction matrix, dimension table, precondition diff) against the live codebase, before code exists. Engages for changes in sensitive domains. |
| [`implement`](skills/implement) | Implement an approved plan end-to-end into an open PR — wave-based, a fresh agent per wave plus a persistent ledger, verify + reconcile at the end. |
| [`review-fix-loop`](skills/review-fix-loop) | Review → fix over an open PR in a **fixed structure**: breadth → single judge → one consolidated review posted → fix → fix-review by a different model. At most one second pass, and only for a Blocker/High. No rounds, no exhaustion criterion. |
| [`deep-review`](skills/deep-review) | Multi-agent review of a PR/branch/commit/local diff through a universal lens set plus one dedicated lens per flow the repo declares. **Economy (tiered) routing is the default**; `full` puts every lens on the decision tier and switches on by itself for a plan-contract branch or a sensitive domain. |

**Checks (used by the loop, and on their own):**

| Skill | What it does |
|---|---|
| [`verify`](skills/verify) | Run the repo's configured format/lint/build/test pipeline (`config › Verify`) with a fix loop until it's green, then report honestly. Run after any change. |
| [`verify-plan`](skills/verify-plan) | Reconcile an implementation against its plan — Missing (coverage) / Diverged (fidelity) / Unplanned (scope-creep). Cheap, plan-grounded; run per-commit and before a PR. Not a bug hunt. |

**Setup (run once per repo):**

| Skill | What it does |
|---|---|
| [`setup`](skills/setup) | Write `docs/agents/skills-config.md` — the file everything else reads. Interviews you about stack, verify command, docs layout, domains, sensitive domains, flows, conventions, and the optional toolkit/telemetry/ratchet sections. |
| [`bootstrap`](skills/bootstrap) | Scaffold the docs the skills consume — core tenets, per-domain premises, flow docs, the intent README, the recurring-failure-modes file — with real content mined from the code and an interview. |

## The toolkits

Scripts and hooks the installer vendors alongside the skills. Each has its own doc; each reads the
same config.

| Toolkit | Pieces | Doc |
|---|---|---|
| **Context** | `ci/context_lint.py` (CI gate: ceilings, frontmatter, every reference that must resolve; `--near-duplicates` reports premises to merge), `ci/premise.py` (read ONE premise by stable id, or resolve a title to its id), `ci/context_decay.py` (monthly: what stopped describing anything alive), `hooks/context_hooks.py` (puts a domain's premises index on the *reasoning* path, and gates the premise trailers on every session commit), `hooks/deep-plan-pr-gate.sh`, `rules/context.md`, `rules/premises.md` | [`docs/context-toolkit.md`](docs/context-toolkit.md) |
| **Telemetry** | two lanes. **A, git-only (default):** `ci/agent_telemetry.py` — which surface loaded and why, plus `commits`, which mines the premise trailers out of the branch log and the open PRs into a read x violated matrix. **B, OTLP (optional):** `ci/otel_headers.py` (auth header from a repo variable), `settings/env.json` | [`docs/telemetry.md`](docs/telemetry.md) |
| **Workflow measurement** | `ci/wf_timeline.py` — per-agent and per-stage wall-clock, turns and tokens of a `deep-plan` / `implement` run, read off its journal; `--stages` prints each stage's window so real parallelism is visible. Per-skill cost telemetry cannot see Workflow subagents | [`docs/workflow-calibration.md`](docs/workflow-calibration.md) |
| **Lint ratchet** | `ci/lint_ratchet.py` (`config-rides-alone`, `cpd-delta`), `ci/refactor_ratio.py` + its workflow example | [`docs/lint-ratchet.md`](docs/lint-ratchet.md) |

## How portability works

Nothing about a stack is hardcoded. Everything reads **`docs/agents/skills-config.md`** in the repo
it's running in (schema: [`skills/setup/skills-config.template.md`](skills/setup/skills-config.template.md)).
That file declares the verify command, where premises and their index live and how a premise is
fetched by id, which domains are *sensitive* (and so get the heavy planning/review path) and which
words name them in a prompt, the repo's key flows (each becomes a dedicated review lens), the intent
directory, per-role model/effort overrides, the commit/PR conventions, and — optional, each with a
working default — the context-toolkit paths and ceilings, which telemetry lanes are on (and, for lane B,
the OTLP destination) and the lint ratchet's file lists. **A missing section is never an error**: the default applies and the tool says
so in its output.

So the first run on a new repo is:

```
scripts/install.sh /path/to/your/repo
/setup        # write the config
/bootstrap    # scaffold tenets + premises + the lifecycle files (skip what exists)
```

then the pipeline (`/refine` → `/implement` → `/review-fix-loop`, or any skill on its own).

## Install

```bash
scripts/install.sh /path/to/your/repo
```

Re-run to update — the second run changes nothing. What it does:

- copies `skills/`, `workflows/` and `hooks/` into the target's `.claude/`, one skill directory at a
  time, so a file removed upstream doesn't linger and skills the target owns are left alone;
- vendors `ci/*.py` **and their tests** into `.github/scripts/` (the CI step discovers the tests
  there), plus a copy of the config reader beside the hooks — a hook resolves imports from its own
  directory;
- writes `rules/*.md` into `.claude/rules/`;
- **merges** `settings/hooks.json` and `settings/env.json` into `.claude/settings.json`: hooks per
  event and per matcher, `env` and other top-level keys only when the target does not already have
  them. Your own values always win. A duplicate JSON key is *valid* JSON where the last one wins, so
  a textual merge would silently drop what it meant to add;
- prints the CI step to paste into your pull-request workflow, and points at
  [`ci/workflows/refactor-ratio.yml.example`](ci/workflows/refactor-ratio.yml.example).

The pure-prose skills are also listed in
[`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) for `npx skills add roo-oliv/skills`; the
workflow-backed ones (`deep-plan`, `implement`, `review-fix-loop`) need the vendored copy so their
`.js` resolves inside the repo.

## Repo layout

```
skills/<name>/SKILL.md      # the skills (some with agents/ role files + reference docs)
workflows/<name>.js         # deterministic multi-agent workflows the heavy skills invoke
ci/*.py                     # scripts CI and the hooks run → vendored to .github/scripts/
ci/workflows/               # workflow examples to copy, not installed
rules/*.md                  # conventions vendored to .claude/rules/
settings/*.json             # settings.json fragments the installer MERGES
hooks/                      # context hooks + the PR-create gate
docs/                       # this repo's own docs: context toolkit, telemetry, lint ratchet, workflow calibration
scripts/install.sh          # vendors all of the above into a target repo
```

## Why it's shaped this way

Every constant here — how many rounds, how many lenses, which model on which role — encodes something
a model of a given generation did not do on its own. The load-bearing ones:

- **Two review rounds capture most of what's findable.** Public measurements put two rounds at
  76–95 % of the achievable improvement (across several models and benchmarks), and reviewers that
  cap at two are the norm. So `review-fix-loop` has a fixed structure, not a round machine: breadth →
  judge → fix → fix-review, and at most one second pass.
- **A reviewer told to find gaps will find some.** Anthropic's own guidance says so, which is why the
  posting predicate is numeric (`confidence ≥ 8`) plus a regex exclusion list — not prose asking the
  model to be moderate. Moderation prompts and self-judged severity have been measured to fail; a
  rejected-class list plus a numeric cut is what worked.
- **Don't use a subagent to verify its own work.** Same guidance. The fix-review runs on a model
  *different from the fixer*, over the fix diff only.
- **Reviewer ≠ author** is the cheapest quality lever there is, and it costs almost nothing: the fix
  is one agent per pass against eight to thirteen reviewers, so the expensive model belongs on the
  small slice.
- **Expensive planner, cheap workers.** An agent that *judges* a load-bearing quantity or writes what
  ships gets the strong model at high effort; an agent that *collects evidence under a checklist* gets
  the cheap one at medium. Thinking is never switched off — effort is lowered instead, because
  thinking at low effort beats no thinking at the same price.
- **`Read` truncates at 2 000 lines.** That single fact is why premises are addressed by stable id
  through a generated index instead of "read the domain's premises file": a large domain silently
  loses its tail, and nobody notices.
- **Re-audit the harness on every model upgrade.** Each constant was calibrated against a model that
  no longer exists after an upgrade. Re-run a baseline set of PRs, compare cost per PR and valid
  findings, and update the numbers — which is what the telemetry above is for. The reasoning behind
  each workflow constant, the per-role model tables and the audit log live in
  [`docs/workflow-calibration.md`](docs/workflow-calibration.md).

## Gotchas

Things that cost real debugging time here:

- **A textual merge of `settings.json` duplicates JSON keys.** It is valid JSON where the last key
  wins, so the file *looks* merged and silently loses half of it. `install.sh` merges structurally.
- **Don't pre-merge a neighbouring branch in a squash-merge repo.** The PR diff then shows the
  neighbour's work as yours, and a `mv` of a file the neighbour deleted resurrects it.
- **Parallel agents share the scratchpad.** Two agents writing `notes.md` overwrite each other — give
  every draft a name unique to the agent.
- **Subagents don't have the `Workflow` tool.** Probes and real workflow runs are the coordinator's
  job; a subagent asked to "run the workflow" will improvise something else.
- **A rule's `paths:` only fires when a file is read or edited.** A prompt that names a domain, or a
  SQL query against its tables, reaches none of them — that path needs a hook, which is what
  `context_hooks.py` is for.
- **`CLAUDE_CODE_SUBAGENT_MODEL` overrides everything.** It is first in the model-resolution order, so
  setting it collapses every tier into one model — including reviewer ≠ fixer. The `review-fix-loop`
  preflight aborts when it finds it set.
- **`$CLAUDE_PROJECT_DIR` does not expand inside `otelHeadersHelper`** (unlike hooks), so that value
  resolves the repo root with `git rev-parse --show-toplevel`.
- **Hooks are read once, at session start.** Changing a gate does not affect the session you changed it
  in — open a new one before concluding the gate does not fire.
- **A subagent inherits its mother's `session_id`.** `Agent-Session` therefore identifies the *session*,
  not the agent; a fan-out of ten lenses is one session in the trailer report.
- **An agent isolated in a git worktree still inherits `$CLAUDE_PROJECT_DIR` from the session that
  spawned it**, so a hook that trusts the variable exercises the *wrong* checkout — not the worktree the
  command runs in. Every gate here resolves the repo from the payload's `cwd` instead, and a hook's
  environment comes from the harness, so no command prefix can correct it after the fact.
- **`gh pr list --json commits` is rejected over 500 000 GraphQL nodes** — `PRs x commits x authors` in
  one query — and the source then reads as *empty*, silently. List the numbers, then ask each PR for its
  commits.
- **Dedupe trailer blocks BETWEEN sources, never inside one.** Two commits of the same session that read
  no premise carry byte-identical trailers; collapsing them undercounts the window.

## Invocation

Two kinds, following the `disable-model-invocation` convention:

- **User-invoked** (`disable-model-invocation: true`): reachable only when you type the slash
  command. `setup`, `bootstrap`, `review-fix-loop`, `implement`.
- **Model-invoked** (default): the model can reach for them automatically when a task fits, or you can
  invoke them. `refine`, `deep-plan`, `deep-review`, `verify`, `verify-plan`.
