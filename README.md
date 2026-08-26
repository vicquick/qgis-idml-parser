# qgis-idml-parser

**Export QGIS print layouts and atlases as native Adobe InDesign IDML —
editable text, native shapes, linked images and maps, referenced fonts.**

QGIS's PDF export is famously un-editable in Adobe apps: text arrives as
per-letter fragments with subset fonts that Illustrator/InDesign report
as *missing* (QGIS [#48419](https://github.com/qgis/QGIS/issues/48419) /
[#49979](https://github.com/qgis/QGIS/issues/49979), open since 2022).
This plugin skips the PDF/Qt-paint pipeline entirely: it walks the
`QgsLayout` item tree and writes the IDML package directly — as if the
document had been authored in InDesign.

| QGIS item | IDML result |
|---|---|
| Label (font mode) | `TextFrame` + `Story` — real, editable text |
| Label (HTML mode) | styled runs via `QTextDocument` (per-fragment font / size / color / bold), CSS `line-height` → leading, hanging indents preserved |
| Label containing an HTML `<table>` | one native `TextFrame` **per column**, side by side at the exact column split |
| Shape (rect / ellipse / triangle) | native `Rectangle` / `Oval` / `Polygon` with `PathGeometry`; rounded corners as editable InDesign corner options; data-defined fill/stroke colors evaluated per atlas feature |
| Polygon / polyline item | native `Polygon` / `GraphicLine` |
| Picture | frame + `Image`/`PDF` + `Link` — file copied to `<name> Links/` |
| Map | rendered alone to a **vector PDF**, placed as a referenced `Link` |
| Legend, scale bar, tables, HTML frames, everything else | item region rendered to a transparent vector-PDF snippet, placed as a referenced `Link` |
| Groups | IDML `<Group>` hierarchy; item IDs become names in InDesign's layers panel |
| Atlas | one spread per feature (per page), all in **one** `.idml` |
| Transparency | symbol opacity → `TransparencySetting`, color alpha → fill/stroke transparency |
| Fonts | **references, never embedded** (IDML spec §6.1): correct typographic family/style + PostScript names read from the actual font files, plus copies in `Document fonts/` next to the output |

The output mirrors an InDesign *File → Package*:

```
report.idml
report Links/        ← maps & snippets as vector PDFs, images
Document fonts/      ← the used font files (InDesign auto-adopts these)
```

Open the `.idml` in InDesign (or Affinity Publisher 2+), *Save As* for a
`.indd`. All `Self` ids are prefixed `qx`, so packages can be composed
into bigger documents with [SimpleIDML](https://github.com/Starou/SimpleIDML)
(`prefix()` / `insert_idml()`).

## Install

1. Copy `export_idml/` into your QGIS profile's plugin folder:
   - Windows QGIS 3: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\export_idml`
   - Windows QGIS 4: `%APPDATA%\QGIS\QGIS4\profiles\default\python\plugins\export_idml`
   - (or run `deploy.ps1`, which copies to both)
2. Restart QGIS.
3. *Plugins → Manage and Install Plugins → Installed →* enable **Export IDML**.

Works on QGIS 3.34+ (Qt5) and QGIS 4.x (Qt6) — all imports go through
`qgis.PyQt`. No external Python dependencies (the font name-table parser
is self-contained).

## Use

*Plugins → Export IDML → Export layout as IDML…*

- pick the print layout,
- pick the output `.idml`,
- optionally check **Export atlas** (one spread per coverage feature),
- keep **Copy used fonts** checked,
- DPI only affects the map/snippet PDF rendering (300 default).

Keep the `… Links/` and `Document fonts/` folders next to the `.idml` —
placed assets and fonts are *references* (that's the point: everything
stays editable and swappable).

### Scripted / headless

```python
from export_idml.exporter import export_layout_to_idml

layout = QgsProject.instance().layoutManager().layoutByName("Steckbriefe")
result = export_layout_to_idml(layout, r"C:\out\report.idml",
                               dpi=300, copy_fonts=True, atlas=True)
print(result["spreads"], result["warnings"])
```

One bad item never aborts an export — it is skipped and reported in
`result["warnings"]`.

## Design notes (why it looks like the QGIS layout)

- **Positions** are taken from `QGraphicsItem.pos()`, not `pagePos()` —
  `pagePos()` returns the *reference point* (which may be the centre or
  a corner) and silently shifts centred items.
- **Fonts**: Qt often reports legacy GDI family names
  (`"Futura PT Book"` + `"Book"` → InDesign would look for a
  non-existent "Futura PT Book Book"). The plugin parses the `name`
  tables of the installed font files (TTF/OTF/TTC, plus Adobe Fonts'
  CoreSync store) and canonicalizes to the typographic family/style
  InDesign expects, with the real PostScript name. Missing weights are
  aliased to the nearest installed one (Thin→Light, …).
- **Frame sizes are law**: if QGIS wrapped a text, the frame width
  arrives unchanged (`AutoSizingType="HeightOnly"`). Only labels whose
  measured natural width fits their frame may auto-size
  (`HeightAndWidth` + *no line breaks*, anchored at the alignment
  point) — this absorbs the 1–3 % width difference between Qt's and
  InDesign's composers that otherwise produces overset frames.
- **Z-order**: entities sort by z; a group sorts at its topmost child's
  z (QGIS renders group children interleaved by their own z — IDML
  groups are atomic).
- **Link URIs** are percent-encoded (`Q&A`, umlauts, spaces in paths are
  all legal on Windows and all break naive IDML).
- Paragraph breaks are `<Br/>` *inside* the paragraph's last
  `CharacterStyleRange`; soft line breaks are `U+2028`; soft hyphens
  (`U+00AD`, e.g. from pyphen-based QGIS expressions) pass through and
  InDesign treats them as discretionary hyphens.

## Known limitations / fidelity gaps

See [FIDELITY.md](FIDELITY.md): all 36 audited findings are fixed as of
0.6.0, except three with no practical IDML equivalent (character
highlight spans, font-file first-baseline metrics, dpi-dependent
hairlines in placed PDFs) — those are documented and warned about
where detectable. Remaining by-design simplifications:

- Labels **inside map content** are outlined/fragmented inside the
  placed map PDF (roadmap: native map labels via `QgsLabelingResults`).
- Legends/scale bars are placed PDFs, not native text (roadmap: rebuild
  from the legend model).
- Colors are RGB process colors (pure black maps to the K-only [Black]
  swatch); CMYK conversion is left to the print workflow.
- Gradient and pattern fills use their base color (symbol layers are
  otherwise exported layer-by-layer, dashes included).

## Tests

```powershell
& "C:\Program Files\QGIS 3.44.13\bin\python-qgis-ltr.bat" tests\standalone_test.py
& "C:\Program Files\QGIS 3.44.13\bin\python-qgis-ltr.bat" tests\atlas_test.py
& "C:\Program Files\QGIS 4.2.1\bin\python-qgis.bat"       tests\standalone_test.py
python tests\validate_idml.py out.idml   # structural validator, plain python
```

The smoke test covers every mapped item type plus regression cases:
`&` in the output path, rotation, umlauts, paragraph-break placement.
`validate_idml.py` checks zip layout (mimetype first, stored), XML
well-formedness, designmap references, story/frame wiring and link
resolution — no InDesign needed.

## Repo layout

```
export_idml/
  __init__.py       plugin entry
  plugin.py         dialog + menu action
  exporter.py       orchestrator (pages, atlas, Links/, Document fonts/, z-order)
  mapping.py        QGIS item → IDML page item dispatch
  idml_package.py   IDML package model + zip writer (designmap, resources, stories)
  text_runs.py      QTextDocument → styled runs & tables (HTML labels)
  fonts.py          pure-python TTF/OTF/TTC name-table parser + font index
  geom.py           mm→pt, ItemTransform matrices, PathGeometry builders
tests/
  standalone_test.py  full item-type smoke test (runs under python-qgis)
  atlas_test.py       3-feature atlas test
  validate_idml.py    structural validator (plain python)
deploy.ps1            copy into QGIS3 + QGIS4 profiles
```

## License

MIT
