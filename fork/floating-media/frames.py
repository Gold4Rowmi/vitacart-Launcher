"""Render chosen frames of draws.txt: frames.py DATA ART BG LOG OUT_PREFIX frame..."""
import sys, numpy as np
from PIL import Image
sys.path.insert(0, sys.argv[6])
import preview as P
DATA, ART, BG, LOG, PRE = sys.argv[1:6]
want = [int(a) for a in sys.argv[7:]]
models, texs = {}, {}
def model(p):
    if p not in models: models[p] = P.load_obj(p.replace("app0:/DATA", DATA))
    return models[p]
def texture(t):
    if t not in texs:
        texs[t] = P.tex(t.replace("app0:/DATA", DATA) if t.startswith("app0:") else f"{ART}/{t}.png")
    return texs[t]
bg = np.asarray(Image.open(BG).convert("RGB")).astype(np.float32) / 255.0
frames, cur = [], None
for line in open(LOG):
    if line.startswith("FRAME"):
        if cur is not None: frames.append(cur)
        cur = []
    else:
        p = line.split(); cur.append((p[0], p[1], *map(float, p[2:])))
frames.append(cur)
for f in want:
    canvas = bg.copy()
    for path, tx, x, y, z, rx, ry, rz in frames[f - 1]:
        P.draw(canvas, model(path), texture(tx), (x, y, z), rx, ry, rz)
    Image.fromarray((np.clip(canvas, 0, 1) * 255).astype(np.uint8)).save(f"{PRE}{f}.png")
    print("frame", f, "draws", len(frames[f - 1]))
