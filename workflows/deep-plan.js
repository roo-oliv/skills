export const meta = {
  name: 'deep-plan',
  description: 'Fill and adversarially refute a plan-contract (matrix, dimension table, precondition diff) from design intent + the live codebase, gating on completeness before synthesis.',
  whenToUse: 'Heavy pass of the /deep-plan skill: a sensitive-domain change that adds/modifies a status/lifecycle or replaces a flow. Invoked by .claude/skills/deep-plan/SKILL.md.',
  calibratedFor: 'Frontier models as of 2026-09 — re-audit on each model upgrade (docs/workflow-calibration.md).',
  phases: [
    { title: 'Enumerate', detail: 'grep caller-enumerated matrix columns + state-mutation seams + guard copies (∥ Analyze)' },
    { title: 'Analyze', detail: 'dimension table, matrix cells sharded by row, premise/test obligations + Contract block' },
    { title: 'Refute', detail: 'one round of anchoring-free lensed refuters, each returning its own reopening patch' },
    { title: 'Gate', detail: 'programmatic completeness: no empty/unjustified cell, every load-bearing premise executable, every guard copy diffed' },
    { title: 'Synthesize', detail: 'deterministic verdict header + lossless artifacts (rendered in JS); LLM writes only the narrative synthesis' },
  ],
}

// =====================================================================
// Structured-output schemas (one per specialist) — gates read these, so
// they are programmatic, not prose.
// =====================================================================

const BASE_ENUM = ['face', 'residual', 'principal-only', 'with-interest', 'net-of-reserved', 'discount-net', 'other']

const DIMENSION_TABLE = {
  type: 'object',
  required: ['rows', 'dimensionalChecks', 'violations'],
  properties: {
    rows: {
      type: 'array',
      items: {
        type: 'object',
        required: ['variable', 'unitBase', 'cap', 'seam'],
        properties: {
          variable: { type: 'string' },
          unitBase: { type: 'string', enum: BASE_ENUM },
          cap: { type: 'string', description: 'upper bound or "none"' },
          seam: { type: 'string', description: 'require/check location, or "MISSING — add at <seam>"' },
        },
      },
    },
    dimensionalChecks: {
      type: 'array',
      items: {
        type: 'object',
        required: ['expression', 'consistent'],
        properties: {
          expression: { type: 'string' },
          consistent: { type: 'boolean' },
          note: { type: 'string' },
        },
      },
    },
    violations: {
      type: 'array',
      items: {
        type: 'object',
        required: ['variable', 'issue'],
        properties: {
          variable: { type: 'string' },
          issue: { type: 'string', enum: ['untagged-base', 'uncapped', 'wrong-base', 'no-seam'] },
          resolution: { type: 'string', maxLength: 240 },
        },
      },
    },
  },
}

const MATRIX_COLUMNS = {
  type: 'object',
  required: ['states', 'columns'],
  properties: {
    states: { type: 'array', items: { type: 'string' }, description: 'each new status/state the intent introduces' },
    columns: {
      type: 'array',
      items: {
        type: 'object',
        required: ['name', 'site', 'reads'],
        properties: {
          name: { type: 'string' },
          site: { type: 'string', description: 'file:line of the reader/transition' },
          reads: { type: 'string', maxLength: 200, description: 'what state it reads' },
          loadBearing: { type: 'string' },
        },
      },
    },
    missedByMemory: { type: 'array', items: { type: 'string' } },
  },
}

const SETTLEMENT_SEAMS = {
  type: 'object',
  required: ['seams', 'requiredChecks', 'copiedGuards'],
  properties: {
    seams: {
      type: 'array',
      items: {
        type: 'object',
        required: ['seam', 'distributionBase', 'filtersNewRecord', 'status'],
        properties: {
          seam: { type: 'string' },
          distributionBase: { type: 'string' },
          filtersNewRecord: { type: 'boolean' },
          capSeam: { type: 'string' },
          status: { type: 'string', enum: ['ok', 'GAP'] },
        },
      },
    },
    requiredChecks: {
      type: 'array',
      items: {
        type: 'object',
        required: ['invariant', 'seam', 'exists'],
        properties: {
          invariant: { type: 'string' },
          seam: { type: 'string' },
          exists: { type: 'boolean' },
        },
      },
    },
    copiedGuards: {
      type: 'array',
      items: {
        type: 'object',
        required: ['predicate', 'copies'],
        properties: {
          predicate: { type: 'string' },
          // A copy is either a bare `file:line` or the precondition-diff ROW for that copy,
          // already filled: this agent found the copy and read its caller, so it holds the
          // evidence — nothing downstream has to re-derive it from scratch.
          copies: {
            type: 'array',
            description: 'each copy: `file:line`, or {copy, oldPrecondition, newReality, resolution} with the precondition row filled',
            items: {
              oneOf: [
                { type: 'string' },
                {
                  type: 'object',
                  required: ['copy'],
                  properties: {
                    copy: { type: 'string', description: 'file:line of this copy' },
                    oldPrecondition: { type: 'string', maxLength: 240 },
                    newReality: { type: 'string', maxLength: 240 },
                    resolution: { type: 'string', maxLength: 240 },
                  },
                },
              ],
            },
          },
        },
      },
    },
  },
}

const MATRIX_CELLS = {
  type: 'object',
  required: ['cells'],
  properties: {
    cells: {
      type: 'array',
      items: {
        type: 'object',
        required: ['state', 'column', 'verdict'],
        properties: {
          state: { type: 'string' },
          column: { type: 'string' },
          verdict: { type: 'string', enum: ['handled', 'N/A', 'GAP'] },
          where: { type: 'string', maxLength: 240, description: 'required for handled' },
          justification: { type: 'string', maxLength: 240, description: 'required for N/A and GAP' },
        },
      },
    },
  },
}

const PREMISE_OBLIGATIONS = {
  type: 'object',
  required: ['tests', 'premises', 'drift', 'contract'],
  properties: {
    tests: {
      type: 'array',
      items: {
        type: 'object',
        required: ['scenario', 'level', 'catches'],
        properties: {
          scenario: { type: 'string', maxLength: 280 },
          level: { type: 'string', enum: ['e2e', 'integration', 'unit'] },
          catches: { type: 'string' },
        },
      },
    },
    premises: {
      type: 'array',
      items: {
        type: 'object',
        required: ['premise', 'seam', 'failingTest', 'status'],
        properties: {
          premise: { type: 'string', maxLength: 240 },
          seam: { type: 'string', description: 'require/check location' },
          failingTest: { type: 'string' },
          status: { type: 'string', enum: ['new', 'existing', 'none-yet'] },
        },
      },
    },
    drift: {
      type: 'array',
      items: {
        type: 'object',
        required: ['doc', 'what'],
        properties: { doc: { type: 'string' }, what: { type: 'string' } },
      },
    },
    // Contract block (plan-contract Artifact 1). This agent needs only the intent, so it runs
    // at t=0 and the Contract no longer waits on a downstream fill agent — the engine folds the
    // failing-first test obligations into the same block deterministically.
    contract: {
      type: 'array',
      items: { type: 'string', maxLength: 280 },
      description: 'atomic commitments: wiring points, predicates, invariants, exact values, files',
    },
  },
}

const PRECONDITION_ROW = {
  type: 'object',
  required: ['guard', 'copy', 'oldPrecondition', 'newReality', 'resolution'],
  properties: {
    guard: { type: 'string', description: 'the predicate, matching a copiedGuards predicate' },
    copy: { type: 'string', description: 'file:line of this copy' },
    oldPrecondition: { type: 'string', maxLength: 240 },
    newReality: { type: 'string', maxLength: 240 },
    resolution: { type: 'string', maxLength: 240 },
  },
}

// The full-draft shape the gates run over — one object, so every check is a pure function.
const DRAFT = {
  type: 'object',
  required: ['contract', 'matrix', 'dimension', 'precondition', 'premises'],
  properties: {
    contract: { type: 'array', items: { type: 'string', maxLength: 280 } },
    matrix: {
      type: 'object',
      required: ['columns', 'states', 'cells'],
      properties: {
        columns: { type: 'array', items: { type: 'string' } },
        states: { type: 'array', items: { type: 'string' } },
        cells: MATRIX_CELLS.properties.cells,
      },
    },
    dimension: {
      type: 'object',
      required: ['rows', 'violations'],
      properties: {
        rows: DIMENSION_TABLE.properties.rows,
        violations: DIMENSION_TABLE.properties.violations,
      },
    },
    precondition: { type: 'array', items: PRECONDITION_ROW },
    premises: PREMISE_OBLIGATIONS.properties.premises,
  },
}

// The unit of change every agent after the analyze phase emits: only what it ADDS or
// CHANGES, merged by key in applyPatch(). Re-emitting the whole — and growing — draft every
// round is what crashed a run on the 64k output-token ceiling: a late refuter found its
// strongest refutation (a creation-time amortization gap) and the integrating agent then
// returned nothing, silently dropping it. The merge never deletes, so a thin or malformed
// patch degrades to "gate still fails", never to silent corruption.
const DRAFT_PATCH = {
  type: 'object',
  properties: {
    columnsAdd: { type: 'array', items: { type: 'string' } },
    statesAdd: { type: 'array', items: { type: 'string' } },
    cellsUpsert: { type: 'array', items: MATRIX_CELLS.properties.cells.items },
    dimensionRowsUpsert: { type: 'array', items: DIMENSION_TABLE.properties.rows.items },
    // A refuter OPENS a dimension violation (it never resolves one); appended uniquely by
    // (variable, issue) so two refuters attacking the same variable stay one row.
    violationsAdd: { type: 'array', items: DIMENSION_TABLE.properties.violations.items },
    violationsResolved: {
      type: 'array',
      items: {
        type: 'object',
        required: ['variable', 'resolution'],
        properties: {
          variable: { type: 'string' },
          issue: { type: 'string' },
          resolution: { type: 'string', maxLength: 240 },
        },
      },
    },
    premisesUpsert: { type: 'array', items: PREMISE_OBLIGATIONS.properties.premises.items },
    preconditionUpsert: { type: 'array', items: PRECONDITION_ROW },
    contractAdd: { type: 'array', items: { type: 'string', maxLength: 280 } },
  },
}

// Declared AFTER DRAFT_PATCH: a refuter now carries its OWN reopening patch. Whoever holds
// the evidence writes the reopening, so the separate resolver hop after each round is gone.
const REFUTATION = {
  type: 'object',
  required: ['refutations', 'survived', 'missingColumns'],
  properties: {
    refutations: {
      type: 'array',
      items: {
        type: 'object',
        required: ['surface', 'target', 'scenario', 'forces', 'attacks'],
        properties: {
          surface: { type: 'string', enum: ['unhandled-scenario', 'dimension-violation', 'precondition-false', 'missing-column'] },
          target: { type: 'string', maxLength: 240, description: 'the cell / dimension row / precondition row attacked' },
          scenario: { type: 'string', maxLength: 280 },
          evidence: { type: 'string', maxLength: 200, description: 'file:line' },
          forces: { type: 'string', maxLength: 200, description: 'which cell/row must be reopened' },
          attacks: { type: 'string', enum: ['resolution', 'new-surface'], description: "'resolution' = breaks a fill/fix the draft already contains; 'new-surface' = names territory the draft lacks" },
        },
      },
    },
    survived: {
      type: 'array',
      items: {
        type: 'object',
        properties: { target: { type: 'string' }, checked: { type: 'string' } },
      },
    },
    missingColumns: {
      type: 'array',
      items: {
        type: 'object',
        properties: { site: { type: 'string' }, reads: { type: 'string' } },
      },
    },
    patch: DRAFT_PATCH,
  },
}

// =====================================================================
// Helpers
// =====================================================================

// This runtime delivers the Workflow `args` as a JSON-ENCODED STRING, not an
// object (probed: `Workflow({args:{intent}})` arrives as the literal
// string `'{"intent":...}'`). Reading `args.intent` off a string yields
// `undefined`, which silently zeroes the DESIGN INTENT and runs the whole heavy
// pass on an empty brief — an 870k-token run was wasted exactly this way before
// this shim. Normalize defensively so it works whether args is delivered as a
// string or an object. (The Workflow tool's own doc warns a stringified value
// reaches the script verbatim; this is the matching guard on the read side.)
const ARGS = (typeof args === 'string' && args.trim())
  ? (() => { try { return JSON.parse(args) } catch { return {} } })()
  : (args || {})

