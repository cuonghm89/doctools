#!/bin/bash
# Đóng gói CPDFGear thành 1 file .app hoàn chỉnh (Info.plist + icon .icns +
# PythonEngine bundled) rồi zip lại vào dist/. Không bundle sẵn Python runtime
# hay .venv (252MB, symlink không portable giữa máy) — máy chạy .app vẫn cần
# tự cài `python3 -m pip install -r PythonEngine/requirements.txt`.
# ponytail: ad-hoc codesign only (không có Apple Developer ID) — người nhận
# app ở máy khác sẽ bị Gatekeeper chặn lần mở đầu tiên, cần chuột phải > Open.
set -euo pipefail
cd "$(dirname "$0")/.."

APP_NAME="CPDFGear"
DISPLAY_NAME="C-PDF Gear"
BUNDLE_ID="dev.cuonghoang.cpdfgear"
VERSION="${1:-$(git describe --tags --always 2>/dev/null || echo 0.0.0-local)}"

BUILD_DIR=".build/release"
DIST_DIR="dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"

echo "==> swift build -c release"
swift build -c release

echo "==> Dựng $APP_DIR (version $VERSION)"
rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RESOURCES/PythonEngine"

cp "$BUILD_DIR/$APP_NAME" "$MACOS/$APP_NAME"

echo "==> Tạo AppIcon.icns"
ICONSET=$(mktemp -d)/AppIcon.iconset
mkdir -p "$ICONSET"
SRC_ICON="Sources/CPDFGear/Resources/AppIcon.png"
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$SRC_ICON" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$SRC_ICON" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$RESOURCES/AppIcon.icns"
rm -rf "$(dirname "$ICONSET")"

echo "==> Copy PythonEngine (chỉ *.py + requirements.txt, bỏ test_*/.venv)"
for f in PythonEngine/*.py; do
    base="$(basename "$f")"
    [[ "$base" == test_* ]] && continue
    cp "$f" "$RESOURCES/PythonEngine/$base"
done
cp PythonEngine/requirements.txt "$RESOURCES/PythonEngine/requirements.txt"

echo "==> Info.plist"
cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleName</key><string>$DISPLAY_NAME</string>
    <key>CFBundleDisplayName</key><string>$DISPLAY_NAME</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>LSApplicationCategoryType</key><string>public.app-category.productivity</string>
</dict>
</plist>
PLIST

echo "==> codesign (ad-hoc)"
codesign --force --deep --sign - "$APP_DIR"

ZIP_PATH="$DIST_DIR/$APP_NAME-$VERSION-macos.zip"
echo "==> Nén $ZIP_PATH"
rm -f "$ZIP_PATH"
ditto -c -k --sequesterRsrc --keepParent "$APP_DIR" "$ZIP_PATH"

echo "==> Xong: $APP_DIR và $ZIP_PATH"
