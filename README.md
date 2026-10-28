# 爱问云邀请码导出 2.1.2

Windows / macOS 下载与源码。用于读取本机已登录的爱问云客户端，导出「实时课堂」的邀请码 Excel。

软件管理页：<https://jokerx.top/projects/software/aiwenyun/>

## 下载

在 [Releases](https://github.com/jokerXYH/aiwenyun-downloads/releases/latest) 下载：

| 平台 | 文件 |
| --- | --- |
| Windows 10/11 x64 | `aiwenyun-invite-windows-x64.exe` |
| macOS Apple Silicon | `aiwenyun-invite-macos-arm64.zip` |
| macOS Intel | `aiwenyun-invite-macos-x64.zip` |

每个 Release 同时提供 `.sha256` 校验文件。

## 2.1.2 修复

- macOS 客户端改用 `open -a` 启动，避免直接执行 Electron 二进制导致白屏。
- 导出结束后自动恢复正常客户端窗口，不再停留在调试实例。
- 增加 macOS 启动/恢复命令单元测试。

## 2.1.1 修复

- macOS 版内置 `certifi` CA 根证书，修复 `CERTIFICATE_VERIFY_FAILED / unable to get local issuer certificate`。
- 打包脚本自动收集 `certifi` 证书文件，macOS / Windows 构建行为一致。
- 更新 macOS 15+ Gatekeeper 首次打开说明。

## Windows

- 免重启、不开 9222 调试端口。
- 只读扫描爱问云进程内存中的登录态（快速扫描约 0.3 秒）。
- 8 线程 + 12 req/s 并发，33 个课堂实测约 3.8–4.0 秒（16 线程约 2.3 秒）。
- 单请求 6 秒硬超时 + 空连接回收 + 卡住看门狗，弱网不会无限卡住。
- 输出 `邀请码-时间.xlsx`（矩阵 + 明细）和 `…-诊断.json`。

## macOS

- 受 SIP / hardened runtime 限制，macOS 版需要关闭并重新打开爱问云，
  仅在本机 `127.0.0.1:9222` 开启调试端口，通过 CDP 读取登录态。
- 读取登录态后会自动恢复正常客户端窗口；导出期间客户端短暂重启属于正常现象。
- GUI 会弹窗确认重启；CLI 需要 `--allow-restart`。
- 未签名 / 未公证的包首次打开会被 Gatekeeper 拦截。macOS 15+ 已不支持
  右键 → 打开：先双击一次，然后到「系统设置 → 隐私与安全性 → 安全性」
  点「仍要打开」；或在终端执行：
  `xattr -dr com.apple.quarantine "/Applications/爱问云邀请码导出.app"`。
- 如果提示 `CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate`，
  这是 macOS 打包版找不到 CA 根证书导致的。当前源码已改为优先使用
  `certifi`，重新构建即可。旧包可临时从终端启动：

  ```bash
  curl -L https://curl.se/ca/cacert.pem -o ~/Downloads/cacert.pem
  SSL_CERT_FILE=~/Downloads/cacert.pem \
    "/Applications/爱问云邀请码导出.app/Contents/MacOS/爱问云邀请码导出"
  ```
- 只在 macOS 12+ 上构建；未在真实 Mac 上人工测试，首次使用请保留日志。

## 稳定性设计（2.1.0）

- **快速扫描**：先扫主进程，通常 0.1–0.3 秒拿到 token；候选失效时自动完整重扫。
- **单请求看门狗**：邀请码请求 socket 超时 6 秒，另有线程看门狗强制断开，
  半死连接不会把工作线程挂住。
- **空闲连接回收**：连接空闲超过 5 秒直接换新，避免复用被服务端 / WAF
  悄悄关掉的连接——这是「爬到一半卡住」最常见的原因。
- **单课堂总截止**：每个课堂最多 22 秒、3 次尝试，到点记失败，绝不无限重试。
- **全局卡住看门狗**：整轮 28 秒没有任何课堂完成就收尾，进入第二阶段。
- **第二轮重试**：第一轮失败的课堂串行重试（预算 30 秒），网络抖动通常能补回来。
- **全局冷却**：超时 / 429 时所有线程一起停 0.7–1.3 秒，避免并发重试把链路打爆。
- **诊断文件**：每次导出在 Excel 旁边生成 `邀请码-…-诊断.json`，包含各阶段耗时、
  p50/p95、尝试次数和失败原因；别的机器卡住时把它发回来就能定位。

实测（33 个课堂，本机千兆网络）：

| 场景 | 结果 |
| --- | --- |
| 默认 8 线程 / 12 req/s | 33/33，约 3.8–4.0 秒 |
| 16 线程 / 30 req/s | 33/33，约 2.3–3.4 秒 |
| 24 线程 / 60 req/s | 33/33，约 2.2–2.4 秒 |
| 人为让 1 个请求卡死 9 秒 | 看门狗 6 秒收尾，第二轮补回，33/33 |
| 45% 请求随机失败 + token 过期 | 31/33，未卡死 |

手动压测（会请求真实接口）：

```bash
python tests/stress_live.py --rounds 2   # 服务器并发上限
python tests/simulate_stall.py           # 卡死/看门狗/第二轮
python tests/fault_injection.py          # 随机网络失败
```

## 安全

- token 只在本进程内存中使用，不写日志、不写 Excel/JSON。
- 错误信息中的 `access_token` 自动脱敏。
- 只允许 HTTPS 业务主机，使用系统证书校验，不跟随重定向。
- 不读取 cookie、localStorage、聊天记录或通讯录。

## 源码与构建

源码在本仓库根目录。Windows 使用 `build.bat`；macOS 使用 `build_macos.command`。
GitHub Actions 会分别在 Windows、macOS arm64、macOS x64 上构建并发布 Release：

```text
.github/workflows/build-release.yml
```

本地源码运行：

```bash
python run_gui.py
```

测试：

```bash
python -m unittest discover -s tests -v
```

## 目录

| 文件 | 作用 |
| --- | --- |
| `gui.py` / `run_gui.py` | Tkinter 界面 |
| `pipeline.py` | 导出流水线、并发、token 刷新 |
| `token_windows.py` | Windows 只读内存扫描 |
| `token_macos.py` | macOS 重启 + CDP |
| `token_model.py` | token 正则与跨平台模型 |
| `net.py` | HTTPS、限速、重试、脱敏 |
| `analyze.py` / `matrix.py` / `excel.py` | 解析、排版、Excel |
