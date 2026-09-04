export const meta = {
  name: 'implement',
  description: 'Wave-based implementation of an approved plan: fresh agent per wave + persistent ledger, verify and verify-plan at the end, opens the PR with documented decisions',
  whenToUse: 'Invoked by the /implement skill (.claude/skills/implement/SKILL.md) with an APPROVED plan. Do not invoke without a plan that has a Contract block.',
  phases: [
    { title: 'Setup', detail: 'validates branch, seeds/resumes ledger, splits the plan into waves, commits the deep-plan contract if present' },
    { title: 'Implement', detail: 'a queue of waves: fresh agent implements, incremental verify, commit, push, ledger; a partial wave enqueues its continuation' },
    { title: 'Verify', detail: 'verify ∥ verify-plan; recon-fix; one final full build ∥ the recheck' },
    { title: 'PR', detail: 'merge origin/<base> (never rebase), build only if the merge brought commits, gh pr create with an extensive body in the repo PR language' },
  ],
}

// args may arrive as a JSON-encoded string in some runtimes — defensive shim.
const ARGS = (() => {
  try { return typeof args === 'string' ? JSON.parse(args) : (args ?? {}) } catch (e) { return {} }
})()

const REQUIRED = ['planPath', 'branch', 'repoRoot', 'ledgerPath', 'timestamp']
const missingArgs = REQUIRED.filter((k) => !ARGS[k])
if (missingArgs.length) {
  return { status: 'blocked', stage: 'args', reason: `required args missing: ${missingArgs.join(', ')}` }
}

const BASE = ARGS.baseBranch || 'main'
const MAX_WAVES = Math.min(ARGS.maxWaves || 8, 12)
// Planned waves + slack for continuations (a wave that returns `partial`). A run cannot spin
// forever: past this many EXECUTED waves the queue is abandoned with what is committed.
const MAX_WAVE_RUNS = MAX_WAVES + 3
const ROLE_DIR = `${ARGS.repoRoot}/.claude/skills/implement/agents`
const CONFIG = 'docs/agents/skills-config.md'

// ---------------------------------------------------------------------------
// Role x model x effort. A DECIDER (writes what ships, or judges it) runs on the strong model at
// high effort; a WORKER (assembles a body from a ledger, splits a plan into waves) on the cheap
// model. Thinking is never disabled — effort is lowered instead. The tier names are the same five
// buckets every workflow here uses, and a repo overrides them verbatim in ${CONFIG} › Models (the
// skill reads that section and passes it as ARGS.models). NEVER set CLAUDE_CODE_SUBAGENT_MODEL:
// it is first in the model-resolution order and collapses every tier into one model.
// ---------------------------------------------------------------------------
const TIER_DEFAULTS = {
  decision: { model: 'opus', effort: 'high' },
  worker: { model: 'sonnet', effort: 'medium' },
  'pr-author': { model: 'sonnet', effort: 'medium' },
}
const TIERS = { ...TIER_DEFAULTS }
for (const [k, v] of Object.entries(ARGS.models || {})) {
  if (!TIER_DEFAULTS[k] || !v) continue
  TIERS[k] = { model: v.model || TIER_DEFAULTS[k].model, effort: v.effort || TIER_DEFAULTS[k].effort }
}
const t = (name, effort) => ({ ...TIERS[name], ...(effort ? { effort } : {}) })
const ROLE = {
  setup: t('worker'), // plan → waves
  wave: t('decision'), // wave-N (+retry) · recon-fix
  verify: t('decision', 'medium'), // verify-N · verify-final
  verifyPlan: t('decision'), // verify-plan (+recheck) — reconciles what another agent wrote
  prAuthor: t('pr-author'), // pr-author (ledger → PR body, merge of the base, push)
}

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------

