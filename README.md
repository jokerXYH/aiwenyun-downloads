# 爱问云邀请码导出 2.0

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

## Windows

- 免重启、不开 9222 调试端口。
- 只读扫描爱问云进程内存中的登录态。
- 8 线程 + 12 req/s 并发，33 个课堂实测约 4–5 秒。
- 输出 `邀请码-时间.xlsx`，含「邀请码」矩阵表和「明细」表。

## macOS

- 受 SIP / hardened runtime 限制，macOS 版需要关闭并重新打开爱问云，
  仅在本机 `127.0.0.1:9222` 开启调试端口，通过 CDP 读取登录态。
- GUI 会弹窗确认重启；CLI 需要 `--allow-restart`。
- 未签名 / 未公证的包首次打开需右键 → 打开，或执行：
  `xattr -dr com.apple.quarantine "爱问云邀请码导出2.0.app"`。
- 只在 macOS 12+ 上构建；未在真实 Mac 上人工测试，首次使用请保留日志。

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
