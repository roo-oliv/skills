# Workflow calibration

Every constant in `workflows/*.js` — how many refuters, how many rounds, how many cells per agent,
which role gets which model at which effort — encodes something a model of a given generation did
not do on its own. **The `.js` is the executable source of truth**; the tables here are a pointer to
it plus the reasoning and the audit trail behind each number.

This is the doc a `deep-plan` or `implement` run's forensics land in, so the SKILL.md files stay
procedure. If you are here to change a constant, read *Re-audit policy* last — the rule is measure
before cutting.

## Expensive decider, cheap enumerator

- **Decider** — judges a load-bearing quantity or writes what ships (dimension table, state-mutation
  seams, refuters, gate justifier, consolidator, `implement`'s waves, the fixer, the fix-reviewer,
  `verify-plan`): the strong model at `high`.
- **Enumerator / worker** — collects evidence under a checklist (matrix columns, matrix cells,
  premise/test obligations, scope, conciliator, PR author): the cheap model at `medium` or `low`.
- **Thinking is never disabled — effort is lowered instead.** Thinking at low effort beats no
  thinking at the same price.
- **Reviewer ≠ author, and fix-reviewer ≠ fixer.** The cheapest quality lever there is: the fix is
  one agent against eight to thirteen reviewers, so the expensive model belongs on the small slice.
- **Never set `CLAUDE_CODE_SUBAGENT_MODEL`.** It is first in the model-resolution order, so it
  collapses every tier into one model — including reviewer ≠ fixer. The `review-fix-loop` preflight
  aborts when it finds it set.

A repo overrides the tier names verbatim in `docs/agents/skills-config.md` › **Models**; the skill
reads that section and passes it as `args.models`. Anything omitted keeps the workflow's default.

## The role tables

`deep-review` / `review-fix-loop`:

| Role | Tier | | `deep-plan` | Tier | | `implement` | Tier |
|---|---|---|---|---|---|---|---|
| `scope` | worker/low | | `a1` dimension-table | decision | | `setup` | worker |
| `lensDeep` (derived-quantity, flow lenses) | decision | | `a2` matrix-columns | worker | | `wave` (+retry, `recon-fix`) | decision |
| `lensWorker` (adjacent, negative-space, contract, tests) | worker | | `a3` state-mutation-seams (+ the precondition rows) | decision | | `verify` | decision/medium |
| `standard` | decision | | `a4` cells by row · targeted fill · gate fill by column | worker | | `verifyPlan` | decision |
| `judge` (consolidate) | decision/medium | | `a5` premises-tests (+ the Contract block) | worker | | `prAuthor` | pr-author |
| `conciliate` | worker | | `a6` refuters (each with its reopening patch) | decision | | | |
| `fixer` | fixer | | `justify` (gate, non-cell violations only) | decision | | | |
| `fixReview` | fix-review | | `consolidate` (owns the contradiction watch) | decision | | | |

## Tiers

- **`economy`** — the default for the review fan-out and for `deep-plan`. The reasoning-heavy agents
  are already on the decision tier; the enumerators stay cheap.
- **`full`** — upgrades the enumerators to the decision tier. It switches on by a **deterministic
  trigger** — the branch carries a plan-contract (`.claude/deep-plan/*.md` in the diff), the artifact
  `/deep-plan` produces for a sensitive-domain lifecycle change and the PR-gate hook demands — and by
  the explicit `full` token.
