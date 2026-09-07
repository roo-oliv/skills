# Telemetry — two lanes: what a premise is worth, and what a session costs

Every estimate about an agent harness is an estimate until someone measures it. Two questions are worth
money, and they are answered by two **independent, complementary** collections:

| Lane | Answers | Needs | Where the data lives |
|---|---|---|---|
| **A — git-only** *(default)* | *is this premise used, and violated anyway* — per premise, across every person and machine | `git`, and `gh` for the open PRs | commit trailers in the branch history + a local load log |
| **B — OTLP** *(optional)* | *what did it cost* — tokens and USD by model x effort x skill x agent | an OTLP intake | your observability backend |

**What each lane does NOT cover**, stated plainly because the gap is the reason both exist:

- lane A never measures a token or a dollar. It counts commits, not usage;
- lane B never knows a premise exists. It has no per-invariant axis at all, and no notion of a violation.

Which lanes are on is `## Telemetry` › **Lanes** in `docs/agents/skills-config.md`
(`git-only | otlp | both`, default `git-only`). On `git-only` the `setup` skill asks for nothing but the
default branch, and `scripts/install.sh` writes **no** OTLP variables into `.claude/settings.json`. A
config that names an `Endpoint` or a `Key variable` but no `Lanes` line reads as `both`, so a repo that
configured the export before the field existed never loses it.

Lane A is the default because it needs no vendor, no account and no key: the data rides in commits the
repo already has. Lane B is worth turning on the moment you want to cut a round, a lens or a subagent
tier and need the cost per PR first.

---

## Lane A — git-only

### Why a commit message is the store

A premise is good or bad only against use, and the signal has two halves: **was it read**, and **was it
violated anyway**. The read half exists in the local load log — but that log is per machine and never
leaves the disk that wrote it. The commit message is the only store that

- every session already writes to,
- **survives the squash**: a squash-merge concatenates the messages of every commit of the PR into the
  body of the commit on the default branch, so each original commit's trailers become body lines,
- aggregates across people and machines with no pipeline at all: whoever clones the repo has the history,
- is minable offline by `git log` and through the PR API.

So the signal lives there, in four trailers:

| Trailer | Written by | Meaning |
|---|---|---|
| `Agent-Session: <session id>` | the commit gate, from the hook payload | this commit was produced inside an agent session — the population the report counts |
| `Premises-Read: p-…, p-…` | the commit gate, from the session's load log | the premises consulted to produce it (ids read by `fetch`/`range`, ceiling 40) |
| `Premises-Files-Read: <path>` | the commit gate, from the load log | whole-file reads, and the tail over the id ceiling |
| `Premises-Violated: p-…` | `review-fix-loop.js`, from the posted findings | the premises whose violation this fix corrects |

Every value is **computed by a script** — from the load log, or from the JSON the review loop itself
posted. The agent transports a string it is handed; it never judges its own output. (A model grading the
usefulness of what it just produced is noise, which is why that design was rejected.)

