# -*- coding: utf-8 -*-
"""
Compare a QGIS-exported PDF page against the InDesign rendering of the IDML
made from the same layout.  Produces a side-by-side, a 50/50 blend, a heat map
of |difference| and a JSON with per-tile scores so an agent can point at the
regions that diverge.

    python tools/compare_pdf.py --ref qgis.pdf --test indesign.pdf --page 1 \
        --out outdir [--dpi 72] [--tile 20]

Tile scores are mean absolute RGB difference (0..255) per tile of `tile` mm.
"""
import argparse
import json
import os

import fitz
import numpy as np
from PIL import Image, ImageChops


def render(pdf, page, dpi, join=1):
    """Render page `page` (1-based). With join=N, pages (page-1)*N+1 .. +N are
    concatenated left-to-right - InDesign exports facing spreads as single
    pages, QGIS has the whole spread on one page."""
    d = fitz.open(pdf)
    ims, w_mm, h_mm = [], 0.0, 0.0
    first = (page - 1) * join
    for i in range(first, first + join):
        p = d[i]
        pm = p.get_pixmap(dpi=dpi, alpha=False)
        ims.append(Image.frombytes("RGB", (pm.width, pm.height), pm.samples))
        w_mm += p.rect.width * 25.4 / 72
        h_mm = max(h_mm, p.rect.height * 25.4 / 72)
    if join == 1:
        return ims[0], (w_mm, h_mm)
    W = sum(im.width for im in ims)
    H = max(im.height for im in ims)
    out = Image.new("RGB", (W, H), "white")
    x = 0
    for im in ims:
        out.paste(im, (x, 0))
        x += im.width
    return out, (w_mm, h_mm)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--test", required=True)
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--test_page", type=int, default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dpi", type=int, default=72)
    ap.add_argument("--tile", type=float, default=20.0)
    ap.add_argument("--join", type=int, default=1,
                    help="concatenate N consecutive test pages side by side")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    ref, ref_mm = render(a.ref, a.page, a.dpi)
    tst, tst_mm = render(a.test, a.test_page or a.page, a.dpi, join=a.join)
    if tst.size != ref.size:
        tst = tst.resize(ref.size)

    W, H = ref.size
    side = Image.new("RGB", (W * 2 + 10, H), "white")
    side.paste(ref, (0, 0))
    side.paste(tst, (W + 10, 0))
    side.save(os.path.join(a.out, "side_by_side.png"))
    Image.blend(ref, tst, 0.5).save(os.path.join(a.out, "blend.png"))

    diff = ImageChops.difference(ref, tst)
    arr = np.asarray(diff).astype(np.float32).mean(axis=2)
    heat = Image.fromarray(np.clip(arr * 3, 0, 255).astype(np.uint8))
    heat.save(os.path.join(a.out, "diff.png"))

    px_per_mm = a.dpi / 25.4
    t = int(round(a.tile * px_per_mm))
    tiles = []
    for y in range(0, H, t):
        for x in range(0, W, t):
            sub = arr[y:y + t, x:x + t]
            score = float(sub.mean())
            if score > 4.0:
                tiles.append({
                    "x_mm": round(x / px_per_mm, 1), "y_mm": round(y / px_per_mm, 1),
                    "size_mm": a.tile, "mean_abs_diff": round(score, 1),
                    "max_abs_diff": round(float(sub.max()), 1),
                })
    tiles.sort(key=lambda r: -r["mean_abs_diff"])
    summary = {
        "ref": os.path.abspath(a.ref), "test": os.path.abspath(a.test),
        "page": a.page, "page_mm": [round(v, 1) for v in ref_mm],
        "test_page_mm": [round(v, 1) for v in tst_mm],
        "global_mean_abs_diff": round(float(arr.mean()), 2),
        "pct_pixels_changed_gt32": round(float((arr > 32).mean() * 100), 2),
        "worst_tiles": tiles[:40],
        "n_tiles_over_threshold": len(tiles),
    }
    with open(os.path.join(a.out, "compare.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "worst_tiles"}))
    for r in tiles[:12]:
        print("  tile @(%5.0f,%5.0f) mm  mean %5.1f  max %5.1f" %
              (r["x_mm"], r["y_mm"], r["mean_abs_diff"], r["max_abs_diff"]))


if __name__ == "__main__":
    main()