const intent = ARGS.intent || ''
const domains = ARGS.domains || []
// Phase-1 reading list, prepared ONCE by the orchestrator (SKILL.md Phase 1) instead of by each
// of the ~13 agents. Empty is legal: readingList() falls back to a self-serve, index-only form.
const brief = String(ARGS.brief || '').trim()
const noThrow = !!ARGS.noThrow
const seedDraft = ARGS.seedDraft
// The repo root the skill is running in. Every agent search MUST stay inside it
// (and inside .gitignore) — never /tmp, never sibling worktrees. See ctx().
const repoRoot = ARGS.repoRoot || '(the current repo root / cwd)'
// Tier: `economy` (default), `full` (every worker agent on the decision tier) or `reduced` —
// a single-seam change or a formula / base swap with no new status and no state machine: same
// DAG, 2 refuters on the quantity and completeness lenses. `ARGS.full === true` is the legacy
// spelling of `full`. An unknown value is logged and falls back to `economy`.
const TIER_NAMES = ['economy', 'full', 'reduced']
const TIER = (ARGS.full === true || ARGS.tier === 'full') ? 'full' : (TIER_NAMES.includes(ARGS.tier) ? ARGS.tier : 'economy')
if (ARGS.tier && !TIER_NAMES.includes(ARGS.tier)) log(`ignoring unknown tier ${JSON.stringify(ARGS.tier)} — running economy (valid: ${TIER_NAMES.join(' / ')})`)
// 4 lensed refuters, ONE round. History: a 4x5 run reached the full r1+r2 cluster
// union in one run — but at ~1.8x cost, STILL no convergence (flat 16/12/15/15/15),
// and a gate of 374->0. The post-mortem found the real bug was label drift, not
// breadth: every extra refuter wrote cells under its own (state,column) label variant
// ("S1" vs "S1 PERFORMANCE_BONUS_RETAINED"), so the exact-string gate saw 374
// phantom-empty grid pairs and the `refKey` dedup never fired (a variant label = a new
// key = "fresh" forever -> the loop CAN'T converge). More refuters amplified the drift
// (gate violations track refuter count: 6->1, 6->84, 20->374). The fix is `normCode`
// keying (below) — once labels collapse by code the gate is honest at ANY breadth. With
// that landed AND canonicalizeMatrixStates collapsing the state-axis drift, the amplifier
// is fixed twice over and a coherent matrix is confirmed — so breadth is now re-raised
// DELIBERATELY, as that post-mortem invited, while DEPTH drops to one round (below).
//
// Breadth = 4 (was 2). The FLAT 11/11/8 trajectory showed coverage-per-round — the number
// of distinct attack angles launched — was the binding constraint, NOT round depth (which
// doesn't converge). Refuters run in PARALLEL (well under the concurrency cap), so breadth
// is ~free on wall-clock: it trades tokens for coverage. Each refuter takes a DISTINCT lens
// (REFUTER_LENSES) so N refuters give N near-independent surfaces, not N collisions on the
// same targets. The ceiling is each refuter's OWN patch size (StructuredOutput bloat), not
// thinking — so this is a band, not "more is better".
// ARGS.refutersPerRound overrides per-run for a big change.
const REFUTERS_PER_ROUND = Math.max(1, ARGS.refutersPerRound || (TIER === 'reduced' ? 2 : 4))
const REFUTER_LENSES = [
  'QUANTITY / dimensional: attack the derived values — a value combined at the wrong base (e.g. face vs residual vs principal-only vs with-interest for a currency amount, or a window-bounded sum vs an instantaneous snapshot for a count), an uncapped settle/mint/derivation, a balance or Σ=0 invariant checked against a SELF-REFERENTIAL sum, a double-count across legs.',
  'ASYNC / ordering / lifecycle: attack timing — a post-commit write lost to a same-transaction join, a crash window between ack and commit, a non-idempotent re-fire / double-action, a two-clock lag, a predicate that must read live status-as-of-event but reads it once.',
  'PREMISE / invariant: attack the stated invariants — a path that violates an immutability / sum / identity premise or a CORE TENET, a load-bearing invariant with no require/check at its seam, a premise the new code introduces but never protects.',
  'COMPLETENESS / wrong-cell: attack the matrix itself — a cell marked handled/N·A that is actually a GAP, a column or state the matrix never enumerated, a copied guard whose precondition is now false for the new caller set.',
]
// The lenses actually dispatched. `reduced` keeps the two a single-seam change turns on:
// QUANTITY (the formula/base being swapped) and COMPLETENESS (what a seam-local plan never
// enumerated). Refuter i always gets ACTIVE_LENSES[i % length], so a raised
// ARGS.refutersPerRound cycles the reduced pair instead of reaching a dropped lens.
const ACTIVE_LENSES = TIER === 'reduced' ? [REFUTER_LENSES[0], REFUTER_LENSES[3]] : REFUTER_LENSES
// ONE refute round by default: every recorded trajectory is FLAT — rounds 2–3 mint as many
// fresh refutations as round 1 (fix-attack equilibrium, not discovery). Coverage comes from
// BREADTH (the lensed refuters above) and from an independent re-run (SKILL.md › One run does
// not exhaust), never from depth. `ARGS.refuteRounds` opts into depth for a single run; with
// one round there is no unattacked-final-patch problem, so the resolution re-refute pass is gone.
const REFUTE_ROUNDS = Math.max(1, ARGS.refuteRounds || 1)
// Gate-justify passes. A refuter that adds a column/state grows the cartesian, leaving
// new (state×column) pairs empty; ONE justify pass left them empty on a real run,
// the gate stayed FAIL, and the run threw away ~3M tokens. Each pass is fed the
// deterministic missing-cell list (missingCells) so it fills the whole grown row/column,
// and a residual after the loop is recorded (NOT thrown) — deep-plan never blocks.
const GATE_JUSTIFY_ROUNDS = 2
// Sizing of the SHARDED matrix work, by CELLS, capped in agent count. One agent per state (or
// per column) was the first cut, and it lost: the per-agent FIXED cost dominates — every agent
// writes its whole prompt to cache before its first turn — so 30 single-column gate fills cost
// more than the one expensive justify pass they replaced. One cheap agent handles ~80 cells
// reliably (the single full-cartesian agent left 22–60 % of the grid empty only when asked for
// 120–200); above that the work splits into at most MAX_FILL_AGENTS parallel agents.
const CELLS_PER_AGENT = Math.max(10, ARGS.cellsPerAgent || 80)
const MAX_FILL_AGENTS = Math.max(1, ARGS.maxFillAgents || 4)
// Split `items` into the number of contiguous groups that `cells` cells need at
// CELLS_PER_AGENT, capped at MAX_FILL_AGENTS — never more groups than items.
function splitByCells(items, cells) {
  const list = items || []
  if (!list.length) return []
  const n = Math.min(list.length, Math.max(1, Math.min(MAX_FILL_AGENTS, Math.ceil(cells / CELLS_PER_AGENT))))
  const size = Math.ceil(list.length / n)
  const out = []
  for (let i = 0; i < list.length; i += size) out.push(list.slice(i, i + size))
  return out
}

// ---------------------------------------------------------------------------
// Role x model x effort. A DECIDER (judges a load-bearing quantity or writes what ships:
// dimension table, state-mutation seams, refuters, gate justifier, consolidator) runs on the
// strong model at high effort; a WORKER (collects evidence under a checklist: matrix columns,
// matrix cells, premise/test obligations) on the cheap model at medium. Thinking is never
// disabled — effort is lowered instead. Agents of the same phase share model/effort/schema, so
// they share a cache prefix.
//
// The tier names below are the same five buckets every workflow in this repo uses, and a repo
// overrides them verbatim in `docs/agents/skills-config.md` › Models (the skill reads that
// section and passes it as ARGS.models). NEVER set CLAUDE_CODE_SUBAGENT_MODEL: it is first in
// the model-resolution order and collapses every tier into one model.
// ---------------------------------------------------------------------------
const TIER_DEFAULTS = {
  decision: { model: 'opus', effort: 'high' },
  worker: { model: 'sonnet', effort: 'medium' },
}
const TIERS = { ...TIER_DEFAULTS }
for (const [k, v] of Object.entries(ARGS.models || {})) {
  if (!TIER_DEFAULTS[k] || !v) continue
  TIERS[k] = { model: v.model || TIER_DEFAULTS[k].model, effort: v.effort || TIER_DEFAULTS[k].effort }
}
// `economy` is the default (the reasoning-heavy agents are already on the decision tier);
// `full` — by arg, or by the sensitive-domain / plan-contract trigger the skill passes —
// upgrades the worker agents too.
const t = (name, effort) => ({ ...TIERS[name], ...(effort ? { effort } : {}) })
const ROLE = {
  a1: t('decision'), // analyze:dimension-table
  a2: t('worker'), // enumerate:matrix-columns
  a3: t('decision'), // enumerate:state-mutation-seams (+ the precondition rows per copy)
  a4: t('worker'), // analyze:cells-* · analyze:cells-fill · gate:fill-*
  a5: t('worker'), // analyze:premises-tests (+ the Contract block)
  a6: t('decision'), // refute:* — each returns its own reopening patch
  justify: t('decision'), // gate:justify-* — only the violations a cell fill cannot close
  // One agent, ~a minute, and it owns the contradiction watch — the highest-value output of
  // the run. The effort downgrade it used to carry bought nothing measurable.
  consolidate: t('decision'), // synthesize:consolidate
}
const FULL_UPGRADES = new Set(['a2', 'a4', 'a5'])
// Per-ROLE model/effort overrides for a benchmark arm (`ARGS.roles = { a6: { model: 'sonnet' },
// a2: { effort: 'low' } }`) — an A/B without editing the script. `ARGS.models` stays the TIER
// map a repo declares in its config › Models; this is the finer knob, keyed by the ROLE names
// above and applied AFTER the full-tier upgrade, so an explicit override always wins. An
// unknown role or a non-object value is logged and ignored: a typo in a benchmark arm must
// never throw away a long run.
const ROLE_OVERRIDES = (() => {
  const out = {}
  for (const [k, v] of Object.entries(ARGS.roles || {})) {
    if (!ROLE[k] || !v || typeof v !== 'object') { log(`ignoring role override ${JSON.stringify(k)}: unknown role or non-object value (valid roles: ${Object.keys(ROLE).join(' / ')})`); continue }
    const o = {}
    if (v.model) o.model = String(v.model)
    if (v.effort) o.effort = String(v.effort)
    if (Object.keys(o).length) out[k] = o
    else log(`ignoring role override ${JSON.stringify(k)}: neither model nor effort given`)
  }
  return out
})()
const role = (k) => ({
  ...(TIER === 'full' && FULL_UPGRADES.has(k) ? t('decision') : ROLE[k]),
  ...(ROLE_OVERRIDES[k] || {}),
})
// The routing actually dispatched — what the run logs and returns, so a benchmark arm is
// auditable from the result alone instead of from the (pre-override) ROLE defaults.
const effectiveRoles = () => Object.fromEntries(Object.keys(ROLE).map((k) => [k, role(k)]))

// Phase-1 reading list — shared by ctx() and the refuter prompt, the two places an agent is
// told what to read. Re-reading a domain's whole premises file (a mature domain's runs to
// hundreds of KB, and `Read` truncates at 2 000 lines anyway) was the largest measured context
// cost of a run, paid once PER AGENT; the brief replaces it with ids the orchestrator resolved
// once. Both branches carry the same hard NEVER, because the fallback is what an ad-hoc
// invocation runs on.
function readingList() {
  if (brief) {
    return [
      `=== PHASE-1 BRIEF (prepared once by the orchestrator — this is your reading list) ===`,
      brief,
      `=== END BRIEF ===`,
      `Open a premise ONLY by id (the fetch command in \`docs/agents/skills-config.md\` › Docs layout › premise-by-id, default \`python3 .github/scripts/premise.py <id>\`); open a recurring-failure-mode entry only when the brief lists it or your own evidence names it; read the plan-contract spec only for the artifact you emit; and NEVER read a whole premises file (they run to hundreds of KB and \`Read\` truncates at 2 000 lines — the single largest context cost measured).`,
    ]
  }
  return [
    `Phase-1 context (no brief was passed — assemble it yourself, inside this repo only). Read \`docs/agents/skills-config.md\` › Docs layout for the exact paths and the premises {domain}/{module} pattern, then read HEADERS AND INDEXES ONLY: the entry headers + trigger lines of the recurring-failure-modes doc (config › Docs layout › Planning — MANDATORY when present; open only the entries whose trigger matches), the section headers of the core-tenets doc (default \`docs/CORE_TENETS.md\`), the plan-contract spec (default \`docs/planning/plan-contract.md\`), and per affected domain its schema doc (default \`docs/schema/{domain}.md\`) + its premises INDEX, opening a premise body by id. If a config section is absent, use these defaults and note it. NEVER read a whole premises file (hundreds of KB; \`Read\` truncates at 2 000 lines).`,
  ]
}

// Shared Phase-1 preamble. Agents have file tools and must read the docs
// themselves — the intent is the analogue of deep-review's diff. Doc paths are
// NOT hardcoded: agents read the repo's `docs/agents/skills-config.md` (the file
// `setup` wrote) for where core-tenets / premises / schema / planning docs live,
// and fall back to the canonical defaults when a section is absent.
// The first line is the role marker `ci/wf_timeline.py` keys on to attribute wall-clock and
// tokens per stage; it must stay line 1 of every prompt this script builds.
function ctx(roleFile, label) {
  return [
    `[deep-plan role: ${label}]`,
    `Read \`.claude/skills/deep-plan/agents/${roleFile}\` and operate as that specialist.`,
    `There is NO diff — the codebase on disk is the CURRENT (pre-change) state. Use file tools to discover the real callers, guards, readers, and seams the intent will collide with.`,
    ``,
    `SEARCH HYGIENE (mandatory — violations have hung runs for >1 hour):`,
    `- Use \`rg\` (ripgrep), NEVER \`grep -r\`/\`grep -rln\`. \`rg\` honors .gitignore, so it skips build output, the VCS dir, and worktrees — a bare \`grep -r\` traverses build artifacts and every SIBLING worktree and can hang for tens of minutes.`,
    `- Stay INSIDE this repo root: \`${repoRoot}\`. Never read or search \`/tmp\`, \`..\`, \`~\`, or any absolute path outside it. Pass scoped paths to \`rg\` (e.g. \`rg PATTERN <source roots>\`).`,
    `- One simple command per Bash call. No fragile compound one-liners (\`rg … | head; echo; find …\`) — an unbalanced quote makes the shell hang waiting on stdin.`,
    `- If you query a DB/MCP, bound it (LIMIT, narrow filters). You are analyzing CODE; don't run heavy unbounded prod queries.`,
    ``,
    `Plan your searches — aim for <= 40 tool calls: locate with \`rg -n\`, read code in RANGES (Read with offset/limit, or \`sed -n 'a,bp'\`), never a whole file over 200 lines; hand a broad caller sweep to an \`Explore\` subagent when the Agent tool is available. Cost is turns x context.`,
    ``,
    `CONTAMINATION GUARD: the ONLY source of truth for what is being proposed is the DESIGN INTENT below. IGNORE any plan-contract, intent file, \`deep-plan-*.md\`, or cached JSON you encounter on disk — they belong to other runs/projects and will anchor you to the wrong feature. Do not let an on-disk artifact override the intent below.`,
    ``,
    ...readingList(),
    ``,
    `Affected domains: ${domains.join(', ') || '(infer from the intent)'}.`,
    ``,
    `OUTPUT MUST BE TERSE — large structured outputs crash serialization. Every field: file:line + one clause, <= 240 chars, no paragraphs, no restating the intent.`,
    ``,
    `=== DESIGN INTENT (what is being proposed — the single source of truth) ===`,
    intent,
    `=== END DESIGN INTENT ===`,
    ``,
    `Return the structured output your role file describes. Do NOT post anywhere — return data to the orchestrator.`,
  ].join('\n')
}

