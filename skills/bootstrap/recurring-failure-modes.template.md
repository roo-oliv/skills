# Recurring failure modes

Failure **classes** that shipped in this repo and were caught only in review — each with the artifact a future
plan must produce to answer it. `/deep-plan` reads this file in phase 1: every entry whose **Trigger** matches
the change must get the artifact response it names, or it stands as an unresolved GAP. `/review-fix-loop` and
`/deep-review` write to it — they promote a recurring finding class into an entry here (and a premise in the
owning domain), which is what closes the loop: a bug caught once becomes a question every future plan answers.

This file is an evidence ledger, not a wish list. An entry earns its place by having happened, with the change
that proves it.

## Adding or updating an entry

Every entry is `## XX-N — title` (`FM-N` here) followed by field lines, checked by `context-lint` C16:

- `**Mined from:**` — the PRs/incidents the class was observed in (`#NNN`); the provenance ledger.
- ``**Occurrences:** N · **State:** advisory | advisory (no gate: why) | deterministic (`GateClass`)`` — `N` counts
  distinct changes (a PR pair fixing one case counts once) and never exceeds the refs in Mined from;
  `deterministic` names the test or lint that turns the class red in CI, and **that gate must exist**.
- `**Trigger:**` (concrete enough for a mechanical match / no-match), `**The failure:**`, `**Artifact response:**`
  (+ `**Executable premise:**` when one applies).

Capture is a step of `/review-fix-loop` (and an option of `/deep-review`'s final phase): `UPDATE` the entry that
matches (append the change, bump N), `ADD` only a class that is mutually exclusive with every existing entry and
will obviously recur, `NOOP` otherwise. `Occurrences ≥ 2` still `advisory` is a **gate owed**
(`.claude/rules/context.md`): a PR of its own ships the gate with its executed red case, flips the state to
`deterministic`, and — when the gate covers the whole `The failure` — cuts the prose to a pointer at the premise
that now holds it. In a sensitive domain, a class with an unambiguous failure signal promotes on the first
occurrence.

The shape of an entry — copy it when the first class earns its place (an entry with a placeholder provenance
fails C16, so this stays fenced until it is real):

```markdown
## FM-1 — short name of the class

**Mined from:** #123
**Occurrences:** 1 · **State:** advisory
**Trigger:** the concrete condition a planner can match or rule out — "the change adds a status", not "risky change"
**The failure:** what went wrong, mechanically
**Artifact response:** the plan artifact that would have caught it — an interaction-matrix column, a dimension-table
row, a precondition diff for every caller, …
**Executable premise:** the require/check seam or test that would make it impossible — or omit this field
```

<!-- Entries go below this line, newest last. -->


## Review exclusions — classes already rejected

Finding classes the review must **never** post. The regex (case-insensitive) is matched against a finding's
`title + description` and applied in code, before any judge sees it — by `/review-fix-loop`'s preflight, which
extracts this block, and by the review of CI. A class enters when a fixer contested it with an accepted argument
**and** a judge kept it as false on another PR; it leaves when a real precedent contradicts it. `id` is stable,
`precedent` is the PR/issue. This section deliberately does not follow the `FM-N` format: it is not a failure
mode, it is the complement — what the review learned NOT to say.

```json review-exclusions
[]
```
