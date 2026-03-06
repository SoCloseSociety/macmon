#!/bin/bash
# build_dmg.sh — Build macmon.app and package into a DMG
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="macmon"
APP_BUNDLE="$SCRIPT_DIR/dist/${APP_NAME}.app"
DMG_NAME="${APP_NAME}.dmg"
DMG_PATH="$SCRIPT_DIR/dist/${DMG_NAME}"
DMG_VOLUME="macmon Installer"
ICON_SIZE=128

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  __  __    _    ____ __  __  ___  _   _ "
echo " |  \/  |  / \  / ___|  \/  |/ _ \| \ | |"
echo " | |\/| | / _ \| |   | |\/| | | | |  \| |"
echo " | |  | |/ ___ \ |___| |  | | |_| | |\  |"
echo " |_|  |_/_/   \_\____|_|  |_|\___/|_| \_|"
echo ""
echo " Building macmon.app + DMG"
echo -e "${NC}"

# ── Cleanup ─────────────────────────────────────────────────────────────
echo -e "${CYAN}Cleaning previous builds...${NC}"
rm -rf "$SCRIPT_DIR/dist"
mkdir -p "$SCRIPT_DIR/dist"

# ── Create .app bundle structure ────────────────────────────────────────
echo -e "${CYAN}Creating app bundle...${NC}"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

# ── Info.plist ──────────────────────────────────────────────────────────
cat > "$APP_BUNDLE/Contents/Info.plist" << 'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>macmon</string>
    <key>CFBundleDisplayName</key>
    <string>macmon</string>
    <key>CFBundleIdentifier</key>
    <string>com.macmon.app</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>macmon-launcher</string>
    <key>CFBundleIconFile</key>
    <string>macmon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
PLIST_EOF

# ── Launcher script ────────────────────────────────────────────────────
# This opens Terminal.app and runs macmon dashboard
cat > "$APP_BUNDLE/Contents/MacOS/macmon-launcher" << 'LAUNCHER_EOF'
#!/bin/bash
# macmon.app launcher — opens Terminal with macmon dashboard

MACMON_DIR="$HOME/.macmon"
VENV_PYTHON="$MACMON_DIR/venv/bin/python"

# Find macmon.py: check ~/.macmon/macmon.py, then /usr/local/share/macmon/macmon.py
if [ -f "$MACMON_DIR/macmon.py" ]; then
    MACMON_SCRIPT="$MACMON_DIR/macmon.py"
elif [ -f "/usr/local/share/macmon/macmon.py" ]; then
    MACMON_SCRIPT="/usr/local/share/macmon/macmon.py"
else
    # Fallback: try the wrapper
    if command -v macmon &>/dev/null; then
        osascript -e 'tell application "Terminal" to do script "macmon; exit"'
        exit 0
    fi
    osascript -e 'display dialog "macmon is not installed yet.\n\nInstall it with:\n  git clone ... && cd macmon && bash install.sh" with title "macmon" buttons {"OK"} default button "OK" with icon caution'
    exit 1
fi

# Check venv exists
if [ ! -f "$VENV_PYTHON" ]; then
    osascript -e 'display dialog "macmon venv not found.\n\nRun install.sh to set up the environment." with title "macmon" buttons {"OK"} default button "OK" with icon caution'
    exit 1
fi

# Open Terminal with macmon dashboard
osascript << APPLE_EOF
tell application "Terminal"
    activate
    set targetTab to do script "clear && $VENV_PYTHON $MACMON_SCRIPT dashboard; exit"
    set custom title of targetTab to "macmon"
end tell
APPLE_EOF
LAUNCHER_EOF

chmod +x "$APP_BUNDLE/Contents/MacOS/macmon-launcher"

# ── Generate app icon ───────────────────────────────────────────────────
echo -e "${CYAN}Generating app icon...${NC}"

# Create a simple icon using sips + iconutil if possible
ICONSET_DIR="$SCRIPT_DIR/dist/macmon.iconset"
mkdir -p "$ICONSET_DIR"

# Generate icon with Python (creates a simple gradient icon with text)
python3 << 'PYICON_EOF'
import struct, zlib, os

