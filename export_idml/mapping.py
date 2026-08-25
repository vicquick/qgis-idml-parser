"""Map QGIS layout items -> IDML page items.

Strategy (v1):
  QgsLayoutItemLabel     -> TextFrame + Story (native text; HTML mode resolved
                            through QTextDocument into styled runs)
  QgsLayoutItemShape     -> Rectangle / Oval / Polygon (native PathGeometry)
  QgsLayoutItemPolygon   -> Polygon (native)
  QgsLayoutItemPolyline  -> GraphicLine (native, open path)
  QgsLayoutItemPicture   -> Rectangle frame + Image/PDF + Link (referenced)
  QgsLayoutItemMap       -> rendered alone to a vector PDF -> Rectangle
                            frame + PDF + Link (referenced)
  everything else        -> item region rendered to a vector PDF snippet ->
                            placed as referenced PDF (legend, scalebar,
                            attribute tables, HTML frames, markers, ...)

Nothing here ever paints text or shapes through the Qt PDF engine -
that pipeline produces per-glyph text and subset fonts (QGIS #48419 /
#49979), which is exactly what we are bypassing.
"""

import os
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

from qgis.PyQt.QtCore import QRectF, QSizeF, QMarginsF, Qt
from qgis.PyQt.QtGui import QColor, QPainter, QPageSize, QPdfWriter, QImageReader

from qgis.core import (
    Qgis,
    QgsLayoutItem,
    QgsLayoutItemGroup,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemPage,
    QgsLayoutItemPicture,
    QgsLayoutItemPolygon,
    QgsLayoutItemPolyline,
    QgsLayoutItemShape,
    QgsLayoutExporter,
    QgsMapRendererCustomPainterJob,
    QgsUnitTypes,
)

from .geom import (
    MM2PT,
    ellipse_path,
    fmt,
    identity_at,
    mm,
    polygon_path,
    rect_path,
    rotation_at,
    triangle_path,
)
from .text_runs import (
    annotate_run_from_font,
    enum_int,
    apply_case_transforms,
    extract_runs,
    extract_structure,
    plain_text_paragraphs,
)
from .fonts import qfont_style_name


# ---------------------------------------------------------------- helpers


def _enum(owner, enum_name, member):
    """Scoped-enum access working across QGIS 3 (flat) and 4 (scoped)."""
    e = getattr(owner, enum_name, None)
    if e is not None and hasattr(e, member):
        return getattr(e, member)
    return getattr(owner, member)


def _layout_unit(name):
    """Qgis.LayoutUnit.<name> with QgsUnitTypes.Layout<name> fallback."""
    try:
        return getattr(Qgis.LayoutUnit, name)
    except AttributeError:
        return getattr(QgsUnitTypes, "Layout" + name)


def _render_unit(name):
    try:
        return getattr(Qgis.RenderUnit, name)
    except AttributeError:
        return getattr(QgsUnitTypes, "Render" + name)


def _render_size_to_pt(size, unit):
    """Convert a render-unit size to points (default: already points)."""
    for name, factor in (
        ("Points", 1.0),
        ("Millimeters", MM2PT),
        ("Pixels", 72.0 / 96.0),
        ("Inches", 72.0),
    ):
        try:
            if unit == _render_unit(name):
                return size * factor
        except AttributeError:
            continue
    return size


def file_uri(path):
    """InDesign-style link URI (spec example: LinkResourceURI="file:C:/x.jpg").

    Percent-encoded (spaces, umlauts, parens - InDesign resolves URIs, so
    "TEST Links/Bäderpark (Nord).jpg" must become %20/%C3%A4/%28...),
    then XML-escaped for the attribute context."""
    p = os.path.abspath(path).replace("\\", "/")
    return escape("file:" + quote(p, safe="/:"))


def item_frame_attrs(item, colors):
    """FillColor / StrokeColor attributes from QGIS frame+background."""
    attrs = []
    if item.hasBackground():
        attrs.append('FillColor="{}"'.format(colors.ref(item.backgroundColor())))
    else:
        attrs.append('FillColor="Swatch/None"')
    if item.frameEnabled():
        attrs.append('StrokeColor="{}"'.format(colors.ref(item.frameStrokeColor())))
        w = item.frameStrokeWidth()
        w_pt = w.length() * _unit_to_pt_factor(w.units())
        attrs.append('StrokeWeight="{}"'.format(fmt(w_pt)))
    else:
        attrs.append('StrokeColor="Swatch/None"')
        attrs.append('StrokeWeight="0"')
    return " ".join(attrs)


def _unit_to_pt_factor(unit):
    try:
        for name, factor in (
            ("Millimeters", MM2PT),
            ("Points", 1.0),
            ("Centimeters", 10.0 * MM2PT),
            ("Inches", 72.0),
            ("Pixels", 72.0 / 96.0),
        ):
            try:
                if unit == _layout_unit(name):
                    return factor
            except AttributeError:
                continue
    except Exception:
        pass
    return MM2PT  # layout default unit is mm


def item_geometry(item, spread):
    """(w_pt, h_pt, item_transform_str) for a layout item on its spread.

    Item inner geometry is anchored at (0,0)-(w,h); ItemTransform places
    it in spread coordinates (page top-left at page_offset).
    QGIS itemRotation() rotates about the item centre.

    IMPORTANT: pagePos() returns the position of the item's REFERENCE
    POINT (may be centre, lower-left, ...) - QGraphicsItem pos() is the
    true unrotated top-left in scene coords, so use pos() - page.pos().
    """
    r = item.rect()
    w_pt = mm(r.width())
    h_pt = mm(r.height())
    ox, oy = spread.page_offset()
    page = None
    try:
        if item.page() >= 0:
            page = item.layout().pageCollection().page(item.page())
    except Exception:
        pass
    if page is not None:
        tx = ox + mm(item.pos().x() - page.pos().x())
        ty = oy + mm(item.pos().y() - page.pos().y())
    else:
        pos = item.pagePos()  # fallback; correct for UpperLeft reference
        tx = ox + mm(pos.x())
        ty = oy + mm(pos.y())
    rot = item.itemRotation()
    if rot:
        # pos() is ALREADY centre-pivot adjusted by QGIS
        # (setItemRotation(..., adjustPosition=True)) - rotate about the
        # local origin only, or the pivot would be applied twice.
        transform = rotation_at(rot, tx, ty)
    else:
        transform = identity_at(tx, ty)
    return w_pt, h_pt, transform


