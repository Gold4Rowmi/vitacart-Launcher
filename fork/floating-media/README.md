# Floating physical media (fork)

Turns the 3D coverflow into floating physical media: Vita games show as game
cards, PSP games as UMDs and PS1 games as discs, each with the cover art as the
label. Every other system keeps its box but floats too. Tilting the Vita turns
the media (accelerometer), a quick twist makes it wobble (gyro), and a gloss
streak slides across the selected label.

## Touch
Touch the selected item and drag to spin it, right round to the back; let go
and it springs back (a quick flick carries on spinning first). Swiping anywhere
else still scrolls the library as normal.

## Data-side diffraction
The back of the UMD and the PS1 disc carry a translucent rainbow layer that is
spun about the disc axis by the viewing angle (`FLOAT.sheen_angle`), so the
spectral lobes sweep round as you tilt or spin the disc, like a real CD.

## Real UMD labels
`umd_labels.py` builds `<GAME ID>_umd.png` disc labels from umdatabase.net
scans for every PSP game in your library (run it on a PC, see its header), or
imports disc images you already have with `--from-folder` (e.g. ScreenScraper
"physical media" PNGs fetched by Skraper or ES-DE).
RetroFlow Float uses them as the round label on the UMD; without one the box
art is used, cropped to a circle.

## Turn it on
Settings → Theme → Reflection effect → cycle to **Floating media**
(order: On → Floating media → Off). Stored as `Reflections=2` in `config.dat`.

## Files
- `DATA/` – models and textures; copied into the VPK's `DATA/` folder.
- `gen.py` – regenerates everything in `DATA/` (`python3 gen.py DATA`).
- `preview.py`, `harness.lua`, `render_log.py` – an offline preview that runs the
  real Lua against mocked APIs and renders it the way the Vita does (no depth
  test, painter's order).

## Code changes (`src/index.lua`, all marked `FLOATING PHYSICAL MEDIA (fork)`)
- `FLOAT` table before `DrawCover`: loading, sensors, spring and drawing.
- `DrawCover` → `FLOAT.draw_cover_real`, plus a small `DrawCover` wrapper that
  holds the selected item back so it draws on top (`FLOAT.flush`).
- Box draw calls get the float offsets added (all zero when the mode is off).
- The cover loop calls `FLOAT.update()` before drawing and `FLOAT.flush()` after.
- The Reflections setting gets a third value.

## Tuning (top of the `FLOAT` table)
`INVERT_X` / `INVERT_Y` if tilt feels backwards, `TILT_MAX_YAW` / `TILT_MAX_PITCH`
for how far it turns, `SPRING` / `DAMP` / `GYRO_KICK` for the wobble, and
`RECENTER` for how fast a new holding angle becomes "level".

## Side-by-side install ("RetroFlow Float")
`make_sidebyside.py` turns the patched build into a separate app so the normal
RetroFlow install is never touched:

    python3 make_sidebyside.py <unzipped RetroFlow VPK> ../../src/index.lua DATA RetroFlowFloat.vpk

- Title ID `RFFLOAT01`, name "RetroFlow Float", icon with a FLOAT badge.
- Own data folder `ux0:/data/RetroFlowFloat/` (settings, caches, favourites,
  collections, overrides). First launch copies these from `ux0:/data/RetroFlow/`,
  which is only ever read.
- Shared, read from RetroFlow: ROMS, COVERS, BACKGROUNDS, ICONS, WALLPAPER, MUSIC.
  Covers downloaded from either app go into the shared COVERS folder.
- Recovery/reset in RetroFlow Float only affects RetroFlowFloat.
- Adrenaline games still launch through the shared RetroFlow Adrenaline Launcher.
- To remove it: delete the RetroFlow Float bubble and `ux0:/data/RetroFlowFloat/`.
