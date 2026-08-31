export const meta = {
  name: 'review-fix-loop',
  description: 'FIXED structure over an open PR: breadth (lenses) → single judge → consolidated review posted on the PR → fix → fix-review (model ≠ fixer), with at most one second fix + fix-review when the fix-review finds Blocker/High. No round machine: the repo\'s CI review on each push and the human code owner are the next gates.',
  whenToUse: 'Invoked by the /review-fix-loop skill (.claude/skills/review-fix-loop/SKILL.md) with an open PR and its branch checked out.',
  calibratedFor: 'Frontier models as of 2026-08 — re-audit on each model upgrade (see the skill\'s Contract).',
  phases: [
    { title: 'Scope', detail: 'deterministic deep/standard mode + an agent judging whether the diff solves ONE scoped problem' },
    { title: 'Review', detail: 'breadth (universal + flow lenses in deep, standard-review + 2 structural lenses in standard) + single judge: confidence >= 8, exclusions, refutation of B/H' },
    { title: 'Conciliate', detail: 'fuses with what is already on the PR, dedups, posts the consolidated review (once)' },
    { title: 'Fix', detail: 'addresses the posted Blocker/High/Medium, commit+push, updates the description, replies to divergences' },
    { title: 'Fix-review', detail: 'one reviewer, model != fixer, over the fix diff only; posts on the PR' },
  ],
}

// args arrives as a JSON-encoded string in some runtimes — defensive shim.
const ARGS = (() => {
  try { return typeof args === 'string' ? JSON.parse(args) : (args ?? {}) } catch (e) { return {} }
})()

const REQUIRED = ['prNumber', 'repoRoot', 'branch']
const missingArgs = REQUIRED.filter((k) => !ARGS[k])
if (missingArgs.length) {
  return { status: 'blocked', stage: 'args', reason: `required args missing: ${missingArgs.join(', ')}` }
}

const PR = ARGS.prNumber
const ROLE_DIR = `${ARGS.repoRoot}/.claude/skills/review-fix-loop/agents`
const DEEP_DIR = `${ARGS.repoRoot}/.claude/skills/deep-review/agents`
const CONFIG = 'docs/agents/skills-config.md'

// ---------------------------------------------------------------------------
// Role x model x effort. A DECIDER (judges code / a critical quantity, or writes what ships)
// runs on the strong model at high effort; a WORKER (collects evidence under a checklist) on the
// cheap model at medium/low. The fixer runs on a model DIFFERENT from the reviewer of its fix
// (reviewer != author: one model catches more bugs in another model's code than in its own).
// Thinking is never disabled — effort is lowered instead.
//
// The five buckets below are what a repo may override, verbatim, in ${CONFIG} › Models
// (decision / worker / fixer / fix-review / pr-author → model + effort). The orchestrator reads
// that section and passes it as ARGS.models; anything absent keeps the default here. Defaults name
// generic tiers (`opus`, `sonnet`) — a repo whose account has other models renames them in config.
// NEVER set CLAUDE_CODE_SUBAGENT_MODEL: it is first in the model-resolution order and collapses
// every tier below into one model, killing reviewer != fixer. The skill's preflight aborts on it.
// ---------------------------------------------------------------------------

const TIER_DEFAULTS = {
  decision: { model: 'opus', effort: 'high' },
  worker: { model: 'sonnet', effort: 'medium' },
  fixer: { model: 'opus', effort: 'high' },
  'fix-review': { model: 'sonnet', effort: 'high' },
  'pr-author': { model: 'sonnet', effort: 'medium' },
}
const TIERS = { ...TIER_DEFAULTS }
for (const [k, v] of Object.entries(ARGS.models || {})) {
  if (!TIER_DEFAULTS[k] || !v) continue
  TIERS[k] = { model: v.model || TIER_DEFAULTS[k].model, effort: v.effort || TIER_DEFAULTS[k].effort }
}
// `full` upgrades the worker lenses to the decision tier. Deterministic trigger: a deep-plan
// contract on the branch (or an explicit `tier` arg) — same trigger the deep-review heavy path uses.
const TIER = ARGS.tier === 'full' || ARGS.tier === 'economy' ? ARGS.tier : (ARGS.deepPlanContractOnBranch ? 'full' : 'economy')
const t = (name, effort) => ({ ...TIERS[name], ...(effort ? { effort } : {}) })
const ROLE = {
  scope: t('worker', 'low'),
  lensDeep: t('decision'), // derived-quantity + one lens per touched flow: cross-domain reasoning
  lensWorker: TIER === 'full' ? t('decision') : t('worker'), // adjacent · negative-space · contract · tests
  standard: t('decision'),
  judge: t('decision', 'medium'), // consolidator = judge: narrow rubric, not a search
  conciliate: t('worker'), // verifying B/H is the judge's job, not this one's
  fixer: t('fixer'),
  fixReview: t('fix-review'), // reviewer of the fix — model different from the fixer
}

