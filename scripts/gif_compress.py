#!/usr/bin/env python
"""Post-hoc GIF compressor (Pillow-only; gifsicle/ffmpeg absent on the login node).

Three levers, in order of impact on a demo GIF:
  1. palette quantization  — GIF is palette-based; MEDIANCUT to N colours is the big win.
  2. frame cap via stride  — long episodes (297 ticks) are dropped to <=max_frames for VIEWING
                             (the mother DATASET uses the per-tick PNGs, never these GIFs).
  3. downscale             — cap the long edge.
A shared palette (quantized from a mid-episode frame, remapped onto every frame) avoids
inter-frame palette flicker that per-frame quantization causes.
"""
import sys
from pathlib import Path
from PIL import Image, ImageSequence


def compress_gif(src, dst, *, max_frames=120, max_width=480, colors=96, fps=15):
    im = Image.open(src)
    frames = [f.convert("RGB") for f in ImageSequence.Iterator(im)]
    n = len(frames)
    if n > max_frames:                                   # stride to the cap
        frames = [frames[int(i * n / max_frames)] for i in range(max_frames)]
    if frames and frames[0].width > max_width:           # downscale
        for i, f in enumerate(frames):
            h = int(f.height * max_width / f.width)
            frames[i] = f.resize((max_width, h), Image.LANCZOS)
    # one shared adaptive palette (mid frame) -> remap all -> no flicker
    base = frames[len(frames) // 2].quantize(colors=colors, method=Image.MEDIANCUT)
    pal = [f.quantize(palette=base, dither=Image.FLOYDSTEINBERG) for f in frames]
    dur = max(1, int(round(1000.0 / max(1, fps))))
    pal[0].save(dst, save_all=True, append_images=pal[1:], duration=dur,
                loop=0, optimize=True, disposal=2)
    return Path(src).stat().st_size, Path(dst).stat().st_size, n, len(pal)


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else src.replace(".gif", ".min.gif")
    kw = dict(a.split("=") for a in sys.argv[3:] if "=" in a)
    kw = {k: int(v) for k, v in kw.items()}
    old, new, nf, kept = compress_gif(src, dst, **kw)
    print(f"{Path(src).name}: {old/1e6:.2f}M -> {new/1e6:.2f}M  ({old/new:.1f}x)  frames {nf}->{kept}")
