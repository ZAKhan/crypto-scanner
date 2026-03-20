#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
#  CRYPTO SCALPER SCANNER v2 — Build Script
#  Builds a standalone Arch Linux binary via PyInstaller
#  then installs it to ~/.local/bin and registers the
#  desktop launcher.
#
#  Usage:  chmod +x build.sh && ./build.sh
#  Needs:  python3, pip, PyQt6, requests
# ═══════════════════════════════════════════════════════════

SCRIPT="crypto_scanner.py"
BINARY="crypto_scanner"
DIST="./dist/linux"
DESKTOP_SRC="./crypto_scanner.desktop"

GREEN="\033[92m"
YELLOW="\033[93m"
RED="\033[91m"
CYAN="\033[96m"
BOLD="\033[1m"
RESET="\033[0m"

info()  { echo -e "${CYAN}${BOLD}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}${BOLD}[ OK ]${RESET}  $*"; }
warn()  { echo -e "${YELLOW}${BOLD}[WARN]${RESET}  $*"; }
err()   { echo -e "${RED}${BOLD}[ERR ]${RESET}  $*"; exit 1; }
step()  { echo -e "\n${BOLD}${CYAN}━━━ $* ━━━${RESET}"; }

# ── Pre-flight ───────────────────────────────────────────────
step "Pre-flight checks"

[[ -f "$SCRIPT" ]]      || err "$SCRIPT not found — run from the project directory."
[[ -f "app_icon.png" ]] || warn "app_icon.png not found — icon will not be embedded."

command -v python3 &>/dev/null || err "python3 not found."
ok "Python: $(python3 --version)"

if ! python3 -c "import PyQt6" &>/dev/null; then
    warn "PyQt6 not found — installing..."
    pip install PyQt6 --break-system-packages -q \
        || err "PyQt6 install failed. Try: sudo pacman -S python-pyqt6"
fi
ok "PyQt6: $(python3 -c 'import PyQt6.QtCore; print(PyQt6.QtCore.PYQT_VERSION_STR)' 2>/dev/null)"

if ! python3 -c "import requests" &>/dev/null; then
    warn "requests not found — installing..."
    pip install requests --break-system-packages -q \
        || err "requests install failed."
fi
ok "requests: $(python3 -c 'import requests; print(requests.__version__)' 2>/dev/null)"

# ── Install PyInstaller ──────────────────────────────────────
step "Installing PyInstaller"
pip install pyinstaller --break-system-packages -q
ok "PyInstaller: $(pyinstaller --version)"

# ── Build ────────────────────────────────────────────────────
step "Building binary  →  $DIST/$BINARY"

# Include app_icon.png alongside the binary
ICON_ARG=""
[[ -f "app_icon.png" ]] && ICON_ARG="--add-data app_icon.png:."

pyinstaller \
    --onefile \
    --name        "$BINARY" \
    --distpath    "$DIST" \
    --clean \
    --strip \
    $ICON_ARG \
    --hidden-import sys \
    --hidden-import os \
    --hidden-import struct \
    --hidden-import math \
    --hidden-import tempfile \
    --hidden-import json \
    --hidden-import time \
    --hidden-import subprocess \
    --hidden-import statistics \
    --hidden-import threading \
    --hidden-import datetime \
    --hidden-import requests \
    --hidden-import PyQt6 \
    --hidden-import PyQt6.QtWidgets \
    --hidden-import PyQt6.QtCore \
    --hidden-import PyQt6.QtGui \
    "$SCRIPT" \
    2>&1 | grep -E "^(INFO|WARN|ERROR|.*(completed|error|warning))" || true

[[ -f "$DIST/$BINARY" ]] || err "Build failed — binary not produced."

chmod +x "$DIST/$BINARY"
SIZE=$(du -sh "$DIST/$BINARY" | cut -f1)
ok "Binary ready: $DIST/$BINARY  ($SIZE)"

# ── Copy to binary/ folder (for releases) ───────────────────
mkdir -p ./binary
cp "$DIST/$BINARY" ./binary/$BINARY
ok "Binary → ./binary/$BINARY"

# ── Install to system ────────────────────────────────────────
step "Installing"

mkdir -p "$HOME/.local/bin"
cp "$DIST/$BINARY" "$HOME/.local/bin/$BINARY"
chmod +x "$HOME/.local/bin/$BINARY"
ok "Binary → ~/.local/bin/$BINARY"

if [[ -f "$DESKTOP_SRC" ]]; then
    mkdir -p "$HOME/.local/share/applications"
    sed "s|%h|$HOME|g" "$DESKTOP_SRC" \
        > "$HOME/.local/share/applications/crypto_scanner.desktop"
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    ok "Desktop entry → ~/.local/share/applications/crypto_scanner.desktop"
else
    warn "No .desktop file found — skipping app menu registration"
fi

# ── Cleanup ──────────────────────────────────────────────────
rm -rf build *.spec __pycache__ 2>/dev/null || true
ok "Cleaned up build artefacts"

# ── Summary ──────────────────────────────────────────────────
step "Done"
echo -e ""
echo -e "  Binary  : ${GREEN}$HOME/.local/bin/$BINARY${RESET}"
echo -e "  Launch  : ${CYAN}$BINARY${RESET}   or from your app menu"
echo -e ""
