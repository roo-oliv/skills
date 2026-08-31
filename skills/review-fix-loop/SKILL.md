---
name: review-fix-loop
description: "Review→fix in clean context over an open PR, in a FIXED structure: breadth (a mirror of the standard code-review action + structural lenses, or deep-review's lens set when the diff is sensitive-domain / deep-planned) → single judge (confidence ≥ 8 + exclusions) → one consolidated review posted on the PR → fixer (commit+push, replies to divergences) → fix-review by a model different from the fixer, over the fix diff only. At most one second fix + fix-review if the fix-review finds Blocker/High. There are no rounds and no exhaustion criterion: the repo's CI review on each push and the human code owner are the next gates."
argument-hint: "[PR number | URL | empty = current branch's PR] [deep|standard]"
disable-model-invocation: true
---

`review-fix-loop` is step 3 of the `refine → implement → review-fix-loop` pipeline, but works standalone on any open PR. The whole cycle runs inside a **Workflow** (`.claude/workflows/review-fix-loop.js`) — this invocation is the user's explicit opt-in, including authorizing the agents to **commit/push on the PR's branch** and **post comments on the PR** via `gh`. Each agent starts with clean context: the reviewer never sees the implementer's reasoning, the fixer never sees the reviewer's reasoning beyond the published review.

This skill reads per-repo configuration from **`docs/agents/skills-config.md`** (schema: `skills/setup/skills-config.template.md`). It hardcodes nothing about stack, build commands, sensitive domains, doc paths, or commit conventions; when a section is absent it falls back to a stated default and says so in its output.

---

## Step 1 — Resolve arguments

- Token `deep` or `standard` → forces the mode (otherwise the workflow decides on its own, deterministically — see Contract).
- PR: number, `#N`, or URL → extract the number. Empty → `gh pr view --json number,headRefName,state` on the current branch. If there is no open PR, stop and say so.

## Step 2 — Preflight

1. `gh pr view <N> --json number,title,state,headRefName,baseRefName,isDraft` — the PR must be `OPEN`.
2. Check out the PR's branch (`gh pr checkout <N>`) with a clean working tree — the fixers work on it. If there are uncommitted local changes, ask first.
3. If the repo's **Verify** command runs containerized tests (config › Verify), confirm the runtime is up before the fixers start (e.g. `docker info`; a stopped daemon → suggest starting it).
4. Deterministic inputs for the workflow:
   - **Plan-contract on the branch?** `git diff --name-only origin/main...HEAD | rg '<plan-contract glob>'` — the glob comes from config › Docs layout › Planning (e.g. `.claude/deep-plan/`).
   - **Sensitive domains touched:** map each changed file to a domain via config › Domains, and intersect with config › Sensitive domains. Empty is a valid answer — then the mode is standard unless a contract is on the branch.
   - **Flows touched:** each flow doc under config › Flows whose frontmatter `covers:` globs intersect the changed files (a flow doc with no `covers` always counts). Pass them as `flows` — deep mode runs one flow lens per touched flow.
   - **Review exclusions:** extract the fenced `review-exclusions` JSON block from the repo's recurring-failure-modes doc (config › Docs layout › Planning), e.g.
     `awk '/^```json review-exclusions/{f=1;next}/^```/{f=0}f' <failure-modes doc>`. Absent file or block → pass `[]`.
   - **Models:** the `## Models` section of the config, if present, as `{ decision, worker, fixer, "fix-review", "pr-author" }` → `{ model, effort }`. Absent → omit; the workflow uses its own defaults.
5. `[ -z "${CLAUDE_CODE_SUBAGENT_MODEL:-}" ]` — if the variable exists, stop and say so: it is first in the model-resolution order and cancels the per-role tiering (`ROLE`) of every workflow, including reviewer ≠ fixer.

## Step 3 — Fire the workflow

Call `Workflow` with `scriptPath: .claude/workflows/review-fix-loop.js` and `args` (a real JSON object):

