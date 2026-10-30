#!/usr/bin/env python3
"""Give cover art the look of a printed sticker: CMYK halftone screens at the classic
angles (the rosette you see with a loupe), a little softness, paper grain and off-white paper.

    print_covers.py IN_DIR OUT_DIR [--cell 2.2] [--suffix _print]      process every png/jpg in a folder
    print_covers.py --vita [--systems "Sony - PlayStation Vita" ...]   pull covers from the Vita's shared
                                                                        RetroFlow COVERS folder, process them
                                                                        and upload to RetroFlow Float's private
                                                                        ux0:/data/RetroFlowFloat/PRINT/COVERS/
RetroFlow Float prefers a PRINT copy when one exists (FLOAT.label_path), normal RetroFlow never sees them.
"""
import argparse, io, math, os, subprocess, sys, urllib.parse
import numpy as np
from PIL import Image, ImageFilter

ANGLES = {"c": 15.0, "m": 75.0, "y": 0.0, "k": 45.0}


def screen(density, cell, angle, ss):
    """Halftone one ink: density 0..1 (already at supersampled size) -> ink coverage 0..1."""
    h, w = density.shape
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    a = math.radians(angle)
    c = cell * ss
    u = (xs * math.cos(a) + ys * math.sin(a)) / c
    v = (-xs * math.sin(a) + ys * math.cos(a)) / c
    du, dv = u - np.floor(u) - 0.5, v - np.floor(v) - 0.5
    dist = np.sqrt(du * du + dv * dv)                      # 0 at the cell centre, up to ~0.7 at a corner
    # density sampled at the cell centre (box average keeps the dots clean)
    cu, cv = np.floor(u) + 0.5, np.floor(v) + 0.5
    cx = (cu * math.cos(a) - cv * math.sin(a)) * c
    cy = (cu * math.sin(a) + cv * math.cos(a)) * c
    xi = np.clip(cx.round().astype(int), 0, w - 1)
    yi = np.clip(cy.round().astype(int), 0, h - 1)
    blurred = np.asarray(Image.fromarray((density * 255).astype(np.uint8)).filter(ImageFilter.BoxBlur(c / 2))).astype(np.float32) / 255
    d = blurred[yi, xi]
    r = np.sqrt(np.clip(d, 0, 1) / math.pi)                # dot radius (in cells) with area = density
    edge = 0.35 / ss                                       # soft edge, antialiased
    return np.clip((r - dist) / edge + 0.5, 0, 1)


def print_effect(img, cell=1.6, ss=3, paper=(0.985, 0.978, 0.962), grain=0.012, soften=0.35, gcr=0.85, mix=0.35):
    img = img.convert("RGB")
    orig = np.asarray(img).astype(np.float32)
    w, h = img.size
    big = np.asarray(img.resize((w * ss, h * ss), Image.LANCZOS)).astype(np.float32) / 255
    r, g, b = big[..., 0], big[..., 1], big[..., 2]
    k = 1 - np.max(big, axis=-1)
    k = k * gcr                                            # partial black replacement keeps colours rich
    den = 1 - k + 1e-6
    c = np.clip((1 - r - k) / den, 0, 1)
    m = np.clip((1 - g - k) / den, 0, 1)
    y = np.clip((1 - b - k) / den, 0, 1)
    inks = {n: screen(v, cell, ANGLES[n], ss) for n, v in (("c", c), ("m", m), ("y", y), ("k", k))}
    # ink colours a little impure, like real process inks
    out = np.ones_like(big) * np.array(paper, np.float32)
    for n, col in (("c", (0.0, 0.62, 0.90)), ("m", (0.90, 0.07, 0.50)), ("y", (1.0, 0.90, 0.05)), ("k", (0.10, 0.10, 0.12))):
        a = inks[n][..., None]
        out = out * (1 - a) + out * np.array(col, np.float32) * a
    out = np.clip(out, 0, 1)
    res = Image.fromarray((out * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(soften * ss)).resize((w, h), Image.LANCZOS)
    arr = np.asarray(res).astype(np.float32) * (1 - mix) + orig * mix     # a little of the original keeps it legible
    noise = np.random.default_rng(7).normal(0, grain * 255, (h, w, 1))
    arr = np.clip(arr + noise, 0, 255)
    return Image.fromarray(arr.astype(np.uint8))


def process_folder(inp, out, cell, suffix):
    os.makedirs(out, exist_ok=True)
    n = 0
    for f in sorted(os.listdir(inp)):
        if f.lower().endswith((".png", ".jpg", ".jpeg")):
            base, _ = os.path.splitext(f)
            print_effect(Image.open(os.path.join(inp, f)), cell).save(os.path.join(out, base + suffix + ".png"))
            n += 1
    print(f"{out}: {n} covers")


def vita_run(systems, cell, ip, port):
    base = f"ftp://{ip}:{port}/ux0:/data/RetroFlow/COVERS/"
    dst = f"ftp://{ip}:{port}/ux0:/data/RetroFlowFloat/PRINT/COVERS/"
    for sysname in systems:
        q = urllib.parse.quote(sysname)
        listing = subprocess.run(["curl", "-s", "--max-time", "60", f"{base}{q}/"], capture_output=True, text=True).stdout
        names = [l.split()[-1] for l in listing.splitlines() if l.lower().endswith(".png") or l.lower().endswith(".jpg")]
        print(f"{sysname}: {len(names)} covers")
        for name in names:
            raw = subprocess.run(["curl", "-s", "--max-time", "120", f"{base}{q}/{urllib.parse.quote(name)}"], capture_output=True).stdout
            if not raw:
                print("  skip (download failed):", name); continue
            img = print_effect(Image.open(io.BytesIO(raw)), cell)
            buf = io.BytesIO(); img.save(buf, "PNG")
            outname = os.path.splitext(name)[0] + ".png"
            r = subprocess.run(["curl", "-sS", "--max-time", "120", "--ftp-create-dirs", "--ftp-method", "nocwd", "-T", "-",
                                f"{dst}{q}/{urllib.parse.quote(outname)}"], input=buf.getvalue(), capture_output=True)
            print("  ", "ok " if r.returncode == 0 else "FAIL", outname, r.stderr.decode().strip()[:80])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("inp", nargs="?"); ap.add_argument("out", nargs="?")
    ap.add_argument("--cell", type=float, default=1.6, help="halftone cell size in output pixels")
    ap.add_argument("--suffix", default="_print")
    ap.add_argument("--vita", action="store_true")
    ap.add_argument("--systems", nargs="*", default=["Sony - PlayStation Vita", "Sony - PlayStation Portable", "Sony - PlayStation"])
    ap.add_argument("--ip", default=os.environ.get("VITA_IP", "192.168.68.60")); ap.add_argument("--port", default="1337")
    a = ap.parse_args()
    if a.vita:
        vita_run(a.systems, a.cell, a.ip, a.port)
    elif a.inp and a.out:
        process_folder(a.inp, a.out, a.cell, a.suffix)
    else:
        ap.print_help()
