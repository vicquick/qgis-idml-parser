# Fidelity audit — QGIS vs InDesign rendering differences

Result of a 42-agent adversarial audit (every finding verified against
the source with a concrete visual-difference scenario). As of 0.6.0 every finding is either **FIXED** or explicitly
*documented* (no practical IDML equivalent exists: character
highlights, font-file baseline metrics, dpi-dependent hairlines —
the exporter warns where it can detect them). Fixed across 0.5.0 /
0.5.1 / 0.6.0; the rest are known,
documented gaps — ordered by severity. PRs welcome.

Out of scope by design: the *interior* of placed map / legend /
scalebar / table PDFs is Qt-rendered and matches QGIS by construction.

*documented = no practical fix exists in the IDML format; see detail.*

| # | Status | Severity | Area | Finding |
|---|--------|----------|------|---------|
| 1 | **FIXED 0.5.0** | high | `mapping.py` | Rotated items are placed at the wrong position (double-counted pivot) |
| 2 | **FIXED 0.5.0** | high | `exporter.py` | Page 0's size is applied to every spread, breaking mixed-page-size layouts |
| 3 | **FIXED 0.5.0** | high | `mapping.py` | Picture resize mode is ignored - QGIS default (Zoom, aspect-preserving) is always exported as Stretch |
| 4 | **FIXED 0.5.0** | high | `exporter.py` | copy_link's dedup check can never match a file it just copied — Links folder grows unbounded |
| 5 | **FIXED 0.5.0** | high | `mapping.py` | export_group() silently ignores excludeFromExports on grouped children |
| 6 | **FIXED 0.5.0** | medium | `idml_package.py` | Empty HTML labels emit a malformed Story (zero ParagraphStyleRange elements) |
| 7 | **FIXED 0.5.0** | low | `mapping.py` | Rounded-rectangle corner radius is not clamped to half the shorter side |
| 8 | **FIXED 0.5.1** | high | `mapping.py` | QgsTextFormat.buffer() (halo/outline) is never read — completely dropped |
| 9 | **FIXED 0.5.1** | high | `text_runs.py` | Format-level line spacing (QgsTextFormat.lineHeight()) is ignored for plain 'Font' mode labels |
| 10 | **FIXED 0.5.1** | high | `text_runs.py` | Text capitalization / case transform (all-caps, small caps) never read — produces a literal text mismatch, not just a style nuance |
| 11 | **FIXED 0.5.1** | high | `text_runs.py` | HTML list markup (<ul>/<ol>/<li>) loses its bullet/number entirely, not just its indent style |
| 12 | **FIXED 0.6.0** | high | `idml_package.py` | Every custom color is written as RGB Process — the predefined CMYK Black swatch is dead code |
| 13 | **FIXED 0.6.0** | high | `mapping.py` | QgsLayoutItem.opacity() (the generic item Rendering-tab opacity slider) is never read for any item type |
| 14 | **FIXED 0.6.0** | high | `mapping.py` | Only symbolLayer(0) is ever read — multi-layer fill/line symbols lose every layer beyond the first |
| 15 | **FIXED 0.6.0** | high | `mapping.py` | Dashed/dotted/other pen styles are dropped — every stroke exports solid |
| 16 | **FIXED 0.6.0** | high | `mapping.py` | Page background color is ignored entirely — the InDesign page always renders with no fill |
| 17 | **FIXED 0.6.0** | high | `mapping.py` | Backgrounded/framed labels get no auto-size overset protection, despite the code's own documented risk |
| 18 | **FIXED 0.5.1** | high | `mapping.py` | Data-defined label text formatting (font family/size/color) is never evaluated |
| 19 | **FIXED 0.5.1** | medium | `mapping.py` | QgsTextFormat.background() (shaped text background/chip) never read — conflated with item.hasBackground() |
| 20 | **FIXED 0.5.1** | medium | `mapping.py` | QgsTextFormat.shadow() (text drop shadow) never read |
| 21 | **FIXED 0.5.1** | medium | `text_runs.py` | Letter spacing / word spacing (QFont.letterSpacing()/wordSpacing()) never forwarded to IDML Tracking |
| 22 | **FIXED 0.5.1** | medium | `idml_package.py` | Character-level color alpha (semi-transparent text) is silently made fully opaque |
| 23 | **FIXED 0.5.1** | medium | `text_runs.py` | Sub/superscript spans (<sub>/<sup>) render as normal baseline text |
| 24 | **FIXED 0.6.0** | medium | `text_runs.py` | Custom tab stops are never emitted; a literal tab character falls back to InDesign's default tab grid |
| 25 | **FIXED 0.6.0** | medium | `mapping.py` | Data-defined (per-atlas-feature) item rotation is ignored |
| 26 | **FIXED 0.6.0** | medium | `exporter.py` | An item spanning a page boundary is placed on only one spread and is missing from the other |
| 27 | **FIXED 0.6.0** | medium | `mapping.py` | QgsLayoutItem.blendMode() is never read — everything exports as Normal blend |
| 28 | **FIXED 0.6.0** | medium | `mapping.py` | Stroke cap and join style are never read — always InDesign's default (butt cap / miter join) |
| 29 | **FIXED 0.6.0** | medium | `mapping.py` | Map/Picture frame background and stroke color always export fully opaque, even when semi-transparent in QGIS |
| 30 | **FIXED 0.6.0** | medium | `exporter.py` | Items straddling two layout pages are placed whole on one spread, missing on the adjacent one |
| 31 | **FIXED 0.6.0** | low | `mapping.py` | QgsTextFormat.blendMode() (and any text blend mode) is never applied |
| 32 | documented* | low | `text_runs.py` | Inline background-color spans (CSS highlight) are dropped |
| 33 | **FIXED 0.6.0** | low | `text_runs.py` | Hyperlinks (<a href>) become inert, unlinked plain text |
| 34 | documented* | low | `mapping.py` | FirstBaselineOffset is hardcoded to 'AscentOffset' for every text frame, independent of font or vAlign |
| 35 | **FIXED 0.6.0** | low | `mapping.py` | export_polyline never emits any transparency — a semi-transparent line stroke always renders fully opaque |
| 36 | documented* | low | `mapping.py` | Export dpi silently controls hairline stroke width in map/fallback PDFs |

