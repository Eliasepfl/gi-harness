#!/bin/bash
#SBATCH -p mit_preemptable
#SBATCH --requeue
#SBATCH -c 4
#SBATCH --mem=8G
#SBATCH -t 00:40:00
#SBATCH -o /orcd/scratch/orcd/008/enaha/gi/logs/export-%j.log
# THE EXPORTER, one game -> one episode package. Renders the pixel channel via the
# software-GL capture lane inside gi-certifier.sif (Xvfb supplied by export_demo.sh).
# env: GAME (abs .gd), OUT (abs dataset root), GI_REPO (repo/worktree root),
#      EXPORT_ARGS (extra `game export` flags, e.g. --width 640 --height 360)
set -x
module load apptainer/1.4.2 2>/dev/null || true
GI_REPO="${GI_REPO:-$HOME/gi}"
echo "=== EXPORT: game=$GAME out=$OUT repo=$GI_REPO"
GI_REPO="$GI_REPO" bash "$GI_REPO/scripts/export_demo.sh" "$GAME" "$OUT" ${EXPORT_ARGS:-}
echo "=== EXPORT_SBATCH_DONE rc=$?"