// Deep mode fans out the genericized deep-review lens set installed alongside this skill: the
// UNIVERSAL lenses (always) + one FLOW lens per flow doc the change touches, via deep-review's
// generic flow-lens.md mechanism. The repo declares its flows as docs (config › Flows; the
// orchestrator passes the touched ones); a repo with no flows simply passes none. If a role file
// is missing on the branch, the lens prompt short-circuits to {"findings": []}.
const UNIVERSAL_LENSES = [
  { key: 'adjacent', file: 'adjacent-code.md', role: 'lensWorker' },
  { key: 'quantity', file: 'derived-quantity.md', role: 'lensDeep' },
  { key: 'negspace', file: 'negative-space.md', role: 'lensWorker' },
  { key: 'contract', file: 'contract-reconciler.md', role: 'lensWorker' },
  { key: 'tests', file: 'test-coverage.md', role: 'lensWorker' },
]
// Flow lenses come from the orchestrator (the touched flow docs): [{ name, doc }] where `doc` is
// the flow doc's full text. Each runs the generic flow-lens.md role file with that doc.
const FLOW_LENSES = (Array.isArray(ARGS.flows) ? ARGS.flows : [])
  .filter((l) => l && (l.name || l.doc))
  .slice(0, 8)
  .map((l, i) => ({ key: `flow-${i}`, file: 'flow-lens.md', role: 'lensDeep', name: String(l.name || `flow-${i}`).slice(0, 60), doc: String(l.doc || '').slice(0, 6000) }))
const DEEP_AGENTS = [...UNIVERSAL_LENSES, ...FLOW_LENSES]
// Standard: the mirror of the CI review action + the two STRUCTURAL lenses (negative space and
// contract x code) — the ones that depend on no domain knowledge and whose blind spots are the
// most expensive to leave uncovered.
const STANDARD_LENSES = ['negspace', 'contract']

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------

const SEVERITIES = ['Blocker', 'High', 'Medium', 'Low']

const FINDING = {
  type: 'object',
  required: ['id', 'severity', 'title', 'description', 'confidence'],
  properties: {
    id: { type: 'string', maxLength: 60, description: 'stable slug derived from file+topic, e.g. sweep-cap-missing' },
    severity: { type: 'string', enum: SEVERITIES },
    title: { type: 'string', maxLength: 120 },
    file: { type: 'string', maxLength: 160 },
    line: { type: 'string', maxLength: 20 },
    description: { type: 'string', maxLength: 500, description: 'concrete scenario where something observable goes wrong' },
    suggestedFix: { type: 'string', maxLength: 300 },
    confidence: { type: 'integer', minimum: 1, maximum: 10, description: '10 = reproduced by a test/trace with file:line; 8 = mechanism traced in the code with file:line and no contrary guard found; 6 = plausible, not traced; 5 or less = speculation' },
    premise: { type: 'string', maxLength: 160, pattern: '^p-[0-9a-f]{8}$|.{3,160}', description: 'optional: the p-xxxxxxxx id of the documented premise this finding violates (an exact H2 title is still accepted; the premise fetch command from ' + CONFIG + ' resolves one with --id-of "<title>")' },
    source: { type: 'string', maxLength: 80, description: 'lens | judge | fix-review | @human | <bot>' },
  },
}

const FINDINGS = {
  type: 'object',
  required: ['findings'],
  properties: {
    findings: { type: 'array', items: FINDING, maxItems: 25 },
    refuted: { type: 'array', maxItems: 20, description: 'the judge only: Blocker/High it discarded, with the evidence that kills them', items: { type: 'object', required: ['id', 'evidence'], properties: { id: { type: 'string', maxLength: 60 }, evidence: { type: 'string', maxLength: 240 } } } },
  },
}

const CONSOLIDATED = {
  type: 'object',
  required: ['findings', 'commentUrl'],
  properties: {
    findings: { type: 'array', items: FINDING, maxItems: 60 },
    commentUrl: { type: 'string', maxLength: 200 },
    droppedAsContested: { type: 'array', items: { type: 'string', maxLength: 60 }, maxItems: 20 },
  },
}

