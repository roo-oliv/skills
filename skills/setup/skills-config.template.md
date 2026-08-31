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
- **Premises index write command** (optional): `<command>`
  <!-- default: python3 .github/scripts/context_lint.py --write-indices — named in every generated index header -->
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

## Context toolkit

Optional — read by the vendored scripts under `.github/scripts/` (`context_lint.py`, `context_decay.py`,
`premise.py`) and by the context hooks. Every value below has a working default, and the two **stack-specific
checks are OFF until you fill them in**: no `Source globs` means the symbol check (C10) never runs, no
`Migration dirs` means the migration check (C9) never runs. Full check table: the context-toolkit doc of the
skills repo (docs/context-toolkit.md there — it is not a file of this repo).

- **Agent instructions file:** `<path>`  <!-- default: CLAUDE.md; AGENTS.md if that is what your harness reads -->
- **Skills dir:** `<path>`  <!-- default: .claude/skills/ -->
- **Docs root:** `<path>`  <!-- default: docs/ -->
- **Docs index:** `<path>`  <!-- default: docs/index.md — the root of the reachability walk (decay signal D6) -->
- **Ephemeral docs:** `<glob>, <glob>`
  <!-- default: docs/work/** — dated, never updated; not a lint surface, and the only input to decay signal D4 -->
- **Never a surface:** `<glob>, <glob>`  <!-- default: .claude/deep-plan/**, **/eval/** -->
- **Source globs:** `<glob>, <glob>`
  <!-- the files whose PascalCase names vouch for a backticked symbol. e.g. *.kt, *.kts, *.yml, *.js | *.ts, *.tsx
       | *.cs. Leave empty to keep C10 off. NEVER include *.sql or *.md: a commented-out statement or another doc
       would vouch for a symbol the code no longer has. -->
- **Migration dirs:** `<dir>, <dir>`  <!-- e.g. db/migration, db/data. Empty keeps C9 off. -->
- **Migration version pattern** (optional): `<regex>`  <!-- default: \bV[0-9]{2,3}(?:__[a-z0-9_]+\.[a-z]+)?\b -->
- **Path resolution roots** (optional): `<dir>, <dir>`
  <!-- extra prefixes a backticked path may be relative to, e.g. src/main/resources — before this list a path
       resolves from the repo root, from the citing doc's folder, or by unique suffix -->

| Ceiling | Value |
|---|---|
| agent instructions lines | 200 |
| always on bytes | 32768 |
| rule lines | 150 |
| premise lines | 40 |
| premise bytes | 4096 |
| premise warn lines | 25 |

## Telemetry

Two independent lanes, and **lane A is the default** because it needs no vendor at all:

- **lane A — git-only:** the commit gate writes `Premises-Read` / `Premises-Violated` / `Agent-Session`
  trailers into every commit made in an agent session, and `agent_telemetry.py commits` mines them out of
  the branch log and the open PRs. It answers *was this premise used, and violated anyway* across every
  person and machine, with nothing but `git` and `gh`. It does **not** measure tokens or dollars.
- **lane B — otlp:** the `env` block `scripts/install.sh` merges into `.claude/settings.json` plus
  `otel_headers.py`, exporting cost and token counts per model / skill / agent to an OTLP intake. It does
  **not** know anything about premises.

Full setup, attributes and example queries: the telemetry doc of the skills repo (docs/telemetry.md there
— it is not a file of this repo).

- **Lanes:** `<git-only | otlp | both>`
  <!-- default: git-only. A section that names an Endpoint or a Key variable but no Lanes counts as
       `both`, so a repo configured before this field existed keeps its export. `git-only` writes NO OTLP
       variables into .claude/settings.json. -->
- **Default branch:** `<branch>`
  <!-- default: main — the branch `agent_telemetry.py commits` mines (it falls back to origin/<branch>) -->

The rows below matter only on `otlp`/`both`. **One vendor is the single example measured**, never a
default: any OTLP intake works.

- **Endpoint:** `<url>`  <!-- default: http://localhost:4318 (a collector or vendor agent on the machine) -->
- **Protocol:** `<http/protobuf | http/json>`
  <!-- default: http/protobuf. gRPC cannot carry the dynamic auth header the helper prints. -->
- **Resource attributes** (optional): `<k=v,k=v>`  <!-- default: repo=<the repo directory name> -->
- **Key variable:** `<NAME>`
  <!-- default: CLAUDE_CODE_OTEL_API_KEY — a GitHub Actions *variable*, not a secret: a secret is
       write-only and no `gh` command reads its value back. Use an intake-only key. -->
- **Key header:** `<header>`  <!-- default: dd-api-key; `Authorization` for a bearer intake -->
- **Key repo** (optional): `<owner/name>`  <!-- default: whatever `gh` infers from the checkout -->

## Lint ratchet

Optional — read by `lint_ratchet.py` and `refactor_ratio.py`. **Both gates are OFF until `Production
globs` is filled in.** The idea (gate the delta, never the absolute; the baseline freezes the legacy;
loosening a threshold is its own PR) is in the lint-ratchet doc of the skills repo. Detekt + PMD/CPD is
the worked example; ESLint bulk suppressions and Sonar's clean-as-you-code are the same shape.

- **Production globs:** `<glob>, <glob>`
  <!-- e.g. */src/main/kotlin/*.kt | src/*.ts | */Assets/Scripts/*.cs. `*` crosses `/` (fnmatch, and
       git's own pathspec matching), so the trailing *.kt already covers every subpackage. Never `**`. -->
- **Lint config files** (optional): `<path>, <path>`
  <!-- the files whose thresholds may not loosen in a feature PR. e.g. config/detekt/detekt.yml -->
- **Baseline files** (optional): `<glob>, <glob>`
  <!-- the committed suppression baselines. e.g. config/detekt/baseline-*.xml -->
- **Baseline entry pattern** (optional): `<regex>`
  <!-- default: <ID> (a detekt baseline). One entry per line instead? Use (?m)^\s*\S -->
- **CPD command** (optional): `<command>`  <!-- e.g. ./gradlew cpd — named in the error when the report is missing -->
- **CPD report** (optional): `<path>`  <!-- default: build/reports/cpd/cpd.xml (PMD 6 or 7) -->

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

| Domain | Detect (path globs) | Prompt terms (optional) |
|---|---|---|
| `<name>` | `<glob>`, `<glob>` | `<word>`, `<word>` |
<!-- e.g. backend: billing | **/billing/** | invoices, dunning ;  monodreams: rendering | MonoDreams/*/Draw/** | -->

**Prompt terms** are the extra words that name the domain in a prompt or a query — a synonym, a term in another
language, a table name. The context hooks use them to put the domain's premises index on the *reasoning* path
(a prompt naming the domain, a SQL query naming one of its tables), which `paths:` rules cannot reach because
they only fire when a file is read or edited. A sensitive domain also fires on its own bare name; a
non-sensitive one only on an explicit term. Leave the column empty to opt out.

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
