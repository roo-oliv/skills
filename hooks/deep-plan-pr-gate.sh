#!/usr/bin/env bash
# PR gate — PreToolUse hook on Bash. Three gates: one on `git commit`, two on `gh pr create` /
# `gh pr ready`:
#
#   0. commit-trailers gate — a `git commit` made inside an agent session must carry the premise
#      trailers the session's load log dictates (Agent-Session, Premises-Read, Premises-Files-Read).
#   1. premises-index gate — the branch touches a sensitive domain and NO session in this worktree
#      read that domain's premises index recently. Read the index and retry.
#   2. deep-plan gate — `gh pr create` on a sensitive branch without a COMPLETE plan-contract (a
#      `## Contract` block, no GATE FAIL, no unjustified GAP, Residual GAPs: 0).
#
# Which domains are sensitive, and which paths belong to them, is read from the repo's config
# (docs/agents/skills-config.md › Domains + Sensitive domains) through
# `context_hooks.py sensitive-domains` — one implementation, never a copy in shell. A repo that
# declares no sensitive domains (or has no config) is never blocked by either gate.
#
# Contract:
#   exit 0            -> allow the command
#   exit 2 + stderr   -> block; stderr is surfaced to the agent (PreToolUse convention)
#
# Overrides: `[deep-plan-override: <reason>]` in the args or DEEP_PLAN_OVERRIDE=<reason> skips the
# deep-plan gate (and NOTHING else); CONTEXT_HOOKS_DISABLE=gate skips the premises-index gate and
# CONTEXT_HOOKS_DISABLE=trailers the commit-trailers one. All audited to stderr.
#
# Fail-open by design: any ambiguity that isn't a clear "incomplete contract on a sensitive branch"
# allows the command through. Recognition is by ARGV, not substring: `echo "gh pr create"` and
# heredoc bodies do not match, while `cd x && GH_TOKEN=y gh pr create` does.

set -uo pipefail

HOOK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Resolve the repo root robustly: the checkout the COMMAND runs in wins (line 3 of gate-input, from
# the payload's cwd — an agent isolated in a worktree keeps the mother session's CLAUDE_PROJECT_DIR
# and every gate would measure the wrong checkout), then the harness-provided dir, then the git
# toplevel (so the gate still works from a subdirectory), then the cwd.
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-}"
if [ -z "$PROJECT_DIR" ]; then
  PROJECT_DIR="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
fi

# --- 0. Read the tool input and run the commit-trailers gate ---------------
# Only read stdin when it is piped (the harness pipes the tool-input JSON); the TTY guard stops a
# manual interactive run from hanging on cat waiting for EOF.
if [ ! -t 0 ]; then
  payload="$(cat 2>/dev/null || true)"
else
  payload=""
fi

# The commit gate answers on stderr only (exit 2 = block, 0 = allow, including the audited escape),
# so 2>&1 into a variable and replay it. It runs before everything else because a `git commit` is
# not a `gh pr` command and would otherwise leave through the early exit below.
# A code other than 0/2 is the gate itself failing (no python3, unreadable script): stay quiet and
# allow — a broken gate must never be the reason a commit cannot be made.
commit_gate_out="$(printf '%s' "$payload" | python3 "$HOOK_DIR/context_hooks.py" commit-gate 2>&1)"
commit_gate_code=$?
if [ "$commit_gate_code" -eq 0 ] || [ "$commit_gate_code" -eq 2 ]; then
  [ -n "$commit_gate_out" ] && printf '%s\n' "$commit_gate_out" >&2
  [ "$commit_gate_code" -eq 2 ] && exit 2
fi

# --- 1. Ask context_hooks.py what the command is ---------------------------
# Three lines: the `gh pr` subcommand recognised BY ARGV (empty when the command merely contains the
# words), the engaged-sentinel TTL in minutes, and the repo the command runs in (empty when the
# payload carries no cwd, or it is not a git checkout).
sub=""
TTL_MIN=""
payload_root=""
{ read -r sub; read -r TTL_MIN; read -r payload_root; } < <(
  printf '%s' "$payload" | python3 "$HOOK_DIR/context_hooks.py" gate-input 2>/dev/null || true
)
[ -n "${payload_root:-}" ] && PROJECT_DIR="$payload_root"
[ -z "${sub:-}" ] && exit 0
[ -z "${TTL_MIN:-}" ] && exit 0

