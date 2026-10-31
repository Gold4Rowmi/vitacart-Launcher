#!/bin/bash
# deploy.sh - push RetroFlow Float to the Vita over vitacompanion and launch it.
# Run from WSL in this folder:
#
#   bash deploy.sh install      full VPK upload (first time after a VitaShell install, or when the engine changes)
#   bash deploy.sh              quick update: only index.lua + the floating-media DATA files, then relaunch
#   bash deploy.sh launch       just launch RetroFlow Float
#   bash deploy.sh kill         close it
#
# FIRST TIME: install RetroFlowFloat_v8.5.0.vpk once with VitaShell so the bubble is registered.
# After that every build only needs this script.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../vita.env" 2>/dev/null || true
. "$HERE/float.env"
VITA_IP="${VITA_IP:-192.168.68.60}"; FTP_PORT="${FTP_PORT:-1337}"; CMD_PORT="${CMD_PORT:-1338}"
VS="$HERE/../vita.sh"
export VITA_IP FTP_PORT CMD_PORT TITLEID

up() {  # up <local file> <path under ux0:/app/TITLEID>
    curl -sS --fail --ftp-method nocwd --ftp-create-dirs -T "$1" \
        "ftp://$VITA_IP:$FTP_PORT/ux0:/app/$TITLEID/$2" || { echo "upload failed: $2" >&2; exit 1; }
}

case "${1:-update}" in
    install) bash "$VS" run "$HERE/$VPK" ;;
    launch)  bash "$VS" launch ;;
    kill)    bash "$VS" kill ;;
    update)
        bash "$VS" kill >/dev/null 2>&1; sleep 1
        tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
        unzip -q "$HERE/$VPK" -d "$tmp" index.lua 'DATA/float_*' || { echo "could not unzip $VPK" >&2; exit 1; }
        up "$tmp/index.lua" index.lua
        n=1
        for f in "$tmp"/DATA/float_*; do up "$f" "DATA/$(basename "$f")"; n=$((n+1)); done
        # Float keeps a copy of DATA on the card (fast opens); push it too so the app never has to copy
        card="ux0:/data/RetroFlowFloat/DATA"
        curl -s -Q "MKD $card" "ftp://$VITA_IP:$FTP_PORT/" >/dev/null 2>&1 || true
        for f in "$tmp"/DATA/float_*; do
            curl -sS --fail --ftp-method nocwd -T "$f" "ftp://$VITA_IP:$FTP_PORT/$card/$(basename "$f")" || { echo "card copy failed: $f" >&2; exit 1; }
        done
        ver=$(grep -o 'FLOAT.DATA_VERSION = "[^"]*"' "$tmp/index.lua" | cut -d'"' -f2)
        printf '%s' "$ver" > "$tmp/version.txt" && curl -sS --fail --ftp-method nocwd -T "$tmp/version.txt" "ftp://$VITA_IP:$FTP_PORT/$card/version.txt"
        echo "card DATA refreshed (version $ver)"
        echo "pushed $n files"
        bash "$VS" launch ;;
    *) sed -n '2,12p' "$0"; exit 1 ;;
esac
