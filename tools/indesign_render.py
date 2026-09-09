# -*- coding: utf-8 -*-
"""
Render an IDML through a running InDesign (Windows COM) and dump what InDesign
actually made of it.  Used to close the loop QGIS -> IDML -> InDesign -> PDF.

    python tools/indesign_render.py --idml "X.idml" --out outdir [--pages 1-2]

Writes into outdir:
    indesign.pdf      pages exported with [High Quality Print]
    preflight.json    missing fonts, link status, overset stories
    items_p<N>.json   every page item on the exported pages with fill/stroke/
                      transparency/geometry as InDesign resolved them

Requires pywin32 and an InDesign that is already running (or startable).
User interaction is switched off so missing-font / missing-link dialogs cannot
block the call.
"""
import argparse
import json
import os
import shutil
import sys
import time

import win32com.client as w32

PDF_TYPE = 1952403524          # ExportFormat.PDF_TYPE  ('pdf ')
NEVER_INTERACT = 1699640946    # UserInteractionLevels.NEVER_INTERACT
INTERACT_ALL = 1699311169      # UserInteractionLevels.INTERACT_WITH_ALL
PT2MM = 25.4 / 72.0


def _name(obj):
    try:
        return obj.Name
    except Exception:
        return None


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def dump_item(it):
    cls = _safe(lambda: it.constructor.name) or _safe(lambda: type(it).__name__)
    gb = _safe(lambda: list(it.GeometricBounds))
    rec = {
        "type": cls,
        "id": _safe(lambda: it.Id),
        "name": _safe(lambda: it.Name),
        "label": _safe(lambda: it.Label),
        "bounds_mm": [round(v * PT2MM, 2) for v in gb] if gb else None,   # y1 x1 y2 x2
        "fill": _safe(lambda: _name(it.FillColor)),
        "fill_tint": _safe(lambda: it.FillTint),
        "stroke": _safe(lambda: _name(it.StrokeColor)),
        "stroke_weight": _safe(lambda: it.StrokeWeight),
        "stroke_type": _safe(lambda: _name(it.StrokeType)),
        "stroke_tint": _safe(lambda: it.StrokeTint),
        "opacity": _safe(lambda: it.TransparencySettings.BlendingSettings.Opacity),
        "fill_opacity": _safe(lambda: it.FillTransparencySettings.BlendingSettings.Opacity),
        "stroke_opacity": _safe(lambda: it.StrokeTransparencySettings.BlendingSettings.Opacity),
        "corner_tl": _safe(lambda: it.TopLeftCornerOption),
        "corner_radius": _safe(lambda: it.TopLeftCornerRadius),
        "visible": _safe(lambda: it.Visible),
        "layer": _safe(lambda: _name(it.ItemLayer)),
    }
    if cls == "TextFrame":
        rec["overflows"] = _safe(lambda: it.Overflows)
        rec["text"] = _safe(lambda: it.Contents)
        if isinstance(rec["text"], str):
            rec["text"] = rec["text"][:120]
        rec["font"] = _safe(lambda: _name(it.Texts.Item(1).AppliedFont))
        rec["font_style"] = _safe(lambda: it.Texts.Item(1).FontStyle)
        rec["point_size"] = _safe(lambda: it.Texts.Item(1).PointSize)
        rec["text_fill"] = _safe(lambda: _name(it.Texts.Item(1).FillColor))
        rec["inset"] = _safe(lambda: list(it.TextFramePreferences.InsetSpacing))
        rec["vjust"] = _safe(lambda: it.TextFramePreferences.VerticalJustification)
    # placed graphics
    n_gr = _safe(lambda: it.AllGraphics.Count, 0)
    if n_gr:
        g = it.AllGraphics.Item(1)
        rec["graphic"] = _safe(lambda: g.constructor.name)
        rec["link"] = _safe(lambda: g.ItemLink.FilePath)
        rec["link_status"] = _safe(lambda: g.ItemLink.Status)
        rec["graphic_bounds_mm"] = _safe(lambda: [round(v * PT2MM, 2) for v in g.GeometricBounds])
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--idml", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", default="1")
    ap.add_argument("--preset", default="[High Quality Print]")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    app = w32.Dispatch("InDesign.Application")
    app.ScriptPreferences.UserInteractionLevel = NEVER_INTERACT
    t0 = time.time()
    # app.Open() on a file the user already has open returns THAT document
    # (with all their unsaved edits) - and we would close it below.  So open a
    # uniquely named copy next to the original (links are relative to the IDML
    # folder, the copy must live in the same folder) and only ever close that.
    src = os.path.abspath(a.idml)
    tmp = os.path.join(os.path.dirname(src),
                       "~render_%d_%s" % (os.getpid(), os.path.basename(src)))
    shutil.copy2(src, tmp)
    n_before = app.Documents.Count
    doc = app.Open(tmp)
    own_doc = app.Documents.Count == n_before + 1
    try:
        pre = {
            "idml": os.path.abspath(a.idml),
            "open_seconds": round(time.time() - t0, 1),
            "pages": doc.Pages.Count,
            "spreads": doc.Spreads.Count,
            "page_size_mm": [round(doc.DocumentPreferences.PageWidth, 2),
                             round(doc.DocumentPreferences.PageHeight, 2)],
            "fonts": [],
            "links": [],
            "overset_stories": 0,
            "stories": doc.Stories.Count,
        }
        for i in range(1, doc.Fonts.Count + 1):
            f = doc.Fonts.Item(i)
            pre["fonts"].append({"name": f.Name, "status": _safe(lambda: f.Status),
                                 "ps": _safe(lambda: f.PostScriptName)})
        for i in range(1, doc.Links.Count + 1):
            ln = doc.Links.Item(i)
            pre["links"].append({"name": ln.Name, "status": _safe(lambda: ln.Status),
                                 "path": _safe(lambda: ln.FilePath)})
        for i in range(1, doc.Stories.Count + 1):
            if _safe(lambda: doc.Stories.Item(i).Overflows, False):
                pre["overset_stories"] += 1
        with open(os.path.join(a.out, "preflight.json"), "w", encoding="utf-8") as fh:
            json.dump(pre, fh, indent=1, ensure_ascii=False)

        # item dumps for the exported pages
        first, last = (a.pages.split("-") + [a.pages])[:2]
        for p in range(int(first), int(last) + 1):
            page = doc.Pages.Item(p)
            items = []
            for j in range(1, page.AllPageItems.Count + 1):
                items.append(dump_item(page.AllPageItems.Item(j)))
            with open(os.path.join(a.out, "items_p%d.json" % p), "w", encoding="utf-8") as fh:
                json.dump(items, fh, indent=1, ensure_ascii=False)

        # PDF
        app.PDFExportPreferences.PageRange = a.pages
        app.PDFExportPreferences.ViewPDF = False
        preset = app.PDFExportPresets.Item(a.preset)
        pdf = os.path.join(os.path.abspath(a.out), "indesign.pdf")
        if os.path.exists(pdf):
            os.remove(pdf)
        doc.Export(PDF_TYPE, pdf, False, preset)
        # export is asynchronous in recent versions - wait for the file
        for _ in range(600):
            if os.path.exists(pdf) and os.path.getsize(pdf) > 0:
                break
            time.sleep(0.5)
        print(json.dumps({"pdf": pdf, "pages": pre["pages"], "fonts": len(pre["fonts"]),
                          "links": len(pre["links"]), "overset": pre["overset_stories"],
                          "seconds": round(time.time() - t0, 1)}))
    finally:
        if own_doc:
            _safe(lambda: doc.Close(1852776480))     # SaveOptions.NO
        else:
            print("WARNING: InDesign handed back an already-open document; left it open",
                  file=sys.stderr)
        _safe(lambda: os.remove(tmp))
        app.ScriptPreferences.UserInteractionLevel = INTERACT_ALL


if __name__ == "__main__":
    sys.exit(main())
