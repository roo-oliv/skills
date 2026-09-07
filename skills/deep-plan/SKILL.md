---
name: deep-plan
description: "Planning-time mirror of deep-review: fills and adversarially refutes a plan-contract (interaction matrix, dimension table, precondition diff) from design intent + the live codebase, before code exists. Auto-engages in plan mode for sensitive-domain status/lifecycle/flow-replacement changes; self-tiers to a light inline pass for trivial or non-sensitive plans."
argument-hint: "[intent file path] [full|reduced]"
---

`deep-plan` is `deep-review` run **before** code exists: it takes a *design intent* (plan-mode prose) + the *live codebase* and emits a **filled, refuted plan-contract** — the four artifacts of the repo's plan-contract spec (`docs/agents/skills-config.md` › Docs layout › Planning; default `docs/planning/plan-contract.md`): Contract block, interaction matrix, dimension table, precondition diff — every cell answered, every derived quantity's base tagged, every guard copy diffed. That artifact is what `/verify-plan` later reconciles the implementation against.

**Why it exists.** The review side is industrial (lenses + consolidator + facet enumeration); the planning side was a single pass, so the matrix shipped with the columns the planner remembered, the dimension table with the variables they happened to name. The same bug class — a deferred/RESERVED record invisible downstream — shipped twice and still needed many review rounds the second time. The missing piece is institutional learning at *plan* time, not another reviewer.

Invocation:

- `/deep-plan` — the current plan-mode draft is the design intent.
- `/deep-plan path/to/draft.md` — read the intent from a file.
- Append `reduced` for the two-refuter pass, `full` for every agent on the decision tier; the default is `economy` (`full` also switches on by itself when the branch carries a plan-contract).

Everything stack-specific comes from `docs/agents/skills-config.md` (where docs live, which domains are sensitive, flows, conventions). When a section is absent, fall back to the stated default and **say so in the output**.

## Phase 0 — Tier

Detect and strip the tier token (`full` / `reduced`), then classify the intent against the repo's **Sensitive domains** (config › Sensitive domains). **If none are listed, treat no domain as sensitive — take the light path and never block**, and say so.

**The self-critique paradox: do not over-critique easy tasks.** A heavy enumerate→refute→gate loop on a one-line config tweak is noise that teaches the user to ignore the skill.

- **light — inline, no fan-out.** The intent touches no sensitive domain, or touches one only cosmetically (rename, doc, dependency bump, a single non-stateful field). Do Phase 1 yourself, then fill the artifacts the change actually needs (often just the Contract block) in one pass. Say: "Light pass — no sensitive-domain lifecycle change detected." (With no Sensitive domains configured: "Light pass — no Sensitive domains configured.")
- **reduced — Workflow, `tier: 'reduced'`.** A sensitive domain **and** a single-seam change, or a swap of the formula / base of an existing derived quantity, with **no** new status and **no** state machine. Same DAG, 2 refuters (quantity + completeness lenses).
- **heavy — Workflow, `tier: 'economy'`, or `'full'` when the token came.** A sensitive domain **and** an added/modified entity status or lifecycle, a new state machine, a long-lived RESERVED/PENDING record, or a replaced flow/method.

Unsure between tiers in a sensitive domain: prefer the heavier one and say why — a false-heavy is cheap, a false-light ships the gap.

## Phase 1 — Brief (both Workflow tiers)

Gather the Phase-1 reading **once, yourself**, and pass it as `args.brief`. Every agent used to re-read the same recurring-failure-modes doc, core tenets and premises files — the largest measured per-agent context cost, paid ~13 times a run, and `Read` truncates a large premises file at 2 000 lines anyway. The brief is one read pass instead of fourteen. Template, ≤ 15 lines:

