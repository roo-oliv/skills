# Agent skills config

Per-repo configuration for the engineering skills (`deep-review`, `deep-plan`, `refine`,
`implement`, `review-fix-loop`). **The skills read this file to adapt to this repo — they
hardcode nothing about stack, paths, domains, or conventions.** The `setup` skill writes it;
edit it by hand anytime. If a section is missing, the skill that needs it falls back to the
default noted here and says so in its output.

> This is a template. Replace the `<…>` placeholders. The `# e.g.` comments show how three
> real repos fill each section (a Kotlin/Spring backend, a TS/Deno Supabase app, a C# game
> engine) — delete them once yours is filled.

## Stack

`<language + build tool + framework>`
<!-- e.g. backend: Kotlin + Gradle (JDK 21), Spring Boot. autopilot: TypeScript + Deno, Supabase. monodreams: C# + .NET, MonoGame. -->

Used only for idioms (test style, file naming) — never as a hard gate.

## Verify

The command sequence the skills run to format, lint, build, and test before committing or
opening a PR. A non-zero exit is a failure the skill must fix before proceeding.

- **Full:** `<command>`
  <!-- e.g. backend: ./gradlew spotlessApply detekt clean build  |  autopilot: bash .claude/scripts/check-all.sh  |  monodreams: dotnet test -->
- **Incremental** (optional — a faster, scoped variant for per-wave checks): `<command>`
  <!-- e.g. backend: ./gradlew spotlessApply detekt :<module>:test --tests "<changed>*" -->
- **Always-run gates** (optional — cheap checks the skills append every verify): `<command>`
  <!-- e.g. backend: ./gradlew test --tests "*ArchitectureTest" (ParallelSafety / TransactionalEventListener / MockBean gates) -->

## Docs layout

Where the docs the skills read and produce live. Use `{domain}` / `{module}` as placeholders
the skill substitutes per change.

- **Core tenets:** `<path>`  <!-- e.g. docs/CORE_TENETS.md (business/architectural invariants) -->
- **Premises:** `<pattern>`
  <!-- e.g. backend: docs/{domain}/premises.md | monodreams: {module}/docs/premises.md (colocated) | autopilot: .claude/rules/premises.md + docs/ -->
- **Premises index** (optional but recommended): `<pattern>`  <!-- default: docs/{domain}/premises-index.md -->
- **Premise fetch command** (optional): `<command with a <id> placeholder>`
  <!-- default: python3 .github/scripts/premise.py <id> -->
