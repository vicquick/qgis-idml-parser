"""Standalone smoke test - run with QGIS's python:

  "C:\\Program Files\\QGIS 3.44.13\\bin\\python-qgis.bat" standalone_test.py

Builds a project + layout containing every mapped item type, exports to
IDML, then runs the structural validator on the result.
"""

import os
import sys
import tempfile

from qgis.core import (
    QgsApplication,
    QgsFeature,
    QgsFillSymbol,
    QgsGeometry,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutItemLegend,
    QgsLayoutItemPicture,
    QgsLayoutItemShape,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # repo root -> import export_idml

qgs = QgsApplication([], False)
qgs.initQgis()

from export_idml.exporter import export_layout_to_idml  # noqa: E402
from export_idml.mapping import _enum  # noqa: E402


def build_layer():
    layer = QgsVectorLayer("Polygon?crs=EPSG:25832&field=name:string", "parcels", "memory")
    pr = layer.dataProvider()
    for i, ring in enumerate(
        [
            [(0, 0), (100, 0), (100, 80), (0, 80)],
            [(120, 10), (200, 10), (200, 70), (160, 90)],
        ]
    ):
        f = QgsFeature(layer.fields())
        f.setAttribute(0, "parcel {}".format(i + 1))
        f.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(x, y) for x, y in ring]]))
        pr.addFeature(f)
    layer.updateExtents()
    return layer


def build_layout(project, layer, png_path):
    layout = QgsPrintLayout(project)
    layout.initializeDefaults()  # one A4 landscape? default A4 portrait
    layout.setName("idml-test")
    project.layoutManager().addLayout(layout)

    # plain label
    label = QgsLayoutItemLabel(layout)
    label.setText("Plain label\nsecond line")
    f = QFont("Arial", 14)
    f.setBold(True)
    tf = label.textFormat()
    tf.setFont(f)
    tf.setSize(14)
    tf.setColor(QColor(20, 30, 40))
    label.setTextFormat(tf)
    label.attemptMove(QgsLayoutPoint(10, 10))
    label.attemptResize(QgsLayoutSize(80, 20))
    layout.addLayoutItem(label)

    # HTML label
    html = QgsLayoutItemLabel(layout)
    html.setMode(_enum(QgsLayoutItemLabel, "Mode", "ModeHtml"))
    html.setText(
        '<p style="text-align:center">Rich <b>bold</b> and '
        '<span style="color:#c81e1e; font-size:18pt">red big</span> text</p>'
    )
    html.attemptMove(QgsLayoutPoint(10, 35))
    html.attemptResize(QgsLayoutSize(120, 25))
    layout.addLayoutItem(html)

    # shapes
    rect = QgsLayoutItemShape(layout)
    rect.setShapeType(_enum(QgsLayoutItemShape, "Shape", "Rectangle"))
    rect.setSymbol(
        QgsFillSymbol.createSimple(
            {"color": "200,220,255", "outline_color": "0,60,120", "outline_width": "0.6"}
        )
    )
    rect.attemptMove(QgsLayoutPoint(150, 10))
    rect.attemptResize(QgsLayoutSize(50, 30))
    layout.addLayoutItem(rect)

    ellipse = QgsLayoutItemShape(layout)
    ellipse.setShapeType(_enum(QgsLayoutItemShape, "Shape", "Ellipse"))
    ellipse.setSymbol(
        QgsFillSymbol.createSimple({"color": "255,235,150", "outline_color": "120,80,0"})
    )
    ellipse.attemptMove(QgsLayoutPoint(210, 10))
    ellipse.attemptResize(QgsLayoutSize(40, 30))
    layout.addLayoutItem(ellipse)

    # rotated shape (rotation baked into ItemTransform)
    rot = QgsLayoutItemShape(layout)
    rot.setShapeType(_enum(QgsLayoutItemShape, "Shape", "Rectangle"))
    rot.attemptMove(QgsLayoutPoint(255, 10))
    rot.attemptResize(QgsLayoutSize(30, 15))
    rot.setItemRotation(30)
    layout.addLayoutItem(rot)

    # multi-paragraph HTML label (checks <Br/> paragraph-break placement)
    multi = QgsLayoutItemLabel(layout)
    multi.setMode(_enum(QgsLayoutItemLabel, "Mode", "ModeHtml"))
    multi.setText("<p>Erster Absatz mit Umlauten äöü</p><p>Zweiter &amp; letzter</p>")
    multi.attemptMove(QgsLayoutPoint(150, 90))
    multi.attemptResize(QgsLayoutSize(80, 30))
    layout.addLayoutItem(multi)

    # font features: buffer/halo + all-caps + letter spacing + line spacing
    fancy = QgsLayoutItemLabel(layout)
    fancy.setText("Halo Text")
    ftf = fancy.textFormat()
    ffont = QFont("Arial", 12)
    ffont.setCapitalization(QFont.Capitalization.AllUppercase)
    ffont.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 105)
    ftf.setFont(ffont)
    ftf.setSize(12)
    ftf.buffer().setEnabled(True)
    ftf.buffer().setSize(1.0)  # mm
    ftf.buffer().setColor(QColor(255, 255, 255))
    try:
        ftf.setLineHeight(1.5)
    except Exception:
        pass
    fancy.setTextFormat(ftf)
    fancy.attemptMove(QgsLayoutPoint(10, 180))
    fancy.attemptResize(QgsLayoutSize(60, 12))
    layout.addLayoutItem(fancy)

    # HTML list + superscript
    lst = QgsLayoutItemLabel(layout)
    lst.setMode(_enum(QgsLayoutItemLabel, "Mode", "ModeHtml"))
    lst.setText("<ul><li>Rutsche</li><li>Schaukel</li></ul><p>m<sup>2</sup></p>")
    lst.attemptMove(QgsLayoutPoint(80, 180))
    lst.attemptResize(QgsLayoutSize(50, 25))
    layout.addLayoutItem(lst)

    # picture
    pic = QgsLayoutItemPicture(layout)
    pic.setPicturePath(png_path)
    pic.attemptMove(QgsLayoutPoint(150, 50))
    pic.attemptResize(QgsLayoutSize(40, 30))
    layout.addLayoutItem(pic)

    # map
    m = QgsLayoutItemMap(layout)
    m.attemptMove(QgsLayoutPoint(10, 70))
    m.attemptResize(QgsLayoutSize(130, 100))
    m.setExtent(QgsRectangle(-10, -10, 210, 100))
    m.setLayers([layer])
    m.setBackgroundColor(QColor(255, 255, 255))
    layout.addLayoutItem(m)

    # legend (fallback path -> placed PDF)
    legend = QgsLayoutItemLegend(layout)
    legend.setLinkedMap(m)
    legend.attemptMove(QgsLayoutPoint(160, 90))
    legend.attemptResize(QgsLayoutSize(60, 40))
    layout.addLayoutItem(legend)

    return layout


