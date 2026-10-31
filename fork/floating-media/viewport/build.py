#!/usr/bin/env python3
"""Build the floating-media 3D viewport (a web page) from index.lua + one or more model sets.

    ~/rfvenv/bin/python build.py OUT_DIR [name=DATA_DIR ...]      default set: work=../DATA

Runs the real FLOAT Lua code (same mocks as harness.lua) to read the layer tables and to
record reference draw lists for a grid of poses, so the page's JS port of draw_layers can
check itself against the Lua. Copies the model sets and sample art into OUT_DIR and writes
OUT_DIR/scene.json + OUT_DIR/files.json (the list of files to publish).
"""
import json, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
INDEX = os.path.join(HERE, "..", "..", "..", "src", "index.lua")
ART = os.path.join(HERE, "art")

def extract_float(src_path):
    lines = open(src_path, encoding="utf-8").read().split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("-- FLOATING PHYSICAL MEDIA (fork)"))
    flush = next(i for i, l in enumerate(lines) if l.startswith("function FLOAT.flush()"))
    end = next(i for i in range(flush, len(lines)) if lines[i] == "end")
    return "\n".join(lines[start:end + 1])

MOCKS = r"""
Timer = { new = function() return {} end, getTime = function() return 0 end }
FILTER_LINEAR = 1
Graphics = { loadImage = function(p) return p end, setImageFilters = function() end, getImageWidth = function() return 0 end, getImageHeight = function() return 0 end }
Render = {
    loadObject = function(path, img) return { path = path, tex = img } end,
    useTexture = function(m, img) m.tex = img end,
    drawModel = function(m, x, y, z, rx, ry, rz) PY_DRAW(m.path, tostring(m.tex), x, y, z, rx, ry, rz) end,
    setCamera = function() end,
}
FREAD = 0
System = { getAsyncState = function() return 1 end, createDirectory = function() end, copyFile = function() end,
    openFile = function() return 1 end, writeFile = function() end, closeFile = function() end,
    listDirectory = function() return {} end, readFile = function() return "FLOAT_DATA_VERSION_PLACEHOLDER" end,
    doesFileExist = function(p)
    if p:find("version%.txt$") then return true end
    p = p:gsub("^ux0:/data/RetroFlowFloat/DATA/", "app0:/DATA/")
    local rel = p:match("^app0:/DATA/(.*)$")
    if rel and DATA_DIR then local f = io.open(DATA_DIR .. "/" .. rel, "rb"); if f then f:close(); return true end end
    return false
end }
Controls = {}
showView, smoothScrolling, showCat, quick_scrolling_factor = 0, 1, 1, 0
hideBoxes, setReflections, showMenu = 0, 2, 0
imgCoverTmp = "noimg"
function load_cover_models_for_app_type() end
"""