```markdown
Domains: <list> — this change is X, NOT Y; ignore any artifact mentioning Y.
FM-<n> <title> — the trigger that matched → the artifact response it demands.   (one line per matching trigger)
Core tenets: <section titles that bear on the change>.
Premises <domain>: <id> <one-line essence> · <id> …
Schema: <schema doc> — tables <t1>, <t2>.
Flows: <flow doc> — the invariants it declares for this path.
```

Premise ids come from the premises index (config › Docs layout); open a body by id with the repo's premise-by-id command (default `python3 .github/scripts/premise.py <id>`) — never a whole premises file. Read the flow doc of every flow the intent touches (config › Flows, frontmatter `covers:` globs): a flow doc is the dedicated core-tenet for that flow, and its invariants are exactly what the dimension table and the matrix hold the change to.

Then run the Workflow `.claude/workflows/deep-plan.js` with **an object** (the runtime delivers `args` as a JSON-encoded *string* and the script `JSON.parse`s it defensively; a hand-built string silently zeroes the intent and runs the whole pass on a blank brief):

```
args: { intent, domains, repoRoot, brief, tier?, models?, roles?, refutersPerRound?, refuteRounds? }
```

`repoRoot` = `git rev-parse --show-toplevel` — every agent search is scoped to it, and role files resolve as `${repoRoot}/.claude/skills/deep-plan/agents/…`. Preflight quarantine first: `mkdir -p ~/.deepplan-quarantine && mv /tmp/deep-plan-*.md ~/.deepplan-quarantine/ 2>/dev/null || true`.

For a **light** pass, do the same gathering inline and skip the Workflow.

## Phases 2–5 — the DAG (inside the Workflow)

- **Enumerate ∥ Analyze — one `parallel`.** Agent 2 greps the interaction-matrix **columns** (every site that reads or transitions the affected state; a column with no `file:line` seam is pruned — a genuinely-missing one is re-added by a refuter *with* a site) and chains agent 4 inside its own thunk. Agent 3 greps the state-mutation seams and **every copy** of each touched guard, returning each copy's precondition row already filled. Agent 1 builds the dimension table; agent 5 emits the premise/test obligations **and** the Contract block, which the engine folds with the failing-first tests deterministically. Only the matrix cells depend on another agent, so everything else starts at t=0.
- **Matrix cells by row, sized by cells.** Row shards (`splitByCells`, ~80 cells per agent, capped) answer the grid; the deterministic `missingCells` then drives ONE targeted fill over exactly the pairs still empty. Every axis carries a leading code (`S3`, `C12`) and every cell is landed on an axis by code / seam / symbol before anything keys on it, so a shard that paraphrases its label cannot mint a phantom row.
- **Refute — one breadth round.** `REFUTERS_PER_ROUND` lensed refuters in parallel (quantity/dimensional · async-ordering/lifecycle · premise/invariant · completeness/wrong-cell; the first and last in `reduced`), each seeing **only** the draft + the intent — anchoring-free, which is what breaks the cascade where planner and reviewer share a blind spot. Each is fed the already-open roots so it pushes to new territory, tags every refutation `resolution` vs `new-surface`, and returns its **own reopening patch** (reopen a cell as GAP, add a column/state with its cells, amend a precondition row — never propose the fix). Patches merge conservatively: on a collision GAP wins, so no refuter can bury another's reopening. `refKey`/`freshByRound` are computed *before* the merge.
- **One round, not a loop.** Every recorded trajectory is FLAT — later rounds mint as many fresh refutations as the first, and their `attacks` mix shows most of them attacking the previous round's resolutions: fix-attack equilibrium, not discovery. Depth is 1 (`ARGS.refuteRounds` opts into more); coverage comes from **breadth** and from an **independent re-run** (below).
- **Gate — programmatic completeness.** It flags an empty or unjustified-`GAP` cell, a load-bearing premise with no `require`/`check` seam or no failing-first test, and a touched guard with no per-copy precondition row. Empty pairs are filled per column, in parallel, *first*; the bounded justify loop (`GATE_JUSTIFY_ROUNDS`) then sees only what a fill cannot close. A residual is **recorded (`gate: FAIL`, `residualGaps`, `violations`) and surfaced, never thrown** — deep-plan never blocks, and a flagged contract beats a 0-byte output.
- **Synthesize.** `renderVerdict()` and `renderArtifacts()` render the header and the four artifacts deterministically in JS — lossless, no output-token ceiling; the state axis is canonicalized first (`canonicalizeMatrixStates`), and columns citing one seam collapse (`canonicalizeColumnsBySeam`), GAP winning every merge. The consolidator (`agents/consolidate.md`) writes **only** `## Síntese`: the GAP/refutation clustering into the BLOCKERs the planner must decide, and the **contradiction watch** — two commitments giving conflicting directives for the same seam, which the gate (completeness, not consistency) cannot catch. Three checks then run over the rendered body as a **regression guard on the engine's own render**: `consolidationFidelity` (dropped item), `contractBlockCoverage` (item buried outside the numbered `## Contract` block), `structuralCounts` (a collection rendering fewer rows than the draft holds). A hit is an engine bug — logged and returned, never patched by an LLM.

