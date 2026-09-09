# -*- coding: utf-8 -*-
"""
Open an IDML in InDesign (COM), report page/spread structure, close it.
Cheap structural probe - no PDF export.

    python tools/indesign_probe.py file.idml
"""
import os
import shutil
import sys

import win32com.client as w32

NEVER_INTERACT = 1699640946
INTERACT_ALL = 1699311169


def main(path):
    app = w32.Dispatch("InDesign.Application")
    app.ScriptPreferences.UserInteractionLevel = NEVER_INTERACT
    src = os.path.abspath(path)
    tmp = os.path.join(os.path.dirname(src), "~probe_%d_%s" % (os.getpid(), os.path.basename(src)))
    shutil.copy2(src, tmp)
    n0 = app.Documents.Count
    doc = app.Open(tmp)
    try:
        own = app.Documents.Count == n0 + 1
        print("pages", doc.Pages.Count, "spreads", doc.Spreads.Count,
              "facing", doc.DocumentPreferences.FacingPages)
        empty = 0
        for i in range(1, doc.Spreads.Count + 1):
            s = doc.Spreads.Item(i)
            if s.AllPageItems.Count == 0:
                empty += 1
        print("empty spreads", empty)
        s1 = doc.Spreads.Item(1)
        print("spread1 pages", s1.Pages.Count, "items", s1.AllPageItems.Count,
              "page1 items", doc.Pages.Item(1).AllPageItems.Count)
    finally:
        if own:
            doc.Close(1852776480)
        os.remove(tmp)
        app.ScriptPreferences.UserInteractionLevel = INTERACT_ALL


if __name__ == "__main__":
    main(sys.argv[1])
