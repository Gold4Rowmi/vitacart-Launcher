#!/usr/bin/env python3
"""Build real UMD disc labels for RetroFlow Float from umdatabase.net scans.

For every PSP game in your library this looks up its disc ID on umdatabase.net,
downloads the UMD scan, crops the disc out of it and writes
    <COVERS>/Sony - PlayStation Portable/<GAME ID>_umd.png
next to the normal cover. RetroFlow Float uses that file as the round label
on the UMD model; the normal cover is untouched and RetroFlow ignores the file.

Run this on a PC with the Vita's memory card mounted (or a copy of the folder):

    pip install requests pillow numpy
    python3 umd_labels.py "/path/to/ux0/data/RetroFlow"

Options:
    --from-folder DIR                 import existing disc images instead of
                                      downloading: e.g. the "physical media" /
                                      "support" PNGs a scraper such as Skraper or
                                      ES-DE fetched from ScreenScraper.fr. Files
                                      are matched by disc ID in the name, or by
                                      the game title used for the RetroFlow cover.
    --ids UCJS-10045 ULUS-10041 ...   only these disc IDs
    --force                           rebuild labels that already exist
    --keep                            keep the raw downloaded scans in .umd_cache/

umdatabase.net is a volunteer archive with no API, so this goes one request at
a time with a pause between them and caches everything it downloads. If a game
isn't in the database, or the scan can't be cropped, that game just keeps using
its box art as before.
"""
import argparse
import io
import json
import os
import re
import sys
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

BASE = "https://umdatabase.net"
UA = "RetroFlow-Float-umd-labels/1.0 (personal library tool; one request at a time)"
PAUSE = 2.0  # seconds between requests
OUT_SIZE = 512


# ----------------------------------------------------------------------------- library
def library_disc_ids(root):
    """Disc IDs of PSP games known to RetroFlow (from its cache db, then the ROM names)."""
    ids = {}
    cache = os.path.join(root, "CACHE")
    if os.path.isdir(cache):
        for name in os.listdir(cache):
            if not name.lower().endswith(".lua"):
                continue
            text = open(os.path.join(cache, name), encoding="utf-8", errors="ignore").read()
            for m in re.finditer(r'\["titleid"\]\s*=\s*"([A-Z]{4}[-]?\d{5})"', text):
                tid = m.group(1).replace("-", "")
                ids[tid] = tid
            for m in re.finditer(r'\["name"\]\s*=\s*"([A-Z]{4}\d{5})"', text):
                ids[m.group(1)] = m.group(1)
    # anything else that looks like a disc ID in the Adrenaline folders
    for sub in ("pspemu/ISO", "pspemu/PSP/GAME"):
        d = os.path.join(os.path.dirname(os.path.dirname(root)), sub)
        if os.path.isdir(d):
            for name in os.listdir(d):
                m = re.search(r"([A-Z]{4})[-_ ]?(\d{5})", name)
                if m:
                    ids[m.group(1) + m.group(2)] = m.group(1) + m.group(2)
    return sorted(ids)


def dashed(disc_id):
    return disc_id[:4] + "-" + disc_id[4:]


# ----------------------------------------------------------------------------- site
class Site:
    def __init__(self, cache_dir):
        import requests
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.index_path = os.path.join(cache_dir, "index.json")
        self.index = json.load(open(self.index_path)) if os.path.exists(self.index_path) else {}
        self.last = 0.0

    def get(self, url, binary=False):
        wait = PAUSE - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        r = self.s.get(url, timeout=30)
        self.last = time.time()
        r.raise_for_status()
        return r.content if binary else r.text

    def entry_for(self, disc_id):
        """The site's hex id for a disc ID, via its search page."""
        if disc_id in self.index:
            return self.index[disc_id]
        html = self.get(f"{BASE}/db.php?search={dashed(disc_id)}")
        # prefer a result row that mentions this disc id; else the first result
        hit = None
        for m in re.finditer(r'view\.php\?id=([0-9A-Fa-f]+)', html):
            window = html[max(0, m.start() - 600): m.end() + 600]
            if dashed(disc_id) in window or disc_id in window:
                hit = m.group(1)
                break
            hit = hit or m.group(1)
        self.index[disc_id] = hit
        json.dump(self.index, open(self.index_path, "w"), indent=1)
        return hit

    def scan_images(self, hex_id):
        """Candidate image URLs for an entry, disc scans first."""
        html = self.get(f"{BASE}/view.php?id={hex_id}")
        urls = re.findall(r'data/' + hex_id + r'/[^"\'<> ]+\.(?:jpg|jpeg|png|JPG|JPEG|PNG)', html)
        seen, out = set(), []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(f"{BASE}/{u}")
        # numbered scans (000.JPG...) are the disc photos; front.jpg is the box
        out.sort(key=lambda u: (not re.search(r'/\d{3}\.', u), u))
        return out

    def image(self, url):
        name = re.sub(r'[^A-Za-z0-9_.-]', "_", url.split("/data/", 1)[-1])
        path = os.path.join(self.cache_dir, name)
        if not os.path.exists(path):
            open(path, "wb").write(self.get(url, binary=True))
        return Image.open(path).convert("RGB")