const DECISION = {
  type: 'object',
  required: ['point', 'chosen', 'why'],
  properties: {
    point: { type: 'string', maxLength: 240 },
    options: { type: 'array', items: { type: 'string', maxLength: 160 }, maxItems: 4 },
    chosen: { type: 'string', maxLength: 240 },
    why: { type: 'string', maxLength: 240 },
  },
}

const SETUP_RESULT = {
  type: 'object',
  required: ['branchOk', 'resumed', 'waves'],
  properties: {
    branchOk: { type: 'boolean' },
    resumed: { type: 'boolean', description: 'true if a pre-existing ledger with completed waves was found' },
    waves: {
      type: 'array',
      maxItems: 12,
      description: 'ONLY pending waves (on a resume, exclude the ones already completed in the ledger)',
      items: {
        type: 'object',
        required: ['id', 'title', 'goal'],
        properties: {
          id: { type: 'string', description: 'the wave number; a continuation of wave N is N.1, N.2, …' },
          title: { type: 'string', maxLength: 80 },
          goal: { type: 'string', maxLength: 400 },
          contractItems: { type: 'array', items: { type: 'string', maxLength: 200 }, maxItems: 15 },
          files: { type: 'array', items: { type: 'string', maxLength: 200 }, maxItems: 20 },
        },
      },
    },
    planSummary: { type: 'string', maxLength: 600 },
    prTitle: { type: 'string', maxLength: 100, description: 'PR title in the repo commit language (Conventional Commits if config says so)' },
    contractCommitted: { type: 'boolean', description: 'true if a deep-plan contract was committed under .claude/deep-plan/' },
    blockedReason: { type: 'string', maxLength: 400 },
  },
}

const WAVE_RESULT = {
  type: 'object',
  required: ['status', 'untestedItems'],
  properties: {
    status: { type: 'string', enum: ['done', 'blocked', 'partial'] },
    remaining: {
      type: 'object',
      required: ['goal', 'contractItems'],
      description: 'required when status=partial: what is left of the wave, for the continuation agent',
      properties: {
        goal: { type: 'string', maxLength: 400 },
        contractItems: { type: 'array', items: { type: 'string', maxLength: 200 }, maxItems: 15 },
        files: { type: 'array', items: { type: 'string', maxLength: 200 }, maxItems: 20 },
      },
    },
    commitShas: { type: 'array', items: { type: 'string', maxLength: 40 }, maxItems: 10 },
    decisions: { type: 'array', items: DECISION, maxItems: 10 },
    testEvidence: {
      type: 'array',
      maxItems: 15,
      description: 'named test(s) per wave contract item, or na justification',
      items: { type: 'object', required: ['item'], properties: { item: { type: 'string', maxLength: 200 }, tests: { type: 'array', items: { type: 'string', maxLength: 160 }, maxItems: 5 }, na: { type: 'string', maxLength: 200 } } },
    },
    untestedItems: { type: 'array', items: { type: 'string', maxLength: 240 }, maxItems: 15, description: 'items without a test and without justification — gate: must be empty to close the wave' },
    handoff: { type: 'string', maxLength: 300 },
    blockedReason: { type: 'string', maxLength: 500 },
  },
}

const VERIFY_RESULT = {
  type: 'object',
  required: ['green'],
  properties: {
    green: { type: 'boolean' },
    commits: { type: 'array', items: { type: 'string', maxLength: 40 }, maxItems: 10 },
    failingSummary: { type: 'string', maxLength: 1500, description: 'tests/steps still failing, telegraphic' },
  },
}

