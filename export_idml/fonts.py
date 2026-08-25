"""Font discovery: map (family, style) -> (PostScript name, file path).

IDML never embeds fonts (spec section 6.1: "InDesign documents do not
support font embedding, and fonts are not embedded in IDML" - fonts are
declared in Resources/Fonts.xml and resolved against installed fonts).
So all we need is:
  1. the correct family / style / PostScript names for Fonts.xml, and
  2. optionally copying the font files into a "Document fonts" folder
     next to the .idml so InDesign auto-adopts them.

Pure-python 'name' table parser - no fontTools dependency (QGIS bundles
are not guaranteed to ship it).  Handles TTF, OTF and TTC.
"""

import os
import struct

_NAME_IDS = {
    1: "family",
    2: "subfamily",
    6: "postscript",
    16: "typo_family",
    17: "typo_subfamily",
}


def _decode(platform_id, encoding_id, data):
    try:
        if platform_id == 3:  # Windows: UTF-16BE
            return data.decode("utf-16-be")
        if platform_id == 0:  # Unicode
            return data.decode("utf-16-be")
        if platform_id == 1:  # Macintosh: mostly mac-roman
            return data.decode("mac_roman", errors="replace")
    except Exception:
        pass
    return None


def _parse_name_table(f, table_offset):
    f.seek(table_offset)
    hdr = f.read(6)
    if len(hdr) < 6:
        return {}
    _fmt, count, string_offset = struct.unpack(">HHH", hdr)
    records = []
    for _ in range(count):
        rec = f.read(12)
        if len(rec) < 12:
            break
        records.append(struct.unpack(">HHHHHH", rec))
    out = {}
    prefer = {}  # name_id -> priority of stored value (lower wins)
    for pid, eid, lang, nid, length, offset in records:
        if nid not in _NAME_IDS:
            continue
        # priority: Windows en-US > Windows any > Unicode > Mac
        if pid == 3 and lang == 0x409:
            prio = 0
        elif pid == 3:
            prio = 1
        elif pid == 0:
            prio = 2
        else:
            prio = 3
        if nid in prefer and prefer[nid] <= prio:
            continue
        f.seek(table_offset + string_offset + offset)
        raw = f.read(length)
        val = _decode(pid, eid, raw)
        if val:
            out[_NAME_IDS[nid]] = val.strip("\x00").strip()
            prefer[nid] = prio
    return out


def _parse_sfnt(f, base_offset):
    f.seek(base_offset)
    hdr = f.read(12)
    if len(hdr) < 12:
        return None
    version, num_tables = struct.unpack(">IH", hdr[:6])
    if version not in (0x00010000, 0x4F54544F, 0x74727565):  # 1.0, 'OTTO', 'true'
        return None
    for i in range(num_tables):
        rec = f.read(16)
        if len(rec) < 16:
            return None
        tag, _checksum, offset, _length = struct.unpack(">4sIII", rec)
        if tag == b"name":
            return _parse_name_table(f, offset)
    return None


def read_font_names(path):
    """Return list of dicts for a font file.

    family/style are the *typographic* names (the ones InDesign shows);
    legacy_family/legacy_style are the 4-style GDI names Qt often reports
    (e.g. legacy "Futura PT Book"/"Regular" vs typo "Futura PT"/"Book")."""
    results = []
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            f.seek(0)
            if magic == b"ttcf":
                f.read(8)
                (num_fonts,) = struct.unpack(">I", f.read(4))
                offsets = struct.unpack(">%dI" % num_fonts, f.read(4 * num_fonts))
                for off in offsets:
                    names = _parse_sfnt(f, off)
                    if names:
                        results.append(names)
            else:
                names = _parse_sfnt(f, 0)
                if names:
                    results.append(names)
    except Exception:
        return []
    out = []
    for names in results:
        family = names.get("typo_family") or names.get("family")
        style = names.get("typo_subfamily") or names.get("subfamily") or "Regular"
        ps = names.get("postscript")
        if family:
            out.append(
                {
                    "family": family,
                    "style": style,
                    "postscript": ps,
                    "legacy_family": names.get("family") or family,
                    "legacy_style": names.get("subfamily") or "Regular",
                }
            )
    return out


