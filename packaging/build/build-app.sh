#!/bin/bash
# 组装 Token monitor.app（薄壳启动器）→ 签名 → 安装到 /Applications
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="Token monitor.app"
DEST="/Applications/$APP"
WORK="$ROOT/dist"

echo "==> 清理"
rm -rf "$WORK"
mkdir -p "$WORK/$APP/Contents/MacOS" "$WORK/$APP/Contents/Resources"

echo "==> 写入 Info.plist / 启动器 / 图标"
cp "$ROOT/payload/Info.plist" "$WORK/$APP/Contents/Info.plist"
cp "$ROOT/payload/tokenmonitor.sh" "$WORK/$APP/Contents/MacOS/tokenmonitor"
chmod 755 "$WORK/$APP/Contents/MacOS/tokenmonitor"
cp "$ROOT/build/icon.icns" "$WORK/$APP/Contents/Resources/icon.icns"

echo "==> ad-hoc 签名"
codesign --force --deep --sign - "$WORK/$APP"
codesign --verify --deep --strict "$WORK/$APP" && echo "签名校验通过"

echo "==> 安装到 /Applications"
rm -rf "$DEST"
cp -R "$WORK/$APP" "$DEST"
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

echo "==> 完成: $DEST"
du -sh "$DEST"