const RECON_RESULT = {
  type: 'object',
  required: ['missing', 'diverged', 'unplanned', 'untestedPremises'],
  properties: {
    missing: { type: 'array', maxItems: 20, items: { type: 'object', required: ['item'], properties: { item: { type: 'string', maxLength: 240 }, note: { type: 'string', maxLength: 240 } } } },
    diverged: { type: 'array', maxItems: 20, items: { type: 'object', required: ['item'], properties: { item: { type: 'string', maxLength: 240 }, planned: { type: 'string', maxLength: 200 }, actual: { type: 'string', maxLength: 200 }, file: { type: 'string', maxLength: 160 } } } },
    unplanned: { type: 'array', maxItems: 20, items: { type: 'object', required: ['file'], properties: { file: { type: 'string', maxLength: 160 }, note: { type: 'string', maxLength: 240 } } } },
    untestedPremises: { type: 'array', maxItems: 15, items: { type: 'object', required: ['premise', 'file'], properties: { premise: { type: 'string', maxLength: 200 }, file: { type: 'string', maxLength: 160 } } } },
  },
}

const PR_RESULT = {
  type: 'object',
  required: ['status'],
  properties: {
    status: { type: 'string', enum: ['pr-opened', 'blocked', 'blocked-gate'] },
    prNumber: { type: 'integer' },
    prUrl: { type: 'string', maxLength: 200 },
    mergedMain: { type: 'boolean', description: 'true if merging the base brought new commits' },
    blockedReason: { type: 'string', maxLength: 600 },
  },
}

// ---------------------------------------------------------------------------
// Prompt helpers
// ---------------------------------------------------------------------------

// The first line marks the role: it is what ci/wf_timeline.py keys on to attribute a stage's
// turns and tokens. It must stay line 1 of every prompt this script builds.
function ctx(label) {
  return [
    `[implement role: ${label}]`,
    `Repo: ${ARGS.repoRoot} (work ONLY inside it; use rg, never grep -r; do not read /tmp, ~, or sibling worktrees).`,
    `Read ${CONFIG} for this repo's stack-specific inputs (Verify command, Sensitive domains, Docs layout, Conventions). If a section is absent, fall back to the stated default and say so.`,
    `Working branch: ${ARGS.branch} (base: origin/${BASE}). Mandatory guard: git branch --show-current must return exactly this; if it diverges, return blocked immediately.`,
    `Push authorized ONLY via "git push origin ${ARGS.branch}". Never force (not even --force-with-lease), never ${BASE}.`,
    `Approved plan: ${ARGS.planPath}. Ledger: ${ARGS.ledgerPath} (gitignored — lives in .claude/.implement/, outside versioning; NEVER commit it).`,
    `Your final text is parsed as structured data by the orchestrator — follow the schema, no extra prose.`,
  ].join('\n')
}

