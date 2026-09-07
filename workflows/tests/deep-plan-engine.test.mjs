#!/usr/bin/env node
/*
 * deep-plan engine tests — the pure functions inside `workflows/deep-plan.js`
 * (`gateCheck`, `applyPatch`, `assemble`, `splitByCells`, the axis resolution and the effective
 * model routing). They are not importable directly: the Workflow runtime injects globals
 * (`agent`/`parallel`/`phase`/`log`/`args`) and wraps the body in an async function so top-level
 * `await`/`return` are legal. This harness reproduces that wrapper — it strips `export ` and runs
 * the body inside an async function with stub globals — then drives the test-only `args` seed
 * paths the script exposes, so we exercise the REAL shipped functions, never a copy.
 *
 * It doubles as the `node --check` of CLAUDE.md: a parse error here fails the suite.
 *
 * Run: node workflows/tests/deep-plan-engine.test.mjs
 */
import fs from 'node:fs'

const SRC = fs
  .readFileSync(new URL('../deep-plan.js', import.meta.url), 'utf8')
  .replace(/^export const meta/m, 'const meta')

// Run the script body with stub globals and the given `args`. Every seed mode returns before
// any live agent call, so the stubs are never exercised — but the body references them.
function runScript(args) {
  const names = ['agent', 'parallel', 'phase', 'log', 'args']
  const stubs = [
    async () => null,
    async (thunks) => Promise.all(thunks.map((t) => t())),
    () => {},
    () => {},
    args,
  ]
  const fn = new Function(...names, `return (async () => {\n${SRC}\n})()`)
  return fn(...stubs)
}

let failures = 0
let total = 0
function check(name, cond) {
  total++
  if (cond) console.log(`  ok   ${name}`)
  else { console.error(`  FAIL ${name}`); failures++ }
}

const emptyDraft = () => ({
  contract: [],
  matrix: { states: ['S1 held'], columns: ['C1 reader'], cells: [] },
  dimension: { rows: [], violations: [] },
  precondition: [],
  premises: [],
})

const cellOf = (d, state, column) =>
  (d.matrix.cells || []).find((c) => c.state.startsWith(state) && c.column.startsWith(column))