def _pdf_writer(path, w_pt, h_pt, dpi):
    writer = QPdfWriter(path)
    writer.setResolution(int(dpi))
    try:
        writer.setPageSize(QPageSize(QSizeF(w_pt, h_pt), QPageSize.Unit.Point))
    except AttributeError:  # Qt5 enum spelling
        writer.setPageSize(QPageSize(QSizeF(w_pt, h_pt), QPageSize.Point))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))
    return writer


def placed_pdf_xml(idgen, colors, item_attrs, w_pt, h_pt, transform, pdf_path,
                   name='"$ID/"'):
    """Rectangle frame + placed PDF + Link (referenced, never embedded)."""
    rect_id = idgen.next("fr")
    pdf_id = idgen.next("pdf")
    link_id = idgen.next("lnk")
    return (
        '<Rectangle Self="{rid}" ContentType="GraphicType" ItemLayer="qxLayer1" '
        'AppliedObjectStyle="ObjectStyle/$ID/[Normal Graphics Frame]" '
        'Visible="true" Name={name} {attrs} ItemTransform="{tf}">'
        "<Properties>{path}</Properties>"
        '<PDF Self="{pid}" ItemTransform="1 0 0 1 0 0" Visible="true" Name="$ID/" '
        'GrayVectorPolicy="IgnoreAll" RGBVectorPolicy="IgnoreAll" '
        'CMYKVectorPolicy="IgnoreAll" AppliedObjectStyle="ObjectStyle/$ID/[None]">'
        "<Properties>"
        '<GraphicBounds Left="0" Top="0" Right="{w}" Bottom="{h}"/>'
        "</Properties>"
        '<PDFAttribute PageNumber="1" PDFCrop="CropMedia" TransparentBackground="true"/>'
        '<Link Self="{lid}" AssetURL="$ID/" AssetID="$ID/" '
        'LinkResourceURI="{uri}" LinkResourceFormat="$ID/Adobe PDF" '
        'StoredState="Normal" LinkClassID="35906" LinkClientID="257" '
        'LinkResourceModified="false" LinkObjectModified="false" '
        'ShowInUI="true" CanEmbed="true" CanUnembed="true" CanPackage="true" '
        'ImportPolicy="NoAutoImport" ExportPolicy="NoAutoExport"/>'
        "</PDF>"
        "</Rectangle>".format(
            rid=rect_id,
            pid=pdf_id,
            lid=link_id,
            name=name,
            attrs=item_attrs,
            tf=transform,
            path=rect_path(w_pt, h_pt),
            w=fmt(w_pt),
            h=fmt(h_pt),
            uri=file_uri(pdf_path),
        )
    )


# ---------------------------------------------------------------- label


def _natural_width_pt(paragraphs):
    """Widest natural (unwrapped) line of the label text, in points.

    QFontMetricsF measures in device px at the 96 dpi logical screen,
    so multiply by 72/96.  Soft hyphens are invisible unless breaking."""
    from qgis.PyQt.QtGui import QFont, QFontMetricsF

    widest = 0.0
    for para in paragraphs:
        line_w = 0.0
        for run in para["runs"]:
            f = QFont(run["family"])
            f.setPointSizeF(run["size_pt"])
            try:
                if run["style"]:
                    f.setStyleName(run["style"])
            except Exception:
                pass
            fm = QFontMetricsF(f)
            segments = run["text"].replace(chr(0xAD), "").split("\n")
            for si, seg in enumerate(segments):
                if si > 0:  # forced line break ends the current line
                    widest = max(widest, line_w)
                    line_w = 0.0
                line_w += fm.horizontalAdvance(seg)
        widest = max(widest, line_w)
    return widest * 72.0 / 96.0


def _export_table_label(item, pkg, spread, table, w_pt, h_pt, transform):
    """HTML label whose content is a single <table>: emit one TextFrame
    per column at the exact QGIS column split (width fractions from the
    table's percentage constraints), grouped under the item's name."""
    tx, ty = (float(v) for v in transform.split()[4:6])
    pad = table["cell_padding_pt"]
    group_id = pkg.idgen.next("grp")
    spread.begin_group(group_id, item.id() or "")
    try:
        x_off = 0.0
        for ci, (frac, col_paras) in enumerate(
            zip(table["col_fractions"], table["columns"])
        ):
            col_w = w_pt * frac
            if not col_paras:
                x_off += col_w
                continue
            story_id = pkg.add_story(col_paras)
            frame_id = pkg.idgen.next("tf")
            name = quoteattr("{}_col{}".format(item.id() or "table", ci + 1))
            spread.add(
                '<TextFrame Self="{fid}" ParentStory="{sid}" ContentType="TextType" '
                'ItemLayer="qxLayer1" '
                'AppliedObjectStyle="ObjectStyle/$ID/[Normal Text Frame]" '
                'PreviousTextFrame="n" NextTextFrame="n" Visible="true" Name={name} '
                'FillColor="Swatch/None" StrokeColor="Swatch/None" StrokeWeight="0" '
                'ItemTransform="{tf}">'
                "<Properties>{path}</Properties>"
                '<TextFramePreference TextColumnCount="1" TextColumnGutter="12" '
                'FirstBaselineOffset="AscentOffset" AutoSizingType="HeightOnly" '
                'AutoSizingReferencePoint="TopLeftPoint" '
                'UseMinimumHeightForAutoSizing="false" '
                'UseNoLineBreaksForAutoSizing="false" '
                'VerticalJustification="TopAlign">'
                "<Properties>"
                '<InsetSpacing type="list">'
                '<ListItem type="unit">0</ListItem>'
                '<ListItem type="unit">{pad}</ListItem>'
                '<ListItem type="unit">0</ListItem>'
                '<ListItem type="unit">{pad}</ListItem>'
                "</InsetSpacing>"
                "</Properties>"
                "</TextFramePreference>"
                "</TextFrame>".format(
                    fid=frame_id,
                    sid=story_id,
                    name=name,
                    tf=identity_at(tx + x_off, ty),
                    path=rect_path(col_w, h_pt),
                    pad=fmt(pad),
                )
            )
            x_off += col_w
    finally:
        spread.end_group()


