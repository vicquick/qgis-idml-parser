# Changelog

## 0.6.0 — 2026-08-26

Closes every remaining fidelity-audit finding (FIDELITY.md: 33 fixed,
3 documented-no-IDML-equivalent — with export warnings where
detectable):

- Multi-layer symbols: one stacked IDML shape per symbol layer
- Dashed/dotted/custom-dash strokes as native DashedStrokeStyle
  (StrokeType), stroke cap/join (EndCap/EndJoin)
- Item-level opacity + blend modes (Multiply/Screen/…) on every item
  type; unified TransparencySetting builder
- Semi-transparent frame background/stroke on maps, pictures, labels;
  polyline transparency
- Page background color as full-bleed bottom rectangle
- Pure black → registration-safe K-only [Black] swatch
- Items straddling a page boundary now appear on every spread they
  touch (positions relative to each spread's own page)
- Data-defined / live item rotation honored (QGraphicsItem.rotation())
- Boxed labels (background/frame) get grow-down overset protection
- HTML hyperlinks → real IDML Hyperlink/HyperlinkURLDestination
- Custom tab stops → paragraph TabList
- Text-format blend mode on labels; inline highlight spans warn


## 0.5.1 — 2026-08-25

Font/typography fidelity release — fixes all ten font-related findings
from the fidelity audit (see FIDELITY.md):

- Text buffer/halo → outlined type (StrokeColor/StrokeWeight = 2×buffer
  radius, opacity as StrokeTint)
- Text drop shadow → InDesign object drop-shadow on the frame
- Text background chip (Format > Background) → shape behind the frame,
  rect/rounded/ellipse (approximated with frame bounds)
- Data-defined text-format overrides (font/size/color per atlas feature)
  evaluated via QgsTextFormat.updateDataDefinedProperties
- Format-level line spacing (percentage → leading, absolute → leading)
- Capitalization: AllCaps/SmallCaps as IDML attributes (text stays
  editable), lowercase/title-case as text transforms
- Letter spacing → Tracking (1/1000 em), word spacing → word-spacing
  percentages
- Character-level color alpha → FillTint
- HTML <ul>/<ol> bullets/numbers synthesized with hanging indents
- <sub>/<sup> → Position="Subscript"/"Superscript"
- PyQt6 enum compatibility helper (enum_int) for all Qt enum reads


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
