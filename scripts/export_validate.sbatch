#!/bin/bash
#SBATCH -p mit_preemptable
#SBATCH --requeue
#SBATCH -c 4
#SBATCH --mem=12G
#SBATCH -t 01:00:00
#SBATCH -o /orcd/scratch/orcd/008/enaha/gi/logs/export-val-%j.log
# 2-game integration proof for THE EXPORTER *with NEGATIVES*: for one 2D + one 3D game export a
# BATCH = demo (win, framed) + N random rollouts (state-only negatives) + K perturbed near-misses
# (framed negatives), then validate the whole dataset end-to-end IN the capture container
# (structure / reward / t=0 build / loader round-trip + the negatives asserts: outcome distribution
# not all-success, failure<win return separation, kind-filter round-trip).
# env: GI_REPO (worktree), OUT (dataset root), GAME2D, GAME3D (abs .gd). Optional: NRAND (4),
# HORIZON (200), NPERTURB (2). Each game's winning witness is witness.json beside the .gd.
set -x
module load apptainer/1.4.2 2>/dev/null || true
GI_REPO="${GI_REPO:?set GI_REPO to the exporter worktree}"
OUT="${OUT:?set OUT to a scratch dataset root}"
GAME2D="${GAME2D:?}"
GAME3D="${GAME3D:?}"
SIF="${GI_SIF:-$HOME/gi/gi-certifier.sif}"
NRAND="${NRAND:-4}"
HORIZON="${HORIZON:-200}"
NPERTURB="${NPERTURB:-2}"
# Serve-host port base in the 49xxx range, spread per job to avoid cross-user collisions.
export GIP_PORT_BASE="${GIP_PORT_BASE:-$((49152 + ${SLURM_JOB_ID:-0} % 800))}"
mkdir -p "$OUT"

# demo (framed win) + NRAND random rollouts (state-only) + NPERTURB perturbed (framed near-misses)
echo "=== EXPORT 2D (demo + $NRAND random + $NPERTURB perturbed): $GAME2D"
GI_REPO="$GI_REPO" bash "$GI_REPO/scripts/export_demo.sh" "$GAME2D" "$OUT" --width 640 --height 360 \
  --random-rollouts "$NRAND" --horizon "$HORIZON" \
  --perturb "$(dirname "$GAME2D")/witness.json" "$NPERTURB"
echo "=== EXPORT 3D (demo + $NRAND random + $NPERTURB perturbed): $GAME3D"
GI_REPO="$GI_REPO" bash "$GI_REPO/scripts/export_demo.sh" "$GAME3D" "$OUT" --width 640 --height 360 \
  --random-rollouts "$NRAND" --horizon "$HORIZON" \
  --perturb "$(dirname "$GAME3D")/witness.json" "$NPERTURB"

echo "=== VALIDATE $OUT"
apptainer exec -B /orcd -B "$GI_REPO" -B "$(dirname "$GAME2D")" -B "$(dirname "$GAME3D")" -B "$OUT" \
  "$SIF" bash -lc "cd '$GI_REPO' && HARNESS_OFFLINE=1 python3 scripts/validate_export.py '$OUT'"
echo "=== VAL_DONE rc=$?"
