#!/bin/bash
# 在 macOS 上构建「爱问云邀请码导出.app」。
# 用法：双击本文件，或在终端执行 ./build_macos.command
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="爱问云邀请码导出"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "没有找到 python3。请先安装 Python 3.10+（python.org 版本自带 Tkinter）。"
  exit 1
fi
if ! "$PYTHON" -c "import tkinter" >/dev/null 2>&1; then
  echo "当前 Python 缺少 Tkinter。请安装 python.org 的 Python 3.12，或执行："
  echo "  brew install python-tk@3.12"
  exit 1
fi

if [ ! -d ".venv-macos" ]; then
  "$PYTHON" -m venv .venv-macos
fi
# shellcheck disable=SC1091
source .venv-macos/bin/activate
python -m pip install --upgrade pip >/dev/null
python -m pip install -r requirements.txt

rm -rf build dist
python -m PyInstaller --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  --icon app.icns \
  --add-data "app.icns:." \
  --paths . \
  --exclude-module numpy \
  --exclude-module PIL \
  --exclude-module lxml \
  --exclude-module pandas \
  --exclude-module scipy \
  --exclude-module matplotlib \
  --hidden-import gui \
  --hidden-import pipeline \
  --hidden-import token_source \
  --hidden-import token_model \
  --hidden-import token_macos \
  --hidden-import net \
  --hidden-import analyze \
  --hidden-import matrix \
  --hidden-import excel \
  --hidden-import config \
  --hidden-import certifi \
  --collect-data certifi \
  --osx-bundle-identifier top.jokerx.aiwenyun \
  run_gui.py

APP="dist/${APP_NAME}.app"
if [ ! -d "$APP" ]; then
  echo "构建失败：没有找到 $APP"
  exit 1
fi

# 版本号写进 Info.plist
/usr/bin/plutil -replace CFBundleShortVersionString -string "2.1.2" "$APP/Contents/Info.plist" || true
/usr/bin/plutil -replace CFBundleVersion -string "2.1.2" "$APP/Contents/Info.plist" || true

# 没有开发者证书时做 ad-hoc 签名；有证书时用 CODESIGN_IDENTITY。
if [ -n "${CODESIGN_IDENTITY:-}" ]; then
  /usr/bin/codesign --deep --force --options runtime \
    --sign "$CODESIGN_IDENTITY" "$APP"
else
  /usr/bin/codesign --deep --force --sign - "$APP" || true
fi

ARCH="$(uname -m)"
case "$ARCH" in
  arm64) ARCH_LABEL="arm64" ;;
  x86_64) ARCH_LABEL="x64" ;;
  *) ARCH_LABEL="$ARCH" ;;
esac

ZIP="dist/aiwenyun-invite-macos-${ARCH_LABEL}.zip"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
/usr/bin/shasum -a 256 "$ZIP" | tee "dist/aiwenyun-invite-macos-${ARCH_LABEL}.sha256"

echo
echo "完成：$ZIP"
echo "未签名/未公证的包首次打开需右键 → 打开；或执行："
echo "  xattr -dr com.apple.quarantine \"$APP\""
