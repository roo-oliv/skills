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
# hooks it was supposed to add. This merges hooks per event and per matcher, adds each `env` key and
# each top-level key only when the target does not already have it, and skips a command that is
# already wired — running the installer twice changes nothing the second time.
if [ -d "$SRC/settings" ]; then
  python3 - "$SRC" "$DEST/settings.json" "$TARGET" <<'SETTINGS_MERGE'
import json
import os
import sys

src, target_path, repo = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, os.path.join(src, "ci"))

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


def load(name):
    path = os.path.join(src, "settings", name)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def clone(value):
    return json.loads(json.dumps(value))


added = 0

# Telemetry lane B (OTLP) is opt-in: `## Telemetry` › `Lanes` decides, and a config that names an
# endpoint or a key variable counts as declaring it. Lane A (the trailers + the load log) needs no env
# block at all, so a git-only repo gets no OTLP variables written into its settings.
# When lane B IS on, the destination is the repo's decision, not this installer's: read it from the
# config. One vendor's header is a documented example there, never a default.
env_fragment = load("env.json")
lanes = "git-only"
try:
    import skills_config

    config = skills_config.load(repo)
    lanes = config.telemetry_lanes
    if not config.otlp_enabled:
        env_fragment = {}
    else:
        env_block = env_fragment.get("env") or {}
        env_block["OTEL_EXPORTER_OTLP_ENDPOINT"] = config.otel_endpoint
        env_block["OTEL_EXPORTER_OTLP_PROTOCOL"] = config.otel_protocol
        env_block["OTEL_RESOURCE_ATTRIBUTES"] = (
            config.otel_resource_attributes or "repo=%s" % os.path.basename(os.path.abspath(repo))
        )
except Exception:  # noqa: BLE001 — a config that cannot be read leaves the fragment's own defaults
    pass

for fragment in (load("hooks.json"), env_fragment):
    for key, value in fragment.items():
        if key == "hooks":
            hooks = target.setdefault("hooks", {})
            for event, groups in (value or {}).items():
                existing = hooks.setdefault(event, [])
                for group in groups:
                    matcher = group.get("matcher")
                    twin = next((g for g in existing if g.get("matcher") == matcher), None)
                    if twin is None:
                        existing.append(clone(group))
                        added += len(group.get("hooks") or [])
                        continue
                    commands = {h.get("command") for h in twin.setdefault("hooks", [])}
                    for hook in group.get("hooks") or []:
                        if hook.get("command") not in commands:
                            twin["hooks"].append(clone(hook))
                            added += 1
        elif isinstance(value, dict):
            # `env` and friends: a key the consuming repo already set always wins.
            block = target.setdefault(key, {})
            if isinstance(block, dict):
                for name, entry in value.items():
                    if name not in block:
                        block[name] = clone(entry)
                        added += 1
        elif key not in target:
            target[key] = clone(value)
            added += 1

os.makedirs(os.path.dirname(target_path), exist_ok=True)
with open(target_path, "w", encoding="utf-8", newline="\n") as handle:
    json.dump(target, handle, indent=2)
    handle.write("\n")
print("  settings  %d entry(ies) added to %s" % (added, target_path) if added else "  settings  already wired")
print("  settings  telemetry lane(s): %s%s" % (lanes, "" if lanes != "git-only" else " (no OTLP env block written)"))
SETTINGS_MERGE
fi

echo ""
echo "Done. Next:"
echo "  1. /setup       — write docs/agents/skills-config.md for this repo"
echo "  2. /bootstrap   — scaffold CORE_TENETS + premises + the lifecycle files (skip what exists)"
echo "  3. review .claude/rules/ — the vendored charter and premises rules; delete what you don't want"
echo ""
echo "The context hooks, the PR gate and the telemetry logger are now wired in .claude/settings.json."
echo "To silence them:"
echo "  CONTEXT_HOOKS_DISABLE=all   (or a comma list: remind,nudge,mark,query,charter,gate)"
echo "  telemetry: drop the two agent_telemetry.py entries, or unset CLAUDE_CODE_ENABLE_TELEMETRY"
echo ""
echo "Telemetry exports to \$OTEL_EXPORTER_OTLP_ENDPOINT and reads its key from a GitHub Actions"
echo "variable; with no key it stays silent. See docs/telemetry.md of the skills repo."
echo ""
echo "CI step to paste into your pull-request workflow (needs only python3 + git):"
cat <<'YAML'
      - name: Context lint
        env:
          # Empty on a push to the default branch → only the absolute ceilings run.
          CONTEXT_LINT_BASE_REF: ${{ github.event.pull_request.base.sha }}
          LINT_RATCHET_BASE_REF: ${{ github.event.pull_request.base.sha }}
        run: |
          python3 -m unittest discover -s .github/scripts -p '*_test.py'
          python3 .github/scripts/context_lint.py
          python3 .github/scripts/lint_ratchet.py config-rides-alone
YAML
echo ""
echo "Optional: ci/workflows/refactor-ratio.yml.example — the monthly add:delete tripwire."