def _evaluated_text_format(item):
    """Text format with data-defined overrides (per-atlas-feature font /
    size / color expressions) resolved - mirrors what QGIS renders."""
    tf = item.textFormat()
    try:
        from qgis.core import QgsRenderContext, QgsTextFormat

        props = tf.dataDefinedProperties()
        if props and props.hasActiveProperties():
            tf = QgsTextFormat(tf)
            rc = QgsRenderContext()
            rc.setExpressionContext(item.createExpressionContext())
            tf.updateDataDefinedProperties(rc)
    except Exception:
        pass
    return tf


_QGIS_CAPS = {1: "AllCaps", 3: "SmallCaps"}


def _apply_tf_caps(tf, paragraphs):
    """QgsTextFormat.capitalization() -> IDML attr or text transform."""
    try:
        caps = enum_int(tf.capitalization())
    except Exception:
        return
    try:
        if caps == enum_int(_enum(Qgis, "Capitalization", "AllSmallCaps")):
            caps = 3
    except Exception:
        pass
    if caps == 0:
        return
    for p in paragraphs:
        for r in p["runs"]:
            if caps in _QGIS_CAPS:
                r["caps"] = _QGIS_CAPS[caps]
            elif caps == 2:
                r["text"] = r["text"].lower()
            elif caps == 4:
                r["text"] = r["text"].title()


