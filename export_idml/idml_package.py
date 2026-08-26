"""IDML package model + writer.

Builds a complete .idml ZIP package from scratch (no InDesign template
needed).  Structure per the IDML File Format Specification:

    mimetype                          (first entry, STORED/uncompressed)
    designmap.xml                     (manifest, idPkg namespace)
    Resources/Fonts.xml               (font *references* - never embedded)
    Resources/Styles.xml              (minimal root styles)
    Resources/Graphic.xml             (colors, swatches, stroke styles)
    Resources/Preferences.xml         (page size etc.)
    MasterSpreads/MasterSpread_*.xml
    Spreads/Spread_*.xml              (one per exported layout page)
    Stories/Story_*.xml               (one per text frame)
    XML/Tags.xml, XML/BackingStory.xml

All Self ids carry the "qx" prefix so a later SimpleIDML
prefix()/insert_idml() merge with other report parts stays collision-free.
"""

import zipfile
from xml.sax.saxutils import escape, quoteattr

from .geom import fmt, identity_at, rect_path

IDPKG = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"
MIMETYPE = "application/vnd.adobe.indesign-idml-package"

XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
AID_PI = (
    '<?aid style="50" type="%s" readerVersion="6.0" featureSet="257" '
    'product="16.0(30)" ?>\n'
)


def _wrap(kind, inner):
    """Wrap a payload element in the idPkg envelope used by package parts."""
    return (
        XML_DECL
        + AID_PI % "snippet"
        + '<idPkg:{kind} xmlns:idPkg="{ns}" DOMVersion="16.0">\n{inner}</idPkg:{kind}>\n'.format(
            kind=kind, ns=IDPKG, inner=inner
        )
    )


class IdGen:
    def __init__(self, prefix="qx"):
        self.prefix = prefix
        self.n = 0

    def next(self, tag=""):
        self.n += 1
        return "{}{}{}".format(self.prefix, tag, self.n)


class ColorRegistry:
    """Collects RGB colors + dashed stroke styles for Resources/Graphic.xml."""

    def __init__(self):
        self._colors = {}  # (r,g,b) -> self id
        self._stroke_styles = {}  # dash tuple (pt) -> self id

    def ref(self, qcolor):
        """Return an IDML color reference for a QColor (or 'Swatch/None')."""
        if qcolor is None or qcolor.alpha() == 0:
            return "Swatch/None"
        key = (qcolor.red(), qcolor.green(), qcolor.blue())
        if key == (0, 0, 0):
            # registration-safe K-only black instead of RGB rich black
            return "Color/Black"
        if key not in self._colors:
            self._colors[key] = "Color/R={} G={} B={}".format(*key)
        return self._colors[key]

    def stroke_style(self, dash_array_pt):
        """Register a dashed stroke style; returns its StrokeType ref."""
        key = tuple(round(v, 3) for v in dash_array_pt)
        if key not in self._stroke_styles:
            self._stroke_styles[key] = "StrokeStyle/qxDash{}".format(
                len(self._stroke_styles) + 1
            )
        return self._stroke_styles[key]

    def graphic_xml(self):
        parts = []
        parts.append(
            '<Color Self="Color/Black" Model="Process" Space="CMYK" '
            'ColorValue="0 0 0 100" ColorOverride="Specialblack" '
            'AlternateSpace="NoAlternateColor" AlternateColorValue="" '
            'Name="Black" ColorEditable="false" ColorRemovable="false" '
            'Visible="true" SwatchCreatorID="7937"/>'
        )
        parts.append(
            '<Color Self="Color/Paper" Model="Process" Space="CMYK" '
            'ColorValue="0 0 0 0" AlternateSpace="NoAlternateColor" '
            'AlternateColorValue="" Name="Paper" ColorEditable="true" '
            'ColorRemovable="false" Visible="true" SwatchCreatorID="7937"/>'
        )
        for (r, g, b), self_id in sorted(self._colors.items()):
            parts.append(
                '<Color Self={self_id} Model="Process" Space="RGB" '
                'ColorValue="{r} {g} {b}" AlternateSpace="NoAlternateColor" '
                'AlternateColorValue="" Name={name} ColorEditable="true" '
                'ColorRemovable="true" Visible="true" SwatchCreatorID="7937"/>'.format(
                    self_id=quoteattr(self_id),
                    name=quoteattr(self_id.split("/", 1)[1]),
                    r=r,
                    g=g,
                    b=b,
                )
            )
        parts.append(
            '<Swatch Self="Swatch/None" Name="None" ColorEditable="false" '
            'ColorRemovable="false" Visible="true" SwatchCreatorID="7937"/>'
        )
        parts.append(
            '<StrokeStyle Self="StrokeStyle/$ID/Solid" Name="$ID/Solid"/>'
        )
        for dash, self_id in sorted(self._stroke_styles.items()):
            items = "".join(
                '<ListItem type="unit">{}</ListItem>'.format(fmt(v)) for v in dash
            )
            parts.append(
                '<DashedStrokeStyle Self={sid} Name={name} '
                'StrokeCornerAdjustment="None">'
                '<Properties><DashArray type="list">{items}</DashArray></Properties>'
                "</DashedStrokeStyle>".format(
                    sid=quoteattr(self_id),
                    name=quoteattr(self_id.split("/", 1)[1]),
                    items=items,
                )
            )
        return _wrap("Graphic", "\n".join(parts) + "\n")