- **`reduced`** (deep-plan only) — a single-seam change, or a swap of the formula / base of an
  existing derived quantity, with no new status and no state machine. Same DAG, **2 refuters** on the
  quantity and completeness lenses. What the reduced tier cuts is the enumeration fan-out, never the
  refutation: the refutation is the best ROI in the pipeline (measured at ~4 % of a pipeline's tokens
  for the run's only planning Blocker). `/refine` Phase 5 delegates to `/deep-plan <plan> reduced`
  rather than hand-rolling a refuter.

## Per-run overrides (`args`)

`deep-plan.js` reads its knobs from the invocation `args`, so a benchmark arm needs no edit to the
script:

- **`models`** — the TIER map, normally straight from config › Models (`{ worker: { model, effort } }`).
- **`roles`** — one ROLE's model and/or effort (`{ a6: { model: 'sonnet' }, a2: { effort: 'low' } }`),
  applied **after** the `full`-tier upgrade, so an explicit override always wins. An unknown role or a
  non-object value is logged and ignored: a typo in a benchmark arm must never throw away a long run.
- **`tier`** — `economy` (default) · `full` · `reduced`. An unknown value is logged and falls back to
  `economy`.
- **`refutersPerRound`** (default 4, or 2 in `reduced`) and **`refuteRounds`** (default 1).
- **`cellsPerAgent`** (default 80) and **`maxFillAgents`** (default 4) — the sharding of the matrix
  work.
- **`brief`** — the Phase-1 reading list the orchestrator assembled once (see the skill's Phase 1).

**A/B protocol.** Same `intent`, same `domains` and the **same `brief`** on both arms. Compare
`python3 ci/wf_timeline.py <wf_dir> --stages` (wall-clock, turns and tokens per stage) against the
counters of the run's `### Verdict` block (fresh refutations and their new-surface share, GAP cells,
distinct seams, contradictions, gate). `result.roles` reports the routing actually dispatched, so an
arm is auditable from the result alone.

## Why the deep-plan DAG looks like this

The forensics behind the current shape, kept out of the SKILL.md:

- **Label drift, not missing analysis, was the scaling bug.** Refuters and fill agents write cells
  under drifting label variants — `"S1"` vs `"S1 PERFORMANCE_BONUS_RETAINED"`, `"C1"` vs
  `"C1 mint hook"`. With the gate keyed by exact string, one wide run wrote cells under 68
  column-labels that collapse to 34 real codes and 22 state-labels that collapse to 15, so the gate
  saw **374 phantom-empty grid pairs**, a single bulk justify re-created 374 duplicates, and the real
  analysis sat in 105 orphaned cells. Gate violations tracked refuter count (6→1, 6→84, 20→374):
  **fix the data structure, don't cap the loop.** Hence `normCode` keying, then coded axes
  (`S#`/`C#`) minted at enumeration, then `resolveAxis` landing every cell before anything keys on
  it, and `canonicalizeMatrixStates` / `canonicalizeColumnsBySeam` collapsing what is left.
- **Paraphrase is the same bug one layer down.** With the matrix sharded by row, five of seven shards
  paraphrased the verbose state label they were handed; without codes on the axes each paraphrase
  became a phantom state, the targeted fill re-did 93 cells, and the gate then faced
  phantom-states × columns of empty pairs — dozens of fill agents. Codes make the label free text
  again, which is why every fill prompt says "the leading code alone is enough".
- **Per-agent fixed cost dominates the sharding decision.** One agent per state (or per column) was
  the first cut and it lost: every agent writes its whole prompt to cache before its first turn, so
  30 single-column gate fills cost more than the one expensive justify pass they replaced. Hence
  `splitByCells` — size the shards by **cells** (~80 per agent), cap the agent count, and drop the
  full draft summary from a fill prompt that does not need it.
- **Breadth beats depth, once the amplifier is fixed.** Every recorded refute trajectory is FLAT:
  later rounds mint as many fresh refutations as the first, and the `attacks` mix shows most of them
  attacking the previous round's resolutions — fix-attack equilibrium, not discovery. A 4×5 run did
  reach the union of two narrower runs, but at ~1.8× cost, with no convergence, and with the
  374-violation gate above, because more refuters *amplified* the then-unfixed label drift. With the
  amplifier fixed twice over, breadth was re-raised 2→4 deliberately and depth dropped to 1. Refuters
  run in parallel, so breadth is ~free on wall-clock: it trades tokens for coverage.
- **Whoever holds the evidence writes the reopening.** The per-round resolver hop existed only to
  retype what the refuters had already found, and it was minutes of serial wall-clock per run. A
  refuter now returns its own `patch`; patches merge **conservatively** (`mergeCell`, GAP wins), so
  refuter 4 cannot bury refuter 1's reopening, and the same conservative mode protects both matrix
  fills from burying a reopening they were not asked about.
- **Patch, never the whole draft.** Re-emitting the full — and growing — draft each round is what hit
  the 64k output-token ceiling: a late refuter found its strongest refutation and the integrating
  agent then returned nothing, silently dropping it. `DRAFT_PATCH` + `applyPatch` merge by key, and
  the merge never deletes, so a thin or malformed patch degrades to "the gate still fails", never to
  silent corruption.
- **Render the artifacts in JS.** An LLM retyping a large contract drops rows — one run rendered 54
  of 70 contract items. `renderArtifacts`/`renderVerdict` are deterministic and lossless; the
  fidelity / contract-block / structural-count checks became a regression guard on that render, and
  the consolidator was left only the narrative and the contradiction watch.
- **Never throw on a residual gap.** A run threw at the gate after a refuter grew the matrix and lost
  ~3M tokens and two hours *before synthesize ever ran*. A flagged contract is incomparably more
  useful than a 0-byte output.

Effect of the parallel DAG, measured in one production repo on the same intent, before and after:
wall-clock **−44 %**, output tokens **−49 %**, cache read **−20 %**, at equal refutation coverage
(same fresh-refutation count and same new-surface share) and the same agent count.

## Why `implement` looks like this

Measured across two full runs: the waves are ~70 % of wall-clock and ~80 % of tokens; inside a wave
the build is 12–33 % and the model 70–90 %, with hundreds of KB of whole-file reads per wave and one
wave reaching 199 turns.

- **`verify` ∥ `verify-plan`.** The reconciliation reads the diff the waves already committed, so it
  does not depend on the verify — the two open together.
- **One final build.** After a `recon-fix` there is a single full build, in parallel with the
  re-check; the PR author builds again **only** if merging the base brought new commits. Before this,
  a run built the whole project twice for nothing.
- **Merge, never rebase.** `git merge --no-edit origin/<base>`, and never force — not even
  `--force-with-lease`. A rebase rewrites commits reviewers have already read.
- **Waves with continuation.** A wave that runs past its context budget ships what is green and
  returns `partial` + `remaining`; the loop is a queue and enqueues `N.1` with a fresh agent, up to
  `maxWaves + 3` executed waves. `partial` is not failure — a degraded agent pushing to the end is.
  The setup's size ruler (**≤ 6 contract items and ≤ 8 files per wave**) is what keeps continuation a
  valve rather than the plan.
- **Mechanical read hygiene in the wave agent.** Range reads instead of whole files, `Explore` for
  caller sweeps, build output tailed to a log. Context is the scarce resource of a wave.

## Measuring a run

`ci/wf_timeline.py` (vendored to `.github/scripts/`) reads a Workflow run's journal directory —
`~/.claude/projects/<project>/<session>/subagents/workflows/wf_*` — and prints, per agent, its role,
model, window, turns, tool calls and the four token counters; `--stages` rolls the agents up into
their workflow's stages with each stage's **window** (`start_min` → `end_min`, minutes from the run
start). Overlapping windows ran in parallel; a wide window with a small `max_min` ran in series.
Classifying a stage as parallel-or-serial would describe neither workflow — `deep-plan` fires
enumerate and analyze inside one `parallel()`, and `implement`'s verify stage holds a parallel pair
*and* its serial retries.

Roles come from the `[<workflow> role: <label>]` marker every prompt carries on line 1, with a
fallback for runs recorded before the marker existed. Per-skill cost telemetry is **not** a
substitute: Workflow subagents arrive without a skill attribution, so a per-skill cost widget only
ever sees the main thread while the run journal shows tens of millions of tokens for the same run
([`telemetry.md`](telemetry.md)).

## Re-audit policy

Each constant was calibrated against a model that no longer exists after an upgrade. On a CLI model
upgrade: re-run a baseline set of plans/PRs, compare cost per run and valid findings, update
`meta.calibratedFor` in each workflow and add a row below. **Measure before cutting** — the
instrument above is what makes "this stage is the long pole" a fact instead of a guess.

| Date | What changed |
|---|---|
| 2026-08 | `ROLE` per role; fixed review structure (breadth → judge → fix → fix-review); one refute round; single judge with a numeric posting predicate |
| 2026-09 | deep-plan: Enumerate ∥ Analyze, matrix by row shards, refuters carrying their own patch (no resolver hop), gate fills by column, orchestrator brief, `reduced` tier, coded axes. implement: verify ∥ verify-plan, one final build, merge instead of rebase, waves with continuation. Both: the role marker and `ci/wf_timeline.py` |
