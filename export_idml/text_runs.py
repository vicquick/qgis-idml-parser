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

_ALIGN = {
    int(Qt.AlignmentFlag.AlignLeft): "LeftAlign",
    int(Qt.AlignmentFlag.AlignHCenter): "CenterAlign",
    int(Qt.AlignmentFlag.AlignRight): "RightAlign",
    int(Qt.AlignmentFlag.AlignJustify): "FullyJustified",
}


def _alignment_name(alignment, default="LeftAlign"):
    horiz = int(alignment) & int(Qt.AlignmentFlag.AlignHorizontal_Mask)
    return _ALIGN.get(horiz, default)


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

    return {
        "family": family,
        "style": qfont_style_name(font),
        "size_pt": float(size_pt),
        "color": color,
        "underline": char_format.fontUnderline(),
        "strikeout": char_format.fontStrikeOut(),
    }


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
        if int(bf.lineHeightType()) == 1 and bf.lineHeight() > 0:
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
            para["runs"].append(run)
        it += 1
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
