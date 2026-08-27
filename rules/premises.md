---
description: How to read (index first), write, update and protect the domain premises files
paths: ["docs/**/premises*.md"]
---

Premises document the **technical invariants** a domain depends on to work: the assumptions that, if violated,
silently break something downstream. They are distinct from `CORE_TENETS.md` (business rules) and the schema docs
(structure). A premise must be **falsifiable** — phrased so a test *could* break if it were violated. "The system
is robust" is not a premise; "a settled record is never updated or deleted" is.

## Reading: the index, then the id — never the whole file

1. Read the domain's **premises index** (generated, one line per premise, each with its `p-` id).
2. Open only the bodies that bear on your change, by id, with the **premise fetch command** from
   `docs/agents/skills-config.md` (default `python3 .github/scripts/premise.py <id>`; `--deps` adds the ones it
   points at).

Never read the premises file whole: the `Read` tool truncates at 2.000 lines, so a large domain silently loses
its tail and you reason on a file you believe you read. The id is minted once, sits right below the H2, and
survives a retitle or a file split — the title does not.

## Format

```markdown
## {Premise title — short, declarative}
**Id:** {p-xxxxxxxx — mint with the fetch command's `mint` subcommand; never reuse or renumber}

{One paragraph: what is true and must remain true.}

**Why:** {the business/technical concern that motivates it.}
**Breaks:** {what specifically goes wrong downstream if violated.}
**Tests:** {the test class or method that protects it.}
**Depends on:** {premises this one relies on, by H2 title or by `p-` id — a cross-domain invariant lives in the
domain that owns the entity, and the consumer references it here.}
```

- `**Tests:** none yet` is incomplete. If the premise is **introduced by new code in this PR**, the test is
  required before merge. If it **documents a pre-existing invariant**, `none yet` is an acceptable starting state
  and a stated follow-up.
- Where a load-bearing invariant has no executable guard, encode it at the seam (`require`/`check`/assert) as well
  as in a test — that is the only defence against paths nobody enumerated. Avoid a silent log-and-continue
  fallback for an invariant that protects a critical quantity: make the soft enforcement deliberate and *alarmed*.
- A domain past ~30 premises splits into `premises-{aspect}.md` part-files, the main file keeping the domain
  context and one link per part. The title list is the **generated** index, never a hand-written one.

## Workflow

1. **Before implementing** — read the index of every domain you touch, fetch the premises that bear on the change.
2. **While planning** — ask the user what invariants the change depends on, what would break elsewhere if the
   assumption were wrong, and where the happy path does not hold.
3. **After implementing** — add or update the premises your code introduces or invalidates (every new one needs
   `**Id:**` and `**Tests:**`), then regenerate the indices (`python3 .github/scripts/context_lint.py
   --write-indices`) and commit them: C15 fails the PR on drift, C17 on a missing or duplicate id.
