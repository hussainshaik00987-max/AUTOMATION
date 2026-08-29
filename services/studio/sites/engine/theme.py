"""Palettes and type systems.

Two hard rules, both chosen so a demo opens instantly on a prospect's phone
during a cold call:

1. **No external requests.** Fonts are system stacks only. A webfont costs a
   round trip and a FOUT, and half these leads are on patchy mobile data.
2. **Contrast is verified, not eyeballed.** Every palette is checked against
   WCAG AA at build time by `audit_palettes()`; the build fails loudly rather
   than shipping unreadable body copy.

Palettes carry only *source* colours. Everything derived (borders, hovers,
tints) is computed in CSS with `color-mix()`, so a palette stays a handful of
honest values instead of thirty hand-tuned ones.
"""

from __future__ import annotations

from dataclasses import dataclass


# --------------------------------------------------------------------------
# colour maths (WCAG 2.1)
# --------------------------------------------------------------------------
def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def relative_luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        srgb = c / 255
        return srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4

    r, g, b = hex_to_rgb(hex_color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def mix(a: str, b: str, weight: float) -> str:
    """Blend two hex colours; `weight` is how much of `a` to keep."""
    ar, ag, ab = hex_to_rgb(a)
    br, bg_, bb = hex_to_rgb(b)
    r = round(ar * weight + br * (1 - weight))
    g = round(ag * weight + bg_ * (1 - weight))
    b_ = round(ab * weight + bb * (1 - weight))
    return f"#{r:02x}{g:02x}{b_:02x}"


@dataclass(frozen=True, slots=True)
class Palette:
    key: str
    name: str
    bg: str          # page background
    surface: str     # cards / alternating bands
    ink: str         # primary text
    ink_soft: str    # secondary text  (AA at normal size)
    accent: str      # brand colour: buttons, rules, highlights
    accent_ink: str  # text placed ON accent
    deep: str        # dark band background (footers, feature sections)
    deep_ink: str    # text on `deep`
    art: tuple[str, str, str]  # three-stop ramp driving the generated SVG art


# --------------------------------------------------------------------------
# palette library — grouped by the vertical they were tuned for
# --------------------------------------------------------------------------
PALETTES: dict[str, Palette] = {}


def _add(*palettes: Palette) -> None:
    for p in palettes:
        PALETTES[p.key] = p


# Wedding photography — filmic, editorial, low-chroma so photos would dominate.
_add(
    Palette("noir", "Noir Brass", "#faf8f5", "#f1ece5", "#15130f", "#5c554a",
            "#8a6a35", "#fffdf9", "#141210", "#f3efe8", ("#8a6a35", "#c9a227", "#2b2620")),
    Palette("sepia", "Sepia Film", "#fbf7f1", "#f2e9dd", "#241a12", "#6b5744",
            "#a4552f", "#fff6f0", "#1d1509", "#f6ece0", ("#a4552f", "#d99b6c", "#2a1d12")),
    Palette("blush", "Blush Editorial", "#fdf8f7", "#f6e9e7", "#231719", "#6a545a",
            "#a6455c", "#fff5f7", "#1f1416", "#f8eaec", ("#a6455c", "#e0a3ad", "#2b1c20")),
    Palette("slate", "Slate & Ivory", "#f7f8f9", "#e9edf1", "#111820", "#4e5b68",
            "#2f6f7c", "#f4fdff", "#0e1620", "#e6edf3", ("#2f6f7c", "#7fb3bb", "#16202b")),
    Palette("sage", "Sage Document", "#f8faf7", "#e9efe7", "#161c16", "#4f5b4d",
            "#4a6b45", "#f6fbf4", "#131a13", "#e8efe6", ("#4a6b45", "#9dbb95", "#1b241a")),
)

# Bakery — warm, appetising, high-comfort.
_add(
    Palette("butter", "Buttercream", "#fffaf0", "#fbeed8", "#2a1c0c", "#6d5535",
            "#9a5c0d", "#fff8ee", "#2a1a08", "#fbeeda", ("#b06b16", "#f0c07a", "#3a2610")),
    Palette("berry", "Strawberry Glaze", "#fff8f8", "#fce7e7", "#2c1219", "#6f4650",
            "#b8375a", "#fff4f6", "#2b1017", "#fbe5e9", ("#b8375a", "#f2a7ba", "#38141f")),
    Palette("pistachio", "Pistachio & Cream", "#f9fcf5", "#eaf3e0", "#1b2413", "#4f5f42",
            "#5a7c33", "#f9fdf3", "#182111", "#e9f2df", ("#5a7c33", "#b2cf8c", "#202d16")),
    Palette("cocoa", "Dark Cocoa", "#fdf9f4", "#f2e6d8", "#241608", "#63513e",
            "#8b5e2b", "#fff8f0", "#1c1208", "#f3e6d5", ("#8b5e2b", "#d9a86a", "#2c1c0c")),
    Palette("citrus", "Citrus Bake", "#fffcf3", "#fdf0cf", "#2b1f06", "#6a5726",
            "#94590a", "#fffaf0", "#2a1e06", "#fbeed2", ("#a86a08", "#f5c95c", "#382806")),
)

# Wedding planning & decor — celebratory, Indian-wedding palettes.
_add(
    Palette("marigold", "Marigold & Maroon", "#fffaf2", "#fbecd6", "#2e1010", "#6d453a",
            "#a32b2b", "#fff6f2", "#2b0d0d", "#faead6", ("#a32b2b", "#eba338", "#3c1512")),
    Palette("emerald", "Royal Emerald", "#f6fbf8", "#e4f1e9", "#0c1f16", "#465b50",
            "#1c6b4b", "#f2fff8", "#0a1d14", "#e2efe7", ("#1c6b4b", "#c8a44a", "#112a1f")),
    Palette("peony", "Blush Peony", "#fefaf9", "#f8eae9", "#271a1d", "#6d555a",
            "#9c4a63", "#fff6f8", "#241619", "#f7e9eb", ("#9c4a63", "#e5b3bd", "#2f1e23")),
    Palette("midnight", "Midnight Silk", "#f7f7fb", "#e9e9f3", "#12132a", "#4d4f6b",
            "#3b3f8f", "#f6f7ff", "#101128", "#e7e8f4", ("#3b3f8f", "#c9a227", "#181a34")),
    Palette("haldi", "Terracotta Haldi", "#fffaf4", "#fbe9da", "#2c1a0e", "#6c5140",
            "#b5541f", "#fff6ef", "#28170c", "#f9e7d7", ("#b5541f", "#e8a95c", "#38210f")),
)

# Fashion & boutique — high contrast, gallery-like.
_add(
    Palette("runway", "Monochrome Runway", "#ffffff", "#f2f2f2", "#0a0a0a", "#565656",
            "#0a0a0a", "#ffffff", "#0a0a0a", "#f4f4f4", ("#0a0a0a", "#8a8a8a", "#1c1c1c")),
    Palette("atelier", "Ivory Atelier", "#fdfbf7", "#f1ebe1", "#1a1611", "#5f574a",
            "#8a6a3a", "#fffcf6", "#171410", "#f2ece2", ("#8a6a3a", "#d3b98d", "#241f18")),
    Palette("indigo", "Indigo Handloom", "#f8f8fa", "#e8e9ef", "#101433", "#4a4f70",
            "#2c3a8c", "#f6f8ff", "#0d1130", "#e6e8f1", ("#2c3a8c", "#b4552f", "#161b3a")),
    Palette("neon", "Neon Editorial", "#fbfbf9", "#efefe9", "#101010", "#555550",
            "#7a1fa2", "#fff8ff", "#0d0d0c", "#f0f0ea", ("#7a1fa2", "#d946ef", "#1a1a18")),
    Palette("rosewood", "Rosewood Couture", "#fdf9f8", "#f4e6e4", "#2a1416", "#6b4a4c",
            "#8e3b40", "#fff5f4", "#251113", "#f3e5e3", ("#8e3b40", "#dba39a", "#31191b")),
)

# Interior & architecture — material, calm, drawing-like.
_add(
    Palette("concrete", "Concrete & Oak", "#f9f8f6", "#eceae5", "#16150f", "#57544a",
            "#8a6231", "#fffaf3", "#141310", "#eceae4", ("#8a6231", "#c2a578", "#22201a")),
    Palette("blueprint", "Blueprint", "#f7f9fb", "#e8eef4", "#0d1620", "#4a5764",
            "#1d5c99", "#f4faff", "#0b1420", "#e6eef6", ("#1d5c99", "#7fb2dd", "#132030")),
    Palette("travertine", "Travertine", "#fbf9f5", "#f0eae0", "#1c1810", "#5e564a",
            "#7a5c2e", "#fffbf4", "#191510", "#f0ebe1", ("#7a5c2e", "#c6ac7c", "#262117")),
    Palette("brass", "Charcoal & Brass", "#f8f8f7", "#eaeae7", "#131313", "#535350",
            "#8a6a1f", "#fffaef", "#111111", "#ebeae6", ("#8a6a1f", "#c9a227", "#1e1e1c")),
    Palette("studio", "Sage Studio", "#f8faf8", "#e9efe9", "#141a15", "#4d584e",
            "#41675a", "#f4fbf8", "#121815", "#e8efea", ("#41675a", "#9dbdb0", "#1c2420")),
)


# --------------------------------------------------------------------------
# type systems — system stacks only, zero network cost
# --------------------------------------------------------------------------
SERIF_OLD = ("'Iowan Old Style','Palatino Linotype','URW Palladio L',P052,Palatino,"
             "'Book Antiqua',Georgia,serif")
SERIF_TRANS = "Charter,Georgia,Cambria,'Times New Roman',Times,serif"
SERIF_DIDONE = ("Didot,'Bodoni MT','Playfair Display','Big Caslon','Hoefler Text',"
                "Garamond,Georgia,serif")
SERIF_UI = "ui-serif,Georgia,Cambria,'Times New Roman',serif"
SANS_UI = ("system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
           "'Helvetica Neue',Arial,sans-serif")
SANS_GROTESK = ("'Helvetica Neue',Helvetica,'Segoe UI',Roboto,Arial,"
                "system-ui,sans-serif")
SANS_COND = ("'Haettenschweiler','Arial Narrow','Segoe UI',Roboto Condensed,"
             "'Helvetica Neue',sans-serif")
MONO = ("ui-monospace,'SF Mono',SFMono-Regular,'Cascadia Mono',Menlo,Consolas,"
        "'Liberation Mono',monospace")


@dataclass(frozen=True, slots=True)
class TypeSystem:
    key: str
    display: str           # headings
    body: str              # paragraphs
    eyebrow: str           # small caps labels
    display_weight: int
    display_spacing: str   # letter-spacing for headings
    eyebrow_spacing: str
    eyebrow_case: str      # uppercase | none
    display_case: str
    # The wordmark is set at ~17px, where a display face chosen for 80px
    # headlines stops working: Haettenschweiler collapses into an unreadable
    # picket fence and a Didone's hairlines drop out entirely. `brand` is
    # therefore a separate, deliberately legible face, and `brand_weight` is
    # heavier than the display weight because 17px needs the extra stem.
    brand: str = ""
    brand_weight: int = 600

    @property
    def brand_face(self) -> str:
        return self.brand or self.display


TYPE_SYSTEMS: dict[str, TypeSystem] = {
    ts.key: ts
    for ts in (
        # Classic editorial serif — the default for anything wedding-adjacent.
        TypeSystem("editorial", SERIF_OLD, SANS_UI, SANS_UI, 400, "-0.02em", "0.18em",
                   "uppercase", "none", brand=SERIF_OLD, brand_weight=600),
        # High-fashion Didone: tight, dramatic, thin.
        # Didone hairlines vanish at wordmark size, so the brand borrows Charter.
        TypeSystem("couture", SERIF_DIDONE, SANS_UI, SANS_UI, 400, "-0.03em", "0.3em",
                   "uppercase", "none", brand=SERIF_TRANS, brand_weight=600),
        # Swiss: everything sans, heavy headline, tight tracking.
        TypeSystem("swiss", SANS_GROTESK, SANS_UI, MONO, 700, "-0.04em", "0.14em",
                   "uppercase", "none", brand=SANS_GROTESK, brand_weight=700),
        # Architectural: sans headings, mono labels, uppercase headings.
        TypeSystem("drafting", SANS_GROTESK, SANS_UI, MONO, 600, "-0.02em", "0.22em",
                   "uppercase", "uppercase", brand=SANS_GROTESK, brand_weight=650),
        # Warm & friendly: transitional serif headings, generous sans body.
        TypeSystem("warm", SERIF_TRANS, SANS_UI, SANS_UI, 600, "-0.015em", "0.16em",
                   "uppercase", "none", brand=SERIF_TRANS, brand_weight=650),
        # Poster: condensed uppercase display.
        # Haettenschweiler is the whole point of this system at 80px and completely
        # unreadable at 17px — the wordmark drops to a grotesk.
        TypeSystem("poster", SANS_COND, SANS_UI, MONO, 700, "0.01em", "0.24em",
                   "uppercase", "uppercase", brand=SANS_GROTESK, brand_weight=700),
        # Quiet minimal: serif UI headings, no shouting anywhere.
        TypeSystem("quiet", SERIF_UI, SANS_UI, SANS_UI, 400, "-0.01em", "0.12em",
                   "uppercase", "none", brand=SERIF_UI, brand_weight=600),
    )
}


# --------------------------------------------------------------------------
# build-time verification
# --------------------------------------------------------------------------
AA_NORMAL = 4.5   # WCAG AA, body text
AA_LARGE = 3.0    # WCAG AA, >=24px or >=18.66px bold


def audit_palettes() -> list[str]:
    """Return a list of WCAG failures. Empty list means every palette passes."""
    problems: list[str] = []

    def check(label: str, fg: str, bg: str, threshold: float) -> None:
        ratio = contrast_ratio(fg, bg)
        if ratio < threshold:
            problems.append(f"{label}: {ratio:.2f} < {threshold} ({fg} on {bg})")

    for p in PALETTES.values():
        check(f"{p.key} ink/bg", p.ink, p.bg, AA_NORMAL)
        check(f"{p.key} ink/surface", p.ink, p.surface, AA_NORMAL)
        check(f"{p.key} ink_soft/bg", p.ink_soft, p.bg, AA_NORMAL)
        check(f"{p.key} ink_soft/surface", p.ink_soft, p.surface, AA_NORMAL)
        # Accent is used for links and button fills, so it needs body-text contrast
        # on the page and legible text on top of itself.
        check(f"{p.key} accent/bg", p.accent, p.bg, AA_NORMAL)
        check(f"{p.key} accent_ink/accent", p.accent_ink, p.accent, AA_NORMAL)
        check(f"{p.key} deep_ink/deep", p.deep_ink, p.deep, AA_NORMAL)
        # Accent must still read as a highlight against the dark band.
        check(f"{p.key} accent-on-deep", mix(p.accent, "#ffffff", 0.55), p.deep, AA_LARGE)
    return problems


if __name__ == "__main__":  # pragma: no cover - developer check
    issues = audit_palettes()
    print(f"{len(PALETTES)} palettes, {len(TYPE_SYSTEMS)} type systems")
    if issues:
        print(f"\n{len(issues)} contrast failures:")
        for issue in issues:
            print("  -", issue)
    else:
        print("all palettes pass WCAG AA")