def main():
    # '&' in the path: XML-attribute escaping regression test
    out_dir = os.path.join(tempfile.gettempdir(), "qgis2idml_test", "Q&A out")
    os.makedirs(out_dir, exist_ok=True)

    # small test PNG
    png_path = os.path.join(out_dir, "test_image.png")
    img = QImage(64, 48, _enum(QImage, "Format", "Format_ARGB32"))
    img.fill(QColor(80, 160, 90))
    p = QPainter(img)
    p.setPen(QColor(255, 255, 255))
    p.drawText(6, 28, "IMG")
    p.end()
    img.save(png_path)

    project = QgsProject.instance()
    layer = build_layer()
    project.addMapLayer(layer)
    layout = build_layout(project, layer, png_path)

    out = os.path.join(out_dir, "test_export.idml")
    result = export_layout_to_idml(layout, out, dpi=150, copy_fonts=True)
    print("EXPORT RESULT:", result)

    sys.path.insert(0, HERE)
    from validate_idml import validate

    errors, warnings = validate(out)
    for w in warnings:
        print("WARN:", w)
    for e in errors:
        print("ERROR:", e)

    # paragraph-break check: no standalone <Br/>-only ParagraphStyleRange,
    # and the multi-paragraph story carries exactly one <Br/>
    import re
    import zipfile

    z = zipfile.ZipFile(out)
    bad = ok_br = 0
    for n in z.namelist():
        if not n.startswith("Stories/"):
            continue
        s = z.read(n).decode("utf-8")
        for m in re.finditer(r"<ParagraphStyleRange[^>]*>(.*?)</ParagraphStyleRange>", s, re.S):
            body = m.group(1)
            if "<Br/>" in body and "<Content>" not in body:
                bad += 1
        if "Erster Absatz" in s:
            ok_br = s.count("<Br/>")
            assert "äöü" in s, "umlauts lost"
            assert "&amp;" in s, "ampersand not escaped in content"
    assert bad == 0, "%d standalone <Br/>-only paragraph ranges" % bad
    assert ok_br == 1, "expected exactly 1 <Br/> in 2-paragraph story, got %d" % ok_br

    # font-feature assertions across all stories
    all_stories = "".join(
        z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("Stories/")
    )
    assert 'Capitalization="AllCaps"' in all_stories, "all-caps lost"
    assert "StrokeWeight=" in all_stories, "text buffer/halo lost"
    assert 'Tracking="50"' in all_stories, "letter spacing lost"
    assert "<Leading" in all_stories, "line spacing lost"
    assert "•" in all_stories, "HTML list bullet lost"
    assert 'Position="Superscript"' in all_stories, "superscript lost"
    if result["warnings"]:
        print("EXPORT WARNINGS:", result["warnings"])
    print("VALIDATION:", "FAILED" if errors else "OK")
    return 1 if errors else 0


if __name__ == "__main__":
    code = main()
    qgs.exitQgis()
    sys.exit(code)