## Details

### 1. Rotated items are placed at the wrong position (double-counted pivot) — **FIXED in 0.5.0**

*high · `C:/Users/victor.budinic/Desktop/BIM/scripts/qgis2idml/export_idml/mapping.py`*

item_geometry() (lines ~148-181) sets tx,ty from item.pos() and, when rot=item.itemRotation()!=0, calls geom.rotation_at(rot, tx, ty, cx=w_pt/2, cy=h_pt/2) (line ~178). Empirically verified live in QGIS: for a rotated QgsLayoutItem, pos() (Qt local-origin scene position) is ALREADY the position QGIS computed to make the rotation visually pivot about the item's centre (setItemRotation(deg, adjustPosition=True) is the default used by the GUI rotation field/handle). The correct scene mapping is simply scene(p) = R(p) + pos() (verified exactly against QGraphicsItem.mapToScene for several test points). By additionally applying rotation_at's own cx/cy center-pivot offset on top of the already-adjusted pos(), the code adds a second, spurious translation equal to R(-c)+c (c=(w/2,h/2)), which for a 30deg/40x20mm test rectangle was an ~7.7x/8.7pt error, and scales with rotation angle and item …

**Suggested fix:** Call rotation_at(rot, tx, ty) with cx=cy=0.0 (the default) since tx,ty from item.pos() already encode the QGIS center-pivot adjustment; do not pass w_pt/2,h_pt/2 as an additional pivot.

### 2. Page 0's size is applied to every spread, breaking mixed-page-size layouts — **FIXED in 0.5.0**

*high · `C:/Users/victor.budinic/Desktop/BIM/scripts/qgis2idml/export_idml/exporter.py`*

