"""Generate floating physical-media models + textures for RetroFlow.

Output matches the OBJ subset lpp-vita's Render.loadObject reads: `v`, `vt`,
`vn`, then triangular `f v/vt/vn` faces. Vertices are shared (keyed on
position + uv) because the loader resolves each face index by walking a
linked list.

The Vita has no depth test, so every piece of media is split into LAYERS,
written as separate OBJs, that index.lua draws back-to-front, or front-to-back
when the item has been spun round to show its back:

    vita : back, band, front, label*, bands, gloss*
    psx  : back, band, front, label*, gloss*
    psp  : backrefl*, slotrim, backglass, shellback, discback, band, label*, hub, shellfront, refl*, gloss*
    (* label is textured with the cover art, gloss only on the selected item)

All models are centred on the origin. Units match RetroFlow's covers.
"""
import json
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

OUT = sys.argv[1] if len(sys.argv) > 1 else "out/DATA"
os.makedirs(OUT, exist_ok=True)
GLOSS_STEPS = 24
TEX = 512


# --------------------------------------------------------------------------- OBJ
class Mesh:
    def __init__(self):
        self.v, self.vt, self.f, self._idx = [], [], [], {}

    def tri(self, a, b, c):
        """a, b, c = ((x, y, z), (u, row)) with row measured from the image top."""
        idx = []
        for p, (u, r) in (a, b, c):
            key = tuple(round(k, 4) for k in (*p, u, 1.0 - r))
            i = self._idx.get(key)
            if i is None:
                self.v.append(p)
                self.vt.append((u, 1.0 - r))  # the loader flips v back
                i = len(self.v)
                self._idx[key] = i
            idx.append(i)
        self.f.append(idx)

    def quad(self, a, b, c, d):
        self.tri(a, b, c)
        self.tri(a, c, d)

    def write(self, name):
        with open(f"{OUT}/{name}.obj", "w") as fh:
            fh.write("\n")
            for x, y, z in self.v:
                fh.write(f"v {x:.4f} {y:.4f} {z:.4f}\n")
            fh.write("\n")
            for u, v in self.vt:
                fh.write(f"vt {u:.4f} {v:.4f}\n")
            fh.write("\nvn 0 0 1.0\n\n")
            for a, b, c in self.f:
                fh.write(f"f {a}/{a}/1 {b}/{b}/1 {c}/{c}/1\n")
        return self


def bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def arc(cx, cy, r, a0, a1, n=6):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def circle(r, seg, cy=0.0):
    return [(r * math.cos(2 * math.pi * i / seg), cy + r * math.sin(2 * math.pi * i / seg)) for i in range(seg)]


def planar(bb, u0=0.0, uw=1.0, r0=0.0, rh=1.0, mirror=False):
    """Map model (x, y) inside bb onto an atlas region (u0..u0+uw, r0..r0+rh)."""
    xmin, ymin, xmax, ymax = bb
    w, h = xmax - xmin, ymax - ymin

    def f(x, y):
        fx = (xmax - x) / w if mirror else (x - xmin) / w
        return (u0 + fx * uw, r0 + (ymax - y) / h * rh)
    return f


def fan(mesh, poly, z, uvf):
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    c = ((cx, cy, z), uvf(cx, cy))
    for i in range(len(poly)):
        p0, p1 = poly[i], poly[(i + 1) % len(poly)]
        mesh.tri(c, ((p0[0], p0[1], z), uvf(*p0)), ((p1[0], p1[1], z), uvf(*p1)))


def ring(mesh, r_in, r_out, z, uvf, seg=48):
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        pts = [(r_in * math.cos(a0), r_in * math.sin(a0)), (r_out * math.cos(a0), r_out * math.sin(a0)),
               (r_out * math.cos(a1), r_out * math.sin(a1)), (r_in * math.cos(a1), r_in * math.sin(a1))]
        mesh.quad(*[((x, y, z), uvf(x, y)) for x, y in pts])


def ray_hit(outline, ang):
    """Distance from the origin to a star-shaped outline along angle ang."""
    dx, dy = math.cos(ang), math.sin(ang)
    best = None
    n = len(outline)
    for i in range(n):
        (x0, y0), (x1, y1) = outline[i], outline[(i + 1) % n]
        ex, ey = x1 - x0, y1 - y0
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = (x0 * ey - y0 * ex) / den
        s = (x0 * dy - y0 * dx) / den
        if t > 0 and -1e-9 <= s <= 1 + 1e-9 and (best is None or t < best):
            best = t
    return best


def ring_to_outline(mesh, r_in, outline, z, uvf, seg=96):
    """Annulus between a circle and a star-shaped outline (both around origin)."""
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        t0, t1 = ray_hit(outline, a0), ray_hit(outline, a1)
        pts = [(r_in * math.cos(a0), r_in * math.sin(a0)), (t0 * math.cos(a0), t0 * math.sin(a0)),
               (t1 * math.cos(a1), t1 * math.sin(a1)), (r_in * math.cos(a1), r_in * math.sin(a1))]
        mesh.quad(*[((x, y, z), uvf(x, y)) for x, y in pts])


def band(mesh, poly, z0, z1, u0=0.0, uw=1.0, r0=0.55, r1=0.95):
    """Side walls of an outline mapped onto a strip of the atlas."""
    per = [0.0]
    n = len(poly)
    for i in range(n):
        per.append(per[-1] + math.dist(poly[i], poly[(i + 1) % n]))
    tot = per[-1] or 1
    for i in range(n):
        p0, p1 = poly[i], poly[(i + 1) % n]
        s0, s1 = u0 + uw * per[i] / tot, u0 + uw * per[i + 1] / tot
        mesh.quad(((p0[0], p0[1], z0), (s0, r1)), ((p1[0], p1[1], z0), (s1, r1)),
                  ((p1[0], p1[1], z1), (s1, r0)), ((p0[0], p0[1], z1), (s0, r0)))


def split_at_y(poly, y, side):
    """Insert vertices where the outline crosses height y on the given side (+1 right, -1 left)."""
    out = []
    n = len(poly)
    for i in range(n):
        p0, p1 = poly[i], poly[(i + 1) % n]
        out.append(p0)
        if (p0[1] - y) * (p1[1] - y) < 0 and (p0[0] + p1[0]) * side > 0:
            t = (y - p0[1]) / (p1[1] - p0[1])
            out.append((p0[0] + (p1[0] - p0[0]) * t, y))
    return out


def band_sides(prefix, poly, z0, z1, u0=0.0, uw=1.0, r0=0.55, r1=0.95, z0f=None):
    """band() split into four meshes by the side each edge faces (R/T/L/B), so the Lua can
    draw the near side last (covering the inside) and the far sides in place.
    z0f(mx, my) -> back z for that edge (a notch where a pocket cuts through the rim)."""
    per = [0.0]
    n = len(poly)
    for i in range(n):
        per.append(per[-1] + math.dist(poly[i], poly[(i + 1) % n]))
    tot = per[-1] or 1
    meshes = {k: Mesh() for k in "RTLB"}
    for i in range(n):
        p0, p1 = poly[i], poly[(i + 1) % n]
        mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
        side = ("R" if mx > 0 else "L") if abs(mx) > abs(my) else ("T" if my > 0 else "B")
        s0, s1 = u0 + uw * per[i] / tot, u0 + uw * per[i + 1] / tot
        zz = z0f(mx, my) if z0f else z0
        meshes[side].quad(((p0[0], p0[1], zz), (s0, r1)), ((p1[0], p1[1], zz), (s1, r1)),
                          ((p1[0], p1[1], z1), (s1, r0)), ((p0[0], p0[1], z1), (s0, r0)))
    for k, m in meshes.items():
        m.write(f"{prefix}{k}")


def gloss_set(name, make_ring):
    for i in range(GLOSS_STEPS):
        s = 0.5 * i / (GLOSS_STEPS - 1)
        g = Mesh()
        make_ring(g, s)
        g.write(f"{name}_gloss{i}")


# ---------------------------------------------------------------------- texture helpers
def px_of(bb, u0, r0, uw, rh, mirror=False):
    xmin, ymin, xmax, ymax = bb

    def px(x, y):
        fx = (xmax - x) / (xmax - xmin) if mirror else (x - xmin) / (xmax - xmin)
        return ((u0 + fx * uw) * TEX, (r0 + (ymax - y) / (ymax - ymin) * rh) * TEX)
    return px


