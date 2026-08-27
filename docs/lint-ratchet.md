# The lint ratchet — gate the delta, never the absolute

Lint configuration only ever loosens. A threshold goes up because a function did not fit; a rule gets
switched off because a legacy file trips it; a baseline grows because the alternative was a bigger
diff. Each step is defensible on its own, and none of them was ever a decision anybody reviewed —
they rode along inside feature PRs.

Two of the defect classes most associated with agent-written code sit exactly there: **copying instead
of moving**, and **masking an error with a catch-all**. A rule that is off produces no signal at all.

The ratchet inverts the direction:

1. the rules for those classes are **on**;
2. the violations that already exist are **frozen** in a committed baseline, so nothing legacy blocks
   anyone and **the gate is green the day it lands**;
3. loosening anything — a threshold, or a baseline that grows — takes **its own change**, with the
   justification in the body. Otherwise entries only shrink.

Everything below is config-driven ([`skills/setup/skills-config.template.md`](../skills/setup/skills-config.template.md)
› `## Lint ratchet`) and **off until `Production globs` is filled in**.

## The two gates

[`ci/lint_ratchet.py`](../ci/lint_ratchet.py) runs in seconds, needs only `python3` and `git`, and is
deterministic.

### `config-rides-alone`

> **FAILS** when the change touches a **Lint config file** *or* grows a **Baseline file**, **and** also
> touches a file matching a **Production glob**.

- **Only the growing side counts.** A baseline that shrinks never fails — the ratchet turns one way.
- **Head is the worktree**, untracked files included, so it behaves the same before a commit and in CI.
  Base is `git merge-base $LINT_RATCHET_BASE_REF HEAD`; with no base ref (a push to the default branch)
  it prints a skip and exits 0. A base ref that was **passed** and does not resolve exits 1 — a gate
  that disarms itself in silence is the failure this file exists for.
- **Tests are not production**: changing a test file beside a threshold passes. What the gate protects
  against is the reading "the threshold moved because the new code did not fit".
- It **also blocks, on purpose**, the legitimate change that tightens a rule *and* fixes the code it
  flags. Those are two decisions, and mixing them is what hides the first. Split it — the fix first,
  the tightening after. There is no bypass label, because a label is the vector of the ride-along.

A baseline's size is counted with **Baseline entry pattern** (default `<ID>`, which is a detekt
baseline). For a one-entry-per-line format, `(?m)^\s*\S`.

When new code hits one of these rules, the ways out are, in order:

1. **fix it** — extract the constant, catch the specific exception, break up the function;
2. **suppress it in the diff, with the reason** — visible to the reviewer, and the baseline does not
   grow. This is the intended escape, and writing the reason is the point;
3. **grow the baseline** — its own change, which is what the policy exists to make expensive.

### `cpd-delta`

A threshold linter does not see a copied block: a duplicate-literal rule catches a repeated string, not
the 64 lines of constructor pasted from another file. A copy-paste detector does — CPD (from PMD) is
the worked example, run through **CPD command** and read from **CPD report** (PMD 6 and 7 report
formats both parse).

> An occurrence is **new** when **more than half** its lines were added relative to the merge base. A
> duplication with at least one new occurrence fails.

**There is no committed CPD baseline, on purpose.** A CPD baseline is per line, and any shift above the
block invalidates it — a file that only produces noise. **The diff is the baseline.**

The "more than half" rule is what separates the two cases that matter: fixing one line inside a legacy
duplicated block stays green (5 of 21 lines new), pasting the whole block goes red (64 of 64). A
**pure** `git mv` passes — `-M` does not count a move as new code. A rename that also gained content
does not: git reports it as `R60`/`R70`, the diff filter includes `R` on purpose, and the new lines
count. Otherwise a `git mv` in the same commit would hide the paste.

Known limit: CPD is exact per token, so a copy with renamed identifiers escapes. It catches the literal
copy, which is the agent failure mode that motivates the gate.

## Wiring it into CI