const SCOPE_RESULT = {
  type: 'object',
  required: ['scoped', 'problem', 'reason'],
  properties: {
    scoped: { type: 'boolean' },
    problem: { type: 'string', maxLength: 200 },
    reason: { type: 'string', maxLength: 300 },
    suggestedSlices: { type: 'array', maxItems: 6, items: { type: 'object', required: ['title'], properties: { title: { type: 'string', maxLength: 120 }, files: { type: 'array', items: { type: 'string', maxLength: 160 }, maxItems: 20 } } } },
  },
}

const FIX_RESULT = {
  type: 'object',
  required: ['status'],
  properties: {
    status: { type: 'string', enum: ['done', 'blocked'] },
    fixed: { type: 'array', maxItems: 30, items: { type: 'object', required: ['id'], properties: { id: { type: 'string', maxLength: 60 }, note: { type: 'string', maxLength: 200 } } } },
    diverged: { type: 'array', maxItems: 20, items: { type: 'object', required: ['id', 'reason'], properties: { id: { type: 'string', maxLength: 60 }, reason: { type: 'string', maxLength: 300 } } } },
    commits: { type: 'array', items: { type: 'string', maxLength: 40 }, maxItems: 15 },
    prBodyUpdated: { type: 'boolean' },
    violatedTrailer: { type: 'boolean', description: 'true iff EVERY commit of this fix carries the --trailer "Premises-Violated: …" the prompt handed over (false when the prompt asked for no trailer)' },
    blockedReason: { type: 'string', maxLength: 500 },
  },
}

const FIX_REVIEW = {
  type: 'object',
  required: ['verdict'],
  properties: {
    verdict: { type: 'string', enum: ['clean', 'open'], description: 'open iff regressions or unaddressed contains a Blocker/High' },
    regressions: { type: 'array', items: FINDING, maxItems: 15, description: 'NEW defects introduced by the fixer\'s commits' },
    unaddressed: { type: 'array', maxItems: 20, items: { type: 'object', required: ['id', 'severity', 'why'], properties: { id: { type: 'string', maxLength: 60 }, severity: { type: 'string', enum: SEVERITIES }, why: { type: 'string', maxLength: 300 } } } },
    commentUrl: { type: 'string', maxLength: 200 },
  },
}

// ---------------------------------------------------------------------------
// Posting predicate — deterministic, in code: no agent decides this. Confidence < 8 and the
// classes listed under `## Review exclusions` in the repo's recurring-failure-modes doc (they
// arrive as ARGS.exclusions, extracted by the skill's preflight) drop BEFORE the judge. An
// invalid regex is logged and ignored, never takes the run down.
// ---------------------------------------------------------------------------

const MIN_CONFIDENCE = 8
const exclusions = []
for (const e of ARGS.exclusions || []) {
  try {
    exclusions.push({ id: e.id, re: new RegExp(e.pattern, 'i') })
  } catch (err) {
    log(`exclusion ${e && e.id ? e.id : '?'} is invalid — ignored (${String((err && err.message) || err).slice(0, 100)})`)
  }
}
let droppedByConfidence = 0
const droppedByExclusion = {}

function applyHardFilters(findings, stage) {
  const input = findings || []
  const kept = []
  for (const f of input) {
    if (typeof f.confidence === 'number' && f.confidence < MIN_CONFIDENCE) { droppedByConfidence++; continue }
    const hit = exclusions.find((e) => e.re.test(`${f.title || ''} ${f.description || ''}`))
    if (hit) { droppedByExclusion[hit.id] = (droppedByExclusion[hit.id] || 0) + 1; continue }
    kept.push(f)
  }
  if (kept.length !== input.length) log(`filter (${stage}): ${input.length} → ${kept.length} finding(s)`)
  return kept
}

// ---------------------------------------------------------------------------
// Prompt helpers
// ---------------------------------------------------------------------------

function ctx() {
  return [
    `Target PR: #${PR} in the repo at ${ARGS.repoRoot} (origin). PR branch: ${ARGS.branch} — already checked out in the working tree.`,
    `Work ONLY inside the repo; use rg, never grep -r; do not read /tmp, ~, or sibling worktrees.`,
    `Before anything: git branch --show-current must return ${ARGS.branch} and git pull --ff-only must be at the PR head. Diverged → return blocked.`,
    `Read per-repo config from ${CONFIG} for build/test commands, doc paths, sensitive domains, and conventions — never assume a stack. If a section is absent, fall back to its stated default and say so.`,
    `Your final text is parsed as structured data — follow the schema, no extra prose.`,
  ].join('\n')
}

