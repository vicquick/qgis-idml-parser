# Changelog

## 0.5.0 — 2026-08-25

First public release. Developed and verified against a production
73-feature A3 atlas (Steckbriefe, CUX) on QGIS 3.44 (Qt5) and
QGIS 4.2 (Qt6), opened in InDesign.

- Direct QGIS layout/atlas → IDML export, bypassing the Qt PDF pipeline
  (no per-glyph text, no subset fonts — QGIS #48419/#49979 irrelevant)
- Native editable text (font + HTML labels), HTML `<table>` labels as
  per-column TextFrames, CSS line-height/margins/hanging indents mapped
  to leading/space-after/indents
- Native shapes with rounded-corner options, per-feature data-defined
  fill/stroke colors, symbol-opacity & color-alpha transparency
- Maps and non-native items as referenced vector-PDF links; images as
  links; fonts as references + `Document fonts/` copies with correct
  typographic names & PostScript names (own name-table parser,
  Adobe Fonts CoreSync store included)
- Group hierarchy + item names in the InDesign layers panel
- Overset-proof auto-sizing that never overrides a QGIS-wrapped width
- Atlas: one spread per feature per page in a single package
- Per-item error isolation with warnings; structural validator; smoke +
  atlas tests runnable without InDesign
