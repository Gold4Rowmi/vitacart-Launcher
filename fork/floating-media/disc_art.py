#!/usr/bin/env python3
"""Import real disc art into RetroFlow Float: PS1 disc labels and PSP UMD labels.

    disc_art.py IMPORT_DIR [--vita] [--print] [--force]

Give it a folder of disc images from any source (Skraper / ScreenScraper "Support 2D"
media, LaunchBox "Disc" images, your own scans). Each file is matched to a game in your
library by the disc ID in its name (SCUS-94228, ULUS10041 ...) or by the game's title,
using RetroFlow Float's cache on the Vita. The disc is located and cropped to a clean
512x512 round label (umd_labels.py's detector), optionally given the printed look, and
uploaded to Float's own folder, which normal RetroFlow never reads:

    ux0:/data/RetroFlowFloat/PRINT/COVERS/Sony - PlayStation/<ID>_disc.png
    ux0:/data/RetroFlowFloat/PRINT/COVERS/Sony - PlayStation Portable/<ID>_umd.png

Without --vita the labels are written next to this script in disc_art_out/ for a look.
"""
import argparse, io, os, re, subprocess, sys, urllib.parse
import numpy as np
from PIL import Image
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import umd_labels
import print_covers

SYSTEMS = {2: ("Sony - PlayStation Portable", "_umd.png"), 3: ("Sony - PlayStation", "_disc.png")}


def norm(t):
    t = t.lower()
    t = re.sub(r"\(.*?\)|\[.*?\]", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def library(ip, port):
    """Games from Float's cache: {titleid: (app_type, title)}."""
    games = {}
    for db in ("db_psx.lua", "db_psp.lua"):
        txt = subprocess.run(["curl", "-s", "--max-time", "60", f"ftp://{ip}:{port}/ux0:/data/RetroFlowFloat/CACHE/{db}"],
                             capture_output=True, text=True).stdout
        for e in re.findall(r"\{(.*?)\n\t\},", txt, re.S):
            g = lambda k: (re.search(r'\["%s"\] = ("?)([^,\n]*?)\1,' % k, e) or [None, None, None])[2]
            tid, at, title = g("titleid"), g("app_type"), g("apptitle") or g("name")
            if tid and at in ("2", "3"):
                games[tid.replace("-", "")] = (int(at), title or tid)
    return games


def match(fname, games):
    stem = os.path.splitext(fname)[0]
    m = re.search(r"([A-Z]{4})[-_ ]?(\d{5})", stem.upper())
    if m and (m.group(1) + m.group(2)) in games:
        return m.group(1) + m.group(2)
    key = norm(re.sub(r"-(physicalmedia|support|disc|cart|umd)$", "", stem, flags=re.I))
    for tid, (_, title) in games.items():
        if norm(title) == key or (len(key) > 6 and (key in norm(title) or norm(title) in key)):
            return tid
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--vita", action="store_true", help="upload to the Vita (else write to disc_art_out/)")
    ap.add_argument("--print", dest="printed", action="store_true", help="apply the printed-sticker look too")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--ip", default=os.environ.get("VITA_IP", "192.168.68.60")); ap.add_argument("--port", default="1337")
    a = ap.parse_args()
    games = library(a.ip, a.port)
    print(f"library: {len(games)} PSP/PS1 games")
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "disc_art_out")
    os.makedirs(out_dir, exist_ok=True)
    done = 0
    for f in sorted(os.listdir(a.folder)):
        if not f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            continue
        tid = match(f, games)
        if not tid:
            print(f"  {f}: no matching game"); continue
        app_type, title = games[tid]
        system, suffix = SYSTEMS[app_type]
        img = Image.open(os.path.join(a.folder, f)).convert("RGBA")
        alpha = np.asarray(img)[..., 3]
        ys, xs = np.nonzero(alpha > 32)
        if len(xs) and (alpha == 0).mean() > 0.1:
            r = max(xs.max() - xs.min(), ys.max() - ys.min()) / 2
            circle = ((xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2, r)
        else:
            circle = umd_labels.find_disc(img.convert("RGB"))
        if not circle:
            print(f"  {f}: no disc found"); continue
        label = umd_labels.disc_label(img.convert("RGB"), circle)
        if a.printed:
            rgb = print_covers.print_effect(label.convert("RGB"), cell=1.6)
            label = Image.merge("RGBA", (*rgb.split(), label.split()[3]))
        name = tid + suffix
        if a.vita:
            buf = io.BytesIO(); label.save(buf, "PNG")
            dst = f"ftp://{a.ip}:{a.port}/ux0:/data/RetroFlowFloat/PRINT/COVERS/{urllib.parse.quote(system)}/{name}"
            subprocess.run(["curl", "-sS", "--max-time", "60", "-Q", f"MKD ux0:/data/RetroFlowFloat/PRINT/COVERS/{system}", f"ftp://{a.ip}:{a.port}/"], capture_output=True)
            r = subprocess.run(["curl", "-sS", "--max-time", "120", "--ftp-method", "nocwd", "-T", "-", dst], input=buf.getvalue(), capture_output=True)
            print("  ", "ok " if r.returncode == 0 else "FAIL", f, "->", name, f"({title})")
        else:
            label.save(os.path.join(out_dir, name)); print(f"  {f} -> disc_art_out/{name} ({title})")
        done += 1
    print(f"done: {done} labels")


if __name__ == "__main__":
    main()