export_layout_to_idml() (lines ~140-144) reads page = layout.pageCollection().page(0) once and derives page_w_pt/page_h_pt only from that page, then constructs IdmlPackage(page_w_pt, page_h_pt, ...). IdmlPackage.new_spread() (idml_package.py, ~lines 436-445) passes self.page_w/self.page_h (page 0's size) to every Spread it creates, and Spread.xml()'s <Page GeometricBounds='0 0 h w'> (idml_package.py ~lines 307-333) plus the single global Preferences.xml DocumentPreference (idml_package.py preferences_xml(), written with pkg.page_w/pkg.page_h at write() ~line 505-507) likewise use only that one size for the whole document. Confirmed live that QGIS print layouts fully support per-page differing sizes (e.g. an A4 cover page followed by an A3 fold-out page, each with its own independent pageSize()) - a real, supported feature, not an edge case. Any page after page 0 whose size differs will …

**Suggested fix:** Track each page's own size individually: pass the specific page's w/h into Spread's constructor (already stored per-Spread as self.w/self.h) instead of always using the IdmlPackage-level self.page_w/self.page_h, and either drop per-page-size support from the global DocumentPreference (documenting the limitation) or emit distinct master spreads/page sizes as IDML allows for non-uniform documents.

### 3. Picture resize mode is ignored - QGIS default (Zoom, aspect-preserving) is always exported as Stretch — **FIXED in 0.5.0**

*high · `C:/Users/victor.budinic/Desktop/BIM/scripts/qgis2idml/export_idml/mapping.py`*

export_picture() (lines ~743-817) always computes sx = w_pt/nat_w and sy = h_pt/nat_h independently and applies them as a non-uniform <Image ItemTransform='{sx} 0 0 {sy} 0 0'> (lines ~774-776, 788), i.e. it unconditionally reproduces QgsLayoutItemPicture's 'Stretch' resize mode, filling the frame exactly regardless of aspect ratio. item.resizeMode() is never read anywhere in this file. Confirmed live that QgsLayoutItemPicture.resizeMode() defaults to Zoom (value 0) for newly created picture items - the common/default case - which QGIS renders aspect-preserving (fit-within-frame, letterboxed/pillarboxed), not stretched. Clip mode (native size, cropped) is also unhandled. Any picture item whose natural image aspect ratio doesn't exactly match its frame's aspect ratio, and which uses the default Zoom (or Clip) mode rather than explicitly-set Stretch, will render visibly …

**Suggested fix:** Read item.resizeMode() and branch: for Zoom, compute a single uniform scale = min(w_pt/nat_w, h_pt/nat_h) and center the image within the frame (letterbox/pillarbox, matching QGIS); for Clip, place at native size (converted through the correct 72/96 factor for SVG) without scaling, clipped to the frame; keep the current stretch-to-fill logic only for the Stretch mode. Fix _natural_size_pt's SVG conversion (multiply by 72.0/96.0) at the same time so any aspect-preserving fit is computed from corr

### 4. copy_link's dedup check can never match a file it just copied — Links folder grows unbounded — **FIXED in 0.5.0**

*high · `export_idml/exporter.py`*

In ExportContext.copy_link() (lines 40-57), the only way the loop avoids appending a numeric suffix is `os.path.samefile(src, dst)` returning True (line 50). `dst` is a file this same function created moments earlier via `shutil.copy2()` from `src` — a byte-for-byte copy is a different inode from the original, so samefile() is always False there. The single case where reuse actually works is the earlier short-circuit `abspath(src) == abspath(dst)` (line 45), i.e. the source is already sitting in links_dir under the exact same name — a degenerate case, not real dedup. Consequence #1 (within one atlas run): a QgsLayoutItemPicture with a STATIC/repeated path (a fixed logo/header/footer placed on every atlas page) gets physically re-copied on every single atlas feature — a 300-feature atlas produces photo.jpg, photo_2.jpg ... photo_300.jpg, all byte-identical, ballooning the Links folder to …

**Suggested fix:** Cache dedup by source path within ExportContext (e.g. self._copied = {} mapping os.path.abspath(src) -> dst, returned directly on a repeat call within the same run). For cross-run reuse, compare a candidate existing file's content (size + hash, or filecmp.cmp) against src rather than relying on inode identity, so a second export run recognizes and reuses files a prior run already placed under that name instead of always incrementing.

### 5. export_group() silently ignores excludeFromExports on grouped children — **FIXED in 0.5.0**

*high · `export_idml/mapping.py`*

_page_items() in exporter.py (lines 78-82) explicitly skips top-level items whose excludeFromExports() is true. export_group()'s children filter (mapping.py lines 966-972) checks only isinstance/isVisible() — excludeFromExports() is never consulted, at any nesting depth (nested sub-groups reach this same filter). Any child item inside a group that is marked 'Exclude from exports' in QGIS but remains isVisible()==True on canvas (the normal way to keep a draft/QA/annotation element around for editing while hiding it from output) is therefore exported into the IDML anyway — the opposite of what happens for the identical item ungrouped. Grouping alone defeats the exclude-from-exports flag.

**Suggested fix:** Apply the same guarded it.excludeFromExports() check used in _page_items() to the children filter in export_group(). export_fallback()'s `others` computation (lines 892-899) has the identical gap and should get the same fix.

### 6. Empty HTML labels emit a malformed Story (zero ParagraphStyleRange elements) — **FIXED in 0.5.0**

*medium · `export_idml/idml_package.py`*

When an HTML-mode label's resolved structure is empty for a given atlas feature (a blank field — extract_structure() trims trailing empty paragraphs, and mapping.py lines 353-359 can leave `paragraphs = []`), export_label() calls pkg.add_story([]). story_xml() (idml_package.py line 360: `n_paras = len(paragraphs)`, then `for pi, para in enumerate(paragraphs):`) then produces a <Story> element with header/preference children but literally zero <ParagraphStyleRange> children. This deviates from IDML's normal content model (InDesign itself always writes at least one, even empty) and is the kind of degenerate part InDesign's 'problems were found... file has been recovered' repair path tends to catch on open. Because it only occurs for whichever atlas features happen to have that particular field blank, it won't show up when spot-checking a handful of feature spreads — only on a full open of …

**Suggested fix:** When `paragraphs` ends up empty (either in export_label() before calling add_story(), or as a guard inside story_xml()), substitute a single empty paragraph entry — the same fallback plain_text_paragraphs() already applies at lines 230-241 — so every Story always has at least one ParagraphStyleRange.

### 7. Rounded-rectangle corner radius is not clamped to half the shorter side — **FIXED in 0.5.0**

*low · `C:/Users/victor.budinic/Desktop/BIM/scripts/qgis2idml/export_idml/mapping.py`*

export_shape() (lines ~602-614) reads item.cornerRadius() and emits TopLeftCornerRadius/TopRightCornerRadius/BottomLeftCornerRadius/BottomRightCornerRadius verbatim (r_pt) with no upper-bound clamp. Qt's own path-building for rounded rects (QPainterPath.addRoundedRect, which QGIS's Qt-based rendering relies on) documents that it auto-clamps the radius to half the rect's width/height when the requested radius would exceed that, producing a 'stadium'/pill shape rather than a malformed/self-overlapping path. It is unverified whether InDesign's corner-radius geometry engine performs the same clamp when parsing a raw (not UI-entered) CornerRadius attribute from IDML XML that exceeds half the object's side - if it does not, a QGIS shape intentionally using an oversized radius to get a pill/stadium look (common for badges/buttons) would import with a different (potentially self-intersecting or …

**Suggested fix:** Defensively clamp r_pt to min(r_pt, w_pt/2.0, h_pt/2.0) before writing the CornerRadius attributes, so the exported geometry matches QGIS's own clamped rendering regardless of InDesign's internal handling of oversized values.

### 8. QgsTextFormat.buffer() (halo/outline) is never read — completely dropped — **FIXED in 0.5.1**

*high · `export_idml/mapping.py`*

export_label() (line ~326) reads item.textFormat() but only pulls font(), size(), sizeUnit(), color() (lines 326-332). tf.buffer() (QgsTextBufferSettings: enabled/size/color/opacity — the 'Format > Buffer' halo QGIS labels commonly use for legibility over busy backgrounds) is never accessed anywhere in mapping.py, text_runs.py, or idml_package.py. story_xml (idml_package.py) only ever writes a single FillColor per CharacterStyleRange, with no stroke/outline duplicate.