_FONT_DIRS = None


def font_dirs():
    global _FONT_DIRS
    if _FONT_DIRS is None:
        dirs = []
        windir = os.environ.get("WINDIR", r"C:\Windows")
        dirs.append(os.path.join(windir, "Fonts"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
        roaming = os.environ.get("APPDATA")
        if roaming:
            # Adobe Fonts (Creative Cloud) activated fonts - extensionless files
            dirs.append(os.path.join(roaming, "Adobe", "CoreSync", "plugins", "livetype", "r"))
        _FONT_DIRS = [d for d in dirs if os.path.isdir(d)]
    return _FONT_DIRS


class FontIndex:
    """Lazy index of installed fonts.

    Keyed by BOTH typographic and legacy (GDI 4-style) name pairs, so a
    Qt-reported ("Futura PT Book", "Book"/"Regular") resolves to the same
    entry as the canonical ("Futura PT", "Book").  lookup() returns the
    entry whose family/style are the typographic names InDesign expects.
    """

    _EXTS = (".ttf", ".otf", ".ttc")

    def __init__(self):
        self._by_key = None

    def _want_file(self, dirpath, name):
        if name.lower().endswith(self._EXTS):
            return True
        # Adobe CoreSync livetype files are extensionless; sniff the magic
        if "livetype" in dirpath.lower():
            try:
                with open(os.path.join(dirpath, name), "rb") as f:
                    return f.read(4) in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf")
            except OSError:
                return False
        return False

    def _build(self):
        self._by_key = {}
        for d in font_dirs():
            try:
                entries = os.listdir(d)
            except OSError:
                continue
            for name in entries:
                if not self._want_file(d, name):
                    continue
                path = os.path.join(d, name)
                for info in read_font_names(path):
                    entry = dict(info, path=path)
                    keys = {
                        (info["family"].lower(), info["style"].lower()),
                        (info["legacy_family"].lower(), info["legacy_style"].lower()),
                        # Qt sometimes pairs the legacy family with the
                        # typographic style ("Futura PT Book" + "Book")
                        (info["legacy_family"].lower(), info["style"].lower()),
                    }
                    for key in keys:
                        # first hit wins (system dir listed first)
                        self._by_key.setdefault(key, entry)

    def _get(self, fam, sty):
        hit = self._by_key.get((fam, sty))
        if hit:
            return hit
        aliases = {
            "regular": ["normal", "book", "roman"],
            "bold italic": ["bold oblique"],
            "italic": ["oblique"],
            # weight neighbours for faces the family doesn't ship
            "thin": ["light", "extralight", "extra light", "ultralight"],
            "extralight": ["light", "thin"],
            "semibold": ["demi", "demibold", "medium"],
            "black": ["heavy", "extra bold", "extrabold"],
        }
        for alt in aliases.get(sty, []):
            hit = self._by_key.get((fam, alt))
            if hit:
                return hit
        if sty != "regular":
            return self._by_key.get((fam, "regular"))
        return None

    def lookup(self, family, style="Regular"):
        """Return {family, style, postscript, path, ...} or None."""
        if self._by_key is None:
            self._build()
        fam = (family or "").lower()
        sty = (style or "Regular").lower()
        hit = self._get(fam, sty)
        if hit:
            return hit
        # peel style words off the end of the family name:
        # "Futura PT Book" -> family "Futura PT", style "Book"
        words = fam.split()
        while len(words) > 1:
            tail = words.pop()
            fam2 = " ".join(words)
            for sty2 in (tail if sty in ("regular", tail) else sty + " " + tail,
                         tail, sty):
                hit = self._get(fam2, sty2)
                if hit:
                    return hit
        return None


def qfont_style_name(font):
    """Derive an InDesign-ish style name from a QFont."""
    style = font.styleName()
    if style:
        return style
    w = font.weight()
    # Qt6 uses OpenType weights (Bold=700); Qt5 uses 0-99 (Bold=75, DemiBold=63)
    bold = font.bold() or w >= 600 or (63 <= w <= 99)
    italic = font.italic()
    if bold and italic:
        return "Bold Italic"
    if bold:
        return "Bold"
    if italic:
        return "Italic"
    return "Regular"
