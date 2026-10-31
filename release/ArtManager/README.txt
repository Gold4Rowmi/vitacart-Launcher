RetroFlow Float Art Manager
===========================

One file, nothing to install. Manages the physical-media art that RetroFlow Float shows:
PS Vita card labels, PSP UMD labels and PS1 disc labels.

Windows:  double-click RetroFlowFloat-ArtManager.exe   (Windows may ask once to allow it)
Mac:      RetroFlowFloat-ArtManager-mac-arm64 (Apple silicon) or -mac-intel; run from Terminal
          the first time:  chmod +x <file>; ./<file>
Linux:    chmod +x RetroFlowFloat-ArtManager-linux; ./RetroFlowFloat-ArtManager-linux

A browser window opens at http://localhost:8765.

Needs: the Vita awake, on the same Wi-Fi, with vitacompanion running (FTP 1337, commands
1338). Put the Vita's IP in the box at the top and press Save. RetroFlow Float must have
been launched at least once so its game cache exists.

What you can do
- Drop a disc scan or a photo of the UMD/disc (or choose a file) on any PSP or PS1 game. An
  editor opens with the disc found automatically; drag the circle or resize it (slider or
  mouse wheel) if it is off, then "Use this disc". The label is cut from inside the circle. Drop a cover on a Vita game to replace its card art.
- Vita covers become the card's sticker: art plus the white strip with the game's ID printed on it.
- "Show as" on each game: Vita card, UMD, PS1 disc or PS1 jewel case, whatever media Float should draw.
- Options: "Printed sticker look on new art" (CMYK-print texture of a real label) and
  "Print look on all stock covers" (every Vita/PSP/PS1 cover in one go, with progress and cancel).
- Remove: deletes the custom art for that game (Undo in the toast puts it back).
- A bar at the bottom counts changes not yet on the Vita and relaunches Float for you.
- Add wallpaper: adds a 960x544 wallpaper to Float's own wallpaper list.
- Relaunch Float: closes and reopens RetroFlow Float on the Vita so changes show.

Where files go: only ux0:/data/RetroFlowFloat/PRINT/COVERS/<system>/ and
ux0:/data/RetroFlowFloat/WALLPAPER/. Your normal RetroFlow is never touched.
A config.json and a cache/ folder (cover thumbnails) are written next to the program.

If an upload fails with "cannot allocate memory", a game is running on the Vita; close it.
