#!/usr/bin/env bash
# render_meshes.sh -- render the mesh_lib.gd showcase (car / rock-spire / ring gate) to a PNG,
# proving pure-GDScript low-poly meshes render through the in-image software-GL lane.
#
# It reuses the EXACT host-side Xvfb + x11-client-lib staging that scripts/capture_demo.sh
# uses (the .sif is deliberately pixel-free), but drives godotworld/tests/render_showcase.gd
# instead of the game-capture CLI -- so the picture is purely MeshLib's output (no dresser).
#
# Usage:  harness/demo/render_meshes.sh [out.png] [width] [height]
# Env:    GI_SIF (default ~/gi/gi-certifier.sif)   GI_GODOT (default /opt/godot/godot)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GI_REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"        # harness/demo/ -> repo root
OUT="${1:-$GI_REPO/harness/demo/mesh_proof.png}"
WIDTH="${2:-1100}"
HEIGHT="${3:-680}"
GI_SIF="${GI_SIF:-$HOME/gi/gi-certifier.sif}"
GI_GODOT="${GI_GODOT:-/opt/godot/godot}"
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

echo "render: out=$OUT_ABS ${WIDTH}x${HEIGHT} display=$DISP"
apptainer exec \
  -B /orcd -B /tmp/.X11-unix -B "$GI_REPO" -B "$OUT_DIR" -B "$X11LIB" \
  "$GI_SIF" bash -lc "
    export DISPLAY='$DISP'
    export LIBGL_ALWAYS_SOFTWARE=1
    export LD_LIBRARY_PATH='$X11LIB':\${LD_LIBRARY_PATH:-}
    cd '$GI_REPO'
    taskset -c 0-1 '$GI_GODOT' --display-driver x11 --rendering-driver opengl3 --path godotworld \
      -s res://tests/render_showcase.gd -- --out='$OUT_ABS' --width=$WIDTH --height=$HEIGHT
  "
echo "done: $OUT_ABS"
ls -l "$OUT_ABS"