def create_png(width, height, filename):
    """Create a simple PNG icon with gradient background and M letter."""
    pixels = []
    for y in range(height):
        row = []
        for x in range(width):
            # Blue-to-cyan gradient background
            r = int(20 + (x / width) * 30)
            g = int(80 + (y / height) * 80)
            b = int(180 + (x / width) * 75)

            # Draw "M" letter in center
            cx, cy = x / width, y / height
            in_m = False

            # M shape boundaries (rough)
            if 0.25 < cx < 0.75 and 0.2 < cy < 0.8:
                # Left vertical bar
                if 0.25 < cx < 0.33:
                    in_m = True
                # Right vertical bar
                elif 0.67 < cx < 0.75:
                    in_m = True
                # Left diagonal
                elif 0.33 <= cx <= 0.50 and abs(cy - 0.2 - (cx - 0.33) * 3.5) < 0.12:
                    in_m = True
                # Right diagonal
                elif 0.50 <= cx <= 0.67 and abs(cy - 0.2 - (0.67 - cx) * 3.5) < 0.12:
                    in_m = True

            if in_m:
                r, g, b = 255, 255, 255

            # Rounded corners
            corner_r = width * 0.18
            for (ccx, ccy) in [(corner_r, corner_r), (width - corner_r, corner_r),
                               (corner_r, height - corner_r), (width - corner_r, height - corner_r)]:
                dx, dy = x - ccx, y - ccy
                if ((x < corner_r or x > width - corner_r) and
                    (y < corner_r or y > height - corner_r)):
                    if dx*dx + dy*dy > corner_r*corner_r:
                        r, g, b = 0, 0, 0  # transparent (black for simplicity)

            row.extend([r, g, b])
        pixels.append(bytes(row))

    # Build PNG
    def chunk(chunk_type, data):
        c = chunk_type + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)

    header = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))

    raw = b''
    for row in pixels:
        raw += b'\x00' + row
    idat = chunk(b'IDAT', zlib.compress(raw, 9))
    iend = chunk(b'IEND', b'')

    with open(filename, 'wb') as f:
        f.write(header + ihdr + idat + iend)

iconset = os.path.expanduser("dist/macmon.iconset")
sizes = [16, 32, 64, 128, 256, 512]
for s in sizes:
    create_png(s, s, f"{iconset}/icon_{s}x{s}.png")
    create_png(s*2, s*2, f"{iconset}/icon_{s}x{s}@2x.png") if s <= 256 else None

print("Icon PNGs generated")
PYICON_EOF

# Convert iconset to icns
if command -v iconutil &>/dev/null; then
    iconutil -c icns "$ICONSET_DIR" -o "$APP_BUNDLE/Contents/Resources/macmon.icns" 2>/dev/null && \
        echo -e "${GREEN}Icon created${NC}" || \
        echo -e "${YELLOW}Icon creation skipped (non-critical)${NC}"
else
    echo -e "${YELLOW}iconutil not found, skipping icon${NC}"
fi

rm -rf "$ICONSET_DIR"

# ── Create DMG ──────────────────────────────────────────────────────────
echo -e "${CYAN}Creating DMG...${NC}"

# Create a temporary directory for DMG contents
DMG_STAGING="$SCRIPT_DIR/dist/dmg_staging"
mkdir -p "$DMG_STAGING"

# Copy app bundle
cp -R "$APP_BUNDLE" "$DMG_STAGING/"

# Create a symlink to Applications for drag-and-drop install
ln -s /Applications "$DMG_STAGING/Applications"

# Create a README in the DMG
cat > "$DMG_STAGING/README.txt" << 'README_EOF'
macmon - Mac Developer Monitor + System Cleaner
================================================

FIRST TIME SETUP:
1. Open Terminal
2. cd to the macmon source directory
3. Run: bash install.sh

THEN:
- Drag macmon.app to Applications (or just double-click it)
- Or use the CLI: macmon

The app opens a Terminal window with the macmon dashboard.
All 28 commands are available via CLI.

Commands: macmon --help
README_EOF

# Build DMG using hdiutil
hdiutil create \
    -volname "$DMG_VOLUME" \
    -srcfolder "$DMG_STAGING" \
    -ov \
    -format UDZO \
    -imagekey zlib-level=9 \
    "$DMG_PATH"

# Cleanup staging
rm -rf "$DMG_STAGING"

# ── Done ────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN} Build complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "  App:  ${CYAN}$APP_BUNDLE${NC}"
echo -e "  DMG:  ${CYAN}$DMG_PATH${NC}"
echo ""

DMG_SIZE=$(du -h "$DMG_PATH" | cut -f1)
echo -e "  Size: ${CYAN}${DMG_SIZE}${NC}"
echo ""
echo -e "${YELLOW}Note: Run install.sh first if not already done.${NC}"
echo -e "${YELLOW}The .app opens macmon dashboard in Terminal.${NC}"
echo ""
echo -e "${GREEN}To install: open dist/macmon.dmg and drag to Applications${NC}"
