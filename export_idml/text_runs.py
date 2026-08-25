"""Resolve rich text (QGIS HTML-mode labels) into styled structure.

Walks the QTextDocument frame tree so TABLES survive: a <table> with two
<td> columns (the Steckbriefe Spielelemente pattern) is returned as a
table entry with per-column paragraph lists + width fractions, which the
exporter turns into side-by-side native TextFrames.

Paragraph entries carry block metrics (hanging indent, space after,
proportional line height) so InDesign reproduces the CSS look:

    {"type": "para", "align": ..., "runs": [...],
     "left_indent_pt": float, "first_line_indent_pt": float,
     "space_after_pt": float, "line_height_pct": float-or-None}

    {"type": "table", "columns": [[para, ...], ...],
     "col_fractions": [0.5, 0.5], "cell_padding_pt": float}

Qt rich text uses 96 dpi px -> pt = px * 72/96.
"""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QTextDocument, QTextLength

from .fonts import qfont_style_name

PX2PT = 72.0 / 96.0


def enum_int(v):
    """int value of a Qt enum across PyQt5 (sip int) and PyQt6 (enum.Enum)."""
    try:
        return int(v)
    except TypeError:
        return int(v.value)

_ALIGN = {
    int(Qt.AlignmentFlag.AlignLeft): "LeftAlign",
    int(Qt.AlignmentFlag.AlignHCenter): "CenterAlign",
    int(Qt.AlignmentFlag.AlignRight): "RightAlign",
    int(Qt.AlignmentFlag.AlignJustify): "FullyJustified",
}


def _alignment_name(alignment, default="LeftAlign"):
    horiz = int(alignment) & int(Qt.AlignmentFlag.AlignHorizontal_Mask)
    return _ALIGN.get(horiz, default)


def annotate_run_from_font(run, font, size_pt):
    """Capitalization / letter spacing / word spacing from a QFont.

    Sets: caps ("AllCaps"/"SmallCaps"), lower/title (text transforms the
    caller applies), tracking (1/1000 em), word_spacing_pt."""
    try:
        caps = enum_int(font.capitalization())
        # QFont: MixedCase 0, AllUppercase 1, AllLowercase 2,
        #        SmallCaps 3, Capitalize 4
        if caps == 1:
            run["caps"] = "AllCaps"
        elif caps == 3:
            run["caps"] = "SmallCaps"
        elif caps == 2:
            run["lower"] = True
        elif caps == 4:
            run["title"] = True
    except Exception:
        pass
    try:
        ls = font.letterSpacing()
        lst = enum_int(font.letterSpacingType())
        # QFont.SpacingType: PercentageSpacing 0, AbsoluteSpacing 1
        if lst == 0 and ls and abs(ls - 100.0) > 0.01:
            run["tracking"] = int(round((ls - 100.0) * 10.0))  # % of em -> 1/1000 em
        elif lst == 1 and ls:
            px_pt = ls * PX2PT
            if size_pt:
                run["tracking"] = int(round(px_pt / size_pt * 1000.0))
    except Exception:
        pass
    try:
        ws = font.wordSpacing()
        if ws:
            run["word_spacing_pt"] = ws * PX2PT
    except Exception:
        pass
    return run


def apply_case_transforms(run):
    """Apply lower/title text transforms flagged by annotate_run_from_font."""
    if run.pop("lower", False):
        run["text"] = run["text"].lower()
    if run.pop("title", False):
        run["text"] = run["text"].title()
    return run


def _run_from_format(char_format, default_font, default_size_pt, default_color):
    font = char_format.font()
    family = font.family() or default_font.family()

    size_pt = char_format.fontPointSize()
    if not size_pt:
        size_pt = font.pointSizeF()
    if not size_pt or size_pt <= 0:
        px = font.pixelSize()
        if px and px > 0:
            size_pt = px * PX2PT
        else:
            size_pt = default_size_pt

    brush = char_format.foreground()
    color = brush.color() if brush.style() != Qt.BrushStyle.NoBrush else default_color

    run = {
        "family": family,
        "style": qfont_style_name(font),
        "size_pt": float(size_pt),
        "color": color,
        "underline": char_format.fontUnderline(),
        "strikeout": char_format.fontStrikeOut(),
    }
    annotate_run_from_font(run, font, size_pt)
    try:
        va = enum_int(char_format.verticalAlignment())
        # QTextCharFormat: AlignSuperScript 1, AlignSubScript 2
        if va == 1:
            run["position"] = "Superscript"
        elif va == 2:
            run["position"] = "Subscript"
    except Exception:
        pass
    return run