// Compact, reasoning-free serialization of the draft — this is ALL the refuter
// sees (plus the intent + its own codebase access). It must not include agent
// notes or the analysis that produced the draft.
function draftSummary(d) {
  const cells = (d.matrix.cells || []).map(c => `  - [${c.state}] x [${c.column}] => ${c.verdict}${c.where ? ' @ ' + c.where : ''}${c.justification ? ' (' + c.justification + ')' : ''}`).join('\n')
  const dim = (d.dimension.rows || []).map(r => `  - ${r.variable}: base=${r.unitBase}, cap=${r.cap}, seam=${r.seam}`).join('\n')
  const pre = (d.precondition || []).map(p => `  - guard \`${p.guard}\` @ ${p.copy}: old=${p.oldPrecondition} | new=${p.newReality} | resolution=${p.resolution}`).join('\n')
  return [
    `INTERACTION MATRIX (states: ${(d.matrix.states || []).join(', ')}; columns: ${(d.matrix.columns || []).join(', ')}):`,
    cells || '  (no cells)',
    ``,
    `DIMENSION TABLE:`,
    dim || '  (no rows)',
    ``,
    `PRECONDITION DIFF:`,
    pre || '  (no rows)',
  ].join('\n')
}

function refKey(r) {
  return `${r.surface}::${(r.target || '').trim()}::${(r.forces || '').trim()}`
}

// Normalize a matrix axis label to its leading code (S1, C12, …) so the SAME logical
// cell written under two conventions keys identically — "S1" and
// "S1 PERFORMANCE_BONUS_RETAINED", "C1" and "C1 RETENTION mint hook". A 4-refuter ×
// 5-round run wrote cells under 68 column-labels that collapse to the 34 real codes and 22
// state-labels → 15 codes; because the gate keyed by exact string, all 374 grid pairs
// read EMPTY (label drift, not missing analysis), a 59k bulk-justify re-created 374
// duplicate cells, and the real analysis sat in 105 orphaned cells. Falls back to the
// trimmed/lowercased full label when there is no S#/C# code, so an uncoded label still
// keys consistently with itself.
function normCode(s) {
  const m = String(s || '').match(/^\s*([SC]\d+)\b/i)
  return m ? m[1].toUpperCase() : String(s || '').trim().toLowerCase()
}

// Loose canonical form of a matrix STATE label, for variant detection BEYOND normCode.
// normCode collapses only EXACT-string variants once a leading code is present; an
// uncoded state falls back to its full lowercased label, so two conventions for the SAME
// logical state stay distinct. This strips the parenthetical gloss and ALL whitespace and
// lowercases, turning "Shipment.status = HELD (active, routes…)" and the terse
// "status=HELD" into "shipment.status=held" and "status=held" — where the
// terse form is a dotted-suffix of the verbose one.
// The gloss strip must handle NESTED parens: a single-pass `\([^)]*\)` stops at the first
// `)`, so "SRE RETENTION_HOLD credit (…heldToKeep=max(0,capΣ−sreBalance))" left a trailing
// `)` residue that defeated both the equality and the endsWith check — a real run got
// 2 phantom dup states × 57 columns = 114 empty grid cells, the entire justify-pass
// workload. Strip innermost-first to a fixpoint, then drop any unbalanced leftover parens.
function looseStateForm(s) {
  let out = String(s || '').toLowerCase()
  let prev
  do { prev = out; out = out.replace(/\([^()]*\)/g, '') } while (out !== prev)
  return out.replace(/[()]/g, '').replace(/\s+/g, '')
}

// True when two state labels denote the SAME logical state: equal loose forms, or one is
// the other with a leading qualifier dropped ("Entity.field=value" vs "field=value").
// The suffix match is boundary-anchored on '.' so the bare "status=x" matches the
// qualified "shipment.status=x", while two DISTINCT qualified states
// ("order.status=x" vs "transfer.status=x") never match each other (neither is a
// suffix of the other). Applied to the state axis ONLY — columns are seam-anchored
// (file:line) and do not exhibit this verbose/terse drift (verified: zero loose collapses
// among the 32 columns of one real run).
function sameLogicalState(a, b) {
  const x = looseStateForm(a), y = looseStateForm(b)
  if (!x || !y) return false
  if (x === y) return true
  const [short, long] = x.length <= y.length ? [x, y] : [y, x]
  if (short.length < 4 || !long.endsWith(short)) return false
  return long[long.length - short.length - 1] === '.'
}

// Load-bearing, verbatim-ish anchors in a string: file:line, ALL_CAPS enum, multi-hump
// CamelCase identifier. Shared by the consolidation-fidelity and contract-block checks.
// File-extension set is broad (covers any stack's source/config files), so this stays
// stack-agnostic; an uncoded prose item with no anchor is skipped (paraphrase is legit).
function anchorTokens(s) {
  return (s || '').match(/[\w/.]+\.(?:kt|kts|sql|ya?ml|java|ts|tsx|js|jsx|cs|py|go|rs|rb|php|swift|c|cc|cpp|h|hpp)(?::\d+)?|:\d+\b|\b[A-Z]{2,}(?:_[A-Z0-9]+)+\b|\b[A-Z][a-z]+(?:[A-Z][a-z]+){2,}\b/g) || []
}

// Matrix discipline: a column becomes a matrix axis only if it cites a CONCRETE
// seam — a file:line or a source-file token. A run enumerated 24 columns into a
// 284-cell matrix whose 504k-token fill STILL left 73 empty cells the loop never
// reached, which the gate then bulk-justified in one thin pass. A column with no
// concrete seam is speculation that multiplies cells without adding coverage; drop
// it. If it is real, a refuter re-adds it via `missingColumns` WITH a site — so this
// prunes noise without losing a genuine interaction. Never silent: dropped names log.
function columnsWithSeam(columns) {
  const hasSeam = (s) => /:\d+|\.(kt|kts|sql|ya?ml|java|ts|tsx|js|jsx|cs|py|go|rs|rb|php|swift|c|cc|cpp|h|hpp)\b/i.test(s || '')
  const kept = [], dropped = []
  for (const c of (columns || [])) (hasSeam(c.site) ? kept : dropped).push(c)
  return { kept, dropped }
}

// A `copiedGuards` copy is either a bare `file:line` string or the pre-filled precondition
// row agent 3 now returns. Every read of a copy goes through this.
function copyOf(c) { return (c && typeof c === 'object') ? String(c.copy || '') : String(c || '') }

// Every matrix axis carries a leading CODE (S1…, C1…) from the moment it is minted, and every
// cell is resolved onto an axis by that code before anything keys on it. Without codes,
// `normCode` falls back to the full label, so a shard that PARAPHRASES its assigned state
// ("status=HELD" for the verbose label it was handed) mints a phantom state — on a real run
// five of seven row shards paraphrased, the targeted fill re-did 93 cells, and the gate then
// faced phantom-states x columns of empty pairs. Codes make the label free text again.
const AXIS_CODE_RE = /^\s*([SC])(\d+)\b/i
function codeAxes(labels, prefix) {
  return (labels || []).map((l, i) => (AXIS_CODE_RE.test(l) ? l : `${prefix}${i + 1} ${l}`))
}
function nextCode(axes, prefix) {
  let max = 0
  for (const a of axes) { const m = String(a).match(AXIS_CODE_RE); if (m && m[1].toUpperCase() === prefix) max = Math.max(max, Number(m[2])) }
  return `${prefix}${max + 1}`
}
const stripCode = (l) => String(l || '').replace(AXIS_CODE_RE, '').trim()
const looseLabel = (l) => stripCode(l).toLowerCase().replace(/\s+/g, '')
// The `Class.method` (or `Class.field`) identifier a column label opens with, when it opens
// with one — null for free text, and null for a bare class name (two methods of one class are
// two columns).
function leadingSymbol(label) {
  const m = stripCode(label).match(/^([A-Z][A-Za-z0-9]*(?:\.[a-zA-Z_][A-Za-z0-9_]*)+)\b/)
  return m ? m[1] : null
}
// Find the axis a free-text label denotes: by code / exact label, then by shared file:line
// seam, then by a shared leading `Class.method` symbol, then by containment of the
// code-stripped text — each of the last three only on a UNIQUE match, so a terse
// "status=PAID" that sits inside two qualified states stays its own row and never bridges
// them. null = no axis; the caller mints one.
function resolveAxis(label, axes) {
  const list = axes || []
  const code = normCode(label)
  const byCode = list.find((a) => normCode(a) === code)
  if (byCode !== undefined) return byCode
  const seam = extractSeam(label)
  if (seam) { const bySeam = list.filter((a) => extractSeam(a) === seam); if (bySeam.length === 1) return bySeam[0] }
  // Same reader named without a line: independent refuters write
  // `OrderService.updateStatus (agreement path)` and `… (non-agreement path)` — one site, two
  // columns. A shared leading `Class.method` symbol is the same column; the path distinction
  // lives in the cell justification, and GAP wins on the merge.
  const sym = leadingSymbol(label)
  if (sym) { const bySym = list.filter((a) => leadingSymbol(a) === sym); if (bySym.length === 1) return bySym[0] }
  const me = looseLabel(label)
  if (me.length < 12) return null
  const contained = list.filter((a) => { const o = looseLabel(a); return o.length >= 12 && (o.includes(me) || me.includes(o)) })
  return contained.length === 1 ? contained[0] : null
}
// Rewrite a cell's state/column onto the axes, minting a CODED axis when nothing matches.
function landCell(cell, states, columns) {
  let st = resolveAxis(cell.state, states)
  if (st === null) { st = AXIS_CODE_RE.test(cell.state) ? cell.state : `${nextCode(states, 'S')} ${cell.state}`; states.push(st) }
  let col = resolveAxis(cell.column, columns)
  if (col === null) { col = AXIS_CODE_RE.test(cell.column) ? cell.column : `${nextCode(columns, 'C')} ${cell.column}`; columns.push(col) }
  return { ...cell, state: st, column: col }
}

// Assemble the raw draft from the analyze-phase structured outputs. Precondition rows are
// seeded one-per-copy from the guard enumeration — already filled when agent 3 returned the
// diff with the copy, so the gate justify only sees what it left blank.
function assemble(cols, seams, dim, cells) {
  const precondition = []
  for (const g of (seams.copiedGuards || [])) {
    for (const c of (g.copies || [])) {
      const row = (c && typeof c === 'object') ? c : {}
      precondition.push({
        guard: g.predicate,
        copy: copyOf(c),
        oldPrecondition: row.oldPrecondition || '',
        newReality: row.newReality || '',
        resolution: row.resolution || '',
      })
    }
  }
  const states = codeAxes(cols.states || [], 'S')
  const columns = codeAxes((cols.columns || []).map(c => c.name), 'C')
  const landed = (cells.cells || []).map((c) => landCell(c, states, columns))
  return {
    contract: [],
    matrix: { columns, states, cells: landed },
    dimension: { rows: dim.rows || [], violations: dim.violations || [] },
    precondition,
    premises: [],
  }
}