async function main() {
  console.log('gateCheck (seedDraft):')
  let r = await runScript({ seedDraft: emptyDraft(), noThrow: true })
  check('an empty matrix cell fails the gate', r.gate === 'FAIL' && r.violations.some((v) => v.kind === 'empty-cell'))

  const complete = emptyDraft()
  complete.matrix.cells = [{ state: 'S1 held', column: 'C1 reader', verdict: 'handled', where: 'Svc.kt:10' }]
  r = await runScript({ seedDraft: complete, noThrow: true })
  check('a complete, justified draft passes the gate', r.gate === 'PASS' && r.violations.length === 0)

  const capped = emptyDraft()
  capped.matrix.cells = [{ state: 'S1 held', column: 'C1 reader', verdict: 'N/A', justification: 'no effect' }]
  capped.dimension.rows = [{ variable: 'residual', unitBase: 'residual', cap: '<= outstanding', seam: 'Svc.kt:5' }]
  r = await runScript({ seedDraft: capped, noThrow: true })
  check('a capped invariant with zero premises trips no-executable-premise',
    r.violations.some((v) => v.kind === 'no-executable-premise'))

  console.log('applyPatch — conservative merge (seedPatch):')
  const filled = emptyDraft()
  filled.matrix.cells = [{ state: 'S1 held', column: 'C1 reader', verdict: 'GAP', justification: 'reopened by a refuter' }]
  r = await runScript({ seedPatch: { draft: filled, patch: { cellsUpsert: [{ state: 'S1 held', column: 'C1 reader', verdict: 'handled', where: 'Svc.kt:12' }] } } })
  check('non-conservative merge overwrites a GAP (default path)',
    cellOf(r.merged, 'S1', 'C1').verdict === 'handled')

  r = await runScript({ seedRefuteMerge: { draft: filled, patches: [{ cellsUpsert: [{ state: 'S1 held', column: 'C1 reader', verdict: 'handled', where: 'Svc.kt:12' }] }] } })
  check('conservative merge keeps the GAP (a later patch cannot bury a reopening)',
    cellOf(r.merged, 'S1', 'C1').verdict === 'GAP')

  r = await runScript({
    seedRefuteMerge: {
      draft: emptyDraft(),
      patches: [
        { cellsUpsert: [{ state: 'S1 held', column: 'C1 reader', verdict: 'GAP', justification: 'refuter one: ordering hazard' }] },
        { cellsUpsert: [{ state: 'S1 held', column: 'C1 reader', verdict: 'N/A', justification: 'refuter two: cannot fire' }] },
      ],
    },
  })
  check('two refuters on one cell: GAP outranks N/A regardless of order',
    cellOf(r.merged, 'S1', 'C1').verdict === 'GAP')

  r = await runScript({ seedPatch: { draft: emptyDraft(), patch: { violationsAdd: [{ variable: 'net', issue: 'uncapped' }, { variable: 'net', issue: 'uncapped' }] } } })
  check('violationsAdd appends uniquely by (variable, issue)', r.merged.dimension.violations.length === 1)

  console.log('coded axes (seedAssemble / seedPatch):')
  r = await runScript({
    seedAssemble: {
      cols: { states: ['Shipment.status = HELD'], columns: [{ name: 'OrderService.settle', site: 'OrderService.kt:44', reads: 'status' }] },
      seams: {}, dim: {}, cells: {},
    },
  })
  check('assemble mints a leading code on every axis',
    r.draft.matrix.states[0].startsWith('S1 ') && r.draft.matrix.columns[0].startsWith('C1 '))

  r = await runScript({
    seedAssemble: {
      cols: { states: ['Shipment.status = HELD'], columns: [{ name: 'OrderService.settle', site: 'OrderService.kt:44', reads: 'status' }] },
      seams: {}, dim: {},
      // A shard that PARAPHRASED its assigned label — the phantom-state class.
      cells: { cells: [{ state: 'S1', column: 'C1', verdict: 'GAP', justification: 'paraphrased label' }] },
    },
  })
  check('a cell written under the bare code lands on the coded axis (no phantom state)',
    r.draft.matrix.states.length === 1 && r.draft.matrix.columns.length === 1 && r.draft.matrix.cells.length === 1)

  console.log('axis resolution (seedPatch):')
  const twoStates = emptyDraft()
  twoStates.matrix.states = ['S1 Order.status=PAID', 'S2 Transfer.status=PAID']
  r = await runScript({ seedPatch: { draft: twoStates, patch: { statesAdd: ['status=PAID'] } } })
  check('an ambiguous terse state does not bridge two qualified states', r.merged.matrix.states.length === 3)

  const twoCols = emptyDraft()
  twoCols.matrix.columns = ['C1 OrderService.settle at OrderService.kt:44']
  r = await runScript({ seedPatch: { draft: twoCols, patch: { columnsAdd: ['settle hook OrderService.kt:44'] } } })
  check('a column citing an existing seam resolves onto it instead of duplicating',
    r.merged.matrix.columns.length === 1)

  const symCols = emptyDraft()
  symCols.matrix.columns = ['C1 OrderService.updateStatus (agreement path)']
  r = await runScript({ seedPatch: { draft: symCols, patch: { columnsAdd: ['OrderService.updateStatus (non-agreement path)'] } } })
  check('a column resolves by its leading Class.method symbol when neither carries a line',
    r.merged.matrix.columns.length === 1)

  const otherMethod = emptyDraft()
  otherMethod.matrix.columns = ['C1 OrderService.updateStatus']
  r = await runScript({ seedPatch: { draft: otherMethod, patch: { columnsAdd: ['OrderService.cancel'] } } })
  check('a different method of the same class stays a different column',
    r.merged.matrix.columns.length === 2)

  console.log('canonicalizeColumnsBySeam (seedPatch):')
  const dupSeam = emptyDraft()
  dupSeam.matrix.columns = ['C1 cancel expired at CancelService.kt:476', 'C2 CancelService.kt:476 sweeper']
  dupSeam.matrix.states = ['S1 held']
  dupSeam.matrix.cells = [
    { state: 'S1 held', column: 'C1 cancel expired at CancelService.kt:476', verdict: 'handled', where: 'x' },
    { state: 'S1 held', column: 'C2 CancelService.kt:476 sweeper', verdict: 'GAP', justification: 'unswept' },
  ]
  r = await runScript({ seedPatch: { draft: dupSeam, patch: {} } })
  check('two columns citing one seam collapse to one, GAP surviving',
    r.merged.matrix.columns.length === 1 && r.merged.matrix.cells.length === 1 && r.merged.matrix.cells[0].verdict === 'GAP')

  console.log('assemble — precondition rows from object copies (seedAssemble):')
  r = await runScript({
    seedAssemble: {
      cols: { states: [], columns: [] },
      seams: {
        copiedGuards: [{
          predicate: 'isSettleable()',
          copies: [
            { copy: 'A.kt:10', oldPrecondition: 'single instrument', newReality: 'two instruments', resolution: 'tighten @ A.kt:12' },
            'B.kt:20',
          ],
        }],
      },
      dim: {}, cells: {},
    },
  })
  check('a filled object copy seeds a filled precondition row',
    r.draft.precondition.length === 2 && r.draft.precondition[0].resolution === 'tighten @ A.kt:12')
  check('a bare string copy still seeds an empty row for the gate to demand',
    r.draft.precondition[1].copy === 'B.kt:20' && r.draft.precondition[1].resolution === '')

  console.log('splitByCells (seedSplit):')
  r = await runScript({ seedSplit: { items: ['a', 'b', 'c', 'd', 'e', 'f', 'g'], cells: 30 } })
  check('a small grid stays one agent', r.groups.length === 1 && r.groups[0].length === 7)
  r = await runScript({ seedSplit: { items: Array.from({ length: 8 }, (_, i) => `s${i}`), cells: 240 } })
  check('~80 cells per agent sizes the shard count, not one agent per axis', r.groups.length === 3)
  r = await runScript({ seedSplit: { items: Array.from({ length: 40 }, (_, i) => `s${i}`), cells: 4000 } })
  check('the shard count is capped at maxFillAgents', r.groups.length === r.maxFillAgents)
  r = await runScript({ seedSplit: { items: ['a', 'b'], cells: 4000 } })
  check('never more groups than items', r.groups.length === 2)
  r = await runScript({ seedSplit: { items: [], cells: 100 } })
  check('an empty item list produces no agent', r.groups.length === 0)

  console.log('routing and tiers (seedRoles):')
  r = await runScript({ seedRoles: true })
  check('economy is the default tier', r.tier === 'economy')
  check('economy keeps the worker agents on the cheap model', r.roles.a2.model === 'sonnet' && r.roles.a4.model === 'sonnet')
  check('economy dispatches 4 refuters on 4 lenses', r.refuters === 4 && r.lenses.length === 4)

  r = await runScript({ seedRoles: true, tier: 'full' })
  check('full upgrades every worker agent to the decision tier',
    r.roles.a2.model === 'opus' && r.roles.a4.model === 'opus' && r.roles.a5.model === 'opus')

  r = await runScript({ seedRoles: true, full: true })
  check('the legacy `full: true` spelling still selects the full tier', r.tier === 'full')

  r = await runScript({ seedRoles: true, tier: 'reduced' })
  check('reduced runs 2 refuters on 2 lenses', r.tier === 'reduced' && r.refuters === 2 && r.lenses.length === 2)
  check('reduced keeps the quantity and completeness lenses',
    /QUANTITY/.test(r.lenses[0]) && /COMPLETENESS/.test(r.lenses[1]))

  r = await runScript({ seedRoles: true, tier: 'nonsense' })
  check('an unknown tier falls back to economy instead of throwing', r.tier === 'economy')

  r = await runScript({ seedRoles: true, models: { worker: { model: 'haiku', effort: 'low' } } })
  check('config Models overrides the whole worker tier', r.roles.a2.model === 'haiku' && r.roles.a2.effort === 'low')

  r = await runScript({ seedRoles: true, tier: 'full', roles: { a4: { model: 'sonnet' } } })
  check('a per-role override wins over the full-tier upgrade', r.roles.a4.model === 'sonnet' && r.roles.a2.model === 'opus')

  r = await runScript({ seedRoles: true, roles: { nope: { model: 'opus' }, a6: 'opus' } })
  check('an unknown role and a non-object override are ignored, not fatal',
    r.roles.a6.model === 'opus' && r.roles.a6.effort === 'high' && !('nope' in r.roles))

  r = await runScript({ seedRoles: true, refutersPerRound: 6, tier: 'reduced' })
  check('a raised refuter count cycles the reduced lens pair', r.refuters === 6 && r.lenses.length === 2)

  console.log('missingCells (seedMissing):')
  const grown = emptyDraft()
  grown.matrix.states = ['S1 a', 'S2 b']
  grown.matrix.columns = ['C1 x', 'C2 y']
  grown.matrix.cells = [{ state: 'S1 a', column: 'C1 x', verdict: 'handled', where: 'k' }]
  r = await runScript({ seedMissing: { draft: grown } })
  check('a grown matrix reports every empty cartesian pair', r.missing.length === 3)

  console.log(`\n${total - failures}/${total} assertions passed`)
  if (failures) process.exit(1)
}

main().catch((e) => { console.error(e); process.exit(1) })
