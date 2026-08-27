---
description: Admission test, ceilings and anchor rules for every context surface — the instructions file, rules, skills, docs
paths: ["CLAUDE.md", "AGENTS.md", ".claude/rules/**", ".claude/skills/**", "docs/**/*.md"]
---

Before creating or extending any context surface, answer three questions; a "no" sends the content down the
hierarchy, not into the kernel:

1. **Would the model infer it from the code, or does CI already catch it?** → don't write it.
2. **Is it needed in more than ~30 % of sessions?** → if not, it is a `paths:`-scoped rule, a skill or a `docs/`
   page — never the instructions file (`CLAUDE.md`/`AGENTS.md`) or an always-on rule.
3. **Is it triggered by an action** (a command, a PR step)? → it is a skill or a hook, not a rule.

**Ceilings** — the numbers live here and in the `Ceilings` table of `docs/agents/skills-config.md`, enforced by
`.github/scripts/context_lint.py` in CI: instructions file ≤ 200 lines; always-on package (instructions +
`@imports` + rules without `paths:`) ≤ 32 KiB; rule ≤ 150 lines; new premise ≤ 40 lines / 4 KB of body (field
lines excluded) carrying `**Id:**`, `**Why:**`, `**Breaks:**`, `**Tests:**`. Crossing a ceiling fails the PR; a
file already over it only warns — and only when it grows.

**Form**, for the text you add or edit (legacy rationale is trimmed when you touch it, never as a drive-by):

- Rules are imperative and dry — the *why* lives in the premise or doc they point at. An inline example is
  ≤ 5 lines; longer, point at a real file.
- **Anchor by symbol, never by line number.** A line-number anchor is a violation by construction (C6): lines
  drift silently while the symbol survives. Every path, symbol, migration and link in backticks must resolve; a
  symbol that no longer exists is history — write it without backticks.
- Frontmatter carries `description` and, when the rule is scoped, `paths:` — nothing else. A key the harness does
  not read (`globs:`, `alwaysApply:`) is silently ignored, which makes the rule always-on by accident.
- Knowledge distilled from an incident cites its PR, issue or journal entry as **provenance**.

**Promotion by recurrence.** A failure class seen in ≥ 2 changes (`**Occurrences:**` in
`docs/planning/recurring-failure-modes.md`) is a **gate owed**: a PR of its own that ships the gate together with
its **executed red case** — the check commented out, the case failing, the check restored, the case passing —
and flips the entry to `deterministic (`Gate`)`. A gate nobody has seen red proves nothing; it may be structurally
empty and no one finds out. When no static gate can express the class, the answer is
`advisory (no gate: <why>)` — explicit, not silent. In a sensitive domain, a class with an unambiguous failure
signal promotes on the first occurrence.

**Decay.** A doc, premise or rule that describes nothing alive is deleted, not kept. The monthly scan that lists
the candidates is `python3 .github/scripts/context_decay.py --since 90`; the routine and the banner grammar are
in `docs/runbooks/context-decay.md`. Deletion needs the evidence in the PR (`git log -S '<name>'` returns the
commit that removed the thing the surface describes) — the git history is the archive, the surface is not.
