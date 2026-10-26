"""Software preview that mimics lpp-vita's Render.drawModel: perspective 45deg,
960x544, model = T * Rx * Ry * Rz, no depth test (painter's order), alpha
blended, unlit textures. Usage: preview.py DATA_DIR ART_DIR BG_IMAGE OUT.png [tilt_deg]"""
import math
import sys

import numpy as np
from PIL import Image

W, H = 960, 544


def load_obj(path):
    v, vt, faces = [], [], []
    for line in open(path):
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            v.append([float(a) for a in p[1:4]])
        elif p[0] == "vt":
            vt.append([float(p[1]), 1.0 - float(p[2])])  # loader flip -> row from top
        elif p[0] == "f":
            faces.append([[int(i) - 1 for i in q.split("/")[:2]] for q in p[1:4]])
    return np.array(v), np.array(vt), faces


def tex(path):
    return np.asarray(Image.open(path).convert("RGBA")).astype(np.float32) / 255.0


def rot(ax, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    if ax == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    if ax == "y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


F = 1.0 / math.tan(math.radians(45) / 2)


def draw(canvas, model, texture, pos, rx=0, ry=0, rz=0):
    v, vt, faces = model
    R = rot("x", rx) @ rot("y", ry) @ rot("z", rz)
    wv = v @ R.T + np.array(pos)
    th, tw = texture.shape[:2]
    for f in faces:
        P = wv[[i for i, _ in f]]
        UV = vt[[j for _, j in f]]
        z = -P[:, 2]
        if (z < 0.1).any():
            continue
        sx = (P[:, 0] * F / (W / H) / z * 0.5 + 0.5) * W
        sy = (0.5 - P[:, 1] * F / z * 0.5) * H
        x0, x1 = int(max(0, np.floor(sx.min()))), int(min(W - 1, np.ceil(sx.max())))
        y0, y1 = int(max(0, np.floor(sy.min()))), int(min(H - 1, np.ceil(sy.max())))
        if x0 > x1 or y0 > y1:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        d = (sy[1] - sy[2]) * (sx[0] - sx[2]) + (sx[2] - sx[1]) * (sy[0] - sy[2])
        if abs(d) < 1e-9:
            continue
        b0 = ((sy[1] - sy[2]) * (xs - sx[2]) + (sx[2] - sx[1]) * (ys - sy[2])) / d
        b1 = ((sy[2] - sy[0]) * (xs - sx[2]) + (sx[0] - sx[2]) * (ys - sy[2])) / d
        b2 = 1 - b0 - b1
        m = (b0 >= -1e-4) & (b1 >= -1e-4) & (b2 >= -1e-4)
        if not m.any():
            continue
        iz = b0 / z[0] + b1 / z[1] + b2 / z[2]
        u = (b0 * UV[0, 0] / z[0] + b1 * UV[1, 0] / z[1] + b2 * UV[2, 0] / z[2]) / iz
        r = (b0 * UV[0, 1] / z[0] + b1 * UV[1, 1] / z[1] + b2 * UV[2, 1] / z[2]) / iz
        tx = np.clip((u * tw).astype(int), 0, tw - 1)
        ty = np.clip((r * th).astype(int), 0, th - 1)
        c = texture[ty, tx]
        a = c[..., 3:4] * m[..., None]
        reg = canvas[y0:y1 + 1, x0:x1 + 1]
        reg[:] = reg * (1 - a) + c[..., :3] * a




models = {}


if __name__ == "__main__":
    def M(name):
        if name not in models:
            models[name] = load_obj(f"{DATA}/{name}.obj")
        return models[name]

    DATA, ART, BG, OUT = sys.argv[1:5]
    TILT = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
    T = {k: tex(f"{DATA}/{k}.png") for k in ["float_vita_body", "float_psx_body", "float_psp_body", "float_gloss", "float_shadow"]}
    A = {k: tex(f"{ART}/{k}.png") for k in ["art_vita", "art_vita2", "art_psp", "art_psx", "art_psx2"]}

    bg = Image.open(BG).convert("RGB").resize((W, H))
    canvas = np.asarray(bg).astype(np.float32) / 255.0
    canvas = canvas.copy()

    HALF = {"vita": 0.925, "psx": 0.925, "psp": 0.89}


    def media(kind, art, x, z, ry, lift, rx=0.0, rz=0.0, gloss=None):
        floor = -0.6
        draw(canvas, M("float_shadow"), T["float_shadow"], (x, floor + 0.003, z), 0, ry, 0)
        pos = (x, floor + HALF[kind] + lift, z)
        draw(canvas, M(f"float_{kind}_body"), T[f"float_{kind}_body"], pos, rx, ry, rz)
        draw(canvas, M(f"float_{kind}_label"), A[art], pos, rx, ry, rz)
        if gloss is not None:
            draw(canvas, M(f"float_{kind}_gloss{gloss}"), T["float_gloss"], pos, rx, ry, rz)


    # Default coverflow layout: neighbours at x=+-1 (+spacing), pushed back 3, rotated +-57deg
    g = int(round((TILT / 22 + 1) / 2 * 8))
    media("psx", "art_psx2", -2.6, -8.0, 57, 0.10)
    media("psp", "art_psp", 2.6, -8.0, -57, 0.10)
    media("vita", "art_vita2", -1.6, -8.0, 57, 0.12)
    media("psx", "art_psx", 1.6, -8.0, -57, 0.12)
    media("vita", "art_vita", 0.0, -4.65, 8 + TILT, 0.28, rx=-6, rz=1.5, gloss=g)

    Image.fromarray((np.clip(canvas, 0, 1) * 255).astype(np.uint8)).save(OUT)
    print("ok", OUT)
