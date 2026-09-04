# CLAUDE.md — skills repo

This repo holds portable Claude Code skills. It is the **source**; skills are vendored into a
consuming repo's `.claude/` via `scripts/install.sh`. When editing here, you are editing what
will run in other repos.

## The one rule that makes this repo work: nothing stack-specific is hardcoded

Every skill adapts to the repo it runs in by reading **`docs/agents/skills-config.md`** *in that
repo* (schema: `skills/setup/skills-config.template.md`). A skill must never assume:

- a build/test command (`./gradlew …`, `npm test`) → read **Verify** from config;
- where premises/tenets live (`docs/{domain}/premises.md`) → read **Docs layout**;
- which domains are sensitive (value movement, safety, etc.) → read **Sensitive domains**;
- a domain vocabulary, or which dedicated review lens to run → read **Domains** / **Flows**;
- a commit/PR language or convention → read **Conventions**.

When a skill needs one of these, it reads the config; if the section is absent, it falls back to
a stated default and **says so in its output**. A hardcoded stack assumption is a bug — the same
file runs on a Kotlin backend, a TS app, and a C# game engine.

## Authoring conventions

- **One skill per directory** under `skills/`, with a `SKILL.md`. Reference material and agent
  role files live beside it (`agents/*.md`, `*.md`) and are reached by the skill, not linked
  across skill folders.
- **Frontmatter:** `name`, `description`. Add `disable-model-invocation: true` for user-invoked
  skills (their description is human-facing — a one-line summary, no trigger phrasing).
  Model-invoked skills keep trigger phrasing in the description so auto-invocation fires.
- **Steps end in a checkable completion criterion.** Push long reference behind a pointer the
  step fires ("read `agents/refuter.md`") rather than inlining it — keep `SKILL.md` legible.
- **Voice:** plain, specific, not self-congratulatory. Describe what the skill does and the
  failure it prevents; skip adjectives. Skill prose is in **English**; the *output* language
  (commits, PR bodies) is whatever the consuming repo's config says.
- **Workflows** (`workflows/*.js`) are plain JS run by the Workflow tool — they begin with
  `export const meta = {…}` and use top-level `await`/`return`. They receive `repoRoot` in
  `args` and resolve role files as `${ARGS.repoRoot}/.claude/skills/<name>/agents/…`. Validate a
  workflow edit by wrapping it in an async fn and running `node --check` (top-level `return`
  isn't valid bare).
- **`workflows/tests/*.test.mjs`** do that wrapping and then drive the script's test-only `seed*`
  `args` paths, so the assertions exercise the SHIPPED pure functions, not a copy — and a parse
  error fails the suite, which is the `node --check` above.
  `node workflows/tests/deep-plan-engine.test.mjs` must be green before a workflow commit. Not
  vendored: the installer globs `workflows/*.js`.
- **Hooks / scripts** are POSIX-ish bash; validate with `bash -n`.
- **`ci/*.py`** — Python 3 **stdlib only, ≥ 3.9**, 120 columns, and every path/glob/threshold read from
  `docs/agents/skills-config.md` via `ci/skills_config.py`, never a constant. Each script has a
  `<name>_test.py` beside it whose red case was **executed** before the commit (a gate nobody has seen red
  proves nothing), and the whole suite must be green here:
  `python3 -m unittest discover -s ci -p '*_test.py'`. The installer vendors the tests too, so the same
  command runs in the consuming repo's CI. A script that needs configuration it does not have **skips and
  passes with a printed reason** — it never guesses a default that could fail a build.
- **`hooks/*`** — POSIX-ish bash or stdlib Python, fail-open by contract: never write to stdout unless the
  hook contract asks for it, always exit 0, always carry a `timeout`.
- **`rules/*.md`** — vendored to the consumer's `.claude/rules/`. Frontmatter carries **only**
  `description` and (optionally) `paths`; no `paths:` means always-on, which is a budget decision, not a
  default. Imperative and dry — the *why* belongs in the doc it points at.
- **`settings/*.json`** — fragments, not files to copy: `install.sh` merges them key by key into the
  consumer's `.claude/settings.json`, and the consumer's own value always wins. Keep each fragment to one
  concern (`hooks.json`, `env.json`) and never write a key the consumer would want to own without knowing.
- What each toolkit does and why: [`docs/context-toolkit.md`](docs/context-toolkit.md),
  [`docs/telemetry.md`](docs/telemetry.md), [`docs/lint-ratchet.md`](docs/lint-ratchet.md). Why every
  workflow constant and per-role model is what it is — plus the audit log a model upgrade appends to:
  [`docs/workflow-calibration.md`](docs/workflow-calibration.md).

## When you add or rename a skill

- Add its directory to `.claude-plugin/plugin.json` if it's a pure-prose skill (no `.js`).
- If it invokes a workflow, the workflow goes in `workflows/` and the SKILL.md calls
  `Workflow({ scriptPath: ".claude/workflows/<name>.js", args: { repoRoot, … } })`.
- Make sure `scripts/install.sh` still copies it (it globs `skills/*/`, `workflows/*.js`, `hooks/*`,
  `rules/*.md`, `ci/*.py`, and merges `settings/*.json`). A brand-new **top-level directory** needs a new
  loop there.
- Update the README tables — the skill list, the toolkit table and the repo layout block.