function contractNote() {
  const confirmed = ARGS.deepPlanContractOnBranch ? ' (the orchestrator CONFIRMED there is a contract on this branch)' : ''
  return `Plan-contract as input${confirmed}: if the branch carries a committed contract (the plan-contract glob from ${CONFIG} › Docs layout › Planning, e.g. \`.claude/deep-plan/*.md\` — check with git diff --name-only origin/main...HEAD), READ IT before analyzing the diff. The contract is the CURRENT spec, not the original plan's. A code↔contract divergence is a finding; a derived load-bearing value present in the code with no corresponding dimension row / premise in the contract is also a finding (High).`
}

function confidenceNote() {
  return `Every finding carries "confidence" (1–10, rubric in the schema): 10 = reproduced; 8 = mechanism traced in the code with file:line and no contrary guard; 6 = plausible, not traced; 5 or less = speculation. Below 8 the finding is DISCARDED in code before the judge — do not inflate the number, just don't report what you did not trace. If the finding violates a documented premise, fill "premise" with its ID (p-xxxxxxxx — the **Id:** right below the H2; it is in the premises index and in the header the fetch command prints). Only if you have nothing but the title, pass the exact H2 title — whoever uses the field resolves it with the fetch command from ${CONFIG} › Docs layout › Premise fetch command, with --id-of "<title>" in place of the id (default: python3 .github/scripts/premise.py --id-of "<title>").`
}

function dispositionsNote() {
  return `Disposition rule (also a review criterion): every case a diff handles carries one of four dispositions, visible in the diff — (1) inexpressible (the type/state model cannot represent it); (2) validated at the boundary (once, producing a typed error); (3) supported (with a test); (4) impossible (an assertion at the seam, no branch). A handled case with NO disposition — a defensive branch, a silent fallback, a broad catch with no disposition, an impossible case handled by a conditional instead of an assertion at the seam — is a finding (Medium; High when it masks a load-bearing value or state). Boundary complement: tolerate what an external provider may ADD (unknown fields), never what VIOLATES its spec (missing required field, wrong type, value out of range) — silent recovery entrenches the bug downstream.`
}

// ---------------------------------------------------------------------------
// Per-premise quality store: the posted comment ends with a `json review-findings`
// block (data, not prose — the miner crosses it with the commits' trailers) and the
// fix carries `Premises-Violated` with the violated ids. The ids come out of the JS,
// never out of the agent's opinion: this is counting, not judgement.
// ---------------------------------------------------------------------------

const PREMISE_ID_RE = /^p-[0-9a-f]{8}$/

function premiseIdsIn(findings) {
  const ids = []
  for (const f of findings || []) {
    const p = typeof f.premise === 'string' ? f.premise.trim() : ''
    if (PREMISE_ID_RE.test(p) && !ids.includes(p)) ids.push(p)
  }
  return ids.sort()
}

function premiseTitlesIn(findings) {
  const titles = []
  for (const f of findings || []) {
    const p = typeof f.premise === 'string' ? f.premise.trim() : ''
    if (p && !PREMISE_ID_RE.test(p) && !titles.includes(p)) titles.push(p)
  }
  return titles.sort()
}

function idOfCommand() {
  return `the premise fetch command from ${CONFIG} › Docs layout › Premise fetch command with --id-of "<title>" in place of the id (default: python3 .github/scripts/premise.py --id-of "<title>")`
}

function findingsBlockNote(what) {
  return `END the comment with a fenced \`\`\`json review-findings block — a JSON array with one {"id","severity","confidence","premise","file"} entry per ${what}, exactly the ones you listed above (\`premise\` = the p-xxxxxxxx id, or "" when the finding cites no premise; resolve a title with ${idOfCommand()}). It is data for the quality-signal miner, not prose: no comments inside the block, and never an entry that is not in the comment.`
}

