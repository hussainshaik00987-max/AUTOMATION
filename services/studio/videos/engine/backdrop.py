"""Pre-rendered stage backgrounds.

The background was originally live CSS: two radial glows, a key light, a vignette
and a grain layer blended over a gradient. Measured, that stack cost ~44ms of
every 122ms frame, because the compositor re-evaluated all of it on each capture
even though it barely changes.

Baking it to a JPEG once turns that into a single blit. It is also a better
picture: the falloffs here are true Gaussians rather than piecewise-linear CSS
gradient stops, and the grain is real noise instead of a tiled PNG under a blend
mode.

Backgrounds vary by vertical and by a variant index, not per lead — nothing in
them is lead-specific, and a handful of shared files keeps them in the browser's
cache across a whole batch.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
from PIL import Image

W, H = 1080, 1920
VARIANTS = 4

# base, glow A, glow B — deep enough that white type sits at >12:1 contrast.
PALETTES = {
    "wedding_photo":   ("#0b0f16", "#22497c", "#5c2c58"),
    "bakery":          ("#130f0a", "#6b3f1c", "#75501f"),
    "wedding_planner": ("#130c14", "#63284f", "#472860"),
    # Rose rather than violet: wedding_planner already owns the purple end, and
    # two verticals opening on the same wash defeats the point of having five.
    "fashion":         ("#140d11", "#6b2440", "#7a3a2a"),
    "interior_arch":   ("#0c110f", "#254c3f", "#3d4d26"),
}


def _rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def _blob(xx: np.ndarray, yy: np.ndarray, cx: float, cy: float,
          radius: float, softness: float = 1.0) -> np.ndarray:
    """Gaussian falloff in [0,1]. `radius` is where it reaches ~0.6."""
    d2 = (xx - cx) ** 2 + (yy - cy) ** 2
    return np.exp(-d2 / (2.0 * (radius * softness) ** 2)).astype(np.float32)


def build(vertical: str, variant: int = 0) -> Image.Image:
    base_hex, a_hex, b_hex = PALETTES.get(vertical, PALETTES["wedding_photo"])
    base, ca, cb = _rgb(base_hex), _rgb(a_hex), _rgb(b_hex)

    rng = np.random.default_rng(
        int(hashlib.sha256(f"{vertical}:{variant}".encode()).hexdigest()[:8], 16))

    y, x = np.mgrid[0:H, 0:W].astype(np.float32)

    img = np.repeat(base[None, None, :], H, 0).repeat(W, 1).copy()

    # Slight top lift so the frame is not a flat slab.
    img += (ca - base)[None, None, :] * (0.16 * (1.0 - y / H)[..., None])

    # Two light pools, placed off-centre and rotated per variant so consecutive
    # leads in the same vertical do not open on an identical frame.
    ang = variant * 0.9
    ax = W * (0.14 + 0.10 * np.cos(ang))
    ay = H * (0.10 + 0.06 * np.sin(ang))
    bx = W * (0.88 - 0.10 * np.cos(ang * 1.4))
    by = H * (0.90 - 0.05 * np.sin(ang * 1.4))

    img += (ca - base)[None, None, :] * (0.95 * _blob(x, y, ax, ay, W * 0.62))[..., None]
    img += (cb - base)[None, None, :] * (0.85 * _blob(x, y, bx, by, W * 0.58))[..., None]

    # Key light behind the device, so its drop shadow has something to fall on.
    key = _blob(x, y, W * 0.5, H * 0.47, W * 0.52)
    img += 26.0 * key[..., None]

    # Vignette, elliptical to match a 9:16 frame.
    r = np.sqrt(((x - W / 2) / (W * 0.78)) ** 2 + ((y - H * 0.46) / (H * 0.62)) ** 2)
    img *= np.clip(1.0 - 0.46 * np.clip(r - 0.55, 0, None) ** 1.7, 0.25, 1.0)[..., None]

    # Grain. Large flat gradients posterise into visible bands once x264
    # quantises them; a couple of levels of noise dithers the ramp away for far
    # less bitrate than the quality bump that would otherwise be needed.
    img += rng.normal(0.0, 2.6, (H, W, 1)).astype(np.float32)

    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def path_for(out_dir: Path, vertical: str, variant: int) -> Path:
    return Path(out_dir) / f"{vertical}-{variant}.jpg"


def ensure(out_dir: Path, force: bool = False) -> dict[tuple[str, int], Path]:
    """Render every (vertical, variant) that is missing. -> lookup table."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    table: dict[tuple[str, int], Path] = {}
    for vertical in PALETTES:
        for variant in range(VARIANTS):
            dest = path_for(out_dir, vertical, variant)
            if force or not dest.exists():
                build(vertical, variant).save(dest, "JPEG", quality=94,
                                              subsampling=0, optimize=True)
            table[(vertical, variant)] = dest
    return table


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "out/bg")
    made = ensure(target, force=True)
    total = sum(p.stat().st_size for p in made.values())
    print(f"{len(made)} backgrounds -> {target}  ({total/1e6:.1f} MB)")