```json
{
  "prNumber": 123,
  "repoRoot": "<git rev-parse --show-toplevel>",
  "branch": "<headRefName>",
  "modeHint": "auto | deep | standard",
  "tier": "economy | full (default: full iff a plan-contract is on the branch)",
  "deepPlanContractOnBranch": true,
  "sensitiveDomains": ["payments", "billing"],
  "flows": [{ "name": "payment-pipeline", "doc": "<full text of the flow doc>" }],
  "exclusions": [{ "id": "nit-style", "pattern": "..." }],
  "models": { "decision": { "model": "opus", "effort": "high" } },
  "timestamp": "<date +%Y-%m-%dT%H-%M>"
}
```

Runs in the background; wait for the `<task-notification>`.

## Step 4 — Report

Read the result and lead with the outcome:

- `status: "done"` — the structure completed. Report the exact trajectory (`scope → breadth (N lenses) → judge → review posted → fix → fix-review [→ fix-2 → fix-review-2]`), `invocations`, the link to the posted review and to the fix-review comment, `severities`, `acceptance` (posted → addressed by a commit), `premisesViolated` + `premisesTrailerConfirmed` (ids demanded in the trailer of a fix that ran, and whether the fixer confirmed putting it on every commit — `false` means a trailer promised and possibly not delivered) and `openHighBlocker` — Blocker/High that survived the last fix-review **do not stop the loop**: say they were left to the CI review and the human reviewer.
- `status: "blocked"` — the fixer stalled; say where and what was already pushed.
- Always: `scope.scoped === false` (name the problem and the suggested slices — it is a recommendation to the author), `droppedByConfidence` / `droppedByExclusion` / `refuted` (what the predicate and the judge discarded and why), `exclusionsLoaded`, and a non-empty `lensFailures` (crashed lenses ⇒ breadth ran degraded — say so explicitly, never call it clean).

**CI check (whenever there was a push):** the fixer's targeted verify catches neither flake nor environment divergence. Run `gh pr checks <N> --watch` (or poll with a ~20 min timeout). If CI is red because of the pushes, run **one single** correction cycle in this session (surgical fix + targeted verify including the always-run gates from config › Verify + push) and re-check. If it stays red, report honestly with the failures — do not iterate indefinitely.

**Capture (whenever the review confirmed Blocker/High):** group the Blocker/High by root cause and reconcile each class with the repo's recurring-failure-modes doc (config › Docs layout › Planning) — `UPDATE` when an entry matches (add the PR to `**Mined from:**`, bump `**Occurrences:**`); `ADD` only when the class is mutually exclusive with every existing entry **and** will obviously recur (it is born `**Occurrences:** 1 · **State:** advisory`, format in the file's last section); otherwise `NOOP`. A class the fixer refuted with an accepted argument and the judge kept as false becomes an entry under `## Review exclusions` in the same file. Commit on the PR's branch, report `ADD/UPDATE/NOOP` per class, and point out — without implementing it in the loop — every entry now at `Occurrences ≥ 2` that is still `advisory`: that is a gate owed. If the config lists no planning path, say so and skip capture.

---

## Workflow contract (what `review-fix-loop.js` guarantees)

