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
- **Hooks / scripts** are POSIX-ish bash; validate with `bash -n`.
- **The context toolkit** (`ci/*.py`, `hooks/*`, `rules/*.md`, `settings/hooks.json`) is Python 3 stdlib only,
  ≥ 3.9, and reads the same `docs/agents/skills-config.md` the skills do — via `ci/skills_config.py`, never a
  constant. Every check has a red case in its `*_test.py`, and the suite must be green here:
  `python3 -m unittest discover -s ci -p '*_test.py'`. What each piece does: [`docs/context-toolkit.md`](docs/context-toolkit.md).

## When you add or rename a skill

- Add its directory to `.claude-plugin/plugin.json` if it's a pure-prose skill (no `.js`).
- If it invokes a workflow, the workflow goes in `workflows/` and the SKILL.md calls
  `Workflow({ scriptPath: ".claude/workflows/<name>.js", args: { repoRoot, … } })`.
- Make sure `scripts/install.sh` still copies it (it globs `skills/`, `workflows/`, `hooks/`).
- Update this file's skill list and the README table.
