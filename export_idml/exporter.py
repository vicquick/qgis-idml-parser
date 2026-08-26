"""Orchestrator: QgsPrintLayout (+ optional atlas) -> .idml package.

Output layout on disk:

    <name>.idml
    <name> Links/            referenced PDFs (maps, fallback items), images
    Document fonts/          copies of used font files (optional)

Fonts and placed assets are *references* (IDML never embeds either);
shipping the Links + Document fonts folders alongside mirrors what
InDesign's own File > Package produces.
"""

import os
import shutil

from qgis.core import QgsLayoutItem, QgsLayoutItemGroup, QgsLayoutItemPage

from .fonts import FontIndex
from .geom import mm
from .idml_package import IdmlPackage
from .mapping import export_item


class ExportContext:
    def __init__(self, links_dir, dpi=300, copy_fonts=True, warnings=None):
        self.links_dir = links_dir
        self.dpi = dpi
        self.copy_fonts = copy_fonts
        self.warnings = warnings if warnings is not None else []
        self._asset_n = 0

    def warn(self, message):
        self.warnings.append(message)

    def next_asset_index(self):
        self._asset_n += 1
        return self._asset_n

    def link_path(self, filename):
        os.makedirs(self.links_dir, exist_ok=True)
        return os.path.join(self.links_dir, filename)

    def copy_link(self, src):
        """Copy an external asset into the Links folder; return new path.

        Dedups by source path within a run (atlas features reusing the
        same logo/photo copy once) and by file content across runs
        (re-exporting into the same folder reuses prior copies)."""
        import filecmp

        src_key = os.path.abspath(src)
        if not hasattr(self, "_copied"):
            self._copied = {}
        if src_key in self._copied:
            return self._copied[src_key]

        os.makedirs(self.links_dir, exist_ok=True)
        base = os.path.basename(src)
        dst = os.path.join(self.links_dir, base)
        if src_key != os.path.abspath(dst):
            root, ext = os.path.splitext(base)
            n = 1
            while os.path.exists(dst):
                try:
                    if os.path.samefile(src, dst) or filecmp.cmp(src, dst, shallow=False):
                        self._copied[src_key] = dst
                        return dst
                except OSError:
                    pass
                n += 1
                dst = os.path.join(self.links_dir, "{}_{}{}".format(root, n, ext))
            shutil.copy2(src, dst)
        self._copied[src_key] = dst
        return dst


def _page_items(layout, page_index):
    """Top-level exportable entities intersecting one page, in paint (z)
    order.  Intersection (not `page()`) so an item straddling a page
    boundary appears on every spread it visibly touches, as in QGIS.

    Groups are entities: their children are exported inside them and are
    therefore excluded here (parentGroup() is not None)."""
    page = layout.pageCollection().page(page_index)
    from qgis.PyQt.QtCore import QRectF

    page_rect = QRectF(
        page.pos().x(), page.pos().y(),
        page.pageSize().width(), page.pageSize().height(),
    )
    items = []
    for it in layout.items():
        if not isinstance(it, QgsLayoutItem):
            continue
        if isinstance(it, QgsLayoutItemPage):
            continue
        if not it.isVisible():
            continue
        try:
            if it.parentGroup() is not None:
                continue  # exported inside its group
        except AttributeError:
            pass
        try:
            if it.excludeFromExports():
                continue
        except AttributeError:
            pass
        inter = page_rect.intersected(it.sceneBoundingRect())
        if inter.width() < 0.01 or inter.height() < 0.01:
            continue
        items.append(it)

    def effective_z(it):
        # QGIS renders group children at their own z (interleaved with
        # ungrouped items); IDML groups are atomic containers.  Sorting a
        # group by its topmost child keeps content-on-top groups above
        # the items they overlap (e.g. boxes overlapping a map).
        if isinstance(it, QgsLayoutItemGroup):
            zs = [effective_z(c) for c in it.items() if isinstance(c, QgsLayoutItem)]
            if zs:
                return max(zs)
        return it.zValue()

    items.sort(key=effective_z)
    return items


