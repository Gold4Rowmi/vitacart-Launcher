-- Runs the patched FLOAT/DrawCover code against mocked lpp-vita APIs and logs
-- every Render.drawModel call as one line per draw, frames separated by "FRAME".
-- usage: luajit harness.lua float_extract.lua > draws.txt
local now_ms = 0
local sim_t = 0

Timer = { new = function() return {} end, getTime = function() return now_ms end }
FILTER_LINEAR = 1
Graphics = {
    loadImage = function(p) return p end,
    setImageFilters = function() end,
}
Render = {
    loadObject = function(path, img) return { path = path, tex = img } end,
    useTexture = function(m, img) m.tex = img end,
    drawModel = function(m, x, y, z, rx, ry, rz)
        for _, v in ipairs({ x, y, z, rx, ry, rz }) do
            assert(type(v) == "number" and v == v, "bad drawModel arg for " .. m.path)
        end
        print(string.format("%s %s %.4f %.4f %.4f %.3f %.3f %.3f", m.path, tostring(m.tex), x, y, z, rx, ry, rz))
    end,
    setCamera = function() end,
}
-- Simulated hands: slow tilt left/right and forward/back, plus a quick twist at 2.5s
Controls = {
    enableGyro = function() end, disableGyro = function() end,
    readAccel = function()
        return 0.30 * math.sin(sim_t * 1.3), -0.6 + 0.18 * math.sin(sim_t * 0.9), -0.8
    end,
    readGyro = function()
        local kick = (sim_t > 2.5 and sim_t < 2.65) and 3.0 or 0
        return 0.05 * math.cos(sim_t * 0.9), 0.4 * math.cos(sim_t * 1.3) + kick, 0
    end,
}

-- Globals DrawCover reads (these are upvalues in the real file)
showView, smoothScrolling, showCat, quick_scrolling_factor = 0, 1, 1, 0
hideBoxes, setReflections = 0, 2
imgCoverTmp = "noimg"
function load_cover_models_for_app_type() end

local src = assert(io.open(arg[1])):read("*a")
-- expose the local DrawCover for the harness
src = src:gsub("local function DrawCover%(", "function DrawCover(")
assert(loadstring(src, "float_extract"))()

-- A small library: apptypes 3=PS1, 1=Vita, 2=PSP
local def = {
    { app_type = 3, icon = "art_psx2" }, { app_type = 1, icon = "art_vita2" },
    { app_type = 1, icon = "art_vita" }, { app_type = 3, icon = "art_psx" },
    { app_type = 2, icon = "art_psp" },
}

local frames, fps = tonumber(arg[2]) or 120, 30
local p, targetX = 3, 2
for f = 1, frames do
    sim_t = (f - 1) / fps
    now_ms = math.floor(sim_t * 1000)
    -- scroll right once at 3.0s so the selection change is visible
    local want_p = sim_t >= 3.0 and 4 or 3
    if want_p ~= p then p = want_p end
    targetX = targetX + ((p - 1) - targetX) * 0.17 -- RetroFlow-like easing
    print("FRAME")
    FLOAT.update()
    FLOAT.deferred = nil
    for l, file in ipairs(def) do
        DrawCover((l - 1) - targetX, -0.6, "", file.icon, l == p, file.app_type, l)
    end
    FLOAT.flush()
end
io.stderr:write(string.format("ok: %d frames, ang_y=%.2f ang_x=%.2f\n", frames, FLOAT.ang_y, FLOAT.ang_x))