class FontRegistry:
    """Collects (family, style) -> Resources/Fonts.xml.

    IDML spec 6.1: fonts are never embedded; <FontFamily>/<Font> entries
    are references resolved against installed fonts (PostScriptName).
    """

    def __init__(self, font_index):
        self._families = {}  # family -> {style: info-or-None}
        self._index = font_index

    def use(self, family, style="Regular"):
        """Register a font use; returns the CANONICAL (family, style).

        Qt often reports the legacy GDI family ("Futura PT Book" +
        "Book") -- the index resolves that to the typographic names
        InDesign expects ("Futura PT" + "Book")."""
        info = self._index.lookup(family, style) if self._index else None
        if info:
            family, style = info["family"], info["style"]
        fam = self._families.setdefault(family, {})
        if style not in fam:
            fam[style] = info
        return family, style

    def used_files(self):
        files = []
        for fam in self._families.values():
            for info in fam.values():
                if info and info.get("path"):
                    files.append(info["path"])
        return sorted(set(files))

    def fonts_xml(self, idgen):
        parts = []
        for family in sorted(self._families):
            fam_id = idgen.next("ff")
            parts.append(
                "<FontFamily Self={} Name={}>".format(
                    quoteattr("di" + fam_id), quoteattr(family)
                )
            )
            for style in sorted(self._families[family]):
                info = self._families[family][style]
                ps = (info or {}).get("postscript") or "{}-{}".format(
                    family.replace(" ", ""), style.replace(" ", "")
                )
                parts.append(
                    "<Font Self={self_id} FontFamily={fam} Name={name} "
                    "PostScriptName={ps} Status=\"Installed\" "
                    "FontStyleName={style} FontType=\"OpenTypeTT\" "
                    "WritingScript=\"0\" FullName={full} "
                    "PlatformName=\"$ID/\" Version=\"\"/>".format(
                        self_id=quoteattr("di" + idgen.next("f")),
                        fam=quoteattr(family),
                        name=quoteattr("{} {}".format(family, style)),
                        ps=quoteattr(ps),
                        style=quoteattr(style),
                        full=quoteattr("{} {}".format(family, style)),
                    )
                )
            parts.append("</FontFamily>")
        return _wrap("Fonts", "\n".join(parts) + "\n")