def export_label(item, pkg, spread, ctx):
    w_pt, h_pt, transform = item_geometry(item, spread)
    tf = _evaluated_text_format(item)
    font = tf.font()
    try:
        size_pt = _render_size_to_pt(tf.size(), tf.sizeUnit())
    except Exception:
        size_pt = tf.size()
    color = tf.color()

    halign = {
        int(Qt.AlignmentFlag.AlignLeft): "LeftAlign",
        int(Qt.AlignmentFlag.AlignHCenter): "CenterAlign",
        int(Qt.AlignmentFlag.AlignRight): "RightAlign",
        int(Qt.AlignmentFlag.AlignJustify): "FullyJustified",
    }.get(int(item.hAlign()) & int(Qt.AlignmentFlag.AlignHorizontal_Mask), "LeftAlign")

    text = item.currentText()
    html_mode = item.mode() == _enum(QgsLayoutItemLabel, "Mode", "ModeHtml")
    if html_mode:
        font.setPointSizeF(size_pt)
        structure = extract_structure(text, font, size_pt, color)
        tables = [e for e in structure if e["type"] == "table"]
        loose_paras = [e for e in structure if e["type"] == "para" and e["runs"]]
        if len(tables) == 1 and not loose_paras and not item.itemRotation():
            # HTML column table (Spielelemente pattern) -> one TextFrame
            # per column, side by side, exactly the QGIS split
            return _export_table_label(
                item, pkg, spread, tables[0], w_pt, h_pt, transform
            )
        paragraphs = []
        for e in structure:
            if e["type"] == "table":
                for col in e["columns"]:
                    paragraphs.extend(col)
            else:
                paragraphs.append(e)
    else:
        # named style (e.g. "Thin") often isn't reflected in QFont.styleName()
        if not font.styleName():
            try:
                named = tf.namedStyle()
                if named:
                    font.setStyleName(named)
            except Exception:
                pass
        paragraphs = plain_text_paragraphs(text, font, size_pt, color, align=halign)
        # font-mode: single line breaks are soft breaks
        for p in paragraphs:
            p["align"] = halign

    if not html_mode:
        # ---- Font-mode typography that QgsTextRenderer would draw ----
        # letter/word spacing + QFont capitalization
        for p in paragraphs:
            for r in p["runs"]:
                annotate_run_from_font(r, font, size_pt)
                apply_case_transforms(r)
        _apply_tf_caps(tf, paragraphs)
        # format-level line spacing
        try:
            lh = tf.lineHeight()
            if tf.lineHeightUnit() == _render_unit("Percentage"):
                if lh and abs(lh - 1.0) > 0.001:
                    for p in paragraphs:
                        p["line_height_pct"] = lh * 100.0
            else:
                lp = _render_size_to_pt(lh, tf.lineHeightUnit())
                if lp > 0:
                    for p in paragraphs:
                        p["leading_pt"] = lp
        except Exception:
            pass
        # buffer/halo -> outlined type (stroke straddles the outline, so
        # weight = 2 x buffer radius)
        try:
            buf = tf.buffer()
            if buf.enabled():
                bpt = _render_size_to_pt(buf.size(), buf.sizeUnit())
                for p in paragraphs:
                    for r in p["runs"]:
                        r["stroke_color"] = buf.color()
                        r["stroke_weight_pt"] = 2.0 * bpt
                        if buf.opacity() < 1.0:
                            r["stroke_tint"] = buf.opacity() * 100.0
        except Exception:
            pass

    story_id = pkg.add_story(paragraphs)

    valign = {
        int(Qt.AlignmentFlag.AlignTop): "TopAlign",
        int(Qt.AlignmentFlag.AlignVCenter): "CenterAlign",
        int(Qt.AlignmentFlag.AlignBottom): "BottomAlign",
    }.get(int(item.vAlign()) & int(Qt.AlignmentFlag.AlignVertical_Mask), "TopAlign")

    inset_x = mm(item.marginX())
    inset_y = mm(item.marginY())

    bg_transparency = ""
    if item.hasBackground() and 0 < item.backgroundColor().alpha() < 255:
        bg_transparency = _transparency_xml(
            fill_alpha=item.backgroundColor().alpha()
        )

    shadow_xml = ""
    if not html_mode:
        # text drop shadow -> object drop shadow on the (transparent)
        # frame: shadows exactly the visible glyphs
        try:
            sh = tf.shadow()
            if sh.enabled():
                import math

                d = _render_size_to_pt(sh.offsetDistance(), sh.offsetUnit())
                a = math.radians(sh.offsetAngle())  # clockwise from north
                blur = _render_size_to_pt(sh.blurRadius(), sh.blurRadiusUnit())
                shadow_xml = (
                    '<TransparencySetting><DropShadowSetting Mode="Drop" '
                    'BlendMode="Multiply" Opacity="{op}" Radius="{blur}" '
                    'XOffset="{dx}" YOffset="{dy}" EffectColor={col} '
                    'KnockedOut="true"/></TransparencySetting>'.format(
                        op=fmt(sh.opacity() * 100.0),
                        blur=fmt(blur),
                        dx=fmt(d * math.sin(a)),
                        dy=fmt(-d * math.cos(a)),
                        col=quoteattr(pkg.colors.ref(sh.color())),
                    )
                )
        except Exception:
            pass
        # text background chip (Format > Background) - approximated with
        # the frame bounds (QGIS sizes it to the text)
        try:
            bgs = tf.background()
            if bgs.enabled():
                t = enum_int(bgs.type())  # 0 rect,1 square,2 ellipse,3 circle,4 svg,5 marker
                if t in (0, 1, 2, 3):
                    tag = "Oval" if t in (2, 3) else "Rectangle"
                    corner = ""
                    if tag == "Rectangle":
                        try:
                            rad = bgs.radii()
                            rpt = _render_size_to_pt(
                                max(rad.width(), rad.height()), bgs.radiiUnit()
                            )
                            rpt = min(rpt, w_pt / 2.0, h_pt / 2.0)
                            if rpt > 0:
                                corner = " ".join(
                                    '{}CornerOption="RoundedCorner" '
                                    '{}CornerRadius="{}"'.format(c, c, fmt(rpt))
                                    for c in ("TopLeft", "TopRight",
                                              "BottomLeft", "BottomRight")
                                ) + " "
                        except Exception:
                            pass
                    path = ellipse_path(w_pt, h_pt) if tag == "Oval" else rect_path(w_pt, h_pt)
                    spread.add(
                        '<{tag} Self="{sid}" ContentType="Unassigned" '
                        'ItemLayer="qxLayer1" '
                        'AppliedObjectStyle="ObjectStyle/$ID/[None]" '
                        'Visible="true" Name={nm} FillColor="{f}" '
                        'StrokeColor="{s}" StrokeWeight="{sw}" '
                        '{corner}ItemTransform="{tfm}">'
                        "<Properties>{path}</Properties>"
                        "</{tag}>".format(
                            tag=tag,
                            sid=pkg.idgen.next("chip"),
                            nm=quoteattr((item.id() or "label") + "_bg"),
                            f=pkg.colors.ref(bgs.fillColor()),
                            s=pkg.colors.ref(bgs.strokeColor()),
                            sw=fmt(_render_size_to_pt(bgs.strokeWidth(),
                                                      bgs.strokeWidthUnit())),
                            corner=corner,
                            tfm=transform,
                            path=path,
                        )
                    )
                else:
                    ctx.warn(
                        "label '{}': SVG/marker text background not exported".format(
                            item.id()
                        )
                    )
        except Exception:
            pass

    # QGIS frames are often sized exactly to the rendered text; InDesign's
    # composer can run a hair wider -> overset.  For labels with no visible
    # frame box (no background, no border) let InDesign auto-size the
    # frame anchored so the text stays put: visually identical, overset
    # impossible.  Multi-line text only grows in height (width growth
    # would unwrap paragraphs).
    autosize_attrs = ""
    if not item.hasBackground() and not item.frameEnabled():
        # A label counts as single-line ONLY if its natural (unwrapped)
        # width fits the QGIS frame - if QGIS wrapped it, the frame width
        # is authoritative and must arrive unchanged in the IDML.
        inner_w = w_pt - 2 * inset_x
        multiline = (
            len(paragraphs) > 1
            or any("\n" in r["text"] for p in paragraphs for r in p["runs"])
            or _natural_width_pt(paragraphs) > inner_w * 1.05
        )
        as_type = "HeightOnly" if multiline else "HeightAndWidth"
        v = {"TopAlign": "Top", "CenterAlign": "Center", "BottomAlign": "Bottom"}[valign]
        h = {"LeftAlign": "Left", "CenterAlign": "Center", "RightAlign": "Right",
             "FullyJustified": "Left"}[halign]
        ref = {
            ("Top", "Left"): "TopLeftPoint",
            ("Top", "Center"): "TopCenterPoint",
            ("Top", "Right"): "TopRightPoint",
            ("Center", "Left"): "LeftCenterPoint",
            ("Center", "Center"): "CenterPoint",
            ("Center", "Right"): "RightCenterPoint",
            ("Bottom", "Left"): "BottomLeftPoint",
            ("Bottom", "Center"): "BottomCenterPoint",
            ("Bottom", "Right"): "BottomRightPoint",
        }[(v, h)]
        # single-line (HeightAndWidth): forbid line breaks while sizing,
        # otherwise InDesign collapses the frame to minimum wrap width
        # and re-wraps everything narrow.  Multi-line (HeightOnly) keeps
        # the original width, so breaks stay allowed.
        no_breaks = "true" if as_type == "HeightAndWidth" else "false"
        autosize_attrs = (
            ' AutoSizingReferencePoint="{}" '
            'UseMinimumHeightForAutoSizing="false" '
            'UseNoLineBreaksForAutoSizing="{}"'.format(ref, no_breaks)
        )
    else:
        as_type = None

    frame_id = pkg.idgen.next("tf")
    spread.add(
        '<TextFrame Self="{fid}" ParentStory="{sid}" ContentType="TextType" '
        'ItemLayer="qxLayer1" '
        'AppliedObjectStyle="ObjectStyle/$ID/[Normal Text Frame]" '
        'PreviousTextFrame="n" NextTextFrame="n" Visible="true" Name={name} '
        "{attrs} "
        'ItemTransform="{tf}">'
        "<Properties>{path}</Properties>"
        "{bg_transparency}{shadow}"
        '<TextFramePreference TextColumnCount="1" TextColumnGutter="12" '
        'FirstBaselineOffset="AscentOffset" AutoSizingType="{astype}"{autosize} '
        'VerticalJustification="{valign}">'
        "<Properties>"
        '<InsetSpacing type="list">'  # InDesign order: top, left, bottom, right
        '<ListItem type="unit">{iy}</ListItem>'
        '<ListItem type="unit">{ix}</ListItem>'
        '<ListItem type="unit">{iy}</ListItem>'
        '<ListItem type="unit">{ix}</ListItem>'
        "</InsetSpacing>"
        "</Properties>"
        "</TextFramePreference>"
        "</TextFrame>".format(
            fid=frame_id,
            sid=story_id,
            name=_item_name(item),
            attrs=item_frame_attrs(item, pkg.colors),
            tf=transform,
            path=rect_path(w_pt, h_pt),
            valign=valign,
            astype=as_type or "Off",
            autosize=autosize_attrs,
            ix=fmt(inset_x),
            iy=fmt(inset_y),
            bg_transparency=bg_transparency,
            shadow=shadow_xml,
        )
    )


