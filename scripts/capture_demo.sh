#!/usr/bin/env bash
# capture_demo.sh -- ORCD host-side orchestration for the in-engine demo-capture lane.
#
# The capture host renders with software GL (Godot x11 + opengl3 + Mesa llvmpipe), which
# needs (a) an X display and (b) the x11 CLIENT libs Godot dlopens -- NEITHER of which is
# in gi-certifier.sif (it is deliberately pixel-free). Rather than rebuild the .sif, this
# wrapper supplies both from the HOST at runtime, with zero build:
#   * starts an Xvfb virtual display on the host (host has Xvfb + all deps natively),
#   * stages the host's x11 client libs into a small bind dir (Godot loads them via
#     LD_LIBRARY_PATH; container glibc >= host glibc so they load fine),
#   * runs `python -m harness game capture ...` INSIDE the container with the X socket +
#     lib dir bound and DISPLAY exported.
# See notes/engines/DEMO_CAPTURE_LANE.md. For a container that already bundles Xvfb + x11
# (gi-capture.sif, see gi-capture.def), skip this wrapper: just `apptainer exec` the harness
# with HARNESS_CAPTURE_XVFB=1.
#
# Usage:
#   scripts/capture_demo.sh <game.gd> <out.gif> [extra `harness game capture` args...]
# Env overrides:
#   GI_SIF   (default ~/gi/gi-certifier.sif)   GI_REPO (default: this worktree root)
#   GI_GODOT (default /opt/godot/godot in-image)
set -euo pipefail

GAME="${1:?usage: capture_demo.sh <game.gd> <out.gif> [args...]}"
OUT="${2:?usage: capture_demo.sh <game.gd> <out.gif> [args...]}"
shift 2 || true
EXTRA=("$@")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GI_REPO="${GI_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
GI_SIF="${GI_SIF:-$HOME/gi/gi-certifier.sif}"
GI_GODOT="${GI_GODOT:-/opt/godot/godot}"
GAME_ABS="$(readlink -f "$GAME")"
GAME_DIR="$(dirname "$GAME_ABS")"
OUT_ABS="$(readlink -f "$OUT" 2>/dev/null || echo "$OUT")"
OUT_DIR="$(dirname "$OUT_ABS")"
mkdir -p "$OUT_DIR"

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

echo "capture: game=$GAME_ABS out=$OUT_ABS display=$DISP repo=$GI_REPO"
apptainer exec \
  -B /orcd -B /tmp/.X11-unix -B "$GI_REPO" -B "$GAME_DIR" -B "$OUT_DIR" -B "$X11LIB" \
  "$GI_SIF" bash -lc "
    export HARNESS_GODOT_EXE='$GI_GODOT'
    export DISPLAY='$DISP'
    export LIBGL_ALWAYS_SOFTWARE=1
    export LD_LIBRARY_PATH='$X11LIB':\${LD_LIBRARY_PATH:-}
    cd '$GI_REPO'
    python3 -m harness game capture '$GAME_ABS' --out '$OUT_ABS' ${EXTRA[*]:-}
  "