function scopePrompt() {
  return `${ctx()}

You decide whether this diff solves ONE scoped problem. Collect: gh pr view ${PR} --json title,body,files,additions,deletions and git diff --stat origin/main...HEAD.

"scoped": true when every group of files is explained by the problem the title/summary state and the parts depend on each other. "scoped": false when the diff is a collage of two or more independent things (feature + unrelated refactor + drive-by fix; a summary whose items do not presuppose each other). SIZE IS NOT A CRITERION — a large diff in service of one problem is scoped. If false, propose the slices (title + files): the recommendation goes into the review comment, it blocks nothing.`
}

function standardReviewPrompt() {
  return `${ctx()}

Read ${ROLE_DIR}/standard-review.md and operate as that reviewer over the CURRENT head of the PR.

${confidenceNote()}

${dispositionsNote()}

${contractNote()}`
}

function deepReviewPrompt(a) {
  const isFlow = a.file === 'flow-lens.md'
  const roleLine = isFlow
    ? `Read ${DEEP_DIR}/flow-lens.md and operate as the "${a.name}" flow lens. Review the diff against what this flow doc says must hold:\n\n=== FLOW DOC: ${a.name} ===\n${a.doc || '(no doc text passed; apply the flow name to this diff)'}\n=== END FLOW DOC ===`
    : `Read ${DEEP_DIR}/${a.file} and operate as that specialist`
  return `${ctx()}

${roleLine}. If the role file does not exist on this branch, return immediately {"findings": []}.

Context you collect yourself (deep-review Phase 1): the diff via gh pr diff ${PR}; metadata via gh pr view ${PR} --json title,body,files; the core-tenets doc (${CONFIG} › Docs layout › Core tenets; default docs/CORE_TENETS.md); the schema doc of each affected domain (${CONFIG} › Docs layout › Schema); and the **premises INDEX** of each affected domain (${CONFIG} › Docs layout › Premises index; default docs/{domain}/premises-index.md) — open the body of a premise with the fetch command from ${CONFIG} › Docs layout › Premise fetch command (default \`python3 .github/scripts/premise.py <id>\`), NEVER the whole premises file: the Read tool truncates at 2000 lines and a domain's premises can exceed that. If the repo has no premises index, read the premises file itself and say so. Do NOT read the PR's existing comments/reviews — clean look. The diff is the source of truth for what is being proposed; read the full files for adjacent context. Verify before reporting (grep/read before claiming "X doesn't handle Y").

${confidenceNote()}

${dispositionsNote()}

${contractNote()}

Convert your findings to the findings schema (severities Blocker/High/Medium/Low per your role file; a defect in a Sensitive domain per ${CONFIG} › Sensitive domains is always Blocker — if none are configured, grade by ordinary functional impact).`
}

function judgePrompt(slices) {
  return `${ctx()}

Read ${DEEP_DIR}/consolidate.md and consolidate (dedup + classification) the findings of the ${slices.length} specialists below. Do NOT post anything to GitHub — return only the consolidated list in the schema.

You are the JUDGE, and you are the ONLY one: there is no validator and no verifier after you. For each Blocker/High, read the code and apply the refutation hunt from ${DEEP_DIR}/verify.md, in this order — a guard the finding missed · a recovery arm for any permanence claim · a declaration · reachability by a production writer · trigger realism. Verdict per Blocker/High: CONFIRMED (fold the sharpest evidence into the finding) · REPRICED (change "severity" and say why) · REFUTED (discard it and list it in "refuted" with the file:line that kills it, so the next reviewer does not re-mine it). Demotion requires evidence, never doubt; never soften the text of a finding that survives.

The findings arrive WITHOUT lens attribution — judge the finding, not who found it. Anything below confidence 8, and the classes listed under \`## Review exclusions\` in the repo's recurring-failure-modes doc (${CONFIG} › Docs layout › Planning), already dropped in code before reaching you.

Specialist findings:
${JSON.stringify(slices, null, 2)}`
}

function conciliatePrompt(findings, scope) {
  return `${ctx()}

Read ${ROLE_DIR}/conciliate.md and operate as that agent. SINGLE pass: this is the only review this loop posts.

Findings already judged (the judge refuted and re-priced what it could — do NOT re-verify Blocker/High):
${JSON.stringify(findings, null, 2)}

${scope && scope.scoped === false ? `SCOPE: the scope agent judged that this PR does not solve a single problem — "${scope.problem}" (${scope.reason}). Add a "Scope" section with the recommendation to slice it and the suggested slices: ${JSON.stringify(scope.suggestedSlices || [])}. It is a recommendation to the author, not a block.` : ''}

Lows are NOT listed — only counted, on the line "**Lows**: N (not listed)". Blocker, High and Medium all go in.

${findingsBlockNote('POSTED finding (the Blocker/High/Medium; Lows stay out, as in the body)')}`
}