def styles_xml():
    inner = """<RootCharacterStyleGroup Self="qxRootCharacterStyleGroup">
<CharacterStyle Self="CharacterStyle/$ID/[No character style]" Imported="false" Name="$ID/[No character style]"/>
</RootCharacterStyleGroup>
<RootParagraphStyleGroup Self="qxRootParagraphStyleGroup">
<ParagraphStyle Self="ParagraphStyle/$ID/[No paragraph style]" Name="$ID/[No paragraph style]" Imported="false" NextStyle="ParagraphStyle/$ID/[No paragraph style]" SplitDocument="false" EmitCss="true" StyleUniqueId="$ID/" IncludeClass="true" EmptyNestedStyles="true" EmptyLineStyles="true" EmptyGrepStyles="true"/>
<ParagraphStyle Self="ParagraphStyle/$ID/NormalParagraphStyle" Name="$ID/NormalParagraphStyle" Imported="false" NextStyle="ParagraphStyle/$ID/NormalParagraphStyle" SplitDocument="false" EmitCss="true" StyleUniqueId="$ID/" IncludeClass="true" EmptyNestedStyles="true" EmptyLineStyles="true" EmptyGrepStyles="true"/>
</RootParagraphStyleGroup>
<RootObjectStyleGroup Self="qxRootObjectStyleGroup">
<ObjectStyle Self="ObjectStyle/$ID/[None]" Name="$ID/[None]" AppliedParagraphStyle="ParagraphStyle/$ID/[No paragraph style]"/>
<ObjectStyle Self="ObjectStyle/$ID/[Normal Graphics Frame]" Name="$ID/[Normal Graphics Frame]" AppliedParagraphStyle="ParagraphStyle/$ID/[No paragraph style]"/>
<ObjectStyle Self="ObjectStyle/$ID/[Normal Text Frame]" Name="$ID/[Normal Text Frame]" AppliedParagraphStyle="ParagraphStyle/$ID/[No paragraph style]"/>
<ObjectStyle Self="ObjectStyle/$ID/[Normal Grid]" Name="$ID/[Normal Grid]" AppliedParagraphStyle="ParagraphStyle/$ID/[No paragraph style]"/>
</RootObjectStyleGroup>
<RootTableStyleGroup Self="qxRootTableStyleGroup">
<TableStyle Self="TableStyle/$ID/[No table style]" Name="$ID/[No table style]"/>
</RootTableStyleGroup>
<RootCellStyleGroup Self="qxRootCellStyleGroup">
<CellStyle Self="CellStyle/$ID/[None]" Name="$ID/[None]"/>
</RootCellStyleGroup>
"""
    return _wrap("Styles", inner)


def preferences_xml(page_w_pt, page_h_pt, pages_per_document):
    inner = (
        '<DocumentPreference PageHeight="{h}" PageWidth="{w}" '
        'PagesPerDocument="{n}" FacingPages="false" '
        'DocumentBleedTopOffset="0" DocumentBleedBottomOffset="0" '
        'DocumentBleedInsideOrLeftOffset="0" '
        'DocumentBleedOutsideOrRightOffset="0" '
        'DocumentBleedUniformSize="true" SlugTopOffset="0" '
        'SlugBottomOffset="0" SlugInsideOrLeftOffset="0" '
        'SlugRightOrOutsideOffset="0" AllowPageShuffle="true" '
        'PreserveLayoutWhenShuffling="true" PageBinding="LeftToRight" '
        'IntentDestination="PrintIntent"/>\n'
        '<ViewPreference HorizontalMeasurementUnits="Points" '
        'VerticalMeasurementUnits="Points" RulerOrigin="PageOrigin"/>\n'
        '<MarginPreference ColumnCount="1" ColumnGutter="12" Top="0" '
        'Bottom="0" Left="0" Right="0" ColumnDirection="Horizontal" '
        'ColumnsPositions="0 {w}"/>\n'
    ).format(w=fmt(page_w_pt), h=fmt(page_h_pt), n=pages_per_document)
    return _wrap("Preferences", inner)


def tags_xml():
    inner = (
        '<XMLTag Self="XMLTag/Root" Name="Root">\n'
        "<Properties><TagColor type=\"enumeration\">LightBlue</TagColor></Properties>\n"
        "</XMLTag>\n"
    )
    return _wrap("Tags", inner)


def backing_story_xml():
    inner = (
        '<XmlStory Self="qxXmlStory" AppliedTOCStyle="n" TrackChanges="false" '
        'StoryTitle="$ID/" AppliedNamedGrid="n">\n'
        '<StoryPreference OpticalMarginAlignment="false" OpticalMarginSize="12" '
        'FrameType="TextFrameType" StoryOrientation="Horizontal" '
        'StoryDirection="LeftToRightDirection"/>\n'
        '<XMLElement Self="qxdi2" MarkupTag="XMLTag/Root"/>\n'
        "</XmlStory>\n"
    )
    return _wrap("BackingStory", inner)


