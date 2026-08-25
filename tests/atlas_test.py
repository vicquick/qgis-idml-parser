"""Atlas export test - one spread per feature. Run with python-qgis[-ltr].bat."""

import os
import sys
import tempfile

from qgis.core import (
    QgsApplication,
    QgsFeature,
    QgsGeometry,
    QgsLayoutItemLabel,
    QgsLayoutItemMap,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsPointXY,
    QgsPrintLayout,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

qgs = QgsApplication([], False)
qgs.initQgis()

from export_idml.exporter import export_layout_to_idml  # noqa: E402
from validate_idml import validate  # noqa: E402


def main():
    project = QgsProject.instance()
    layer = QgsVectorLayer(
        "Polygon?crs=EPSG:25832&field=name:string", "areas", "memory"
    )
    pr = layer.dataProvider()
    for i in range(3):
        f = QgsFeature(layer.fields())
        f.setAttribute(0, "Gebiet {}".format(i + 1))
        x0 = i * 200
        f.setGeometry(
            QgsGeometry.fromPolygonXY(
                [[QgsPointXY(x0, 0), QgsPointXY(x0 + 100, 0),
                  QgsPointXY(x0 + 100, 80), QgsPointXY(x0, 80)]]
            )
        )
        pr.addFeature(f)
    layer.updateExtents()
    project.addMapLayer(layer)

    layout = QgsPrintLayout(project)
    layout.initializeDefaults()
    layout.setName("atlas-test")
    project.layoutManager().addLayout(layout)

    label = QgsLayoutItemLabel(layout)
    label.setText("Steckbrief: [% \"name\" %]")
    label.attemptMove(QgsLayoutPoint(10, 10))
    label.attemptResize(QgsLayoutSize(120, 15))
    layout.addLayoutItem(label)

    m = QgsLayoutItemMap(layout)
    m.attemptMove(QgsLayoutPoint(10, 30))
    m.attemptResize(QgsLayoutSize(150, 100))
    m.setExtent(QgsRectangle(-10, -10, 110, 90))
    m.setLayers([layer])
    m.setAtlasDriven(True)
    layout.addLayoutItem(m)

    atlas = layout.atlas()
    atlas.setCoverageLayer(layer)
    atlas.setEnabled(True)

    out = os.path.join(tempfile.gettempdir(), "qgis2idml_test", "atlas_export.idml")
    result = export_layout_to_idml(layout, out, dpi=150, atlas=True)
    print("EXPORT RESULT:", result)
    assert result["spreads"] == 3, "expected 3 spreads, got %s" % result["spreads"]
    assert result["atlas_features"] == 3

    errors, warnings = validate(out)
    for e in errors:
        print("ERROR:", e)
    print("VALIDATION:", "FAILED" if errors else "OK")

    # label text must differ per spread (expression evaluated per feature)
    import zipfile

    z = zipfile.ZipFile(out)
    texts = []
    for n in z.namelist():
        if n.startswith("Stories/"):
            texts.append(z.read(n).decode())
    hits = [t for t in texts if "Gebiet" in t]
    print("stories with atlas feature name:", len(hits), "of", len(texts))
    assert len(hits) == 3, "atlas expressions not evaluated per feature"
    return 1 if errors else 0


if __name__ == "__main__":
    code = main()
    qgs.exitQgis()
    sys.exit(code)