cd "$PROJECT_DIR" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

# --- 2. Is this a sensitive-domain branch? (config, not hardcoded) ---------
changed="$(git diff --name-only origin/main...HEAD 2>/dev/null || true)"
[ -z "$changed" ] && changed="$(git diff --name-only origin/main 2>/dev/null || true)"
[ -z "$changed" ] && exit 0

domains="$(
  printf '%s\n' "$changed" |
    CLAUDE_PROJECT_DIR="$PROJECT_DIR" python3 "$HOOK_DIR/context_hooks.py" sensitive-domains 2>/dev/null || true
)"
# Not a sensitive-domain change (or no config) — neither gate applies.
[ -z "$domains" ] && exit 0

# --- 3. premises-index gate (both `create` and `ready`) --------------------
# The sentinel is written by the context hooks when any session in THIS WORKTREE reads the domain's
# premises file or index. Worktree-wide on purpose: the pipeline opens PRs from a headless session of
# its own, so requiring the read in the same session would block every pipeline PR.
case ",${CONTEXT_HOOKS_DISABLE:-}," in
  *,all,* | *,gate,*)
    echo "premises-index gate: disabled via CONTEXT_HOOKS_DISABLE — allowing." >&2
    ;;
  *)
    missing=""
    for d in $domains; do
      if ! find "$PROJECT_DIR/.claude/.context-hooks" -mindepth 2 -maxdepth 2 \
        -name "engaged-$d" -mmin "-$TTL_MIN" 2>/dev/null | grep -q .; then
        missing="$missing $d"
      fi
    done
    if [ -n "$missing" ]; then
      {
        echo "🚫 premises-index gate: blocked."
        echo ""
        echo "This branch touches sensitive domain(s)$(printf '%s' "$missing" | sed 's/ /, /g; s/^,//') and no"
        echo "session in this worktree read their premises index in the last $TTL_MIN min."
        echo "Read (Read tool or cat) and retry:"
        printf '%s\n' $missing |
          CLAUDE_PROJECT_DIR="$PROJECT_DIR" python3 "$HOOK_DIR/context_hooks.py" premises-paths 2>/dev/null |
          while IFS="$(printf '\t')" read -r name index premises; do
            echo "  $index   (fallback: $premises)"
          done
        echo ""
        echo "Escape (audited): CONTEXT_HOOKS_DISABLE=gate."
      } >&2
      exit 2
    fi
    ;;
esac

# `gh pr ready` only passes through the index gate — the plan-contract is checked when the PR is
# created, not when a draft is marked ready.
[ "$sub" = "ready" ] && exit 0

# --- 4. Override of the deep-plan gate -------------------------------------
if [ -n "${DEEP_PLAN_OVERRIDE:-}" ]; then
  echo "deep-plan PR gate: overridden via DEEP_PLAN_OVERRIDE=${DEEP_PLAN_OVERRIDE} — allowing." >&2
  exit 0
fi
cmd="$(printf '%s' "$payload" | python3 -c 'import sys,json
try:
    d=json.load(sys.stdin)
    print((d.get("tool_input") or {}).get("command",""))
except Exception:
    print("")' 2>/dev/null || true)"
case "$cmd" in
  *"[deep-plan-override:"*)
    echo "deep-plan PR gate: overridden via [deep-plan-override:] token in PR args — allowing." >&2
    exit 0
    ;;
esac