The structure is **fixed** and the loop **is not the gate**: the repo's CI review runs on every push and a human code owner reviews afterwards. Evidence for cutting the depth: in one production repo, rounds 2–3 found zero new Highs after a complete round 1; an audit of a 10-round run showed ~93% of post-round-1 Blocker/High were already visible to earlier passes, and the one extreme case was a large diff — solved by **scope**, not by rounds. Public measurements put 2 review rounds at 76–95% of what is findable, and [Anthropic's guidance](https://www.anthropic.com/engineering/claude-code-best-practices) warns that a reviewer told to find gaps usually reports something, and that a subagent should not be used to verify its own work.

- **Mode and tier are deterministic** (no agent decides): `deep` iff there is a plan-contract on the branch or the diff touches a Sensitive domain (config › Sensitive domains); `modeHint` forces it. `tier: full` iff there is a contract on the branch, or by explicit arg — it upgrades the worker lenses to the decision tier.
- **1. Breadth, once** — *deep*: the universal lenses (`adjacent-code`, `derived-quantity`, `negative-space`, `contract-reconciler`, `test-coverage`) + one `flow-lens` per touched flow doc. *standard*: the mirror of the standard code-review action (`agents/standard-review.md`) + negative-space + contract×code. All receive the branch's plan-contract as the current spec, and the affected domains' **premises index** with the per-id fetch command (config › Docs layout) — never the whole premises file, which the `Read` tool truncates at 2000 lines.
- **2. Single judge** — the consolidator is the judge: it receives the findings **without lens attribution**, applies the five refutation tests of `.claude/skills/deep-review/agents/verify.md` to each Blocker/High, and returns CONFIRMED / REPRICED / `refuted[]` with the file:line that kills the finding. There is no validator and no verifier after it — using a subagent to verify one's own work buys no coverage.
- **Posting predicate, in code** — `FINDING.confidence` (1–10) is mandatory; the JS discards `< 8` and anything matching the regexes under `## Review exclusions` in the recurring-failure-modes doc **before** the judge, and echoes `droppedByConfidence` / `droppedByExclusion`. Lows are counted, never posted. `FINDING.premise` (optional) names the violated premise **by id** `p-xxxxxxxx` (an exact H2 title is still accepted — the premise fetch command with `--id-of "<title>"` resolves it, exit 1 when absent or ambiguous) — the only utility signal a premise emits.
- **Scope, not size** — one agent decides whether the diff solves ONE problem; `scoped=false` adds a slicing recommendation (with the slices) to the comment, and **nothing else changes**: size is never a gate.
- **3. Conciliate and post** — one agent fuses the judged review with what is already on the PR (humans, bots), dedups, does not re-raise a finding contested with an argument, and posts the consolidated review (`gh pr comment` — the GitHub API rejects `gh pr review` on one's own PR), ended by a fenced ```json review-findings``` block (`id`, `severity`, `confidence`, `premise`, `file`) — the same block closes the fix-review comment. That block plus the `Premises-Violated` trailer the JS hands the fixer ready-made are the per-premise quality store, minable with `git log` after the squash ([`docs/telemetry.md`](../../docs/telemetry.md)).
- **4. Fix** — the fixer addresses the posted Blocker/High/Medium, runs the targeted verify (the full build stays with CI), commits, pushes, updates the PR description and replies on the PR when it diverges. A fix that changes a predicate, window, formula or the semantics of a load-bearing value updates the plan-contract and the premise in the same commit (strike, don't append).
- **5. Fix-review** — ONE agent, **on a model different from the fixer's**, scope closed to the fix diff: regressions introduced + posted findings not addressed. Posts on the PR. If it finds Blocker/High: one more fix + one more fix-review, **exactly once**. End.
- **Role × model × effort** — the `ROLE` table at the top of the workflow, over five tiers a repo may override in config › **Models**: `decision` (lenses that reason across domains, standard reviewer, judge at medium), `worker` (checklist lenses, scope, conciliator), `fixer`, `fix-review` (**a different model from the fixer** — model inversion: one model catches more bugs in another model's code), `pr-author`. Thinking is never disabled — effort is lowered instead; agents of the same phase share model/effort/schema so they share a cache prefix. Never set `CLAUDE_CODE_SUBAGENT_MODEL` (preflight aborts on it). The result echoes `tier`, `roles` and `invocations` (≈8 in deep with no flows, ≈4 in standard; +2 with the second pass).
- **Acceptance rate is a metric, not a stop** — `acceptance` (posted → addressed by a commit) goes into the result for reading and telemetry; no workflow decision depends on it.
- **Re-audit on model upgrade** — every constant here (which role gets which tier, the confidence floor, one fix-review pass) encodes what the models of its day did not do alone. On a CLI model upgrade, re-run a baseline set of PRs, compare cost/PR, acceptance and valid Highs, and update `meta.calibratedFor`.
