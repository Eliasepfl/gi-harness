#!/usr/bin/env bash
# export_demo.sh -- ORCD host-side orchestration for THE EXPORTER (GI bullet-3).
#
# Exports ONE certified game's witness replay to an EPISODE PACKAGE
# (<out>/<slug>/<seed>/{episode.json,steps.jsonl,frames/*.png} + a manifest.jsonl line).
# The exporter renders the PIXEL channel via the same software-GL capture lane as
# scripts/capture_demo.sh, so it needs (a) an X display and (b) the x11 client libs Godot
# dlopens -- NEITHER of which is in the pixel-free gi-certifier.sif. This wrapper supplies
# both from the HOST at runtime (Xvfb + staged libs), exactly like capture_demo.sh, then
# runs `python -m harness game export ...` INSIDE the container.
#
# Usage:
#   scripts/export_demo.sh <game.gd> <out_dir> [extra `harness game export` args...]
# Env overrides:
#   GI_SIF   (default ~/gi/gi-certifier.sif)   GI_REPO (default: this worktree root)
#   GI_GODOT (default /opt/godot/godot in-image)
set -euo pipefail

GAME="${1:?usage: export_demo.sh <game.gd> <out_dir> [args...]}"
OUT="${2:?usage: export_demo.sh <game.gd> <out_dir> [args...]}"
shift 2 || true
EXTRA=("$@")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GI_REPO="${GI_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
GI_SIF="${GI_SIF:-$HOME/gi/gi-certifier.sif}"
GI_GODOT="${GI_GODOT:-/opt/godot/godot}"
GAME_ABS="$(readlink -f "$GAME")"
GAME_DIR="$(dirname "$GAME_ABS")"
OUT_ABS="$(readlink -f "$OUT" 2>/dev/null || echo "$OUT")"
mkdir -p "$OUT_ABS"

command -v apptainer >/dev/null || module load apptainer/1.4.2 2>/dev/null || true
command -v Xvfb >/dev/null || { echo "ERROR: Xvfb not on host PATH" >&2; exit 1; }

# --- stage the x11 client libs Godot dlopens (cached under ~/.cache) ---------
X11LIB="${GI_X11LIB:-$HOME/.cache/gi-capture/x11libs}"
mkdir -p "$X11LIB"
NEED=(libX11.so.6 libXcursor.so.1 libXinerama.so.1 libXext.so.6 libXrandr.so.2 \
      libXrender.so.1 libXi.so.6 libxkbcommon.so.0 libxkbcommon-x11.so.0 \
      libxcb.so.1 libXau.so.6 libXdmcp.so.6 libXfixes.so.3 libxcb-randr.so.0)
for l in "${NEED[@]}"; do
  [ -f "$X11LIB/$l" ] && continue
  src="$(ls /usr/lib64/$l 2>/dev/null | head -1 || true)"
  [ -z "$src" ] && src="$(ls /usr/lib/x86_64-linux-gnu/$l 2>/dev/null | head -1 || true)"
  [ -n "$src" ] && cp -L "$src" "$X11LIB/$l" || echo "WARN: host lib $l not found" >&2
done

# --- pick a free display + start Xvfb ----------------------------------------
DISP=""
for n in $(seq 90 140); do
  [ -e "/tmp/.X11-unix/X$n" ] || { DISP=":$n"; break; }
done
[ -z "$DISP" ] && DISP=":99"
Xvfb "$DISP" -screen 0 1400x1000x24 -nolisten tcp >/dev/null 2>&1 &
XVFB_PID=$!
cleanup() { kill "$XVFB_PID" 2>/dev/null || true; }
trap cleanup EXIT
sleep 2

echo "export: game=$GAME_ABS out=$OUT_ABS display=$DISP repo=$GI_REPO"
apptainer exec \
  -B /orcd -B /tmp/.X11-unix -B "$GI_REPO" -B "$GAME_DIR" -B "$OUT_ABS" -B "$X11LIB" \
  "$GI_SIF" bash -lc "
    export HARNESS_GODOT_EXE='$GI_GODOT'
    export DISPLAY='$DISP'
    export LIBGL_ALWAYS_SOFTWARE=1
    export LD_LIBRARY_PATH='$X11LIB':\${LD_LIBRARY_PATH:-}
    # Replay at the SAME game-tick speedup certification ran at (default 1), so the capture
    # host's physics pins match the serve host that produced the witness (byte-faithful).
    export HARNESS_GODOT_SPEEDUP='${HARNESS_GODOT_SPEEDUP:-1}'
    # Serve/capture host port base -- keep in the 49xxx range and spread per job so the
    # random-rollout/perturbation serve host never collides with another user's Godot.
    export GIP_PORT_BASE='${GIP_PORT_BASE:-49152}'
    # Offline asset-routing on a compute node (no OpenRouter key / no egress): keeps the
    # cosmetic bank-asset routing on its deterministic fallback instead of a doomed LLM call.
    export HARNESS_OFFLINE='${HARNESS_OFFLINE:-1}'
    cd '$GI_REPO'
    python3 -m harness game export '$GAME_ABS' --out '$OUT_ABS' ${EXTRA[*]:-}
  "
echo "=== EXPORT_DONE rc=$?"
