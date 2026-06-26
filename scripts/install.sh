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

echo ""
echo "Done. Next:"
echo "  1. /setup       — write docs/agents/skills-config.md for this repo"
echo "  2. /bootstrap   — scaffold CORE_TENETS + premises (skip if they exist)"
echo ""
echo "Note: the PR-gate hook is copied to .claude/hooks/ but NOT wired up. To enable it,"
echo "add a PreToolUse(Bash) hook for it in $TARGET/.claude/settings.json."
