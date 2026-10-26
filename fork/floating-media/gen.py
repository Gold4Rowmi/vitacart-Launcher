"""Generate floating physical-media models + textures for RetroFlow.

Output layout matches RetroFlow's AccuTrans-style OBJs as read by lpp-vita's
Render.loadObject: all `v`, then all `vt`, then all `vn`, then triangular
`f v/vt/vn` faces. No comments or other directives (the loader strstr()s for
"f ").

lpp-vita has no depth test, so faces are emitted back -> sides -> front, and the
label/gloss are separate models drawn afterwards (painter's algorithm).

All models are centred on the origin (pivot in the middle so gyro tilt looks
natural). Units match RetroFlow's existing covers (~1.6 x 1.8 for a Vita box).
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

OUT = sys.argv[1] if len(sys.argv) > 1 else "out/DATA"
os.makedirs(OUT, exist_ok=True)

GLOSS_STEPS = 9


# --------------------------------------------------------------------------- OBJ
class Mesh:
    """Vertices are shared between faces (keyed on position + uv): the lpp-vita
    loader resolves every face index by walking a linked list, so vertex count
    directly sets load time."""

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
                self.vt.append((u, 1.0 - r))  # loader flips v back
                i = len(self.v)
                self._idx[key] = i
            idx.append(i)
        self.f.append(idx)

    def quad(self, a, b, c, d):
        self.tri(a, b, c)
        self.tri(a, c, d)

    def write(self, path):
        with open(path, "w") as fh:
            fh.write("\n")
            for x, y, z in self.v:
                fh.write(f"v {x:.4f} {y:.4f} {z:.4f}\n")
            fh.write("\n")
            for u, v in self.vt:
                fh.write(f"vt {u:.4f} {v:.4f}\n")
            fh.write("\nvn 0 0 1.0\n\n")
            for a, b, c in self.f:
                fh.write(f"f {a}/{a}/1 {b}/{b}/1 {c}/{c}/1\n")


def bbox(pts):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def rounded_corner(cx, cy, r, a0, a1, n=6):
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def fan(mesh, poly, z, uvf):
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    c = ((cx, cy, z), uvf(cx, cy))
    for i in range(len(poly)):
        p0, p1 = poly[i], poly[(i + 1) % len(poly)]
        mesh.tri(c, ((p0[0], p0[1], z), uvf(*p0)), ((p1[0], p1[1], z), uvf(*p1)))


def ring(mesh, r_in, r_out, z, uvf, seg=64, cy=0.0):
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        pts = [(r_in * math.cos(a0), cy + r_in * math.sin(a0)), (r_out * math.cos(a0), cy + r_out * math.sin(a0)),
               (r_out * math.cos(a1), cy + r_out * math.sin(a1)), (r_in * math.cos(a1), cy + r_in * math.sin(a1))]
        mesh.quad(*[((x, y, z), uvf(x, y)) for x, y in pts])


def band(mesh, poly, z0, z1, closed=True):
    """Side walls mapped onto the side strip (rows 0.55..0.95 of the atlas)."""
    per = [0.0]
    n = len(poly) if closed else len(poly) - 1
    for i in range(n):
        p0, p1 = poly[i], poly[(i + 1) % len(poly)]
        per.append(per[-1] + math.dist(p0, p1))
    tot = per[-1] or 1
    for i in range(n):
        p0, p1 = poly[i], poly[(i + 1) % len(poly)]
        u0, u1 = per[i] / tot, per[i + 1] / tot
        mesh.quad(((p0[0], p0[1], z0), (u0, 0.95)), ((p1[0], p1[1], z0), (u1, 0.95)),
                  ((p1[0], p1[1], z1), (u1, 0.55)), ((p0[0], p0[1], z1), (u0, 0.55)))


def planar(bb, u0, uw, r0, rh, mirror=False):
    xmin, ymin, xmax, ymax = bb
    w, h = xmax - xmin, ymax - ymin

    def f(x, y):
        fx = (xmax - x) / w if mirror else (x - xmin) / w
        return (u0 + fx * uw, r0 + (ymax - y) / h * rh)
    return f


def slab_body(poly, t, name):
    """Extruded convex outline: back cap, side walls, front cap (in that order)."""
    m = Mesh()
    bb = bbox(poly)
    fan(m, poly, -t / 2, planar(bb, 0.5, 0.5, 0.0, 0.5, mirror=True))
    band(m, poly, -t / 2, t / 2)
    fan(m, poly, t / 2, planar(bb, 0.0, 0.5, 0.0, 0.5))
    m.write(f"{OUT}/{name}.obj")
    return bb


def label_and_gloss(poly, z, crop, name):
    """Label (cover art, cropped to crop=(u0,r0,u1,r1)) and N gloss variants."""
    bb = bbox(poly)
    u0, r0, u1, r1 = crop
    m = Mesh()
    fan(m, poly, z, planar(bb, u0, u1 - u0, r0, r1 - r0))
    m.write(f"{OUT}/{name}_label.obj")
    for i in range(GLOSS_STEPS):
        s = 0.5 * i / (GLOSS_STEPS - 1)
        g = Mesh()
        fan(g, poly, z + 0.004, planar(bb, s, 0.5, 0.0, 1.0))
        g.write(f"{OUT}/{name}_gloss{i}.obj")
    return bb


def ring_label_and_gloss(r_in, r_out, z, name, seg=64):
    bb = (-r_out, -r_out, r_out, r_out)
    m = Mesh()
    ring(m, r_in, r_out, z, planar(bb, 0, 1, 0, 1), seg)
    m.write(f"{OUT}/{name}_label.obj")
    for i in range(GLOSS_STEPS):
        s = 0.5 * i / (GLOSS_STEPS - 1)
        g = Mesh()
        ring(g, r_in, r_out, z + 0.004, planar(bb, s, 0.5, 0, 1), seg)
        g.write(f"{OUT}/{name}_gloss{i}.obj")


# ---------------------------------------------------------------------- textures
TEX = 512


def region_painter(img, u0, r0, uw, rh, bb):
    """Return a function mapping model (x, y) -> pixel coords inside an atlas region."""
    xmin, ymin, xmax, ymax = bb

    def px(x, y, mirror=False):
        fx = (xmax - x) / (xmax - xmin) if mirror else (x - xmin) / (xmax - xmin)
        return ((u0 + fx * uw) * TEX, (r0 + (ymax - y) / (ymax - ymin) * rh) * TEX)
    return px


def vgrad(draw, box, top, bot):
    x0, y0, x1, y1 = [int(v) for v in box]
    for y in range(y0, y1):
        t = (y - y0) / max(1, y1 - y0 - 1)
        draw.line([(x0, y), (x1, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))


def side_strip(draw, base):
    """Bottom half of the atlas: edge strip, lit across its thickness."""
    for r in range(TEX // 2, TEX):
        t = (r - TEX // 2) / (TEX // 2)
        k = 1.0 - abs(t - 0.4) * 0.9  # bright band, rolled-off edges
        draw.line([(0, r), (TEX, r)], fill=tuple(int(min(255, c * (0.7 + 0.6 * k))) for c in base) + (255,))


def tex_vita_card(bb, outline):
    img = Image.new("RGBA", (TEX, TEX), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    # front (left top quarter)
    vgrad(d, (0, 0, TEX // 2, TEX // 2), (58, 60, 66), (34, 35, 39))
    pf = region_painter(img, 0, 0, 0.5, 0.5, bb)
    # grip ridges above the label
    for k in range(5):
        y = 0.73 + k * 0.035
        d.line([pf(-0.45, y), pf(0.38, y)], fill=(78, 80, 88, 255), width=2)
        d.line([pf(-0.45, y - 0.012), pf(0.38, y - 0.012)], fill=(22, 23, 26, 255), width=1)
    # rim highlight
    d.line([pf(*p) for p in outline] + [pf(*outline[0])], fill=(110, 113, 122, 255), width=3)
    # back (right top quarter)
    vgrad(d, (TEX // 2, 0, TEX, TEX // 2), (44, 45, 50), (26, 27, 30))
    pb = region_painter(img, 0.5, 0, 0.5, 0.5, bb)
    # recessed panel + gold contacts near the bottom edge
    d.rounded_rectangle([pb(0.55, -0.35, True), pb(-0.55, -0.88, True)], radius=6, fill=(30, 31, 35, 255))
    for k in range(9):
        x = -0.48 + k * 0.12
        a, b = pb(x + 0.04, -0.62, True), pb(x - 0.04, -0.84, True)
        d.rectangle([min(a[0], b[0]), a[1], max(a[0], b[0]), b[1]], fill=(201, 164, 82, 255))
        d.line([(min(a[0], b[0]), a[1]), (max(a[0], b[0]), a[1])], fill=(245, 214, 140, 255), width=2)
    d.line([pb(*p, True) for p in outline] + [pb(*outline[0], True)], fill=(95, 98, 106, 255), width=3)
    side_strip(d, (70, 72, 80))
    return img


def tex_disc_ps1(R, r_hole, r_lab_in, r_lab_out):
    img = Image.new("RGBA", (TEX, TEX), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    bb = (-R, -R, R, R)
    pf = region_painter(img, 0, 0, 0.5, 0.5, bb)
    # front: silver hub rings + clear outer rim (label covers the middle)
    c = pf(0, 0)
    scale = (TEX * 0.5) / (2 * R)
    for r in range(int(R * scale), 0, -1):
        rr = r / scale
        if rr > r_lab_out:
            col = (196, 202, 210)
        elif rr > r_lab_in:
            col = (170, 174, 182)
        elif rr > 0.30 * R:
            col = (214, 218, 224)  # mirror band
        elif rr > 0.24 * R:
            col = (150, 156, 166)  # clamping ring groove
        else:
            col = (226, 230, 236)  # clear hub
        if int(rr * 400) % 9 == 0 and rr < r_lab_in:
            col = tuple(max(0, v - 25) for v in col)
        d.ellipse([c[0] - r, c[1] - r, c[0] + r, c[1] + r], fill=col + (255,))
    # back: PlayStation black disc with a subtle sheen
    pb = region_painter(img, 0.5, 0, 0.5, 0.5, bb)
    cb = pb(0, 0, True)
    for r in range(int(R * scale), 0, -1):
        rr = r / scale
        v = 18 + int(22 * math.sin(rr / R * math.pi) ** 2)
        col = (v, v, v + 6) if rr > 0.3 * R else (200, 205, 212)
        d.ellipse([cb[0] - r, cb[1] - r, cb[0] + r, cb[1] + r], fill=col + (255,))
    # sheen streak
    sheen = Image.new("RGBA", (TEX // 2, TEX // 2), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.polygon([(60, 0), (110, 0), (196, 256), (146, 256)], fill=(120, 120, 140, 70))
    sheen = sheen.filter(ImageFilter.GaussianBlur(14))
    mask = Image.new("L", (TEX // 2, TEX // 2), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, TEX // 2 - 1, TEX // 2 - 1], fill=255)
    region = img.crop((TEX // 2, 0, TEX, TEX // 2))
    region.alpha_composite(Image.composite(sheen, Image.new("RGBA", sheen.size, (0, 0, 0, 0)), mask))
    img.paste(region, (TEX // 2, 0))
    side_strip(d, (190, 196, 205))
    return img


def tex_umd(bb, outline, disc_c, disc_r):
    img = Image.new("RGBA", (TEX, TEX), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    vgrad(d, (0, 0, TEX // 2, TEX // 2), (92, 96, 104), (60, 63, 70))
    pf = region_painter(img, 0, 0, 0.5, 0.5, bb)
    d.line([pf(*p) for p in outline] + [pf(*outline[0])], fill=(150, 154, 162, 255), width=4)
    # back: smoked shell with the disc window
    vgrad(d, (TEX // 2, 0, TEX, TEX // 2), (70, 73, 80), (48, 50, 56))
    pb = region_painter(img, 0.5, 0, 0.5, 0.5, bb)
    cx, cy = disc_c
    a, b = pb(cx + disc_r, cy + disc_r, True), pb(cx - disc_r, cy - disc_r, True)
    d.ellipse([a[0], a[1], b[0], b[1]], fill=(176, 184, 196, 255))
    for k, (rf, col) in enumerate([(0.85, (205, 210, 220)), (0.62, (160, 170, 186)), (0.30, (120, 124, 132)), (0.18, (30, 30, 34))]):
        rr = disc_r * rf
        a, b = pb(cx + rr, cy + rr, True), pb(cx - rr, cy - rr, True)
        d.ellipse([a[0], a[1], b[0], b[1]], fill=col + (255,))
    d.line([pb(*p, True) for p in outline] + [pb(*outline[0], True)], fill=(130, 134, 142, 255), width=4)
    side_strip(d, (96, 100, 108))
    return img


def tex_gloss():
    """Soft diagonal highlight in the middle of a wide strip; the gloss models
    slide a half-width window across it as the device tilts."""
    w, h = 512, 256
    img = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(226, 0), (262, 0), (300, h), (264, h)], fill=(255, 255, 255, 120))
    d.polygon([(276, 0), (286, 0), (324, h), (314, h)], fill=(255, 255, 255, 70))
    img = img.filter(ImageFilter.GaussianBlur(9))
    return img


def tex_shadow():
    w = 128
    img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([18, 34, w - 18, w - 34], fill=(0, 0, 0, 150))
    return img.filter(ImageFilter.GaussianBlur(12))


def shadow_model():
    m = Mesh()
    w, dpt = 1.0, 0.55
    m.quad(((-w, 0, -dpt), (0, 0)), ((w, 0, -dpt), (1, 0)), ((w, 0, dpt), (1, 1)), ((-w, 0, dpt), (0, 1)))
    m.write(f"{OUT}/float_shadow.obj")


# --------------------------------------------------------------------- the media
def vita_card():
    W, H, T = 1.36, 1.85, 0.09
    hw, hh, ch, rc = W / 2, H / 2, 0.20, 0.06
    outline = (rounded_corner(-hw + rc, -hh + rc, rc, 180, 270)
               + rounded_corner(hw - rc, -hh + rc, rc, 270, 360)
               + [(hw, hh - ch), (hw - ch, hh)]
               + rounded_corner(-hw + rc, hh - rc, rc, 90, 180))
    bb = slab_body(outline, T, "float_vita_body")
    # label: 0.78 aspect (matches Vita cover art), small rounded corners
    lw, lh, lb, lr = 1.20, 1.54, -0.85, 0.035
    lx0, lx1, ly0, ly1 = -lw / 2, lw / 2, lb, lb + lh
    lab = (rounded_corner(lx0 + lr, ly0 + lr, lr, 180, 270, 3) + rounded_corner(lx1 - lr, ly0 + lr, lr, 270, 360, 3)
           + rounded_corner(lx1 - lr, ly1 - lr, lr, 0, 90, 3) + rounded_corner(lx0 + lr, ly1 - lr, lr, 90, 180, 3))
    label_and_gloss(lab, T / 2 + 0.004, (0.0, 0.0, 1.0, 1.0), "float_vita")
    tex_vita_card(bb, outline).save(f"{OUT}/float_vita_body.png")


def ps1_disc():
    R, r_hole, T = 0.925, 0.115, 0.03
    r_li, r_lo = 0.33 * R, 0.975 * R
    m = Mesh()
    seg = 48
    bb = (-R, -R, R, R)
    ring(m, r_hole, R, -T / 2, planar(bb, 0.5, 0.5, 0, 0.5, mirror=True), seg)
    circ = [(R * math.cos(2 * math.pi * i / seg), R * math.sin(2 * math.pi * i / seg)) for i in range(seg)]
    hole = [(r_hole * math.cos(2 * math.pi * i / seg), r_hole * math.sin(2 * math.pi * i / seg)) for i in range(seg)]
    band(m, circ, -T / 2, T / 2)
    band(m, hole, -T / 2, T / 2)
    ring(m, r_hole, R, T / 2, planar(bb, 0, 0.5, 0, 0.5), seg)
    m.write(f"{OUT}/float_psx_body.obj")
    ring_label_and_gloss(r_li, r_lo, T / 2 + 0.003, "float_psx", seg)
    tex_disc_ps1(R, r_hole, r_li, r_lo).save(f"{OUT}/float_psx_body.png")


def psp_umd():
    W, H, T = 1.80, 1.78, 0.12
    hw, hh, rc = W / 2, H / 2, 0.14
    arc_c = -hh + hw  # bottom is a semicircle the width of the shell
    outline = ([(hw * math.cos(math.radians(a)), arc_c + hw * math.sin(math.radians(a))) for a in range(180, 361, 10)]
               + rounded_corner(hw - rc, hh - rc, rc, 0, 90)
               + rounded_corner(-hw + rc, hh - rc, rc, 90, 180))
    # de-dup the seam point
    outline = [p for i, p in enumerate(outline) if i == 0 or math.dist(p, outline[i - 1]) > 1e-6]
    bb = slab_body(outline, T, "float_psp_body")
    # label: inset outline, PSP art (0.5625) cropped to the top of the cover
    k = 0.90
    lab = [(x * k, arc_c * (1 - k) + y * k + 0.03) for x, y in outline]
    lbb = bbox(lab)
    aspect = (lbb[2] - lbb[0]) / (lbb[3] - lbb[1])
    hfrac = 0.5625 / aspect
    label_and_gloss(lab, T / 2 + 0.004, (0.0, 0.04, 1.0, 0.04 + hfrac), "float_psp")
    tex_umd(bb, outline, (0, arc_c), hw * 0.86).save(f"{OUT}/float_psp_body.png")


if __name__ == "__main__":
    vita_card()
    ps1_disc()
    psp_umd()
    shadow_model()
    tex_gloss().save(f"{OUT}/float_gloss.png")
    tex_shadow().save(f"{OUT}/float_shadow.png")
    print("\n".join(sorted(os.listdir(OUT))))
