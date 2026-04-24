#!/usr/bin/env bash
# Rebuild the .plugin zip(s) for Cowork distribution. Run from repo root.
# Usage:  ./scripts/pack.sh              # packs all plugins under plugins/
#         ./scripts/pack.sh <name>       # packs one named plugin
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$REPO_ROOT/dist"
mkdir -p "$DIST"

pack_one() {
  local name="$1"
  local src="$REPO_ROOT/plugins/$name"
  if [[ ! -d "$src" ]]; then
    echo "❌  plugins/$name does not exist" >&2
    return 1
  fi
  if [[ ! -f "$src/.claude-plugin/plugin.json" ]]; then
    echo "❌  plugins/$name/.claude-plugin/plugin.json missing — not a valid plugin" >&2
    return 1
  fi
  local out="$DIST/$name.plugin"
  rm -f "$out"
  # zip from inside the plugin dir so the zip root has .claude-plugin/ at top level
  (cd "$src" && zip -r "$out" . -x "*.DS_Store" -x "*/__pycache__/*" -x "*.pyc" >/dev/null)
  echo "✅  $out  ($(du -h "$out" | cut -f1))"
}

if [[ $# -eq 0 ]]; then
  # pack every plugin directory
  for d in "$REPO_ROOT"/plugins/*/; do
    pack_one "$(basename "$d")"
  done
else
  pack_one "$1"
fi