def main():
    out = sys.argv[1]
    sets = dict(a.split("=", 1) for a in sys.argv[2:]) or {"work": os.path.join(HERE, "..", "DATA")}
    import lupa.lua51 as lupa
    L = lupa.LuaRuntime(unpack_returned_tuples=True)
    draws = []
    L.globals().PY_DRAW = lambda path, tex, x, y, z, rx, ry, rz: draws.append([os.path.basename(path), tex, x, y, z, rx, ry, rz])
    L.globals().DATA_DIR = os.path.abspath(next(iter(sets.values())))
    L.execute(MOCKS)
    src = extract_float(INDEX).replace("local function DrawCover(", "function DrawCover(")
    L.execute(src)
    F = L.globals().FLOAT
    F.load()   # step queue; the mock clock never advances so one call runs every step

    kinds = sorted(k for k in F.LAYERS.keys())
    scene = {
        "steps": int(F.GLOSS_STEPS), "grime": bool(F.GRIME), "shine": {k: float(F.SHINE[k]) for k in kinds if F.SHINE[k]}, "shineSpan": float(F.SHINE_SPAN), "reflectOn": bool(F.REFLECT), "discPhysics": bool(F.DISC_PHYSICS), "discPlay": float(F.DISC_PLAY), "discLayers": {k: True for k in ("label", "hub", "discback", "sheen") if F.DISC_LAYERS[k]}, "reflfade": {"obj": "float_floor.obj", "tex": "float_floor.png"},
        "half": {k: float(F.HALF[k]) for k in kinds},
        "yoff": {k: float(F.YOFF[k]) for k in kinds},
        "layers": {}, "labeldisc": {}, "sets": list(sets), "ref": [],
        "shadow": {"obj": os.path.basename(F.shadow.path), "tex": os.path.basename(F.shadow_img)},
    }
    for k in kinds:
        m = F.media[k]
        layers = []
        for i in range(1, len(m.layers) + 1):
            Lr = m.layers[i]
            e = {"name": Lr.name}
            if Lr.set:
                e["set"] = [os.path.basename(Lr.set[g].path) for g in range(int(F.GLOSS_STEPS))]
                e["tex"] = os.path.basename(Lr.set[0].tex)
                e["back"] = bool(Lr.back)
                if Lr.imgs and Lr.imgs[0]:
                    e["texset"] = [os.path.basename(Lr.imgs[g]) for g in range(int(F.GLOSS_STEPS))]
            else:
                e["obj"] = os.path.basename(Lr.obj.path)
                e["tex"] = os.path.basename(Lr.obj.tex)   # "noimg" for the label = cover art
                if Lr.side: e["side"] = Lr.side; e["back"] = bool(Lr.back)
                if Lr.band: e["band"] = True
                if Lr.grime: e["grime"] = True; e["back"] = bool(Lr.back)
                if Lr.shine: e["shine"] = True
                if F.DETAIL and F.DETAIL[Lr.name]: e["detail"] = True
                if F.BACK_ONLY and F.BACK_ONLY[Lr.name]: e["backOnly"] = float(F.BACK_ONLY[Lr.name])
            layers.append(e)
        scene["layers"][k] = layers
        if m.labeldisc: scene["labeldisc"][k] = os.path.basename(m.labeldisc.path)
        if m.shadow_p:
            scene.setdefault("shadowSet", {})[k] = {"tex": os.path.basename(m.shadow_img), "steps": int(F.SHADOW_STEPS),
                "p": [os.path.basename(m.shadow_p[i].path) for i in range(int(F.SHADOW_STEPS))], "n": [os.path.basename(m.shadow_n[i].path) for i in range(int(F.SHADOW_STEPS))]}
        if m.reflect:
            scene.setdefault("reflect", {})[k] = [{"name": m.reflect[i].name, "obj": os.path.basename(m.reflect[i].obj.path), "tex": os.path.basename(m.reflect[i].obj.tex)} for i in range(1, len(m.reflect) + 1)]

    # reference draw lists straight from the Lua, for the page's self-check
    for k in kinds:
        for disc in ([False, True] if k in scene["labeldisc"] else [False]):
            for rx in (0, 20, -20, 60, -75):
                for ry in (0, 30, -30, 90, 150, 180, 210, 270, 330, 45, -45, 400, -500):
                    for sel in (True, False):
                        del draws[:]
                        F.draw_layers(k, "ART", disc, 0.0, 0.0, 0.0, float(rx), float(ry), 3.0, sel)
                        scene["ref"].append({"kind": k, "disc": disc, "rx": rx, "ry": ry, "sel": sel,
                                             "draws": [[d[0], d[1], round(d[7], 3)] for d in draws]})

    os.makedirs(out, exist_ok=True)
    files = []
    for name, d in sets.items():
        dst = os.path.join(out, "sets", name)
        os.makedirs(dst, exist_ok=True)
        for f in sorted(os.listdir(d)):
            if f.startswith("float_") and (f.endswith(".obj") or f.endswith(".png")):
                pub = f + ".txt" if f.endswith(".obj") else f   # the artifact host serves .txt, not .obj
                shutil.copy(os.path.join(d, f), os.path.join(dst, pub))
                files.append(f"sets/{name}/{pub}")
    art = {"vita": [], "psp": [], "psx": [], "bg": []}
    os.makedirs(os.path.join(out, "art"), exist_ok=True)
    for f in sorted(os.listdir(ART)):
        shutil.copy(os.path.join(ART, f), os.path.join(out, "art"))
        files.append(f"art/{f}")
        for k in art:
            if f.startswith(f"art_{k}") or (k == "bg" and f.startswith("bg_")): art[k].append(f)
    scene["art"] = art
    json.dump(scene, open(os.path.join(out, "scene.json"), "w"), separators=(",", ":"))
    files.append("scene.json")
    shutil.copy(os.path.join(HERE, "index.html"), out) if os.path.exists(os.path.join(HERE, "index.html")) else None
    json.dump([{"path": f} for f in files], open(os.path.join(out, "files.json"), "w"))
    print(f"{out}: {len(files)} files, {len(scene['ref'])} reference poses, layers:",
          {k: len(v) for k, v in scene["layers"].items()})

if __name__ == "__main__":
    main()
