#!/usr/bin/env bash
# generate_missing_readmes.sh — for every direct subfolder of <kb-root>, if
# it's missing README.md, write a stub README.md the user can edit before sync.
#
# This is the safety net for non-technical users who haven't internalized the
# bundle-README convention. They drop their notes into a folder, run this, and
# end up with citation-aware READMEs ready to fill in.
#
# Usage:  bash generate_missing_readmes.sh /path/to/kb-root
# Flag:   --force   overwrite existing READMEs (don't use this casually)

set -euo pipefail

FORCE=0
ROOT=""
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help)
      sed -n '2,/^$/p' "$0" | sed 's/^# //'
      exit 0 ;;
    *) ROOT="$arg" ;;
  esac
done

if [[ -z "$ROOT" ]]; then
  echo "Usage: $0 <kb-root> [--force]" >&2
  exit 64
fi
ROOT="$(cd "$ROOT" && pwd)"

CREATED=0
SKIPPED=0

for dir in "$ROOT"/*/; do
  [[ ! -d "$dir" ]] && continue
  SLUG="$(basename "$dir")"
  README="$dir/README.md"

  if [[ -f "$README" && $FORCE -eq 0 ]]; then
    SKIPPED=$((SKIPPED+1))
    continue
  fi

  # Heuristic: pick a friendly title from the slug
  TITLE=$(echo "$SLUG" | sed -E 's/^[0-9]{4}-[0-9]{2}-[0-9]{2}_//; s/[-_]/ /g; s/\b(.)/\u\1/g')

  cat > "$README" <<EOF
# $TITLE

> ⚠ THIS IS A STUB README. Edit the **bracketed sections** below before publishing
> the bundle. AI agents will read this file every time they cite content from
> this bundle, so it's worth getting right.

## What is this?

[One paragraph describing what this bundle contains. Who made it? When? What
question does it answer? What's the headline finding?]

## What's in it?

[Layout map of the files in this folder. List the most important files first.
For research bundles, mention key documents like INSIGHTS.md, EXEC_BRIEFING.md,
TOP_N.md. For course/transcript bundles, mention the structure of the data file.]

## How do I cite findings from this bundle?

[Authorship rules: who can be named as the author? If multiple contributors,
what's the safe neutral attribution? E.g.: "In our 2026 cover-letter research,
we found..." rather than "Vadym says..." unless verified.]

[Sample size and dates: when was the data collected? How many records does
the analysis rest on? What's the validation status?]

## What's off-limits?

[Numbers that must NEVER appear in published content: customer counts, internal
identifiers, anything that could leak business-sensitive info. List them
explicitly so agents can audit their own output.]

## Authority level

[When does this source override public-web sources? When (rarely) does it not?
For first-party data, it usually overrides everything. For internal opinion, it
may only carry weight inside the team.]

## Maintenance

[How and when does this bundle get refreshed? Annually? On demand? Who owns it?]

---
**Bundle slug:** \`$SLUG\`
**Created:** $(date +%Y-%m-%d)
**Last edited:** _fill in when you finish editing this README_
EOF

  echo "  + $SLUG/README.md (stub)"
  CREATED=$((CREATED+1))
done

echo
if [[ $CREATED -eq 0 && $SKIPPED -eq 0 ]]; then
  echo "  No subfolders found in $ROOT."
elif [[ $CREATED -eq 0 ]]; then
  echo "  All bundles have READMEs already ($SKIPPED checked, 0 created)."
else
  echo "  ✓ created $CREATED stub README(s), skipped $SKIPPED existing."
  echo
  echo "  NEXT STEP: open each generated README.md and fill in the bracketed sections"
  echo "  before running scripts/05_sync_folder_to_r2.sh."
  echo "  AI agents will use these to cite your content correctly."
fi
