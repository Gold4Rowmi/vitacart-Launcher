"""Build "RetroFlow Float": a separate app that installs NEXT TO RetroFlow.

- Own title ID (RFFLOAT01) and name, so the original RETROFLOW bubble is untouched.
- Own data folder ux0:/data/RetroFlowFloat/ for settings, caches, favourites,
  collections etc. On first launch it copies these from ux0:/data/RetroFlow/
  (read only) so no rescan is needed. Nothing is ever written back.
- Shares (reads) ROMs, covers, backgrounds, icons, wallpapers and music with
  RetroFlow, so there are no duplicate gigabytes. Cover downloads land in the
  shared COVERS folder, which only helps both apps.
- Recovery/reset inside RetroFlow Float only touches RetroFlowFloat.
- Floating media is on by default.

usage: make_sidebyside.py ORIGINAL_VPK_DIR PATCHED_INDEX_LUA MEDIA_DATA_DIR OUT_VPK
"""
import io
import os
import re
import shutil
import struct
import sys
import tempfile
import zipfile

from PIL import Image, ImageDraw, ImageFont

TITLE_ID = "RFFLOAT01"
TITLE = "RetroFlow Float"
OLD = "ux0:/data/RetroFlow"
NEW = "ux0:/data/RetroFlowFloat"
SHARED = r"(?:ROMS|COVERS|BACKGROUNDS|ICONS|WALLPAPER|MUSIC)/|Music\.ogg"

SEED = r'''
-- RETROFLOW FLOAT (side-by-side build): first launch copies settings, caches and
-- lists from the main RetroFlow install. The original folder is only read.
do
    local src, dst = "ux0:/data/RetroFlow/", "ux0:/data/RetroFlowFloat/"
    local function seed_copy_dir(from, to)
        System.createDirectory(to)
        local ok, list = pcall(System.listDirectory, from)
        if not ok or type(list) ~= "table" then return end
        for _, e in ipairs(list) do
            if e.directory then
                seed_copy_dir(from .. e.name .. "/", to .. e.name .. "/")
            elseif not System.doesFileExist(to .. e.name) then
                pcall(System.copyFile, from .. e.name, to .. e.name)
            end
        end
    end
    if not System.doesFileExist(dst .. "config.dat") and System.doesFileExist(src .. "config.dat") then
        local ok, list = pcall(System.listDirectory, src)
        if ok and type(list) == "table" then
            for _, e in ipairs(list) do
                local n = e.name
                if not e.directory and not n:lower():match("%.ogg$") and not n:lower():match("%.png$") and not n:lower():match("%.jpg$") then
                    pcall(System.copyFile, src .. n, dst .. n)
                end
            end
        end
        for _, d in ipairs({ "CACHE", "COLLECTIONS", "TITLES" }) do
            if System.doesDirExist(src .. d) then seed_copy_dir(src .. d .. "/", dst .. d .. "/") end
        end
        -- switch the copied settings to floating media
        local f = io.open(dst .. "config.dat", "r")
        if f then
            local s = f:read("*a"); f:close()
            s = s:gsub("Reflections=%-?%d+", "Reflections=2", 1)
            f = io.open(dst .. "config.dat", "w")
            if f then f:write(s); f:close() end
        end
    end
end
'''


def convert_paths(text, report, name):
    n_total = 0

    def sub(pattern, repl, s):
        nonlocal n_total
        s2, n = re.subn(pattern, repl, s)
        n_total += n
        return s2

    # private data -> RetroFlowFloat (everything except the shared asset folders)
    text = sub(re.escape(OLD + "/") + r"(?!" + SHARED + r")", NEW + "/", text)
    # bare root (recovery.lua) and recovery backups
    text = sub(re.escape(OLD) + r'"', NEW + '"', text)
    text = sub(re.escape(OLD) + r"_backup_", NEW + "_backup_", text)
    report.append(f"{name}: {n_total} private paths redirected")
    return text


def patch_index(text, report):
    text = convert_paths(text, report, "index.lua")
    # music via cur_dir stays shared with RetroFlow
    text, n = re.subn(r'cur_dir \.\. "/MUSIC/"', '"' + OLD + '/MUSIC/"', text)
    report.append(f"index.lua: {n} cur_dir MUSIC paths kept shared")
    # floating media on by default
    text, n = re.subn(r"^local setReflections = 1$", "local setReflections = 2", text, flags=re.M)
    assert n == 1, "setReflections default not found"
    # seed on first launch, right after the data folder is created
    anchor = f'System.createDirectory("{NEW}/")\n'
    assert text.count(anchor) == 1, "data dir anchor not found"
    text = text.replace(anchor, anchor + SEED, 1)
    return text


def patch_sfo(data):
    magic, ver, kt, dt, n = struct.unpack("<4sIIII", data[:20])
    data = bytearray(data)
    want = {"TITLE_ID": TITLE_ID, "TITLE": TITLE, "STITLE": TITLE}
    for i in range(n):
        off = 20 + i * 16
        ko, fmt, ln, mx, do = struct.unpack("<HHIII", data[off:off + 16])
        key = bytes(data[kt + ko:data.index(b"\0", kt + ko)]).decode()
        if key in want:
            val = want[key].encode() + b"\0"
            assert len(val) <= mx, key
            data[dt + do:dt + do + mx] = val + b"\0" * (mx - len(val))
            struct.pack_into("<HHIII", data, off, ko, fmt, len(val), mx, do)
    return bytes(data)


def badge_icon(path):
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    d = ImageDraw.Draw(im)
    bh = int(h * 0.24)
    d.rectangle([0, h - bh, w, h], fill=(20, 22, 28, 235))
    font = None
    for f in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        if os.path.exists(f):
            font = ImageFont.truetype(f, int(bh * 0.62))
    text = "FLOAT"
    tw = d.textlength(text, font=font)
    d.text(((w - tw) / 2, h - bh + bh * 0.14), text, fill=(255, 214, 90, 255), font=font)
    # LiveArea wants 8-bit indexed PNGs
    im.convert("RGB").quantize(colors=256, method=Image.Quantize.MEDIANCUT).save(path, optimize=True)


def main():
    SRC_VPK, INDEX, MEDIA, OUT = sys.argv[1:5]
    report = []
    work = tempfile.mkdtemp()
    build = os.path.join(work, "vpk")
    shutil.copytree(SRC_VPK, build)
    for f in os.listdir(MEDIA):
        shutil.copy(os.path.join(MEDIA, f), os.path.join(build, "DATA", f))
    idx = open(INDEX, encoding="utf-8").read()
    open(os.path.join(build, "index.lua"), "w", encoding="utf-8").write(patch_index(idx, report))
    for f in sorted(os.listdir(os.path.join(build, "addons"))):
        if f.endswith(".lua"):
            p = os.path.join(build, "addons", f)
            t = open(p, encoding="utf-8").read()
            t2 = convert_paths(t, report, "addons/" + f)
            if t2 != t:
                open(p, "w", encoding="utf-8").write(t2)
    sfo = os.path.join(build, "sce_sys", "param.sfo")
    sfo_data = open(sfo, "rb").read()
    open(sfo, "wb").write(patch_sfo(sfo_data))
    badge_icon(os.path.join(build, "sce_sys", "icon0.png"))
    if os.path.exists(OUT):
        os.remove(OUT)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(build):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, build))
    print("\n".join(r for r in report if not r.endswith(": 0 private paths redirected")))
    print("built", OUT, "work dir", build)


if __name__ == "__main__":
    main()
