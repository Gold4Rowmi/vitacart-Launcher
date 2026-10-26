"""Generate floating physical-media models + textures for RetroFlow.

Output matches the OBJ subset lpp-vita's Render.loadObject reads: `v`, `vt`,
`vn`, then triangular `f v/vt/vn` faces. Vertices are shared (keyed on
position + uv) because the loader resolves each face index by walking a
linked list.

The Vita has no depth test, so every piece of media is split into LAYERS,
written as separate OBJs, that index.lua draws back-to-front, or front-to-back
when the item has been spun round to show its back:

    vita : back, band, front, label*, gloss*
    psx  : back, band, front, label*, gloss*
    psp  : shellback, discback, shutter, band, label*, hub, shellfront, gloss*
    (* label is textured with the cover art, gloss only on the selected item)

All models are centred on the origin. Units match RetroFlow's covers.
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

OUT = sys.argv[1] if len(sys.argv) > 1 else "out/DATA"
os.makedirs(OUT, exist_ok=True)
GLOSS_STEPS = 9
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


# ================================================================== PS VITA CARD
def vita_card():
    W, H, T = 1.36, 1.85, 0.09
    hw, hh, ch, rc = W / 2, H / 2, 0.20, 0.06
    outline = (arc(-hw + rc, -hh + rc, rc, 180, 270) + arc(hw - rc, -hh + rc, rc, 270, 360)
               + [(hw, hh - ch), (hw - ch, hh)] + arc(-hw + rc, hh - rc, rc, 90, 180))
    bb = bbox(outline)
    Mesh().__class__  # noqa (keeps linters quiet about unused helpers)
    m = Mesh(); fan(m, outline, -T / 2, planar(bb, 0.5, 0.5, 0, 0.5, mirror=True)); m.write("float_vita_back")
    m = Mesh(); band(m, outline, -T / 2, T / 2); m.write("float_vita_band")
    m = Mesh(); fan(m, outline, T / 2, planar(bb, 0, 0.5, 0, 0.5)); m.write("float_vita_front")
    lw, lh, lb, lr = 1.20, 1.54, -0.85, 0.035
    lx0, lx1, ly0, ly1 = -lw / 2, lw / 2, lb, lb + lh
    lab = (arc(lx0 + lr, ly0 + lr, lr, 180, 270, 3) + arc(lx1 - lr, ly0 + lr, lr, 270, 360, 3)
           + arc(lx1 - lr, ly1 - lr, lr, 0, 90, 3) + arc(lx0 + lr, ly1 - lr, lr, 90, 180, 3))
    lbb = bbox(lab)
    m = Mesh(); fan(m, lab, T / 2 + 0.004, planar(lbb)); m.write("float_vita_label")
    gloss_set("float_vita", lambda g, s: fan(g, lab, T / 2 + 0.008, planar(lbb, s, 0.5, 0, 1)))

    img = Image.new("RGBA", (TEX, TEX), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    vgrad(d, (0, 0, TEX // 2, TEX // 2), (58, 60, 66), (34, 35, 39))
    pf = px_of(bb, 0, 0, 0.5, 0.5)
    for k in range(5):
        y = 0.73 + k * 0.035
        d.line([pf(-0.45, y), pf(0.38, y)], fill=(78, 80, 88, 255), width=2)
        d.line([pf(-0.45, y - 0.012), pf(0.38, y - 0.012)], fill=(22, 23, 26, 255), width=1)
    d.line([pf(*p) for p in outline] + [pf(*outline[0])], fill=(110, 113, 122, 255), width=3)
    vgrad(d, (TEX // 2, 0, TEX, TEX // 2), (44, 45, 50), (26, 27, 30))
    pb = px_of(bb, 0.5, 0, 0.5, 0.5, mirror=True)
    d.rounded_rectangle([pb(0.55, -0.35), pb(-0.55, -0.88)], radius=6, fill=(30, 31, 35, 255))
    for k in range(9):
        x = -0.48 + k * 0.12
        a, b = pb(x + 0.04, -0.62), pb(x - 0.04, -0.84)
        d.rectangle([min(a[0], b[0]), a[1], max(a[0], b[0]), b[1]], fill=(201, 164, 82, 255))
        d.line([(min(a[0], b[0]), a[1]), (max(a[0], b[0]), a[1])], fill=(245, 214, 140, 255), width=2)
    d.line([pb(*p) for p in outline] + [pb(*outline[0])], fill=(95, 98, 106, 255), width=3)
    side_strip(d, (70, 72, 80))
    img.save(f"{OUT}/float_vita_body.png")
    return H / 2


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
    gloss_set("float_psx", lambda g, s: ring(g, r_li, r_lo, T / 2 + 0.007, planar(bb, s, 0.5, 0, 1), seg))

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


def strip(mesh, A, zA, B, zB, u0, uw, rA, rB):
    """Quad strip between two outlines with matching vertex order (a bevel)."""
    per = [0.0]
    n = len(A)
    for i in range(n):
        per.append(per[-1] + math.dist(A[i], A[(i + 1) % n]))
    tot = per[-1] or 1
    for i in range(n):
        j = (i + 1) % n
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


def psp_umd():
    """Real UMD: a 60mm disc in a ~64 x 63mm shell. Circle a little larger than
    the disc, extended down into a flat base; the disc shows on both sides
    behind a clear window. Hi-fi version: bevelled edges, an inner lip around
    the window, a recessed hub, plus reflection/shade overlay layers."""
    R_d = 0.86
    R_s = R_d + 0.07
    base_y = -(R_d + 0.16)
    T, bev, rc = 0.13, 0.022, 0.10
    r_hub, r_hole = 0.30, 0.05
    R_open = R_d + 0.012          # window opening
    seg = 72
    outline = ([(R_s, 0.0)] + [(R_s, base_y + rc)] + arc(R_s - rc, base_y + rc, rc, 0, -90, 6)[1:]
               + arc(-R_s + rc, base_y + rc, rc, -90, -180, 6)[1:] + [(-R_s, 0.0)]
               + [(R_s * math.cos(math.radians(a)), R_s * math.sin(math.radians(a))) for a in range(176, 0, -4)])
    outline = outline[::-1]
    inner = offset_outline(outline, bev)
    bb = bbox(outline)
    dbb = (-R_d, -R_d, R_d, R_d)
    zf, zb = T / 2, -T / 2
    disc_zf, disc_zb = 0.004, -0.004   # the disc sits in the middle of the shell
    hub_z = disc_zf - 0.012

    # atlas regions (1024): shell front face 0..0.5 x 0..0.5, shell back 0.5..1 x 0..0.5
    #   bevel strip rows 0.52..0.56, band rows 0.58..0.68, lip rows 0.70..0.73
    #   disc back 0..0.5 x 0.5..1 (mirrored), hub 0.52..0.74 sq, shutter 0.77..0.98 x 0.52..0.74
    A = planar(bb, 0, 0.5, 0, 0.5)
    Ab = planar(bb, 0.5, 0.5, 0, 0.5, mirror=True)

    # --- back
    m = Mesh(); ring_to_outline(m, R_open, inner, zb, Ab, seg * 2); m.write("float_psp_shellback")
    m = Mesh(); strip(m, inner, zb, outline, zb + bev, 0.5, 0.5, 0.78, 0.82); m.write("float_psp_shellbackbevel")
    sx0, sx1, sy0, sy1 = -0.12, R_s - 0.02, -0.24, 0.08
    zs = zb + 0.006
    m = Mesh()
    m.quad(((sx0, sy0, zs), (0.77, 0.74)), ((sx1, sy0, zs), (0.98, 0.74)), ((sx1, sy1, zs), (0.98, 0.52)), ((sx0, sy1, zs), (0.77, 0.52)))
    m.quad(((sx0, sy0 - 0.02, zs + 0.004), (0.77, 0.745)), ((sx1, sy0 - 0.02, zs + 0.004), (0.98, 0.745)),
           ((sx1, sy0, zs + 0.004), (0.98, 0.735)), ((sx0, sy0, zs + 0.004), (0.77, 0.735)))  # lip
    m.write("float_psp_shutter")
    m = Mesh(); ring(m, 0.30, R_d, disc_zb - 0.004, planar(dbb), seg); m.write("float_psp_sheen")
    m = Mesh(); ring(m, r_hole, R_d, disc_zb, planar(dbb, 0, 0.5, 0.5, 0.5, mirror=True), seg); m.write("float_psp_discback")
    def wall_arcs(name, z_outer, z_inner):
        """Inner wall of the window split into R/T/L/B arcs (from the front)."""
        for tag, a0 in (("R", -45), ("T", 45), ("L", 135), ("B", 225)):
            m = Mesh()
            n = seg // 4
            for i in range(n):
                t0 = math.radians(a0 + 90 * i / n)
                t1 = math.radians(a0 + 90 * (i + 1) / n)
                p = [(R_open * math.cos(t0), R_open * math.sin(t0), z_outer), (R_open * math.cos(t1), R_open * math.sin(t1), z_outer),
                     ((R_open - 0.004) * math.cos(t1), (R_open - 0.004) * math.sin(t1), z_inner),
                     ((R_open - 0.004) * math.cos(t0), (R_open - 0.004) * math.sin(t0), z_inner)]
                m.quad(((p[0][0], p[0][1], p[0][2]), (0.55, 0.952)), ((p[1][0], p[1][1], p[1][2]), (0.65, 0.952)),
                       ((p[2][0], p[2][1], p[2][2]), (0.65, 0.978)), ((p[3][0], p[3][1], p[3][2]), (0.55, 0.978)))
            m.write(f"{name}{tag}")
    wall_arcs("float_psp_wallback", zb, disc_zb - 0.006)
    # --- side
    m = Mesh(); band(m, outline, zb + bev, zf - bev, 0.5, 0.5, 0.84, 0.94); m.write("float_psp_band")
    # --- front
    wall_arcs("float_psp_wall", zf, disc_zf + 0.006)
    # glass: plastic tint over the window + contact shadow where the lip overhangs the disc
    m = Mesh(); ring(m, r_hub - 0.03, R_open, disc_zf + 0.009, planar((-R_open, -R_open, R_open, R_open)), seg); m.write("float_psp_glass")
    m = Mesh(); ring(m, r_hub, R_d, disc_zf, planar(dbb, 0, 1, 0.02, 1), seg); m.write("float_psp_label")
    m = Mesh(); ring(m, r_hub, R_d, disc_zf, planar(dbb), seg); m.write("float_psp_labeldisc")
    m = Mesh(); cone_ring(m, r_hub - 0.03, hub_z, r_hub, disc_zf, lambda x, y: (0.63 + x * 0.35, 0.63 - y * 0.35), 36); m.write("float_psp_hubbevel")
    m = Mesh(); fan(m, circle(r_hub - 0.03, 36), hub_z, planar((-r_hub, -r_hub, r_hub, r_hub), 0.52, 0.22, 0.52, 0.22)); m.write("float_psp_hub")
    m = Mesh(); ring_to_outline(m, R_open, inner, zf, A, seg * 2); m.write("float_psp_shellfront")
    m = Mesh(); strip(m, inner, zf, outline, zf - bev, 0.5, 0.5, 0.78, 0.82); m.write("float_psp_shellfrontbevel")
    # overlays: clear window over the disc + the whole shell front, planar UV window (yaw-shifted)
    def overlay(g, s, z_add, scale_v=1.0):
        uv = planar(bb, s, 0.5, 0, scale_v)
        ring(g, r_hub, R_open - 0.004, zf - 0.002 + z_add, uv, seg)  # the window surface, flush with the shell
        ring_to_outline(g, R_open, inner, zf + 0.003 + z_add, uv, seg * 2)
        strip_uv = lambda pts, z: None
        # bevel as a cone-like strip with planar uv
        per = inner
        for i in range(len(inner)):
            j = (i + 1) % len(inner)
            g.quad(((inner[i][0], inner[i][1], zf + 0.003 + z_add), uv(*inner[i])),
                   ((inner[j][0], inner[j][1], zf + 0.003 + z_add), uv(*inner[j])),
                   ((outline[j][0], outline[j][1], zf - bev + 0.003 + z_add), uv(*outline[j])),
                   ((outline[i][0], outline[i][1], zf - bev + 0.003 + z_add), uv(*outline[i])))
    variant_set("float_psp_refl", lambda g, s: overlay(g, s, 0.0))
    variant_set("float_psp_shade", lambda g, s: overlay(g, s, 0.002))
    gloss_set("float_psp", lambda g, s: ring(g, r_hub, R_d, disc_zf + 0.010, planar(dbb, s, 0.5, 0, 1), seg))

    # ---------------------------------------------------------------- textures
    global TEX
    TEX = 1024
    img = Image.new("RGBA", (TEX, TEX), (0, 0, 0, 255))
    d = ImageDraw.Draw(img)
    white, shade = (236, 237, 240), (204, 206, 211)
    # shell front: soft plastic gradient + faint grain, darker ring at the opening, notches, light "PSP" area
    vgrad(d, (0, 0, TEX // 2, TEX // 2), (242, 243, 245), (220, 221, 226))
    pf = px_of(bb, 0, 0, 0.5, 0.5)
    c = pf(0, 0)
    scale = (TEX * 0.5) / (bb[2] - bb[0])
    for rr, col, w in [(R_open + 0.03, (178, 180, 186), 5), (R_open + 0.006, (120, 122, 128), 3)]:
        s_ = rr * scale
        d.ellipse([c[0] - s_, c[1] - s_, c[0] + s_, c[1] + s_], outline=col + (255,), width=w)
    for x in (-R_s + 0.22, R_s - 0.22):
        a_, b_ = pf(x - 0.05, base_y + 0.04), pf(x + 0.05, base_y + 0.14)
        d.ellipse([min(a_[0], b_[0]), min(a_[1], b_[1]), max(a_[0], b_[0]), max(a_[1], b_[1])], fill=shade + (255,))
        d.ellipse([min(a_[0], b_[0]) + 3, min(a_[1], b_[1]) + 3, max(a_[0], b_[0]) - 3, max(a_[1], b_[1]) - 3], fill=(224, 225, 229, 255))
    d.line([pf(*p) for p in inner] + [pf(*inner[0])], fill=(200, 202, 208, 255), width=2)
    # shell back
    vgrad(d, (TEX // 2, 0, TEX, TEX // 2), (226, 227, 231), (206, 207, 212))
    pb = px_of(bb, 0.5, 0, 0.5, 0.5, mirror=True)
    c = pb(0, 0)
    s_ = (R_open + 0.006) * scale
    d.ellipse([c[0] - s_, c[1] - s_, c[0] + s_, c[1] + s_], outline=(132, 134, 140, 255), width=3)
    d.line([pb(*p) for p in inner] + [pb(*inner[0])], fill=(188, 190, 196, 255), width=2)
    # bevel strips (front 0..0.5, back 0.5..1): bright top edge rolling to the side tone
    for r in range(int(0.78 * TEX) - 2, int(0.82 * TEX) + 3):
        t = min(1.0, max(0.0, (r - 0.78 * TEX) / (0.04 * TEX)))
        v = int(250 - 40 * t)
        d.line([(TEX // 2, r), (TEX, r)], fill=(v, v, v + 3, 255))
    # band
    side_strip(d, (214, 215, 219), 0.5, 0.5, 0.84, 0.94)
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
    # hub (0.52..0.74): white printed hub, faint ring, centre pin
    hx0, hy0, hx1, hy1 = int(0.52 * TEX), int(0.52 * TEX), int(0.74 * TEX), int(0.74 * TEX)
    d.ellipse([hx0, hy0, hx1, hy1], fill=(247, 248, 249, 255))
    d.ellipse([hx0 + 10, hy0 + 10, hx1 - 10, hy1 - 10], outline=(216, 218, 222, 255), width=3)
    hc = ((hx0 + hx1) / 2, (hy0 + hy1) / 2)
    d.ellipse([hc[0] - 9, hc[1] - 9, hc[0] + 9, hc[1] + 9], fill=(150, 152, 158, 255))
    # shutter (0.77..0.98 x 0.52..0.745)
    sx0p, sy0p, sx1p, sy1p = int(0.77 * TEX), int(0.52 * TEX), int(0.98 * TEX), int(0.745 * TEX)
    vgrad(d, (sx0p, sy0p, sx1p, sy1p), (200, 202, 208), (166, 168, 174))
    d.rectangle([sx0p, sy0p, sx1p, sy0p + 8], fill=(228, 230, 234, 255))
    d.rectangle([sx1p - 10, sy0p, sx1p, sy1p], fill=(138, 140, 146, 255))
    d.rectangle([sx0p, sy1p - 10, sx1p, sy1p], fill=(120, 122, 128, 255))  # lip
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
            a = min(1.0, base + sb)
            tint = (255, 252, 246) if sb > 0.05 else (210, 225, 245)
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
    img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse([18, 34, 110, 94], fill=(0, 0, 0, 150))
    img.filter(ImageFilter.GaussianBlur(12)).save(f"{OUT}/float_shadow.png")


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
    w, h = 512, 256
    img = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    d = ImageDraw.Draw(img)
    d.polygon([(226, 0), (262, 0), (300, h), (264, h)], fill=(255, 255, 255, 120))
    d.polygon([(276, 0), (286, 0), (324, h), (314, h)], fill=(255, 255, 255, 70))
    img.filter(ImageFilter.GaussianBlur(9)).save(f"{OUT}/float_gloss.png")


if __name__ == "__main__":
    hv = vita_card()
    hp = ps1_disc()
    hu, off = psp_umd()
    shadow()
    gloss_texture()
    sheen_texture()
    env_texture()
    shade_texture()
    glass_texture()
    print(f"half heights: vita={hv:.3f} psx={hp:.3f} psp={hu:.3f} (psp centre offset {off:+.3f})")
    print(len(os.listdir(OUT)), "files")