// Merge a DRAFT_PATCH into the draft. Additive/override-by-key
// only: upserts cells (by state+column), dimension rows (by variable), premises
// (by text), precondition rows (by guard+copy); appends columns/states/contract
// items uniquely; stamps resolutions onto matching dimension violations. It never
// deletes, so a malformed patch can only leave the gate unsatisfied — which the
// gate then catches — rather than silently corrupting a filled draft.
// `opts.conservative` is for the refute round and the two matrix fills, where N independent
// agents patch the SAME draft in sequence: a cell collision resolves through mergeCell (GAP
// wins, richer text on a tie) instead of last-writer-wins, so the last patch applied cannot
// bury an earlier reopening.
function applyPatch(draft, patch, opts = {}) {
  if (!patch) return draft
  const d = JSON.parse(JSON.stringify(draft))
  const uniqPush = (arr, items) => { for (const x of (items || [])) if (!arr.includes(x)) arr.push(x) }
  // A new axis gets a CODE unless the label already carries one; an add that resolves onto an
  // existing axis (same code / seam / symbol / text) is a no-op instead of a duplicate.
  for (const a of (patch.columnsAdd || [])) if (resolveAxis(a, d.matrix.columns) === null) d.matrix.columns.push(AXIS_CODE_RE.test(a) ? a : `${nextCode(d.matrix.columns, 'C')} ${a}`)
  for (const a of (patch.statesAdd || [])) if (resolveAxis(a, d.matrix.states) === null) d.matrix.states.push(AXIS_CODE_RE.test(a) ? a : `${nextCode(d.matrix.states, 'S')} ${a}`)
  uniqPush(d.contract, patch.contractAdd)

  // Key cells by normalized (state,column) CODE so a variant-labeled upsert updates the
  // existing cell instead of creating a duplicate (the label-drift fix).
  const ckey = (s, c) => JSON.stringify([normCode(s), normCode(c)])
  const cellIdx = {}
  d.matrix.cells.forEach((c, i) => { cellIdx[ckey(c.state, c.column)] = i })
  for (const raw of (patch.cellsUpsert || [])) {
    const c = landCell(raw, d.matrix.states, d.matrix.columns)
    const k = ckey(c.state, c.column)
    if (k in cellIdx) d.matrix.cells[cellIdx[k]] = opts.conservative ? mergeCell(d.matrix.cells[cellIdx[k]], c) : c
    else { cellIdx[k] = d.matrix.cells.length; d.matrix.cells.push(c) }
  }
  // Referential integrity: refuters add cells faster than they add columnsAdd/statesAdd,
  // so a cell can reference an axis code absent from columns[]/states[] (a run: cells used
  // 15 state-codes / 34 column-codes but the axes declared only 11 / 34, leaving 4 real
  // states uncovered). Landing every cell reconciles the axes so they always span the
  // analyzed surface and the gate validates it, not a stale grid.
  d.matrix.cells = d.matrix.cells.map((c) => landCell(c, d.matrix.states, d.matrix.columns))

  const rowIdx = {}
  d.dimension.rows.forEach((r, i) => { rowIdx[r.variable] = i })
  for (const r of (patch.dimensionRowsUpsert || [])) {
    if (r.variable in rowIdx) d.dimension.rows[rowIdx[r.variable]] = r
    else { rowIdx[r.variable] = d.dimension.rows.length; d.dimension.rows.push(r) }
  }
  const vkey = (x) => JSON.stringify([x.variable, x.issue])
  const haveViol = new Set(d.dimension.violations.map(vkey))
  for (const nv of (patch.violationsAdd || [])) {
    const k = vkey(nv)
    if (!haveViol.has(k)) { haveViol.add(k); d.dimension.violations.push(nv) }
  }
  for (const vr of (patch.violationsResolved || [])) {
    const hit = d.dimension.violations.find(x => x.variable === vr.variable && (!vr.issue || x.issue === vr.issue))
    if (hit) hit.resolution = vr.resolution
  }

  const premIdx = {}
  d.premises.forEach((p, i) => { premIdx[p.premise] = i })
  for (const p of (patch.premisesUpsert || [])) {
    if (p.premise in premIdx) d.premises[premIdx[p.premise]] = p
    else { premIdx[p.premise] = d.premises.length; d.premises.push(p) }
  }

  const pkey = (g, c) => JSON.stringify([g, c])
  const preIdx = {}
  d.precondition.forEach((p, i) => { preIdx[pkey(p.guard, p.copy)] = i })
  for (const p of (patch.preconditionUpsert || [])) {
    const k = pkey(p.guard, p.copy)
    if (k in preIdx) d.precondition[preIdx[k]] = p
    else { preIdx[k] = d.precondition.length; d.precondition.push(p) }
  }
  canonicalizeMatrixStates(d)
  canonicalizeColumnsBySeam(d)
  return d
}

// Collapse two column labels that cite the SAME seam (Class:line / file.ext:line) into one
// column, remapping cells and merging collisions (GAP wins). Four independent refuters name
// the same reader under four spellings; `normCode` cannot see it (no shared code), and each
// spelling became its own column on a real run, inflating the cartesian the gate then had to
// fill. Columns without a seam are left alone. Canonical label = the longest (most context).
// Idempotent.
function canonicalizeColumnsBySeam(d) {
  const columns = (d.matrix && d.matrix.columns) || []
  if (columns.length < 2) return
  const bySeam = new Map()
  for (const c of columns) {
    const seam = extractSeam(c)
    if (!seam) continue
    const g = bySeam.get(seam)
    if (!g) bySeam.set(seam, { canon: c, members: [c] })
    else { g.members.push(c); if (String(c).length > String(g.canon).length) g.canon = c }
  }
  const canonical = {}
  for (const g of bySeam.values()) if (g.members.length > 1) for (const m of g.members) canonical[m] = g.canon
  if (!Object.keys(canonical).length) return
  const seen = new Set(); const newColumns = []
  for (const c of columns) { const k = canonical[c] || c; if (!seen.has(k)) { seen.add(k); newColumns.push(k) } }
  d.matrix.columns = newColumns
  const byKey = new Map()
  for (const cell of (d.matrix.cells || [])) {
    const cc = { ...cell, column: canonical[cell.column] || cell.column }
    const k = JSON.stringify([normCode(cc.state), normCode(cc.column)])
    byKey.set(k, byKey.has(k) ? mergeCell(byKey.get(k), cc) : cc)
  }
  d.matrix.cells = [...byKey.values()]
}

// Pick the surviving cell when two variant-state cells collapse onto the same
// (canonical state, column). GAP outranks handled/N·A so a merge can never HIDE an
// unresolved interaction; among equal verdicts the richer (longer) text wins.
function mergeCell(a, b) {
  const rank = { GAP: 3, 'N/A': 1, handled: 1 }
  const ra = rank[a.verdict] || 0, rb = rank[b.verdict] || 0
  if (rb !== ra) return rb > ra ? b : a
  const len = (c) => ((c.justification || '') + (c.where || '')).length
  return len(b) > len(a) ? b : a
}

// Collapse verbose/terse variants of the same logical state to ONE canonical label, remap
// every cell's state, and merge cells that then collide on (state, column). WHY: a
// downstream fill/refute/justify agent paraphrases an enumerated state into a terser
// convention ("status=HELD" vs the verbose "Shipment.status = HELD (…)");
// normCode's full-label fallback can't collapse them, so the cell-reconcile loop minted a
// phantom duplicate state, the cartesian DOUBLED (a run: 18 states / 576 cells where
// 9 / 288 were real), and the gate-justify pass then dutifully FILLED all ~288 phantom
// cells — wasted fill work AND a 2x-inflated GAP count that read twice as alarming as the
// truth (every verbose/terse pair carried an identical GAP count). Canonical label = the
// longest in each group (most context). Idempotent; a no-op when <2 states.
function canonicalizeMatrixStates(d) {
  const states = (d.matrix && d.matrix.states) || []
  if (states.length < 2) return
  // Group over a SPECIFICITY-SORTED copy (entity-qualified — loose form contains '.' —
  // first, longer first) so every qualified group exists before any terse variant
  // attaches; a terse state then sees the full picture and ambiguity is detectable.
  // Output axis order still follows the ORIGINAL states array (newStates below).
  const bySpecificity = [...states].sort((a, b) => {
    const la = looseStateForm(a), lb = looseStateForm(b)
    return (lb.includes('.') ? 1 : 0) - (la.includes('.') ? 1 : 0) || lb.length - la.length
  })
  const groups = []
  for (const s of bySpecificity) {
    // Membership = mutual compatibility with EVERY member of EXACTLY ONE group.
    // sameLogicalState is deliberately non-transitive — a terse "status=PAID" matches
    // BOTH "Order.status=PAID" and "Transfer.status=PAID", which never match each
    // other — so the old `some`-membership computed the transitive closure: the terse
    // state BRIDGED two distinct qualified states into one group, silently erasing a
    // matrix row. `every` blocks the bridge; the ≥2-full-matches guard keeps a genuinely
    // ambiguous terse state as its own row (honest phantom cells) instead of binding its
    // cells to an arbitrary entity.
    const full = groups.filter((grp) => grp.members.every((m) => sameLogicalState(m, s)))
    let g = full.length === 1 ? full[0] : null
    if (!g) { g = { canon: s, members: [] }; groups.push(g) }
    g.members.push(s)
    if (String(s).length > String(g.canon).length) g.canon = s
  }
  if (groups.length === states.length) return // every state distinct — nothing to collapse
  const canonical = {}
  for (const g of groups) for (const m of g.members) canonical[m] = g.canon
  const seen = new Set(); const newStates = []
  for (const s of states) { const c = canonical[s] || s; if (!seen.has(c)) { seen.add(c); newStates.push(c) } }
  d.matrix.states = newStates
  const byKey = new Map()
  for (const c of (d.matrix.cells || [])) {
    const cc = { ...c, state: canonical[c.state] || c.state }
    const k = JSON.stringify([normCode(cc.state), normCode(cc.column)])
    byKey.set(k, byKey.has(k) ? mergeCell(byKey.get(k), cc) : cc)
  }
  d.matrix.cells = [...byKey.values()]
}

// =====================================================================
// Programmatic GATE — pure function over the draft + the guard-copy enumeration.
// Returns { pass, violations }. This is the deterministic completeness check the
// plan's Contract item 6 specifies.
// =====================================================================
function gateCheck(d, copiedGuards) {
  const v = []
  const states = d.matrix.states || []
  const columns = d.matrix.columns || []
  const cells = d.matrix.cells || []

  // 1. No empty cell: every (state x column) pair must have a cell, and each
  //    cell must be justified.
  // Key by normalized (state,column) CODE: state/column names contain spaces and are
  // written under drifting label variants ("S1" vs "S1 PERFORMANCE_BONUS_RETAINED"), so
  // keying by exact string made a run see 374 phantom-empty grid pairs while the matching
  // cells existed under a different label. normCode collapses the variants; JSON.stringify
  // keeps the composite key collision-free. Iterate the grid by DISTINCT code so a
  // drifted axis list doesn't double-count a pair.
  const ckey = (s, c) => JSON.stringify([normCode(s), normCode(c)])
  const distinct = (arr) => { const seen = new Set(), out = []; for (const x of arr) { const k = normCode(x); if (!seen.has(k)) { seen.add(k); out.push(x) } } return out }
  const cellAt = {}
  for (const c of cells) cellAt[ckey(c.state, c.column)] = c
  for (const s of distinct(states)) {
    for (const col of distinct(columns)) {
      const c = cellAt[ckey(s, col)]
      if (!c) {
        v.push({ kind: 'empty-cell', detail: `matrix cell [${s}] x [${col}] is missing` })
        continue
      }
      if (c.verdict === 'GAP' && !(c.justification && c.justification.trim())) {
        v.push({ kind: 'unjustified-gap', detail: `cell [${s}] x [${col}] is GAP without justification` })
      }
      if (c.verdict === 'handled' && !(c.where && c.where.trim())) {
        v.push({ kind: 'handled-no-where', detail: `cell [${s}] x [${col}] is handled but cites no location` })
      }
      if (c.verdict === 'N/A' && !((c.justification && c.justification.trim()) || (c.where && c.where.trim()))) {
        v.push({ kind: 'na-no-reason', detail: `cell [${s}] x [${col}] is N/A without a why` })
      }
    }
  }

  // 2. Every load-bearing premise must be executable: a require/check seam AND a
  //    failing-first test. A documentation-only or none-yet premise fails.
  for (const p of (d.premises || [])) {
    if (p.status === 'none-yet' || !(p.seam && p.seam.trim()) || !(p.failingTest && p.failingTest.trim())) {
      v.push({ kind: 'premise-not-executable', detail: `premise "${p.premise}" lacks ${!(p.seam && p.seam.trim()) ? 'a require/check seam' : 'a failing-first test'}` })
    }
  }

  // 2b. A load-bearing invariant exists (a dimension row with a real cap) but NO
  //     executable premise protects it -> the documentation-only-invariant
  //     failure the gate exists to catch. Vacuous over an empty premise list
  //     otherwise.
  const cappedRows = (d.dimension.rows || []).filter(
    (r) => r.cap && r.cap.trim() && r.cap.trim().toLowerCase() !== 'none',
  )
  if (cappedRows.length > 0 && (d.premises || []).length === 0) {
    v.push({ kind: 'no-executable-premise', detail: `${cappedRows.length} capped invariant(s) but zero executable premises (require/check + failing-first test)` })
  }

  // 3. Every unresolved dimension violation fails (a wrong/untagged base
  //    or uncapped variable that was never resolved).
  for (const dv of (d.dimension.violations || [])) {
    if (!(dv.resolution && dv.resolution.trim())) {
      v.push({ kind: 'dimension-violation', detail: `${dv.variable}: ${dv.issue} unresolved` })
    }
  }

  // 4. Every touched guard must have one precondition row PER COPY, each filled.
  for (const g of (copiedGuards || [])) {
    const rows = (d.precondition || []).filter(p => p.guard === g.predicate)
    if (rows.length < (g.copies || []).length) {
      v.push({ kind: 'missing-precondition-copy', detail: `guard \`${g.predicate}\` has ${(g.copies || []).length} copies but only ${rows.length} precondition rows` })
    }
    for (const r of rows) {
      if (!(r.resolution && r.resolution.trim()) || !(r.newReality && r.newReality.trim()) || !(r.oldPrecondition && r.oldPrecondition.trim())) {
        v.push({ kind: 'unfilled-precondition', detail: `precondition row for \`${g.predicate}\` @ ${r.copy} is unfilled` })
      }
    }
  }

  return { pass: v.length === 0, violations: v }
}

