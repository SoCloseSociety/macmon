#!/usr/bin/env bash
# AegisForge.app -- reproducible macOS build (menu-bar .app via PyInstaller).
# Mirrors the fleet's build_sentinel.sh pattern: --windowed + --icon, an
# LSUIElement agent (no Dock icon), ad-hoc codesign. NOT the CLI: the public
# `macmon` CLI stays a plain console tool with no rumps/pyobjc dependency.
#
# Usage:
#   ./build_aegisforge.sh            # build dist/AegisForge.app
#   ./build_aegisforge.sh --dmg      # also wrap it in a .dmg
#
# Heavy build: on a memory-pressured Mac run `sudo purge` (or free RAM) first.
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="AegisForge"
BUNDLE_ID="co.soclose.aegisforge"
VENV="${VENV:-.venv}"
PY="$VENV/bin/python"
PYI="$VENV/bin/pyinstaller"
ICNS="assets/aegisforge.icns"
say() { printf '\033[36m==>\033[0m %s\n' "$*"; }

[ -x "$PY" ] || { echo "no venv at $VENV -- create one and pip install -e '.[app]'"; exit 1; }

# 1. Build deps (kept OUT of the CLI's runtime deps -- they live here only).
say "Ensuring build deps (rumps, pyobjc, pyinstaller) in $VENV"
"$PY" -m pip install -q --upgrade "rumps>=0.4" "pyobjc-framework-Cocoa>=10" pyinstaller >/dev/null

# 2. Render the monochrome menu-bar template glyph from the brand mono mark.
MONO_SVG="${AEGIS_MONO_SVG:-../NeoBot-aegisforge-wt/compute-node/aegisforge-brand/icon/aegisforge-mark-mono.svg}"
OUT_PNG="assets/aegisforge-menubar.png"
if [ ! -f "$OUT_PNG" ] && [ -f "$MONO_SVG" ]; then
  say "Rendering menu-bar template glyph -> $OUT_PNG"
  if command -v rsvg-convert >/dev/null 2>&1; then
    rsvg-convert -w 44 -h 44 "$MONO_SVG" -o "$OUT_PNG" || true
  elif "$PY" -c "import cairosvg" 2>/dev/null; then
    "$PY" -c "import cairosvg,sys; cairosvg.svg2png(url='$MONO_SVG', write_to='$OUT_PNG', output_width=44, output_height=44)" || true
  else
    echo "  (no SVG renderer -- the app falls back to a text title; install librsvg or cairosvg to get the glyph)"
  fi
fi

# 3. Freeze. --windowed = .app bundle; collect the package + native-heavy deps
# so the frozen app does not die with ModuleNotFoundError (the build_sentinel.sh
# lesson). rumps + pyobjc need explicit collection.
say "PyInstaller freeze -> dist/$APP_NAME.app"
rm -rf "build/$APP_NAME" "dist/$APP_NAME.app"
"$PYI" --noconfirm --windowed --name "$APP_NAME" \
  --icon "$ICNS" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --collect-submodules macmon_core \
  --collect-all rumps \
  --hidden-import psutil --hidden-import rich \
  --add-data "assets:assets" \
  aegisforge_app.py

APP="dist/$APP_NAME.app"
[ -d "$APP" ] || { echo "build failed: $APP not produced"; exit 1; }

# 4. LSUIElement=1 -> menu-bar agent, no Dock icon / no window on launch.
PLIST="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleName AegisForge" "$PLIST" 2>/dev/null || true

# 5. Ad-hoc codesign (no Apple Dev ID here; enough to launch locally).
say "Ad-hoc codesign"
codesign --force --deep --sign - --identifier "$BUNDLE_ID" "$APP" 2>/dev/null || true

say "Built: $APP"
say "Launch: open '$APP'   (look for the ember-shield glyph in the menu bar)"

if [ "${1:-}" = "--dmg" ]; then
  say "Packaging .dmg"
  rm -f "dist/$APP_NAME.dmg"
  hdiutil create -volname "$APP_NAME" -srcfolder "$APP" -ov -format UDZO "dist/$APP_NAME.dmg" >/dev/null
  say "Built: dist/$APP_NAME.dmg"
fi