function setupPrompt() {
  return `${ctx('setup')}

You are the SETUP agent of the /implement workflow.

1. Confirm the branch guard and that the working tree is clean (git status --porcelain).
2. Read the plan (${ARGS.planPath}) in full — prose + "## Contract" (+ interaction matrix / precondition diff if present).
3. Ledger: if ${ARGS.ledgerPath} already exists, read it and PRESERVE all existing content — especially the "## Directives (from the user)" section, which is the orchestrator/user injection channel and takes precedence over agents' autonomous decisions. It is a RESUME (resumed=true) only if there are waves marked completed with commits; a ledger seeded with only a header/directives is NOT a resume. If it doesn't exist, create it (create the directory if needed) with:
   - plan: ${ARGS.planPath} | branch: ${ARGS.branch} | started: ${ARGS.timestamp}
   - sections "## Directives (from the user)" (empty if none), "## Waves" (checklist), "## Decisions", "## Handoffs".
4. ${ARGS.deepPlanContractPath ? `Deep-plan contract: ${ARGS.deepPlanContractPath}. If not yet committed on the branch under .claude/deep-plan/, copy it there (name <branch-with-/-as-->-<shortSha>.md), git add + commit (a chore commit adding the deep-plan plan-contract, per the repo's commit conventions in ${CONFIG} › Conventions) + push. It is the artifact the PR gate hook reads on a sensitive-domain branch.` : 'No deep-plan contract to commit.'}
5. Split the plan into AT MOST ${MAX_WAVES} PENDING waves, in dependency order: by the plan's phase structure if it has one, otherwise by logical commit groups (schema/migration + entity → service/logic → wiring/listeners → tests — adapt to the plan and to the repo's commit conventions). Each wave: id, title, goal (what will be true at the end), contractItems (numbers/text from the Contract it covers — EVERY Contract item must belong to exactly one wave), files (initial guess). Waves small enough for a fresh agent to finish without blowing context — ruler: **≤ 6 contract items and ≤ 8 new/changed files per wave**; above that, split. An over-large wave degrades the agent (a measured one reached 199 turns) and the continuation mechanism (\`partial\`) is the valve, not the plan.
6. Record the wave split in the ledger.

If anything blocks (wrong branch, plan with no Contract, dirty tree), return branchOk=false/blockedReason. Also suggest the prTitle (per the repo's commit conventions in ${CONFIG} › Conventions)${ARGS.prTitleHint ? ` — user hint: "${ARGS.prTitleHint}"` : ''}.`
}

function wavePrompt(wave, retryReason) {
  return `${ctx(`wave-${wave.id}${retryReason ? '-retry' : ''}`)}

Read ${ROLE_DIR}/wave-implementer.md and operate as that agent.

Your wave:
${JSON.stringify(wave, null, 2)}
${retryReason ? `\nRETRY: the previous attempt of this wave failed with: "${retryReason}". Read the ledger and git log to see what it left half-done before continuing — there may be uncommitted or partially committed work.` : ''}`
}

function verifyPrompt(label, attemptLabel, previousFailing) {
  return `${ctx(label)}

You are the VERIFY agent (${attemptLabel}) of the /implement workflow. All waves have committed; your job is to leave the whole build green.

Run the repo's FULL Verify command (${CONFIG} › Verify): format → lint → tests targeted at the branch diff (git diff --name-only origin/${BASE}...HEAD) → full build/test, plus the always-run gates listed there. Tee long output to a file and read failures from there if the output overflows. If config is absent, ask the user for the format/lint/build/test command.
${previousFailing ? `Pending failures from the previous attempt: ${previousFailing}` : ''}

Fix the failures you find (surgical changes; follow the repo's CLAUDE.md and conventions), commit (a fix commit per the repo's commit conventions — "post-implementation verify fixes" or more specific) + push per group of fixes. If the repo's test conventions / repo memory document known flaky tests, confirm with an isolated re-run before treating a failure as a regression.

green=true ONLY with the full Verify command passing end to end. If you can't, green=false + a precise failingSummary.`
}

function reconPrompt(isRecheck) {
  return `${ctx(isRecheck ? 'verify-plan-recheck' : 'verify-plan')}

You are the VERIFY-PLAN agent${isRecheck ? ' (RE-CHECK post-fix)' : ''} — plan↔diff reconciliation with fresh eyes. You implemented nothing; do not assume intent.

Read ${ARGS.repoRoot}/.claude/skills/verify-plan/SKILL.md and apply its Phase 1 (coverage / fidelity / scope-creep), with:
- Plan: ${ARGS.planPath} (use the "## Contract"; include the interaction matrix and precondition diff if present).
- Diff: git fetch origin ${BASE} && git diff origin/${BASE}...HEAD (everything is already committed).

Bounded: this is NOT a bug hunt. Item by item against the contract.

4th MECHANICAL bucket — untestedPremises: run git diff origin/${BASE}...HEAD over the branch's premises files (use the premises path pattern from ${CONFIG} › Docs layout; default 'docs/**/premises.md' if absent) and list every premise added/changed on this branch whose **Tests:** field is "none yet" or absent. For a premise introduced in the PR this is a merge gate (per the repo's premises rule), not a suggestion.

Return the four structured buckets; empty if clean.`
}

function reconFixPrompt(recon) {
  return `${ctx('recon-fix')}

Read ${ROLE_DIR}/wave-implementer.md and operate as that agent, with one difference: your "wave" is the verify-plan findings below. Resolve each Missing (implement the contracted item) and each Diverged (align the code to the contract — if the deviation is deliberate and superior, keep the code and record it as a decision with the why). Unplanned: assess; remove if it's value-less scope-creep, record as a decision if it stays. UntestedPremises: write the test that protects each premise (per the repo's test conventions in ${CONFIG} › Conventions — a test that BREAKS if the premise is violated, not a happy path) and fill its **Tests:** field. Incremental verify + commit + push + ledger as usual.

Findings:
${JSON.stringify(recon, null, 2)}`
}

function prPrompt(setup, decisions, residual, testEvidence) {
  return `${ctx('pr-author')}

Read ${ROLE_DIR}/pr-author.md and operate as that agent.

- Suggested title: ${setup.prTitle || ARGS.prTitleHint || '(derive from the plan)'}
- Plan summary: ${setup.planSummary || '(read the plan)'}
- Plan origin (link in the Context section if it's an issue/Jira/Slack): see the "Origin" header of the plan file.
- Basis for the test-plan section: the tests named per contract item below (mark [x] those the verify already ran; items with an na justification become a note, not a checkbox):
${JSON.stringify(testEvidence, null, 2)}
- Autonomous decisions (mandatory in the body, one by one):
${JSON.stringify(decisions, null, 2)}
- verify-plan residuals for the "What this PR does NOT cover" section (empty = omit the section):
${JSON.stringify(residual, null, 2)}
- Integrating the base: \`git fetch origin ${BASE} && git merge --no-edit origin/${BASE}\` — **never rebase, never force**. A conflict you cannot resolve mechanically and safely → \`git merge --abort\` + blocked.
- Build: the full Verify already ran green on this branch. Run it again ONLY if the merge brought new commits; if the merge was a no-op, go straight to the PR body.`
}

// ---------------------------------------------------------------------------
// Resilience: agent({schema}) THROWS when the subagent finishes without a
// StructuredOutput (throttling / retry cap), and a throw takes down the WHOLE
// workflow even with waves already completed, committed, and pushed (real case:
// the PR author opened the PR and crashed only at the report step — the entire
// run surfaced as "failed"). Degrade to null: every call site below already has
// null semantics.
// ---------------------------------------------------------------------------

async function tryAgent(prompt, opts) {
  try {
    return await agent(prompt, opts)
  } catch (e) {
    log(`agent ${opts?.label || '?'} crashed without structured output — degrading to null (${String((e && e.message) || e).slice(0, 140)})`)
    return null
  }
}

// ---------------------------------------------------------------------------
// Phases
// ---------------------------------------------------------------------------

phase('Setup')
log(`roles=${JSON.stringify(ROLE)}`)
const setup = await tryAgent(setupPrompt(), { schema: SETUP_RESULT, label: 'setup', ...ROLE.setup })
if (!setup) return { status: 'blocked', stage: 'setup', reason: 'setup agent died without a result' }
if (!setup.branchOk) return { status: 'blocked', stage: 'setup', reason: setup.blockedReason || 'invalid branch/preflight' }

const waves = (setup.waves || []).slice(0, MAX_WAVES)
log(`${setup.resumed ? 'RESUME — ' : ''}${waves.length} pending wave(s): ${waves.map((w) => `${w.id}:${w.title}`).join(' · ')}`)
if (!waves.length && !setup.resumed) return { status: 'blocked', stage: 'setup', reason: 'setup produced no waves' }

phase('Implement')
const allDecisions = []
const allTestEvidence = []
const waveReports = []
let blocked = null
// closing gate: done with a non-empty untestedItems does NOT close the wave (untested commitments are the cheapest review findings to prevent at the source)
const untestedFailure = (r) => ((r.untestedItems || []).length
  ? `test gate: items without a test or justification — ${r.untestedItems.join(' | ')}. Write the tests (or justify na in testEvidence) to close the wave.`
  : null)
// `partial` closes what it delivered (same test gate) but must say what is left and in what
// state it leaves the branch — that is the continuation agent's whole briefing.
const closeFailure = (r) => {
  if (!r) return 'no result'
  if (r.status === 'partial') {
    if (!(r.remaining && r.remaining.goal)) return 'partial without remaining: describe the goal + contractItems of what is left'
    if (!(r.handoff || '').trim() || r.handoff === 'none') return 'partial without handoff: describe the branch state for the continuation agent'
    return untestedFailure(r)
  }
  if (r.status !== 'done') return r.blockedReason || 'blocked without reason'
  return untestedFailure(r)
}
// Continuation id/title derived from the wave id: 3 → 3.1 → 3.2 (never 3.1.1).
const continuationOf = (wave, remaining) => {
  const [rootId, seq] = String(wave.id).split('.')
  const n = Number(seq || 0) + 1
  return {
    id: `${rootId}.${n}`,
    title: `${String(wave.title).replace(/ \(cont\. \d+\)$/, '')} (cont. ${n})`,
    goal: remaining.goal,
    contractItems: remaining.contractItems || [],
    files: remaining.files || wave.files,
  }
}
const pending = [...waves]
let executed = 0
while (pending.length) {
  if (executed >= MAX_WAVE_RUNS) {
    blocked = { wave: pending[0].id, title: pending[0].title, reason: `continuation ceiling: ${executed} waves executed (max ${MAX_WAVE_RUNS})` }
    break
  }
  const wave = pending.shift()
  executed++
  let res = await tryAgent(wavePrompt(wave), { schema: WAVE_RESULT, label: `wave-${wave.id}: ${wave.title}`, phase: 'Implement', ...ROLE.wave })
  let failure = closeFailure(res)
  if (failure) {
    log(`wave ${wave.id} did not close (${failure.slice(0, 160)}) — single retry with a fresh agent`)
    res = await tryAgent(wavePrompt(wave, failure), { schema: WAVE_RESULT, label: `wave-${wave.id}-retry`, phase: 'Implement', ...ROLE.wave })
    failure = closeFailure(res)
  }
  if (failure) {
    blocked = { wave: wave.id, title: wave.title, reason: failure }
    break
  }
  allDecisions.push(...(res.decisions || []))
  allTestEvidence.push(...(res.testEvidence || []))
  waveReports.push({ wave: wave.id, title: wave.title, status: res.status, commits: res.commitShas || [], handoff: res.handoff || 'none' })
  log(`wave ${wave.id} ${res.status === 'partial' ? 'partial' : 'done'}: ${(res.commitShas || []).length} commit(s), ${(res.decisions || []).length} decision(s), ${(res.testEvidence || []).length} item(s) with a named test`)
  if (res.status === 'partial') {
    const next = continuationOf(wave, res.remaining)
    pending.unshift(next)
    log(`wave ${wave.id} returned remaining — queued continuation ${next.id}: ${next.goal.slice(0, 120)}`)
  }
}
if (blocked) {
  return { status: 'blocked', stage: 'implement', blocked, wavesDone: waveReports, decisions: allDecisions, note: 'work of the completed waves is committed and pushed on the branch' }
}

phase('Verify')
const runVerify = (label, attemptLabel, previousFailing) => tryAgent(
  verifyPrompt(label, attemptLabel, previousFailing),
  { schema: VERIFY_RESULT, label, phase: 'Verify', ...ROLE.verify },
)
// verify-plan reads the diff the waves already committed, so it does not depend on the verify:
// the two open together. Fix commits the verify makes land in the recheck, which runs after the
// recon-fix.
const [v1, recon0] = await parallel([
  () => runVerify('verify-1', 'attempt 1 of 3', ''),
  () => tryAgent(reconPrompt(false), { schema: RECON_RESULT, label: 'verify-plan', phase: 'Verify', ...ROLE.verifyPlan }),
])
let green = !!v1?.green
let failingSummary = v1?.failingSummary || ''
if (!green) log(`verify attempt 1: still red — ${failingSummary.slice(0, 200)}`)
for (let attempt = 2; attempt <= 3 && !green; attempt++) {
  const v = await runVerify(`verify-${attempt}`, `attempt ${attempt} of 3`, failingSummary)
  green = !!v?.green
  failingSummary = v?.failingSummary || ''
  if (!green) log(`verify attempt ${attempt}: still red — ${failingSummary.slice(0, 200)}`)
}
if (!green) {
  return { status: 'verify-failed', stage: 'verify', failingSummary, wavesDone: waveReports, decisions: allDecisions, note: 'branch pushed with a red build — do NOT open a PR' }
}

let recon = recon0
if (recon && ((recon.missing || []).length || (recon.diverged || []).length || (recon.untestedPremises || []).length)) {
  log(`verify-plan: ${(recon.missing || []).length} missing, ${(recon.diverged || []).length} diverged, ${(recon.untestedPremises || []).length} premise(s) without a test — 1 fix round`)
  const fix = await tryAgent(reconFixPrompt(recon), { schema: WAVE_RESULT, label: 'recon-fix', phase: 'Verify', ...ROLE.wave })
  if (fix?.decisions) allDecisions.push(...fix.decisions)
  // The recon-fix committed new code: ONE final full build closes the run, in parallel with the
  // recheck. The PR author then builds again only if merging the base brings commits.
  const [vf, recheck] = await parallel([
    () => runVerify('verify-final', 'final build after the recon-fix', ''),
    () => tryAgent(reconPrompt(true), { schema: RECON_RESULT, label: 'verify-plan-recheck', phase: 'Verify', ...ROLE.verifyPlan }),
  ])
  green = !!vf?.green
  failingSummary = vf?.failingSummary || ''
  if (!green) {
    log(`final verify: red after the recon-fix — ${failingSummary.slice(0, 200)}`)
    const vr = await runVerify('verify-final-retry', 'last build (retry of the final build)', failingSummary)
    green = !!vr?.green
    failingSummary = vr?.failingSummary || failingSummary
  }
  if (!green) {
    return { status: 'verify-failed', stage: 'verify', failingSummary, wavesDone: waveReports, decisions: allDecisions, note: 'the recon-fix left the build red — do NOT open a PR' }
  }
  recon = recheck
}
const residual = recon || { missing: [], diverged: [], unplanned: [] }

phase('PR')
const pr = await tryAgent(prPrompt(setup, allDecisions, residual, allTestEvidence), { schema: PR_RESULT, label: 'pr-author', phase: 'PR', ...ROLE.prAuthor })
if (!pr) {
  // The PR author works (merge, push, gh pr create) BEFORE reporting — a crash at the report
  // step does not mean the PR was not opened. 'pr-unconfirmed' tells the orchestrator to check
  // `gh pr view --head <branch>` and resume from the ledger instead of re-running from scratch.
  return {
    status: 'pr-unconfirmed',
    stage: 'pr',
    note: 'pr-author died without structured output — the PR may have been opened; run gh pr view --head and read the ledger before re-running',
    resumed: setup.resumed,
    wavesDone: waveReports,
    decisions: allDecisions,
    testEvidence: allTestEvidence,
    verifyPlanResiduals: residual,
  }
}
return {
  status: pr.status || 'blocked',
  stage: 'pr',
  roles: ROLE,
  prNumber: pr?.prNumber,
  prUrl: pr?.prUrl,
  mergedMain: pr?.mergedMain || false,
  blockedReason: pr?.blockedReason,
  resumed: setup.resumed,
  wavesDone: waveReports,
  decisions: allDecisions,
  testEvidence: allTestEvidence,
  verifyPlanResiduals: residual,
}