// Deterministic cartesian-completeness: the (state × column) pairs (keyed by normCode)
// that have NO cell. Pure function — the same product gateCheck walks, exposed so the
// targeted fill and the gate's per-column fills answer ONLY the gaps rather than re-emit
// all N cells. A refuter that adds a column/state grows the product; this
// surfaces the new empty pairs so the justify pass fills the whole grown row/column, not
// just the one cell the refuter named (the matrix-growth gate-fail).
function missingCells(draft) {
  const states = (draft.matrix && draft.matrix.states) || []
  const columns = (draft.matrix && draft.matrix.columns) || []
  const cells = (draft.matrix && draft.matrix.cells) || []
  const distinct = (arr) => { const seen = new Set(), out = []; for (const x of arr) { const k = normCode(x); if (!seen.has(k)) { seen.add(k); out.push(x) } } return out }
  const have = new Set(cells.map((c) => JSON.stringify([normCode(c.state), normCode(c.column)])))
  const missing = []
  for (const s of distinct(states)) for (const col of distinct(columns)) {
    if (!have.has(JSON.stringify([normCode(s), normCode(col)]))) missing.push({ state: s, column: col })
  }
  return missing
}

// Consolidation fidelity: the gate proves the DRAFT (JSON) is complete; this checks the
// rendered body did not DROP any of it. When an LLM retyped the artifacts this caught real
// lossiness — a run dropped a premise, buried a scoped commitment, left dangling refs,
// because a 284-cell draft can't fit faithfully in a ~32k-char body. The artifacts are
// now rendered deterministically (renderArtifacts), so this is a REGRESSION GUARD on the
// engine's own render — a hit means renderArtifacts itself dropped something. It catches the
// DROP class: every premise / contract item / GAP cell whose distinctive tokens (file:line,
// ALL_CAPS enum, multi-hump CamelCase identifier) appear NOWHERE in the body. Items with no
// distinctive token are skipped (prose rephrasing is legitimate); the check only fires on
// load-bearing, verbatim-ish anchors — so a hit is a real omission, not a paraphrase.
function consolidationFidelity(draft, body) {
  const lc = (body || '').toLowerCase()
  const covered = (fields) => {
    const toks = [...new Set(fields.flatMap(anchorTokens))]
    if (!toks.length) return true
    return toks.some((t) => lc.includes(t.toLowerCase()))
  }
  const missing = []
  for (const p of (draft.premises || [])) {
    if (!covered([p.premise, p.seam, p.failingTest])) missing.push({ kind: 'premise', item: p.premise })
  }
  for (const c of (draft.contract || [])) {
    if (!covered([c])) missing.push({ kind: 'contract', item: c })
  }
  for (const cell of (draft.matrix.cells || []).filter((c) => c.verdict === 'GAP')) {
    if (!covered([cell.justification, cell.column, cell.state])) missing.push({ kind: 'gap-cell', item: `[${cell.state}] x [${cell.column}]` })
  }
  return missing
}

