"""Procedural SVG artwork — the substitute for photography we do not have.

The leads CSV contains zero images, and every demo still has to look like a real
studio site. Stock-photo CDNs were rejected: they add external requests (these
open on Indian mobile data during a call), they expire, and the same twelve
Unsplash weddings across 2,000 sites reads as a template farm.

So every visual is generated. Five motifs, one per vertical, each with several
variants:

    film     wedding photography  - duotone wash, light leaks, bokeh, sprockets
    icing    bakery               - frosting swirls, scalloped frills, sprinkles
    mandala  wedding planning     - rangoli rotational symmetry, drape swags
    drape    fashion              - fabric folds, colour blocking, halftone
    plan     interior/architecture- perspective rooms, blueprint grid, elevations

Composition is deliberate rather than scattered: forms are placed on thirds and
golden-ratio offsets, and each piece layers background wash -> mid-ground form
-> foreground texture so it reads as depth instead of noise.

Everything is seeded from the business slug, so a studio's art is unique to it
and identical on every rebuild.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .theme import Palette, mix

PHI = 0.6180339887


class Brush:
    """Small deterministic RNG + geometry helpers.

    Uses a SplitMix64-style integer hash rather than `random` so output never
    depends on Python's global RNG state or version.
    """

    __slots__ = ("_state", "uid")

    def __init__(self, seed: int, uid: str) -> None:
        self._state = seed & 0xFFFFFFFFFFFFFFFF
        self.uid = uid

    def _next(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & 0xFFFFFFFFFFFFFFFF
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
        return z ^ (z >> 31)

    def rand(self) -> float:
        return self._next() / 0xFFFFFFFFFFFFFFFF

    def uniform(self, lo: float, hi: float) -> float:
        return lo + (hi - lo) * self.rand()

    def randint(self, lo: int, hi: int) -> int:
        return lo + self._next() % (hi - lo + 1)

    def pick(self, seq):
        return seq[self._next() % len(seq)]

    def chance(self, probability: float) -> bool:
        return self.rand() < probability

    def ident(self, tag: str) -> str:
        """Namespaced element id — several SVGs share a page and gradient ids collide."""
        return f"{tag}-{self.uid}"


def _f(value: float) -> str:
    """Compact float for SVG output (keeps generated pages small)."""
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _pts(points) -> str:
    return " ".join(f"{_f(x)},{_f(y)}" for x, y in points)


@dataclass(frozen=True, slots=True)
class Canvas:
    w: float
    h: float

    @property
    def cx(self) -> float:
        return self.w / 2

    @property
    def cy(self) -> float:
        return self.h / 2


# ==========================================================================
# shared layers
# ==========================================================================
def _wash(b: Brush, c: Canvas, pal: Palette, dark: bool, punch: float = 0.0) -> str:
    """Background gradient — the base every motif sits on.

    `punch` (0-1) deepens the ramp. Motifs that layer bright forms on top (film,
    plan) need a darker base or the forms wash out; motifs that are mostly form
    (mandala, icing) keep the softer default.
    """
    gid = b.ident("w")
    a0, a1, a2 = pal.art
    if dark:
        top, mid, bottom = a2, mix(a0, a2, 0.55), mix(a2, "#000000", 0.8)
    else:
        top = mix(a1, "#ffffff", 0.45 - punch * 0.3)
        mid = mix(a0, "#ffffff", 0.55 - punch * 0.35)
        bottom = mix(a0, "#ffffff", 0.25 - punch * 0.25)
    angle = b.uniform(0.0, 1.0)
    return (
        f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="{_f(angle)}" y2="1">'
        f'<stop offset="0" stop-color="{top}"/>'
        f'<stop offset="0.55" stop-color="{mid}"/>'
        f'<stop offset="1" stop-color="{bottom}"/>'
        f"</linearGradient></defs>"
        f'<rect width="{_f(c.w)}" height="{_f(c.h)}" fill="url(#{gid})"/>'
    )


def _bloom(b: Brush, c: Canvas, colour: str, count: int, opacity: float) -> str:
    """Soft blurred light — reads as lens flare, glaze sheen or ambient light."""
    fid = b.ident("bl")
    out = [
        f'<filter id="{fid}" x="-50%" y="-50%" width="200%" height="200%">'
        f'<feGaussianBlur stdDeviation="{_f(c.w * 0.07)}"/></filter>'
        f'<g filter="url(#{fid})" opacity="{_f(opacity)}">'
    ]
    for _ in range(count):
        x = b.uniform(-0.1, 1.1) * c.w
        y = b.uniform(-0.1, 1.1) * c.h
        r = b.uniform(0.12, 0.34) * c.w
        out.append(f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(r)}" fill="{colour}"/>')
    out.append("</g>")
    return "".join(out)


def _vignette(b: Brush, c: Canvas, strength: float = 0.34) -> str:
    gid = b.ident("v")
    return (
        f'<defs><radialGradient id="{gid}" cx="0.5" cy="0.45" r="0.75">'
        f'<stop offset="0.45" stop-color="#000" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="#000" stop-opacity="{_f(strength)}"/>'
        f"</radialGradient></defs>"
        f'<rect width="{_f(c.w)}" height="{_f(c.h)}" fill="url(#{gid})"/>'
    )


# ==========================================================================
# motif: film — wedding photography
# ==========================================================================
def _polygon_ring(cx: float, cy: float, r: float, sides: int, rot: float = 0.0):
    return [
        (cx + math.cos(rot + 2 * math.pi * i / sides) * r,
         cy + math.sin(rot + 2 * math.pi * i / sides) * r)
        for i in range(sides)
    ]


def _film(b: Brush, c: Canvas, pal: Palette, dark: bool, wide: bool = False) -> str:
    a0, a1, a2 = pal.art
    parts = [_wash(b, c, pal, dark, punch=0.55)]
    hot = mix(a1, "#ffffff", 0.62)   # specular highlight
    deep = mix(a2, "#000000", 0.35)  # shadow mass
    # The aperture is a centred composition and all but disappears when a banner
    # crop slices the sides off, so wide bands never draw it.
    variant = b.pick((0, 1, 2)) if wide else b.randint(0, 3)

    if variant == 0:
        # Silhouette horizon — a hard dark landmass against a lit sky. High
        # contrast is the point; the earlier soft version read as fog.
        y = c.h * b.uniform(0.58, 0.74)
        gid = b.ident("sky")
        parts.append(
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{mix(a1, "#ffffff", 0.25)}"/>'
            f'<stop offset="0.7" stop-color="{hot}"/>'
            f'<stop offset="1" stop-color="{mix(a0, "#ffffff", 0.3)}"/>'
            f"</linearGradient></defs>"
            f'<rect width="{_f(c.w)}" height="{_f(y)}" fill="url(#{gid})"/>'
        )
        sun_x = c.w * (PHI if b.chance(0.5) else 1 - PHI)
        sun_y = y * b.uniform(0.62, 0.82)
        parts.append(
            f'<circle cx="{_f(sun_x)}" cy="{_f(sun_y)}" r="{_f(c.w * 0.115)}" '
            f'fill="#ffffff" opacity="0.55"/>'
            f'<circle cx="{_f(sun_x)}" cy="{_f(sun_y)}" r="{_f(c.w * 0.062)}" '
            f'fill="#ffffff" opacity="0.92"/>'
        )
        # Ridge line with a few peaks, then a solid mass beneath it.
        ridge = [f"M-10 {_f(y)}"]
        x = 0.0
        while x < c.w:
            step = c.w * b.uniform(0.14, 0.3)
            ridge.append(
                f"Q {_f(x + step * 0.5)} {_f(y - b.uniform(0.02, 0.11) * c.h)} "
                f"{_f(x + step)} {_f(y + b.uniform(-0.01, 0.02) * c.h)}"
            )
            x += step
        ridge.append(f"L{_f(c.w + 10)} {_f(c.h + 10)} L-10 {_f(c.h + 10)} Z")
        parts.append(f'<path d="{" ".join(ridge)}" fill="{deep}" opacity="0.94"/>')

    elif variant == 1:
        # Bokeh — layered depth: soft wide discs behind, crisp rings in front.
        # Blades match a real iris (hexagonal on most fast primes).
        blades = b.pick((6, 7, 0))  # 0 == round
        fid = b.ident("bk")
        parts.append(
            f'<filter id="{fid}" x="-30%" y="-30%" width="160%" height="160%">'
            f'<feGaussianBlur stdDeviation="{_f(c.w * 0.028)}"/></filter>'
        )
        back = []
        for _ in range(b.randint(5, 8)):
            x, y = b.uniform(-0.05, 1.05) * c.w, b.uniform(-0.05, 1.05) * c.h
            r = b.uniform(0.12, 0.26) * c.w
            back.append(
                f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(r)}" fill="{hot}" '
                f'opacity="{_f(b.uniform(0.3, 0.55))}"/>'
            )
        parts.append(f'<g filter="url(#{fid})">{"".join(back)}</g>')

        for _ in range(b.randint(7, 12)):
            x, y = b.uniform(0, 1) * c.w, b.uniform(0, 1) * c.h
            r = b.uniform(0.045, 0.13) * c.w
            col = b.pick((hot, mix(a1, "#ffffff", 0.35), mix(a0, "#ffffff", 0.5)))
            op = b.uniform(0.4, 0.8)
            if blades:
                pts = _polygon_ring(x, y, r, blades, b.uniform(0, 1))
                parts.append(
                    f'<polygon points="{_pts(pts)}" fill="{col}" opacity="{_f(op * 0.4)}" '
                    f'stroke="{hot}" stroke-width="{_f(r * 0.14)}"/>'
                )
            else:
                parts.append(
                    f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(r)}" fill="{col}" '
                    f'opacity="{_f(op * 0.32)}" stroke="{hot}" '
                    f'stroke-width="{_f(r * 0.16)}"/>'
                )

    elif variant == 2:
        # Light leak — the hard-edged diagonal burn you get from a loose film back.
        parts.append(_bloom(b, c, a1, 2, 0.4))
        gid = b.ident("lk")
        x0 = c.w * b.uniform(-0.1, 0.5)
        width = c.w * b.uniform(0.22, 0.45)
        skew = c.w * b.uniform(0.1, 0.4)
        parts.append(
            f'<defs><linearGradient id="{gid}" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="{hot}" stop-opacity="0"/>'
            f'<stop offset="0.45" stop-color="#ffffff" stop-opacity="0.85"/>'
            f'<stop offset="0.6" stop-color="{hot}" stop-opacity="0.7"/>'
            f'<stop offset="1" stop-color="{a0}" stop-opacity="0"/>'
            f"</linearGradient></defs>"
            f'<polygon points="{_pts([(x0, -10), (x0 + width, -10), (x0 + width + skew, c.h + 10), (x0 + skew, c.h + 10)])}" '
            f'fill="url(#{gid})"/>'
        )
        for _ in range(b.randint(2, 4)):  # flare ghosts along the leak axis
            t = b.uniform(0.15, 0.85)
            cx_, cy_ = x0 + width * 0.5 + skew * t, c.h * t
            r = b.uniform(0.03, 0.075) * c.w
            parts.append(
                f'<circle cx="{_f(cx_)}" cy="{_f(cy_)}" r="{_f(r)}" fill="none" '
                f'stroke="{hot}" stroke-width="{_f(r * 0.3)}" opacity="0.65"/>'
            )

    else:
        # Aperture — iris blades around a bright centre. Unmistakably a camera.
        cx, cy = c.w * b.uniform(0.44, 0.56), c.h * b.uniform(0.42, 0.54)
        radius = min(c.w, c.h) * b.uniform(0.3, 0.4)
        blades = b.pick((6, 7, 8))
        rot = b.uniform(0, 1)
        parts.append(
            f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(radius * 1.5)}" '
            f'fill="{hot}" opacity="0.28"/>'
        )
        for i in range(blades):
            a_ = rot + 2 * math.pi * i / blades
            nxt = rot + 2 * math.pi * (i + 1) / blades
            p1 = (cx + math.cos(a_) * radius, cy + math.sin(a_) * radius)
            p2 = (cx + math.cos(nxt) * radius, cy + math.sin(nxt) * radius)
            p3 = (cx + math.cos(nxt) * radius * 0.34, cy + math.sin(nxt) * radius * 0.34)
            parts.append(
                f'<polygon points="{_pts([p1, p2, p3])}" fill="{deep}" '
                f'opacity="{_f(0.5 + (i % 2) * 0.28)}" stroke="{hot}" '
                f'stroke-width="{_f(radius * 0.012)}"/>'
            )
        parts.append(
            f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(radius * 0.3)}" '
            f'fill="#ffffff" opacity="0.8"/>'
        )
        for _ in range(b.randint(3, 6)):  # radial light rays
            a_ = b.uniform(0, 2 * math.pi)
            length = radius * b.uniform(1.6, 2.8)
            parts.append(
                f'<line x1="{_f(cx)}" y1="{_f(cy)}" '
                f'x2="{_f(cx + math.cos(a_) * length)}" y2="{_f(cy + math.sin(a_) * length)}" '
                f'stroke="{hot}" stroke-width="{_f(radius * 0.03)}" opacity="0.3"/>'
            )

    if b.chance(0.3):  # 35mm sprocket strip
        hole_h = c.h * 0.035
        y = c.h * 0.035 if b.chance(0.5) else c.h * (1 - 0.07)
        step = c.w / 14
        parts.append(
            "".join(
                f'<rect x="{_f(i * step + step * 0.25)}" y="{_f(y)}" '
                f'width="{_f(step * 0.5)}" height="{_f(hole_h)}" rx="{_f(hole_h * 0.28)}" '
                f'fill="{deep}" opacity="0.6"/>'
                for i in range(14)
            )
        )

    parts.append(_vignette(b, c, b.uniform(0.3, 0.5)))
    return "".join(parts)


# ==========================================================================
# motif: icing — bakery
# ==========================================================================
def _icing(b: Brush, c: Canvas, pal: Palette, dark: bool, wide: bool = False) -> str:
    a0, a1, a2 = pal.art
    parts = [_wash(b, c, pal, dark, punch=0.22)]
    # The swirl is a single centred form; a banner crop leaves flat fill either
    # side of it, so wide bands use the scallop and drip variants only.
    variant = b.pick((1, 2)) if wide else b.randint(0, 2)

    if variant == 0:
        # Piped frosting swirl — nested offset arcs spiralling to a peak.
        cx, cy = c.w * b.uniform(0.42, 0.58), c.h * b.uniform(0.5, 0.62)
        rings = b.randint(5, 8)
        for i in range(rings):
            t = i / rings
            r = c.w * (0.42 - t * 0.33)
            off = c.h * 0.03 * t
            col = mix(a1, a0, t)
            parts.append(
                f'<circle cx="{_f(cx + b.uniform(-6, 6))}" cy="{_f(cy - off * 3)}" '
                f'r="{_f(r)}" fill="none" stroke="{col}" '
                f'stroke-width="{_f(c.w * 0.055)}" opacity="{_f(0.35 + t * 0.4)}" '
                f'stroke-linecap="round"/>'
            )
    elif variant == 1:
        # Scalloped frill layers — cake-stand doily geometry.
        layers = b.randint(2, 3)
        for layer in range(layers):
            y = c.h * (0.34 + layer * 0.24)
            scallops = b.randint(7, 11)
            step = c.w / scallops
            path = [f"M0 {_f(y)}"]
            for i in range(scallops):
                path.append(
                    f"a {_f(step / 2)} {_f(step / 2)} 0 0 0 {_f(step)} 0"
                )
            path.append(f"L{_f(c.w)} {_f(c.h)} L0 {_f(c.h)} Z")
            col = mix(a1 if layer % 2 == 0 else a0, "#ffffff", 0.25 - layer * 0.12)
            parts.append(
                f'<path d="{" ".join(path)}" fill="{col}" opacity="{_f(0.55 + layer * 0.15)}"/>'
            )
    else:
        # Glaze drip over a warm base.
        y = c.h * b.uniform(0.28, 0.42)
        drips, step = b.randint(6, 10), 0.0
        path = [f"M0 0 L{_f(c.w)} 0 L{_f(c.w)} {_f(y)}"]
        step = c.w / drips
        for i in range(drips):
            x = c.w - i * step
            depth = b.uniform(0.04, 0.16) * c.h
            path.append(
                f"C {_f(x - step * 0.2)} {_f(y + depth)} "
                f"{_f(x - step * 0.8)} {_f(y + depth)} {_f(x - step)} {_f(y)}"
            )
        path.append("Z")
        parts.append(f'<path d="{" ".join(path)}" fill="{a0}" opacity="0.9"/>')

    # Sprinkles — rotated capsules on a jittered grid so they read as scattered,
    # not random-clumped.
    cols = b.randint(4, 6) * (2 if wide else 1)
    rows = b.randint(3, 5)
    for gx in range(cols):
        for gy in range(rows):
            if not b.chance(0.55):
                continue
            x = (gx + b.uniform(0.15, 0.85)) * (c.w / cols)
            y = (gy + b.uniform(0.15, 0.85)) * (c.h / rows)
            length = b.uniform(0.018, 0.036) * c.w
            rot = b.uniform(0, 180)
            col = b.pick((mix(a1, "#ffffff", 0.55), mix(a0, "#ffffff", 0.4), a2))
            parts.append(
                f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(length)}" '
                f'height="{_f(length * 0.32)}" rx="{_f(length * 0.16)}" fill="{col}" '
                f'opacity="{_f(b.uniform(0.45, 0.85))}" '
                f'transform="rotate({_f(rot)} {_f(x)} {_f(y)})"/>'
            )
    return "".join(parts)


# ==========================================================================
# motif: mandala — wedding planning & decor
# ==========================================================================
def _mandala(b: Brush, c: Canvas, pal: Palette, dark: bool, wide: bool = False) -> str:
    a0, a1, a2 = pal.art
    parts = [_wash(b, c, pal, dark), _bloom(b, c, a1, 2, 0.32)]

    # Rangoli: n-fold rotational symmetry, two or three petal rings.
    cx, cy = c.w * b.uniform(0.45, 0.55), c.h * b.uniform(0.42, 0.55)
    fold = b.pick((8, 12, 16))
    base_r = min(c.w, c.h) * b.uniform(0.3, 0.42)

    for ring, scale in enumerate((1.0, 0.66, 0.4)):
        radius = base_r * scale
        if radius < 8:
            continue
        petals = []
        for i in range(fold):
            angle = (2 * math.pi * i) / fold
            tip_x, tip_y = cx + math.cos(angle) * radius, cy + math.sin(angle) * radius
            spread = (2 * math.pi) / fold * 0.42
            c1x = cx + math.cos(angle - spread) * radius * 0.6
            c1y = cy + math.sin(angle - spread) * radius * 0.6
            c2x = cx + math.cos(angle + spread) * radius * 0.6
            c2y = cy + math.sin(angle + spread) * radius * 0.6
            petals.append(
                f"M{_f(cx)} {_f(cy)} Q{_f(c1x)} {_f(c1y)} {_f(tip_x)} {_f(tip_y)} "
                f"Q{_f(c2x)} {_f(c2y)} {_f(cx)} {_f(cy)} Z"
            )
        col = mix(a1 if ring % 2 == 0 else a0, "#ffffff", 0.3)
        parts.append(
            f'<path d="{" ".join(petals)}" fill="none" stroke="{col}" '
            f'stroke-width="{_f(min(c.w, c.h) * 0.006)}" '
            f'opacity="{_f(0.5 - ring * 0.09)}"/>'
        )

    parts.append(
        f'<circle cx="{_f(cx)}" cy="{_f(cy)}" r="{_f(base_r * 0.12)}" '
        f'fill="{mix(a1, "#ffffff", 0.4)}" opacity="0.6"/>'
    )

    # Drape swags — the fabric canopy over a mandap.
    for i in range(b.randint(2, 3)):
        y = c.h * (0.06 + i * 0.075)
        sag = c.h * b.uniform(0.1, 0.2)
        parts.append(
            f'<path d="M-10 {_f(y)} Q {_f(c.cx)} {_f(y + sag)} {_f(c.w + 10)} {_f(y)}" '
            f'fill="none" stroke="{mix(a1, "#ffffff", 0.35)}" '
            f'stroke-width="{_f(c.h * 0.012)}" opacity="{_f(0.45 - i * 0.1)}"/>'
        )

    # Marigold strings / sparkle field.
    for _ in range(b.randint(14, 26)):
        x, y = b.uniform(0, 1) * c.w, b.uniform(0, 1) * c.h
        r = b.uniform(0.004, 0.013) * c.w
        parts.append(
            f'<circle cx="{_f(x)}" cy="{_f(y)}" r="{_f(r)}" '
            f'fill="{mix(a1, "#ffffff", 0.5)}" opacity="{_f(b.uniform(0.3, 0.8))}"/>'
        )
    return "".join(parts)


# ==========================================================================
# motif: drape — fashion & boutique
# ==========================================================================
def _drape(b: Brush, c: Canvas, pal: Palette, dark: bool, wide: bool = False) -> str:
    a0, a1, a2 = pal.art
    parts = [_wash(b, c, pal, dark)]
    variant = b.pick((1, 2)) if wide else b.randint(0, 2)

    if variant == 0:
        # Diagonal colour blocking — Bauhaus-ish, strong for lookbook covers.
        split = b.uniform(0.35, 0.65)
        skew = b.uniform(-0.35, 0.35)
        parts.append(
            f'<polygon points="{_pts([(0, c.h * (split + skew)), (c.w, c.h * (split - skew)), (c.w, c.h), (0, c.h)])}" '
            f'fill="{a2}" opacity="0.92"/>'
        )
        bar_x = c.w * (PHI if b.chance(0.5) else 1 - PHI)
        parts.append(
            f'<rect x="{_f(bar_x)}" y="0" width="{_f(c.w * 0.07)}" height="{_f(c.h)}" '
            f'fill="{a0}" opacity="0.85"/>'
        )
    elif variant == 1:
        # Fabric folds — stacked beziers, each shaded to fake a light source.
        folds = b.randint(5, 9)
        for i in range(folds):
            t = i / folds
            y = c.h * (0.1 + t * 0.95)
            amp = c.h * b.uniform(0.06, 0.16)
            phase = b.uniform(0.3, 0.7)
            col = mix(a0 if i % 2 else a1, a2, 0.25 + t * 0.5)
            parts.append(
                f'<path d="M-10 {_f(y)} C {_f(c.w * phase)} {_f(y - amp)} '
                f'{_f(c.w * (1 - phase * 0.5))} {_f(y + amp)} {_f(c.w + 10)} {_f(y - amp * 0.3)} '
                f'L{_f(c.w + 10)} {_f(c.h + 10)} L-10 {_f(c.h + 10)} Z" '
                f'fill="{col}" opacity="{_f(0.35 + t * 0.4)}"/>'
            )
    else:
        # Halftone gradient — print-editorial texture.
        cols, rows = 16, 11
        dots = []
        for gx in range(cols):
            for gy in range(rows):
                t = (gx / cols) * 0.5 + (gy / rows) * 0.5
                r = (1 - t) * (c.w / cols) * 0.46
                if r < 0.4:
                    continue
                dots.append(
                    f'<circle cx="{_f((gx + 0.5) * c.w / cols)}" '
                    f'cy="{_f((gy + 0.5) * c.h / rows)}" r="{_f(r)}"/>'
                )
        parts.append(f'<g fill="{a0}" opacity="0.5">{"".join(dots)}</g>')
        parts.append(
            f'<polygon points="{_pts([(0, 0), (c.w * 0.45, 0), (c.w * 0.2, c.h), (0, c.h)])}" '
            f'fill="{a2}" opacity="0.85"/>'
        )

    if b.chance(0.45):  # stitched seam line
        x = c.w * b.uniform(0.2, 0.8)
        parts.append(
            f'<line x1="{_f(x)}" y1="0" x2="{_f(x + b.uniform(-40, 40))}" y2="{_f(c.h)}" '
            f'stroke="{mix(a1, "#ffffff", 0.4)}" stroke-width="{_f(c.w * 0.004)}" '
            f'stroke-dasharray="{_f(c.w * 0.02)} {_f(c.w * 0.014)}" opacity="0.6"/>'
        )
    return "".join(parts)


# ==========================================================================
# motif: plan — interior design & architecture
# ==========================================================================
def _plan(b: Brush, c: Canvas, pal: Palette, dark: bool, wide: bool = False) -> str:
    a0, a1, a2 = pal.art
    parts = [_wash(b, c, pal, dark, punch=0.35)]
    # Drawing ink: near-white on dark, near-black on light. The previous value
    # sat mid-tone against both and the linework disappeared.
    line = "#ffffff" if dark else mix(a2, "#000000", 0.55)
    ink_op = 0.5 if dark else 0.75
    variant = b.pick((1, 2)) if wide else b.randint(0, 2)

    # Blueprint grid underlay.
    gid = b.ident("g")
    cell = c.w / b.randint(11, 16)
    parts.append(
        f'<defs><pattern id="{gid}" width="{_f(cell)}" height="{_f(cell)}" '
        f'patternUnits="userSpaceOnUse">'
        f'<path d="M{_f(cell)} 0 L0 0 0 {_f(cell)}" fill="none" stroke="{line}" '
        f'stroke-width="0.8" opacity="0.35"/></pattern></defs>'
        f'<rect width="{_f(c.w)}" height="{_f(c.h)}" fill="url(#{gid})"/>'
    )

    if variant == 0:
        # One-point perspective room: back wall on a golden-ratio vanishing point.
        vx, vy = c.w * b.uniform(0.42, 0.58), c.h * b.uniform(0.44, 0.56)
        bw, bh = c.w * 0.42, c.h * 0.42
        x0, y0 = vx - bw / 2, vy - bh / 2
        x1, y1 = vx + bw / 2, vy + bh / 2
        parts.append(
            f'<polygon points="{_pts([(0, c.h), (x0, y1), (x1, y1), (c.w, c.h)])}" '
            f'fill="{a2}" opacity="0.45"/>'                       # floor
            f'<polygon points="{_pts([(0, 0), (x0, y0), (x0, y1), (0, c.h)])}" '
            f'fill="{a0}" opacity="0.3"/>'                        # left wall
            f'<polygon points="{_pts([(c.w, 0), (x1, y0), (x1, y1), (c.w, c.h)])}" '
            f'fill="{a0}" opacity="0.18"/>'                       # right wall
            f'<rect x="{_f(x0)}" y="{_f(y0)}" width="{_f(bw)}" height="{_f(bh)}" '
            f'fill="{mix(a1, "#ffffff", 0.35)}" opacity="0.32"/>' # back wall
        )
        for px, py in ((0, 0), (0, c.h), (c.w, 0), (c.w, c.h)):
            tx = x0 if px == 0 else x1
            ty = y0 if py == 0 else y1
            parts.append(
                f'<line x1="{_f(px)}" y1="{_f(py)}" x2="{_f(tx)}" y2="{_f(ty)}" '
                f'stroke="{line}" stroke-width="1.4" opacity="{_f(ink_op)}"/>'
            )
        parts.append(
            f'<rect x="{_f(x0)}" y="{_f(y0)}" width="{_f(bw)}" height="{_f(bh)}" '
            f'fill="none" stroke="{line}" stroke-width="1.6" opacity="{_f(ink_op)}"/>'
        )
        # Window with light spill onto the floor.
        wx = x0 + bw * b.uniform(0.15, 0.5)
        parts.append(
            f'<rect x="{_f(wx)}" y="{_f(y0 + bh * 0.16)}" width="{_f(bw * 0.3)}" '
            f'height="{_f(bh * 0.42)}" fill="{mix(a1, "#ffffff", 0.75)}" opacity="0.6"/>'
        )
        parts.append(_bloom(b, c, mix(a1, "#ffffff", 0.6), 1, 0.35))
        # Pendant lamp on a cord.
        lx = vx + bw * b.uniform(-0.3, 0.3)
        parts.append(
            f'<line x1="{_f(lx)}" y1="{_f(y0)}" x2="{_f(lx)}" y2="{_f(vy - bh * 0.06)}" '
            f'stroke="{line}" stroke-width="1.2" opacity="0.6"/>'
            f'<path d="M{_f(lx - bw * 0.07)} {_f(vy - bh * 0.06)} '
            f'Q{_f(lx)} {_f(vy + bh * 0.06)} {_f(lx + bw * 0.07)} {_f(vy - bh * 0.06)} Z" '
            f'fill="{mix(a1, "#ffffff", 0.5)}" opacity="0.7"/>'
        )
    elif variant == 1:
        # Floor plan: rooms subdivided by recursive splits, with a door swing.
        pad = c.w * 0.1
        rooms = [(pad, pad, c.w - pad * 2, c.h - pad * 2)]
        for _ in range(b.randint(3, 5)):
            idx = b.randint(0, len(rooms) - 1)
            x, y, w, h = rooms.pop(idx)
            if w < c.w * 0.16 or h < c.h * 0.16:
                rooms.append((x, y, w, h))
                continue
            if w > h:
                cut = w * b.uniform(0.35, 0.65)
                rooms += [(x, y, cut, h), (x + cut, y, w - cut, h)]
            else:
                cut = h * b.uniform(0.35, 0.65)
                rooms += [(x, y, w, cut), (x, y + cut, w, h - cut)]
        hatch = b.ident("hx")
        parts.append(
            f'<defs><pattern id="{hatch}" width="7" height="7" '
            f'patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<line x1="0" y1="0" x2="0" y2="7" stroke="{line}" stroke-width="1.1" '
            f'opacity="0.4"/></pattern></defs>'
        )
        for i, (x, y, w, h) in enumerate(rooms):
            parts.append(
                f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" '
                f'fill="{a1 if i % 3 == 0 else a0}" opacity="{_f(b.uniform(0.14, 0.3))}"/>'
                f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" '
                f'fill="none" stroke="{line}" stroke-width="2.2" opacity="{_f(ink_op)}"/>'
            )
            # Furniture blocks — the thing that makes a plan read as a *home*.
            if w > c.w * 0.16 and h > c.h * 0.12 and b.chance(0.7):
                fw, fh = w * b.uniform(0.3, 0.55), h * b.uniform(0.22, 0.4)
                fx = x + b.uniform(0.1, 0.9) * (w - fw)
                fy = y + b.uniform(0.1, 0.9) * (h - fh)
                parts.append(
                    f'<rect x="{_f(fx)}" y="{_f(fy)}" width="{_f(fw)}" height="{_f(fh)}" '
                    f'rx="{_f(min(fw, fh) * 0.12)}" fill="none" stroke="{line}" '
                    f'stroke-width="1.3" opacity="{_f(ink_op * 0.8)}"/>'
                )
        # One room hatched, as a drawing would mark a wet area or terrace.
        hx, hy, hw, hh = rooms[b.randint(0, len(rooms) - 1)]
        parts.append(
            f'<rect x="{_f(hx)}" y="{_f(hy)}" width="{_f(hw)}" height="{_f(hh)}" '
            f'fill="url(#{hatch})"/>'
        )
        x, y, w, h = rooms[0]
        r = min(w, h) * 0.4
        parts.append(
            f'<path d="M{_f(x)} {_f(y + r)} A{_f(r)} {_f(r)} 0 0 1 {_f(x + r)} {_f(y)}" '
            f'fill="none" stroke="{line}" stroke-width="1.5" opacity="{_f(ink_op)}"/>'
        )
    else:
        # Facade elevation — a window grid with real mullions. The previous
        # version was flat colour bands and read as mud at thumbnail size.
        mx, my = c.w * 0.1, c.h * 0.1
        bw, bh = c.w - mx * 2, c.h - my * 2
        floors = b.randint(3, 5)
        bays = b.randint(3, 6)
        parts.append(
            f'<rect x="{_f(mx)}" y="{_f(my)}" width="{_f(bw)}" height="{_f(bh)}" '
            f'fill="{a0}" opacity="0.22" stroke="{line}" stroke-width="2.4" '
            f'opacity="{_f(ink_op)}"/>'
        )
        fh_, bw_ = bh / floors, bw / bays
        glass = mix(a1, "#ffffff", 0.55 if not dark else 0.3)
        for f_i in range(floors):
            for b_i in range(bays):
                if b.chance(0.14):  # solid panel instead of glazing
                    continue
                pad_x, pad_y = bw_ * 0.18, fh_ * 0.2
                x = mx + b_i * bw_ + pad_x
                y = my + f_i * fh_ + pad_y
                w, h = bw_ - pad_x * 2, fh_ - pad_y * 2
                parts.append(
                    f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" '
                    f'fill="{glass}" opacity="{_f(b.uniform(0.4, 0.85))}"/>'
                    f'<rect x="{_f(x)}" y="{_f(y)}" width="{_f(w)}" height="{_f(h)}" '
                    f'fill="none" stroke="{line}" stroke-width="1.2" opacity="{_f(ink_op)}"/>'
                    f'<line x1="{_f(x + w / 2)}" y1="{_f(y)}" x2="{_f(x + w / 2)}" '
                    f'y2="{_f(y + h)}" stroke="{line}" stroke-width="0.9" '
                    f'opacity="{_f(ink_op * 0.7)}"/>'
                )
            parts.append(  # floor slab
                f'<line x1="{_f(mx)}" y1="{_f(my + (f_i + 1) * fh_)}" x2="{_f(mx + bw)}" '
                f'y2="{_f(my + (f_i + 1) * fh_)}" stroke="{line}" stroke-width="1.8" '
                f'opacity="{_f(ink_op)}"/>'
            )
        # Ground line + dimension string, the way an elevation sheet is annotated.
        gy = my + bh
        parts.append(
            f'<line x1="0" y1="{_f(gy)}" x2="{_f(c.w)}" y2="{_f(gy)}" stroke="{line}" '
            f'stroke-width="2.6" opacity="{_f(ink_op)}"/>'
        )
        dx = mx * 0.5
        parts.append(
            f'<line x1="{_f(dx)}" y1="{_f(my)}" x2="{_f(dx)}" y2="{_f(gy)}" '
            f'stroke="{line}" stroke-width="1" opacity="{_f(ink_op * 0.8)}"/>'
        )
        for f_i in range(floors + 1):
            ty = my + f_i * fh_
            parts.append(
                f'<line x1="{_f(dx - 5)}" y1="{_f(ty)}" x2="{_f(dx + 5)}" y2="{_f(ty)}" '
                f'stroke="{line}" stroke-width="1" opacity="{_f(ink_op * 0.8)}"/>'
            )
    return "".join(parts)


MOTIFS = {
    "film": _film,
    "icing": _icing,
    "mandala": _mandala,
    "drape": _drape,
    "plan": _plan,
}

VERTICAL_MOTIF = {
    "wedding_photo": "film",
    "bakery": "icing",
    "wedding_planner": "mandala",
    "fashion": "drape",
    "interior_arch": "plan",
}


# ==========================================================================
# public API
# ==========================================================================
def artwork(
    seed: int,
    motif: str,
    index: int,
    palette: Palette,
    *,
    width: int = 800,
    height: int = 600,
    dark: bool = False,
    css_class: str = "art",
    label: str = "",
) -> str:
    """Render one deterministic SVG artwork.

    `index` varies the piece within a business (gallery tile 3 is always tile 3).
    `preserveAspectRatio="slice"` lets a single piece fill any container shape.

    Aspect ratios past ~2:1 are treated as banners: motifs drop their centred
    compositions in favour of variants that carry across the full width, because
    a `slice` crop of a centred form leaves flat colour at both ends.
    """
    uid = f"{seed % 0xFFFFFF:06x}{index:02x}"
    brush = Brush(seed * 2654435761 + index * 40503, uid)
    canvas = Canvas(width, height)
    wide = width / max(height, 1) >= 2.0
    body = MOTIFS.get(motif, _film)(brush, canvas, palette, dark, wide)
    role = (
        f'role="img" aria-label="{label}"' if label else 'role="presentation" aria-hidden="true"'
    )
    # Several motifs deliberately draw past the viewBox (light rays, blooms,
    # ridge lines). The UA clips that ink visually, but Chromium still counts it
    # in scrollWidth, which shows up as phantom page overflow. Clipping in the
    # SVG itself keeps the geometry honest rather than masking it downstream.
    clip = f"c{uid}"
    return (
        f'<svg class="{css_class}" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg" '
        f"{role}>"
        f'<defs><clipPath id="{clip}"><rect width="{width}" height="{height}"/>'
        f"</clipPath></defs>"
        f'<g clip-path="url(#{clip})">{body}</g></svg>'
    )


def street_plate(seed: int, palette: Palette) -> str:
    """A schematic street grid for the locality card.

    This slot used to render a full motif artwork — a 10KB composition, on every
    page, behind a label box that covers its middle. Across 2,000 pages that was
    ~19MB of ink nobody sees. A few dozen deterministic shapes read as a map at
    a glance and cost about 1KB.

    Everything is drawn at low contrast on purpose: the address card sits on top
    of it and has to stay the thing you read.
    """
    b = Brush(seed * 2246822519, "mp")
    block = mix(palette.ink, palette.surface, 0.055)
    minor = mix(palette.ink, palette.surface, 0.20)
    major = mix(palette.ink, palette.surface, 0.34)
    parts = [f'<rect width="900" height="560" fill="{palette.surface}"/>']

    vx = [0] + [80 + b.randint(0, 40) + i * 148 for i in range(5)] + [900]
    hy = [0] + [64 + b.randint(0, 26) + i * 118 for i in range(4)] + [560]

    # City blocks first, so the roads read as gaps between built-up land.
    for xi in range(len(vx) - 1):
        for yi in range(len(hy) - 1):
            x0, x1 = vx[xi] + 7, vx[xi + 1] - 7
            y0, y1 = hy[yi] + 7, hy[yi + 1] - 7
            if x1 - x0 < 18 or y1 - y0 < 18 or b.chance(0.22):
                continue
            parts.append(f'<rect x="{_f(x0)}" y="{_f(y0)}" width="{_f(x1 - x0)}" '
                         f'height="{_f(y1 - y0)}" fill="{block}" rx="2"/>')

    for x in vx[1:-1]:
        parts.append(f'<path d="M{x} 0V560" stroke="{minor}" stroke-width="2.2"/>')
    for y in hy[1:-1]:
        parts.append(f'<path d="M0 {y}H900" stroke="{minor}" stroke-width="2.2"/>')

    # One arterial road each way, plus a diagonal so it is not pure graph paper.
    ax, ay = b.pick(vx[1:-1]), b.pick(hy[1:-1])
    parts.append(f'<path d="M{ax} 0V560" stroke="{major}" stroke-width="7"/>')
    parts.append(f'<path d="M0 {ay}H900" stroke="{major}" stroke-width="7"/>')
    dx = b.randint(120, 320)
    parts.append(f'<path d="M{dx} 560L{dx + 420} 0" stroke="{minor}" '
                 f'stroke-width="4.5"/>')

    # A park and a waterway to break up the grey.
    px, py = vx[1], hy[2]
    parts.append(f'<rect x="{px + 7}" y="{py + 7}" width="{vx[2] - px - 14}" '
                 f'height="{hy[3] - py - 14}" fill="{palette.accent}" '
                 f'opacity=".16" rx="3"/>')
    wx = vx[4]
    parts.append(f'<path d="M{wx} 0q54 132 12 236t26 324" fill="none" '
                 f'stroke="{palette.accent}" stroke-width="13" opacity=".2" '
                 f'stroke-linecap="round"/>')
    return (
        '<svg class="art" viewBox="0 0 900 560" preserveAspectRatio="xMidYMid slice" '
        'xmlns="http://www.w3.org/2000/svg" role="presentation" aria-hidden="true">'
        + "".join(parts) + "</svg>"
    )


def favicon(seed: int, palette: Palette, initials: str) -> str:
    """A data-URI monogram favicon — keeps the page at zero external requests."""
    bg, fg = palette.accent, palette.accent_ink
    text = initials[:2].upper()
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'>"
        f"<rect width='64' height='64' rx='14' fill='{bg}'/>"
        f"<text x='32' y='33' font-family='Georgia,serif' font-size='30' "
        f"font-weight='600' fill='{fg}' text-anchor='middle' "
        f"dominant-baseline='central'>{text}</text></svg>"
    )
    from urllib.parse import quote

    return "data:image/svg+xml," + quote(svg, safe="")


def grain_data_uri(opacity: float = 0.5) -> str:
    """Film-grain tile used as a CSS background overlay.

    Applied once in CSS rather than as a per-SVG filter: one composited layer for
    the whole page instead of a `feTurbulence` pass per artwork.
    """
    from urllib.parse import quote

    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='120'>"
        "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' "
        "numOctaves='3' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/>"
        "</filter>"
        f"<rect width='120' height='120' filter='url(#n)' opacity='{opacity}'/></svg>"
    )
    return "data:image/svg+xml," + quote(svg, safe="")
