#!/usr/bin/env bash
# export_library.sh -- batch driver for THE EXPORTER (GI bullet-3).
#
# Exports EVERY game under scenes/games/ that has a replayable witness into one dataset
# root (<out>/<slug>/<seed>/...). Each game becomes an sbatch job on mit_preemptable
# (--requeue, logs to /orcd/scratch/orcd/008/enaha/gi/logs/), OR runs inline with --local.
#
# Usage:
#   scripts/export_library.sh <out_dataset_root> [--limit N] [--local] [--dry-run] \
#                             [--args "<extra game-export flags>"]
#
# A game is exportable iff it has a witness: witness.json beside the game, a trained
# demo_trajectory.json (beside the game or in round_*/g3/), i.e. anything the exporter's
# witness resolver can pick up. The manifest.jsonl at <out> is appended per episode.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GI_REPO="${GI_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
# scenes/games is gitignored (generated artifacts), so a fresh worktree has none; fall back
# to the deployment tree's games dir. Override GAMES_DIR to point anywhere else.
GAMES_DIR="${GAMES_DIR:-$GI_REPO/scenes/games}"
[ -d "$GAMES_DIR" ] || GAMES_DIR="$HOME/gi/scenes/games"

OUT="${1:?usage: export_library.sh <out_dataset_root> [--limit N] [--local] [--dry-run] [--args \"...\"]}"
shift || true
LIMIT=0; LOCAL=0; DRYRUN=0; EXPORT_ARGS=""
while [ $# -gt 0 ]; do
  case "$1" in
    --limit) LIMIT="$2"; shift 2 ;;
    --local) LOCAL=1; shift ;;
    --dry-run) DRYRUN=1; shift ;;
    --args) EXPORT_ARGS="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$OUT"

has_witness() {
  local d="$1"
  [ -f "$d/witness.json" ] && return 0
  [ -f "$d/demo_trajectory.json" ] && return 0
  ls "$d"/round_*/g3/demo_trajectory.json >/dev/null 2>&1 && return 0
  return 1
}

n=0; submitted=0
for d in "$GAMES_DIR"/*/; do
  d="${d%/}"
  slug="$(basename "$d")"
  game="$d/$slug.gd"
  [ -f "$game" ] || continue
  has_witness "$d" || continue
  n=$((n+1))
  if [ "$LIMIT" -gt 0 ] && [ "$submitted" -ge "$LIMIT" ]; then break; fi
  # 3D games get the chase cam (overview shrinks the action to specks -- same rule as QA).
  args="$EXPORT_ARGS"
  grep -q "PhysicsServer3D" "$game" && args="$args --follow"
  if [ "$DRYRUN" = 1 ]; then
    echo "would export: $slug  (args:$args)"
  elif [ "$LOCAL" = 1 ]; then
    echo "=== exporting (local): $slug"
    GI_REPO="$GI_REPO" bash "$SCRIPT_DIR/export_demo.sh" "$game" "$OUT" $args
  else
    sbatch --job-name="exp-$slug" \
      --export=ALL,GAME="$game",OUT="$OUT",GI_REPO="$GI_REPO",EXPORT_ARGS="$args" \
      "$SCRIPT_DIR/export_game.sbatch"
  fi
  submitted=$((submitted+1))
done
echo "=== export_library: $submitted game(s) $([ "$DRYRUN" = 1 ] && echo listed || ([ "$LOCAL" = 1 ] && echo run || echo submitted)) of $n exportable -> $OUT"
