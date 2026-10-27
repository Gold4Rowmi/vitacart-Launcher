# RetroFlow Float – deploy from this folder

1. First time only: copy `RetroFlowFloat_v8.5.0.vpk` to the Vita and install it with
   VitaShell (it installs as its own bubble, "RetroFlow Float", next to RetroFlow).
2. Make sure vitacompanion is running on the Vita and `../vita.env` has its IP.
3. From WSL, in this folder:

       bash deploy.sh            # quick update of index.lua + float models, then launch
       bash deploy.sh install    # full VPK push (only needed if the engine changes)

`0001..0007-*.patch` are the source changes for the fork (git am). `umd_labels.py`
builds real UMD disc labels (see its header).

## Rebuilding without the original RetroFlow VPK (2026-10-27)
Source lives in WSL at `~/retroflow` (branch `float`, patches applied). Python venv `~/rfvenv`.

    cd ~/retroflow/fork/floating-media
    ~/rfvenv/bin/python gen.py DATA
    ~/rfvenv/bin/python rebuild_float.py "<this folder>/RetroFlowFloat_v8.5.0.vpk" ../../src/index.lua DATA "<this folder>/RetroFlowFloat_v8.5.1.vpk"
    cd "<this folder>" && bash deploy.sh

Patches 0008+ are the media redesign work (UMD, Vita card). `VITA_CONTEXT.md` one level up has the full session log.
