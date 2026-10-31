"""Rebuild a RetroFlow Float VPK from an earlier one: keeps its engine, icon and
param.sfo, swaps in a freshly patched index.lua and the current floating-media
DATA files (stale float_* files are dropped).

usage: rebuild_float.py OLD_FLOAT_VPK PATCHED_INDEX_LUA MEDIA_DATA_DIR OUT_VPK
"""
import os
import sys
import zipfile

from make_sidebyside import patch_index

OLD_VPK, INDEX, MEDIA, OUT = sys.argv[1:5]
report = []
index = patch_index(open(INDEX, encoding="utf-8").read(), report)
import hashlib
h = hashlib.md5()
for f in sorted(os.listdir(MEDIA)):
    h.update(f.encode()); h.update(open(os.path.join(MEDIA, f), "rb").read())
index = index.replace('FLOAT_DATA_VERSION_PLACEHOLDER', h.hexdigest()[:16])   # the card copy of DATA is refreshed only when the files change
media = sorted(os.listdir(MEDIA))
with zipfile.ZipFile(OLD_VPK) as src, zipfile.ZipFile(OUT + ".tmp", "w", zipfile.ZIP_DEFLATED) as dst:
    kept = 0
    for info in src.infolist():
        n = info.filename
        if n == "index.lua" or n.startswith("DATA/float_"):
            continue
        dst.writestr(info, src.read(info))
        kept += 1
    dst.writestr("index.lua", index.encode("utf-8"))
    for f in media:
        dst.write(os.path.join(MEDIA, f), "DATA/" + f)
os.replace(OUT + ".tmp", OUT)
print("\n".join(report))
print(f"built {OUT}: {kept} files kept, index.lua + {len(media)} media files replaced")