Model routing lives in the `ROLE` table of `.claude/workflows/deep-plan.js` — **the `.js` is the executable source of truth.** Rationale, tier semantics and the audit log: [`docs/workflow-calibration.md`](../../docs/workflow-calibration.md). `args.models` overrides a whole tier (from config › Models); `args.roles` overrides one role's model/effort for an A/B without editing the script.

## Phase 6 — Verdict (advisory-loud) and next step

deep-plan **never blocks** plan finalization — plan mode is used for one-liners too, and a hard gate there needs a fragile escape hatch. It surfaces residual gaps prominently, then asks what to do with `AskUserQuestion`.

The **Workflow pass already emits its own verdict header** — `## deep-plan contract — …`, a `### Verdict` block (contradiction count first, substantive-GAP count + seam clustering, refute status + per-round trajectory shape + final-round resolution/new-surface mix, reframed gate verdict) and a `### ⚠️ Unresolved` list. Present that body as-is; you only add the one-line banner above it:

```markdown
**Mode**: {full | economy | reduced} · **Tier**: Heavy (Workflow)
```

For a **light inline pass** (no Workflow), prepend the full header yourself:

```markdown
## deep-plan: {plan name or intent summary}

**Mode**: {full | economy} · **Tier**: Light inline
**Affected domains**: {list}
**Residual GAPs**: {count — 0 means the contract is complete}
```

If there are residual GAPs, list them first, each as `⚠️ GAP: <state> × <event/variable> — <why unresolved>`. Then `AskUserQuestion` — options:

