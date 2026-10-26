# Floating physical media (fork)

Turns the 3D coverflow into floating physical media: Vita games show as game
cards, PSP games as UMDs and PS1 games as discs, each with the cover art as the
label. Every other system keeps its box but floats too. Tilting the Vita turns
the media (accelerometer), a quick twist makes it wobble (gyro), and a gloss
streak slides across the selected label.

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