def _page_background(layout, pkg, spread, page):
    """QGIS page background (Page Setup style symbol) -> full-bleed
    rectangle at the bottom of the spread (skipped for plain white)."""
    try:
        sym = layout.pageCollection().pageStyleSymbol()
        if sym is None or sym.symbolLayerCount() == 0:
            return
        sl = sym.symbolLayer(0)
        color = sl.fillColor() if hasattr(sl, "fillColor") else sl.color()
        if color.alpha() == 0:
            return
        if (color.red(), color.green(), color.blue(), color.alpha()) == (255, 255, 255, 255):
            return  # default white = InDesign's own page
        from .geom import rect_path, identity_at, fmt as _fmt
        from .mapping import _transparency_xml

        w, h = spread.w, spread.h
        ox, oy = spread.page_offset()
        spread.add(
            '<Rectangle Self="{sid}" ContentType="Unassigned" ItemLayer="qxLayer1" '
            'AppliedObjectStyle="ObjectStyle/$ID/[None]" Visible="true" '
            'Name="page background" FillColor="{fill}" StrokeColor="Swatch/None" '
            'StrokeWeight="0" ItemTransform="{tf}">'
            "<Properties>{path}</Properties>{transparency}</Rectangle>".format(
                sid=pkg.idgen.next("pbg"),
                fill=pkg.colors.ref(color),
                tf=identity_at(ox, oy),
                path=rect_path(w, h),
                transparency=_transparency_xml(fill_alpha=color.alpha()),
            )
        )
    except Exception:
        pass


def _export_pages(layout, pkg, ctx, warnings):
    n_pages = layout.pageCollection().pageCount()
    for page_index in range(n_pages):
        page = layout.pageCollection().page(page_index)
        spread = pkg.new_spread(mm(page.pageSize().width()), mm(page.pageSize().height()))
        # items straddling pages are positioned relative to THIS page
        spread.page_scene_x = page.pos().x()
        spread.page_scene_y = page.pos().y()
        _page_background(layout, pkg, spread, page)
        for item in _page_items(layout, page_index):
            try:
                export_item(item, pkg, spread, ctx)
            except Exception as e:  # one bad item must not kill the export
                warnings.append(
                    "{} '{}' skipped: {}".format(
                        type(item).__name__, item.id() or item.uuid(), e
                    )
                )


def export_layout_to_idml(
    layout,
    out_path,
    dpi=300,
    copy_fonts=True,
    atlas=False,
    feedback=None,
):
    """Export `layout` to `out_path` (.idml).

    atlas=True: iterate the layout's atlas; one spread (per layout page)
    per feature, all in ONE package.
    Returns dict with summary info.
    """
    out_path = os.path.abspath(out_path)
    if not out_path.lower().endswith(".idml"):
        out_path += ".idml"
    base = os.path.splitext(os.path.basename(out_path))[0]
    out_dir = os.path.dirname(out_path)
    links_dir = os.path.join(out_dir, base + " Links")

    if layout.pageCollection().pageCount() == 0:
        raise RuntimeError("Layout has no pages")
    page = layout.pageCollection().page(0)
    page_w_pt = mm(page.pageSize().width())
    page_h_pt = mm(page.pageSize().height())

    pkg = IdmlPackage(page_w_pt, page_h_pt, font_index=FontIndex())
    warnings = []
    ctx = ExportContext(links_dir, dpi=dpi, copy_fonts=copy_fonts, warnings=warnings)

    features_done = 0
    if atlas:
        atl = layout.atlas()
        if atl is None or not atl.enabled():
            raise RuntimeError("Atlas is not configured on this layout")
        if not atl.beginRender():
            raise RuntimeError("atlas.beginRender() failed")
        # count() is only populated once rendering has begun
        if atl.count() == 0:
            atl.endRender()
            raise RuntimeError("Atlas has no features (filter matches nothing?)")
        try:
            for i in range(atl.count()):
                atl.seekTo(i)
                layout.refresh()
                _export_pages(layout, pkg, ctx, warnings)
                features_done += 1
                if feedback:
                    feedback(i + 1, atl.count())
        finally:
            atl.endRender()
    else:
        _export_pages(layout, pkg, ctx, warnings)

    pkg.write(out_path)

    fonts_copied = []
    if copy_fonts:
        font_files = pkg.fonts.used_files()
        if font_files:
            fonts_dir = os.path.join(out_dir, "Document fonts")
            os.makedirs(fonts_dir, exist_ok=True)
            for f in font_files:
                dst = os.path.join(fonts_dir, os.path.basename(f))
                try:
                    if not os.path.exists(dst):
                        shutil.copy2(f, dst)
                    fonts_copied.append(dst)
                except OSError:
                    pass

    return {
        "idml": out_path,
        "links_dir": links_dir if os.path.isdir(links_dir) else None,
        "fonts_copied": fonts_copied,
        "spreads": len(pkg.spreads),
        "stories": len(pkg.stories),
        "atlas_features": features_done,
        "warnings": warnings,
    }
