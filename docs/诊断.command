#!/bin/bash
# 爱问云邀请码导出 · macOS 诊断脚本
# 用法：在终端执行  bash ~/Downloads/诊断.command
# 或者右键 → 打开方式 → 终端

set -u
cd "$(dirname "$0")"
LOG="诊断日志.txt"

{
  echo "爱问云邀请码导出 诊断日志"
  echo "生成时间: $(date '+%Y-%m-%d %H:%M:%S')"
  echo "macOS: $(sw_vers -productVersion) ($(sw_vers -buildVersion))"
  echo "架构: $(uname -m)"
  echo

  echo "=== 1. 查找 App ==="
  APP=""
  for p in \
    "/Applications/爱问云邀请码导出.app" \
    "$HOME/Applications/爱问云邀请码导出.app" \
    "$HOME/Downloads/爱问云邀请码导出.app" \
    "$(dirname "$0")/爱问云邀请码导出.app"
  do
    if [ -d "$p" ]; then APP="$p"; break; fi
  done

  if [ -z "$APP" ]; then
    echo "未找到 App。请把 App 放到 /Applications，或与本脚本放在同一目录。"
  else
    echo "App: $APP"
    echo "版本: $(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null || echo 未知)"
    echo "架构: $(file "$APP/Contents/MacOS/"* 2>/dev/null | head -n 1)"
    echo "隔离属性: $(xattr -p com.apple.quarantine "$APP" 2>/dev/null || echo 无)"
    echo
    echo "=== 2. 内置证书文件 ==="
    find "$APP/Contents" \( -iname 'cacert.pem' -o -iname 'certifi*' \) 2>/dev/null | head -n 20
    echo
    echo "=== 3. App 自检 ==="
    BIN="$(find "$APP/Contents/MacOS" -type f -perm -111 2>/dev/null | head -n 1)"
    if [ -n "$BIN" ]; then
      echo "二进制: $BIN"
      "$BIN" --smoke-test 2>&1 || echo "smoke-test 失败，退出码 $?"
      "$BIN" --ssl-test 2>&1 || echo "ssl-test 失败，退出码 $?"
    else
      echo "未找到可执行文件"
    fi
  fi

  echo
  echo "=== 4. 爱问云客户端 ==="
  if [ -d "/Applications/AiCloud.app" ]; then
    echo "找到 /Applications/AiCloud.app"
    /usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
      "/Applications/AiCloud.app/Contents/Info.plist" 2>/dev/null || true
  else
    echo "未找到 /Applications/AiCloud.app"
  fi

  echo
  echo "=== 5. 登录态调试端口 ==="
  if /usr/bin/nc -z 127.0.0.1 9222 2>/dev/null; then
    echo "127.0.0.1:9222 已开启"
  else
    echo "127.0.0.1:9222 未开启（如果还没启动导出工具，这是正常的）"
  fi

  echo
  echo "=== 完成 ==="
  echo "请把本文件（诊断日志.txt）发给技术支持。"
} > "$LOG" 2>&1

open "$LOG"