def master_spread_xml(master_id, page_w_pt, page_h_pt):
    inner = (
        '<MasterSpread Self="{mid}" ItemTransform="1 0 0 1 0 0" Name="A-Master" '
        'NamePrefix="A" BaseName="Master" ShowMasterItems="true" PageCount="1" '
        'OverriddenPageItemProps="">\n'
        '<Page Self="{mid}p" GeometricBounds="0 0 {h} {w}" '
        'ItemTransform="1 0 0 1 {tx} {ty}" Name="A" '
        'AppliedMaster="n" OverrideList="" TabOrder="" '
        'GridStartingPoint="TopOutside" UseMasterGrid="true">\n'
        "<Properties><PageColor type=\"enumeration\">UseMasterColor</PageColor></Properties>\n"
        '<MarginPreference ColumnCount="1" ColumnGutter="12" Top="0" Bottom="0" '
        'Left="0" Right="0" ColumnDirection="Horizontal" '
        'ColumnsPositions="0 {w}"/>\n'
        "</Page>\n"
        "</MasterSpread>\n"
    ).format(
        mid=master_id,
        w=fmt(page_w_pt),
        h=fmt(page_h_pt),
        tx=fmt(-page_w_pt / 2.0),
        ty=fmt(-page_h_pt / 2.0),
    )
    return _wrap("MasterSpread", inner)


class Spread:
    """One IDML spread holding one page (one QGIS layout page)."""

    def __init__(self, spread_id, page_id, master_id, page_w_pt, page_h_pt):
        self.spread_id = spread_id
        self.page_id = page_id
        self.master_id = master_id
        self.w = page_w_pt
        self.h = page_h_pt
        self.items = []  # raw XML strings, in paint order (later = on top)
        self._group_stack = []  # open <Group> buffers

    def page_offset(self):
        """Spread coords of the page's top-left corner."""
        return (-self.w / 2.0, -self.h / 2.0)

    def add(self, xml):
        (self._group_stack[-1][1] if self._group_stack else self.items).append(xml)

    def begin_group(self, group_id, name):
        """Open an IDML <Group>; subsequent add() calls land inside it.

        Group inner space == spread space (identity transform), so
        children keep their normal spread-coordinate ItemTransforms."""
        self._group_stack.append(((group_id, name), []))

    def end_group(self):
        (group_id, name), children = self._group_stack.pop()
        from xml.sax.saxutils import quoteattr as _qa

        xml = (
            '<Group Self="{gid}" ItemLayer="qxLayer1" Visible="true" '
            "Name={name} "
            'AppliedObjectStyle="ObjectStyle/$ID/[None]" '
            'ItemTransform="1 0 0 1 0 0">{body}</Group>'.format(
                gid=group_id, name=_qa(name or "$ID/"), body="".join(children)
            )
        )
        self.add(xml)

    def xml(self):
        parts = [
            '<Spread Self="{sid}" FlattenerOverride="Default" '
            'AllowPageShuffle="true" ItemTransform="1 0 0 1 0 0" '
            'ShowMasterItems="true" PageCount="1" BindingLocation="0" '
            'PageTransitionType="None" PageTransitionDirection="NotApplicable" '
            'PageTransitionDuration="Medium">'.format(sid=self.spread_id),
            '<Page Self="{pid}" GeometricBounds="0 0 {h} {w}" '
            'ItemTransform="1 0 0 1 {tx} {ty}" Name="1" '
            'AppliedMaster="{mid}" OverrideList="" TabOrder="" '
            'GridStartingPoint="TopOutside" UseMasterGrid="true">'
            "<Properties><PageColor type=\"enumeration\">UseMasterColor</PageColor></Properties>"
            '<MarginPreference ColumnCount="1" ColumnGutter="12" Top="0" '
            'Bottom="0" Left="0" Right="0" ColumnDirection="Horizontal" '
            'ColumnsPositions="0 {w}"/>'
            "</Page>".format(
                pid=self.page_id,
                mid=self.master_id,
                w=fmt(self.w),
                h=fmt(self.h),
                tx=fmt(-self.w / 2.0),
                ty=fmt(-self.h / 2.0),
            ),
        ]
        parts.extend(self.items)
        parts.append("</Spread>")
        return _wrap("Spread", "\n".join(parts) + "\n")


_ILLEGAL_XML = dict.fromkeys(
    c for c in range(0x20) if c not in (0x09,)
)  # strip C0 controls except tab (\n was already mapped to U+2028)


