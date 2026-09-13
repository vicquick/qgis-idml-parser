# Changelog

## 0.7.1 — 2026-09-13

Fixes:
- Link URIs are relative to the folder the `.idml` is written to
  (`file:<name> Links/x.jpg`) instead of absolute `file:C:/Users/...`
  paths — a package now opens with resolved links after being moved or
  handed on. Verified in InDesign 21.3 (233/233 links resolved from a
  copy in an unrelated folder). The `file:` scheme is required: a bare
  relative path makes InDesign drop every link silently. A path on
  another drive has no relative form and stays absolute.
- Geometry-generator symbol layers were painted as opaque black boxes
  (base-class `fillColor()` is an invalid black QColor, IDML has no
  generators). A generator placing dots along `boundary($geometry)` —
  the QGIS way to get a dot on every corner — maps to `$ID/Canned
  Dotted`; any other generator is skipped.
- Data-defined *Exclude item from exports* is evaluated per atlas
  feature (`excludeFromExports()` only returns the static checkbox), for
  top-level items and group children alike.
- `tests/validate_idml.py` resolves relative links against the package
  folder.

Docs: README section *InDesign quirks — verified verdicts*.


## 0.7.0 — 2026-09-09

First release verified by an actual InDesign round trip (QGIS PDF vs
InDesign-rendered IDML, per-tile pixel diff; see tools/). A production
A3 atlas, 45 features: 4.3/255 mean difference, remaining diff is
anti-aliasing / JPEG resampling.

Fixes:
- Line symbol layers inside fill symbols ("Outline: Simple line")
  were exported as opaque black fills with a 0 pt stroke — every
  QgsSymbolLayer inherits fillColor()/strokeColor() from the base
  class, on a line layer they return an invalid (black) QColor
- Symbol layer stacking order was inverted (`reversed(range(n))`):
  QGIS index 0 is the BOTTOM layer; the fill was painted over the
  outline, eating its inner half
- `PagesPerDocument` must be 1: InDesign builds a document with that
  many pages in its default two-per-spread layout before reading the
  package spreads, leaving ceil(N/2)-1 empty facing spreads in front
  (45 spreads → 89 pages, page 1 blank)
- Round-capped dot patterns (near-zero dash + gap) map to InDesign's
  built-in `$ID/Canned Dotted`; a DashedStrokeStyle with 0-length
  dashes renders invisible (item EndCap does not extend dash segments)
- HTML `<table>` labels that are really a bullet list (bullet cell +
  text cell per row) become a hanging-indent paragraph list instead of
  a two-column InDesign table with a 50/50 split
- Placed map/fallback PDFs: GraphicBounds now carry the real page box
  Qt rounded to (whole points) plus a compensating scale, instead of
  the nominal size
- HTML-mode labels reproduce QGIS's pixel quantization: runs are sized
  from integer pixels (1 px = 72/106.2 pt), CSS point sizes go through
  Qt's 96-dpi conversion first (`font-size:10pt` → 13 px = 8.81 pt),
  line pitch = ceil(fractional lineSpacing) px — measured in QGIS, dpi
  independent. Before, InDesign text was ~13 % larger with auto leading

New:
- `pages_per_spread` (API + plugin dialog, remembered per layout in
  custom property `export_idml/pages_per_spread`): one QGIS layout
  page becomes N equal-width InDesign pages on a facing spread — an
  A3-landscape layout page exports as a left+right A4 spread, exactly
  how InDesign represents a spread itself
- tools/indesign_render.py (COM: open a temp copy, export PDF, dump
  preflight + resolved page items), tools/indesign_probe.py,
  tools/compare_pdf.py (blend, heat map, per-tile scores, `--join`),
  tools/idml_patch.py (regex A/B patches on a package)


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
73-feature A3 atlas on QGIS 3.44 (Qt5) and
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