# ----------------------------------------------------------------------------- crop
def find_disc(img):
    """Locate the disc in a UMD scan: the largest round, non-background blob.
    Returns (cx, cy, r) in pixels or None."""
    small = img.copy()
    small.thumbnail((600, 600))
    k = img.width / small.width
    a = np.asarray(small).astype(np.float32)
    # background = colour of the image border
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    bg = np.median(border, axis=0)
    diff = np.sqrt(((a - bg) ** 2).sum(axis=2))
    mask = diff > 40
    # the white shell is close to a white background; the disc is darker/coloured.
    # Drop near-white pixels so the shell doesn't count as disc.
    bright = a.min(axis=2) > 215
    disc_mask = mask & ~bright
    ys, xs = np.nonzero(disc_mask)
    if len(xs) < 500:
        return None
    # robust centre and radius from the mask's extent (trim 1% outliers)
    x0, x1 = np.percentile(xs, [1, 99])
    y0, y1 = np.percentile(ys, [1, 99])
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = max(x1 - x0, y1 - y0) / 2
    # refine: radius where mask coverage along the circle drops
    best = r
    for rr in np.linspace(r * 0.8, r * 1.15, 36):
        ang = np.linspace(0, 2 * np.pi, 180)
        px = np.clip((cx + rr * np.cos(ang)).astype(int), 0, disc_mask.shape[1] - 1)
        py = np.clip((cy + rr * np.sin(ang)).astype(int), 0, disc_mask.shape[0] - 1)
        cov = disc_mask[py, px].mean()
        if cov > 0.6:
            best = rr
    return cx * k, cy * k, best * k


def disc_label(img, circle, size=OUT_SIZE):
    cx, cy, r = circle
    r *= 0.985  # trim the shell rim
    box = (int(cx - r), int(cy - r), int(cx + r), int(cy + r))
    sq = img.crop(box).resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([1, 1, size - 2, size - 2], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(0.8))
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(sq, (0, 0), mask)
    return out


# ----------------------------------------------------------------------------- import
def import_folder(folder, covers, ids, force):
    """Use disc images already on disk (ScreenScraper 'physical media' etc.)."""
    files = [f for f in os.listdir(folder) if f.lower().endswith((".png", ".jpg", ".jpeg"))]
    cover_names = {os.path.splitext(f)[0].lower(): os.path.splitext(f)[0]
                   for f in os.listdir(covers) if f.lower().endswith(".png") and not f.lower().endswith("_umd.png")}
    done = 0
    for f in files:
        stem = os.path.splitext(f)[0]
        m = re.search(r"([A-Z]{4})[-_ ]?(\d{5})", stem.upper())
        if m:
            target = m.group(1) + m.group(2)
        else:
            # match the cover's own name (RetroFlow names PSP covers by game id or title)
            key = re.sub(r"-(physicalmedia|support|disc|cart)$", "", stem.lower())
            target = cover_names.get(key)
        if not target or (ids and target not in ids):
            continue
        out = os.path.join(covers, f"{target}_umd.png")
        if os.path.exists(out) and not force:
            continue
        img = Image.open(os.path.join(folder, f)).convert("RGBA")
        # already a clean disc on transparency? use its alpha bounds; else find the disc
        alpha = np.asarray(img)[..., 3]
        ys, xs = np.nonzero(alpha > 32)
        if len(xs) and (alpha == 0).mean() > 0.1:
            r = max(xs.max() - xs.min(), ys.max() - ys.min()) / 2
            circle = ((xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2, r)
        else:
            circle = find_disc(img.convert("RGB"))
        if not circle:
            print(f"  {f}: no disc found")
            continue
        disc_label(img.convert("RGB"), circle).save(out, optimize=True)
        print(f"  {f} -> {os.path.basename(out)}")
        done += 1
    print(f"done: {done} labels written")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("retroflow_dir", help="ux0:/data/RetroFlow folder (mounted or copied)")
    ap.add_argument("--from-folder", help="import disc PNG/JPGs from this folder instead of downloading")
    ap.add_argument("--ids", nargs="*", help="only these disc IDs (UCJS-10045 ...)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    root = args.retroflow_dir.rstrip("/\\")
    covers = os.path.join(root, "COVERS", "Sony - PlayStation Portable")
    os.makedirs(covers, exist_ok=True)
    ids = [i.replace("-", "").upper() for i in args.ids] if args.ids else library_disc_ids(root)
    if args.from_folder:
        import_folder(args.from_folder, covers, ids if args.ids else None, args.force)
        return
    if not ids:
        sys.exit("No PSP disc IDs found. Open RetroFlow once so its CACHE exists, or pass --ids.")
    print(f"{len(ids)} PSP games")

    site = Site(os.path.join(root, ".umd_cache"))
    done = missing = failed = 0
    for disc_id in ids:
        out = os.path.join(covers, f"{disc_id}_umd.png")
        if os.path.exists(out) and not args.force:
            continue
        try:
            hex_id = site.entry_for(disc_id)
            if not hex_id:
                print(f"  {dashed(disc_id)}: not in umdatabase")
                missing += 1
                continue
            label = None
            for url in site.scan_images(hex_id):
                if "/front" in url:
                    continue
                img = site.image(url)
                circle = find_disc(img)
                if circle and circle[2] > min(img.size) * 0.25:
                    label = disc_label(img, circle)
                    break
            if label is None:
                print(f"  {dashed(disc_id)}: scan found but no disc could be cropped")
                failed += 1
                continue
            label.save(out, optimize=True)
            print(f"  {dashed(disc_id)}: ok")
            done += 1
        except Exception as e:  # keep going: one bad entry shouldn't stop the run
            print(f"  {dashed(disc_id)}: error {e}")
            failed += 1
    if not args.keep:
        for n in os.listdir(site.cache_dir):
            if n != "index.json":
                os.remove(os.path.join(site.cache_dir, n))
    print(f"done: {done} labels written, {missing} not in database, {failed} failed")
    print("Rescan isn't needed: RetroFlow Float picks the labels up the next time it loads the covers.")


if __name__ == "__main__":
    main()