// Structural Contract-block coverage. consolidationFidelity catches DROPPED items
// (anchor absent from the WHOLE body) but not BURIED ones — a contract commitment the
// consolidator mentions only in prose, OUTSIDE the numbered "## Contract" block, reads
// as present to a token scan yet is invisible to `/verify-plan`, which reconciles against
// the Contract block. A run buried item 30 (and another buried a scoped commitment)
// exactly this way. This isolates the Contract block from the body and flags any contract
// item whose anchor is in the body but NOT inside that block, plus a missing block.
// The anchor heading must BEGIN with "contract" (the section heading "### Contract"), not
// merely contain it: `.*contract` matched the document TITLE "## deep-plan contract — …"
// first, isolating the title+intro as the "block" so all real Contract items read as
// buried → a wasted repair pass + a polluted body. The title begins with "deep-plan", so a
// leading-word match excludes it while still catching "### Contract".
function contractBlockCoverage(draft, body) {
  const items = draft.contract || []
  const lines = (body || '').split('\n')
  const start = lines.findIndex((l) => /^#{1,6}\s+contract\b/i.test(l))
  let block = ''
  if (start >= 0) {
    const rest = lines.slice(start + 1)
    let rel = rest.findIndex((l) => /^#{1,6}\s/.test(l))
    const end = rel === -1 ? lines.length : start + 1 + rel
    block = lines.slice(start, end).join('\n')
  }
  const blockLc = block.toLowerCase()
  const bodyLc = (body || '').toLowerCase()
  const buried = []
  for (const it of items) {
    const toks = [...new Set(anchorTokens(it))]
    if (!toks.length) continue
    const inBody = toks.some((t) => bodyLc.includes(t.toLowerCase()))
    const inBlock = toks.some((t) => blockLc.includes(t.toLowerCase()))
    if (inBody && !inBlock) buried.push(it)
  }
  return { missingBlock: start < 0 && items.length > 0, buried }
}

// The consolidator body must START at its title heading. A repair-pass agent
// prepended task narration — "Now I understand the consolidator's job … I'll re-emit …" —
// which leaked into result.body line 1. The role file says "Start directly with the
// heading — no preface", but a model can ignore it, so strip any lines before the first
// markdown heading. No-op when the body already begins with a heading (h === 0) or has
// none (h < 0) — a heading-less body is returned verbatim.
function stripPreamble(body) {
  const lines = (body || '').split('\n')
  const h = lines.findIndex((l) => /^#{1,6}\s/.test(l))
  return h > 0 ? lines.slice(h).join('\n') : (body || '')
}

// Structural count reconciliation. consolidationFidelity (token presence) and
// contractBlockCoverage (block membership) are blind to COUNT: a run rendered 34 premise
// rows from a 22-premise draft and neither check noticed the 22≠34 drift (the draft array
// was the lossy side — the gate validated only 22). This counts the rendered rows of each
// structured collection and reports the draft-vs-rendered pair, flagging a `shortfall`
// ONLY when the render has FEWER than the draft (a real drop in the deliverable; a richer
// render is fine and just surfaced in the counts). Counting is table/list-structural and
// tolerant of multiple tables per section (it subtracts one header + one separator per table).
function structuralCounts(draft, body) {
  const lines = (body || '').split('\n')
  const seg = (re) => {
    const s = lines.findIndex((l) => /^#{1,6}\s/.test(l) && re.test(l))
    if (s < 0) return null
    const rest = lines.slice(s + 1)
    const rel = rest.findIndex((l) => /^#{1,6}\s/.test(l))
    return rel === -1 ? rest : rest.slice(0, rel)
  }
  const tableRows = (re) => {
    const s = seg(re)
    if (!s) return 0
    const pipe = s.filter((l) => /^\s*\|/.test(l))
    const seps = pipe.filter((l) => /^\s*\|[\s:|-]+\|?\s*$/.test(l))
    return Math.max(0, pipe.length - 2 * seps.length)
  }
  const contractBlock = seg(/^#{1,6}\s+contract\b/i)
  const contractItems = contractBlock ? contractBlock.filter((l) => /^\s*\d+\.\s/.test(l)).length : 0
  const dimRows = (draft.dimension && draft.dimension.rows) || []
  const counts = {
    contract: { draft: (draft.contract || []).length, rendered: contractItems },
    dimension: { draft: dimRows.length, rendered: tableRows(/dimension/i) },
    premises: { draft: (draft.premises || []).length, rendered: tableRows(/premise/i) },
  }
  const shortfalls = Object.keys(counts).filter((k) => counts[k].rendered < counts[k].draft)
  return { counts, shortfalls }
}

// Escape a draft string for a markdown table cell (pipes break columns; newlines break rows).
function mdCell(s) { return String(s == null ? '' : s).replace(/\|/g, '\\|').replace(/\s*\n+\s*/g, ' ').trim() }

// Short, deterministic title for the contract — the intent's first markdown heading, else
// the affected-domain list. No load-bearing meaning; just a human label on the document.
function intentTitle(intentStr, domainList) {
  const h = String(intentStr || '').split('\n').map((l) => l.trim()).find((l) => /^#{1,3}\s+\S/.test(l))
  return h ? h.replace(/^#{1,3}\s+/, '').slice(0, 80) : ((domainList || []).join(', ') || 'plan')
}

// LOSSLESS deterministic render of the four plan-contract artifacts (+ premises) from the
// gated draft. The consolidator (an LLM) reliably DROPS rows retyping a 70-item contract /
// hundreds of matrix cells into markdown — a run rendered 54 of 70 contract items; others
// hit the same class, forcing a `jq` reconstruction from the JSON. Rendering in JS removes
// the only lossy step: no output-token ceiling, every draft row appears verbatim. The
// consolidator now writes only the narrative synthesis; THESE artifacts are the source of
// truth, and the structuralCounts/fidelity checks become a regression guard on this render.
function renderArtifacts(draft) {
  const cells = draft.matrix.cells || []
  const out = []
  out.push('## Contract', '')
  ;(draft.contract || []).forEach((c, i) => out.push(`${i + 1}. ${mdCell(c)}`))
  if (!(draft.contract || []).length) out.push('_(no contract items)_')
  out.push('')
  out.push('## Interaction matrix', '')
  out.push(`> ${(draft.matrix.states || []).length} states × ${(draft.matrix.columns || []).length} columns = ${cells.length} cells · ${cells.filter((c) => c.verdict === 'handled').length} handled / ${cells.filter((c) => c.verdict === 'N/A').length} N·A / **${cells.filter((c) => c.verdict === 'GAP').length} GAP**.`, '')
  out.push('| State | Column | Verdict | Where / justification |', '|---|---|---|---|')
  for (const c of cells) {
    const note = c.verdict === 'handled' ? (c.where || c.justification || '') : (c.justification || c.where || '')
    out.push(`| ${mdCell(c.state)} | ${mdCell(c.column)} | ${c.verdict} | ${mdCell(note)} |`)
  }
  out.push('')
  out.push('## Dimension table', '')
  out.push('| Variable | Unit / base | Cap | require/check seam |', '|---|---|---|---|')
  for (const r of (draft.dimension.rows || [])) out.push(`| ${mdCell(r.variable)} | ${mdCell(r.unitBase)} | ${mdCell(r.cap)} | ${mdCell(r.seam)} |`)
  if (!(draft.dimension.rows || []).length) out.push('| _(none)_ | | | |')
  const dviol = draft.dimension.violations || []
  if (dviol.length) {
    out.push('', '**Dimension violations:**')
    for (const v of dviol) out.push(`- ${mdCell(v.variable)}: ${mdCell(v.issue)} — ${v.resolution ? mdCell(v.resolution) : '**UNRESOLVED**'}`)
  }
  out.push('')
  out.push('## Precondition diff', '')
  out.push('| Guard | Copy | Old precondition | New reality | Resolution |', '|---|---|---|---|---|')
  for (const p of (draft.precondition || [])) out.push(`| ${mdCell(p.guard)} | ${mdCell(p.copy)} | ${mdCell(p.oldPrecondition)} | ${mdCell(p.newReality)} | ${mdCell(p.resolution)} |`)
  if (!(draft.precondition || []).length) out.push('| _(no copied guards)_ | | | | |')
  out.push('')
  out.push('## Failing-first tests & premises', '')
  out.push('| Premise | require/check seam | Failing-first test | Status |', '|---|---|---|---|')
  for (const p of (draft.premises || [])) out.push(`| ${mdCell(p.premise)} | ${mdCell(p.seam)} | ${mdCell(p.failingTest)} | ${mdCell(p.status || '')} |`)
  if (!(draft.premises || []).length) out.push('| _(none)_ | | | |')
  return out.join('\n')
}

// Seam extraction — the primary code site (Class:line / file.kt:line) a commitment turns
// on. Lets GAP cells / contract items that name the SAME seam be grouped: the headline GAP
// count measures CELLS, but most cells repeat ONE un-built surface across rows (a run:
// 100 GAP cells over ~9 real decisions). gapSeamCount recovers the decision count (B).
// sharedSeams flags seams carrying a committed directive (contract item) AND >=1 other
// reference — the consistency-watch set passed to the consolidator, which is hard-ruled to
// surface any two commitments giving CONFLICTING directives for the same mechanism as a
// `### ⚠️ Contradiction` theme (C — the fork the gate passed); renderVerdict counts those
// themes back out of the narrative so the top-line cannot hide a contradiction.
function extractSeam(s) {
  const m = String(s || '').match(/\b([A-Z][A-Za-z0-9]+(?:\.[a-z]{1,4})?:\d+)/)
  return m ? m[1] : null
}
function gapSeamCount(draft) {
  const seams = new Set()
  let unseamed = 0
  for (const c of (draft.matrix.cells || [])) {
    if (c.verdict !== 'GAP') continue
    const s = extractSeam(c.justification || c.where)
    if (s) seams.add(s)
    else unseamed++
  }
  return { seams: seams.size, unseamed }
}
function sharedSeams(draft) {
  const byKind = new Map()
  const add = (seam, kind) => { if (!seam) return; if (!byKind.has(seam)) byKind.set(seam, { contract: 0, cell: 0 }); byKind.get(seam)[kind]++ }
  for (const it of (draft.contract || [])) add(extractSeam(it), 'contract')
  for (const c of (draft.matrix.cells || [])) if (c.verdict === 'GAP') add(extractSeam(c.justification || c.where), 'cell')
  const out = []
  for (const [seam, g] of byKind) if (g.contract >= 1 && g.contract + g.cell >= 2) out.push({ seam, contract: g.contract, cell: g.cell })
  return out.sort((a, b) => (b.contract + b.cell) - (a.contract + a.cell))
}

// HONEST deterministic verdict header. The programmatic gate PASSES whenever every cell is
// non-empty and justified — which is NOT "no open interactions": a matrix full of
// substantively-justified GAPs passes. A run shipped `gate: PASS, residualGaps: 0` atop 56
// real unresolved GAP cells while the refute loop hit its round cap WITHOUT converging —
// both invisible in the old top-line, which read as "clean/done". This header leads with the
// figures that measure remaining work, BEFORE the gate verdict, and reframes PASS so it
// cannot be misread.
function renderVerdict(draft, m) {
  const cells = draft.matrix.cells || []
  const gapCells = cells.filter((c) => c.verdict === 'GAP')
  const dviol = (draft.dimension.violations || []).filter((v) => !(v.resolution && v.resolution.trim()))
  // (A) Trajectory shape: a FLAT fresh-per-round sequence (11,11,8) means the surface is far
  // from exhausted — independent re-runs keep finding NEW interactions; a DECAYING one
  // (11,5,1) means a round or two more would close it. "did not converge" alone can't tell
  // the planner which — so name the shape.
  const fbr = m.freshByRound || []
  const peak = fbr.length ? Math.max(...fbr) : 0
  const last = fbr.length ? fbr[fbr.length - 1] : 0
  const shape = m.converged ? 'CONVERGED' : (peak > 0 && last <= peak * 0.5 ? 'DECAYING' : 'FLAT')
  const shapeNote = shape === 'FLAT'
    ? ' (final ≈ peak — the interaction surface is far from exhausted; independent re-runs keep finding new interactions, not the same draft re-sampled)'
    : shape === 'DECAYING' ? ' (decaying toward zero — one or two more rounds would likely close it)'
      : ' (a full round surfaced no new refutation)'
  // Decompose the final round's fresh into resolution-attacks vs new-surface: a FLAT shape
  // with a high resolution share means fix-attack equilibrium (each reopening patch mints new
  // attack surface — the loop is generative), NOT that the original surface is unexplored.
  const mix = (m.freshMixByRound || [])[(m.freshMixByRound || []).length - 1]
  const mixNote = mix && mix.resolution + mix.newSurface > 0
    ? ` Final-round mix: ${mix.resolution} attack earlier rounds' RESOLUTIONS vs ${mix.newSurface} new surface — a high resolution share means the loop is in fix-attack equilibrium (each fix mints new attack surface), not still discovering the original surface.`
    : ''
  const trajectory = fbr.length ? ` Fresh refutations per round: [${fbr.join(', ')}] — shape **${shape}**${shapeNote}.${mixNote}` : ''
  const refute = m.converged
    ? `${m.rounds} breadth round(s), the last one with no fresh refutation.${trajectory}`
    : `${m.rounds} breadth round(s) with ${m.lastFresh} fresh refutation(s) still landing in the last one.${trajectory} Treat this contract as a SAMPLE of the interaction space, not an exhaustive enumeration — for coverage, run again (SKILL.md › One run does not exhaust).`
  // (B) GAP clustering: the headline cell count over-states remaining unknowns — most cells
  // are ONE un-built surface repeated across rows. Reduce to distinct seams (+ the Síntese
  // theme count) so the planner reads ~decisions, not ~cells.
  const { seams: gapSeams, unseamed } = gapSeamCount(draft)
  const clusterTotal = gapSeams + unseamed
  const lines = [
    `## deep-plan contract — ${m.title}`,
    ``,
    `### Verdict`,
    ``,
  ]
  // (C) Contradiction surfacing: the gate checks completeness, NOT consistency between
  // commitments — a run shipped contract item 12 ("source from swept RS, NOT bucket")
  // beside cells/Síntese saying "source from bucket delta". The consolidator is hard-ruled to
  // emit `### ⚠️ Contradiction` themes; we count them back out so the top-line leads with them.
  if (m.contradictions) lines.push(`- **⚠️ Contradictions flagged: ${m.contradictions}** — two commitments give conflicting directives for the same mechanism/seam. RESOLVE THESE FIRST (see the \`⚠️ Contradiction\` theme(s) in ## Síntese); a gate PASS does not detect inconsistency between commitments.`)
  lines.push(
    `- **Unresolved interactions:** ${gapCells.length} substantive GAP cell(s)${dviol.length ? ` + ${dviol.length} unresolved dimension violation(s)` : ''} across a ${(draft.matrix.states || []).length}×${(draft.matrix.columns || []).length} matrix. **This is the real remaining work** — each is an interaction the plan has not closed.`,
    `- **GAP clustering:** those ${gapCells.length} cell(s) reduce to ~${clusterTotal} distinct seam(s)${m.narrativeThemes ? `, grouped into ${m.narrativeThemes} decision theme(s) in ## Síntese` : ''} — the headline counts cells, not independent unknowns; most cells are one un-built surface repeated across rows.`,
    `- **Refute:** ${refute}`,
    `- **Gate:** ${m.gate} — ${m.gate === 'PASS' ? 'every cell is filled and justified' : `${m.residualGaps} cell(s) left empty/unjustified`}. PASS means *no blank or bare-GAP cell*; it does **NOT** mean zero open interactions — see the GAP count above.`,
    `- **Affected domains:** ${(m.domains || []).join(', ') || '(n/a)'}`,
  )
  if (gapCells.length || dviol.length) {
    lines.push('', `### ⚠️ Unresolved — resolve or explicitly accept before finalizing`, '')
    for (const c of gapCells) lines.push(`- GAP: ${c.state} × ${c.column} — ${c.justification || '_(unjustified — must resolve)_'}`)
    for (const v of dviol) lines.push(`- DIM: ${v.variable} — ${v.issue} (unresolved)`)
  }
  return lines.join('\n')
}

// =====================================================================
// SEED MODE — for the engine sanity test: feed an incomplete draft and assert
// the Gate fails/throws.
// =====================================================================
if (seedDraft) {
  phase('Gate')
  const { pass, violations } = gateCheck(seedDraft, (seedDraft._copiedGuards || []))
  log(`seed-mode gate: ${pass ? 'PASS' : 'FAIL'} (${violations.length} violations)`)
  if (!pass && !noThrow) throw new Error('GATE FAIL (seed mode):\n' + violations.map(x => `- ${x.kind}: ${x.detail}`).join('\n'))
  return { gate: pass ? 'PASS' : 'FAIL', violations, contract: seedDraft, seedMode: true }
}

// SEED-PATCH MODE — exercises applyPatch() deterministically for the engine test, the
// same way seedDraft exercises gateCheck (no live agents). ARGS.seedPatch =
// { draft, patch }; returns the merged draft.
const seedPatch = ARGS.seedPatch
if (seedPatch) {
  phase('Gate')
  const merged = applyPatch(seedPatch.draft, seedPatch.patch)
  log('seed-patch mode: merged patch into draft')
  return { merged, seedMode: true }
}

// SEED-REFUTE-MERGE MODE — exercises the refute round's CONSERVATIVE merge (N independent
// refuter patches applied in order over one draft, GAP winning every collision) for the engine
// test. ARGS.seedRefuteMerge = { draft, patches }; returns the merged draft.
const seedRefuteMerge = ARGS.seedRefuteMerge
if (seedRefuteMerge) {
  phase('Refute')
  let merged = seedRefuteMerge.draft
  for (const p of (seedRefuteMerge.patches || [])) merged = applyPatch(merged, p, { conservative: true })
  log(`seed-refute-merge mode: merged ${(seedRefuteMerge.patches || []).length} refuter patch(es) conservatively`)
  return { merged, seedMode: true }
}

// SEED-FIDELITY MODE — exercises consolidationFidelity() deterministically for the
// engine test. ARGS.seedFidelity = { draft, body }; returns the omission list.
const seedFidelity = ARGS.seedFidelity
if (seedFidelity) {
  phase('Synthesize')
  const missing = consolidationFidelity(seedFidelity.draft, seedFidelity.body)
  log(`seed-fidelity mode: ${missing.length} omission(s)`)
  return { missing, seedMode: true }
}

// SEED-COLUMNS MODE — exercises columnsWithSeam() deterministically for the engine test.
// ARGS.seedColumns = { columns }; returns { kept, dropped }.
const seedColumns = ARGS.seedColumns
if (seedColumns) {
  phase('Enumerate')
  const { kept, dropped } = columnsWithSeam(seedColumns.columns)
  log(`seed-columns mode: kept ${kept.length}, dropped ${dropped.length}`)
  return { kept, dropped, seedMode: true }
}

// SEED-CONTRACT-BLOCK MODE — exercises contractBlockCoverage() deterministically for the
// engine test. ARGS.seedContractBlock = { draft, body }; returns { missingBlock, buried }.
const seedContractBlock = ARGS.seedContractBlock
if (seedContractBlock) {
  phase('Synthesize')
  const r = contractBlockCoverage(seedContractBlock.draft, seedContractBlock.body)
  log(`seed-contract-block mode: missingBlock=${r.missingBlock}, buried=${r.buried.length}`)
  return { ...r, seedMode: true }
}

// SEED-STRIP MODE — exercises stripPreamble() deterministically for the engine test.
// ARGS.seedStrip = { body }; returns the stripped body.
const seedStrip = ARGS.seedStrip
if (seedStrip) {
  phase('Synthesize')
  const stripped = stripPreamble(seedStrip.body)
  log('seed-strip mode: stripped consolidator preamble')
  return { stripped, seedMode: true }
}

// SEED-STRUCTURAL MODE — exercises structuralCounts() deterministically for the engine test.
// ARGS.seedStructural = { draft, body }; returns { counts, shortfalls }.
const seedStructural = ARGS.seedStructural
if (seedStructural) {
  phase('Synthesize')
  const r = structuralCounts(seedStructural.draft, seedStructural.body)
  log(`seed-structural mode: shortfalls=${r.shortfalls.join(',') || 'none'}`)
  return { ...r, seedMode: true }
}

// SEED-MISSING MODE — exercises missingCells() deterministically for the engine test.
// ARGS.seedMissing = { draft }; returns the missing (state,column) pairs.
const seedMissing = ARGS.seedMissing
if (seedMissing) {
  phase('Gate')
  const missing = missingCells(seedMissing.draft)
  log(`seed-missing mode: ${missing.length} missing cell(s)`)
  return { missing, seedMode: true }
}

// SEED-ARTIFACTS MODE — exercises renderArtifacts() deterministically for the engine test.
// ARGS.seedArtifacts = { draft }; returns the rendered markdown.
const seedArtifacts = ARGS.seedArtifacts
if (seedArtifacts) {
  phase('Synthesize')
  const md = renderArtifacts(seedArtifacts.draft)
  log('seed-artifacts mode: rendered the four artifacts deterministically')
  return { md, seedMode: true }
}

// SEED-VERDICT MODE — exercises renderVerdict() deterministically for the engine test.
// ARGS.seedVerdict = { draft, meta }; returns the rendered header.
const seedVerdict = ARGS.seedVerdict
if (seedVerdict) {
  phase('Synthesize')
  const md = renderVerdict(seedVerdict.draft, seedVerdict.meta)
  log('seed-verdict mode: rendered the honest verdict header')
  return { md, seedMode: true }
}

// SEED-SPLIT MODE — exercises splitByCells() for the engine test.
// ARGS.seedSplit = { items, cells }; returns the contiguous groups.
const seedSplit = ARGS.seedSplit
if (seedSplit) {
  phase('Analyze')
  const groups = splitByCells(seedSplit.items, seedSplit.cells)
  log(`seed-split mode: ${groups.length} group(s) for ${seedSplit.cells} cell(s)`)
  return { groups, cellsPerAgent: CELLS_PER_AGENT, maxFillAgents: MAX_FILL_AGENTS, seedMode: true }
}

// SEED-ASSEMBLE MODE — exercises assemble() (axis coding + cell landing + precondition rows
// seeded from object copies) for the engine test. ARGS.seedAssemble = { cols, seams, dim, cells }.
const seedAssemble = ARGS.seedAssemble
if (seedAssemble) {
  phase('Analyze')
  const s = seedAssemble
  const draft0 = assemble(s.cols || {}, s.seams || {}, s.dim || {}, s.cells || {})
  log(`seed-assemble mode: ${draft0.matrix.states.length} state(s) x ${draft0.matrix.columns.length} column(s), ${draft0.precondition.length} precondition row(s)`)
  return { draft: draft0, seedMode: true }
}

// SEED-ROLES MODE — exposes the EFFECTIVE routing (tier, per-role model/effort after
// ARGS.full / ARGS.models / ARGS.roles, refuter count and lens set) without calling a single
// agent, so a benchmark arm is assertable in the engine test instead of in a live run.
// ARGS.seedRoles = true.
const seedRoles = ARGS.seedRoles
if (seedRoles) {
  phase('Enumerate')
  log(`seed-roles mode: tier=${TIER}, ${REFUTERS_PER_ROUND} refuter(s), ${ACTIVE_LENSES.length} lens(es)`)
  return { tier: TIER, roles: effectiveRoles(), refuters: REFUTERS_PER_ROUND, lenses: ACTIVE_LENSES, seedMode: true }
}

// =====================================================================
// PHASES 1+2 — ENUMERATE ∥ ANALYZE, one `parallel`. Only the matrix cells depend on another
// agent's output (agent 2's columns), so they are chained INSIDE that thunk; the seam map, the
// dimension table and the premise/test obligations depend on the intent alone and start at
// t=0. Every agent carries an explicit `phase` so the two progress groups do not race on the
// global phase() state while they overlap in time.
// =====================================================================
phase('Enumerate')
log(`tier=${TIER} refuters=${REFUTERS_PER_ROUND} roles=${JSON.stringify(effectiveRoles())}`)

// Agent 4 sharded by matrix ROW: each agent answers the whole row of the states it is given,
// sized by CELLS (splitByCells) rather than one agent per state — the per-agent fixed cost
// dominates. A single agent over the full cartesian left 22–60 % of the grid empty, which an
// expensive serial fill then re-did. A dropped shard is LOGGED: a silent cap reads as "covered
// everything" when it did not.
async function fillCellsByRow(c, columnLines) {
  const groups = splitByCells(c.states || [], (c.states || []).length * (c.columns || []).length)
  const shards = await parallel(groups.map((states, i) => () => agent(
    ctx('lifecycle-matrix.md', `analyze:cells-${i + 1}`)
      + `\n\n=== MATRIX ROWS TO FILL (${states.length} state(s)) ===\n` + states.map(s => `- State (row): ${s}`).join('\n')
      + `\nColumns:\n${columnLines}\n`
      + `Answer EVERY column for EACH listed state — handled (with where) / N·A (with justification) / GAP (with justification). Every field <= 240 chars. Write each cell's \`state\` and \`column\` EXACTLY as listed (they begin with a code such as S3 / C12 — the code alone is enough); never paraphrase a label.`,
    { label: `analyze:cells-${i + 1}`, phase: 'Analyze', schema: MATRIX_CELLS, ...role('a4'), agentType: 'general-purpose' },
  )))
  const cells = []
  shards.forEach((sh, i) => {
    if (sh) cells.push(...(sh.cells || []))
    else log(`  matrix row shard ${i + 1} ([${groups[i].join(' | ')}]) returned nothing — its cells fall through to the targeted fill.`)
  })
  return { cells }
}

const columnLine = (c) => `- ${c.name} @ ${c.site} (reads ${c.reads})`
const [colsAndCells, seams, dim, premOut] = await parallel([
  () => agent(ctx('matrix-columns.md', 'enumerate:matrix-columns'), { label: 'enumerate:matrix-columns', phase: 'Enumerate', schema: MATRIX_COLUMNS, ...role('a2'), agentType: 'general-purpose' })
    .then((c) => {
      if (!c) return null
      // Matrix discipline: keep only columns citing a concrete seam (file:line / source file)
      // so the matrix stays dense — a column without one bloats the grid and leaves empty
      // cells. A genuinely-missing interaction is re-added by a refuter WITH a site.
      const { kept, dropped } = columnsWithSeam(c.columns)
      c.columns = kept
      // Code the axes NOW so every downstream agent (shards, refuters, fills) sees `S3 …` /
      // `C12 …` and cells key by code, never by free text an agent may paraphrase.
      c.states = codeAxes(c.states, 'S')
      c.columns.forEach((col, i) => { col.name = AXIS_CODE_RE.test(col.name) ? col.name : `C${i + 1} ${col.name}` })
      log(`Enumerated ${kept.length} matrix columns (dropped ${dropped.length} lacking a concrete seam), ${(c.states || []).length} new states.`)
      if (dropped.length) log(`  pruned columns (no file:line): ${dropped.map(x => x.name).join(', ')}`)
      return fillCellsByRow(c, kept.map(columnLine).join('\n')).then((cells) => ({ cols: c, cells }))
    }),
  () => agent(ctx('state-mutation-seams.md', 'enumerate:state-mutation-seams'), { label: 'enumerate:state-mutation-seams', phase: 'Enumerate', schema: SETTLEMENT_SEAMS, ...role('a3'), agentType: 'general-purpose' }),
  () => agent(ctx('dimension-table.md', 'analyze:dimension-table'), { label: 'analyze:dimension-table', phase: 'Analyze', schema: DIMENSION_TABLE, ...role('a1'), agentType: 'general-purpose' }),
  () => agent(ctx('test-coverage.md', 'analyze:premises-tests'), { label: 'analyze:premises-tests', phase: 'Analyze', schema: PREMISE_OBLIGATIONS, ...role('a5'), agentType: 'general-purpose' }),
])
const cols = colsAndCells && colsAndCells.cols
const cellsOut = colsAndCells && colsAndCells.cells
if (!cols || !seams) throw new Error('Enumerate phase failed — matrix columns or state-mutation seams missing.')
if (!dim || !cellsOut || !premOut) throw new Error('Analyze phase failed — a specialist slice is missing.')
log(`Enumerated ${(seams.copiedGuards || []).length} copied guard(s).`)

let draft = assemble(cols, seams, dim, cellsOut)
draft.premises = premOut.premises || []
// Contract block, folded deterministically: agent 5's own commitments plus one line per
// failing-first test obligation. A prompt used to ask an agent for this fold, and dropped items.
draft.contract = [
  ...(premOut.contract || []),
  ...(premOut.tests || []).map(t => `Failing-first test [${t.level}]: ${t.scenario} — catches ${t.catches}`),
]
canonicalizeMatrixStates(draft) // collapse any verbose/terse state drift before missingCells keys off it

// Targeted fill: ONE agent over exactly the pairs the row shards left empty. There is no
// separate expensive resolver hop any more — the shards own the grid, the gate owns the rest.
const stillMissing = missingCells(draft)
let targetedFilled = 0
if (stillMissing.length) {
  const fill = await agent(
    ctx('lifecycle-matrix.md', 'analyze:cells-fill')
      + `\n\n=== MISSING CELLS TO FILL (${stillMissing.length}) ===\n`
      + stillMissing.map(m => `- [${m.state}] x [${m.column}]`).join('\n')
      + `\n\nColumn sites:\n${(cols.columns || []).map(columnLine).join('\n')}\n`
      + `Answer ONLY these pairs — handled (with where) / N·A (with justification) / GAP (with justification). Write each cell's \`state\` and \`column\` EXACTLY as listed (the leading S#/C# code alone is enough); never paraphrase a label. Every field <= 240 chars.`,
    { label: 'analyze:cells-fill', phase: 'Analyze', schema: MATRIX_CELLS, ...role('a4'), agentType: 'general-purpose' },
  )
  if (fill) {
    targetedFilled = (fill.cells || []).length
    // Conservative: the fill was asked ONLY for the missing pairs; a stray answer for an
    // existing pair must not bury a row shard's GAP.
    draft = applyPatch(draft, { cellsUpsert: fill.cells || [] }, { conservative: true })
  }
}
log(`Matrix: ${(cols.states || []).length} states x ${(cols.columns || []).length} columns; shards filled ${(cellsOut.cells || []).length} cells; targeted fill filled ${targetedFilled} of ${stillMissing.length} missing`)

// =====================================================================
// PHASE 3 — REFUTE: anchoring-free lensed refuters in breadth, ONE round by default. Each
// refuter emits its OWN reopening patch — whoever holds the evidence writes the reopening —
// and the patches merge conservatively (GAP wins). There is no resolver hop.
// =====================================================================
phase('Refute')
const seen = new Set()
let round = 0
let lastRoundFresh = 0 // fresh refutations in the final executed round — feeds the convergence verdict
const freshByRound = [] // fresh count per round — the trajectory shape (FLAT vs DECAYING) renderVerdict reports
// Per-round decomposition of fresh into resolution-attacks vs new-surface. A run's
// FLAT [30,25,23] decomposed to ~11/16 refutations attacking RESOLUTIONS minted by earlier
// rounds — the loop is GENERATIVE (each reopening patch mints new attack surface), not
// re-sampling an unexplored original surface. The mix is what tells the planner which.
const freshMixByRound = []
while (round < REFUTE_ROUNDS) {
  round++
  const summary = draftSummary(draft)
  // Already-open roots: cells the draft already marks GAP + unresolved dimension
  // violations. Fed to refuters so a round's "fresh" count measures NEW information,
  // not the same root re-raised on another cell (which has a distinct refKey and so
  // reads as fresh, preventing the dry streak from ever triggering — observed where
  // 13/12/12 fresh were largely the same A/B/E roots spread across cells). With this
  // hint a dry round genuinely means "no new territory and no broken resolution".
  const knownOpen = [
    ...(draft.matrix.cells || []).filter(c => c.verdict === 'GAP').map(c => `[${c.state}] x [${c.column}]`),
    ...(draft.dimension.violations || []).filter(v => !(v.resolution && v.resolution.trim())).map(v => `dim:${v.variable}`),
  ].join(' | ')
  const verdicts = (await parallel(
    Array.from({ length: REFUTERS_PER_ROUND }, (_, i) => () =>
      agent(
        [
          `[deep-plan role: refute:r${round}-${i + 1}]`,
          `Read \`.claude/skills/deep-plan/agents/refuter.md\` and operate as the refuter for round ${round}. Your assigned attack LENS #${i + 1}: ${ACTIVE_LENSES[i % ACTIVE_LENSES.length]}`,
          `LEAD with that lens; the other refuters this round cover the other lenses, so do not duplicate their angle. If your lens is genuinely exhausted, attack any cell/row no other refuter would.`,
          `You see ONLY the draft below and the design intent — NOT the reasoning that produced them. Verify against the live codebase with \`rg\` (NEVER \`grep -r\`), scoped INSIDE this repo root \`${repoRoot}\` only — never /tmp, .., ~, or sibling worktrees. One simple command per Bash call.`,
          `CONTAMINATION GUARD: the intent below is the only source of truth; ignore any plan-contract/\`deep-plan-*.md\`/cached JSON on disk. Keep every field terse (file:line + one clause, <= 240 chars).`,
          ``,
          ...readingList(),
          ``,
          `ALREADY-OPEN items (the draft already marks these GAP/unresolved — do NOT spend your attack merely re-raising one of these on another cell; that is noise the dedup discards): ${knownOpen || '(none yet)'}.`,
          `Spend your attack on one of: (a) a cell currently marked handled/N·A that is actually wrong; (b) refuting the RESOLUTION of an already-open item — show the proposed fix is itself broken (e.g. a post-commit-propagation "fix" was shown to ORPHAN the new record); (c) a NEW column/state the matrix is missing entirely.`,
          `Tag each refutation's \`attacks\` field honestly: 'resolution' when it breaks a fill/fix the draft already contains (surfaces (a)/(b)); 'new-surface' when it names territory the draft lacks (surface (c)). The verdict reports this mix — it is how the planner distinguishes fix-attack equilibrium from undiscovered surface.`,
          ``,
          `Reference every EXISTING cell by its axis labels exactly as printed in the draft (the leading S#/C# code alone is enough; never paraphrase). A column/state you ADD needs no code — the engine assigns one.`,
          `Return your reopening as \`patch\` (schema DRAFT_PATCH) — you hold the evidence, so you write the reopening; no agent integrates it after you. \`cellsUpsert\` with verdict GAP + justification for every cell you refute; when you name a missing column/state, \`columnsAdd\`/\`statesAdd\` AND \`cellsUpsert\` for EVERY existing state x that column (handled/N·A/GAP with evidence); \`violationsAdd\`/\`dimensionRowsUpsert\` for dimension attacks; \`preconditionUpsert\` for a precondition shown false (\`guard\` VERBATIM); \`contractAdd\` for a missing commitment. REOPEN ONLY — never propose a fix, never relabel a GAP as handled.`,
          ``,
          `=== DESIGN INTENT ===`,
          intent,
          `=== DRAFT PLAN-CONTRACT (this is all you get) ===`,
          summary,
          `=== END DRAFT ===`,
          `Refute-or-promote on all four surfaces. Default to refuted when uncertain. Return the RefutationVerdict.`,
        ].join('\n'),
        { label: `refute:r${round}-${i + 1}`, phase: 'Refute', schema: REFUTATION, ...role('a6'), agentType: 'general-purpose' },
      )
    )
  )).filter(Boolean)

  const fresh = []
  for (const verdict of verdicts) {
    for (const r of (verdict.refutations || [])) {
      const k = refKey(r)
      if (!seen.has(k)) { seen.add(k); fresh.push(r) }
    }
    for (const mc of (verdict.missingColumns || [])) {
      const k = `missing-column::${mc.site}`
      if (!seen.has(k)) { seen.add(k); fresh.push({ surface: 'missing-column', target: mc.site, scenario: `reads ${mc.reads}`, forces: `add column for ${mc.site}`, attacks: 'new-surface' }) }
    }
  }

  // The trajectory metrics are computed BEFORE any merge, so `fresh` keeps measuring new
  // information against the draft the refuters actually saw.
  lastRoundFresh = fresh.length
  freshByRound.push(fresh.length)
  const mixResolution = fresh.filter((r) => r.attacks === 'resolution').length
  freshMixByRound.push({ resolution: mixResolution, newSurface: fresh.length - mixResolution })

  // Merge each refuter's own reopening patch, in order, CONSERVATIVELY: a cell two refuters
  // both touch resolves through mergeCell (GAP wins), so the last patch applied cannot bury
  // an earlier reopening.
  let patched = 0
  for (const verdict of verdicts) {
    if (!verdict.patch) continue
    patched++
    draft = applyPatch(draft, verdict.patch, { conservative: true })
  }
  // A named missing column that no patch declared still becomes an axis, so its empty pairs
  // surface in missingCells and get filled by the gate's per-column pass.
  for (const verdict of verdicts) {
    for (const mc of (verdict.missingColumns || [])) {
      if (!mc || !mc.site) continue
      if (resolveAxis(mc.site, draft.matrix.columns) !== null) continue
      draft = applyPatch(draft, { columnsAdd: [`${mc.site} (reads ${mc.reads || 'the affected state'})`] })
    }
  }
  log(`Refute round ${round}: ${fresh.length} fresh refutation(s); ${patched}/${verdicts.length} refuter patch(es) merged.`)
}
log(`Refute ended after ${round} round(s).`)

// The only LLM step left in the gate: a patch for the violations a per-column fill cannot
// close (unjustified GAPs, non-executable premises, dimension violations, unfilled
// precondition rows), with any residual empty pairs as a fallback. Patch-only — re-emitting
// the growing draft is what crashed a run on the 64k output-token ceiling.
function justifyPrompt(label, d, viols, residualEmpties) {
  return [
    `[deep-plan role: ${label}]`,
    `You are the deep-plan GATE JUSTIFIER. The programmatic gate found the violations below. Resolve each: give every GAP a written justification, make every load-bearing premise executable (a require/check seam + a failing-first test), resolve dimension violations by adding cap+seam, and complete every per-copy precondition row (oldPrecondition/newReality/resolution all non-empty, \`guard\` VERBATIM so it keeps matching the predicate). A GAP you cannot close must carry an explicit written justification; never delete a real GAP by relabeling it handled without evidence.`,
    `Return ONLY a PATCH (schema DRAFT_PATCH) — emit just the cells/rows/premises/columns/contract items you ADD or CHANGE. Unchanged content is merged in automatically; do NOT re-emit the whole draft. Key exactly so the merge lands: a cell by (state,column) in \`cellsUpsert\`; a dimension row by \`variable\` in \`dimensionRowsUpsert\`; a premise by its text in \`premisesUpsert\`; a precondition row by (guard,copy) in \`preconditionUpsert\`; a dimension violation via \`violationsResolved\` [{variable, resolution}].`,
    `A require/check seam is an executable assertion at the boundary, written in this repo's language and idiom (read \`docs/agents/skills-config.md\` › Stack if unsure) — the rule is stack-agnostic; the assertion form is just the local idiom.`,
    `TERSENESS IS MANDATORY — an over-long output crashes serialization (the StructuredOutput call returns nothing and the whole run is wasted). Every field: file:line + one clause, <= 240 chars, no paragraphs, no quoting the intent back. Do not pad.`,
    `CONTAMINATION GUARD: the DESIGN INTENT below is the only source of truth. Ignore any plan-contract/\`deep-plan-*.md\`/cached JSON you might have seen on disk — never let it reshape this draft toward another feature.`,
    ``,
    `=== DESIGN INTENT ===`,
    intent,
    `=== CURRENT DRAFT (JSON) ===`,
    JSON.stringify(d),
    `=== GATE VIOLATIONS ===`,
    viols.map(x => `- ${x.kind}: ${x.detail}`).join('\n') || '(none)',
    residualEmpties.length ? `=== EMPTY CELLS STILL UNFILLED (answer every one) ===\n` + residualEmpties.map(m => `- [${m.state}] x [${m.column}]`).join('\n') : ``,
    ``,
    `Guard copies that each need a precondition row: ` + (seams.copiedGuards || []).map(g => `\`${g.predicate}\` (${(g.copies || []).length} copies: ${(g.copies || []).map(copyOf).join(', ')})`).join('; '),
  ].join('\n')
}

// =====================================================================
// PHASE 4 — GATE: programmatic completeness. The refuters grow the matrix, so the empty pairs
// are filled FIRST — cheap agents, batched by column and sized by cells, in parallel — and the
// bounded justify loop then only sees what a fill cannot close. A residual after the loop is
// RECORDED, never thrown — deep-plan never blocks (SKILL.md Phase 6).
// =====================================================================
phase('Gate')
const empties = missingCells(draft)
if (empties.length) {
  const byColumn = new Map()
  for (const m of empties) {
    const k = normCode(m.column)
    if (!byColumn.has(k)) byColumn.set(k, { column: m.column, states: [] })
    byColumn.get(k).states.push(m.state)
  }
  const groups = splitByCells([...byColumn.values()], empties.length)
  const colMeta = new Map((cols.columns || []).map(c => [normCode(c.name), c]))
  log(`Gate: ${empties.length} empty grid cell(s) across ${byColumn.size} column(s) — ${groups.length} fill agent(s) of ~${CELLS_PER_AGENT} cells, in parallel.`)
  // The fill gets the states and the column seams, NOT the whole draftSummary: the summary is
  // the single largest per-agent cost here and the column's cells do not exist yet, so it
  // bought nothing.
  const fills = await parallel(groups.map((batch, i) => () => agent(
    ctx('lifecycle-matrix.md', `gate:fill-${i + 1}`)
      + `\n\n=== MATRIX COLUMNS TO FILL (${batch.length}) ===\n`
      + batch.map((g) => {
        const meta = colMeta.get(normCode(g.column))
        return `Column: ${g.column}\n  site: ${meta ? `${meta.site} (reads ${meta.reads})` : 'the site named in the column label'}\n  states (rows) still unanswered:\n` + g.states.map(s => `  - ${s}`).join('\n')
      }).join('\n')
      + `\n\nAnswer EVERY listed state of EACH column — handled (with where) / N·A (with justification) / GAP (with justification). Write each cell's \`state\` and \`column\` EXACTLY as listed (the leading S#/C# code alone is enough); never paraphrase a label. Every field <= 240 chars.`,
    { label: `gate:fill-${i + 1}`, phase: 'Gate', schema: MATRIX_CELLS, ...role('a4'), agentType: 'general-purpose' },
  )))
  fills.forEach((f, i) => {
    // Conservative: this runs AFTER the refuters, so a fill answer for an already-filled pair
    // must never overwrite a refuter's reopening (GAP wins).
    if (f) draft = applyPatch(draft, { cellsUpsert: f.cells || [] }, { conservative: true })
    else log(`  gate fill batch ${i + 1} ([${groups[i].map(g => g.column).join(' | ')}]) returned nothing — its cells stay empty for the justify pass.`)
  })
}
let { pass, violations } = gateCheck(draft, seams.copiedGuards)
let gateRound = 0
while (!pass && gateRound < GATE_JUSTIFY_ROUNDS) {
  gateRound++
  const residualEmpties = missingCells(draft)
  const nonCellViolations = violations.filter(x => x.kind !== 'empty-cell')
  log(`Gate: ${violations.length} violation(s)${residualEmpties.length ? `, ${residualEmpties.length} empty grid cell(s) the column fills did not close` : ''} — justify pass ${gateRound}/${GATE_JUSTIFY_ROUNDS}.`)
  const justified = await agent(
    justifyPrompt(`gate:justify-${gateRound}`, draft, nonCellViolations, residualEmpties),
    { label: `gate:justify-${gateRound}`, phase: 'Gate', schema: DRAFT_PATCH, ...role('justify'), agentType: 'general-purpose' },
  )
  if (justified) draft = applyPatch(draft, justified)
  ;({ pass, violations } = gateCheck(draft, seams.copiedGuards))
}
log(`Gate: ${pass ? 'PASS' : 'FAIL'} (${violations.length} residual violation(s)).`)
// A residual-GAP run is NOT fatal. A run threw here and lost ~3M tokens / 2h:
// a refuter grew the matrix, the single justify pass left new cartesian pairs empty, and
// the throw nuked the run before synthesize ever ran. deep-plan never blocks (SKILL.md
// Phase 6) — surface residual GAPs in the result (gate:FAIL + residualGaps + the ⚠️
// Unresolved block renderVerdict emits) and STILL synthesize; a flagged contract is
// incomparably more useful than a 0-byte output. (Seed mode keeps its own throw for the
// gate-detects-incompleteness test; `noThrow` there returns the violation list instead.)
if (!pass) {
  log(`Gate: ${violations.length} residual GAP(s) recorded — proceeding to synthesize (deep-plan never blocks; see result.violations).`)
}

// =====================================================================
// PHASE 5 — SYNTHESIZE: deterministic verdict header + the LLM's narrative + the
// LOSSLESS deterministic artifacts. The four artifacts and the verdict are rendered in JS
// from the gated draft (renderVerdict/renderArtifacts) — the consolidator no longer
// RETYPES them, which is what dropped 16/70 contract items on a run (the recurring
// consolidation-lossiness class). The LLM is left ONLY the narrative synthesis: clustering
// the GAPs/refutations into the BLOCKERs the planner must decide. The fidelity/structural
// checks now verify the engine's own render (a regression guard), not the LLM's retype.
// =====================================================================
phase('Synthesize')
const converged = lastRoundFresh === 0
const artifacts = renderArtifacts(draft)
// (C) consistency watch: seams carrying a committed directive (contract item) AND >=1 other
// reference are where two parts of the contract can give conflicting directives (the fork
// the gate passed). Hand the list to the consolidator so it refutes or confirms consistency
// and surfaces any clash as a `### ⚠️ Contradiction` theme.
const watchSeams = sharedSeams(draft)
const narrative = stripPreamble(await agent(
  [
    `[deep-plan role: synthesize:consolidate]`,
    `Read \`.claude/skills/deep-plan/agents/consolidate.md\` and operate as the consolidator.`,
    `The verdict header and the four structured artifacts (Contract, interaction matrix, dimension table, precondition diff, premises) are rendered DETERMINISTICALLY by the engine — do NOT reproduce them, do NOT write a title or any "## Contract"/matrix/table. Your job is the NARRATIVE SYNTHESIS only.`,
    `Cluster the GAP cells and refutations into the handful of THEMES / likely BLOCKERs the planner must decide, in priority order; each theme cites the file:line it turns on and the decision required. Do NOT re-soften any GAP. The gate verdict is fixed: ${pass ? 'PASS' : 'FAIL'} with ${violations.length} residual GAP(s).`,
    `CONSISTENCY WATCH (highest priority): scan ALL commitments — contract items, cells, and your own themes — for any TWO that give CONFLICTING directives for the same mechanism/seam (e.g. "source X from the swept set" vs "source X from the bucket delta"). For each clash emit a \`### ⚠️ Contradiction — <seam>\` theme FIRST, naming both sides and the decision required. The gate cannot detect this; it is the single highest-value thing you produce. Seams already carrying >=2 commitments (start here): ${watchSeams.slice(0, 12).map(s => s.seam).join(', ') || '(none flagged)'}.`,
    `Begin DIRECTLY with the heading \`## Síntese\` — no preamble, no commentary about your task. Keep the heading \`## Síntese\` and your subsection markers \`### \` verbatim — the engine counts themes/contradictions from them. Use \`### <theme>\` subsections, 1–3 sentences each, naming the cells/rows covered.`,
    ``,
    `=== GATED DRAFT (JSON) ===`,
    JSON.stringify(draft),
    `=== RESIDUAL VIOLATIONS ===`,
    violations.map(x => `- ${x.kind}: ${x.detail}`).join('\n') || '(none)',
    ``,
    `Affected domains: ${domains.join(', ')}.`,
  ].join('\n'),
  { label: 'synthesize:consolidate', phase: 'Synthesize', ...role('consolidate'), agentType: 'general-purpose' },
))
// Count themes / contradictions back OUT of the narrative so renderVerdict's top-line leads
// with what the consolidator actually flagged (deterministic — reports the LLM's own output).
const narrativeThemes = (String(narrative).match(/^### /gm) || []).length
const contradictions = (String(narrative).match(/^###[^\n]*contradiction/gim) || []).length
const verdictHeader = renderVerdict(draft, {
  title: intentTitle(intent, domains),
  domains,
  gate: pass ? 'PASS' : 'FAIL',
  residualGaps: violations.length,
  converged,
  rounds: round,
  lastFresh: lastRoundFresh,
  freshByRound,
  freshMixByRound,
  narrativeThemes,
  contradictions,
})
let body = [verdictHeader, narrative || '## Síntese\n\n_(no narrative produced)_', artifacts].join('\n\n')

// Regression guard on the DETERMINISTIC render (B): with the artifacts rendered in JS, these
// should always be clean — a hit means renderArtifacts dropped something (an engine bug),
// not the old LLM-retype lossiness. Surfaced in the result + logged; no LLM repair pass
// (that pass was the cure for retype-lossiness, which deterministic render eliminates).
const omissions = consolidationFidelity(draft, body)
const coverage = contractBlockCoverage(draft, body)
const structural = structuralCounts(draft, body)
if (omissions.length || coverage.buried.length || coverage.missingBlock || structural.shortfalls.length) {
  log(`⚠️ deterministic-render guard tripped (engine bug — investigate renderArtifacts): ${omissions.length} dropped, ${coverage.buried.length} buried${coverage.missingBlock ? ', NO Contract block' : ''}${structural.shortfalls.length ? `, shortfall [${structural.shortfalls.join(', ')}]` : ''}.`)
}

return {
  gate: pass ? 'PASS' : 'FAIL',
  tier: TIER,
  roles: effectiveRoles(),
  residualGaps: violations.length,
  violations,
  contract: draft,
  body: body || '(consolidator produced no body)',
  rounds: round,
  freshByRound,
  freshMixByRound,
  converged,
  contradictions,
  consolidationOmissions: omissions,
  contractBuried: coverage.buried,
  contractBlockMissing: coverage.missingBlock,
  structuralCounts: structural.counts,
  structuralShortfalls: structural.shortfalls,
}