# ---------------------------------------------------------------- shapes


def _item_name(item):
    """IDML Name attribute (already quoted) - mirrors QGIS item id in the
    InDesign layers panel."""
    try:
        name = item.id()
    except Exception:
        name = ""
    return quoteattr(name or "$ID/")


def _transparency_xml(symbol_opacity=1.0, fill_alpha=255, stroke_alpha=255):
    """IDML transparency children for a page item.

    QGIS symbol-level opacity -> object TransparencySetting;
    partial color alphas -> Fill/StrokeTransparencySetting."""
    parts = []
    if symbol_opacity < 0.999:
        parts.append(
            '<TransparencySetting><BlendingSetting Opacity="{}"/>'
            "</TransparencySetting>".format(fmt(symbol_opacity * 100.0))
        )
    if 0 < fill_alpha < 255:
        parts.append(
            '<FillTransparencySetting><BlendingSetting Opacity="{}"/>'
            "</FillTransparencySetting>".format(fmt(fill_alpha / 255.0 * 100.0))
        )
    if 0 < stroke_alpha < 255:
        parts.append(
            '<StrokeTransparencySetting><BlendingSetting Opacity="{}"/>'
            "</StrokeTransparencySetting>".format(fmt(stroke_alpha / 255.0 * 100.0))
        )
    return "".join(parts)


def _dd_color(sl, prop_name, ctx, default):
    """Evaluate a data-defined symbol-layer color (e.g. rating squares
    whose fill is a CASE expression over atlas-feature attributes).
    Returns default when the property is absent/inactive."""
    if ctx is None:
        return default
    try:
        from qgis.core import QgsSymbolLayer

        key = _enum(QgsSymbolLayer, "Property", "Property" + prop_name)
        props = sl.dataDefinedProperties()
        p = props.property(key) if props else None
        if not p or not p.isActive():
            return default
        val = p.valueAsColor(ctx, default)
        # newer PyQGIS returns (QColor, ok)
        if isinstance(val, tuple):
            color, ok = val
            return color if ok else default
        return val if val and val.isValid() else default
    except Exception:
        return default