function fixerPrompt(findings, opts) {
  const violated = premiseIdsIn(findings)
  const titles = premiseTitlesIn(findings)
  const trailer = violated.length ? `--trailer "Premises-Violated: ${violated.join(', ')}"` : ''
  const resolveNote = titles.length
    ? ` ${violated.length ? 'Besides those ids' : 'No finding cites the premise by id, but'} ${titles.length} finding(s) cite the premise by TITLE (${JSON.stringify(titles)}): resolve each with ${idOfCommand()} and add the id to the trailer's list, alphabetically (exit 1 = unresolved: leave it out).`
    : ''
  return `${ctx()}

Read ${ROLE_DIR}/fixer.md and operate as that agent${opts.second ? ' — this is the SECOND and last fix: the previous fix-review found Blocker/High' : ''}.

Address the Blocker/High/Medium below: implement, run the TARGETED verify (the incremental Verify command from ${CONFIG} › Verify plus the always-run gates listed there — the FULL build stays with the PR's CI, which is the authoritative gate), commit, push, update the PR description, and reply on the PR whenever you decide to diverge from a finding, with the why.
${trailer ? `
MANDATORY TRAILER — add to EVERY \`git commit\` of this fix, literally, this string (do not rewrite it, do not reorder it, do not invent ids):

    ${trailer}

It comes from the assigned findings, not from your assessment: these are the premises whose violation this fix corrects.${resolveNote} Return "violatedTrailer": true iff every commit you created carries the trailer.
` : titles.length ? `
MANDATORY TRAILER — the assigned findings do cite premises, but by title, so you resolve the id:${resolveNote} With the ids in hand, add \`--trailer "Premises-Violated: <ids separated by ', '>"\` to EVERY \`git commit\` of this fix and return "violatedTrailer": true; if no title resolves there is no trailer and "violatedTrailer" is false.
` : `
No assigned finding cites a premise, so there is NO \`Premises-Violated\` trailer to include: return "violatedTrailer": false.
`}
After you, ONE fix-review runs (on a model different from yours) over your diff${opts.second ? ', and nothing else' : ''}. There is no depth round: whatever is not addressed here is left to the CI review and the human reviewer.

Assigned findings:
${JSON.stringify(findings, null, 2)}`
}

function fixReviewPrompt(posted, commits, opts) {
  return `${ctx()}

You are the FIX-REVIEW${opts.second ? ' (second and last pass)' : ''} — the only reviewer of the fix, on a model different from the fixer's. CLOSED scope, two questions:

1. REGRESSION — review ONLY the code touched by the fixer's commits (${JSON.stringify(commits)}): git show <sha>, git diff <first>^..<last>. Look for what the fixes themselves introduced: a predicate / temporal window / formula with new semantics, a derived load-bearing value with no dimension row in the contract, a guard copied whose precondition does not hold for the new caller, a test weakened to pass. Do NOT re-review the whole PR.
2. UNADDRESSED — for each posted finding below: does the current head resolve its concrete scenario? If not, and the fixer posted no divergence with an argument, list it under "unaddressed" with the why.

${confidenceNote()}

${contractNote()}

Post a comment on the PR (gh pr comment ${PR} --body-file <tmpfile>) in the commit/PR language from ${CONFIG} › Conventions, with the marker <!-- review-fix-loop: fix-review --> and both lists; return the URL in "commentUrl". "verdict": "open" iff there is a Blocker/High in regressions or unaddressed.

${findingsBlockNote('item of "regressions" and of "unaddressed" (for an unaddressed item use its severity, confidence 8 and the "premise" of the original posted finding)')}

