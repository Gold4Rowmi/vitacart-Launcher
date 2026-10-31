-- Runs the patched FLOAT/DrawCover code against mocked lpp-vita APIs and logs
-- every Render.drawModel call, frames separated by "FRAME".
-- usage: luajit harness.lua float_extract.lua [frames] > draws.txt
-- Simulates: gentle tilt, a gyro twist at 2.5s, then a touch drag at 4s that
-- spins the selected item round to its back and flicks it on release at 5.2s.
local now_ms, sim_t = 0, 0
local touch_x, touch_y = nil, nil

Timer = { new = function() return {} end, getTime = function() return now_ms end }
FILTER_LINEAR = 1
Graphics = { loadImage = function(p) return p end, setImageFilters = function() end }
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
FREAD = 0
System = { getAsyncState = function() return 1 end, createDirectory = function() end, copyFile = function() end,
    openFile = function() return 1 end, writeFile = function() end, closeFile = function() end,
    listDirectory = function() return {} end, readFile = function() return "FLOAT_DATA_VERSION_PLACEHOLDER" end,
    doesFileExist = function(p)
    if p:find("version%.txt$") then return true end
    p = p:gsub("^ux0:/data/RetroFlowFloat/DATA/", "app0:/DATA/")
    if p:find("umd_real") then return true end
    local rel = p:match("^app0:/DATA/(.*)$")
    if rel and DATA_DIR then local f = io.open(DATA_DIR .. "/" .. rel, "rb"); if f then f:close(); return true end end
    return false
end }
Controls = {
    enableGyro = function() end, disableGyro = function() end,
    readAccel = function() return 0.30 * math.sin(sim_t * 1.3), -0.6 + 0.18 * math.sin(sim_t * 0.9), -0.8 end,
    readGyro = function()
        local kick = (sim_t > 2.5 and sim_t < 2.65) and 3.0 or 0
        return 0.05 * math.cos(sim_t * 0.9), 0.4 * math.cos(sim_t * 1.3) + kick, 0
    end,
    readTouch = function() return touch_x, touch_y end,
}

showView, smoothScrolling, showCat, quick_scrolling_factor = 0, 1, 1, 0
hideBoxes, setReflections, showMenu = 0, 2, 0
imgCoverTmp = "noimg"
function load_cover_models_for_app_type() end

local src = assert(io.open(arg[1])):read("*a")
src = src:gsub("local function DrawCover%(", "function DrawCover(")
assert(loadstring(src, "float_extract"))()

-- library: apptypes 3=PS1, 1=Vita, 2=PSP (one PSP has a real disc scan)
local def = {
    { app_type = 3, icon_path = "art_psx2.png" }, { app_type = 1, icon_path = "art_vita2.png" },
    { app_type = 2, icon_path = "art_psp.png" }, { app_type = 3, icon_path = "art_psx.png" },
    { app_type = 2, icon_path = "umd_real.png" },
}
for _, f in ipairs(def) do f.icon = FLOAT.label_path(f):gsub("%.png$", "") end

local frames, fps = tonumber(arg[2]) or 120, 30
local p, targetX = 3, 2
for f = 1, frames do
    sim_t = (f - 1) / fps
    now_ms = math.floor(sim_t * 1000)
    -- touch drag: 4.0s..5.2s, sweeping 420px right (spins ~190 deg) and 40px down
    if sim_t >= 4.0 and sim_t < 5.2 then
        local k = (sim_t - 4.0) / 1.2
        touch_x, touch_y = 480 - 60 + 420 * k, 250 + 40 * k
    else
        touch_x, touch_y = nil, nil
    end
    local want_p = sim_t >= 6.0 and 5 or 3
    if want_p ~= p then p = want_p end
    targetX = targetX + ((p - 1) - targetX) * 0.17
    print("FRAME")
    FLOAT.update()
    FLOAT.touch(touch_x, touch_y)
    FLOAT.deferred = nil
    FLOAT.cur_def = def
    for l, file in ipairs(def) do
        DrawCover((l - 1) - targetX, -0.6, "", file.icon, l == p, file.app_type, l)
    end
    FLOAT.flush()
    if f % 15 == 0 then
        io.stderr:write(string.format("t=%.1f grab=%s ang_y=%.1f ang_x=%.1f vel_y=%.0f\n", sim_t, tostring(FLOAT.grab), FLOAT.ang_y, FLOAT.ang_x, FLOAT.vel_y))
    end
end