- **Schema** (optional): `<pattern>`  <!-- e.g. backend: docs/schema/{domain}.md -->
- **Planning** (optional): plan-contract spec `<path>`; recurring-failure-modes `<path>`; plan-contract glob `<glob>`
  <!-- e.g. backend: docs/planning/plan-contract.md, docs/planning/recurring-failure-modes.md, .claude/deep-plan/*.md -->
- **Rules dir** (optional): `<path>`  <!-- e.g. .claude/rules/ — glob-scoped convention files the skills should honor -->

**Why the index matters.** Review and planning lenses are given the domain's *premises index* — one
line per invariant, each with a stable id — and open only the bodies they need, by id, with the fetch
command. They are never handed the whole premises file: the `Read` tool truncates at 2000 lines, so a
large domain silently loses its tail. With no index configured, the skills read the premises file and
say so in their output. (The `bootstrap` skill scaffolds the index and the fetch script.)

**The recurring-failure-modes doc carries two things**: the `FM-N` entries `/deep-plan` must answer,
and a fenced ```` ```json review-exclusions ```` block at the end listing finding classes the review
must never post (`{ id, pattern, precedent, why }`, matched case-insensitively against a finding's
title+description). `/review-fix-loop` extracts that block in preflight and drops matching findings in
code, before any judge sees them.

## Intent

Where the pipeline's planning artifacts live. Following the
[AI-native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook), each unit of work gets
a folder whose files are committed beside the code they produced — "every stage commits an artifact the
next stage can read":

```
<intent dir>/<slug>/
├── intent.md   # the problem and desired outcome, in the originator's words
├── spec.md     # requirements and design, with this repo's policies applied
└── plan.md     # the implementation plan — files, order, risks, proof — and the Contract block
```

- **Intent dir:** `<dir>`  <!-- default: intent/ ; keep it OUT of a published docs/ tree -->

Each file carries `Status: draft | approved | implemented` in its header. `refine` writes all three and
stamps `approved`; `implement` reads `plan.md` and stamps `implemented`; `verify-plan` reconciles the
diff against `plan.md`'s Contract block. `bootstrap` scaffolds `<intent dir>/README.md` with the
convention. If this section is absent, the skills fall back to a gitignored `.claude/.plans/<slug>.md`
and say so.

## Models

Optional. The workflows tier every subagent by role: a **decider** (judges code or a load-bearing
quantity, or writes what ships) runs on a strong model at high effort; a **worker** (collects evidence
under a checklist) on a cheaper model at medium. Thinking is never disabled — effort is lowered
instead. Override any row here; anything you omit keeps the workflow's default.

| Role | Model | Effort | Default |
|---|---|---|---|
| `decision` | `<model>` | `<high\|medium\|low>` | `opus` / `high` |
| `worker` | `<model>` | `<...>` | `sonnet` / `medium` |
| `fixer` | `<model>` | `<...>` | `opus` / `high` |
| `fix-review` | `<model>` | `<...>` | `sonnet` / `high` |
| `pr-author` | `<model>` | `<...>` | `sonnet` / `medium` |

**`fix-review` must name a different model from `fixer`** — model inversion is the point: one model
catches more bugs in another model's code than in its own. And **never set
`CLAUDE_CODE_SUBAGENT_MODEL`** in your environment: it is first in the model-resolution order, so it
overrides every row above and collapses the tiering (including reviewer ≠ fixer) into one model. The
`review-fix-loop` preflight aborts if it finds the variable set.

## Domains

The bounded contexts / domains of this repo, and how to detect which one a changed file
belongs to (path globs → domain). Skills use this to load the right premises and lenses.
If you don't partition by domain, write a single `default` row matching everything.

| Domain | Detect (path globs) |
|---|---|
| `<name>` | `<glob>`, `<glob>` |
<!-- e.g. backend: billing | **/billing/** ;  monodreams: rendering | MonoDreams/*/Draw/**, MonoDreams/Renderer/** -->

## Sensitive domains

Subset of the domains above where a mistake is expensive or irreversible — value movement,
data loss, security, safety-critical correctness. A change touching ANY of these triggers
the **heavy path** in `deep-plan` and `deep-review` (full lens fan-out + adversarial refute
+ the PR-create gate). **May be empty** — then every change takes the light path and the
gate never blocks.

`<domain>, <domain>`
<!-- e.g. backend: payments, credits, billing, ledger, disbursement | monodreams: (none — or physics, collision if you treat correctness as load-bearing) -->

## Flows

`deep-review` / `deep-plan` always run a **universal** lens set, stack-agnostic by design:
adjacent-code (downstream callers a change forgets), derived-quantity (every computed value's
base/unit/cap), negative-space (unhandled states/scope), contract×code (code vs the plan/premises
it claims to satisfy), test-coverage (premises no test protects).

On top of those, the review spawns **one dedicated lens per *flow* this repo declares**. A flow
is a path that data, state or value takes through the system that must be reasoned about as a whole
— a payment pipeline, a level-load sequence, an auth handshake. You document each one as a
markdown file that reads like a **dedicated core-tenet for that flow**: descriptive (not review
instructions), but carrying everything a reviewer or planner needs — the path, the entities and
their lifecycle, the invariants, the load-bearing quantities, the failure modes. The flow lens
turns that doc into review questions against the diff. This is how the skills get repo-specific
without anything being hardcoded: a repo with no financial flows simply declares none.

- **Flows dir:** `<dir>`  <!-- default: docs/flows/ ; one `<flow>.md` per flow -->

Author flow docs with the `bootstrap` skill (or by hand) using the format in
[bootstrap/flow.template.md](../bootstrap/flow.template.md). Each doc's frontmatter `covers:`
globs decide which flows a given change touches — only those flows' lenses run. No flows dir, or
no flow docs → only the universal lenses run.
<!-- e.g. backend: docs/flows/{payment-pipeline,settlement,refund-lifecycle,disbursement}.md
     monodreams: docs/flows/{level-load,collision-resolution,render-pass}.md
     a repo with no load-bearing flows: none — the universal lenses are enough. -->

(There is no separate "sensitive lens" list — sensitivity is the **Sensitive domains** axis above,
which decides heavy-vs-light + the gate; flows decide *which dedicated lenses* run.)

## Conventions

- **Commit/PR language:** `<pt-br | en | …>`  <!-- the language the skills WRITE commits and PR bodies in -->
- **Conventional commits:** `<yes | no>`  <!-- type(scope): description -->
- **Branch naming:** `<pattern>`  <!-- e.g. kebab-case, type/short-slug -->
- **PR body:** `<inline requirements, or a pointer>`  <!-- e.g. "see .claude/rules/git-conventions.md" — required sections, payload samples, rollback -->
- **Test conventions:** `<pointer>`  <!-- e.g. "see .claude/rules/testing.md" — assertion quality, isolation, e2e>integration>unit -->
- **Commit trailer** (optional): `<trailer lines the skills append to commits>`