# --- 5. Find a complete plan-contract for this repo ------------------------
is_complete_contract() {
  local f="$1"
  grep -Eq '^##+ *Contract' "$f" 2>/dev/null || return 1
  # "gate ... fail" tolerating markdown emphasis/colon (**Gate**: FAIL, Gate: fail, GATE FAIL). The
  # separators are punctuation/space only, so "gateway ... fail" cannot match (the 'w' is no separator).
  grep -Eiq 'gate[*_: ]+fail' "$f" 2>/dev/null && return 1
  if grep -Eiq 'residual gaps' "$f" 2>/dev/null; then
    grep -Eiq 'residual gaps[^0-9]*0' "$f" 2>/dev/null || return 1
  fi
  if grep -Eiq '\| *GAP *\|' "$f" 2>/dev/null; then
    return 1
  fi
  return 0
}

found_complete=0
checked=0

# 5a. Repo-local contract artifacts committed ON THIS BRANCH (most robust). Only a contract this
# branch adds/modifies counts — otherwise one that landed on main would bypass the gate forever.
if [ -d "$PROJECT_DIR/.claude/deep-plan" ]; then
  for f in "$PROJECT_DIR"/.claude/deep-plan/*.md; do
    [ -e "$f" ] || continue
    rel="${f#"$PROJECT_DIR"/}"
    printf '%s\n' "$changed" | grep -Fq "$rel" || continue
    checked=$((checked+1))
    if is_complete_contract "$f"; then found_complete=1; break; fi
  done
fi

# 5b. Plans describing THIS branch's change. The versioned intent dir first (where /refine writes
# <slug>/plan.md; override the dir with DEEP_PLAN_INTENT_DIR), then the gitignored repo-local
# .claude/.plans/, then the global ~/.claude/plans/. "Belongs" means the plan references a file this
# branch actually changes — not merely one that exists in the repo.
scan_plans_dir() {
  local plans_dir="$1"
  local glob="${2:-*.md}"
  [ -d "$plans_dir" ] || return 0
  while IFS= read -r f; do
    [ -e "$f" ] || continue
    grep -Eq '^##+ *Contract' "$f" 2>/dev/null || continue
    belongs=0
    while IFS= read -r tok; do
      [ -n "$tok" ] || continue
      if printf '%s\n' "$changed" | grep -Fq "$tok"; then belongs=1; break; fi
    done < <(grep -oE '[A-Za-z0-9_./-]+/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+' "$f" 2>/dev/null | sort -u | head -40)
    [ "$belongs" -eq 1 ] || continue
    checked=$((checked+1))
    if is_complete_contract "$f"; then found_complete=1; return 0; fi
  done < <(ls -t "$plans_dir"/$glob 2>/dev/null | head -10)
}

[ "$found_complete" -eq 0 ] && scan_plans_dir "$PROJECT_DIR/${DEEP_PLAN_INTENT_DIR:-intent}" "*/plan.md"
[ "$found_complete" -eq 0 ] && scan_plans_dir "$PROJECT_DIR/.claude/.plans"
[ "$found_complete" -eq 0 ] && scan_plans_dir "$HOME/.claude/plans"

if [ "$found_complete" -eq 1 ]; then
  exit 0
fi

# --- 6. Block --------------------------------------------------------------
touched="$(printf '%s' "$domains" | tr '\n' ',' | sed 's/,$//')"
cat >&2 <<EOF
🚫 deep-plan PR gate: blocked.

This branch touches sensitive domain(s) ($touched) but no COMPLETE
plan-contract was found (checked $checked candidate(s)).

Before opening this PR:
  1. Author/finish the plan-contract — run /deep-plan (it fills the interaction matrix,
     dimension table, and precondition diff, and gates on completeness).
  2. Run /verify-plan and resolve every Missing / Diverged / unresolved-GAP.
  3. Make the contract discoverable: deep-plan's "write artifacts" option, or commit it
     under .claude/deep-plan/<branch>.md.

A complete contract has a "## Contract" block and no incompleteness markers: no
"Gate: FAIL", "Residual GAPs: 0" (when present), no bare "| GAP |" cells.

Sensitive domains are read from docs/agents/skills-config.md › Sensitive domains. To
override for a genuine exception (audited): add [deep-plan-override: <reason>] to the
gh pr create args, or set DEEP_PLAN_OVERRIDE=<reason>.
EOF
exit 2