def _clean(text):
    return text.translate(_ILLEGAL_XML)


def story_xml(story_id, paragraphs, color_registry, font_registry,
              idgen=None, hyperlinks=None):
    """Build a Story from the neutral paragraph/run structure.

    IDML content model: each <ParagraphStyleRange> is one paragraph; the
    paragraph-terminating <Br/> sits INSIDE the paragraph's last
    <CharacterStyleRange> (a separate range holding only <Br/> would read
    as an extra empty paragraph in InDesign)."""
    parts = [
        '<Story Self="{sid}" AppliedTOCStyle="n" TrackChanges="false" '
        'StoryTitle="$ID/" AppliedNamedGrid="n">'.format(sid=story_id),
        '<StoryPreference OpticalMarginAlignment="false" OpticalMarginSize="12" '
        'FrameType="TextFrameType" StoryOrientation="Horizontal" '
        'StoryDirection="LeftToRightDirection"/>',
        '<InCopyExportOption IncludeGraphicProxies="true" IncludeAllResources="false"/>',
    ]
    n_paras = len(paragraphs)
    for pi, para in enumerate(paragraphs):
        needs_break = pi < n_paras - 1
        para_attrs = ['Justification="{}"'.format(para.get("align", "LeftAlign"))]
        # block metrics from Qt rich text (hanging bullets, spacing)
        li = para.get("left_indent_pt") or 0.0
        fli = para.get("first_line_indent_pt") or 0.0
        sa = para.get("space_after_pt") or 0.0
        if li:
            para_attrs.append('LeftIndent="{}"'.format(fmt(li)))
        if fli:
            para_attrs.append('FirstLineIndent="{}"'.format(fmt(fli)))
        if sa:
            para_attrs.append('SpaceAfter="{}"'.format(fmt(sa)))
        # word spacing: extra pt per space -> percentage of a normal space
        ws_runs = [r for r in para.get("runs", []) if r.get("word_spacing_pt")]
        if ws_runs:
            r0 = ws_runs[0]
            normal = 0.25 * (r0.get("size_pt") or 10.0)  # ~space width
            pct = max(0, min(500, 100.0 + r0["word_spacing_pt"] / normal * 100.0))
            for a in ("DesiredWordSpacing", "MinimumWordSpacing", "MaximumWordSpacing"):
                para_attrs.append('{}="{}"'.format(a, fmt(pct)))
        parts.append(
            '<ParagraphStyleRange '
            'AppliedParagraphStyle="ParagraphStyle/$ID/NormalParagraphStyle" '
            "{}>".format(" ".join(para_attrs))
        )
        tabs = para.get("tabs")
        if tabs:
            records = "".join(
                '<ListItem type="record">'
                '<Alignment type="enumeration">{align}</Alignment>'
                '<AlignmentCharacter type="string">.</AlignmentCharacter>'
                '<Leader type="string"></Leader>'
                '<Position type="unit">{pos}</Position>'
                "</ListItem>".format(align=align, pos=fmt(pos))
                for pos, align in tabs
            )
            parts.append(
                '<Properties><TabList type="list">{}</TabList></Properties>'.format(
                    records
                )
            )
        runs = para["runs"]
        for ri, run in enumerate(runs):
            family, style = font_registry.use(run["family"], run["style"])
            fill = color_registry.ref(run.get("color"))
            attrs = [
                'AppliedCharacterStyle="CharacterStyle/$ID/[No character style]"',
                'PointSize="{}"'.format(fmt(run["size_pt"])),
                "FillColor={}".format(quoteattr(fill)),
                "FontStyle={}".format(quoteattr(style)),
            ]
            if run.get("underline"):
                attrs.append('Underline="true"')
            if run.get("strikeout"):
                attrs.append('StrikeThru="true"')
            if run.get("tracking"):
                attrs.append('Tracking="{}"'.format(int(run["tracking"])))
            if run.get("caps"):
                attrs.append('Capitalization="{}"'.format(run["caps"]))
            if run.get("position"):
                attrs.append('Position="{}"'.format(run["position"]))
            color = run.get("color")
            if color is not None and 0 < color.alpha() < 255:
                # semi-transparent text -> tint of the solid swatch
                attrs.append('FillTint="{}"'.format(fmt(color.alpha() / 255.0 * 100.0)))
            if run.get("stroke_weight_pt"):
                # QGIS text buffer/halo -> outlined type (stroke straddles
                # the outline, so weight = 2 x buffer radius)
                attrs.append(
                    "StrokeColor={}".format(
                        quoteattr(color_registry.ref(run.get("stroke_color")))
                    )
                )
                attrs.append('StrokeWeight="{}"'.format(fmt(run["stroke_weight_pt"])))
                tint = run.get("stroke_tint")
                if tint is not None and tint < 100:
                    attrs.append('StrokeTint="{}"'.format(fmt(tint)))
            href = run.get("href")
            hts_id = None
            if href and idgen is not None and hyperlinks is not None:
                hts_id = idgen.next("hts")
                parts.append(
                    '<HyperlinkTextSource Self="{sid}" Name={name} '
                    'Hidden="false">'.format(sid=hts_id, name=quoteattr(href))
                )
                hyperlinks.append((hts_id, href))
            parts.append("<CharacterStyleRange {}>".format(" ".join(attrs)))
            props = ["<AppliedFont type=\"string\">{}</AppliedFont>".format(escape(family))]
            leading_pt = para.get("leading_pt")
            lh = para.get("line_height_pct")
            if leading_pt:
                props.append(
                    "<Leading type=\"unit\">{}</Leading>".format(fmt(leading_pt))
                )
            elif lh:
                # proportional line-height -> absolute IDML leading
                props.append(
                    "<Leading type=\"unit\">{}</Leading>".format(
                        fmt(run["size_pt"] * lh / 100.0)
                    )
                )
            parts.append("<Properties>{}</Properties>".format("".join(props)))
            # newline inside a run -> forced (soft) line break = U+2028
            # (<Br/> would be a *paragraph* break in IDML)
            text = _clean(run["text"]).replace("\n", chr(0x2028))
            if text:
                parts.append("<Content>{}</Content>".format(escape(text)))
            if needs_break and ri == len(runs) - 1:
                parts.append("<Br/>")  # paragraph break lives IN the paragraph
            parts.append("</CharacterStyleRange>")
            if hts_id is not None:
                parts.append("</HyperlinkTextSource>")
        if needs_break and not runs:
            # empty paragraph still needs its terminating break
            parts.append(
                "<CharacterStyleRange AppliedCharacterStyle="
                '"CharacterStyle/$ID/[No character style]"><Br/></CharacterStyleRange>'
            )
        parts.append("</ParagraphStyleRange>")
    parts.append("</Story>")
    return _wrap("Story", "\n".join(parts) + "\n")


