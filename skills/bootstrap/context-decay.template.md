# Context decay round — retiring surface that no longer describes anything alive

The context stock (`docs/`, `.claude/rules/`, `.claude/skills/`) only grows, and it rots in silence: nobody
notices when a `paths:` glob points at a directory that no longer exists, when a premise cites a deleted test, or
when a dated plan outlives the rollout it planned. This is the routine that closes the cycle: **the scan lists,
a human decides, one PR effects the round.** Nothing decays by date — age qualifies, it never names.

The charter for the surfaces themselves is `.claude/rules/context.md`.

## Who, when

- **Owner:** {the tech lead / doc owner}, in the first week of each month, on an up-to-date default branch.
- Also worth running when someone **closes a workstream**, restricted to that workstream's docs — that is the
  moment when what was distilled and what was left over is still known.
- Automating the cadence (a cron opening an issue) waits until two rounds have been run by hand: with nobody
  running it, a monthly issue is noise.

## How to run

```bash
python3 .github/scripts/context_decay.py --since 90
python3 .github/scripts/context_decay.py --since 90 --telemetry /tmp/telemetry.json
```

The scan is a **report, not a gate**: it always exits 0 and writes nothing. `--since` (default 90 days) is the
window of signal D4 — pick it from your own repo's age distribution: a window that qualifies roughly the oldest
third of the content is a quarter, or three rounds.

`--telemetry` is optional and takes the JSON usage report (`schema_version` 1 or 2). Without it the scan says
`telemetry: absent` and **does not evaluate D3**; with `schema_version: 1` it does not evaluate per-premise D3. A
surface absent from the report is "not covered", never a candidate. The generated premises index is **out of the
universe**: it is derived from the premises themselves, so it would be permanently `zero-load` and permanently
"unreachable". So is the intent dir — those artifacts live until their `Status:` says `implemented`, and the PR
that implements them is their review.

## The signals

| Signal | What it is | Source |
|---|---|---|
| D1 `superseded` | a `> **YYYY-MM-DD:** superseded — …` banner in the first 15 lines | the file itself |
| D2 `broken-ref` | a C6/C7/C8/C9/C13/C14 finding of `context-lint` on the file | the linter |
| D3 `unloaded` | the report marks the surface — or one premise H2 of a file that does load — `zero-load` | telemetry |
| D4 `dated` | an ephemeral doc whose effective date is older than the window, with no supersession banner | filename or banner |
| D5 `dead-scope` | a rule whose `paths:` glob matches 0 tracked files (all dead = the rule never loads) | frontmatter |
| D6 `unreachable` | a doc not reachable from the docs index by links or backticked paths | links |
| D7 `promotion-due` | a `recurring-failure-modes.md` entry with `Occurrences ≥ 2` still `advisory` | the entry's fields |

**C10 never enters the queue.** A backticked symbol that does not resolve with one of our own suffixes (`*Test`,
`*DTO`, `*Calculator`) is already a linter FAIL and never reaches the default branch; what is left in WARN is
framework types the identifier index cannot see, by design. C1–C5/C11/C12 are ceilings, not staleness.

The content age in the table is the median commit timestamp **weighted by added lines** — "last commit" measures
the last sweep, not the age of what is written. It is evidence for the decision; on its own it qualifies nobody.

## Deciding, per candidate

| Signal | Default action |
|---|---|
| D1 | delete — the facts are already at the destination the banner names |
| D2 | fix the reference, or delete the passage that no longer describes anything |
| D3 | check `paths:`/the index (it may be invisible, not dead); if it persists, delete |
| D4 | conclude it: distil the durable facts, add the banner, delete next round |
| D5 | remove the dead glob; if all of them died, the rule never loads — fix `paths:` or delete it |
| D6 | index it, or delete |
| D7 | a PR of its own shipping the gate (below) |

Hard rules, in this order:

1. **A doc that protects a live risk migrates, it does not decay.** The anchor moves to the code or the premise
   that carries the risk; only then does the dated doc go.
2. **Cited as evidence by a stable doc → keep.** The "cited by" column only counts live citers: ephemeral and
   generated docs sustain nobody.
3. **A dead reference gets deleted.** Git keeps it: `git log -S '<file name>' --oneline` returns the commit that
   removed it and the content. A deletion without that evidence in the PR does not happen.
4. **A concluded ephemeral doc distils first.** The durable facts become a premise, a tenet or a runbook; the doc
   gets a `superseded — <destination>` banner and disappears next round (or the same one, if the distillation
   already exists).
5. **A `kept — <risk>` banner pushes the effective date** by one window. It is how you say "I know it is old, the
   risk is still live".

## Banner grammar

```
> **2026-08-24:** superseded — docs/accounts/premises.md
> **2026-09-01:** kept — the migration it guards is still half-rolled-out
```

In the **first 15 lines**, one line, ISO date; the most recent date is the effective date. It is the **only**
edit allowed in a dated doc.

## The round's PR

A `chore(docs): decay YYYY-MM` PR with: the scan output **before and after** in the body; one line per decision
(delete / move / keep + why); the reference sweep for every removed path; the docs index without the lines of the
files that left; and `context_lint.py --base origin/<default branch>` green, with the WARN count before/after
(deleting a file that carried legacy warnings changes the summary line — that is expected, not a regression).

## Promotions due (the scan's second table)

An entry with `Occurrences ≥ 2` still `advisory` is a **gate owed**: its own PR delivering the gate with the red
case **executed**, flipping the state to ``deterministic (`Gate`)``. When no static gate can express the class,
the answer is `advisory (no gate: <why>)` — explicit, not silent.

## Outside the repository

An agent's automatic memory (per user, outside git) follows the same criteria — supersession, cited evidence,
"does it describe anything alive?" — but it is applied in-session by its owner, without a PR.