1. **Write artifacts into the plan** — append the filled Contract / Matrix / Dimension table / Precondition diff to the plan file (or the in-session draft), replacing any hand-filled versions. A standalone contract goes to **`.claude/deep-plan/<branch>-<shortSha>.md`** (`git rev-parse --abbrev-ref HEAD` + `git rev-parse --short HEAD`) — unique per run and exactly what the PR gate reads.
2. **Save gap report to file** — a **run-unique** path `.claude/deep-plan/<branch>-<shortSha>/report.md` (or the repo's own scratch dir), never a shared `/tmp/deep-plan-{name}.md`.
3. **Proceed anyway** — leave the plan as-is; the GAPs are recorded in the conversation (a deliberate, justified exclusion).
4. **Nothing** — keep it in the conversation output.

The real choke point is downstream: the `gh pr create` PreToolUse hook (`hooks/deep-plan-pr-gate.sh`, installed under `.claude/`) blocks a sensitive-domain branch whose plan-contract is incomplete or whose `/verify-plan` is not clean. deep-plan at plan time is advisory; **the hook at PR time is the gate.**

## Key rules

- **Enumerate from the codebase, never from memory.** Matrix columns and guard copies are *grepped*, not recalled; a column set that matches your memory is suspect — grep anyway.
- **The intent is the source of truth for what is proposed; the codebase is the current state.** A contradiction between them is a finding, not something to silently reconcile.
- **The dimension axis is king.** A load-bearing derived value with no tagged base or no cap seam is a Blocker-class gap. A currency amount is the canonical case; a count, a window-bounded sum, or a ratio on the wrong base is the same bug.
- **Refute independently.** The refuter sees the draft, never the reasoning that produced it.
- **Tier honestly.** Don't fan out on a trivial or non-sensitive plan; don't light-pass a sensitive-domain lifecycle change.
- **Re-audit on model upgrade.** Every constant here — refuter breadth, round depth, cells per agent, which role gets which tier — encodes what the models of its day did not do alone. See `docs/workflow-calibration.md` › Re-audit policy.

## Hygiene

- **Search with `rg`, scoped to `repoRoot`; never `grep -r`.** `rg` honors `.gitignore`, so it skips build output, the VCS dir and sibling **worktrees** — a bare `grep -r` from a worktree has hung a run for over an hour. Never read or search `/tmp`, `..`, `~`, or any path outside the repo. Applies to the light pass too.
- **One simple command per Bash call** (a fragile `rg … | head; echo; find …` one-liner hangs on an unbalanced quote), and bound every DB/MCP query (`LIMIT`). Locate with `rg -n` and read in ranges — never a whole file over 200 lines.
- **Contamination guard.** Running two deep-plans concurrently, an agent that searches broadly can read *another run's* artifact and silently re-anchor the whole contract to the wrong feature. The inline intent is the only source of truth; when two live branches touch the same domain, spell it out in the intent text: "this change is X, NOT Y; ignore any artifact mentioning Y."
- **Run-unique outputs.** Key every saved artifact by `<branch>-<shortSha>` (append `-<unixTimestamp>` for a re-run on the same commit). Unique *naming* alone is insufficient — the real fix is that agents no longer read shared temp or sibling worktrees at all.
- **Terse fields.** `file:line` + one clause, ≤ 240 chars per field; an oversized draft makes `StructuredOutput` return nothing and wastes the run. Don't relax the caps.

## One run does not exhaust

- A single heavy pass samples a *fraction* of the finding space, and a refute round that surfaces nothing new is **not** proof it found everything. Across four dogfood runs of the same plan, two of them agreed on ~4 core clusters while each caught 4–6 the other missed, and a run at half the budget of the widest one still surfaced a blocker.
- For a high-stakes sensitive-domain plan, **run 2+ times and union**: a finding present in *any* run is in scope. Each run already writes `.claude/deep-plan/<branch>-<shortSha>.md`.
- **Strike revoked items — never append a revision beside the commitment it supersedes.** Union debt is what produced a run opening with 7 contradictions, all of them a stale unioned commitment sitting beside its replacement.
- **Re-refute the union**: feed the unioned contract as the next run's intent. Its contradictions render first — resolve them before reading anything else.
- A run with **zero new load-bearing findings** is the convergence signal; two in a row is dry. Leading indicator: more top findings *revoking* earlier decisions than discovering surface.
- **Breadth beats depth.** Read the `### Verdict` trajectory shape, not a `gate: PASS`: FLAT means re-run, DECAYING means one more round would close it. `refuteRounds` buys little; per-round breadth (`refutersPerRound`) and an independent re-run buy a lot.

## Measuring a run

`python3 .github/scripts/wf_timeline.py <wf_dir> --stages` over the run journal (`~/.claude/projects/<project>/<session>/subagents/workflows/wf_*`) gives wall-clock, turns and tokens per stage plus each stage's window (`start_min` → `end_min`, so overlap and serial retries are readable straight off the table). Per-skill cost telemetry does not see Workflow subagents at all — see [`docs/telemetry.md`](../../docs/telemetry.md). Measure before cutting.
