# -*- coding: utf-8 -*-
r"""
Rewrite one or more member files inside an IDML with a regex substitution and
write a new package (mimetype first, stored) - for quick A/B experiments
against InDesign without re-running the QGIS export.

    python tools/idml_patch.py in.idml out.idml \
        --sub 'Resources/Preferences.xml' 'PagesPerDocument="\d+"' 'PagesPerDocument="1"'
"""
import argparse
import fnmatch
import re
import zipfile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--sub", nargs=3, action="append", metavar=("MEMBER", "PATTERN", "REPL"),
                    default=[])
    a = ap.parse_args()
    zin = zipfile.ZipFile(a.src)
    with zipfile.ZipFile(a.dst, "w") as zout:
        zout.writestr(zipfile.ZipInfo("mimetype"), zin.read("mimetype"), zipfile.ZIP_STORED)
        for info in zin.infolist():
            if info.filename == "mimetype":
                continue
            data = zin.read(info.filename)
            for member, pat, repl in a.sub:
                if fnmatch.fnmatch(info.filename, member):
                    txt = data.decode("utf-8")
                    txt, n = re.subn(pat, repl, txt)
                    print("%s: %d substitution(s)" % (member, n))
                    data = txt.encode("utf-8")
            zout.writestr(info.filename, data, zipfile.ZIP_DEFLATED)
    print("wrote", a.dst)


if __name__ == "__main__":
    main()
