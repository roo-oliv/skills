# Telemetry — what a session costs, and what it loaded

Every estimate about an agent harness is an estimate until someone measures it. Before cutting a
round, a lens or a subagent tier you want three numbers nobody has by default: **cost per PR**, **the
share of tokens spent in subagents**, and **cost per skill**. This is the collection that produces
them — measure first, then cut.

Two independent halves, deliberately:

| Half | Answers | Where it goes |
|---|---|---|
| **OTLP export** (`settings/env.json` + [`ci/otel_headers.py`](../ci/otel_headers.py)) | *what did it cost* — tokens, USD, sessions, PRs, commits, by model × effort × query source | your observability backend |
| **Instruction-load log** ([`ci/agent_telemetry.py`](../ci/agent_telemetry.py)) | *what entered the context, and why* — per file and per premise | `.claude/telemetry/*.jsonl`, local |

The first is a vendor's problem; the second is the input to the decay cycle
([`docs/context-toolkit.md`](context-toolkit.md) › D3).

## The `env` block

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

## Setup: one repo variable, and `gh auth` already working

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

## What to measure

| Question | How |
|---|---|
| **Cost per PR** | `cost.usage` ÷ `pull_request.count` |
| **Share of tokens in subagents** | `token.usage{query_source:subagent}` ÷ `token.usage` |
| **Cost per skill** | `cost.usage` grouped by `skill.name` |
| **Model × effort** | `cost.usage` and `token.usage` grouped by `model,effort` — the pair the role tiering is calibrated on |

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

### Example queries (Datadog — one example, not a requirement)

Datadog is the only backend measured here. Any OTLP intake works; the query dialect changes.

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

### Two limits that change how the numbers read

- `claude_code.pull_request.count` only counts a PR **created inside a session**. "Cost per PR" means
  PRs the agent opened, not PRs the repo merged.
- A session with `ANTHROPIC_API_KEY` in the environment runs on the API, so it measures API dollars
  rather than pressure on a subscription's limits — and the user attribute disappears on those
  sessions. That absence is the filter that separates the two populations.

Cost of the collection itself: a few hundred custom series and one or two million log events a month
at short retention — tens of dollars a month at worst. Review the series count once the baseline is in.

## Offline baseline: ccusage

Independent of everything above; useful as the "before" and as a total to check against:

```bash
npx -y ccusage@latest daily --since 20260820 --offline --breakdown
```

Also `session`, `blocks` and `--json`. What it does **not** give — and what this collection adds — is
attribution per subagent, per skill and per agent, and cost per PR.

## The instruction-load log

The metrics answer "what did it cost". They do not answer "**which instruction files entered the
context, and why**", which is what the decay cycle needs. That half is local: two hooks pointing at
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

## The report

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
[`ci/context_decay.py`](../ci/context_decay.py) reads (it accepts 1 and 2): `window`, `surfaces[]`,
`candidates[]` and `premises[]`. Each `premises[]` row is one premise of the tree, read or not: `path`,
`title`, `id`, `loads_range`, `loads_whole`, `loads_fetch`, `sessions`, `last_loaded`, `candidate`. The
three counters mean different things — `fetch` is a **directed** read (the agent asked for that premise
by id), `range` is a windowed read, and `whole` credits every premise in the file without any of them
having been *sought*, the weakest of the three signals.
