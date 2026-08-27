# intent/ — the artifact chain of each unit of work

Following the [AI-native SDLC playbook](https://claude.com/blog/the-ai-native-sdlc-playbook): every unit of work
gets a folder carrying the artifacts of each phase of the loop, versioned next to the code they produced —
"every stage commits an artifact the next stage can read".

```
intent/<slug>/
├── intent.md   # Plan phase   — the problem and desired outcome, in the originator's words
├── spec.md     # Design phase — requirements and design, with this repo's policies applied
└── plan.md     # Build phase  — the implementation plan (files, order, risks, proof) + the Contract block
```

Conventions:

- **`Status:` in the header of each artifact**: `draft` → `approved` → `implemented`. `refine` writes all three
  and stamps `approved`; `implement` reads `plan.md` and stamps `implemented`; `verify-plan` reconciles the diff
  against the Contract block in `plan.md`.
- `intent.md` follows the playbook's template: **Problem / Proposed outcome / Affected users and systems /
  Constraints / Open questions**.
- Unlike a dated `docs/work/` record (written once, never edited, superseded by a banner), these artifacts are
  **live until `implemented`** — the revision history is the git log.
- This folder sits **outside the docs tree deliberately**: `docs/**` is published (wiki, docs site, an MCP
  server's document table…) on every merge, and an intent draft is not documentation.
- The PR gate reads `plan.md` here first when it looks for a complete plan-contract on a branch that touches a
  sensitive domain.