def vgrad(d, box, top, bot):
    x0, y0, x1, y1 = [int(v) for v in box]
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0 - 1)
        d.line([(x0, y), (x1, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))


def side_strip(d, base, u0=0.0, uw=1.0, r0=0.55, r1=0.95):
    for r in range(int(r0 * TEX) - 2, int(r1 * TEX) + 2):
        t = (r - r0 * TEX) / ((r1 - r0) * TEX)
        k = 1.0 - abs(t - 0.4) * 0.9
        d.line([(u0 * TEX, r), ((u0 + uw) * TEX, r)], fill=tuple(int(min(255, c * (0.7 + 0.6 * k))) for c in base) + (255,))


def side_strip_lit(d, img, base, outline, u0, uw, r0, r1, light=(-0.6, 0.8), grain=18, seam=True):
    """Band texture that follows the outline: columns map to perimeter position (as band_sides
    does), lit by the edge's outward normal (light from the top left), with a faint parting
    line between the two shell halves, bright bevel seams and the same grain as the faces."""
    import numpy as np
    x0, x1 = int(u0 * TEX), int((u0 + uw) * TEX)
    y0, y1 = int(r0 * TEX) - 2, int(r1 * TEX) + 2
    w, h = x1 - x0, y1 - y0
    n = len(outline)
    per = [0.0]
    for i in range(n):
        per.append(per[-1] + math.dist(outline[i], outline[(i + 1) % n]))
    tot = per[-1] or 1
    # outward normal per edge -> lighting per perimeter position
    cx = sum(p[0] for p in outline) / n
    cy = sum(p[1] for p in outline) / n
    lit = np.ones(w, np.float32)
    for i in range(n):
        p0, p1 = outline[i], outline[(i + 1) % n]
        ex, ey = p1[0] - p0[0], p1[1] - p0[1]
        nx, ny = ey, -ex
        if (nx * (p0[0] - cx) + ny * (p0[1] - cy)) < 0:
            nx, ny = -nx, -ny
        ln = math.hypot(nx, ny) or 1
        f = (nx * light[0] + ny * light[1]) / ln / math.hypot(*light)
        a, b = int(per[i] / tot * w), max(int(per[i] / tot * w) + 1, int(per[i + 1] / tot * w))
        lit[a:b] = 1.0 + 0.28 * f
    lit = np.convolve(np.pad(lit, 3, mode="wrap"), np.ones(7) / 7, mode="same")[3:-3]
    v = (np.arange(h, dtype=np.float32) - 2) / max(1, h - 5)          # 0 at one face edge, 1 at the other
    prof = 0.86 + 0.22 * np.sqrt(np.maximum(np.sin(np.clip(v, 0, 1).astype(np.float64) * math.pi), 0.0))   # rounded: brighter in the middle
    prof = prof - 0.16 * np.exp(-((v - 0.5) / 0.035) ** 2) * (1 if seam else 0)   # parting line
    prof[:3] *= 1.08; prof[-3:] *= 1.08                                 # lit seams at the bevels
    tone = np.array(base, np.float32)[None, None, :] * (prof[:, None, None] * lit[None, :, None])
    noise = np.random.default_rng(3).normal(0, grain, (h, w, 1))
    arr = np.clip(tone + noise, 0, 255).astype(np.uint8)
    img.paste(Image.fromarray(np.concatenate([arr, np.full((h, w, 1), 255, np.uint8)], -1), "RGBA"), (x0, y0))


def radial(d, px, cx, cy, R, scale, shade):
    """Concentric fill: shade(rr) -> colour for radius rr (model units)."""
    c = px(cx, cy)
    for r in range(int(R * scale), 0, -1):
        col = shade(r / scale)
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=col + (255,))


def data_side(rr, R, hub):
    """Colour of a CD/UMD data side at radius rr: silver with a faint hue sweep."""
    if rr < hub:
        return (210, 214, 220)
    t = (rr - hub) / (R - hub)
    base = 128 + int(34 * t) + int(10 * math.sin(t * 5.0) ** 2)
    col = (base + 4, base + 2, base + 10)
    if 0.17 < t < 0.20:  # dark ring where the data area starts
        col = tuple(v - 40 for v in col)
    return tuple(max(0, min(255, v)) for v in col)


def rainbow_sweep(img, box, ang_deg):
    """Soft diagonal hue streak composited inside box (gives a disc its sheen)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    cols = [(255, 90, 90), (255, 200, 60), (120, 255, 120), (90, 160, 255), (190, 110, 255)]
    for i, col in enumerate(cols):
        off = (i - 2) * w * 0.07
        ld.polygon([(w * 0.35 + off, 0), (w * 0.47 + off, 0), (w * 0.65 + off, h), (w * 0.53 + off, h)], fill=col + (38,))
    layer = layer.rotate(ang_deg, resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(10))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, w - 1, h - 1], fill=255)
    region = img.crop(box)
    region.alpha_composite(Image.composite(layer, Image.new("RGBA", (w, h), (0, 0, 0, 0)), mask))
    img.paste(region, (x0, y0))


def x_crossings(poly, y):
    xs = []
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if (y0 > y) != (y1 > y):
            xs.append(x0 + (x1 - x0) * (y - y0) / (y1 - y0))
    return sorted(xs)


def face_with_holes(mesh, outline, holes, z, uvf):
    """A flat face with real holes (the Vita depth-tests, so pockets must be open in the
    face): the face is cut into horizontal slabs at every vertex height and each slab is
    filled with quads between the outline and the hole intervals."""
    def free(y):
        xs = x_crossings(outline, y)
        segs = [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]
        cuts = []
        for h in holes:
            hx = x_crossings(h, y)
            cuts += [(hx[i], hx[i + 1]) for i in range(0, len(hx) - 1, 2)]
        cuts.sort()
        out = []
        for a, b in segs:
            cur = a
            for c0, c1 in cuts:
                if c1 <= cur or c0 >= b:
                    continue
                if c0 > cur:
                    out.append((cur, c0))
                cur = max(cur, c1)
            if cur < b - 1e-9:
                out.append((cur, b))
        return out

    def slab(y0, y1, depth=0):
        eps = (y1 - y0) * 1e-3
        f0, f1 = free(y0 + eps), free(y1 - eps)
        if len(f0) != len(f1):
            if depth < 4:
                ym = (y0 + y1) / 2
                slab(y0, ym, depth + 1)
                slab(ym, y1, depth + 1)
            return
        for (a0, b0), (a1, b1) in zip(f0, f1):
            mesh.quad(((a0, y0, z), uvf(a0, y0)), ((b0, y0, z), uvf(b0, y0)), ((b1, y1, z), uvf(b1, y1)), ((a1, y1, z), uvf(a1, y1)))
    ys = sorted(set([p[1] for p in outline] + [p[1] for h in holes for p in h]))
    for y0, y1 in zip(ys, ys[1:]):
        if y1 - y0 > 1e-6:
            slab(y0, y1)


def bake_light(img, box, layers, light=(-0.7, -0.75), k=14.0, ao=0.55, blur=2.5, ao_blur=14):
    """Fake lighting for a flat atlas region: layers = [(pixel polygon, height 0..1), ...] drawn
    in order (0 outside the part). Edges whose downhill side faces the light brighten, the
    others darken (rolled edges, lit lips), and low areas next to higher ones get ambient
    occlusion (pocket floors, the well). Multiplies the region's colours in place."""
    import numpy as np
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    hm = Image.new("L", (w, h), 0)
    hd = ImageDraw.Draw(hm)
    for poly, height in layers:
        hd.polygon([(x - x0, y - y0) for x, y in poly], fill=int(255 * height))
    H = np.asarray(hm).astype(np.float32) / 255.0
    Hb = np.asarray(hm.filter(ImageFilter.GaussianBlur(blur))).astype(np.float32) / 255.0
    gy, gx = np.gradient(Hb)
    lx, ly = light
    n = math.hypot(lx, ly)
    lit = -(gx * lx + gy * ly) / n * k          # downhill towards the light = lit
    Hw = np.asarray(hm.filter(ImageFilter.GaussianBlur(ao_blur))).astype(np.float32) / 255.0
    occ = np.clip(Hw - H, 0, 1) * ao            # lower than the neighbourhood = in shadow
    inside = H > 0.01
    mul = np.where(inside, np.clip(1.0 + lit - occ, 0.35, 1.6), 1.0)
    region = np.asarray(img.crop(box)).astype(np.float32)
    region[..., :3] = np.clip(region[..., :3] * mul[..., None], 0, 255)
    img.paste(Image.fromarray(region.astype(np.uint8), "RGBA"), (x0, y0))


def grime_texture():
    """Wear overlay atlas (RGBA, alpha only where there is something): four 512 regions,
    vita front / vita back / psp front / psp back. Dust and lint, a couple of faint
    fingerprints, hairline scuffs, lighter wear along the edges, dark grime in corners."""
    import numpy as np
    n = 1024
    rng = np.random.default_rng(11)
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    for ri, (ox, oy) in enumerate(((0, 0), (512, 0), (0, 512), (512, 512))):
        layer = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        # fingerprints: concentric ridge arcs inside an ellipse, very faint, lighter (skin oil)
        for _ in range(2):
            cx, cy = rng.uniform(120, 392), rng.uniform(120, 392)
            rx, ry = rng.uniform(55, 80), rng.uniform(70, 100)
            ang = rng.uniform(0, 180)
            fp = Image.new("RGBA", (int(rx * 2.4), int(ry * 2.4)), (0, 0, 0, 0))
            fd = ImageDraw.Draw(fp)
            c = (fp.size[0] / 2, fp.size[1] / 2)
            for k in range(2, int(min(rx, ry) / 3)):
                r = k * 3.0 + rng.uniform(-0.6, 0.6)
                e = min(1.0, (1 - r / min(rx, ry)) * 1.4)
                a = int(28 * max(0.0, e) * rng.uniform(0.4, 1.0))
                fd.ellipse([c[0] - r * rx / min(rx, ry), c[1] - r * ry / min(rx, ry), c[0] + r * rx / min(rx, ry), c[1] + r * ry / min(rx, ry)],
                           outline=(235, 235, 240, a), width=1)
            fp = fp.rotate(ang, resample=Image.BICUBIC, expand=True).filter(ImageFilter.GaussianBlur(0.6))
            layer.alpha_composite(fp, (int(cx - fp.size[0] / 2), int(cy - fp.size[1] / 2)))
        # hairline scuffs
        for _ in range(int(rng.integers(14, 24))):
            x0, y0 = rng.uniform(20, 492), rng.uniform(20, 492)
            ln, an = rng.uniform(8, 60), rng.uniform(0, math.pi)
            a = int(rng.uniform(40, 110))
            d.line([(x0, y0), (x0 + ln * math.cos(an), y0 + ln * math.sin(an))], fill=(250, 250, 252, a), width=1)
        for _ in range(3):   # a few longer, fainter
            x0, y0 = rng.uniform(40, 472), rng.uniform(40, 472)
            ln, an = rng.uniform(80, 200), rng.uniform(0, math.pi)
            d.line([(x0, y0), (x0 + ln * math.cos(an), y0 + ln * math.sin(an))], fill=(250, 250, 252, 45), width=1)
        # dust and lint
        for _ in range(int(rng.integers(90, 140))):
            x, y = rng.uniform(0, 512), rng.uniform(0, 512)
            r = rng.uniform(0.5, 1.6)
            a = int(rng.uniform(70, 170))
            col = (240, 240, 244, a) if rng.random() < 0.7 else (60, 58, 56, a // 2)
            d.ellipse([x - r, y - r, x + r, y + r], fill=col)
        for _ in range(6):   # lint fibres
            x0, y0 = rng.uniform(0, 512), rng.uniform(0, 512)
            pts = [(x0, y0)]
            for k in range(5):
                pts.append((pts[-1][0] + rng.uniform(-6, 6), pts[-1][1] + rng.uniform(-6, 6)))
            d.line(pts, fill=(245, 245, 248, 120), width=1)
        # dark grime drifting in from the corners, lighter wear along the edges
        gr = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        gd = ImageDraw.Draw(gr)
        for (cx, cy) in ((0, 0), (512, 0), (0, 512), (512, 512)):
            gd.ellipse([cx - 90, cy - 90, cx + 90, cy + 90], fill=(40, 36, 30, 34))
        gr = gr.filter(ImageFilter.GaussianBlur(26))
        wear = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        ImageDraw.Draw(wear).rectangle([6, 6, 505, 505], outline=(255, 255, 255, 26), width=10)
        wear = wear.filter(ImageFilter.GaussianBlur(7))
        layer.alpha_composite(gr)
        layer.alpha_composite(wear)
        img.alpha_composite(layer, (ox, oy))
    img.save(f"{OUT}/float_grime.png")


def grime_meshes(kind, outline, bb, zf, zb, region_front, region_back):
    """Front and back wear overlays as outline fans, mapped to their grime atlas regions."""
    m = Mesh(); fan(m, outline, zf + 0.005, planar(bb, *region_front)); m.write(f"float_{kind}_grime")
    m = Mesh(); fan(m, outline, zb - 0.005, planar(bb, *region_back, mirror=True)); m.write(f"float_{kind}_backgrime")


# ================================================================== PS VITA CARD (traced)
VTRACE = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "vita_trace.json")))
VT = dict(shell=40, well=41, white=42, blue=43, art=44, arrow=45, psvita=range(46, 52),
          back=0, chan_l=1, chan_r=2, pins=range(3, 13), edge_circle=13, logo=(14, 15, 16),
          made_in=range(17, 28), ce=(28, 29), gold=range(30, 40))