The gate that enforces the first three, what is *not* a commit, and the audited escape are in
[`docs/context-toolkit.md`](context-toolkit.md#the-commit-trailers-gate). The `Premises-Violated` half,
and the `json review-findings` block that closes every posted review, are in the
[`review-fix-loop` skill](../skills/review-fix-loop/SKILL.md).

### The local load log

The trailers say which premises a *commit* consulted. The finer-grained question — **which
instruction files entered the context at all, and why** — is answered locally, by two hooks pointing
at
[`ci/agent_telemetry.py`](../ci/agent_telemetry.py) (both installed by `scripts/install.sh`).

| Event | Matcher | What it records |
|---|---|---|
| `InstructionsLoaded` | every `load_reason` | every load of the instructions file and of a rule — `session_start`, `nested_traversal`, `path_glob_match`, `include`, `compact` |
| `PostToolUse` | `Read\|Skill\|Bash` | a Read landing on a surface of this repo, a Skill invocation whose `SKILL.md` the repo owns; inside a premises file, one extra line per premise read; and in `Bash`, one line per premise fetched by id |

One JSON line per load, in `.claude/telemetry/loads-<UTC date>.jsonl` — gitignored, one file per day,
one append per line:

```json
{"agent_type": null, "bytes": 17340, "effort": "high", "event": "InstructionsLoaded", "memory_type": "Project", "path": "CLAUDE.md", "premise": null, "premise_id": null, "read": null, "reason": "session_start", "session_id": "fc48235c-…", "trigger": null, "ts": "2026-08-26T23:08:27.090Z"}
{"agent_type": null, "bytes": 4329, "effort": "high", "event": "InstructionsLoaded", "memory_type": "Project", "path": ".claude/rules/docs.md", "premise": null, "premise_id": null, "read": null, "reason": "path_glob_match", "session_id": "fc48235c-…", "trigger": "docs/index.md", "ts": "2026-08-26T23:08:31.215Z"}
```

`path` is repo-relative when the file is internal and absolute with `~` when it is user memory.
`trigger` is the file that fired the glob — it is what explains *why* a rule loaded. `agent_type` is
filled in when the load happened inside a subagent, which separates fan-out context cost from the main
thread's. `effort` is the session's reasoning level (`low`, `medium`, `high`, `xhigh`, `max`), read
from the payload and then from `$CLAUDE_EFFORT`; anything outside those five logs as `null`, because a
level this build does not know would invent a bucket in the histogram.

**What counts as a surface** comes from the config, never a constant: the instructions file and the
README, plus anything under the rules dir, the skills dir and the docs root, minus the excluded globs.
Move `docs/` to `documentation/` in the config and the logger follows.

### One Read of a premises file is N loads of premises

A premises file is a **container of independent surfaces**: reading it is not one load of 200 KB of
knowledge, it is a load of the premises the window covered. So when a Read lands on a premises file
(the generated index excepted — that is a plain surface), the hook writes, **besides** the file line,
one line per premise whose section intersects the range:

- sections are parsed exactly as `premise.py` and `context_lint.py` parse them, so a title logged here
  is the title the linter and the index use;
- the range is `[offset, offset + limit)`, 1-based; with neither, the whole file;
- the extra fields are `premise`, `premise_id` and `read` (`range` or `whole`); on every other line all
  three are `null`;
- the **file** line stays one line: in the report a Read is always **one** load of the file. Counting
  the premise lines too would charge one Read as N loads and N times the file size.

### Reading by id — the main path

With a premises index each premise carries a stable `**Id:** p-xxxxxxxx` and an exact fetch command:

```bash
python3 .github/scripts/premise.py p-1a2b3c4d --deps
```

That is how an agent actually reads a premise — through `Bash`, not through `Read` with an `offset`. An
offset→heading mapping alone would be blind to the main path, so the hook also runs on `PostToolUse`
matcher `Bash`: when the command **invokes** the configured fetch script (in command position: the
script itself, or the argument of an interpreter) with one or more ids in **argv**, it writes one line
per id with `read: "fetch"`, the premise resolved from the tree, and `premise_id` always set.

It is argv, not substring: `echo "premise.py p-1a2b3c4d"` and a `grep` for the same string do **not**
count. An id the tree no longer carries still produces its line, with `path` and `premise` null — a
dangling reference is exactly what the lifecycle wants to see.

The hook is **fail-open by contract**: it never writes to stdout, always exits 0, and is wired with a
5 s timeout. A session outside the repo, or a machine without `python3`, simply produces no log. Each
worktree writes to its own directory; the report accepts several at once.

### The load report

```bash
python3 .github/scripts/agent_telemetry.py report --since 2026-08-20 .claude/telemetry
python3 .github/scripts/agent_telemetry.py report --format json ~/work/*/.claude/telemetry
```

Per file: `loads`, distinct `sessions`, `session_share`, `bytes` (size in the tree today),
`bytes_loaded` (Σ of what was loaded — the **tax** the surface charges), a `reasons` histogram and
`last_loaded`. The universe is the repo's own tree; a path loaded outside it is listed as `external`.

`kind` ∈ `instructions`, `readme`, `rule-always-on`, `rule-scoped`, `skill`, `doc`, `external`.
`candidate` marks what the lifecycle should look at: `zero-load` (no load in the window) or `low-load`
(`session_share` < 5 % with ≥ 20 sessions) — and **never** for `instructions`, `readme`,
`rule-always-on` or `external`: they either cannot not load, or are not this repo's surfaces. A
candidate is a candidate, not a verdict.

`window.sessions_by_effort` is a histogram of **distinct sessions per effort level**, `unknown` for
lines without the field. It counts sessions, not loads; a session that changed level inside the window
counts in each level it logged, so the totals may exceed `window.sessions`.

`--format json` carries `schema_version: 2` and is the contract
[`ci/context_decay.py`](../ci/context_decay.py) reads (it accepts 1, 2 and the 3 of `commits` below): `window`, `surfaces[]`,
`candidates[]` and `premises[]`. Each `premises[]` row is one premise of the tree, read or not: `path`,
`title`, `id`, `loads_range`, `loads_whole`, `loads_fetch`, `sessions`, `last_loaded`, `candidate`. The
three counters mean different things — `fetch` is a **directed** read (the agent asked for that premise
by id), `range` is a windowed read, and `whole` credits every premise in the file without any of them
having been *sought*, the weakest of the three signals.

### The trailer miner: `commits`

```bash
python3 .github/scripts/agent_telemetry.py commits --prs open
python3 .github/scripts/agent_telemetry.py commits --prs open --report /tmp/telemetry.json --format json
```

Two sources. The **branch log** (`--branch`, default `## Telemetry` › `Default branch`, falling back to
`origin/<branch>`) carries the squash bodies of everything that merged. The **open PRs** (`--prs open`)
add what has not merged yet, through `gh`. Only the open ones: a merged PR is already a squash body on
the branch, and counting it again doubles every premise it read. If the same trailer block shows up in
both, the second is skipped (`duplicates_skipped`). `gh` missing or unauthenticated is a **note**, never
a failure — the branch alone is a smaller, valid answer.

Two implementation facts that are not obvious and cost real debugging:

- **never `git interpret-trailers`.** It reads only the last paragraph, and the last paragraph of a
  squash body belongs to the *merge*, not to any of the commits that carried a session. The miner runs a
  line-by-line regex over the whole body instead, and a run of consecutive `Key: value` lines is one
  original commit — so the `Co-Authored-By:` lines git already writes do not split a block in two.
- **two `gh` calls, not one.** `gh pr list --json commits` asks GitHub for `PRs x commits x authors`
  nodes in a single GraphQL query, which is rejected above 500 000 nodes; the source then reads as
  *empty*, silently. The miner lists the numbers first and asks each PR for its own commits.

One `Agent-Session` block = one original commit. Aggregation is by premise id over the tree's universe,
and each premise gets a **class** — counting, not judgement:

| class | when | action |
|---|---|---|
| `confusing` | read **and** violated ≥ 2x | rewrite it, or promote it to a gate — read and violated anyway is a problem with the text, not with the reader |
| `undiscoverable` | violated ≥ 1x, never read | a discovery problem: the title in the index, the hook's term mapping |
| `watch` | read and violated exactly once | one violation is an accident, two are a pattern |
| `working` | read, never violated | keep |
| `redundant` | never read, has `**Tests:**` | a test already gates it — trim the prose |
| `decay-candidate` | never read, no `**Tests:**`, never violated | a decay candidate |
| `unclassified` | silence in a window with fewer than 20 annotated commits | nothing to conclude |

The thresholds are constants at the top of the script (`CONFUSING_MIN_VIOLATIONS = 2`,
`MIN_COMMITS_FOR_SILENCE = 20`) and are echoed in `commits.totals`. The silence floor is the same
reasoning as `MIN_SESSIONS_FOR_LOW_LOAD` in the load report: without enough annotated commits, "nobody
read it" describes the window, not the premise.

`co_read_pairs` lists the ids that appear together in the same `Premises-Read` in ≥ 2 commits —
merge candidates, cross-checked against `--near-duplicates` below.

`--format json` emits `schema_version: 3`, a **superset of 2**: the same `window`/`surfaces`/`premises`/
`candidates` keys plus `commits`. Without `--report` the v2 half is empty — this source carries no load
telemetry, and an empty `surfaces[]` says exactly that to a v2 consumer instead of lying. With
`--report <the v2 json>` one file answers both axes and the decay scan takes a single `--telemetry`. The
`report` subcommand keeps emitting `2`: the version rises where the new key is, not across the tree.

**On a repo whose history predates the gate, `commits` finds zero blocks** and everything lands in
`unclassified`. That is the honest baseline, not a bug.

### Near-duplicate premises

```bash
python3 .github/scripts/context_lint.py --near-duplicates              # markdown
python3 .github/scripts/context_lint.py --near-duplicates --format json
```

A **report, not a check**: it always exits 0 and never runs in CI. It compares every pair of premises
with the same `difflib.SequenceMatcher(...).ratio()` the linter's rename detection (C11/C12) uses, so
"near-duplicate" means one thing in this toolkit. What it compares is the **rationale**, with the
`**Id/Why/Breaks/Tests/Depends on:**` blocks removed: every premise carries the same field skeleton, and
including it puts two short, unrelated premises over 0.8 before a word of their content is compared. A
Jaccard word prefilter (≥ 0.4, half the body threshold) throws out nearly all of the n² pairs before the
expensive comparison — a 600-premise tree finishes in about a second. `--threshold` moves the floor.

### Feeding the decay scan

The monthly round with both quality signals is four commands, the second folding the load report into
the commit report so the scan receives **one** `--telemetry`:

```bash
python3 .github/scripts/agent_telemetry.py report --format json .claude/telemetry > /tmp/telemetry.json
python3 .github/scripts/agent_telemetry.py commits --prs open --report /tmp/telemetry.json \
        --format json > /tmp/commits.json
python3 .github/scripts/context_lint.py --near-duplicates --format json > /tmp/dups.json
python3 .github/scripts/context_decay.py --since 90 --telemetry /tmp/commits.json --duplicates /tmp/dups.json
```

The scan turns them into two signals ([`docs/context-toolkit.md`](context-toolkit.md#the-decay-cycle)):
**D8** for a premise classed `confusing` or `undiscoverable` (the other classes are states, not queues),
and **D9** for one paired by `--near-duplicates`. Both are decided *before* the citation rule that
protects a cited doc, because neither is a deletion: rewriting a confusing premise and merging two
near-identical ones are things you do to a doc that is very much alive. To read the matrix with your
eyes instead, `commits --prs open` in markdown is the meeting agenda: the class counts first, then the
`confusing`/`undiscoverable` list and the co-read pairs.

---

## Lane B — OTLP (optional)

The metrics answer *what did it cost* and nothing else — no premise axis, no notion of a violation. Turn
it on with `Lanes: otlp` or `both`; before cutting a round, a lens or a subagent tier you want three
numbers nobody has by default: **cost per PR**, **the share of tokens spent in subagents**, and **cost
per skill**. Measure first, then cut.

### The `env` block

`scripts/install.sh` merges [`settings/env.json`](../settings/env.json) into the target's
`.claude/settings.json` — each key only if the repo does not already set it, so your own values always
win. Nothing here is a secret, and the data collected is not sensitive.

| Variable | Value | Why |
|---|---|---|
| `CLAUDE_CODE_ENABLE_TELEMETRY` | `1` | turns collection on; without it nothing is emitted |
| `OTEL_METRICS_EXPORTER` | `otlp` | metrics to the OTLP intake |
| `OTEL_LOGS_EXPORTER` | `otlp` | events (`api_request`, `tool_result`, …) as logs |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | config › **Protocol**, default `http/protobuf` | gRPC cannot carry a *dynamic* auth header, and most agentless intakes do not speak it |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | config › **Endpoint**, default `http://localhost:4318` | the default is the OTLP one: a collector or vendor agent on the machine |
| `OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE` | `delta` | several intakes accept **only** delta; it is already the CLI default, made explicit |
| `OTEL_RESOURCE_ATTRIBUTES` | config › **Resource attributes**, default `repo=<dir name>` | separates this repo from every other one exporting to the same place |
| `OTEL_METRICS_INCLUDE_SESSION_ID` | `false` | cardinality: per-session analysis happens on the log events, not the metrics |
| `OTEL_METRICS_INCLUDE_ACCOUNT_UUID` | `false` | same; the hashed `user.id` already identifies the emitter |
| `OTEL_METRICS_INCLUDE_VERSION` | `true` | `app.version` correlates a behaviour change with a CLI upgrade |

Prompts and tool details stay **off** (`OTEL_LOG_USER_PROMPTS` and `OTEL_LOG_TOOL_DETAILS` are not
set): they would carry customer data and credential fragments to a third party.

**Kill switch**, for diagnosis — in `.claude/settings.local.json` (gitignored, local beats project):

```json
{ "env": { "OTEL_METRICS_EXPORTER": "none", "OTEL_LOGS_EXPORTER": "none" } }
```

### Setup: one repo variable, and `gh auth` already working

There is no per-machine step. The key is read at runtime by
[`ci/otel_headers.py`](../ci/otel_headers.py), which the `otelHeadersHelper` entry invokes:

```json
{ "otelHeadersHelper": "bash -c 'cd \"$(git rev-parse --show-toplevel 2>/dev/null || echo .)\" && python3 .github/scripts/otel_headers.py || echo {}'" }
```

The script runs `gh variable get <Key variable>` and prints `{"<Key header>": "<value>"}`. On any
failure — no `gh`, not authenticated, offline, variable absent — it prints `{}` and exits 0 without a
word on stderr: a telemetry helper must never delay or break a session start.

```bash
gh variable set CLAUDE_CODE_OTEL_API_KEY --body '<an intake-only key>'
```

**A variable, not a secret**, on purpose: a secret is write-only and no `gh` command reads its value
back. Anyone with read on the repo can read a variable — and that is the intended clearance, not a
leak, *provided the key is intake-only*: it writes telemetry and reads nothing, is dedicated to this
use and is revocable on its own. Never the application's key. (`GET
/repos/{owner}/{repo}/actions/variables/{name}` needs collaborator access; the Settings UI needs
admin, so a read-only collaborator reads the value by API without seeing it in the UI. Someone without
the clearance just gets `{}` and emits nothing.)

**Why the helper resolves the repo root itself.** Measured: `$CLAUDE_PROJECT_DIR` does **not** expand
inside the `otelHeadersHelper` value, and the variable is not in the helper's environment either
(unlike hooks). A relative path only works when the session was opened at the root — opened in `docs/`,
the helper never runs. An absolute path works but is not committable. `git rev-parse --show-toplevel`
covers both, and the script falls back to it as well.

**Refresh.** The helper runs at session start **and periodically** — about every 29 minutes by default,
tunable with `CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS`. So **rotating the key does not require
reopening a session**: a running one picks up the new value on the next cycle. Rotation is: revoke the
old key at the vendor, create the new one, re-run `gh variable set`. No commit, no PR.

**With no variable** there is no header and the export fails authentication. Measured (8 headless
sessions with the `env` block and no header, against 8 controls): stderr byte-identical to the control
— no OTLP error, no export line, no 401 — and exit latency inside the model's own noise. Before the
variable exists, and on a CI runner where `gh` is not authenticated as a human, the session simply
emits nothing. That is why there is no "disable telemetry in CI" step.

### What to measure

| Question | How |
|---|---|
| **Cost per PR** | `cost.usage` ÷ `pull_request.count` |
| **Share of tokens in subagents** | `token.usage{query_source:subagent}` ÷ `token.usage` |
| **Cost per skill** | `cost.usage` grouped by `skill.name` — but see the Workflow caveat below |
| **Model × effort** | `cost.usage` and `token.usage` grouped by `model,effort` — the pair the role tiering is calibrated on |
| **Cost of one workflow run** | not from here — `ci/wf_timeline.py` over the run journal (below) |

**Cost per skill does not see a Workflow's subagents.** They arrive tagged with a generic agent
type and no `skill.name`, so a `cost.usage{skill.name:deep-plan}` widget only ever reports the main
thread — under a dollar a run, next to run journals showing tens of millions of cache-read tokens
for the same runs. For a `deep-plan` or `implement` run, the instrument is
`ci/wf_timeline.py <wf_dir> --stages` (vendored to `.github/scripts/`), which reads
`~/.claude/projects/<project>/<session>/subagents/workflows/wf_*` and reports wall-clock, turns and
tokens **per agent and per stage**, each stage with its window. See
[`workflow-calibration.md`](workflow-calibration.md).

Attributes worth knowing, verified on a real session:

- `claude_code.session.count`, `active_time.total`, `pull_request.count`, `commit.count`,
  `lines_of_code.count`, `code_edit_tool.decision` carry `repo`, `user.id`, `app.version`,
  `terminal.type`;
- `claude_code.token.usage` adds `model`, `query_source`, `type` ∈ input | output | cacheCreation |
  cacheRead; `claude_code.cost.usage` adds `model`, `query_source` (USD);
- `query_source` is `main` on the main thread, `subagent` in a fan-out, plus `auxiliary`, `compact`,
  `repl_main_thread`; a **named** subagent can appear under its own name;
- `agent.name` is literal for a built-in subagent and `custom` for a user-defined one — this is the
  axis that separates fan-out cost from main-thread cost;
- `effort` exists on recent CLI versions and disappears on a model that does not support it;
- `session.id` and `account.uuid` are **not** in the metrics (the two `INCLUDE_` flags above), which is
  why per-session analysis lives in the log events: filter `@event.name:api_request` and sum
  `@cost_usd` by `@session.id`.

#### Example queries (one vendor as the worked example, not a requirement)

One vendor's dialect is what was actually measured here; any OTLP intake works and the query language changes.

| Widget | Query |
|---|---|
| Cost per PR | `sum:claude_code.cost.usage{$repo}.as_count()` ÷ `sum:claude_code.pull_request.count{$repo}.as_count()` |
| % tokens in subagents | `100 * sum:claude_code.token.usage{$repo,query_source:subagent}.as_count()` ÷ `sum:claude_code.token.usage{$repo}.as_count()` |
| Cost per skill | `sum:claude_code.cost.usage{$repo} by {skill.name}.as_count()` |
| Tokens per agent | `sum:claude_code.token.usage{$repo} by {agent.name}.as_count()` |
| Tokens by type | `sum:claude_code.token.usage{$repo} by {type}.as_count().rollup(sum, 86400)` |
| Cost by model × effort | `sum:claude_code.cost.usage{$repo} by {model,effort}.as_count()` |
| Cost by query source | `sum:claude_code.cost.usage{$repo} by {query_source}.as_count()` |
| PRs / commits / sessions | `sum:claude_code.{pull_request,commit,session}.count{$repo}.as_count()` |

Scope every query to a **template variable** `$repo` rather than `{*}`: one dashboard serves every repo
that exports, and `OTEL_RESOURCE_ATTRIBUTES` is what tells them apart. A dashboard is not versioned —
this table is the record of what it should contain.

#### Two limits that change how the numbers read

- `claude_code.pull_request.count` only counts a PR **created inside a session**. "Cost per PR" means
  PRs the agent opened, not PRs the repo merged.
- A session with `ANTHROPIC_API_KEY` in the environment runs on the API, so it measures API dollars
  rather than pressure on a subscription's limits — and the user attribute disappears on those
  sessions. That absence is the filter that separates the two populations.

Cost of the collection itself: a few hundred custom series and one or two million log events a month
at short retention — tens of dollars a month at worst. Review the series count once the baseline is in.

### Offline baseline: ccusage

Independent of everything above; useful as the "before" and as a total to check against:

```bash
npx -y ccusage@latest daily --since 20260820 --offline --breakdown
```

Also `session`, `blocks` and `--json`. What it does **not** give — and what this collection adds — is
attribution per subagent, per skill and per agent, and cost per PR.