def _fill_stroke_from_symbol(symbol, colors, item=None):
    """Best-effort fill/stroke from a QgsFillSymbol's first simple layer.

    Data-defined fill/stroke colors are evaluated against the item's
    expression context (atlas feature attributes).
    Returns (fill_ref, stroke_ref, stroke_w_pt, transparency_xml)."""
    fill = "Swatch/None"
    stroke = "Swatch/None"
    stroke_w_pt = 0.0
    opacity = 1.0
    fill_alpha = 255
    stroke_alpha = 255
    ctx = None
    if item is not None:
        try:
            ctx = item.createExpressionContext()
        except Exception:
            ctx = None
    try:
        if symbol and symbol.symbolLayerCount() > 0:
            opacity = symbol.opacity()
            sl = symbol.symbolLayer(0)
            if hasattr(sl, "fillColor") and sl.fillColor().alpha() > 0:
                if getattr(sl, "brushStyle", None) is None or sl.brushStyle() != Qt.BrushStyle.NoBrush:
                    fc = _dd_color(sl, "FillColor", ctx, sl.fillColor())
                    fill = colors.ref(fc)
                    fill_alpha = fc.alpha()
            if hasattr(sl, "strokeColor") and sl.strokeColor().alpha() > 0:
                style_ok = True
                if hasattr(sl, "strokeStyle"):
                    style_ok = sl.strokeStyle() != Qt.PenStyle.NoPen
                if style_ok:
                    sc = _dd_color(sl, "StrokeColor", ctx, sl.strokeColor())
                    stroke = colors.ref(sc)
                    stroke_alpha = sc.alpha()
                    if hasattr(sl, "strokeWidth"):
                        w = sl.strokeWidth()
                        if hasattr(sl, "strokeWidthUnit"):
                            stroke_w_pt = _render_size_to_pt(w, sl.strokeWidthUnit())
                        else:
                            stroke_w_pt = w * MM2PT  # symbol default unit is mm
                        if stroke_w_pt <= 0:
                            stroke_w_pt = 0.5
            elif hasattr(sl, "color") and fill == "Swatch/None":
                fill = colors.ref(sl.color())
                fill_alpha = sl.color().alpha()
    except Exception:
        pass
    transparency = _transparency_xml(opacity, fill_alpha, stroke_alpha)
    return fill, stroke, stroke_w_pt, transparency


def export_shape(item, pkg, spread, ctx):
    w_pt, h_pt, transform = item_geometry(item, spread)
    fill, stroke, stroke_w, transparency = _fill_stroke_from_symbol(
        item.symbol(), pkg.colors, item=item
    )

    shape_type = item.shapeType()
    corner_attrs = ""
    if shape_type == _enum(QgsLayoutItemShape, "Shape", "Ellipse"):
        tag, path = "Oval", ellipse_path(w_pt, h_pt)
    elif shape_type == _enum(QgsLayoutItemShape, "Shape", "Triangle"):
        tag, path = "Polygon", triangle_path(w_pt, h_pt)
    else:
        tag, path = "Rectangle", rect_path(w_pt, h_pt)
        # rounded corners -> native InDesign corner options (editable)
        try:
            cr = item.cornerRadius()
            r_pt = cr.length() * _unit_to_pt_factor(cr.units())
        except Exception:
            r_pt = 0.0
        r_pt = min(r_pt, w_pt / 2.0, h_pt / 2.0)  # InDesign rejects larger
        if r_pt > 0:
            corner_attrs = " ".join(
                '{}CornerOption="RoundedCorner" {}CornerRadius="{}"'.format(
                    c, c, fmt(r_pt)
                )
                for c in ("TopLeft", "TopRight", "BottomLeft", "BottomRight")
            ) + " "

    self_id = pkg.idgen.next("sh")
    spread.add(
        '<{tag} Self="{sid}" ContentType="Unassigned" ItemLayer="qxLayer1" '
        'AppliedObjectStyle="ObjectStyle/$ID/[None]" Visible="true" Name={name} '
        'FillColor="{fill}" StrokeColor="{stroke}" StrokeWeight="{sw}" '
        "{corners}"
        'ItemTransform="{tf}">'
        "<Properties>{path}</Properties>"
        "{transparency}"
        "</{tag}>".format(
            tag=tag,
            sid=self_id,
            name=_item_name(item),
            fill=fill,
            stroke=stroke,
            sw=fmt(stroke_w),
            corners=corner_attrs,
            tf=transform,
            path=path,
            transparency=transparency,
        )
    )


def _nodes_to_points(item):
    """QgsLayoutNodesItem nodes in item-local mm -> list of (x_pt, y_pt)."""
    nodes = item.nodes()
    return [(mm(p.x()), mm(p.y())) for p in nodes]


def export_polygon(item, pkg, spread, ctx):
    w_pt, h_pt, transform = item_geometry(item, spread)
    try:
        points = _nodes_to_points(item)
    except Exception:
        points = []
    if len(points) < 2:
        return export_fallback(item, pkg, spread, ctx)
    fill, stroke, stroke_w, transparency = _fill_stroke_from_symbol(
        item.symbol(), pkg.colors, item=item
    )
    self_id = pkg.idgen.next("pg")
    spread.add(
        '<Polygon Self="{sid}" ContentType="Unassigned" ItemLayer="qxLayer1" '
        'AppliedObjectStyle="ObjectStyle/$ID/[None]" Visible="true" Name={name} '
        'FillColor="{fill}" StrokeColor="{stroke}" StrokeWeight="{sw}" '
        'ItemTransform="{tf}">'
        "<Properties>{path}</Properties>"
        "{transparency}"
        "</Polygon>".format(
            sid=self_id,
            name=_item_name(item),
            fill=fill,
            stroke=stroke,
            sw=fmt(stroke_w),
            tf=transform,
            path=polygon_path(points, closed=True),
            transparency=transparency,
        )
    )


def export_polyline(item, pkg, spread, ctx):
    w_pt, h_pt, transform = item_geometry(item, spread)
    try:
        points = _nodes_to_points(item)
    except Exception:
        points = []
    if len(points) < 2:
        return export_fallback(item, pkg, spread, ctx)
    stroke = "Color/Black"
    stroke_w = 1.0
    try:
        sym = item.symbol()  # QgsLineSymbol
        if sym and sym.symbolLayerCount() > 0:
            sl = sym.symbolLayer(0)
            if hasattr(sl, "color"):
                stroke = pkg.colors.ref(sl.color())
            if hasattr(sl, "width"):
                if hasattr(sl, "widthUnit"):
                    stroke_w = _render_size_to_pt(sl.width(), sl.widthUnit())
                else:
                    stroke_w = sl.width() * MM2PT  # symbol default unit is mm
    except Exception:
        pass
    self_id = pkg.idgen.next("gl")
    spread.add(
        '<GraphicLine Self="{sid}" ContentType="Unassigned" ItemLayer="qxLayer1" '
        'AppliedObjectStyle="ObjectStyle/$ID/[None]" Visible="true" Name={name} '
        'FillColor="Swatch/None" StrokeColor="{stroke}" StrokeWeight="{sw}" '
        'ItemTransform="{tf}">'
        "<Properties>{path}</Properties>"
        "</GraphicLine>".format(
            sid=self_id,
            name=_item_name(item),
            stroke=stroke,
            sw=fmt(stroke_w),
            tf=transform,
            path=polygon_path(points, closed=False),
        )
    )


