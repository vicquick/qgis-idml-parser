"""QGIS plugin UI: Layout -> Export as IDML."""

import os
import traceback

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAction,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from qgis.core import QgsProject

from .exporter import export_layout_to_idml


class ExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export layout as IDML")
        v = QVBoxLayout(self)

        v.addWidget(QLabel("Print layout:"))
        self.layout_combo = QComboBox()
        self._layouts = QgsProject.instance().layoutManager().printLayouts()
        for lyt in self._layouts:
            self.layout_combo.addItem(lyt.name())
        v.addWidget(self.layout_combo)

        v.addWidget(QLabel("Output .idml file:"))
        h = QHBoxLayout()
        self.path_edit = QLineEdit()
        browse = QPushButton("...")
        browse.clicked.connect(self._browse)
        h.addWidget(self.path_edit)
        h.addWidget(browse)
        v.addLayout(h)

        self.atlas_check = QCheckBox("Export atlas (one spread per feature)")
        v.addWidget(self.atlas_check)

        self.fonts_check = QCheckBox('Copy used fonts to "Document fonts" folder')
        self.fonts_check.setChecked(True)
        v.addWidget(self.fonts_check)

        h2 = QHBoxLayout()
        h2.addWidget(QLabel("Map render DPI:"))
        self.dpi_spin = QSpinBox()
        self.dpi_spin.setRange(72, 1200)
        self.dpi_spin.setValue(300)
        h2.addWidget(self.dpi_spin)
        h2.addStretch()
        v.addLayout(h2)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save IDML", self.path_edit.text() or os.path.expanduser("~"),
            "InDesign Markup (*.idml)"
        )
        if path:
            self.path_edit.setText(path)

    def selected_layout(self):
        i = self.layout_combo.currentIndex()
        return self._layouts[i] if 0 <= i < len(self._layouts) else None


class ExportIdmlPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        self.action = QAction("Export layout as IDML...", self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu("Export IDML", self.action)

    def unload(self):
        if self.action:
            self.iface.removePluginMenu("Export IDML", self.action)
            self.action = None

    def run(self):
        dlg = ExportDialog(self.iface.mainWindow())
        if not dlg._layouts:
            QMessageBox.warning(
                self.iface.mainWindow(), "Export IDML",
                "No print layouts in this project."
            )
            return
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        layout = dlg.selected_layout()
        out = dlg.path_edit.text().strip()
        if not layout or not out:
            QMessageBox.warning(
                self.iface.mainWindow(), "Export IDML", "Pick a layout and output file."
            )
            return
        try:
            result = export_layout_to_idml(
                layout,
                out,
                dpi=dlg.dpi_spin.value(),
                copy_fonts=dlg.fonts_check.isChecked(),
                atlas=dlg.atlas_check.isChecked(),
            )
        except Exception:
            QMessageBox.critical(
                self.iface.mainWindow(), "Export IDML failed", traceback.format_exc()
            )
            return
        msg = "Exported {} spread(s), {} storie(s)\n{}".format(
            result["spreads"], result["stories"], result["idml"]
        )
        if result["fonts_copied"]:
            msg += "\n{} font file(s) copied to Document fonts".format(
                len(result["fonts_copied"])
            )
        QMessageBox.information(self.iface.mainWindow(), "Export IDML", msg)