def _para_from_block(block, default_font, default_size_pt, default_color):
    bf = block.blockFormat()
    para = {
        "type": "para",
        "align": _alignment_name(bf.alignment()),
        "runs": [],
        "left_indent_pt": bf.leftMargin() * PX2PT,
        "first_line_indent_pt": bf.textIndent() * PX2PT,
        "space_after_pt": bf.bottomMargin() * PX2PT,
        "line_height_pct": None,
    }
    try:
        # 1 == QTextBlockFormat.ProportionalHeight
        if enum_int(bf.lineHeightType()) == 1 and bf.lineHeight() > 0:
            para["line_height_pct"] = bf.lineHeight()
    except Exception:
        pass
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid() and frag.text():
            run = _run_from_format(
                frag.charFormat(), default_font, default_size_pt, default_color
            )
            # Qt encodes <br/> inside a block as U+2028 line separator
            run["text"] = frag.text().replace(chr(0x2028), "\n")
            apply_case_transforms(run)
            para["runs"].append(run)
        it += 1

    # <ul>/<ol> markers live on the block's QTextList and never appear in
    # fragment text - synthesize them as a literal leading run + hanging
    # indent so bullets/numbers survive
    try:
        tl = block.textList()
    except Exception:
        tl = None
    if tl is not None and para["runs"]:
        style = enum_int(tl.format().style())
        markers = {-1: "•", -2: "◦", -3: "▪"}
        if style in markers:
            marker = markers[style] + " "
        else:
            marker = (tl.itemText(block) or "•") + " "
        first = dict(para["runs"][0])
        first["text"] = marker
        para["runs"].insert(0, first)
        ind = max(1, tl.format().indent())
        if not para["left_indent_pt"]:
            para["left_indent_pt"] = 12.0 * ind
        if not para["first_line_indent_pt"]:
            para["first_line_indent_pt"] = -12.0
    return para


def _paras_in_cell(cell, default_font, default_size_pt, default_color):
    # PyQt exposes no QTextTableCell.begin(); walk blocks by position
    paras = []
    block = cell.firstCursorPosition().block()
    end_pos = cell.lastCursorPosition().position()
    while block.isValid() and block.position() <= end_pos:
        paras.append(
            _para_from_block(block, default_font, default_size_pt, default_color)
        )
        block = block.next()
    while paras and not paras[-1]["runs"]:
        paras.pop()
    return paras


def _table_entry(table, default_font, default_size_pt, default_color):
    fmt = table.format()
    n_cols = table.columns()
    fractions = [1.0 / n_cols] * n_cols
    try:
        constraints = fmt.columnWidthConstraints()
        try:
            pct_type = QTextLength.Type.PercentageLength
        except AttributeError:  # Qt5 spelling
            pct_type = QTextLength.PercentageLength
        pct = [
            c.rawValue() if c.type() == pct_type else None for c in constraints
        ]
    except Exception:
        pct = []
    if pct and all(p is not None for p in pct) and sum(pct) > 0:
        total = sum(pct)
        fractions = [p / total for p in pct]
    columns = []
    for col in range(n_cols):
        paras = []
        seen_cells = set()
        for row in range(table.rows()):
            cell = table.cellAt(row, col)
            key = (cell.row(), cell.column())
            if key in seen_cells:
                continue
            seen_cells.add(key)
            paras.extend(
                _paras_in_cell(cell, default_font, default_size_pt, default_color)
            )
        columns.append(paras)
    return {
        "type": "table",
        "columns": columns,
        "col_fractions": fractions,
        "cell_padding_pt": max(0.0, fmt.cellPadding() * PX2PT),
    }


def _walk_frame(frame, default_font, default_size_pt, default_color, out):
    from qgis.PyQt.QtGui import QTextTable

    it = frame.begin()
    while not it.atEnd():
        child = it.currentFrame()
        if child is not None:
            if isinstance(child, QTextTable):
                out.append(
                    _table_entry(child, default_font, default_size_pt, default_color)
                )
            else:
                _walk_frame(child, default_font, default_size_pt, default_color, out)
        else:
            block = it.currentBlock()
            if block.isValid():
                out.append(
                    _para_from_block(
                        block, default_font, default_size_pt, default_color
                    )
                )
        it += 1
    return out


def extract_structure(html, default_font, default_size_pt, default_color):
    """Full structure: list of para / table entries."""
    doc = QTextDocument()
    doc.setDefaultFont(default_font)
    doc.setHtml(html)
    out = []
    _walk_frame(doc.rootFrame(), default_font, default_size_pt, default_color, out)
    # trim trailing empty paragraphs Qt likes to append
    while out and out[-1]["type"] == "para" and not out[-1]["runs"]:
        out.pop()
    return out


def extract_runs(html, default_font, default_size_pt, default_color):
    """Flattened paragraphs (tables collapsed column-wise) - legacy shape."""
    flat = []
    for entry in extract_structure(html, default_font, default_size_pt, default_color):
        if entry["type"] == "table":
            for col in entry["columns"]:
                flat.extend(col)
        else:
            flat.append(entry)
    return flat


def plain_text_paragraphs(text, font, size_pt, color, align="LeftAlign"):
    """Plain (font-mode) label text -> same paragraph structure."""
    style = qfont_style_name(font)
    paragraphs = []
    for line_group in text.split("\n\n"):
        paragraphs.append(
            {
                "type": "para",
                "align": align,
                "runs": [
                    {
                        "text": line_group,
                        "family": font.family(),
                        "style": style,
                        "size_pt": float(size_pt),
                        "color": color,
                        "underline": font.underline(),
                        "strikeout": font.strikeOut(),
                    }
                ],
                "left_indent_pt": 0.0,
                "first_line_indent_pt": 0.0,
                "space_after_pt": 0.0,
                "line_height_pct": None,
            }
        )
    if not paragraphs:
        paragraphs = [
            {
                "type": "para",
                "align": align,
                "runs": [],
                "left_indent_pt": 0.0,
                "first_line_indent_pt": 0.0,
                "space_after_pt": 0.0,
                "line_height_pct": None,
            }
        ]
    return paragraphs