VT_FC, VT_FCY, VT_BC, VT_BCY = 449.346, 201.265, 155.743, 199.965   # trace centres: front x,y / back x,y


def vtrace_polys(i, to_model, steps=10):
    return [clean_poly(p) for p in trace_polys_from(VTRACE, i, to_model, steps)]


def trace_polys_from(table, i, to_model, steps=10):
    global TRACE
    saved = TRACE
    TRACE = table
    try:
        return trace_polys(i, to_model, steps)
    finally:
        TRACE = saved


def vtrace_fill(img, ids, to_model, px, colour, ss=4):
    global TRACE
    saved = TRACE
    TRACE = VTRACE
    try:
        trace_fill(img, ids, to_model, px, colour, ss)
    finally:
        TRACE = saved


def split_by_polys(outline, polys):
    """Insert outline vertices wherever a pocket edge crosses it (so bands can be notched exactly)."""
    out = list(outline)
    for poly in polys:
        n = len(poly)
        for k in range(n):
            (ax, ay), (bx, by) = poly[k], poly[(k + 1) % n]
            new = []
            m = len(out)
            for i in range(m):
                (px0, py0), (px1, py1) = out[i], out[(i + 1) % m]
                new.append(out[i])
                den = (px1 - px0) * (by - ay) - (py1 - py0) * (bx - ax)
                if abs(den) < 1e-12:
                    continue
                t = ((ax - px0) * (by - ay) - (ay - py0) * (bx - ax)) / den
                u = ((ax - px0) * (py1 - py0) - (ay - py0) * (px1 - px0)) / den
                if 1e-4 < t < 1 - 1e-4 and -1e-4 <= u <= 1 + 1e-4:
                    new.append((px0 + (px1 - px0) * t, py0 + (py1 - py0) * t))
            out = new
    return out


def point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % n]
        if (y0 > y) != (y1 > y) and x < x0 + (x1 - x0) * (y - y0) / (y1 - y0):
            inside = not inside
    return inside


def dist_to_outline(x, y, outline):
    best = 1e9
    n = len(outline)
    for i in range(n):
        (x0, y0), (x1, y1) = outline[i], outline[(i + 1) % n]
        dx, dy = x1 - x0, y1 - y0
        t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / (dx * dx + dy * dy or 1e-12)))
        best = min(best, math.hypot(x - x0 - dx * t, y - y0 - dy * t))
    return best


def mask_quads(mesh, polys, z, uvf, cell):
    """Rasterise polygons (even-odd) onto a grid and emit one quad per filled cell: a mesh
    for glyph-like shapes with holes without a triangulator. Vertices are shared."""
    from PIL import ImageChops
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    nx, ny = int(math.ceil((x1 - x0) / cell)) + 1, int(math.ceil((y1 - y0) / cell)) + 1
    mask = Image.new("1", (nx, ny), 0)
    for poly in polys:
        m = Image.new("1", (nx, ny), 0)
        ImageDraw.Draw(m).polygon([((x - x0) / cell, (y1 - y) / cell) for x, y in poly], fill=1)
        mask = ImageChops.logical_xor(mask, m)
    px = mask.load()
    for j in range(ny):
        for i in range(nx):
            if px[i, j]:
                ax, ay = x0 + i * cell, y1 - (j + 1) * cell
                bx, by = ax + cell, ay + cell
                mesh.quad(((ax, ay, z), uvf(ax, ay)), ((bx, ay, z), uvf(bx, ay)), ((bx, by, z), uvf(bx, by)), ((ax, by, z), uvf(ax, by)))