```yaml
      - name: Lint ratchet
        env:
          # Empty on a push to the default branch → the gate skips and passes.
          LINT_RATCHET_BASE_REF: ${{ github.event.pull_request.base.sha }}
        run: |
          python3 -m unittest discover -s .github/scripts -p 'lint_ratchet_test.py'
          python3 .github/scripts/lint_ratchet.py config-rides-alone
          ./gradlew cpd && python3 .github/scripts/lint_ratchet.py cpd-delta
```

Locally, exactly as CI runs it:

```bash
python3 .github/scripts/lint_ratchet.py config-rides-alone --base origin/main
./gradlew cpd && python3 .github/scripts/lint_ratchet.py cpd-delta --base origin/main
```

The red cases live in [`ci/lint_ratchet_test.py`](../ci/lint_ratchet_test.py), each over a throwaway
git repository: a loosened threshold plus production code, a baseline growing plus production code,
the same with an **untracked** production file, and a pasted block for CPD. The matching green cases
cover a shrinking baseline, a ratchet change travelling alone, a test-only change, a pure move and the
absent base ref. **A gate nobody has seen red proves nothing** — any new check of this family lands
with its red run pasted in the PR.

## Refactor ratio — a tripwire, never a KPI

Baselines and duplication gates catch defects per file. What they do not measure is the direction of
the whole repository: **code that only grows is code nobody is moving**, and an agent that copies
instead of moving pushes the add:delete ratio up without producing a single lint finding.

[`ci/refactor_ratio.py`](../ci/refactor_ratio.py) measures exactly that — lines added and removed under
the production globs, month by month, plus the same number restricted to commits whose subject starts
with `refactor`. If even those add more than they delete, the label is decoration. ~2x is the commonly
cited norm; a repo drifting into double digits is worth a conversation.

**This is not, and must not become, a KPI.** A ratio target is trivially gamed — rename the commit, or
delete dead code to inflate the denominator — and a deletion quota is worse than the disease. The
number exists to make someone *ask*. Which is why:

- the script **always exits 0** — no number breaks a build;
- the report goes where someone reads it: a monthly comment on an issue, plus the run's step summary
  ([`ci/workflows/refactor-ratio.yml.example`](../ci/workflows/refactor-ratio.yml.example)). A runbook
  is forgotten and a step summary alone is never opened;
- a failed comment (issue closed, locked, renumbered) becomes a note in the summary, not a red job.

The month is the **committer date**, `-M` keeps a pure move from counting as writing, and the current
month is marked "(partial)".

```bash
python3 .github/scripts/refactor_ratio.py                    # last 7 months
python3 .github/scripts/refactor_ratio.py --months 12
python3 .github/scripts/refactor_ratio.py --until 2026-08-16 # reproduce a past report
```

## Outside the JVM

Detekt + PMD/CPD is the example these scripts were measured on, not a requirement. The same three
pieces exist elsewhere and plug into the same config:

- **ESLint** — `--suppress-all` writes a bulk-suppressions file; point **Baseline files** at it and set
  **Baseline entry pattern** to whatever one entry looks like there.
- **Sonar** — *clean as you code* is the same idea at the platform level: new code is gated, existing
  code is not. If you already have it, `config-rides-alone` still adds the piece Sonar does not cover —
  that the *configuration itself* did not loosen inside a feature PR.
- **jscpd**, **PMD CPD** and most duplication detectors emit an XML report with the same
  file/line/endline shape; `cpd-delta` reads it with or without a namespace.

## What this does NOT cover

- **A coarse suppression signature.** Some tools key a baseline entry by class + parameter name rather
  than by occurrence, so one entry can cover several new violations in the same class. In files that
  already have an entry the gate is weaker than in a new file — human review stays the net; fixing the
  legacy entries restores the strength.
- **Source sets nobody lints.** A test-fixtures or generated source set with no lint task registered is
  invisible to all of this. That is a pre-existing gap, not something the ratchet closes.
- **Identifier-renamed copies**, per the CPD limit above.
- **Making any of it required.** Unless these checks are in branch protection, the ratchet is a strong,
  visible signal — not a mechanical block.