# ---------------------------------------------------------------- picture


def _natural_size_pt(path, fallback_w, fallback_h):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".svg":
            from qgis.PyQt.QtSvg import QSvgRenderer

            r = QSvgRenderer(path)
            s = r.defaultSize()
            if s.isValid() and s.width() > 0:
                # SVG default size is CSS px at 96 dpi
                return s.width() * 72.0 / 96.0, s.height() * 72.0 / 96.0
        else:
            reader = QImageReader(path)
            s = reader.size()
            if s.isValid() and s.width() > 0:
                # assume 72 ppi natural size; scaling handles the rest
                return float(s.width()), float(s.height())
    except Exception:
        pass
    return fallback_w, fallback_h


def export_picture(item, pkg, spread, ctx):
    # data-defined picture paths resolve via evaluatedPath()
    src = None
    try:
        src = item.evaluatedPath()
    except AttributeError:
        pass
    if not src:
        src = item.picturePath()
    if not src or not os.path.exists(src):
        return export_fallback(item, pkg, spread, ctx)

    w_pt, h_pt, transform = item_geometry(item, spread)
    linked = ctx.copy_link(src)
    ext = os.path.splitext(linked)[1].lower()

    if ext == ".pdf":
        spread.add(
            placed_pdf_xml(
                pkg.idgen,
                pkg.colors,
                item_frame_attrs(item, pkg.colors),
                w_pt,
                h_pt,
                transform,
                linked,
                name=_item_name(item),
            )
        )
        return

    nat_w, nat_h = _natural_size_pt(linked, w_pt, h_pt)
    sx = w_pt / nat_w if nat_w else 1.0
    sy = h_pt / nat_h if nat_h else 1.0
    # QGIS default resize mode is Zoom: aspect-preserving fit, anchored
    # top-left.  Only Stretch fills the frame non-uniformly; Clip places
    # the image at natural size.
    try:
        mode = item.resizeMode()
        zoom = _enum(QgsLayoutItemPicture, "ResizeMode", "Zoom")
        zoom_rf = _enum(QgsLayoutItemPicture, "ResizeMode", "ZoomResizeFrame")
        clip = _enum(QgsLayoutItemPicture, "ResizeMode", "Clip")
        if mode in (zoom, zoom_rf):
            sx = sy = min(sx, sy)
        elif mode == clip:
            sx = sy = 1.0
    except Exception:
        pass

    rect_id = pkg.idgen.next("fr")
    img_id = pkg.idgen.next("img")
    link_id = pkg.idgen.next("lnk")
    fmt_name = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "tif": "TIFF",
                "tiff": "TIFF", "svg": "SVG", "gif": "GIF"}.get(ext.lstrip("."), "PNG")
    spread.add(
        '<Rectangle Self="{rid}" ContentType="GraphicType" ItemLayer="qxLayer1" '
        'AppliedObjectStyle="ObjectStyle/$ID/[Normal Graphics Frame]" '
        'Visible="true" Name={iname} {attrs} ItemTransform="{tf}">'
        "<Properties>{path}</Properties>"
        '<Image Self="{iid}" ItemTransform="{sx} 0 0 {sy} 0 0" Visible="true" '
        'Name="$ID/" AppliedObjectStyle="ObjectStyle/$ID/[None]" '
        'ImageRenderingIntent="UseColorSettings" LocalDisplaySetting="Default">'
        "<Properties>"
        '<Profile type="string">$ID/None</Profile>'
        '<GraphicBounds Left="0" Top="0" Right="{nw}" Bottom="{nh}"/>'
        "</Properties>"
        '<Link Self="{lid}" AssetURL="$ID/" AssetID="$ID/" '
        'LinkResourceURI="{uri}" LinkResourceFormat="$ID/{fmt}" '
        'StoredState="Normal" LinkClassID="35906" LinkClientID="257" '
        'LinkResourceModified="false" LinkObjectModified="false" '
        'ShowInUI="true" CanEmbed="true" CanUnembed="true" CanPackage="true" '
        'ImportPolicy="NoAutoImport" ExportPolicy="NoAutoExport"/>'
        "</Image>"
        "</Rectangle>".format(
            rid=rect_id,
            iid=img_id,
            lid=link_id,
            iname=_item_name(item),
            attrs=item_frame_attrs(item, pkg.colors),
            tf=transform,
            path=rect_path(w_pt, h_pt),
            sx=fmt(sx),
            sy=fmt(sy),
            nw=fmt(nat_w),
            nh=fmt(nat_h),
            uri=file_uri(linked),
            fmt=fmt_name,
        )
    )


# ---------------------------------------------------------------- map