def vita_card():
    """PS Vita game card from the trace: domed top, small side step notches, chamfered
    bottom-left corner. Front: matte black shell, a shallow well holding the sticker (domed
    blue PS VITA band, cover art, white serial band), glossy black arrow. Back: two deep
    side channels, ten pin slots with gold contacts and a half-round edge cut (all open at
    the bottom edge), glossy black PlayStation logo, CE and MADE IN CHINA that catch the light."""
    global TEX
    W, T = 1.36, 0.11
    K = W / 254.34                         # trace pt -> model units (front shell width)
    front = lambda X, Y: ((X - VT_FC) * K, (VT_FCY - Y) * K)
    back = lambda X, Y: ((VT_BC - X) * K, (VT_BCY - Y) * K)   # seen from the back: trace-left is model +x
    depth, well_d = 0.05, 0.010
    zf, zb = T / 2, -T / 2
    zfloor, zwell = zb + depth, zf - well_d
    poly_area = lambda p: abs(sum(p[i][0] * p[(i + 1) % len(p)][1] - p[(i + 1) % len(p)][0] * p[i][1] for i in range(len(p))))
    outline = clean_poly(max(vtrace_polys(VT["shell"], front, steps=14), key=poly_area))   # the drawing also holds the band cut-outs
    pockets = ([vtrace_polys(VT["chan_l"], back)[0], vtrace_polys(VT["chan_r"], back)[0], vtrace_polys(VT["edge_circle"], back)[0]]
               + [vtrace_polys(i, back)[0] for i in VT["pins"]])
    outline = split_by_polys(outline, pockets)
    bb = bbox(outline)
    well = max(vtrace_polys(VT["well"], front), key=poly_area)   # the outer rounded rect, not the band shapes
    blue = vtrace_polys(VT["blue"], front)[0]
    white = vtrace_polys(VT["white"], front)[0]
    art = vtrace_polys(VT["art"], front)[0]
    arrow = vtrace_polys(VT["arrow"], front)[0]
    # sticker = blue band's dome + straight sides + white band's rounded bottom
    abb = bbox(art)
    sticker = ([p for p in white if p[1] < abb[1] + 1e-4] + [p for p in blue if p[1] > abb[3] - 1e-4])
    sticker = clean_poly(sorted(sticker, key=lambda p: math.atan2(p[1], p[0])))
    sbb = bbox(sticker)
    A = planar(bb, 0, 0.5, 0, 0.5)
    Ab = planar(bb, 0.5, 0.5, 0, 0.5, mirror=True)
    wall_uv = (0.55, 0.65, 0.988, 0.962)   # rows 0.96..0.99 of the atlas: light at the face edge, dark at the floor
    open_edge = lambda p, q: dist_to_outline((p[0] + q[0]) / 2, (p[1] + q[1]) / 2, outline) < 0.004
    in_pocket = lambda mx, my: any(point_in_poly(mx, my, poly) for poly in pockets)

    # --- back
    def marks(g, s):
        uv = planar(bb, s, 0.5, 0, 1, mirror=True)
        polys = [q for i in list(VT["logo"]) + list(VT["made_in"]) + list(VT["ce"]) for q in vtrace_polys(i, back)]
        mask_quads(g, polys, zb - 0.002, uv, 0.012)
    variant_set("float_vita_backgloss_marks", marks)
    def pins(g, s):                        # the gold contacts catch the light too
        uv = planar(bb, s, 0.5, 0, 1, mirror=True)
        for i in VT["gold"]:
            fan(g, vtrace_polys(i, back)[0], zfloor - 0.001, uv)
    variant_set("float_vita_backgloss_pins", pins)
    m_ = Mesh()
    for poly in pockets:
        fan(m_, poly, zfloor, Ab)
    m_.write("float_vita_backcuts")
    m_ = Mesh(); face_with_holes(m_, outline, pockets, zb, Ab); m_.write("float_vita_back")
    walls = {"R": Mesh(), "T": Mesh(), "L": Mesh(), "B": Mesh()}
    for poly in pockets:
        cx_ = sum(p[0] for p in poly) / len(poly)
        cy_ = sum(p[1] for p in poly) / len(poly)
        for i in range(len(poly)):
            p0, p1 = poly[i], poly[(i + 1) % len(poly)]
            if open_edge(p0, p1):
                continue
            mx, my = (p0[0] + p1[0]) / 2 - cx_, (p0[1] + p1[1]) / 2 - cy_
            side = ("R" if mx > 0 else "L") if abs(mx) > abs(my) else ("T" if my > 0 else "B")
            walls[side].quad(((p0[0], p0[1], zb), (wall_uv[0], wall_uv[2])), ((p1[0], p1[1], zb), (wall_uv[1], wall_uv[2])),
                             ((p1[0], p1[1], zfloor), (wall_uv[1], wall_uv[3])), ((p0[0], p0[1], zfloor), (wall_uv[0], wall_uv[3])))
    for k, g in walls.items():
        g.write(f"float_vita_wallback{k}")
    # --- side, notched where the channels open onto the edge
    band_sides("float_vita_band", outline, zb, zf, 0.5, 0.5, 0.55, 0.95,
               z0f=lambda mx, my: zfloor if in_pocket(mx, my) else zb)
    # --- front
    m_ = Mesh(); face_with_holes(m_, outline, [well], zf, A); m_.write("float_vita_front")
    m_ = Mesh(); fan(m_, well, zwell, A); band(m_, well, zwell, zf, 0.5, 0.5, 0.988, 0.962); m_.write("float_vita_well")
    grime_meshes("vita", outline, bb, zf, zb, (0, 0.5, 0, 0.5), (0.5, 0.5, 0, 0.5))
    # label: the cover art, cropped to the art window's aspect (covers are taller than wide)
    aw, ah = abb[2] - abb[0], abb[3] - abb[1]
    cover_aspect = 250 / 320
    vis = min(1.0, (ah / aw) * cover_aspect)       # full cover width, so only this fraction of its height shows (no squash)
    top = min(0.13, 1 - vis)                       # cover scans carry their own PS VITA strip: crop it off the top
    m_ = Mesh(); fan(m_, art, zwell + 0.003, planar(abb, 0, 1, top, vis)); m_.write("float_vita_label")
    m_ = Mesh()
    bbb, wbb_ = bbox(blue), bbox(white)
    fan(m_, blue, zwell + 0.004, planar(bbb, 0, 0.5, 0.56, 0.11))
    fan(m_, white, zwell + 0.004, planar(wbb_, 0, 0.5, 0.70, 0.045))
    m_.write("float_vita_bands")
    variant_set("float_vita_gloss_arrow", lambda g, s: fan(g, arrow, zf + 0.002, planar(bb, s, 0.5, 0, 1)))
    shine_quad("vita", sbb[2] - sbb[0], sbb[1] + 0.01, sbb[3] - 0.10, zwell + 0.008)   # drifts 0.10 (index.lua FLOAT.SHINE)
    shadow_set("vita", outline, T)

    # ---------------------------------------------------------------- texture (1024 atlas)
    TEX = 1024
    img = Image.new("RGBA", (TEX, TEX), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    scale = (TEX * 0.5) / (bb[2] - bb[0])
    pf = px_of(bb, 0, 0, 0.5, 0.5)
    pb = px_of(bb, 0.5, 0, 0.5, 0.5, mirror=True)
    # front: matte black, the well a touch darker with a shadow along its upper edge
    vgrad(d, (0, 0, TEX // 2, TEX // 2), (58, 60, 68), (34, 35, 39))
    d.polygon([pf(*p) for p in well], fill=(30, 31, 35, 255))
    d.line([pf(*p) for p in well] + [pf(*well[0])], fill=(14, 15, 18, 255), width=4)
    d.line([(x, y + 3) for x, y in [pf(*p) for p in well]] + [pf(*well[0])], fill=(74, 76, 86, 255), width=2)
    d.line([pf(*p) for p in outline] + [pf(*outline[0])], fill=(110, 113, 122, 255), width=3)
    # back: matte black, channel floors darker with a shadow under the walls, gold pins, glossy marks
    vgrad(d, (TEX // 2, 0, TEX, TEX // 2), (62, 64, 72), (30, 31, 35))
    for poly in pockets:
        pts = [pb(*p) for p in poly] + [pb(*poly[0])]
        d.polygon(pts, fill=(22, 23, 27, 255))
        d.line(pts, fill=(8, 8, 10, 255), width=4)
    for i in VT["gold"]:
        vtrace_fill(img, i, back, pb, (201, 164, 82))
        poly = vtrace_polys(i, back)[0]
        gb = bbox(poly)
        d.line([pb(gb[0], gb[3]), pb(gb[2], gb[3])], fill=(245, 214, 140, 255), width=2)
    for off, col in ((2, (96, 98, 108)), (0, (10, 11, 14))):     # moulded glossy black: lit edge, then the dark face
        shifted = lambda x, y, o=off: (pb(x, y)[0] + o, pb(x, y)[1] + o)
        vtrace_fill(img, list(VT["logo"]) + list(VT["made_in"]) + list(VT["ce"]), back, shifted, col)
    d.text(pb(0, -0.40), "VCDA A6307DF6MM2UF", fill=(72, 74, 82, 255), font=font(int(0.05 * scale), weight="regular"), anchor="mm")   # serial between MADE IN CHINA and the pins
    d.line([pb(*p) for p in outline] + [pb(*outline[0])], fill=(95, 98, 106, 255), width=3)
    bake_light(img, (0, 0, TEX // 2, TEX // 2), [([pf(*q) for q in outline], 1.0), ([pf(*q) for q in well], 0.86)], k=16.0, ao=0.6, blur=3)
    bake_light(img, (TEX // 2, 0, TEX, TEX // 2), [([pb(*q) for q in outline], 1.0)] + [([pb(*q) for q in poly], 0.5) for poly in pockets]
               + [([pb(*q) for q in vtrace_polys(i, back)[0]], 0.72) for i in VT["gold"]], k=16.0, ao=0.7, blur=3)
    noise = Image.effect_noise((TEX // 2, TEX // 2), 22).convert("L")   # fine speckle of the moulded plastic
    grain = Image.merge("RGBA", (noise, noise, noise, Image.new("L", noise.size, 40)))
    for ox in (0, TEX // 2):
        region = img.crop((ox, 0, ox + TEX // 2, TEX // 2)); region.alpha_composite(grain); img.paste(region, (ox, 0))
    d = ImageDraw.Draw(img)
    tri = [pf(*p) for p in arrow]                                          # glossy black arrow on the front
    d.polygon(tri, fill=(8, 9, 12, 255))
    d.line([tri[0], tri[1]], fill=(150, 154, 164, 255), width=2)
    # bands: blue PS VITA strip (rows 0.56..0.67) with the traced white wordmark, white serial strip (0.70..0.745)
    bx0, by0, bx1, by1 = 0, int(0.56 * TEX), TEX // 2, int(0.67 * TEX)
    vgrad(d, (bx0, by0, bx1, by1), (18, 92, 170), (10, 70, 150))
    for k, col in enumerate(((225, 236, 250), (150, 185, 230), (70, 125, 200), (34, 100, 180))):   # the sticker's top edge catches the light
        d.line([(bx0, by0 + k), (bx1, by0 + k)], fill=col + (255,))
    vtrace_fill(img, VT["psvita"], front, px_of(bbb, 0, 0.56, 0.5, 0.11), (246, 248, 252))
    wy0, wy1 = int(0.70 * TEX), int(0.745 * TEX)
    d.rectangle([bx0, wy0, bx1, wy1], fill=(241, 242, 245, 255))
    d.text(((bx0 + bx1) / 2, (wy0 + wy1) / 2), "VCJS-10007", fill=(40, 42, 48, 255), font=font(int((wy1 - wy0) * 0.6), weight="regular"), anchor="mm")
    side_strip_lit(d, img, (58, 60, 68), outline, 0.5, 0.5, 0.55, 0.95, grain=9)
    for r in range(int(0.96 * TEX) - 2, int(0.99 * TEX) + 3):           # pocket wall rows: dark floor edge -> lit face edge
        t = min(1.0, max(0.0, (r - 0.96 * TEX) / (0.03 * TEX)))
        v = int(14 + 70 * t)
        d.line([(TEX // 2, r), (TEX, r)], fill=(v, v, v + 4, 255))
    img.save(f"{OUT}/float_vita_body.png")
    TEX = 512
    return (bb[3] - bb[1]) / 2, -(bb[1] + bb[3]) / 2


# ================================================================== PS1 DISC
def ps1_disc():
    R, r_hole, T, seg = 0.925, 0.115, 0.03, 48
    r_li, r_lo = 0.33 * R, 0.975 * R
    bb = (-R, -R, R, R)
    m = Mesh(); ring(m, r_hole, R, -T / 2, planar(bb, 0.5, 0.5, 0, 0.5, mirror=True), seg); m.write("float_psx_back")
    m = Mesh(); ring(m, 0.3 * R, R, -T / 2 - 0.004, planar(bb), seg); m.write("float_psx_sheen")
    m = Mesh(); band(m, circle(R, seg), -T / 2, T / 2, 0, 1); band(m, circle(r_hole, seg), -T / 2, T / 2, 0, 1)
    m.write("float_psx_band")
    m = Mesh(); ring(m, r_hole, R, T / 2, planar(bb, 0, 0.5, 0, 0.5), seg); m.write("float_psx_front")
    m = Mesh(); ring(m, r_li, r_lo, T / 2 + 0.003, planar(bb), seg); m.write("float_psx_label")
    shine_quad("psx", 1.30, -0.66, 0.66, T / 2 + 0.007)   # drifts 0.14
    shadow_set("psx", circle(R, 64), T)

    img = Image.new("RGBA", (TEX, TEX), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    scale = (TEX * 0.5) / (2 * R)
    pf = px_of(bb, 0, 0, 0.5, 0.5)

    def front(rr):
        if rr > r_lo: return (196, 202, 210)
        if rr > r_li: return (170, 174, 182)
        if rr > 0.30 * R: return (214, 218, 224)
        if rr > 0.24 * R: return (150, 156, 166)
        return (226, 230, 236)
    radial(d, pf, 0, 0, R, scale, front)
    # back: PlayStation-style black data side with a sheen
    pb = px_of(bb, 0.5, 0, 0.5, 0.5, mirror=True)
    radial(d, pb, 0, 0, R, scale, lambda rr: (18 + int(22 * math.sin(rr / R * math.pi) ** 2),) * 2 + (24 + int(22 * math.sin(rr / R * math.pi) ** 2),) if rr > 0.3 * R else (200, 205, 212))
    side_strip(d, (190, 196, 205))
    img.save(f"{OUT}/float_psx_body.png")
    return R


# ================================================================== PSP UMD (hi-fi)
def offset_outline(outline, d, cy=0.0):
    """Shrink a star-shaped outline towards (0, cy) by d along each vertex ray."""
    out = []
    for x, y in outline:
        yy = y - cy
        r = math.hypot(x, yy) or 1e-6
        k = max(0.0, (r - d) / r)
        out.append((x * k, cy + yy * k))
    return out


def strip(mesh, A, zA, B, zB, u0, uw, rA, rB, skip=None):
    """Quad strip between two outlines with matching vertex order (a bevel). skip(mx, my) drops edges."""
    per = [0.0]
    n = len(A)
    for i in range(n):
        per.append(per[-1] + math.dist(A[i], A[(i + 1) % n]))
    tot = per[-1] or 1
    for i in range(n):
        j = (i + 1) % n
        if skip and skip((B[i][0] + B[j][0]) / 2, (B[i][1] + B[j][1]) / 2):
            continue
        s0, s1 = u0 + uw * per[i] / tot, u0 + uw * per[i + 1] / tot
        mesh.quad(((A[i][0], A[i][1], zA), (s0, rA)), ((A[j][0], A[j][1], zA), (s1, rA)),
                  ((B[j][0], B[j][1], zB), (s1, rB)), ((B[i][0], B[i][1], zB), (s0, rB)))


def cone_ring(mesh, r0, z0, r1, z1, uvf, seg=48):
    """Ring whose inner and outer edges sit at different heights (a chamfer)."""
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        p = [(r0 * math.cos(a0), r0 * math.sin(a0), z0), (r1 * math.cos(a0), r1 * math.sin(a0), z1),
             (r1 * math.cos(a1), r1 * math.sin(a1), z1), (r0 * math.cos(a1), r0 * math.sin(a1), z0)]
        mesh.quad(*[((x, y, z), uvf(x, y)) for x, y, z in p])


def variant_set(name, make, steps=GLOSS_STEPS):
    for i in range(steps):
        s = 0.5 * i / (steps - 1)
        g = Mesh()
        make(g, s)
        g.write(f"{name}{i}")


def font(size, italic=False, weight="bold"):
    """A real TTF when one is around (Windows fonts via /mnt/c, DejaVu), else PIL's bitmap font."""
    from PIL import ImageFont
    if weight == "regular":
        names = ["arial.ttf", "Arial.ttf", "DejaVuSans.ttf"]
    else:
        names = ["arialbi.ttf", "Arial Bold Italic.ttf"] if italic else ["arialbd.ttf", "Arial Bold.ttf"]
        names += ["DejaVuSans-BoldOblique.ttf" if italic else "DejaVuSans-Bold.ttf"]
    dirs = ["/mnt/c/Windows/Fonts", "C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu", "/Library/Fonts"]
    for dd in dirs:
        for n in names:
            try:
                return ImageFont.truetype(os.path.join(dd, n), size)
            except OSError:
                pass
    return ImageFont.load_default()


# ------------------------------------------------------------------ traced UMD (umd_trace.json)
# Vector trace of a real UMD (front and back, 2026-10-30): shell outline, laser slot, the
# back recesses, the arrow etched in the back window, the PSP / controller marks and the
# centre sticker lettering. Everything below is scaled by the window radius.
TRACE = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "umd_trace.json")))
TR_WIN = 106.85                                   # window radius in the trace (pt)
TR_FC, TR_BC, TR_CY = 428.736, 183.152, 169.03    # window centres of the front / back drawings
TR = dict(shell=7, window=8, psp=(9, 10, 11), pad=12, sticker=13, umd=(32, 33, 34), small=range(14, 32),
          slot=6, edge_cut=5, left_cut=2, dome=3, arrow=4)


def trace_polys(i, to_model, steps=10):
    """Closed polygons (model units) of trace drawing i, beziers flattened."""
    polys, cur, last = [], [], None

    def close():
        nonlocal cur
        if len(cur) > 2:
            polys.append(cur)
        cur = []
    for it in TRACE[i]["items"]:
        if it[0] == "re":
            x0, y0, x1, y1 = it[1]
            close()
            polys.append([to_model(x0, y0), to_model(x1, y0), to_model(x1, y1), to_model(x0, y1)])
            last = None
            continue
        p0 = it[1]
        if last is None or math.dist(p0, last) > 0.05:
            close()
            cur.append(to_model(*p0))
        if it[0] == "l":
            cur.append(to_model(*it[2]))
            last = it[2]
        else:
            a, b, c, e = it[1:5]
            for k in range(1, steps + 1):
                t = k / steps
                u = 1 - t
                cur.append(to_model(u ** 3 * a[0] + 3 * u * u * t * b[0] + 3 * u * t * t * c[0] + t ** 3 * e[0],
                                    u ** 3 * a[1] + 3 * u * u * t * b[1] + 3 * u * t * t * c[1] + t ** 3 * e[1]))
            last = e
    close()
    return polys


def trace_fill(img, ids, to_model, px, colour, ss=4):
    """Fill trace drawings through px (model -> atlas pixel), even-odd so glyph counters stay open."""
    from PIL import ImageChops
    for i in ([ids] if isinstance(ids, int) else ids):
        polys = [[px(*p) for p in poly] for poly in trace_polys(i, to_model)]
        xs = [p[0] for poly in polys for p in poly]
        ys = [p[1] for poly in polys for p in poly]
        x0, y0, x1, y1 = int(min(xs)) - 2, int(min(ys)) - 2, int(max(xs)) + 3, int(max(ys)) + 3
        w, h = (x1 - x0) * ss, (y1 - y0) * ss
        mask = Image.new("1", (w, h), 0)
        for poly in polys:
            m = Image.new("1", (w, h), 0)
            ImageDraw.Draw(m).polygon([((x - x0) * ss, (y - y0) * ss) for x, y in poly], fill=1)
            mask = ImageChops.logical_xor(mask, m)
        img.paste(colour + (255,), (x0, y0, x1, y1), mask.convert("L").resize((x1 - x0, y1 - y0), Image.LANCZOS))


def clean_poly(poly, ccw=True):
    """Drop repeated points, force the winding."""
    out = []
    for p in poly:
        if not out or math.dist(p, out[-1]) > 1e-4:
            out.append(p)
    if len(out) > 1 and math.dist(out[0], out[-1]) < 1e-4:
        out.pop()
    area = sum(out[i][0] * out[(i + 1) % len(out)][1] - out[(i + 1) % len(out)][0] * out[i][1] for i in range(len(out)))
    return out if (area > 0) == ccw else out[::-1]


def edge_x(outline, y):
    """Largest x where the outline crosses height y."""
    best = None
    n = len(outline)
    for i in range(n):
        (x0, y0), (x1, y1) = outline[i], outline[(i + 1) % n]
        if (y0 - y) * (y1 - y) <= 0 and y0 != y1:
            x = x0 + (x1 - x0) * (y - y0) / (y1 - y0)
            if best is None or x > best:
                best = x
    return best


def recess(mesh, poly, z_face, z_floor, uvf, wall_uv, skip=None, floor=True, centre_z=None):
    """A pocket in a face: its floor (or a dimple cone when centre_z is given) plus the walls
    around it. skip(p, q) = True leaves out that wall (an edge that is open to the outside)."""
    if floor and centre_z is None:
        fan(mesh, poly, z_floor, uvf)
    elif floor:
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        for i in range(len(poly)):
            p0, p1 = poly[i], poly[(i + 1) % len(poly)]
            mesh.tri(((cx, cy, centre_z), uvf(cx, cy)), ((p0[0], p0[1], z_face), uvf(*p0)), ((p1[0], p1[1], z_face), uvf(*p1)))
        return
    for i in range(len(poly)):
        p0, p1 = poly[i], poly[(i + 1) % len(poly)]
        if skip and skip(p0, p1):
            continue
        mesh.quad(((p0[0], p0[1], z_face), (wall_uv[0], wall_uv[2])), ((p1[0], p1[1], z_face), (wall_uv[1], wall_uv[2])),
                  ((p1[0], p1[1], z_floor), (wall_uv[1], wall_uv[3])), ((p0[0], p0[1], z_floor), (wall_uv[0], wall_uv[3])))


def slot_region(mesh, slot_h, x_end, R_open, z, uvf, width=None):
    """Back window minus the laser slot {x > x_end, |y| < slot_h} (square inner end, as traced),
    cut exactly with face_with_holes. width: only a thin rim strip along the slot edge."""
    win = circle(R_open, 96)
    slot = [(x_end, -slot_h), (R_open + 0.1, -slot_h), (R_open + 0.1, slot_h), (x_end, slot_h)]
    if width:
        h = slot_h + width
        xc = lambda y: math.sqrt(max(0.0, R_open * R_open - y * y))
        rim = ([(x_end - width, -h), (xc(-h), -h)] + [(xc(y), y) for y in [-h + 2 * h * k / 12 for k in range(1, 12)]]
               + [(xc(h), h), (x_end - width, h)])
        face_with_holes(mesh, rim, [slot], z, lambda x, y: (0.6, 0.965))
        return
    face_with_holes(mesh, win, [slot], z, uvf)


def psp_umd():
    """Real UMD from the trace: a 60mm disc in a shell whose top half is a circle a little
    larger than the disc, straight sides, rounded corners and a gently curved base. The
    disc shows on both sides behind a clear window; the front label has the flat white
    centre sticker, the back window has the laser slot cut out of it and the shell rim
    recessed around the slot (not through), a round cut and a domed dimple in the base,
    a cut into the right edge, and the arrow etched into the clear window at the top."""
    R_d = 0.86
    R_open = R_d + 0.012                  # window opening
    K = R_open / TR_WIN                   # trace pt -> model units
    front = lambda X, Y: ((X - TR_FC) * K, (TR_CY - Y) * K)
    back = lambda X, Y: ((TR_BC - X) * K, (TR_CY - Y) * K)    # seen from the back: trace-left is model +x
    T, bev = 0.13, 0.022
    r_stick, r_pin, r_lab = 0.404, 0.028, 0.26   # white centre sticker (traced), disc pin hole (back), label starts under it
    depth = 0.090                         # back recesses nearly cut through to the front face (shell is 0.13 thick)
    seg = 72
    outline = clean_poly(trace_polys(TR["shell"], front, steps=14)[0])
    bb = bbox(outline)
    dbb = (-R_d, -R_d, R_d, R_d)
    wbb = (-R_open, -R_open, R_open, R_open)
    zf, zb = T / 2, -T / 2
    disc_zf, disc_zb = 0.004, -0.004      # the disc sits in the middle of the shell
    zfloor = zb + depth
    slot = clean_poly(trace_polys(TR["slot"], back)[0])
    sbb = bbox(slot)
    x_end, slot_h = sbb[0], (sbb[3] - sbb[1]) / 2
    edge_cut = clean_poly(trace_polys(TR["edge_cut"], back)[0])
    left_cut = clean_poly(trace_polys(TR["left_cut"], back)[0])
    dome = clean_poly(trace_polys(TR["dome"], back)[0])
    arrow = [clean_poly(p) for p in trace_polys(TR["arrow"], back)]
    ebb = bbox(edge_cut)
    for y in (slot_h, -slot_h):            # outline vertices where the pockets meet the edge
        outline = split_at_y(outline, y, +1)
    for y in (ebb[1], ebb[3]):
        outline = split_at_y(outline, y, -1)
    inner = offset_outline(outline, bev)
    def in_notch(mx, my):                  # edge midpoints inside a pocket that reaches the shell edge
        return (mx > 0 and abs(my) < slot_h - 1e-6) or (mx < 0 and ebb[1] + 1e-6 < my < ebb[3] - 1e-6)

    # atlas regions (1024): shell front face 0..0.5 x 0..0.5, shell back 0.5..1 x 0..0.5
    #   bevel strip rows 0.78..0.82, band rows 0.84..0.94, inner wall rows 0.95..0.98,
    #   etch patch 0.5..0.6 x 0.985..1, disc back 0..0.5 x 0.5..1 (mirrored), centre sticker 0.52..0.74 sq
    A = planar(bb, 0, 0.5, 0, 0.5)
    Ab = planar(bb, 0.5, 0.5, 0, 0.5, mirror=True)
    wall_uv = (0.55, 0.65, 0.978, 0.952)  # u0, u1, row at the face edge (light), row at the floor (dark)

    # --- back (listed first = drawn last when the back faces the camera)
    m = Mesh(); slot_region(m, slot_h, x_end, R_open, zb + 0.001, None, width=0.014); m.write("float_psp_slotrim")
    m = Mesh()                             # arrow etched into the window: strip between the two traced triangles
    def by_angle(tri):
        cx = sum(p[0] for p in tri) / 3
        cy = sum(p[1] for p in tri) / 3
        return sorted(tri, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    o, i_ = by_angle(arrow[0]), by_angle(arrow[1])
    if abs(o[0][1]) < abs(i_[0][1]):
        o, i_ = i_, o
    for k in range(3):
        m.quad(((o[k][0], o[k][1], zb), (0.55, 0.992)), ((o[(k + 1) % 3][0], o[(k + 1) % 3][1], zb), (0.55, 0.992)),
               ((i_[(k + 1) % 3][0], i_[(k + 1) % 3][1], zb), (0.55, 0.992)), ((i_[k][0], i_[k][1], zb), (0.55, 0.992)))
    m.write("float_psp_backetch")
    m = Mesh(); slot_region(m, slot_h, x_end, R_open, zb + 0.002, planar(wbb)); m.write("float_psp_backglass")
    m = Mesh()                             # the recesses in the back face
    n = 16                                 # slot: the rim between the window and the shell edge, lowered
    for k in range(n):
        ya, yb = -slot_h + 2 * slot_h * k / n, -slot_h + 2 * slot_h * (k + 1) / n
        pts = [(math.sqrt(R_open ** 2 - ya * ya), ya), (edge_x(outline, ya), ya), (edge_x(outline, yb), yb), (math.sqrt(R_open ** 2 - yb * yb), yb)]
        m.quad(*[((x, y, zfloor), Ab(x, y)) for x, y in pts])
    ecx = min(p[0] for p in edge_cut) + 0.006     # the edge cut is open towards the shell edge
    fan(m, edge_cut, zfloor, Ab)
    fan(m, left_cut, zfloor, Ab)
    recess(m, dome, zb, zfloor, Ab, wall_uv, centre_z=zb + depth * 0.8)
    m.write("float_psp_backcuts")
    # the recess walls: each goes into the window-wall mesh of the side it faces away from, so the
    # Lua draws it only when that side is the far one (a wall facing -y belongs to "T", etc.)
    extra_walls = {"R": [], "T": [], "L": [], "B": []}
    def wall_quad(p0, p1):
        return (((p0[0], p0[1], zb), (wall_uv[0], wall_uv[2])), ((p1[0], p1[1], zb), (wall_uv[1], wall_uv[2])),
                ((p1[0], p1[1], zfloor), (wall_uv[1], wall_uv[3])), ((p0[0], p0[1], zfloor), (wall_uv[0], wall_uv[3])))
    def wall_side(p0, p1, poly_cx, poly_cy):
        # the wall's outward normal points away from the pocket centre; it is seen from the opposite side
        mx, my = (p0[0] + p1[0]) / 2 - poly_cx, (p0[1] + p1[1]) / 2 - poly_cy
        if abs(mx) > abs(my):
            return "R" if mx > 0 else "L"
        return "T" if my > 0 else "B"
    for sgn in (1, -1):                    # slot long walls, across the rim only
        xa, xb = math.sqrt(R_open ** 2 - slot_h ** 2), edge_x(outline, sgn * slot_h)
        extra_walls["B" if sgn > 0 else "T"].append(wall_quad((xa, sgn * slot_h), (xb, sgn * slot_h)))
    for poly, skip in ((edge_cut, lambda p, q: p[0] < ecx and q[0] < ecx), (left_cut, None)):
        cx_ = sum(p[0] for p in poly) / len(poly)
        cy_ = sum(p[1] for p in poly) / len(poly)
        for i in range(len(poly)):
            p0, p1 = poly[i], poly[(i + 1) % len(poly)]
            if skip and skip(p0, p1):
                continue
            extra_walls[wall_side(p0, p1, cx_, cy_)].append(wall_quad(p0, p1))
    m = Mesh(); face_with_holes(m, inner, [circle(R_open, 96), slot, edge_cut, left_cut, dome], zb, Ab); m.write("float_psp_shellback")
    m = Mesh(); strip(m, inner, zb, outline, zb + bev, 0.5, 0.5, 0.78, 0.82, skip=in_notch); m.write("float_psp_shellbackbevel")
    m = Mesh(); ring(m, 0.30, R_d, disc_zb - 0.004, planar(dbb), seg); m.write("float_psp_sheen")
    m = Mesh(); ring(m, r_pin, R_d, disc_zb, planar(dbb, 0, 0.5, 0.5, 0.5, mirror=True), seg); m.write("float_psp_discback")
    def wall_arcs(name, z_outer, z_inner, skip=None, extra=None):
        """Inner wall of the window split into R/T/L/B arcs (from the front)."""
        for tag, a0 in (("R", -45), ("T", 45), ("L", 135), ("B", 225)):
            m = Mesh()
            for q in (extra or {}).get(tag, []):
                m.quad(*q)
            n = seg // 4
            for i in range(n):
                t0 = math.radians(a0 + 90 * i / n)
                t1 = math.radians(a0 + 90 * (i + 1) / n)
                if skip and skip(R_open * math.cos((t0 + t1) / 2), R_open * math.sin((t0 + t1) / 2)):
                    continue
                p = [(R_open * math.cos(t0), R_open * math.sin(t0), z_outer), (R_open * math.cos(t1), R_open * math.sin(t1), z_outer),
                     ((R_open - 0.004) * math.cos(t1), (R_open - 0.004) * math.sin(t1), z_inner),
                     ((R_open - 0.004) * math.cos(t0), (R_open - 0.004) * math.sin(t0), z_inner)]
                m.quad(((p[0][0], p[0][1], p[0][2]), (0.55, 0.952)), ((p[1][0], p[1][1], p[1][2]), (0.65, 0.952)),
                       ((p[2][0], p[2][1], p[2][2]), (0.65, 0.978)), ((p[3][0], p[3][1], p[3][2]), (0.55, 0.978)))
            m.write(f"{name}{tag}")
    wall_arcs("float_psp_wallback", zb, disc_zb - 0.006, skip=lambda x, y: x > 0 and abs(y) < slot_h, extra=extra_walls)   # no wall inside the slot
    # --- side
    band_sides("float_psp_band", outline, zb + bev, zf - bev, 0.5, 0.5, 0.84, 0.94,
               z0f=lambda mx, my: zfloor if in_notch(mx, my) else zb + bev)   # notched where the pockets cut through
    # --- front
    wall_arcs("float_psp_wall", zf, disc_zf + 0.006)
    # glass: plastic tint over the whole window + contact shadow where the lip overhangs the disc
    m = Mesh(); ring(m, 0.02, R_open, disc_zf + 0.009, planar(wbb), seg); m.write("float_psp_glass")
    m = Mesh(); ring(m, r_lab, R_d, disc_zf, planar(dbb, 0, 1, 0.02, 1), seg); m.write("float_psp_label")
    m = Mesh(); ring(m, r_lab, R_d, disc_zf, planar(dbb), seg); m.write("float_psp_labeldisc")
    # flat white centre sticker on top of the label
    m = Mesh(); fan(m, circle(r_stick, 48), disc_zf + 0.002, planar((-r_stick, -r_stick, r_stick, r_stick), 0.52, 0.22, 0.52, 0.22)); m.write("float_psp_hub")
    m = Mesh(); ring_to_outline(m, R_open, inner, zf, A, seg * 2); m.write("float_psp_shellfront")
    m = Mesh(); strip(m, inner, zf, outline, zf - bev, 0.5, 0.5, 0.78, 0.82); m.write("float_psp_shellfrontbevel")
    grime_meshes("psp", outline, bb, zf, zb, (0, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5))
    # overlays: clear window over the disc + the whole shell front, planar UV window (yaw-shifted)
    def overlay(g, s, z_add, scale_v=1.0):
        # only the clear window reflects; the white shell around it is matte plastic
        uv = planar(bb, s, 0.5, 0, scale_v)
        ring(g, 0.02, R_open - 0.004, zf - 0.002 + z_add, uv, seg)  # the window surface, flush with the shell
    variant_set("float_psp_refl", lambda g, s: overlay(g, s, 0.0))
    variant_set("float_psp_shade", lambda g, s: overlay(g, s, 0.002))
    # back overlay: the environment reflected in the clear back window only (not in the slot); the rim is matte shell
    def back_overlay(g, s):
        uv = planar(bb, s, 0.5, 0, 1, mirror=True)
        slot_region(g, slot_h, x_end, R_open, zb - 0.002, uv)
    variant_set("float_psp_backrefl", back_overlay)
    shine_quad("psp", 1.24, -0.62, 0.62, disc_zf + 0.010)   # drifts 0.14
    shadow_set("psp", outline, T)

    # ---------------------------------------------------------------- textures
    global TEX
    TEX = 1024
    img = Image.new("RGBA", (TEX, TEX), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    mark = (158, 160, 166)   # printed grey of the PSP logo and the controller mark
    # shell front: soft plastic gradient + faint grain, darker ring at the opening, PSP logo and controller mark (traced)
    vgrad(d, (0, 0, TEX // 2, TEX // 2), (242, 243, 245), (220, 221, 226))
    pf = px_of(bb, 0, 0, 0.5, 0.5)
    c = pf(0, 0)
    scale = (TEX * 0.5) / (bb[2] - bb[0])
    for rr, col, w in [(R_open + 0.03, (178, 180, 186), 5), (R_open + 0.006, (120, 122, 128), 3)]:
        s_ = rr * scale
        d.ellipse([c[0] - s_, c[1] - s_, c[0] + s_, c[1] + s_], outline=col + (255,), width=w)
    trace_fill(img, TR["psp"], front, pf, mark)
    trace_fill(img, TR["pad"], front, pf, mark)
    d.line([pf(*p) for p in inner] + [pf(*inner[0])], fill=(200, 202, 208, 255), width=2)
    # shell back: the recess floors a shade darker with a shadow along their walls, the dimple shaded
    vgrad(d, (TEX // 2, 0, TEX, TEX // 2), (240, 241, 243), (222, 223, 228))   # same white shell as the front
    pb = px_of(bb, 0.5, 0, 0.5, 0.5, mirror=True)
    c = pb(0, 0)
    s_ = (R_open + 0.006) * scale
    d.ellipse([c[0] - s_, c[1] - s_, c[0] + s_, c[1] + s_], outline=(132, 134, 140, 255), width=3)
    d.line([pb(*p) for p in inner] + [pb(*inner[0])], fill=(188, 190, 196, 255), width=2)
    for key in ("slot", "edge_cut", "left_cut"):
        trace_fill(img, TR[key], back, pb, (198, 199, 204))
        poly = clean_poly(trace_polys(TR[key], back)[0])
        pts = [pb(*p) for p in poly] + [pb(*poly[0])]
        d.line(pts, fill=(118, 120, 126, 255), width=6)     # shadow under the wall
        d.line([(x, y + 3) for x, y in pts], fill=(168, 170, 176, 255), width=3)
    dpts = [pb(*p) for p in dome]
    dxs, dys = [p[0] for p in dpts], [p[1] for p in dpts]
    dbox = (int(min(dxs)), int(min(dys)), int(max(dxs)) + 1, int(max(dys)) + 1)
    dimple = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(dimple)
    for k in range(dbox[1], dbox[3]):      # concave dimple: shadowed at the top, lit at the bottom
        t = (k - dbox[1]) / max(1, dbox[3] - dbox[1] - 1)
        v = int(150 + 80 * t)
        dd.line([(dbox[0], k), (dbox[2], k)], fill=(v, v, v + 4, 255))
    dome_mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(dome_mask).polygon(dpts, fill=255)
    img.paste(dimple, (0, 0), dome_mask)
    d.ellipse([dbox[0], dbox[1], dbox[2], dbox[3]], outline=(140, 142, 148, 255), width=2)
    # etch patch: pale, half transparent (the arrow cut into the clear window)
    d.rectangle([int(0.5 * TEX), int(0.985 * TEX), int(0.6 * TEX), TEX], fill=(238, 242, 250, 150))
    # bevel strips (front 0..0.5, back 0.5..1): bright top edge rolling to the side tone
    for r in range(int(0.78 * TEX) - 2, int(0.82 * TEX) + 3):
        t = min(1.0, max(0.0, (r - 0.78 * TEX) / (0.04 * TEX)))
        v = int(250 - 40 * t)
        d.line([(TEX // 2, r), (TEX, r)], fill=(v, v, v + 3, 255))
    # band
    side_strip_lit(d, img, (226, 227, 231), outline, 0.5, 0.5, 0.84, 0.94, grain=5)
    # inner wall of the window: dark under the lip, lighter towards the disc
    for r in range(int(0.95 * TEX) - 2, int(0.98 * TEX) + 3):
        t = min(1.0, max(0.0, (r - 0.95 * TEX) / (0.03 * TEX)))
        v = int(96 + 70 * t)
        d.line([(TEX // 2, r), (TEX, r)], fill=(v, v, v + 4, 255))
    # disc back (0..0.5 x 0.5..1, mirrored)
    pd = px_of(dbb, 0, 0.5, 0.5, 0.5, mirror=True)
    radial(d, pd, 0, 0, R_d, (TEX * 0.5) / (2 * R_d), lambda rr: data_side(rr, R_d, 0.34))
    c = pd(0, 0)
    for rr, col in [(0.34, (90, 92, 98)), (0.28, (160, 164, 170)), (0.12, (60, 60, 64))]:
        s_ = rr * (TEX * 0.5) / (2 * R_d)
        d.ellipse([c[0] - s_, c[1] - s_, c[0] + s_, c[1] + s_], outline=col + (255,), width=3)
    # centre sticker (0.52..0.74): flat white, faint edge, traced UMD wordmark and small print
    hx0, hy0, hx1, hy1 = int(0.52 * TEX), int(0.52 * TEX), int(0.74 * TEX), int(0.74 * TEX)
    d.ellipse([hx0, hy0, hx1, hy1], fill=(250, 250, 252, 255))
    d.ellipse([hx0 + 3, hy0 + 3, hx1 - 3, hy1 - 3], outline=(214, 216, 222, 255), width=2)
    ps = px_of((-r_stick, -r_stick, r_stick, r_stick), 0.52, 0.52, 0.22, 0.22)
    trace_fill(img, TR["umd"], front, ps, (39, 117, 185))
    trace_fill(img, TR["small"], front, ps, (100, 102, 108))
    # lighting: rolled edges and the pockets' shadows
    win_px = [pf(*q) for q in circle(R_open, 96)]
    bake_light(img, (0, 0, TEX // 2, TEX // 2), [([pf(*q) for q in outline], 1.0), (win_px, 0.6)], k=22.0, ao=0.7, blur=4)
    bake_light(img, (TEX // 2, 0, TEX, TEX // 2), [([pb(*q) for q in outline], 1.0), ([pb(*q) for q in circle(R_open, 96)], 0.6),
               ([pb(*q) for q in slot], 0.55), ([pb(*q) for q in edge_cut], 0.55), ([pb(*q) for q in left_cut], 0.55), ([pb(*q) for q in dome], 0.75)], k=22.0, ao=0.7, blur=4)
    # plastic grain on the shell faces
    noise = Image.effect_noise((TEX // 2, TEX // 2), 6).convert("L")
    grain = Image.merge("RGBA", (noise, noise, noise, Image.new("L", noise.size, 22)))
    for ox in (0, TEX // 2):
        region = img.crop((ox, 0, ox + TEX // 2, TEX // 2))
        region.alpha_composite(grain)
        img.paste(region, (ox, 0))
    img.save(f"{OUT}/float_psp_body.png")
    TEX = 512
    return (bb[3] - bb[1]) / 2, -(bb[1] + bb[3]) / 2


def glass_texture():
    n = 512
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    px = img.load()
    c = (n - 1) / 2
    for y in range(n):
        for x in range(n):
            r = math.hypot((x - c) / c, (y - c) / c)
            if r > 1.0:
                continue
            ao = max(0.0, (r - 0.80) / 0.20) ** 1.6          # lip shadow near the window edge
            tint = 0.07 * (1 - 0.5 * r)                        # faint plastic haze, lighter at the centre
            a = min(1.0, 0.55 * ao + tint)
            k = tint / a if a > 0 else 0
            v = int(255 * k)
            px[x, y] = (v, v, int(min(255, v * 1.05)), int(255 * a))
    img.filter(ImageFilter.GaussianBlur(1.5)).save(f"{OUT}/float_glass.png")


def env_texture():
    """Studio environment with baked alpha: what the clear window and glossy
    shell reflect. A wide strip; overlays slide a half-width window across it
    as the item yaws."""
    w, h = 1024, 512
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for y in range(h):
        for x in range(w):
            u, v = x / w, y / h
            # soft ceiling light + darker floor
            base = 0.16 * (1 - v) ** 1.5 + 0.04
            # two softbox panels drifting across the strip
            sb = 0.0
            for cx, wdt, hgt in ((0.33, 0.07, 0.42), (0.78, 0.05, 0.36)):
                dx = (u - cx) / wdt
                dy = (v - 0.22) / hgt
                sb += math.exp(-(dx * dx) * 2.2) * max(0.0, 1 - dy * dy) * 0.55
            # a bright window pane and a thin light tube in the reflection
            win = 0.0
            for cx, cy, wx, wy in ((0.55, 0.30, 0.09, 0.22), (0.12, 0.34, 0.05, 0.18)):
                if abs(u - cx) < wx and abs(v - cy) < wy:
                    ex = min(1.0, (wx - abs(u - cx)) / 0.012)
                    ey = min(1.0, (wy - abs(v - cy)) / 0.02)
                    win = max(win, 0.7 * min(ex, ey) * (1 - 0.6 * abs(u - cx) / wx))
            tube = 0.45 * math.exp(-((v - 0.52) / 0.012) ** 2) * (0.5 + 0.5 * math.cos((u - 0.7) * 3.2))
            a = min(1.0, base + sb + win + tube)
            tint = (255, 252, 246) if sb > 0.05 or win > 0.05 else (210, 225, 245)
            px[x, y] = (tint[0], tint[1], tint[2], int(255 * a * 0.75))
    img.filter(ImageFilter.GaussianBlur(2)).save(f"{OUT}/float_env.png")


def shade_texture():
    """Directional shade strip: transparent on one side, darkening on the other."""
    w, h = 1024, 64
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for x in range(w):
        u = x / w
        a = int(255 * 0.42 * max(0.0, (u - 0.5) / 0.5) ** 1.4)
        for y in range(h):
            px[x, y] = (8, 10, 16, a)
    img.save(f"{OUT}/float_shade.png")


# ================================================================== SHARED
def shadow():
    m = Mesh()
    w, dpt = 1.0, 0.55
    m.quad(((-w, 0, -dpt), (0, 0)), ((w, 0, -dpt), (1, 0)), ((w, 0, dpt), (1, 1)), ((-w, 0, dpt), (0, 1)))
    m.write("float_shadow")
    # contact shadow: a wide soft halo plus a tight dark core where the item meets the floor
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([24, 84, 232, 172], fill=(0, 0, 0, 70))
    img = img.filter(ImageFilter.GaussianBlur(30))
    mid = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(mid).ellipse([52, 108, 204, 148], fill=(0, 0, 0, 120))
    img.alpha_composite(mid.filter(ImageFilter.GaussianBlur(12)))
    core = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(core).ellipse([80, 120, 176, 136], fill=(0, 0, 0, 190))
    img.alpha_composite(core.filter(ImageFilter.GaussianBlur(5)))
    img.save(f"{OUT}/float_shadow.png")


def sheen_texture():
    """Diffraction colours of a disc's data side: hue runs along the radius,
    strongest in two opposite lobes. index.lua spins this layer about the disc
    axis as the viewing angle changes, so the colours sweep round like a real CD."""
    n = 256
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    px = img.load()
    c = (n - 1) / 2
    for y in range(n):
        for x in range(n):
            dx, dy = (x - c) / c, (y - c) / c
            r = math.hypot(dx, dy)
            if r > 1.0 or r < 0.3:
                continue
            a = math.atan2(dy, dx)
            def spoke(centre, width):  # angular gaussian, mirrored across the disc
                d = (a - centre + math.pi) % math.pi - math.pi / 2
                return math.exp(-(d / width) ** 2)
            lobe = spoke(0.0, 0.42) + 0.5 * spoke(1.25, 0.22)
            fade = math.sin((r - 0.3) / 0.7 * math.pi) ** 0.5
            h = ((r - 0.3) / 0.7 * 1.6 + 0.65) % 1.0
            i = int(h * 6); f = h * 6 - i
            q, t = 1 - f, f
            rgb = [(1, t, 0), (q, 1, 0), (0, 1, t), (0, q, 1), (t, 0, 1), (1, 0, q)][i % 6]
            alpha = int(255 * min(1.0, 0.8 * lobe) * fade)
            px[x, y] = (int(90 + 165 * rgb[0]), int(90 + 165 * rgb[1]), int(90 + 165 * rgb[2]), alpha)
    img.filter(ImageFilter.GaussianBlur(1.2)).save(f"{OUT}/float_sheen.png")


def gloss_texture():
    """Highlight that slides across a glossy surface: a wide soft sheen with a sharp bright
    streak inside it, slanted like a softbox reflection."""
    import numpy as np
    w, h = 512, 256
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    u = xs - 256 - (ys - 128) * 0.28                 # slanted
    sheen = np.exp(-(u / 48.0) ** 2) * 0.30
    streak = np.exp(-(u / 7.0) ** 2) * 0.60
    streak2 = np.exp(-((u + 26) / 4.0) ** 2) * 0.22
    a = np.clip(sheen + streak + streak2, 0, 1)
    img = np.zeros((h, w, 4), np.uint8)
    img[..., :3] = 255
    img[..., 3] = (a * 255).astype(np.uint8)
    Image.fromarray(img, "RGBA").filter(ImageFilter.GaussianBlur(1.0)).save(f"{OUT}/float_gloss.png")



def shine_texture():
    """One soft highlight that the Lua drifts across the label: a broad, diffuse sheen like a
    soft room light on glossy paper (no bar, no streak), fading out all round."""
    import numpy as np
    w, h = 256, 512
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = (xs - w / 2) / w, (ys - h / 2) / h
    glow = np.exp(-((u / 0.34) ** 2 + ((v - 0.08) / 0.40) ** 2)) * 0.17
    edge = np.clip((0.5 - np.abs(u)) / 0.12, 0, 1) * np.clip((0.5 - np.abs(v)) / 0.12, 0, 1)
    a = np.clip(glow * edge, 0, 1)
    img = np.zeros((h, w, 4), np.uint8)
    img[..., :3] = 255
    img[..., 3] = (a * 255).astype(np.uint8)
    Image.fromarray(img, "RGBA").filter(ImageFilter.GaussianBlur(3.0)).save(f"{OUT}/float_shine.png")


def shine_quad(kind, w, y0, y1, z):
    """The sliding highlight: a quad w wide centred on x = 0 (the Lua moves it along the item's x)."""
    m = Mesh()
    m.quad(((-w / 2, y0, z), (0.0, 1.0)), ((w / 2, y0, z), (1.0, 1.0)), ((w / 2, y1, z), (1.0, 0.0)), ((-w / 2, y1, z), (0.0, 0.0)))
    m.write(f"float_{kind}_shine")



REFLECT = { "vita": ["front", "well", "label", "bands", "bandR", "bandT", "bandL", "bandB"],
            "psp": ["shellfront", "shellfrontbevel", "label", "hub", "glass", "bandR", "bandT", "bandL", "bandB"],
            "psx": ["front", "label", "band"] }


def mirror_meshes():
    """Floor-reflection copies: the same meshes with y negated (float_<kind>_r_<name>.obj). The Lua
    draws them below the floor line with the pitch and roll negated, so they mirror the item."""
    n = 0
    for kind, names in REFLECT.items():
        for name in names:
            src = f"{OUT}/float_{kind}_{name}.obj"
            if not os.path.exists(src):
                continue
            out = []
            for line in open(src):
                p = line.split()
                if p and p[0] == "v":
                    out.append(f"v {p[1]} {-float(p[2]):.4f} {p[3]}\n")
                else:
                    out.append(line)
            open(f"{OUT}/float_{kind}_r_{name}.obj", "w").writelines(out)
            n += 1
    return n


def reflection_fade():
    """Viewer-side copy of RetroFlow's floor plane (planefloor.obj + floor.png): a huge horizontal
    plane at the floor line with a translucent black band that dims the reflections. On the
    Vita the Lua draws RetroFlow's own modFloor instead."""
    m = Mesh()
    m.quad(((-21.12, 0, 12.83), (0.0, 0.0)), ((21.12, 0, 12.83), (1.0, 0.0)), ((21.12, 0, -12.83), (1.0, 1.0)), ((-21.12, 0, -12.83), (0.0, 1.0)))
    m.write("float_floor")
    import numpy as np
    img = np.zeros((64, 64, 4), np.uint8)
    v = np.linspace(0, 1, 64)[:, None]
    a = 77 * np.clip(np.minimum(v / 0.22, (1 - v) / 0.22), 0, 1)
    img[..., 3] = a.astype(np.uint8)
    Image.fromarray(img, "RGBA").save(f"{OUT}/float_floor.png")



SHADOW_STEPS = 12


def shadow_set(kind, outline, T):
    """Footprint shadow under a top light: the item's own silhouette (soft-edged texture on a
    quad) laid on the floor, its depth scaled by the pitch: a thin strip when upright, the full
    outline when flat. 12 steps each way (float_<kind>_shadow{k} top towards the camera,
    float_<kind>_shadown{k} top away)."""
    bb = bbox(outline)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    cx_, cy_ = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
    n, margin = 256, 0.14
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    sx, sy = (n * (1 - 2 * margin)) / w, (n * (1 - 2 * margin)) / h
    sc = min(sx, sy)
    pts = [(n / 2 + (x - cx_) * sc, n / 2 - (y - cy_) * sc) for x, y in outline]
    ImageDraw.Draw(img).polygon(pts, fill=(0, 0, 0, 110))
    img = img.filter(ImageFilter.GaussianBlur(16))
    core = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    ImageDraw.Draw(core).polygon([(n / 2 + (x - cx_) * sc * 0.8, n / 2 - (y - cy_) * sc * 0.8) for x, y in outline], fill=(0, 0, 0, 70))
    img.alpha_composite(core.filter(ImageFilter.GaussianBlur(22)))
    img.save(f"{OUT}/float_{kind}_shadow.png")
    ext_x = (n / 2) / sc          # half extent of the quad in model units (texture edge to edge)
    for k in range(SHADOW_STEPS):
        s_ = math.sin(math.radians(90 * k / (SHADOW_STEPS - 1)))
        depth = max(T * 1.6, h * s_)                 # the silhouette's depth on the floor
        ext_z = ext_x * depth / w if False else (n / 2) / sc * (depth / h)
        for sign, name in ((1, "shadow"), (-1, "shadown")):
            m = Mesh()
            # texture row 0 (top of the silhouette) lands at +z (towards the camera) for sign +1
            m.quad(((cx_ - ext_x, 0, -sign * ext_z), (0.0, 1.0)), ((cx_ + ext_x, 0, -sign * ext_z), (1.0, 1.0)),
                   ((cx_ + ext_x, 0, sign * ext_z), (1.0, 0.0)), ((cx_ - ext_x, 0, sign * ext_z), (0.0, 0.0)))
            m.write(f"float_{kind}_{name}{k}")


if __name__ == "__main__":
    hv, offv = vita_card()
    hp = ps1_disc()
    hu, off = psp_umd()
    shadow()
    gloss_texture()
    sheen_texture()
    env_texture()
    shade_texture()
    glass_texture()
    grime_texture()
    shine_texture()
    reflection_fade()
    print(mirror_meshes(), "reflection meshes")
    print(f"half heights: vita={hv:.3f} (offset {offv:+.3f}) psx={hp:.3f} psp={hu:.3f} (psp centre offset {off:+.3f})")
    print(len(os.listdir(OUT)), "files")
