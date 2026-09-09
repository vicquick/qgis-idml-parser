"""merge_idml.py -- merge one IDML package's Spreads/Stories into another.

Reusable, stdlib-only merge tool for combining a QGIS-plugin-generated
"atlas" IDML snippet (all Self ids "qx"-prefixed -> collision-free) into a
real InDesign-exported "report" IDML package.

Both inputs are opened READ-ONLY and are never modified. A brand new
package is written to OUT.

What gets merged (see algorithm notes inline):
  - INSERT's Spreads/*.xml and Stories/*.xml are copied into OUT verbatim
    as new zip entries (qx-prefixed Self ids, so no collision with BASE).
  - INSERT's <Layer> elements are appended to BASE's designmap.xml Layer
    list (later Layer elements paint on top in InDesign).
  - New <idPkg:Spread>/<idPkg:Story> references are appended to BASE's
    designmap.xml, and BASE's Document@StoryList is extended.
  - INSERT's Color/Gradient/Swatch/Ink/StrokeStyle swatches are merged into
    BASE's Resources/Graphic.xml, skipping anything whose Self id already
    exists in BASE.
  - INSERT's FontFamily/Font entries are merged into BASE's
    Resources/Fonts.xml: a FontFamily with a Name BASE already has gets
    only the missing Font children (matched by PostScriptName) added; a
    wholly new family is appended whole.
  - Any Hyperlink*/HyperlinkURLDestination elements in INSERT's designmap
    are copied across too (best effort -- appended at the end of
    <Document>, since neither package this tool has been exercised against
    carries any and IDML does not document a stricter anchor point).
  - BASE's last <Section>'s Length (and AlternateLayoutLength) is bumped by
    however many new <Page> elements the copied Spreads carry, so
    Sum(Section.Length) == total document page count still holds after the
    merge (every genuine InDesign-authored IDML keeps that invariant; a
    mismatch is exactly the kind of defect that triggers InDesign's
    open-time repair pass). New Spread refs are always appended at the end
    of designmap.xml's idPkg:Spread list (see merge_spread_refs), so their
    pages land at the end of the document's page order -- extending the
    section that already reaches the old last page is correct. If BASE has
    no <Section> element at all, this is a no-op (nothing to extend).
  - AppliedMaster references in copied spreads that point at an INSERT-only
    MasterSpread (never copied, since it would only duplicate content BASE
    doesn't need) are rewritten to "n" (None).
  - DOMVersion on every copied Spread/Story part's idPkg root is bumped to
    match BASE's Document DOMVersion.
  - A per-part <?aid ... type="snippet" ...?> processing instruction (the
    signature of INSERT's parts being authored in InDesign's Snippet-export
    shape) is stripped from every copied Spread/Story part: real InDesign
    package parts never carry an <?aid?> PI at all (only designmap.xml
    does, with type="document"), so leaving a snippet-typed one in place
    would splice an InDesign-Snippet-shaped fragment into a full package.
  - LinkResourceURI paths in copied spreads can be rewritten with
    --rewrite-links OLD=NEW and/or redirected wholesale into a single
    folder with --links-dir NEWDIR; --fix-base-link OLDURI=NEWPATH patches
    a specific already-broken link living in one of BASE's own (untouched)
    spread files.

Deliberately NOT done (left to a human / a follow-up run), because the
task this tool was built for calls for leaving them alone:
  - No new <Section> is ever added for the inserted pages (only the last
    existing one is extended, see above) -- if the merged pages need their
    own independent numbering/prefix, split it off by hand in InDesign.
  - BASE's own Spread/MasterSpread PageCount attributes are left untouched.
  - INSERT's Resources/Preferences.xml, Styles.xml, MasterSpreads/*,
    META-INF/* and XML/* are never copied in (see docstring above).

CLI:
    python merge_idml.py BASE.idml INSERT.idml OUT.idml \
        [--links-dir "NEW LINKS DIR"] \
        [--rewrite-links "OLDDIR=NEWDIR"] ... \
        [--fix-base-link "OLDURI=NEWPATH"] ...

Validate the result afterwards with tests/validate_idml.py (a stricter,
InDesign-agnostic structural check lives there; this script only does the
sanity checks described in step 9 below).
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
import zipfile
from urllib.parse import quote, unquote

IDPKG_NS = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"
MIMETYPE_NAME = "mimetype"
MIMETYPE_CONTENT = b"application/vnd.adobe.indesign-idml-package"

SWATCH_TAGS = ["Color", "Gradient", "Swatch", "Ink", "StrokeStyle"]
HYPERLINK_TAG_RE = re.compile(r"^Hyperlink")


# --------------------------------------------------------------------------
# Small XML-by-text helpers.
#
# We deliberately do NOT round-trip designmap.xml / Graphic.xml / Fonts.xml
# through xml.etree serialization: that would risk losing the exact
# attribute order, the leading <?xml ...?>/<?aid ...?> processing
# instructions, and self-closing-tag spacing that real InDesign wrote.
# Instead we do targeted string surgery, which the task's own algorithm
# calls out as "ACCEPTABLE and often safer".
# --------------------------------------------------------------------------


def find_selfs(text: str, tag: str) -> set:
    """Every Self="..." value on a <tag ...> opening tag (any spacing/order)."""
    ids = set()
    for m in re.finditer(r"<%s\b" % re.escape(tag), text):
        gt = text.index(">", m.end())
        opening = text[m.start() : gt + 1]
        sm = re.search(r'\bSelf="([^"]*)"', opening)
        if sm:
            ids.add(sm.group(1))
    return ids


def extract_element_span(text: str, self_id: str):
    """Return (start, end, tag_name) for the element whose Self==self_id.

    Handles both self-closing (<Tag .../>) and container (<Tag ...>...</Tag>)
    forms, and does not assume Self is the first attribute. Assumes the tag
    does not nest a same-named child (true for every element type this
    script touches: Layer, FontFamily, Font, Color, Gradient, Swatch, Ink,
    StrokeStyle, Hyperlink*).
    """
    m = re.search(r"<([\w:.-]+)\b[^>]*\bSelf=\"%s\"" % re.escape(self_id), text)
    if not m:
        return None
    tag = m.group(1)
    start = m.start()
    open_end = text.index(">", m.end())
    if text[open_end - 1] == "/":
        return start, open_end + 1, tag
    close_tag = "</%s>" % tag
    close = text.index(close_tag, open_end)
    return start, close + len(close_tag), tag


def detect_indent(text: str, pos: int) -> str:
    """Indentation of the line that ends at (or contains) pos.

    pos is typically right after a sibling element's closing/self-closing
    tag (e.g. immediately after "</Layer>" or "... />"), i.e. still
    mid-line, not at column 0 -- so this reads back to the nearest "\n"
    and takes THAT line's leading whitespace. (An earlier version walked
    back an extra line, which for a container element like <Layer> landed
    on a *nested child's* deeper indentation instead of the Layer's own --
    e.g. produced "\t\t" for a new top-level <Layer> that should get "\t".)
    """
    before = text[:pos]
    nl = before.rfind("\n")
    line = before[nl + 1 :] if nl != -1 else before
    m = re.match(r"[ \t]*", line)
    ind = m.group(0) if m else ""
    return ind if ind else "\t"


def block_prefix(text: str, pos: int) -> str:
    """"\n" iff inserting at pos would otherwise glue onto the preceding
    character (pos isn't already at the start of a line).

    Every merge_* function below builds its inserted block as
    "indent + element + \\n" per item, which is only self-contained when
    pos sits at column 0. Several insertion points instead sit right after
    a previous sibling's closing tag (e.g. end of "</Layer>", end of an
    "<idPkg:Spread .../>") -- mid-line -- so without this prefix the new
    element's indent whitespace concatenates directly onto that tag with
    no line break at all.
    """
    return "" if pos == 0 or text[pos - 1] == "\n" else "\n"


def apply_insertions(text: str, insertions):
    """Apply a list of (pos, str_to_insert) tuples, highest pos first."""
    for pos, s in sorted(insertions, key=lambda t: t[0], reverse=True):
        text = text[:pos] + s + text[pos:]
    return text


def bump_dom_version(text: str, new_version: str) -> str:
    return re.sub(r'DOMVersion="[^"]*"', 'DOMVersion="%s"' % new_version, text, count=1)


# A real InDesign package never puts an <?aid ...?> processing instruction
# on an individual Spreads/*.xml or Stories/*.xml part -- only designmap.xml
# carries one, with type="document". The QGIS-plugin-generated "atlas" IDML
# this tool merges in writes its Spread/Story parts in InDesign's
# Snippet-export shape, so each one opens with an
# <?aid style="50" type="snippet" ...?> PI. That's a legitimate PI on a
# *.idms snippet file, but it has no business surviving inside a full
# package's part files once spliced in -- it's the fingerprint of a
# snippet fragment sitting where a native package part goes. Stripped here
# (not at the source: the atlas IDML is an input this tool must not touch).
AID_PI_RE = re.compile(r'[ \t]*<\?aid\b[^>]*\?>\r?\n?')


def strip_part_aid_pi(text: str):
    """Remove a snippet-style <?aid ...?> PI from a copied part's text.

    Returns (new_text, 1) if one was found and removed, else (text, 0).
    """
    return AID_PI_RE.subn("", text, count=1)


# --------------------------------------------------------------------------
# Link rewriting
# --------------------------------------------------------------------------


def _decode_file_uri(uri: str):
    if not uri.startswith("file:"):
        return None
    raw = uri[5:]
    raw = re.sub(r"^/{1,3}", "", raw)  # tolerate file:/// forms too
    return unquote(raw)


def _encode_file_uri(path: str) -> str:
    norm = path.replace("\\", "/")
    return "file:" + quote(norm, safe="/:")


def rewrite_link_uri(uri: str, rewrites, links_dir: str | None):
    """rewrites: list of (old_dir, new_dir) pairs, both plain filesystem paths."""
    path = _decode_file_uri(uri)
    if path is None:
        return uri, False
    norm_path = path.replace("\\", "/")

    for old, new in rewrites:
        old_norm = old.replace("\\", "/").rstrip("/")
        new_norm = new.replace("\\", "/").rstrip("/")
        if norm_path.lower() == old_norm.lower():
            return _encode_file_uri(new_norm), True
        prefix = old_norm.lower() + "/"
        if norm_path.lower().startswith(prefix):
            rest = norm_path[len(old_norm) :]
            return _encode_file_uri(new_norm + rest), True

    if links_dir:
        base_name = posixpath.basename(norm_path)
        new_dir = links_dir.replace("\\", "/").rstrip("/")
        candidate = new_dir + "/" + base_name
        if candidate.lower() != norm_path.lower():
            return _encode_file_uri(candidate), True

    return uri, False


def rewrite_links_in_text(text: str, rewrites, links_dir):
    count = 0

    def repl(m):
        nonlocal count
        new_uri, changed = rewrite_link_uri(m.group(1), rewrites, links_dir)
        if changed:
            count += 1
        return 'LinkResourceURI="%s"' % new_uri

    new_text = re.sub(r'LinkResourceURI="([^"]*)"', repl, text)
    return new_text, count


def fix_base_links_in_text(text: str, fixes):
    """fixes: list of (old_uri_or_path, new_path) pairs."""
    count = 0

    def repl(m):
        nonlocal count
        uri = m.group(1)
        path = _decode_file_uri(uri) or uri
        for old, new in fixes:
            old_is_uri = old.startswith("file:")
            match = (uri == old) if old_is_uri else (
                path.replace("\\", "/").lower() == old.replace("\\", "/").lower()
            )
            if match:
                count += 1
                return 'LinkResourceURI="%s"' % _encode_file_uri(new)
        return m.group(0)

    new_text = re.sub(r'LinkResourceURI="([^"]*)"', repl, text)
    return new_text, count


# --------------------------------------------------------------------------
# designmap.xml merge
# --------------------------------------------------------------------------


def merge_layers(base_dm: str, insert_dm: str):
    base_selfs = find_selfs(base_dm, "Layer")
    insert_selfs = find_selfs(insert_dm, "Layer")
    new_selfs = [s for s in insert_selfs if s not in base_selfs]
    if not new_selfs:
        return base_dm, []

    snippets = []
    for self_id in new_selfs:
        span = extract_element_span(insert_dm, self_id)
        if span:
            start, end, _ = span
            snippets.append(insert_dm[start:end])

    layer_matches = list(re.finditer(r"<Layer\b.*?</Layer>", base_dm, re.DOTALL))
    if layer_matches:
        pos = layer_matches[-1].end()
    else:
        m = re.search(r"<idPkg:MasterSpread\b", base_dm)
        pos = m.start() if m else base_dm.rfind("</Document>")

    indent = detect_indent(base_dm, pos)
    block = block_prefix(base_dm, pos) + "".join(indent + s + "\n" for s in snippets)
    return apply_insertions(base_dm, [(pos, block)]), new_selfs


def merge_spread_refs(base_dm: str, spread_srcs):
    if not spread_srcs:
        return base_dm
    matches = list(re.finditer(r'<idPkg:Spread\s+src="[^"]*"\s*/>', base_dm))
    if matches:
        pos = matches[-1].end()
    else:
        m = re.search(r"<Section\b|<DocumentUser\b", base_dm)
        pos = m.start() if m else base_dm.rfind("</Document>")
    indent = detect_indent(base_dm, pos)
    block = block_prefix(base_dm, pos) + "".join(
        '%s<idPkg:Spread src="%s" />\n' % (indent, src) for src in spread_srcs
    )
    return apply_insertions(base_dm, [(pos, block)])


def merge_story_refs(base_dm: str, story_srcs):
    if not story_srcs:
        return base_dm
    matches = list(re.finditer(r'<idPkg:Story\s+src="[^"]*"\s*/>', base_dm))
    if matches:
        pos = matches[-1].end()
    else:
        m = re.search(r"<IndexingSortOption\b", base_dm)
        pos = m.start() if m else base_dm.rfind("</Document>")
    indent = detect_indent(base_dm, pos)
    block = block_prefix(base_dm, pos) + "".join(
        '%s<idPkg:Story src="%s" />\n' % (indent, src) for src in story_srcs
    )
    return apply_insertions(base_dm, [(pos, block)])


def extend_story_list(base_dm: str, new_story_ids):
    if not new_story_ids:
        return base_dm

    def repl(m):
        return 'StoryList="%s %s"' % (m.group(1), " ".join(new_story_ids))

    new_dm, n = re.subn(r'StoryList="([^"]*)"', repl, base_dm, count=1)
    if n == 0:
        raise ValueError("Document@StoryList attribute not found in base designmap.xml")
    return new_dm


def bump_last_section_length(base_dm: str, added_pages: int) -> str:
    """Extend BASE's last <Section> by added_pages, keeping the IDML
    invariant Sum(Section.Length) == total document page count.

    New Spread refs are always appended at the end of the idPkg:Spread list
    (merge_spread_refs), so the pages they carry land at the end of the
    document's page order -- the section that already reaches the old last
    page is the right one to extend, not a brand-new section (that would
    only be correct if the new pages wanted independent numbering).
    """
    if added_pages <= 0:
        return base_dm
    matches = list(re.finditer(r"<Section\b", base_dm))
    if not matches:
        # No Section element at all in this document -- nothing to bump;
        # leave the (unusual) absence of sectioning as-is.
        return base_dm
    m = matches[-1]
    gt = base_dm.index(">", m.end())
    opening = base_dm[m.start() : gt + 1]

    def bump(seg: str, attr: str) -> str:
        am = re.search(r'\b%s="(\d+)"' % attr, seg)
        if not am:
            return seg
        new_val = str(int(am.group(1)) + added_pages)
        return seg[: am.start(1)] + new_val + seg[am.end(1) :]

    new_opening = bump(opening, "Length")
    new_opening = bump(new_opening, "AlternateLayoutLength")
    return base_dm[: m.start()] + new_opening + base_dm[gt + 1 :]


def merge_hyperlinks(base_dm: str, insert_dm: str):
    """Best-effort copy of Hyperlink*/HyperlinkURLDestination elements.

    Placement is a pragmatic fallback (end of <Document>) since neither of
    the packages this tool was written against carries any hyperlink
    elements at all -- there is no observed anchor point to imitate.
    """
    base_selfs = set()
    for tag_m in re.finditer(r"<(Hyperlink[\w]*)\b", base_dm):
        base_selfs |= find_selfs(base_dm, tag_m.group(1))

    seen_tags = set()
    added = []
    snippets = []
    for tag_m in re.finditer(r"<(Hyperlink[\w]*)\b", insert_dm):
        tag = tag_m.group(1)
        if tag in seen_tags:
            continue
        seen_tags.add(tag)
        for self_id in find_selfs(insert_dm, tag):
            if self_id in base_selfs:
                continue
            span = extract_element_span(insert_dm, self_id)
            if not span:
                continue
            start, end, _ = span
            snippets.append(insert_dm[start:end])
            added.append(self_id)
            base_selfs.add(self_id)

    if not snippets:
        return base_dm, added

    pos = base_dm.rfind("</Document>")
    indent = detect_indent(base_dm, pos)
    block = block_prefix(base_dm, pos) + "".join(indent + s + "\n" for s in snippets)
    return apply_insertions(base_dm, [(pos, block)]), added


# --------------------------------------------------------------------------
# Resources/Graphic.xml merge
# --------------------------------------------------------------------------


def merge_swatches(base_graphic: str, insert_graphic: str):
    base_selfs = set()
    for tag in SWATCH_TAGS:
        base_selfs |= find_selfs(base_graphic, tag)

    to_add = []
    added = []
    for tag in SWATCH_TAGS:
        for self_id in find_selfs(insert_graphic, tag):
            if self_id in base_selfs:
                continue
            span = extract_element_span(insert_graphic, self_id)
            if not span:
                continue
            start, end, _ = span
            to_add.append(insert_graphic[start:end])
            added.append(self_id)
            base_selfs.add(self_id)

    if not to_add:
        return base_graphic, added

    pos = base_graphic.rfind("</idPkg:Graphic>")
    if pos == -1:
        raise ValueError("</idPkg:Graphic> not found in base Resources/Graphic.xml")
    indent = detect_indent(base_graphic, pos)
    block = block_prefix(base_graphic, pos) + "".join(indent + s + "\n" for s in to_add)
    return apply_insertions(base_graphic, [(pos, block)]), added


# --------------------------------------------------------------------------
# Resources/Fonts.xml merge
# --------------------------------------------------------------------------


def merge_fonts(base_fonts: str, insert_fonts: str):
    base_family_by_name = {}
    for m in re.finditer(
        r'<FontFamily\b[^>]*\bSelf="([^"]*)"[^>]*\bName="([^"]*)"', base_fonts
    ):
        self_id, name = m.group(1), m.group(2)
        span = extract_element_span(base_fonts, self_id)
        if span:
            base_family_by_name[name] = span  # (start, end, tag)

    insertions = []
    added_families = []
    added_fonts = []  # (family_name, postscript_name_or_None)

    fonts_root_end = base_fonts.rfind("</idPkg:Fonts>")
    if fonts_root_end == -1:
        raise ValueError("</idPkg:Fonts> not found in base Resources/Fonts.xml")

    seen_insert_families = set()
    for m in re.finditer(
        r'<FontFamily\b[^>]*\bSelf="([^"]*)"[^>]*\bName="([^"]*)"', insert_fonts
    ):
        ins_self, ins_name = m.group(1), m.group(2)
        if ins_self in seen_insert_families:
            continue
        seen_insert_families.add(ins_self)
        ins_span = extract_element_span(insert_fonts, ins_self)
        if ins_span is None:
            continue
        i_start, i_end, _ = ins_span

        if ins_name in base_family_by_name:
            b_start, b_end, b_tag = base_family_by_name[ins_name]
            base_block = base_fonts[b_start:b_end]
            existing_psn = set(re.findall(r'\bPostScriptName="([^"]*)"', base_block))

            fonts_to_add = []
            for fm in re.finditer(
                r'<Font\b[^>]*\bSelf="([^"]*)"', insert_fonts[i_start:i_end]
            ):
                f_self = fm.group(1)
                f_span = extract_element_span(insert_fonts, f_self)
                if not f_span:
                    continue
                fs, fe, _ = f_span
                font_xml = insert_fonts[fs:fe]
                psn_m = re.search(r'\bPostScriptName="([^"]*)"', font_xml)
                psn = psn_m.group(1) if psn_m else None
                if psn and psn in existing_psn:
                    continue
                fonts_to_add.append(font_xml)
                added_fonts.append((ins_name, psn))
                if psn:
                    existing_psn.add(psn)

            if fonts_to_add:
                close_tag = "</%s>" % b_tag
                insert_pos = b_end - len(close_tag)
                indent = detect_indent(base_fonts, insert_pos)
                block = block_prefix(base_fonts, insert_pos) + "".join(
                    indent + s + "\n" for s in fonts_to_add
                )
                insertions.append((insert_pos, block))
        else:
            added_families.append(ins_name)
            indent = detect_indent(base_fonts, fonts_root_end)
            block = (
                block_prefix(base_fonts, fonts_root_end)
                + indent + insert_fonts[i_start:i_end] + "\n"
            )
            insertions.append((fonts_root_end, block))

    if not insertions:
        return base_fonts, added_families, added_fonts
    return apply_insertions(base_fonts, insertions), added_families, added_fonts


# --------------------------------------------------------------------------
# Main merge driver
# --------------------------------------------------------------------------


def _split_kv(spec: str, flag: str):
    if "=" not in spec:
        raise argparse.ArgumentTypeError(
            '%s expects OLD=NEW, got: %r' % (flag, spec)
        )
    old, new = spec.split("=", 1)
    return old, new


def merge(base_path, insert_path, out_path, rewrite_links=None, links_dir=None,
          fix_base_link=None, verbose=True):
    rewrite_links = rewrite_links or []
    fix_base_link = fix_base_link or []

    bz = zipfile.ZipFile(base_path, "r")
    iz = zipfile.ZipFile(insert_path, "r")

    base_infos = bz.infolist()
    if not base_infos or base_infos[0].filename != MIMETYPE_NAME:
        raise ValueError("base package does not start with a 'mimetype' entry")

    base_names = set(bz.namelist())
    insert_names = set(iz.namelist())

    base_dm = bz.read("designmap.xml").decode("utf-8")
    insert_dm = iz.read("designmap.xml").decode("utf-8")

    dom_m = re.search(r"<Document\b[^>]*\bDOMVersion=\"([^\"]+)\"", base_dm)
    if not dom_m:
        raise ValueError("base designmap.xml <Document> has no DOMVersion")
    base_dom_version = dom_m.group(1)

    # ---- which parts to copy across --------------------------------------
    spread_names = sorted(
        n for n in insert_names if n.startswith("Spreads/") and n.endswith(".xml")
    )
    story_names = sorted(
        n for n in insert_names if n.startswith("Stories/") and n.endswith(".xml")
    )

    # ---- insert-only MasterSpread Self ids (so AppliedMaster refs to them
    #      inside copied spreads, which are otherwise dangling, get fixed) --
    insert_master_selfs = set()
    for m in re.finditer(r'<idPkg:MasterSpread\s+src="([^"]*)"', insert_dm):
        ms_name = m.group(1)
        if ms_name in insert_names:
            ms_text = iz.read(ms_name).decode("utf-8")
            insert_master_selfs |= find_selfs(ms_text, "MasterSpread")

    # ---- new Spread/Story src references + story ids for StoryList -------
    spread_srcs = list(spread_names)
    story_srcs = list(story_names)
    story_ids = []
    for name in story_names:
        text = iz.read(name).decode("utf-8")
        m = re.search(r'<Story\b[^>]*\bSelf="([^"]*)"', text)
        if m:
            story_ids.append(m.group(1))

    # ---- designmap.xml edits ----------------------------------------------
    new_dm, added_layers = merge_layers(base_dm, insert_dm)
    new_dm = merge_spread_refs(new_dm, spread_srcs)
    new_dm = merge_story_refs(new_dm, story_srcs)
    new_dm = extend_story_list(new_dm, story_ids)
    new_dm, added_hyperlinks = merge_hyperlinks(new_dm, insert_dm)

    # ---- Resources/Graphic.xml ---------------------------------------------
    base_graphic = bz.read("Resources/Graphic.xml").decode("utf-8")
    insert_graphic = iz.read("Resources/Graphic.xml").decode("utf-8")
    new_graphic, added_swatches = merge_swatches(base_graphic, insert_graphic)

    # ---- Resources/Fonts.xml -----------------------------------------------
    base_fonts = bz.read("Resources/Fonts.xml").decode("utf-8")
    insert_fonts = iz.read("Resources/Fonts.xml").decode("utf-8")
    new_fonts, added_families, added_fonts = merge_fonts(base_fonts, insert_fonts)

    # ---- copied Spreads: AppliedMaster fix-up, DOMVersion bump, link
    #      rewriting -----------------------------------------------------
    edited_spread_bytes = {}
    total_links_rewritten = 0
    total_pages_added = 0
    total_aid_pi_stripped = 0
    for name in spread_names:
        text = iz.read(name).decode("utf-8")
        total_pages_added += len(find_selfs(text, "Page"))
        text, n_aid = strip_part_aid_pi(text)
        total_aid_pi_stripped += n_aid
        for master_self in insert_master_selfs:
            text = text.replace(
                'AppliedMaster="%s"' % master_self, 'AppliedMaster="n"'
            )
        text = bump_dom_version(text, base_dom_version)
        text, n = rewrite_links_in_text(text, rewrite_links, links_dir)
        total_links_rewritten += n
        edited_spread_bytes[name] = text.encode("utf-8")

    new_dm = bump_last_section_length(new_dm, total_pages_added)

    edited_story_bytes = {}
    for name in story_names:
        text = iz.read(name).decode("utf-8")
        text, n_aid = strip_part_aid_pi(text)
        total_aid_pi_stripped += n_aid
        text = bump_dom_version(text, base_dom_version)
        edited_story_bytes[name] = text.encode("utf-8")

    # ---- --fix-base-link: patch links inside BASE's own (untouched) spread
    #      files, only if a fix actually targets that file -----------------
    base_spread_names = sorted(
        n for n in base_names if n.startswith("Spreads/") and n.endswith(".xml")
    )
    edited_base_spread_bytes = {}
    total_base_links_fixed = 0
    if fix_base_link:
        for name in base_spread_names:
            text = bz.read(name).decode("utf-8")
            new_text, n = fix_base_links_in_text(text, fix_base_link)
            if n:
                edited_base_spread_bytes[name] = new_text.encode("utf-8")
                total_base_links_fixed += n

    # ---- assemble output zip -----------------------------------------------
    edited_texts = {
        "designmap.xml": new_dm.encode("utf-8"),
        "Resources/Graphic.xml": new_graphic.encode("utf-8"),
        "Resources/Fonts.xml": new_fonts.encode("utf-8"),
    }
    edited_texts.update(edited_base_spread_bytes)

    out = zipfile.ZipFile(out_path, "w")
    try:
        for info in base_infos:
            data = edited_texts.get(info.filename)
            if data is None:
                data = bz.read(info.filename)
            out.writestr(info, data)

        for name in spread_names:
            src_info = iz.getinfo(name)
            zi = zipfile.ZipInfo(name, date_time=src_info.date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(zi, edited_spread_bytes[name])

        for name in story_names:
            src_info = iz.getinfo(name)
            zi = zipfile.ZipInfo(name, date_time=src_info.date_time)
            zi.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(zi, edited_story_bytes[name])
    finally:
        out.close()
        bz.close()
        iz.close()

    summary = {
        "spreads_added": len(spread_names),
        "stories_added": len(story_names),
        "pages_added": total_pages_added,
        "layers_added": added_layers,
        "swatches_added": added_swatches,
        "font_families_added": added_families,
        "fonts_added": added_fonts,
        "hyperlinks_added": added_hyperlinks,
        "links_rewritten": total_links_rewritten,
        "base_links_fixed": total_base_links_fixed,
        "aid_pi_stripped": total_aid_pi_stripped,
    }

    if verbose:
        print("Merged '%s' + '%s' -> '%s'" % (base_path, insert_path, out_path))
        print("  spreads added:        %d (%s)" % (
            len(spread_names), ", ".join(spread_names) or "-"))
        print("  stories added:        %d" % len(story_names))
        print("  pages added:          %d (Section.Length bumped to match)" % total_pages_added)
        print("  layers added:         %d (%s)" % (
            len(added_layers), ", ".join(added_layers) or "-"))
        print("  swatches added:       %d (%s)" % (
            len(added_swatches), ", ".join(added_swatches) or "-"))
        print("  font families added:  %d (%s)" % (
            len(added_families), ", ".join(added_families) or "-"))
        print("  fonts added:          %d (%s)" % (
            len(added_fonts),
            ", ".join("%s/%s" % (fam, psn) for fam, psn in added_fonts) or "-",
        ))
        print("  hyperlinks added:     %d" % len(added_hyperlinks))
        print("  links rewritten:      %d" % total_links_rewritten)
        print("  base links fixed:     %d" % total_base_links_fixed)
        print("  snippet <?aid?> PIs stripped from copied parts: %d" % total_aid_pi_stripped)

    return summary


def _validate(out_path):
    """Lightweight step-9 sanity pass (not a substitute for
    tests/validate_idml.py, which this tool's caller should also run)."""
    errors = []
    z = zipfile.ZipFile(out_path, "r")
    infos = z.infolist()
    if not infos or infos[0].filename != MIMETYPE_NAME:
        errors.append("mimetype is not the first zip entry")
    elif infos[0].compress_type != zipfile.ZIP_STORED:
        errors.append("mimetype entry is not stored uncompressed")
    elif z.read(MIMETYPE_NAME) != MIMETYPE_CONTENT:
        errors.append("mimetype content is wrong")

    names = set(z.namelist())
    import xml.etree.ElementTree as ET

    try:
        dm = ET.fromstring(z.read("designmap.xml"))
    except ET.ParseError as e:
        errors.append("designmap.xml does not parse: %s" % e)
        z.close()
        return errors

    # A real InDesign package's Document@StoryList also carries the Self id
    # of the document's root/backing story (its XML-tagging structure story),
    # which idPkg:BackingStory packages at XML/BackingStory.xml -- outside
    # Stories/ -- rather than as a Stories/Story_<id>.xml part. That id is a
    # legitimate StoryList entry with no Stories/*.xml counterpart, so it must
    # be resolved (from the BackingStory part's own <XmlStory Self="..."> --
    # never assumed to be a fixed literal like "ub0", since a different base
    # document would mint a different id) and excluded from the "every
    # StoryList id needs a Stories/*.xml part" check below, instead of that
    # check silently accepting every id unchecked.
    backing_story_ids = set()
    for el in dm.iter():
        src = el.attrib.get("src")
        if src and src not in names:
            errors.append("designmap.xml references missing part: %s" % src)
        elif src and el.tag == "{%s}BackingStory" % IDPKG_NS:
            try:
                backing_root = ET.fromstring(z.read(src))
            except ET.ParseError as e:
                errors.append("%s does not parse: %s" % (src, e))
            else:
                for xs in backing_root.iter("XmlStory"):
                    sid = xs.attrib.get("Self")
                    if sid:
                        backing_story_ids.add(sid)

    story_ids = set()
    for name in names:
        if name.startswith("Stories/") and name.endswith(".xml"):
            try:
                root = ET.fromstring(z.read(name))
            except ET.ParseError as e:
                errors.append("%s does not parse: %s" % (name, e))
                continue
            for st in root.iter("Story"):
                sid = st.attrib.get("Self")
                if sid:
                    story_ids.add(sid)

    story_list = dm.attrib.get("StoryList", "").split()
    for sid in story_list:
        if sid in story_ids or sid in backing_story_ids:
            continue
        errors.append(
            "Document@StoryList id %r has no Stories/*.xml part and is not "
            "the BackingStory's XmlStory id" % sid
        )

    for name in names:
        if name.startswith("Spreads/") and name.endswith(".xml"):
            try:
                root = ET.fromstring(z.read(name))
            except ET.ParseError as e:
                errors.append("%s does not parse: %s" % (name, e))
                continue
            for tf in root.iter("TextFrame"):
                ps = tf.attrib.get("ParentStory")
                if ps and ps not in story_ids:
                    errors.append(
                        "%s: TextFrame %s ParentStory %s has no Story part"
                        % (name, tf.attrib.get("Self"), ps)
                    )

    total_pages = 0
    for name in names:
        if name.startswith("Spreads/") and name.endswith(".xml"):
            total_pages += len(find_selfs(z.read(name).decode("utf-8"), "Page"))
    section_dm_text = z.read("designmap.xml").decode("utf-8")
    section_lengths = [
        int(m.group(1))
        for m in re.finditer(r'<Section\b[^>]*\bLength="(\d+)"', section_dm_text)
    ]
    if section_lengths and sum(section_lengths) != total_pages:
        errors.append(
            "Sum(Section.Length)=%d does not match total page count=%d "
            "(every Spread's <Page> elements must be covered by a Section)"
            % (sum(section_lengths), total_pages)
        )

    for tag in ("Layer", "Color", "Gradient", "Swatch", "Ink", "StrokeStyle",
                "FontFamily"):
        seen = set()
        for name in ("designmap.xml", "Resources/Graphic.xml", "Resources/Fonts.xml"):
            if name not in names:
                continue
            text = z.read(name).decode("utf-8")
            for sid in find_selfs(text, tag):
                if sid in seen:
                    errors.append("duplicate Self=%r for <%s> in %s" % (sid, tag, name))
                seen.add(sid)

    z.close()
    return errors


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Merge an INSERT IDML's Spreads/Stories into a BASE IDML."
    )
    p.add_argument("base", help="path to the base .idml (untouched)")
    p.add_argument("insert", help="path to the .idml to merge in (untouched)")
    p.add_argument("out", help="path to write the merged .idml to")
    p.add_argument(
        "--links-dir",
        default=None,
        help="redirect every copied LinkResourceURI to this folder (keeps filename)",
    )
    p.add_argument(
        "--rewrite-links",
        action="append",
        default=[],
        metavar="OLDDIR=NEWDIR",
        help="rewrite copied LinkResourceURI paths under OLDDIR to NEWDIR "
        "(repeatable)",
    )
    p.add_argument(
        "--fix-base-link",
        action="append",
        default=[],
        metavar="OLDURI=NEWPATH",
        help="patch one already-broken LinkResourceURI in BASE's own spreads "
        "(repeatable)",
    )
    p.add_argument(
        "--no-validate",
        action="store_true",
        help="skip the built-in post-merge sanity pass",
    )
    args = p.parse_args(argv)

    rewrite_links = [_split_kv(s, "--rewrite-links") for s in args.rewrite_links]
    fix_base_link = [_split_kv(s, "--fix-base-link") for s in args.fix_base_link]

    merge(
        args.base,
        args.insert,
        args.out,
        rewrite_links=rewrite_links,
        links_dir=args.links_dir,
        fix_base_link=fix_base_link,
    )

    if not args.no_validate:
        errors = _validate(args.out)
        if errors:
            print("VALIDATION ERRORS:")
            for e in errors:
                print("  -", e)
            sys.exit(1)
        print("Built-in sanity check: OK")


if __name__ == "__main__":
    main()