class IdmlPackage:
    """Accumulates parts, then writes the .idml zip."""

    def __init__(self, page_w_pt, page_h_pt, font_index=None):
        self.idgen = IdGen()
        self.colors = ColorRegistry()
        self.fonts = FontRegistry(font_index)
        self.page_w = page_w_pt
        self.page_h = page_h_pt
        self.master_id = "qxMasterA"
        self.spreads = []  # Spread objects
        self.stories = []  # (story_id, xml)
        self.hyperlinks = []  # (HyperlinkTextSource id, url)

    def new_spread(self, page_w_pt=None, page_h_pt=None):
        sp = Spread(
            self.idgen.next("sp"),
            self.idgen.next("pg"),
            self.master_id,
            page_w_pt if page_w_pt else self.page_w,
            page_h_pt if page_h_pt else self.page_h,
        )
        self.spreads.append(sp)
        return sp

    def add_story(self, paragraphs):
        if not paragraphs:
            # InDesign always writes at least one paragraph range
            paragraphs = [{"type": "para", "align": "LeftAlign", "runs": []}]
        story_id = self.idgen.next("st")
        xml = story_xml(
            story_id, paragraphs, self.colors, self.fonts,
            idgen=self.idgen, hyperlinks=self.hyperlinks,
        )
        self.stories.append((story_id, xml))
        return story_id

    def designmap(self):
        parts = [
            XML_DECL,
            AID_PI % "document",
            '<Document xmlns:idPkg="{ns}" DOMVersion="16.0" Self="qxdoc" '
            'StoryList="{stories}" Name="export" ZeroPoint="0 0" '
            'ActiveLayer="qxLayer1" CMYKProfile="U.S. Web Coated (SWOP) v2" '
            'RGBProfile="sRGB IEC61966-2.1" SolidColorIntent="UseColorSettings" '
            'AfterBlendingIntent="UseColorSettings" '
            'DefaultImageIntent="UseColorSettings" RGBPolicy="PreserveEmbeddedProfiles" '
            'CMYKPolicy="CMYK_IgnoreProfiles" AccurateLDSNative="true">\n'.format(
                ns=IDPKG, stories=" ".join(sid for sid, _ in self.stories)
            ),
            '<Language Self="Language/$ID/English%3a USA" Name="$ID/English: USA" '
            'SingleQuotes="&#8216;&#8217;" DoubleQuotes="&#8220;&#8221;" '
            'PrimaryLanguageName="$ID/English" SublanguageName="$ID/USA" '
            'Id="269" HyphenationVendor="Hunspell" SpellingVendor="Hunspell"/>\n',
            '<Layer Self="qxLayer1" Name="Layer 1" Visible="true" Locked="false" '
            'IgnoreWrap="false" ShowGuides="true" LockGuides="false" UI="true" '
            'Expendable="true" Printable="true">'
            "<Properties><LayerColor type=\"enumeration\">LightBlue</LayerColor>"
            "</Properties></Layer>\n",
            '<idPkg:Graphic src="Resources/Graphic.xml"/>\n',
            '<idPkg:Fonts src="Resources/Fonts.xml"/>\n',
            '<idPkg:Styles src="Resources/Styles.xml"/>\n',
            '<idPkg:Preferences src="Resources/Preferences.xml"/>\n',
            '<idPkg:Tags src="XML/Tags.xml"/>\n',
            '<idPkg:MasterSpread src="MasterSpreads/MasterSpread_{mid}.xml"/>\n'.format(
                mid=self.master_id
            ),
        ]
        for sp in self.spreads:
            parts.append(
                '<idPkg:Spread src="Spreads/Spread_{sid}.xml"/>\n'.format(sid=sp.spread_id)
            )
        parts.append('<idPkg:BackingStory src="XML/BackingStory.xml"/>\n')
        for sid, _ in self.stories:
            parts.append('<idPkg:Story src="Stories/Story_{sid}.xml"/>\n'.format(sid=sid))
        for n, (hts_id, url) in enumerate(self.hyperlinks, 1):
            u = quoteattr(url)
            parts.append(
                '<HyperlinkURLDestination Self="HyperlinkURLDestination/qxhld{n}" '
                "Name={u} DestinationURL={u} DestinationUniqueKey=\"{n}\"/>\n"
                '<Hyperlink Self="qxhl{n}" Name={u} Source="{src}" Visible="false" '
                'Highlight="None" Width="Thin" BorderStyle="Solid" Hidden="false" '
                'DestinationUniqueKey="{n}">'
                "<Properties>"
                '<BorderColor type="enumeration">Black</BorderColor>'
                '<Destination type="object">HyperlinkURLDestination/qxhld{n}</Destination>'
                "</Properties></Hyperlink>\n".format(n=n, u=u, src=hts_id)
            )
        parts.append("</Document>\n")
        return "".join(parts)

    def write(self, out_path):
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            # mimetype MUST be first and uncompressed
            z.writestr(
                zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED
            )
            z.writestr("designmap.xml", self.designmap())
            z.writestr("Resources/Graphic.xml", self.colors.graphic_xml())
            z.writestr("Resources/Fonts.xml", self.fonts.fonts_xml(self.idgen))
            z.writestr("Resources/Styles.xml", styles_xml())
            z.writestr(
                "Resources/Preferences.xml",
                preferences_xml(self.page_w, self.page_h, max(1, len(self.spreads))),
            )
            z.writestr("XML/Tags.xml", tags_xml())
            z.writestr("XML/BackingStory.xml", backing_story_xml())
            z.writestr(
                "MasterSpreads/MasterSpread_{}.xml".format(self.master_id),
                master_spread_xml(self.master_id, self.page_w, self.page_h),
            )
            for sp in self.spreads:
                z.writestr("Spreads/Spread_{}.xml".format(sp.spread_id), sp.xml())
            for sid, xml in self.stories:
                z.writestr("Stories/Story_{}.xml".format(sid), xml)
