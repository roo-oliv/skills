#!/usr/bin/env bash
# Vendor these skills into a target repo's .claude/.
#
# Copies skills/ + workflows/ + hooks/ into <target>/.claude/ so the workflow-backed
# skills resolve their .js inside the consuming repo. Re-run to update — it overwrites
# the vendored skill/workflow/hook files but never touches anything else under .claude/
# (your settings.json, your own skills, the gitignored scratch dirs).
#
# Usage:
#   scripts/install.sh /path/to/target/repo
#   scripts/install.sh                      # defaults to $PWD if it's a git repo
#
# After installing, run /setup in the target repo to write docs/agents/skills-config.md,
# then /bootstrap if it has no CORE_TENETS / premises yet.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${1:-$PWD}"

# Resolve the target's repo root (so running from a subdir still works).
if root="$(cd "$TARGET" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)"; then
  TARGET="$root"
else
  echo "error: '$TARGET' is not inside a git repository." >&2
  echo "usage: $0 /path/to/target/repo" >&2
  exit 1
fi

if [ "$TARGET" = "$SRC" ]; then
  echo "error: target is this skills repo itself — pick a consuming repo." >&2
  exit 1
fi

DEST="$TARGET/.claude"
echo "Installing skills from $SRC"
echo "                    into $DEST"
mkdir -p "$DEST/skills" "$DEST/workflows" "$DEST/hooks"

# Per-skill copy (so a removed-upstream file in one skill doesn't linger, but other
# .claude/skills the target owns are left alone).
for dir in "$SRC"/skills/*/; do
  name="$(basename "$dir")"
  rm -rf "$DEST/skills/$name"
  cp -R "$dir" "$DEST/skills/$name"
  echo "  skill    $name"
done

for f in "$SRC"/workflows/*.js; do
  [ -e "$f" ] || continue
  cp "$f" "$DEST/workflows/$(basename "$f")"
  echo "  workflow $(basename "$f")"
done

for f in "$SRC"/hooks/*; do
  [ -e "$f" ] || continue
  cp "$f" "$DEST/hooks/$(basename "$f")"
  chmod +x "$DEST/hooks/$(basename "$f")" 2>/dev/null || true
  echo "  hook     $(basename "$f")"
done

for f in "$SRC"/rules/*.md; do
  [ -e "$f" ] || continue
  mkdir -p "$DEST/rules"
  cp "$f" "$DEST/rules/$(basename "$f")"
  echo "  rule     $(basename "$f")"
done

# The context toolkit: scripts CI runs, plus a copy of the config reader beside the hooks (a hook
# resolves its imports from its own directory — .github/scripts is not on its path).
for f in "$SRC"/ci/*.py; do
  [ -e "$f" ] || continue
  case "$(basename "$f")" in *_test.py) continue ;; esac
  mkdir -p "$TARGET/.github/scripts"
  cp "$f" "$TARGET/.github/scripts/$(basename "$f")"
  echo "  script   .github/scripts/$(basename "$f")"
done
if [ -f "$SRC/ci/skills_config.py" ] && [ -f "$DEST/hooks/context_hooks.py" ]; then
  cp "$SRC/ci/skills_config.py" "$DEST/hooks/skills_config.py"
  echo "  script   .claude/hooks/skills_config.py (config reader, beside the hooks)"
fi
for f in "$SRC"/ci/*_test.py; do
  [ -e "$f" ] || continue
  mkdir -p "$TARGET/.github/scripts"
  cp "$f" "$TARGET/.github/scripts/$(basename "$f")"
  echo "  test     .github/scripts/$(basename "$f")"
done

# --- settings.json: merge, never concatenate -------------------------------------------------
# A duplicate key is VALID JSON in which the last one wins, so a textual merge silently drops the
# hooks it was supposed to add. This merges per event and per matcher, and skips a command that is
# already wired — running the installer twice changes nothing the second time.
if [ -f "$SRC/settings/hooks.json" ]; then
  python3 - "$SRC/settings/hooks.json" "$DEST/settings.json" <<'PY'
import json
import os
import sys

fragment_path, target_path = sys.argv[1], sys.argv[2]
with open(fragment_path, encoding="utf-8") as handle:
    fragment = json.load(handle)

target = {}
if os.path.exists(target_path):
    try:
        with open(target_path, encoding="utf-8") as handle:
            target = json.load(handle)
    except ValueError as error:
        print("  settings  SKIPPED — %s is not valid JSON (%s); merge it by hand" % (target_path, error))
        raise SystemExit(0)
    if not isinstance(target, dict):
        print("  settings  SKIPPED — %s is not a JSON object; merge it by hand" % target_path)
        raise SystemExit(0)

hooks = target.setdefault("hooks", {})
added = 0
for event, groups in (fragment.get("hooks") or {}).items():
    existing = hooks.setdefault(event, [])
    for group in groups:
        matcher = group.get("matcher")
        twin = next((g for g in existing if g.get("matcher") == matcher), None)
        if twin is None:
            existing.append(json.loads(json.dumps(group)))
            added += len(group.get("hooks") or [])
            continue
        commands = {h.get("command") for h in twin.setdefault("hooks", [])}
        for hook in group.get("hooks") or []:
            if hook.get("command") not in commands:
                twin["hooks"].append(json.loads(json.dumps(hook)))
                added += 1

os.makedirs(os.path.dirname(target_path), exist_ok=True)
with open(target_path, "w", encoding="utf-8", newline="\n") as handle:
    json.dump(target, handle, indent=2)
    handle.write("\n")
print("  settings  %d hook(s) added to %s" % (added, target_path) if added else "  settings  already wired")
PY
fi

echo ""
echo "Done. Next:"
echo "  1. /setup       — write docs/agents/skills-config.md for this repo"
echo "  2. /bootstrap   — scaffold CORE_TENETS + premises + the lifecycle files (skip what exists)"
echo "  3. review .claude/rules/ — the vendored charter and premises rules; delete what you don't want"
echo ""
echo "The context hooks and the PR gate are now wired in .claude/settings.json. To silence them:"
echo "  CONTEXT_HOOKS_DISABLE=all   (or a comma list: remind,nudge,mark,query,charter,gate)"
echo ""
echo "CI step to paste into your pull-request workflow (needs only python3 + git):"
cat <<'YAML'
      - name: Context lint
        env:
          # Empty on a push to the default branch → only the absolute ceilings run.
          CONTEXT_LINT_BASE_REF: ${{ github.event.pull_request.base.sha }}
        run: |
          python3 -m unittest discover -s .github/scripts -p '*_test.py'
          python3 .github/scripts/context_lint.py
YAML
