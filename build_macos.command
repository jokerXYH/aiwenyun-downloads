#!/bin/bash
# 在 macOS 上构建「爱问云集成工具.app」。
# 用法：双击本文件，或在终端执行 ./build_macos.command
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="爱问云集成工具"
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
if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "需要 Python 3.10+（当前：$("$PYTHON" --version 2>&1)）。"
  echo "项目里的 Path.write_text(newline=...) 在 3.9 上会失败。"
  exit 1
fi

if [ -d ".venv-macos" ] && ! .venv-macos/bin/python -c \
    'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "现有 .venv-macos 的 Python 版本过旧。请先删除后重试："
  echo "  rm -rf .venv-macos"
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
  --add-data "assets:assets" \
  --paths . \
  --exclude-module numpy \
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
  --hidden-import login \
  --hidden-import banshu_export \
  --hidden-import feedback_export \
  --hidden-import homework_export \
  --hidden-import update_checker \
  --hidden-import update_installer \
  --hidden-import theme_art \
  --hidden-import PIL \
  --hidden-import PIL.ImageDraw \
  --hidden-import certifi \
  --collect-data certifi \
  --osx-bundle-identifier top.jokerx.aiwenyun \
  run_gui.py

APP="dist/${APP_NAME}.app"
if [ ! -d "$APP" ]; then
  echo "构建失败：没有找到 $APP"
  exit 1
fi

# 版本号只从 config.py 读取，避免 README / Info.plist / Release tag 漂移。
APP_VERSION="$(python -c 'from config import APP_VERSION; print(APP_VERSION)')"
if [ -z "$APP_VERSION" ]; then
  echo "构建失败：无法从 config.py 读取 APP_VERSION"
  exit 1
fi
echo "版本：$APP_VERSION"
/usr/bin/plutil -replace CFBundleShortVersionString -string "$APP_VERSION" "$APP/Contents/Info.plist" || true
/usr/bin/plutil -replace CFBundleVersion -string "$APP_VERSION" "$APP/Contents/Info.plist" || true

BIN="$(find "$APP/Contents/MacOS" -type f -perm -111 -print -quit)"
if [ -z "$BIN" ]; then
  echo "构建失败：App 内没有可执行文件"
  exit 1
fi
"$BIN" --smoke-test

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

ZIP="dist/aiwenyun-tools-macos-${ARCH_LABEL}.zip"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
/usr/bin/shasum -a 256 "$ZIP" | tee "dist/aiwenyun-tools-macos-${ARCH_LABEL}.sha256"

echo
echo "完成：$ZIP"
echo "未签名/未公证的包首次打开会被 Gatekeeper 拦截。macOS 15+："
echo "  双击一次 → 系统设置 → 隐私与安全性 → 仍要打开"
echo "或执行："
echo "  xattr -dr com.apple.quarantine \"$APP\""
echo "（App 移到 /Applications 后，把路径换成 /Applications/${APP_NAME}.app）"