def export_map(item, pkg, spread, ctx):
    w_pt, h_pt, transform = item_geometry(item, spread)
    if w_pt <= 0 or h_pt <= 0:
        return
    r = item.rect()
    dpi = ctx.dpi
    w_px = r.width() / 25.4 * dpi
    h_px = r.height() / 25.4 * dpi

    pdf_path = ctx.link_path("map_{}.pdf".format(ctx.next_asset_index()))
    ms = item.mapSettings(item.extent(), QSizeF(w_px, h_px), dpi, True)
    try:
        ms.setFlag(Qgis.MapSettingsFlag.ForceVectorOutput, True)
    except Exception:
        try:
            from qgis.core import QgsMapSettings

            ms.setFlag(QgsMapSettings.ForceVectorOutput, True)
        except Exception:
            pass
    if item.hasBackground():
        ms.setBackgroundColor(item.backgroundColor())
    else:
        ms.setBackgroundColor(QColor(0, 0, 0, 0))

    writer = _pdf_writer(pdf_path, w_pt, h_pt, dpi)
    painter = QPainter(writer)
    if not painter.isActive():
        raise RuntimeError("cannot open PDF writer for map: " + pdf_path)
    try:
        job = QgsMapRendererCustomPainterJob(ms, painter)
        job.renderSynchronously()
    finally:
        painter.end()

    spread.add(
        placed_pdf_xml(
            pkg.idgen,
            pkg.colors,
            item_frame_attrs(item, pkg.colors),
            w_pt,
            h_pt,
            transform,
            pdf_path,
            name=_item_name(item),
        )
    )


# ---------------------------------------------------------------- fallback


def export_fallback(item, pkg, spread, ctx):
    """Render just this item (all others hidden) to a vector PDF snippet
    and place it as a referenced PDF.  Used for legend, scalebar,
    attribute tables, HTML frames and anything not natively mapped."""
    # size the snippet from the *scene* bounding rect: for rotated items
    # renderRegion draws the rotated bounds, not the unrotated item rect
    br = item.sceneBoundingRect()
    bw_pt = mm(br.width())
    bh_pt = mm(br.height())
    if bw_pt <= 0 or bh_pt <= 0:
        return

    layout = item.layout()
    pdf_path = ctx.link_path(
        "{}_{}.pdf".format(type(item).__name__.lower(), ctx.next_asset_index())
    )

    others = [
        it
        for it in layout.items()
        if isinstance(it, QgsLayoutItem)
        and it is not item
        and not isinstance(it, QgsLayoutItemPage)
        and it.isVisible()
    ]
    # don't hide the item's own group parents
    parents = set()
    p = item.parentGroup() if hasattr(item, "parentGroup") else None
    while p:
        parents.add(p)
        p = p.parentGroup() if hasattr(p, "parentGroup") else None
    others = [it for it in others if it not in parents]

    rc = layout.renderContext()
    pages_visible = True
    try:
        pages_visible = rc.pagesVisible()
        rc.setPagesVisible(False)
    except Exception:
        pass

    for it in others:
        it.setVisibility(False)
    try:
        writer = _pdf_writer(pdf_path, bw_pt, bh_pt, ctx.dpi)
        painter = QPainter(writer)
        if not painter.isActive():
            raise RuntimeError("cannot open PDF writer: " + pdf_path)
        try:
            exporter = QgsLayoutExporter(layout)
            exporter.renderRegion(painter, br)  # layout (scene) coords, mm
        finally:
            painter.end()
    finally:
        for it in others:
            it.setVisibility(True)
        try:
            rc.setPagesVisible(pages_visible)
        except Exception:
            pass

    # place axis-aligned at the bounding rect's scene position
    # (rotation is already baked into the rendered PDF)
    ox, oy = spread.page_offset()
    page = item.layout().pageCollection().page(item.page()) if item.page() >= 0 else None
    page_y = page.pos().y() if page else 0.0
    page_x = page.pos().x() if page else 0.0
    tx = ox + mm(br.x() - page_x)
    ty = oy + mm(br.y() - page_y)
    spread.add(
        placed_pdf_xml(
            pkg.idgen,
            pkg.colors,
            'FillColor="Swatch/None" StrokeColor="Swatch/None" StrokeWeight="0"',
            bw_pt,
            bh_pt,
            identity_at(tx, ty),
            pdf_path,
            name=_item_name(item),
        )
    )


# ---------------------------------------------------------------- groups


def export_group(group, pkg, spread, ctx):
    """QGIS layout group -> IDML <Group> mirroring the item hierarchy.

    The Group's inner space equals the spread space (identity transform),
    so children keep their normal spread-coordinate ItemTransforms."""
    def _exportable(it):
        if not isinstance(it, QgsLayoutItem) or isinstance(it, QgsLayoutItemPage):
            return False
        if not it.isVisible():
            return False
        try:
            if it.excludeFromExports():
                return False
        except AttributeError:
            pass
        return True

    children = [it for it in group.items() if _exportable(it)]
    if not children:
        return
    children.sort(key=lambda i: i.zValue())
    group_id = pkg.idgen.next("grp")
    spread.begin_group(group_id, group.id() or "")
    try:
        for child in children:
            export_item(child, pkg, spread, ctx)
    finally:
        spread.end_group()


# ---------------------------------------------------------------- dispatch


def export_item(item, pkg, spread, ctx):
    if isinstance(item, QgsLayoutItemGroup):
        return export_group(item, pkg, spread, ctx)
    if isinstance(item, QgsLayoutItemLabel):
        return export_label(item, pkg, spread, ctx)
    if isinstance(item, QgsLayoutItemMap):
        return export_map(item, pkg, spread, ctx)
    if isinstance(item, QgsLayoutItemPicture):
        return export_picture(item, pkg, spread, ctx)
    if isinstance(item, QgsLayoutItemShape):
        return export_shape(item, pkg, spread, ctx)
    if isinstance(item, QgsLayoutItemPolygon):
        return export_polygon(item, pkg, spread, ctx)
    if isinstance(item, QgsLayoutItemPolyline):
        return export_polyline(item, pkg, spread, ctx)
    if isinstance(item, QgsLayoutItemPage):
        return  # pages are handled as spreads
    return export_fallback(item, pkg, spread, ctx)
