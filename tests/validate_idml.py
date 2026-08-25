"""Structural validator for generated .idml packages (no InDesign needed).

Checks:
  - zip opens, mimetype is first entry, stored uncompressed, right value
  - every XML part parses
  - every designmap idPkg src exists in the package
  - every TextFrame ParentStory has a matching Story part
  - every Link LinkResourceURI resolves to an existing file
  - PathPointArrays have >= 2 points
Usage: python validate_idml.py file.idml
"""

import re
import sys
import zipfile
import xml.etree.ElementTree as ET

IDPKG = "{http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging}"
MIMETYPE = "application/vnd.adobe.indesign-idml-package"


def validate(path):
    errors = []
    warnings = []
    z = zipfile.ZipFile(path)
    infos = z.infolist()

    if not infos or infos[0].filename != "mimetype":
        errors.append("mimetype is not the first zip entry")
    else:
        if infos[0].compress_type != zipfile.ZIP_STORED:
            errors.append("mimetype entry is compressed (must be STORED)")
        if z.read("mimetype").decode() != MIMETYPE:
            errors.append("mimetype content wrong")

    names = set(z.namelist())
    parsed = {}
    for name in names:
        if not name.endswith(".xml"):
            continue
        try:
            parsed[name] = ET.fromstring(z.read(name))
        except ET.ParseError as e:
            errors.append("XML parse error in {}: {}".format(name, e))

    dm = parsed.get("designmap.xml")
    if dm is None:
        errors.append("designmap.xml missing or unparseable")
        return errors, warnings

    for el in dm.iter():
        src = el.attrib.get("src")
        if src and src not in names:
            errors.append("designmap references missing part: " + src)

    story_ids = set()
    for name, root in parsed.items():
        if name.startswith("Stories/"):
            for st in root.iter("Story"):
                story_ids.add(st.attrib.get("Self"))

    n_frames = n_items = n_links = 0
    for name, root in parsed.items():
        if not name.startswith("Spreads/"):
            continue
        for tf in root.iter("TextFrame"):
            n_frames += 1
            ps = tf.attrib.get("ParentStory")
            if ps not in story_ids:
                errors.append(
                    "{}: TextFrame {} ParentStory {} has no Story part".format(
                        name, tf.attrib.get("Self"), ps
                    )
                )
        for tag in ("Rectangle", "Oval", "Polygon", "GraphicLine", "TextFrame"):
            n_items += len(list(root.iter(tag)))
        for link in root.iter("Link"):
            n_links += 1
            uri = link.attrib.get("LinkResourceURI", "")
            if uri.startswith("file:"):
                from urllib.parse import unquote
                import os

                p = unquote(re.sub("^///", "", uri[5:]))
                if not os.path.exists(p):
                    errors.append("{}: broken link {}".format(name, uri))
        for ppa in root.iter("PathPointArray"):
            if len(list(ppa)) < 2:
                errors.append("{}: PathPointArray with < 2 points".format(name))

    spreads = [n for n in names if n.startswith("Spreads/")]
    stories = [n for n in names if n.startswith("Stories/")]
    print(
        "parts={} spreads={} stories={} frames={} pageitems={} links={}".format(
            len(names), len(spreads), len(stories), n_frames, n_items, n_links
        )
    )
    return errors, warnings


if __name__ == "__main__":
    errors, warnings = validate(sys.argv[1])
    for w in warnings:
        print("WARN:", w)
    for e in errors:
        print("ERROR:", e)
    if errors:
        sys.exit(1)
    print("OK: package structurally valid")