Findings posted in the review:
${JSON.stringify(posted, null, 2)}`
}

// ---------------------------------------------------------------------------
// Resilience: agent({schema}) THROWS when the subagent finishes without a StructuredOutput
// (throttling / retry cap), and a throw on a direct await takes down the WHOLE workflow even
// though earlier agents' work is already committed and pushed. Degrade to null: every call site
// below has null semantics.
// ---------------------------------------------------------------------------

let invocations = 0

async function tryAgent(prompt, opts) {
  invocations++
  try {
    return await agent(prompt, opts)
  } catch (e) {
    log(`agent ${opts?.label || '?'} crashed without structured output — degrading to null (${String((e && e.message) || e).slice(0, 140)})`)
    return null
  }
}

// ---------------------------------------------------------------------------
// Fixed structure: breadth → judge → conciliate → fix → fix-review (→ fix → fix-review, at most
// once). There is no round loop and no exhaustion criterion: the repo's CI review on each push and
// the human code owner are the next gates.
// ---------------------------------------------------------------------------

phase('Scope')
log(`tier=${TIER} roles=${JSON.stringify(ROLE)} exclusionsLoaded=${exclusions.length}`)
// Mode is DETERMINISTIC — no agent decides: a deep-plan contract on the branch, or a Sensitive
// domain in the diff (config › Sensitive domains; empty means this arm never fires).
const mode = ARGS.modeHint === 'deep' || ARGS.modeHint === 'standard'
  ? ARGS.modeHint
  : ((ARGS.deepPlanContractOnBranch || (ARGS.sensitiveDomains || []).length) ? 'deep' : 'standard')
log(`mode: ${mode} (contract=${!!ARGS.deepPlanContractOnBranch}, sensitiveDomains=${JSON.stringify(ARGS.sensitiveDomains || [])})`)

const scope = await tryAgent(scopePrompt(), { schema: SCOPE_RESULT, label: 'scope', phase: 'Scope', ...ROLE.scope })
if (scope && scope.scoped === false) {
  log(`PR NOT scoped — ${scope.problem}: the recommendation to slice it goes in the review comment (blocks nothing, changes no structure)`)
}

phase('Review')
const lensAgents = mode === 'deep' ? DEEP_AGENTS : DEEP_AGENTS.filter((a) => STANDARD_LENSES.includes(a.key))
const lensLabels = lensAgents.map((a) => a.key)
const lensThunks = lensAgents.map((a) => () => {
  invocations++
  return agent(deepReviewPrompt(a), { schema: FINDINGS, label: `lens:${a.key}`, phase: 'Review', ...ROLE[a.role] })
})
if (mode === 'standard') {
  lensLabels.unshift('standard')
  lensThunks.unshift(() => {
    invocations++
    return agent(standardReviewPrompt(), { schema: FINDINGS, label: 'lens:standard', phase: 'Review', ...ROLE.standard })
  })
}
const slices = await parallel(lensThunks)
// A crashed lens falls to null here (under throttling an agent can exceed the StructuredOutput
// retry cap). Breadth then ran DEGRADED — that is a warning in the result, never a clean state.
const lensFailures = lensLabels.filter((_, i) => !slices[i])
if (lensFailures.length) log(`breadth DEGRADED: ${lensFailures.length}/${lensLabels.length} lens(es) crashed — ${lensFailures.join(', ')}`)

const filteredSlices = slices.filter(Boolean).map((s) => ({ findings: applyHardFilters(s.findings, 'lens') }))
const cons = await tryAgent(judgePrompt(filteredSlices), { schema: FINDINGS, label: 'judge', phase: 'Review', ...ROLE.judge })
const refuted = cons?.refuted || []
const judged = applyHardFilters(cons?.findings ?? filteredSlices.flatMap((s) => s.findings), 'judge')
const severities = {
  blocker: judged.filter((f) => f.severity === 'Blocker').length,
  high: judged.filter((f) => f.severity === 'High').length,
  medium: judged.filter((f) => f.severity === 'Medium').length,
  low: judged.filter((f) => f.severity === 'Low').length,
}
log(`breadth: ${severities.blocker} Blocker · ${severities.high} High · ${severities.medium} Medium · ${severities.low} Low (counted, not posted) · ${refuted.length} refuted by the judge`)

phase('Conciliate')
const con = await tryAgent(conciliatePrompt(judged, scope), { schema: CONSOLIDATED, label: 'conciliate', phase: 'Conciliate', ...ROLE.conciliate })
const posted = con?.findings ?? judged
log(`review posted: ${con?.commentUrl || 'comment not confirmed'}`)

const state = { fixed: [], diverged: [] }
const allCommits = []
// The ids the JS DEMANDED in the trailer of a fix that actually ran (never of a blocked/crashed one),
// and the fixer's confirmation kept separately: demanded ≠ confirmed, and it is that pair that lets the
// miner see a promised trailer that never reached the git log. null = no fix ever owed a trailer.
const premisesViolated = []
let premisesTrailerConfirmed = null
const fixReviews = []
let finalStatus = 'done'
let blockedReason = null
let assigned = posted.filter((f) => f.severity !== 'Low')

for (let pass = 1; pass <= 2 && assigned.length; pass++) {
  phase('Fix')
  const suffix = pass === 2 ? '-2' : ''
  const demanded = premiseIdsIn(assigned)
  const fix = await tryAgent(fixerPrompt(assigned, { second: pass === 2 }), { schema: FIX_RESULT, label: `fix${suffix}`, phase: 'Fix', ...ROLE.fixer })
  if (!fix || fix.status === 'blocked') {
    finalStatus = 'blocked'
    blockedReason = fix?.blockedReason || 'fixer died with no result'
    break
  }
  state.fixed.push(...(fix.fixed || []))
  state.diverged.push(...(fix.diverged || []))
  allCommits.push(...(fix.commits || []))
  if (demanded.length) {
    for (const id of demanded) if (!premisesViolated.includes(id)) premisesViolated.push(id)
    premisesTrailerConfirmed = premisesTrailerConfirmed !== false && fix.violatedTrailer === true
  }
  log(`fix ${pass}: ${(fix.fixed || []).length} fixed, ${(fix.diverged || []).length} divergence(s), ${(fix.commits || []).length} commit(s), Premises-Violated demanded=${demanded.join(', ') || '—'} (fixer confirmed the trailer: ${fix.violatedTrailer === true})`)

  phase('Fix-review')
  const fr = await tryAgent(
    fixReviewPrompt(assigned, fix.commits || [], { second: pass === 2 }),
    { schema: FIX_REVIEW, label: `fix-review${suffix}`, phase: 'Fix-review', ...ROLE.fixReview }
  )
  if (!fr) {
    fixReviews.push({ pass, verdict: 'unknown' })
    log(`fix-review ${pass} crashed — the fix is still pushed; the CI review and the human reviewer are the gates`)
    assigned = []
    break
  }
  const regressions = applyHardFilters(fr.regressions, 'fix-review')
  const unaddressed = (fr.unaddressed || []).filter((u) => !state.diverged.some((d) => d.id === u.id))
  fixReviews.push({ pass, verdict: fr.verdict, regressions: regressions.length, unaddressed: unaddressed.length, commentUrl: fr.commentUrl })
  log(`fix-review ${pass}: ${fr.verdict} — ${regressions.length} regression(s), ${unaddressed.length} unaddressed`)

  // Blocker/High from the fix-review buys ONE second fix, and only one: after it the gates are the
  // repo's CI review on the push and the human code owner. Medium/Low from a fix-review never reopen.
  const stillOpen = regressions
    .filter((f) => f.severity === 'Blocker' || f.severity === 'High')
    .concat(unaddressed
      .filter((u) => u.severity === 'Blocker' || u.severity === 'High')
      .map((u) => assigned.find((f) => f.id === u.id) || { id: u.id, severity: u.severity, title: u.id, description: u.why, confidence: MIN_CONFIDENCE }))
  assigned = stillOpen
  if (pass === 2 && stillOpen.length) {
    log(`${stillOpen.length} Blocker/High still open after the 2nd fix-review — left to the CI review and the human reviewer`)
  }
}

// Acceptance rate: posted → addressed by a commit. A reading metric in the report, NEVER a stopping criterion.
const assignedIds = new Set(posted.filter((f) => f.severity !== 'Low').map((f) => f.id))
const fixedIds = new Set(state.fixed.map((f) => f.id).filter((id) => assignedIds.has(id)))
const acceptance = {
  assigned: assignedIds.size,
  fixed: fixedIds.size,
  diverged: state.diverged.length,
  rate: assignedIds.size ? Number((fixedIds.size / assignedIds.size).toFixed(2)) : null,
}

return {
  status: finalStatus,
  mode,
  tier: TIER,
  roles: ROLE,
  scope,
  invocations,
  severities,
  reviewCommentUrl: con?.commentUrl,
  exclusionsLoaded: exclusions.length,
  droppedByConfidence,
  droppedByExclusion,
  refuted,
  lensFailures, // non-empty ⇒ breadth ran degraded; say so in the report
  fixReviews,
  openHighBlocker: assigned,
  acceptance,
  premisesViolated, // ids DEMANDED in the Premises-Violated trailer of a fix that ran
  premisesTrailerConfirmed, // did the fixer confirm the trailer on every fix that owed one? null = none owed
  commits: allCommits,
  fixed: state.fixed,
  diverged: state.diverged,
  blockedReason,
}
