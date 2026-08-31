---
name: setup
description: Configure this repo for the engineering skills — stack, verify command, docs layout, domains, sensitive domains, flows, and conventions. Run once before first use of deep-review / deep-plan / refine / implement / review-fix-loop.
disable-model-invocation: true
---

# setup

The engineering skills (`deep-review`, `deep-plan`, `refine`, `implement`, `review-fix-loop`)
adapt to a repo by reading one file: `docs/agents/skills-config.md`. This skill writes that
file by looking at the repo and asking you a few questions. Run it once per repo; edit the
file by hand afterwards whenever something changes.

This is prompt-driven, not a script. Explore → present what you found → confirm → write.

## 1. Explore

Read the repo's current state — don't assume:

- `git remote -v` — host and repo name.
- Build/test config: `build.gradle*`, `package.json`, `*.csproj`, `Cargo.toml`, `Makefile`,
  `.claude/scripts/`, CI workflows. What command actually formats, lints, builds, tests here?
- `CLAUDE.md` / `AGENTS.md` at the root — does either exist? Is there already an
  `## Agent skills` block?
- Docs: `docs/CORE_TENETS.md`, `docs/`, `.claude/rules/`, and any `premises.md` —
  centralized (`docs/{domain}/premises.md`) or colocated (`{module}/docs/premises.md`)?
- Top-level source layout — what are the natural domains/modules?
- An existing `docs/agents/skills-config.md` — if present, this is a re-run; update in place.

## 2. Present findings, then ask one section at a time

Summarise what's present and what's missing. Then walk the user through the config
**one section at a time** — propose a default from what you found, let them correct it, move
on. Don't dump every question at once. Assume the user may not know a term; lead each section
with a one-line explainer of what it controls and what changes if they pick differently.

The sections (full schema + per-repo examples in [skills-config.template.md](./skills-config.template.md)):

- **Stack** — language + build tool + framework. Infer from build files; just confirm.
- **Verify** — the format/lint/build/test command. This is the one the skills run before every
  commit and PR. Prefer a single entrypoint if the repo has one (`check-all.sh`, `gradlew …`).
  Ask for an incremental variant and any always-run gates (e.g. architecture tests) if they exist.
- **Docs layout** — where core-tenets and premises live, and the premises **pattern**
  (centralized vs colocated). Detect from what's on disk. Also ask for the **premises index**
  pattern and the **premise fetch command** (defaults `docs/{domain}/premises-index.md` and
  `python3 .github/scripts/premise.py <id>`): review and planning lenses are handed the index and
  open premise bodies by id, because the `Read` tool truncates a long premises file at 2000 lines.
  If neither exists yet, record the defaults and point the user at `bootstrap`, which scaffolds them.
  Under **Planning**, record the plan-contract spec, the plan-contract glob, and the
  recurring-failure-modes doc — the last one also carries the `review-exclusions` block the review
  loop reads.
- **Context toolkit** — optional; only ask if the repo vendors the `ci/` scripts (`context_lint.py`,
  `context_decay.py`, `premise.py`). Everything defaults, but two checks stay OFF until answered:
  **Source globs** (which file types vouch for a backticked symbol — never `*.sql` or `*.md`) and
  **Migration dirs**. Also ask whether the instructions file is `CLAUDE.md` or `AGENTS.md`, and leave the
  ceilings table at its defaults unless the user objects.
- **Intent** — the directory holding each unit of work's `intent.md` / `spec.md` / `plan.md`
  (default `intent/`). Keep it out of any published docs tree. `bootstrap` writes its README.
- **Telemetry** — ask **Lanes** first: `git-only` (the default — the commit trailers and the load log,
  no vendor, no endpoint), `otlp` or `both`. On `git-only`, record the **Default branch** the trailer
  miner reads and **ask nothing else in this section**: no endpoint, no key variable, and the installer
  writes no OTLP variables. Only on `otlp`/`both` ask for the rest; everything there defaults (endpoint
  `http://localhost:4318`, `http/protobuf`, variable `CLAUDE_CODE_OTEL_API_KEY`, header `dd-api-key`).
  What to explain then: the key comes from a GitHub Actions **variable**, not a secret, and with no key
  the session simply exports nothing.
- **Lint ratchet** — optional; only ask if the repo has a lint config and a suppression baseline worth
  freezing. **Production globs** is the one that arms both gates; without it they skip and pass.
- **Models** — optional per-role model/effort overrides (`decision`, `worker`, `fixer`,
  `fix-review`, `pr-author`). Only ask if the user wants to deviate; explain that `fix-review` must
  name a different model from `fixer`, and that `CLAUDE_CODE_SUBAGENT_MODEL` must never be set
  because it overrides the whole table.
- **Domains** — the bounded contexts and a path-glob → domain map, plus the optional **prompt terms**
  column (synonyms, other-language words, table names) the context hooks match against a prompt or a
  query. If the repo isn't partitioned, a single `default` row is fine.
- **Sensitive domains** — the subset where mistakes are expensive/irreversible (value movement,
  data loss, security, safety). This is the single most important answer: it decides when the heavy
  deep-plan/deep-review path and the PR gate fire. **Empty is a valid answer** — say so.
- **Flows** — where the repo's flow docs live (default `docs/flows/`); each flow doc becomes a
  dedicated review lens on top of the universal set. Optional; point the user at `bootstrap` to
  author the docs themselves. Just record the directory here.
- **Conventions** — commit/PR language, conventional commits, branch naming, pointers to the
  repo's git/test conventions, any commit trailer.

## 3. Confirm, then write

Show a draft of the filled `docs/agents/skills-config.md` and the `## Agent skills` block.
Let the user edit before writing. Then:

1. Write `docs/agents/skills-config.md` (create `docs/agents/` if needed) from the template,
   placeholders replaced, `# e.g.` comments removed.
2. Add (or update in place) an `## Agent skills` block in the existing `CLAUDE.md`, else
   `AGENTS.md`. If neither exists, ask which to create — don't pick. Never create one when the
   other is already there.

   ```markdown
   ## Agent skills

   The engineering skills (deep-review, deep-plan, refine, implement, review-fix-loop) read
   their per-repo configuration from `docs/agents/skills-config.md` — stack, verify command,
   docs layout, intent dir, models, domains, sensitive domains, flows, conventions. Edit that
   file to retune them.

   Every case a diff handles carries one of four dispositions, visible in the diff:
   **(1) inexpressible** — the type/state model cannot represent it; **(2) validated at the
   boundary** — once, producing a typed error; **(3) supported** — with a test;
   **(4) impossible** — an assertion at the seam, no branch. A handled case with no disposition
   is scope creep: delete it (trust framework guarantees and internal callers). Boundary
   complement: tolerate what an external provider may *add* (unknown fields), never what
   *violates* its spec (missing required field, wrong type, value out of range) — silent
   recovery entrenches the bug downstream; fail loudly at the seam.
   ```

   That second paragraph is deliberate: it is the rule the review lenses grade against (an
   undisposed handled case is a finding), so it belongs in the file every agent in the repo
   loads. If the user's conventions doc already states an equivalent rule, keep theirs and say so.

## 4. Done

Tell the user setup is complete and that the skills now read `docs/agents/skills-config.md`.
If the repo has no core-tenets doc, premises files, premises index or intent dir yet, point them at
the `bootstrap` skill to scaffold those — `deep-review`/`deep-plan` are far more useful once they
exist, and the review lenses read the index rather than whole premises files.
