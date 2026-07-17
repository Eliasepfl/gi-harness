#!/bin/bash
#SBATCH -p mit_preemptable
#SBATCH --requeue
#SBATCH -c 4
#SBATCH --mem=12G
#SBATCH -t 01:00:00
#SBATCH -o /orcd/scratch/orcd/008/enaha/gi/logs/export-val-%j.log
# 2-game integration proof for THE EXPORTER: export one 2D + one 3D game, then validate
# the packages end-to-end (structure / reward / t=0 build / loader round-trip) IN the
# capture container. env: GI_REPO (worktree), OUT (dataset root), GAME2D, GAME3D (abs .gd).
set -x
module load apptainer/1.4.2 2>/dev/null || true
GI_REPO="${GI_REPO:?set GI_REPO to the exporter worktree}"
OUT="${OUT:?set OUT to a scratch dataset root}"
GAME2D="${GAME2D:?}"
GAME3D="${GAME3D:?}"
SIF="${GI_SIF:-$HOME/gi/gi-certifier.sif}"
mkdir -p "$OUT"

echo "=== EXPORT 2D: $GAME2D"
GI_REPO="$GI_REPO" bash "$GI_REPO/scripts/export_demo.sh" "$GAME2D" "$OUT" --width 640 --height 360
echo "=== EXPORT 3D: $GAME3D"
GI_REPO="$GI_REPO" bash "$GI_REPO/scripts/export_demo.sh" "$GAME3D" "$OUT" --width 640 --height 360

echo "=== VALIDATE $OUT"
apptainer exec -B /orcd -B "$GI_REPO" -B "$(dirname "$GAME2D")" -B "$(dirname "$GAME3D")" -B "$OUT" \
  "$SIF" bash -lc "cd '$GI_REPO' && HARNESS_OFFLINE=1 python3 scripts/validate_export.py '$OUT'"
echo "=== VAL_DONE rc=$?"
