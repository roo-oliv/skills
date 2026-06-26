# skills

Claude Code skills I use daily as a software engineer — a planning → implementation → review
pipeline plus the docs scaffolding it leans on. They're meant to be **portable**: each skill
reads one per-repo config file instead of hardcoding a stack, so the same skill works on a
Kotlin/Spring backend, a TypeScript/Supabase app, and a C# game engine.

## The skills

**Pipeline (the core loop):**

| Skill | What it does |
|---|---|
| [`refine`](skills/refine) | Turn a raw request (text, a plan file, or a GitHub/Jira/Slack link) into an *approved* plan with a verifiable Contract block. Replaces interactive plan mode. |
| [`deep-plan`](skills/deep-plan) | Fill and adversarially refute a plan's contract (interaction matrix, dimension table, precondition diff) against the live codebase, before code exists. Engages for changes in sensitive domains. |
| [`implement`](skills/implement) | Implement an approved plan end-to-end into an open PR — wave-based, a fresh agent per wave plus a persistent ledger, verify + reconcile at the end. |
| [`review-fix-loop`](skills/review-fix-loop) | Review → fix loop over an open PR until exhaustion: review, reconcile with what's already posted, post a consolidated review, fix, repeat. |
| [`deep-review`](skills/deep-review) | Multi-agent review of a PR/branch/commit/local diff through a universal lens set plus the repo's own domain lenses. Used standalone or inside `review-fix-loop`. |

**Setup (run once per repo):**

| Skill | What it does |
|---|---|
| [`setup`](skills/setup) | Write `docs/agents/skills-config.md` — the file every other skill reads. Interviews you about stack, verify command, docs layout, domains, sensitive domains, lenses, conventions. |
| [`bootstrap`](skills/bootstrap) | Scaffold the docs the skills consume — `CORE_TENETS.md` and per-domain `premises.md` — with real content mined from the code and an interview. |

## How portability works

Nothing about a stack is hardcoded. Each skill reads **`docs/agents/skills-config.md`** in the
repo it's running in. That file declares the verify command, where premises live, which domains
are *sensitive* (and so get the heavy planning/review path), the repo's extra review lenses, and
the commit/PR conventions. Run `setup` once to write it; edit it by hand anytime.

So the first run on a new repo is:

```
/setup        # write the config
/bootstrap    # scaffold CORE_TENETS + premises (skip if the repo already has them)
```

then the pipeline (`/refine` → `/implement` → `/review-fix-loop`, or any skill on its own).

## Install

Vendored into the target repo's `.claude/` (skills + the workflow scripts they invoke + the
PR-gate hook):

```bash
scripts/install.sh /path/to/your/repo
```

Re-run to update. The pure-prose skills (`deep-review`, `refine`, `setup`, `bootstrap`) are
also listed in [`.claude-plugin/plugin.json`](.claude-plugin/plugin.json) for
`npx skills add roo-oliv/skills`; the workflow-backed ones (`deep-plan`, `implement`,
`review-fix-loop`) need the vendor copy so their `.js` resolves inside the repo.

## Repo layout

```
skills/<name>/SKILL.md      # the skills (some with agents/ role files + reference docs)
workflows/<name>.js         # deterministic multi-agent workflows the heavy skills invoke
hooks/                      # PR-create gate (blocks an incomplete plan-contract on a sensitive branch)
scripts/install.sh          # vendor skills/ + workflows/ + hooks/ into a target repo's .claude/
```

## Invocation

Two kinds, following the `disable-model-invocation` convention:

- **User-invoked** (`disable-model-invocation: true`): reachable only when you type the slash
  command. `setup`, `bootstrap`.
- **Model-invoked** (default): the model can reach for them automatically when a task fits, or
  you can invoke them. The pipeline + `deep-review`.
