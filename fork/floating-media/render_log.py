"""Render draws.txt (from harness.lua) into frames + an animated GIF/MP4."""
import sys, os
import numpy as np
from PIL import Image
import preview as P

DATA, ART, BG, LOG, OUTDIR = sys.argv[1:6]
os.makedirs(OUTDIR, exist_ok=True)
models, texs = {}, {}
def model(path):
    if path not in models: models[path] = P.load_obj(path.replace("app0:/DATA", DATA))
    return models[path]
def texture(t):
    if t not in texs:
        texs[t] = P.tex(t.replace("app0:/DATA", DATA) if t.startswith("app0:") else f"{ART}/{t}.png")
    return texs[t]
bg = np.asarray(Image.open(BG).convert("RGB").resize((P.W, P.H))).astype(np.float32) / 255.0
frames, cur = [], None
for line in open(LOG):
    if line.startswith("FRAME"):
        if cur is not None: frames.append(cur)
        cur = []
    else:
        p = line.split(); cur.append((p[0], p[1], *map(float, p[2:])))
frames.append(cur)
for i, fr in enumerate(frames):
    c = bg.copy()
    for mp, tx, x, y, z, rx, ry, rz in fr:
        P.draw(c, model(mp), texture(tx), (x, y, z), rx, ry, rz)
    Image.fromarray((np.clip(c, 0, 1) * 255).astype(np.uint8)).save(f"{OUTDIR}/f{i:04d}.png")
print(len(frames), "frames")