**Suggested fix:** When tf.buffer().enabled(), either emit a duplicated CharacterStyleRange/text-frame copy underneath using StrokeColor+StrokeWeight (IDML supports outlined type per character range) sized from buffer().size(), or at minimum push an export warning so the loss is visible to the user rather than silent.

### 9. Format-level line spacing (QgsTextFormat.lineHeight()) is ignored for plain 'Font' mode labels — **FIXED in 0.5.1**

*high · `export_idml/text_runs.py`*

plain_text_paragraphs() (lines 204-242) hardcodes "line_height_pct": None unconditionally for every paragraph. HTML-mode labels can pick up line spacing from Qt's per-block CSS line-height, but the majority of simple map labels use 'Font' mode, and export_label() (mapping.py ~369) calls plain_text_paragraphs() without ever reading tf.lineHeight()/tf.lineHeightUnit() (the 'Line spacing' field under Format that applies to plain-text labels too).

**Suggested fix:** In export_label(), for the non-HTML branch, read tf.lineHeight()/tf.lineHeightUnit(), convert to a percentage, and set it on each paragraph the same way HTML mode already threads line_height_pct through to story_xml's <Leading> element.

### 10. Text capitalization / case transform (all-caps, small caps) never read — produces a literal text mismatch, not just a style nuance — **FIXED in 0.5.1**

*high · `export_idml/text_runs.py`*

