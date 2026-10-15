#!/usr/bin/env python3
"""Correct the in-artwork cross-reference "(Fig. 3)" -> "(Fig. 4)" in Figs. 2, 3, 5, 6.

The diagrams were drawn when the repair loop was numbered Fig. 3; in the paper
(both the 4 Oct draft and this manuscript) it is Fig. 4, so four figures point
readers at the wrong diagram. The repository's .drawio sources are not the
versions these PNGs were exported from (re-exporting them changes the layout),
so instead of re-exporting, this script swaps the single "3" glyph in each
published PNG for a "4" set in the same face, size, baseline and ink colour.

The face and size are not assumed: for every candidate (Helvetica/Arial,
regular/bold) the script renders a "3" at the size whose glyph height matches
the one in the image and keeps the candidate that correlates best with it.

Input:  the original figures in paper/ (never modified).
Output: paper/springer-nature/fig{2,3,5,6}.png, plus before/after crops in
        the directory given as the first argument (for visual checking).

Glyph boxes come from a tesseract pass over the figures ("3)" token after "(Fig.").
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

PAPER = Path(__file__).resolve().parents[2]
OUT = PAPER / "springer-nature"

# manuscript figure -> (original file in paper/, bbox of the OCR token "3)": left, top, w, h)
TARGETS = {
    "fig2.png": ("fig2-nonlinear-stage-model.png", (4324, 1342, 69, 79)),
    "fig3.png": ("fig4-requirements-test-repair-traceability.png", (7648, 1025, 69, 78)),
    "fig5.png": ("fig5-deployment-topology-live-validation.png", (3238, 4276, 68, 79)),
    "fig6.png": ("fig6-hero-path-sequence.png", (3948, 2231, 69, 78)),
}

HELV = "/System/Library/Fonts/Helvetica.ttc"
SUPP = "/System/Library/Fonts/Supplemental"
CANDIDATES = [
    ("Helvetica", HELV, 0),
    ("Helvetica Bold", HELV, 1),
    ("Arial", f"{SUPP}/Arial.ttf", 0),
    ("Arial Bold", f"{SUPP}/Arial Bold.ttf", 0),
]


def render(text: str, font: ImageFont.FreeTypeFont) -> tuple[np.ndarray, tuple[int, int]]:
    """Render ``text`` at origin (pad, pad); return the coverage mask and its tight offset."""
    pad = 20
    w, h = font.getbbox(text)[2] + 2 * pad, font.getbbox(text)[3] + 2 * pad
    canvas = Image.new("L", (w, h), 0)
    ImageDraw.Draw(canvas).text((pad, pad), text, fill=255, font=font)
    arr = np.asarray(canvas).astype(float) / 255.0
    ys, xs = np.nonzero(arr > 0.5)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return arr[y0:y1, x0:x1], (x0 - pad, y0 - pad)


def best_font(glyph: np.ndarray) -> tuple[str, ImageFont.FreeTypeFont, float]:
    """Pick the face/size whose rendered '3' best matches the coverage mask ``glyph``."""
    target_h = glyph.shape[0]
    best = ("", None, -1.0)
    for name, path, index in CANDIDATES:
        for size in range(int(target_h * 1.0), int(target_h * 1.8)):
            font = ImageFont.truetype(path, size, index=index)
            mask, _ = render("3", font)
            if abs(mask.shape[0] - target_h) > 1:
                continue
            resized = np.asarray(
                Image.fromarray((mask * 255).astype(np.uint8)).resize(glyph.shape[::-1])
            ).astype(float) / 255.0
            corr = np.corrcoef(resized.ravel(), glyph.ravel())[0, 1]
            if corr > best[2]:
                best = (f"{name} {size}px", font, corr)
    if best[1] is None:
        sys.exit("no candidate font matched the glyph height")
    return best  # type: ignore[return-value]


def swap(src: Path, dst: Path, box: tuple[int, int, int, int], crops: Path) -> str:
    img = np.asarray(Image.open(src).convert("RGBA")).astype(float)
    left, top, w, h = box
    pad = 12
    x0, y0 = left - pad, top - pad
    region = img[y0 : top + h + pad, x0 : left + w + pad]

    # Ink = darkness after compositing on the local background colour.
    alpha = region[..., 3:4] / 255.0
    on_white = region[..., :3] * alpha + 255 * (1 - alpha)
    lum = on_white @ np.array([0.299, 0.587, 0.114])
    bg_lum = np.median(lum)
    ink = lum < bg_lum - 60
    labels, n = ndimage.label(ink)
    comps = ndimage.find_objects(labels)
    # The "3" is the left-most component inside the "3)" token box.
    order = sorted(range(n), key=lambda i: comps[i][1].start)
    k = order[0]
    sl = comps[k]
    comp = labels == k + 1
    gy0, gy1, gx0, gx1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop

    ink_lum = np.percentile(lum[comp], 5)
    coverage = np.clip((bg_lum - lum) / max(bg_lum - ink_lum, 1), 0, 1)
    glyph = coverage[gy0:gy1, gx0:gx1] * ndimage.binary_dilation(comp, iterations=2)[gy0:gy1, gx0:gx1]
    fname, font, corr = best_font(glyph)

    # Background and ink colours, sampled from the region itself.
    erase = ndimage.binary_dilation(comp, iterations=4)
    ring = ndimage.binary_dilation(erase, iterations=6) & ~ndimage.binary_dilation(ink, iterations=4)
    bg_rgba = np.median(region[ring], axis=0)
    ink_rgba = np.median(region[comp & (lum <= np.percentile(lum[comp], 20))], axis=0)

    # Place the "4" on the same text origin as the "3" (Helvetica/Arial digits share one advance).
    _, (dx3, dy3) = render("3", font)
    mask4, (dx4, dy4) = render("4", font)
    ox, oy = gx0 - dx3, gy0 - dy3
    out = region.copy()
    out[erase] = bg_rgba
    h4, w4 = mask4.shape
    py, px = oy + dy4, ox + dx4
    a = mask4[..., None]
    patch = out[py : py + h4, px : px + w4]
    # Premultiplied "over" so a transparent background stays transparent around the glyph.
    a_ink, a_bg = ink_rgba[3] / 255.0 * a, patch[..., 3:4] / 255.0 * (1 - a)
    a_out = a_ink + a_bg
    rgb = (ink_rgba[:3] * a_ink + patch[..., :3] * a_bg) / np.maximum(a_out, 1e-6)
    out[py : py + h4, px : px + w4] = np.concatenate([rgb, a_out * 255], axis=-1)

    full = img.copy()
    full[y0 : y0 + region.shape[0], x0 : x0 + region.shape[1]] = out
    Image.fromarray(np.clip(full, 0, 255).round().astype(np.uint8), "RGBA").save(dst, dpi=(600, 600))

    # Before/after crop of the whole "(Fig. N)" label on white, for inspection.
    cx0, cy0, cx1, cy1 = left - 230, top - 20, left + w + 30, top + h + 20
    pair = []
    for arr in (img, full):
        crop = Image.fromarray(np.clip(arr[cy0:cy1, cx0:cx1], 0, 255).astype(np.uint8), "RGBA")
        bg = Image.new("RGBA", crop.size, "white")
        bg.alpha_composite(crop)
        pair.append(bg.convert("RGB"))
    sheet = Image.new("RGB", (pair[0].width, pair[0].height * 2 + 6), "red")
    sheet.paste(pair[0], (0, 0))
    sheet.paste(pair[1], (0, pair[0].height + 6))
    sheet.save(crops / f"{dst.stem}-before-after.png")
    return f"{dst.name}: matched {fname} (r={corr:.3f}), bg={bg_rgba.round().astype(int).tolist()}"


def main() -> None:
    crops = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT / "review"
    crops.mkdir(parents=True, exist_ok=True)
    for name, (orig, box) in TARGETS.items():
        print(swap(PAPER / orig, OUT / name, box, crops))


if __name__ == "__main__":
    main()