Neither _run_from_format nor plain_text_paragraphs reads font.capitalization() (Qt: AllUppercase/AllLowercase/SmallCaps/Capitalize — QGIS's Format tab 'change case' setting). Only the literal underlying text is exported.

**Suggested fix:** Read font.capitalization() per run and either transform the literal <Content> text to match, or (preferred, keeps text editable) set IDML's Capitalization="AllCaps"/"SmallCaps" attribute on the CharacterStyleRange.

### 11. HTML list markup (<ul>/<ol>/<li>) loses its bullet/number entirely, not just its indent style — **FIXED in 0.5.1**

*high · `export_idml/text_runs.py`*

_para_from_block (lines 68-96) only walks block.begin() fragment text; _walk_frame (lines 154-176) never inspects block.textList(). In Qt's QTextDocument, list markers (bullet glyph or number) live on the block's QTextList/QTextListFormat and are synthesized at paint time — they never appear in fragment.text(). So a <ul><li>Rutsche</li><li>Schaukel</li></ul> label exports as two plain, unmarked, non-indented lines with no bullet character at all.

**Suggested fix:** In _para_from_block, check block.textList(); when present, resolve the marker (list.itemText(block) for ordered lists, or the configured bullet glyph for unordered) and prepend it as a literal leading run, and derive left/first-line indent from the list format for a proper hanging-indent look.

### 12. Every custom color is written as RGB Process — the predefined CMYK Black swatch is dead code — **FIXED in 0.6.0**

*high · `export_idml/idml_package.py`*

ColorRegistry.ref() (lines 63-70) keys purely on (r,g,b) and always emits '<Color ... Model="Process" Space="RGB" ...>' (graphic_xml, lines 87-99). The one CMYK swatch that IS defined, 'Color/Black' (lines 74-80, C=0 M=0 Y=0 K=100), is never referenced anywhere — ref() has no special case that routes pure black (0,0,0) to it. So ordinary black label text (the QGIS default text color) exports as an RGB(0,0,0) swatch, not registration-safe K-only black. When the InDesign user later converts/exports to CMYK for print (CMYKPolicy="CMYK_IgnoreProfiles" in designmap.xml means InDesign's own default RGB→CMYK conversion runs), RGB(0,0,0) typically becomes a 4-ink 'rich black' (e.g. C75 M68 Y67 K90 under SWOP) instead of pure K100. Rich-black fine text causes visible misregistration/blur on offset or low-end digital presses — a real production failure, not a subtle one, for a script whose stated …

**Suggested fix:** Special-case (0,0,0)/near-black in ColorRegistry.ref() to return 'Color/Black' (K-only) instead of minting an RGB swatch, and consider adding a document- or item-level option to author swatches as CMYK (Space="CMYK", converted via a fixed profile) when the export is known to target print, rather than always RGB.

### 13. QgsLayoutItem.opacity() (the generic item Rendering-tab opacity slider) is never read for any item type — **FIXED in 0.6.0**

*high · `export_idml/mapping.py`*

A grep across mapping.py for opacity/blendMode shows the only opacity ever captured is symbol.opacity() inside _fill_stroke_from_symbol (line 556), used solely by export_shape/export_polygon. The separate, generic QgsLayoutItem.opacity() property (Item Properties > Rendering > Opacity, available on labels, pictures, maps, and shapes independently of any symbol-level opacity) is not called anywhere in the file. Consequently a QGIS label, picture, or map item that is faded via the item-level opacity slider (a very common technique for watermarks, dimmed backgrounds, overlay callouts) exports at 100% opacity in the IDML — a plainly visible difference between the QGIS layout and the InDesign result.

**Suggested fix:** Read item.opacity() for every item type and fold it into the object-level <TransparencySetting><BlendingSetting Opacity=.../></TransparencySetting> (multiplying with symbol.opacity() for shapes, since QGIS applies both).

### 14. Only symbolLayer(0) is ever read — multi-layer fill/line symbols lose every layer beyond the first — **FIXED in 0.6.0**

*high · `export_idml/mapping.py`*

_fill_stroke_from_symbol (line 557: 'sl = symbol.symbolLayer(0)') and export_polyline (line 691: 'sl = sym.symbolLayer(0)') both hard-index layer 0 and never iterate symbol.symbolLayerCount(). Any QGIS symbol built from multiple stacked layers — e.g. a fill + hatch/point-pattern overlay, a line with a separate casing layer, or a line with a marker-line layer for dashes/arrows — silently loses every layer past the first. The shape/line renders with only the bottom layer's flat color and width; overlay patterns, casings, and decorations disappear with no warning.

**Suggested fix:** Loop over symbol.symbolLayerCount() and either merge additional simple layers into extra IDML page items (stacked shapes) or at minimum emit a warning listing dropped layers, similar to the existing per-item try/except warning mechanism in exporter.py.

### 15. Dashed/dotted/other pen styles are dropped — every stroke exports solid — **FIXED in 0.6.0**

*high · `export_idml/mapping.py`*

The only check against a QGIS pen style is 'style_ok = sl.strokeStyle() != Qt.PenStyle.NoPen' (line 566), a boolean gate ('is there a stroke at all') — the actual pattern (DashLine, DotLine, DashDotLine, custom dash array) is never read or translated. Consistently, idml_package.py's ColorRegistry.graphic_xml() defines exactly one <StrokeStyle Self="StrokeStyle/$ID/Solid"...> (lines 104-106) and no other stroke style is ever created; no shape/polygon/polyline/frame element in mapping.py ever emits a StrokeType attribute. Any QGIS symbol using a dashed or dotted line style (extremely common in landscape/GIS plans for property lines, hidden edges, phase boundaries) exports as a solid line in InDesign — a clear, common visual break.

**Suggested fix:** Read the symbol layer's dash pattern (customDashVector()/useCustomDashPattern(), or the PenStyle enum) and either emit a matching <StrokeStyle> (dash pattern) referenced via StrokeType, or at minimum map the common PenStyle values (Dash/Dot/DashDot) to IDML's built-in stroke styles ('$ID/Dashed (4 and 4)', etc.).

### 16. Page background color is ignored entirely — the InDesign page always renders with no fill — **FIXED in 0.6.0**

*high · `export_idml/mapping.py`*

QgsLayoutItemPage's own page style (its background fill symbol, configurable per page in QGIS via Layout > Page Setup, defaulting to white but frequently changed for dark/colored posters) is never read anywhere in this file. export_item explicitly no-ops on QgsLayoutItemPage (lines 1003-1004: '# pages are handled as spreads'), and in idml_package.py both Spread.xml() (line 318) and master_spread_xml() (line 251) hardcode '<PageColor type="enumeration">UseMasterColor</PageColor>' — which is only the Pages-panel icon tint in InDesign, not an actual page fill. A QGIS layout whose page background is set to black, a brand color, or any non-default fill will export to InDesign with no page fill at all (transparent — showing white/pasteboard), a very visible whole-page difference for any dark-background poster or A1 plan layout.

**Suggested fix:** Read page.pageStyleSymbol() (or equivalent) and emit a full-bleed Rectangle at the bottom of z-order on the Spread with that fill color (and its own transparency, since page backgrounds can also carry alpha).

### 17. Backgrounded/framed labels get no auto-size overset protection, despite the code's own documented risk — **FIXED in 0.6.0**

*high · `export_idml/mapping.py`*

The comment at lines 391-396 states the exporter's rationale plainly: 'QGIS frames are often sized exactly to the rendered text; InDesign's composer can run a hair wider -> overset.' The mitigation (AutoSizingType=HeightOnly/HeightAndWidth) only applies `if not item.hasBackground() and not item.frameEnabled()` (line 398); any label WITH a background fill or a border falls to `as_type = None` -> 'Off' (lines 433-434), emitting a fixed-size TextFrame at exactly the w_pt/h_pt QGIS reported for that one atlas feature. This is precisely the overset risk the comment warns about, left unmitigated for exactly the labels most likely to carry variable-length per-feature text (badge/rating/callout boxes with a colored background — a common atlas-report pattern). When InDesign's composer wraps a line differently than Qt did for the nominally same font (kerning/hyphenation/hinting differences), the …

**Suggested fix:** Extend the HeightOnly auto-sizing path to backgrounded/framed labels too (InDesign auto-sizing still keeps the frame's own fill/stroke — it only grows the box), or at minimum estimate wrapped line count via the existing _natural_width_pt()-style measurement and push a warning when it's close to overset so affected spreads are flagged for manual review.

### 18. Data-defined label text formatting (font family/size/color) is never evaluated — **FIXED in 0.5.1**

*high · `export_idml/mapping.py`*

export_label() reads `tf = item.textFormat()` then `font = tf.font()` / `color = tf.color()` (lines 326-332) as static values. Unlike shape fill/stroke, where the code explicitly evaluates data-defined symbol-layer properties against the item's expression context via _dd_color() (lines 512-533, used from _fill_stroke_from_symbol — its own docstring cites 'rating squares whose fill is a CASE expression over atlas-feature attributes' as the motivating case), nothing in export_label() evaluates data-defined overrides on the text format. QGIS's Text Format panel exposes the same expression/data-defined-override buttons on Font/Size/Color as symbol layers do. If any atlas label relies on a data-defined Size (e.g. a shrink-to-fit pattern that reduces font size per feature so variable-length content still fits a fixed box) rather than an external pre-render script, tf.size() here returns the …

**Suggested fix:** Before reading font/size/color, evaluate the text format's data-defined properties against item.createExpressionContext() (the same context already built for _fill_stroke_from_symbol/_dd_color), mirroring the pattern already used for shapes.

### 19. QgsTextFormat.background() (shaped text background/chip) never read — conflated with item.hasBackground() — **FIXED in 0.5.1**

*medium · `export_idml/mapping.py`*

Lines 385-389 read item.hasBackground()/backgroundColor() — the label item's own rectangular frame fill — but that is a distinct setting from tf.background() (Format > Background tab: a shaped fill sized to the text, e.g. rounded-rect badge). tf.background() is never referenced anywhere in the codebase.

**Suggested fix:** Read tf.background(); when enabled(), emit a shape (Rectangle/Oval per background().type()) behind the text frame sized/colored from its settings, or log a warning that the badge/chip background was dropped.

### 20. QgsTextFormat.shadow() (text drop shadow) never read — **FIXED in 0.5.1**

*medium · `export_idml/mapping.py`*

Same pattern as buffer/background: tf.shadow() (QgsTextShadowSettings: enabled, color, opacity, offset, blur radius) is never accessed in export_label(), and no drop-shadow object effect is ever emitted for any TextFrame in idml_package.py.

**Suggested fix:** When tf.shadow().enabled(), emit an IDML object-level drop-shadow effect on the TextFrame (or at least warn that the shadow was dropped).

### 21. Letter spacing / word spacing (QFont.letterSpacing()/wordSpacing()) never forwarded to IDML Tracking — **FIXED in 0.5.1**

*medium · `export_idml/text_runs.py`*

_run_from_format (lines 41-65, HTML mode) and plain_text_paragraphs (lines 204-242, Font mode) both build runs from a QFont but never call font.letterSpacing()/font.wordSpacing() — the values QgsTextFormat's Font tab 'character/word spacing' fields bake into the font object. story_xml's CharacterStyleRange (idml_package.py 380-393) never emits a Tracking attribute at all.

**Suggested fix:** Read font.letterSpacing()/letterSpacingType() in both run builders, convert to IDML Tracking (1/1000 em) on the CharacterStyleRange.

### 22. Character-level color alpha (semi-transparent text) is silently made fully opaque — **FIXED in 0.5.1**

*medium · `export_idml/idml_package.py`*

ColorRegistry.ref() (lines 63-70) keys colors by (r,g,b) only, discarding alpha entirely. story_xml's CharacterStyleRange (lines 380-393) sets only FillColor, with no equivalent of mapping.py's _transparency_xml (used for shapes) applied to text runs — so a QColor with alpha < 255 (e.g. a 50%-opacity text color set via Format > Font, or an HTML span with rgba() color) exports as 100% opaque.

**Suggested fix:** Carry alpha alongside RGB per run (or a separate opacity field) and emit <FillTransparencySetting><BlendingSetting Opacity="..."/></FillTransparencySetting> inside each CharacterStyleRange, mirroring the pattern _transparency_xml already uses for shape fills.

### 23. Sub/superscript spans (<sub>/<sup>) render as normal baseline text — **FIXED in 0.5.1**

*medium · `export_idml/text_runs.py`*

_run_from_format (lines 41-65) never reads char_format.verticalAlignment() (Qt's AlignSubScript/AlignSuperScript). A label authored with real <sup>/<sub> markup (e.g. 'CO<sub>2</sub>', 'm<sup>2</sup>') loses the vertical shift and size reduction on export.

**Suggested fix:** Read char_format.verticalAlignment() per run and set IDML's Position="Superscript"/"Subscript" on the corresponding CharacterStyleRange.

### 24. Custom tab stops are never emitted; a literal tab character falls back to InDesign's default tab grid — **FIXED in 0.6.0**

*medium · `export_idml/text_runs.py`*

_para_from_block (lines 68-96) never reads block.tabPositions(). story_xml's paragraph-attribute builder (idml_package.py lines 363-378) only ever sets Justification/LeftIndent/FirstLineIndent/SpaceAfter — no <TabList> is ever produced. Meanwhile a literal '\t' inside run text survives into <Content> unchanged (idml_package.py's _ILLEGAL_XML at line 336-338 explicitly excludes 0x09 from stripping), so InDesign will align it to its own default tab stops instead of whatever position QGIS/Qt intended.

**Suggested fix:** Read block.tabPositions(), convert px to pt, and emit a <TabList> (<TabStop Alignment="LeftAlign" Position="..."/> per entry) as a paragraph Properties child in story_xml.

### 25. Data-defined (per-atlas-feature) item rotation is ignored — **FIXED in 0.6.0**

*medium · `C:/Users/victor.budinic/Desktop/BIM/scripts/qgis2idml/export_idml/mapping.py`*

item_geometry() (line ~176) reads rot = item.itemRotation(). QGIS's own API doc for itemRotation() explicitly warns: 'this method will always return the user-set rotation for the item, which may differ from the current item rotation (if data defined rotation settings are present). Use QGraphicsItem.rotation() to obtain the current item rotation.' Confirmed live that QgsLayoutObject exposes an ItemRotation data-defined property (QgsLayoutObject.ItemRotation), so a layout item's rotation CAN legitimately be driven by an expression (e.g. rotating a directional icon/label per atlas feature - a realistic pattern for this project's atlas-driven Steckbriefe-style exports). Because the code never consults dataDefinedProperties().property(QgsLayoutObject.ItemRotation) nor uses item.rotation(), any such item is exported at its static base rotation (often 0) instead of the per-feature evaluated …

**Suggested fix:** Use item.rotation() (the live QGraphicsItem rotation, reflecting any data-defined override after refresh) instead of item.itemRotation() when computing rot in item_geometry(), or explicitly evaluate the ItemRotation data-defined property when active.

### 26. An item spanning a page boundary is placed on only one spread and is missing from the other — **FIXED in 0.6.0**

*medium · `C:/Users/victor.budinic/Desktop/BIM/scripts/qgis2idml/export_idml/exporter.py`*

_page_items() (lines ~60-99) filters items strictly by if it.page() != page_index: continue, and _export_pages() (lines ~102-114) creates exactly one Spread per QGIS page, calling export_item() only for items whose .page() equals that page's index. Confirmed live: a shape positioned to straddle two pages (e.g. extending 20mm into the bottom of page 0 and 20mm into the top of page 1, spanning the inter-page gap) reports item.page() == 0 only. In QGIS's own rendering/printing, each page is a crop of one continuous canvas, so such an item visibly appears (clipped) on BOTH pages. Because IDML spreads here are independent, disconnected single-page coordinate spaces (FacingPages=false, one page per Spread), placing the full item only on spread 0 puts the overflow into spread 0's pasteboard - it does not appear on spread 1's page at all, so the portion QGIS shows on page 1 is entirely missing …

**Suggested fix:** For items whose sceneBoundingRect() overlaps more than one page's rect, either clip/duplicate the item's placement onto each overlapping spread (positioned/clipped per page), or route such items through the export_fallback per-page rendered-PDF-snippet path (which already renders/crops relative to a given page) instead of a single native placement.

### 27. QgsLayoutItem.blendMode() is never read — everything exports as Normal blend — **FIXED in 0.6.0**

*medium · `export_idml/mapping.py`*

_transparency_xml (lines 488-509) only ever writes <BlendingSetting Opacity="..."/>, never a BlendMode attribute, and item.blendMode() is not queried anywhere in the file. IDML/InDesign's BlendingSetting supports BlendMode values (Multiply, Screen, Darken, etc.). Any QGIS layout item using a non-Normal blend mode (a common technique to darken/tint a map overlay, or knock a shape into underlying artwork) will render with a completely different composite result in InDesign, since it silently falls back to Normal.

**Suggested fix:** Map item.blendMode() (and symbol-layer blend mode for shapes, if set) to the BlendMode attribute of BlendingSetting.

### 28. Stroke cap and join style are never read — always InDesign's default (butt cap / miter join) — **FIXED in 0.6.0**

*medium · `export_idml/mapping.py`*

Nowhere in mapping.py is QgsSimpleLineSymbolLayer.penCapStyle() or penJoinStyle() (or the equivalent QgsSimpleFillSymbolLayer stroke cap/join) queried, and no shape/polygon/polyline element ever emits StrokeCap or StrokeJoin attributes. QGIS symbols commonly use round caps/joins for thick paths (a frequent choice for hand-drawn-looking plan lines); those render with square/mitered corners and ends in InDesign instead, a visible difference especially at sharp polygon corners or line endpoints with heavy stroke weight.

**Suggested fix:** Map penCapStyle()/penJoinStyle() to IDML's StrokeCap ("Butt"/"Round"/"Projecting") and StrokeJoin ("Miter"/"Round"/"Bevel") attributes on Rectangle/Oval/Polygon/GraphicLine elements.

### 29. Map/Picture frame background and stroke color always export fully opaque, even when semi-transparent in QGIS — **FIXED in 0.6.0**

*medium · `export_idml/mapping.py`*

item_frame_attrs (lines 111-126) builds FillColor/StrokeColor purely via ColorRegistry.ref(), which (idml_package.py lines 63-70) discards the alpha channel for any alpha>0 (only alpha==0 maps to Swatch/None). Neither placed_pdf_xml (used by export_map for all maps, and by export_picture for PDF assets) nor the raster-image Rectangle built in export_picture (lines 784-817) ever call _transparency_xml for that frame's fill/stroke — unlike export_label, which explicitly special-cases its own background alpha via bg_transparency (lines 385-389, and even there only the fill/background is handled, not a semi-transparent frame stroke). So a QgsLayoutItemMap or QgsLayoutItemPicture with a semi-transparent background color or border (item.backgroundColor()/frameStrokeColor() alpha < 255) renders fully opaque in InDesign, hiding whatever QGIS layered underneath it.

**Suggested fix:** Extend item_frame_attrs (or its callers) to compute FillTransparencySetting/StrokeTransparencySetting from backgroundColor().alpha() and frameStrokeColor().alpha() for every item type that uses it, the same way export_shape already does for symbol fill/stroke.

### 30. Items straddling two layout pages are placed whole on one spread, missing on the adjacent one — **FIXED in 0.6.0**

*medium · `export_idml/exporter.py`*

_page_items(layout, page_index) (line 83: `if it.page() != page_index: continue`) assigns every item to exactly one page/spread via item.page(). For an item whose geometry actually overlaps two pages (e.g. a legend or attribute table near the bottom of page 1 continuing onto page 2 — plausible in multi-page-per-feature atlas report templates), the exporter renders and places it ONLY on its assigned page's spread, using the item's full un-clipped rect()/sceneBoundingRect() (mapping.py item_geometry() / export_fallback() line 881). QGIS's own page-by-page PDF/image export instead clips such an item hard at each page boundary and continues its content onto the next page's render. Result: the assigned spread shows the item bleeding past its own page's crop (invisible once actually printed at page size, but present past the fold in InDesign), while the OTHER page's spread — where QGIS would …

**Suggested fix:** When an item's bounds extend past its assigned page's edge into a neighbouring page, either clip the rendered snippet/geometry per page and emit a correspondingly-clipped second placement on the neighbouring spread, or at minimum add a warning (mirroring the existing per-item try/except in _export_pages) so multi-page-spanning items get flagged for manual review.

### 31. QgsTextFormat.blendMode() (and any text blend mode) is never applied — **FIXED in 0.6.0**

*low · `export_idml/mapping.py`*

tf.blendMode() (e.g. Multiply, so label text visually mixes with what's underneath) is never read in export_label(). Unlike shapes (which get _transparency_xml, line 488), no BlendingSetting/Mode attribute is ever emitted for a TextFrame — and even the shape path's _transparency_xml only ever sets Opacity, never Mode, so a non-Normal blend mode is dropped everywhere in this pipeline, text included.

**Suggested fix:** Read tf.blendMode(), map the QPainter::CompositionMode to InDesign's blend-mode enum, and emit <TransparencySetting><BlendingSetting Mode="..." .../></TransparencySetting> on the TextFrame.

### 32. Inline background-color spans (CSS highlight) are dropped — **documented, no practical IDML fix** (0.6.0 warns where detectable)

*low · `export_idml/text_runs.py`*

_run_from_format only reads char_format.foreground() for run color (line 55-56); char_format.background() (the brush set by a <span style="background-color:...">, e.g. a highlighted keyword) is never inspected, so any inline highlight is lost on export.

**Suggested fix:** Read char_format.background(); when it is a non-null brush, either approximate it with a small filled Rectangle behind the run or surface an export warning that inline highlight spans are unsupported.

### 33. Hyperlinks (<a href>) become inert, unlinked plain text — **FIXED in 0.6.0**

*low · `export_idml/text_runs.py`*

_run_from_format never reads char_format.isAnchor()/anchorHref(), and no Hyperlink/HyperlinkTextSource objects are ever built in idml_package.py, so a link in an HTML label produces plain text with no click behavior (and no guaranteed visual styling, since underline/color aren't forced from anchor state either).

**Suggested fix:** Read anchorHref(); emit an IDML HyperlinkURLDestination plus a HyperlinkTextSource range over the run, or at minimum apply underline+link color as a visual fallback and log a warning that link functionality was dropped.

### 34. FirstBaselineOffset is hardcoded to 'AscentOffset' for every text frame, independent of font or vAlign — **documented, no practical IDML fix** (0.6.0 warns where detectable)

*low · `export_idml/mapping.py`*

Both _export_table_label (~line 296) and export_label (~line 447) hardcode FirstBaselineOffset="AscentOffset" in the TextFramePreference. InDesign's AscentOffset is derived from the placed font's own hhea/OS2 vertical-metrics tables, which can diverge from Qt's runtime QFontMetrics ascent used by QGIS/QgsTextRenderer to position text — by a few points, more for fonts with unusual vertical metrics. This is most visible for short, precisely Bottom- or Center-aligned single-line labels (the autosize/valign logic at lines 409-432 relies on exact positioning).

**Suggested fix:** No bit-exact fix is available since InDesign derives this from font-file metrics, but consider validating AscentOffset vs LeadingOffset/FixedOffset empirically for the font families actually in use, and for BottomAlign/CenterAlign cases apply a small compensating vertical nudge to the ItemTransform ty computed from QFontMetricsF ascent (the pattern already used in _natural_width_pt).

### 35. export_polyline never emits any transparency — a semi-transparent line stroke always renders fully opaque — **FIXED in 0.6.0**

*low · `export_idml/mapping.py`*

export_polyline (lines 678-716) builds its GraphicLine's StrokeColor via 'pkg.colors.ref(sl.color())' (line 693) but never calls _transparency_xml, and GraphicLine's XML (lines 703-716) has no transparency child at all. Unlike export_shape (which correctly emits StrokeTransparencySetting from the stroke color's alpha), a QgsLayoutItemPolyline styled with a translucent stroke color (e.g. a soft sightline/annotation arrow at 50% opacity) exports as a fully opaque line in InDesign.

**Suggested fix:** Compute stroke alpha from sl.color().alpha() in export_polyline and append a StrokeTransparencySetting via _transparency_xml, mirroring export_shape's handling.

### 36. Export dpi silently controls hairline stroke width in map/fallback PDFs — **documented, no practical IDML fix** (0.6.0 warns where detectable)

*low · `export_idml/mapping.py`*

_pdf_writer() (lines 184-192) calls writer.setResolution(int(dpi)) where dpi is the export's raster-quality knob (default 300, exposed on export_layout_to_idml(dpi=...)). Both export_map() and export_fallback() reuse this same writer for genuinely vector content. Qt's PDF backend derives the physical size of a cosmetic/0-width pen from the device resolution (1 device px = 72/dpi pt), so any QGIS line symbol layer using width '0' ('hairline', a common way to mean 'always exactly 1 rendered pixel') comes out at a different physical thickness purely as a function of the dpi value passed to the exporter — e.g. 0.24pt at 300dpi vs 0.12pt at 600dpi for the identical QGIS style. A user raising dpi for sharper raster/image quality will, without any indication, also halve the thickness of every hairline-style stroke in the map and in any fallback-rendered content (e.g. legend swatches) — a …

**Suggested fix:** Document the dpi/hairline coupling explicitly, or warn when hairline-width (0pt) symbol layers are present in the map's active layers so operators know stroke thickness is dpi-dependent; a full fix isn't practically available through QPdfWriter's public API.