# 发布规则

本文是爱问云集成工具的唯一发布说明。版本号、GitHub Actions、jokerx.top
下载镜像和 macOS 脚本都按这里的顺序执行。

## 1. 版本号

- 唯一版本来源是 `config.py` 的 `APP_VERSION`。
- Git tag 必须是 `vX.Y.Z`，且满足 `v${APP_VERSION}`（例如
  `APP_VERSION = '2.3.2'` 对应 tag `v2.3.2`）。
- 默认规则：修复用 patch（`2.3.2`），新增用户功能用 minor（`2.4.0`），
  不兼容的大改才用 major。
- 同步更新 `README.md` 标题、`docs/macOS使用说明.txt` 和 CI 默认版本；
  `build_macos.command` 直接读取 `config.py`，不再写死版本号。
- `build-release.yml` 的 `preflight` 会校验 tag 和 `APP_VERSION`，不一致
  会直接失败，避免 Release 文件名、程序标题和下载页版本对不上。

## 2. 发布前检查

在仓库根目录执行：

```bash
python -m unittest discover -s tests -v
```

Windows 本地打包并检查版本：

```powershell
cmd /c "build.bat < nul"
dist\爱问云集成工具.exe --version-file "$env:TEMP\version.txt"
Get-Content "$env:TEMP\version.txt"
```

macOS 本地构建（在 Mac 上执行）：

```bash
chmod +x build_macos.command
./build_macos.command
```

确认 `git status` 只包含本次发布相关文件；不要把 `dist/`、`build/`、
`.venv*`、日志或凭据提交进仓库。

## 3. GitHub Actions 发布

1. 提交版本号和代码改动：
   ```bash
   git add ...
   git commit -m "release: 2.3.2（反馈导出保留 26秋 学期）"
   ```
2. 打 tag 并推送：
   ```bash
   git tag -a v2.3.2 -m "爱问云集成工具 2.3.2"
   git push origin main
   git push origin v2.3.2
   ```
3. `.github/workflows/build-release.yml` 使用同一个 tag 的 concurrency，
   重复触发会取消旧 run，避免两个构建同时覆盖 Release。随后依次：
   - `preflight`：校验 tag == `v${APP_VERSION}`；
   - Windows：单测、PyInstaller、`--smoke-test` / `--ssl-test`；
   - macOS arm64 / x64：单测、`build_macos.command`、包内 smoke/ssl；
   - Release：上传 `aiwenyun-tools-windows-x64.exe`、
     `aiwenyun-tools-macos-arm64.zip`、`aiwenyun-tools-macos-x64.zip`、
     对应 `.sha256` 和 `macOS-DIAGNOSE.command`。
4. 必须确认 Actions 运行成功，不能只看 push 成功。失败时先修代码并重新
   发一个 patch 版本；不要强推已发布的 tag。

验证 Release 资产：

```bash
gh release view v2.3.2
# 或打开 https://github.com/jokerXYH/aiwenyun-downloads/releases/tag/v2.3.2
```

## 4. 发布到 jokerx.top

服务器分发由站点仓库（私有）完成，不把凭据或 `deploy/` 放进本仓库。

1. 在站点仓库更新：
   - `projects/software/aiwenyun/index.html`：版本徽标、构建日期、文件大小、
     下载链接 query、更新说明、安全说明；
   - `projects/software/index.html`：爱问云卡片版本和链接；
   - `docs/CHANGELOG.md`：本次用户可感知变化；
   - `deploy/bt-panel/README.md`：部署历史（成功后追加）。
2. 推送站点仓库 `main`，让 `.github/workflows/deploy.yml` 部署页面；
   Runner 不可用时才按站点仓库说明使用 `node bt-deploy.cjs` 兜底。
3. 触发站点仓库的 `.github/workflows/mirror-aiwenyun.yml`，输入本次 tag。
   该工作流会从 GitHub Release 下载 `aiwenyun-tools-*`，校验 SHA-256，
   并以 `aiwenyun-invite-*` 旧文件名部署到
   `/downloads/aiwenyun/`，避免破坏已有下载链接。
4. 验证：
   ```bash
   curl -I https://jokerx.top/downloads/aiwenyun/aiwenyun-invite-windows-x64.exe
   curl -I https://jokerx.top/downloads/aiwenyun/aiwenyun-invite-macos-arm64.zip
   curl -I https://jokerx.top/downloads/aiwenyun/aiwenyun-invite-macos-x64.zip
   ```
   再核对页面版本、exe 的 `--version-file`、zip 解压后的
   `CFBundleShortVersionString`，以及服务器 `.sha256` 与 Release `.sha256`
   完全一致。
5. 更新 `deploy/bt-panel/README.md` 的部署历史，记录 tag、Actions run id、
   三个平台文件的 SHA-256、页面验证结果。

镜像文件名映射：

| Release 资产（GitHub） | jokerx.top 文件名 |
| --- | --- |
| `aiwenyun-tools-windows-x64.exe` | `aiwenyun-invite-windows-x64.exe` |
| `aiwenyun-tools-macos-arm64.zip` | `aiwenyun-invite-macos-arm64.zip` |
| `aiwenyun-tools-macos-x64.zip` | `aiwenyun-invite-macos-x64.zip` |

## 5. macOS 脚本

`build_macos.command` 是 Apple 上可用的构建脚本，目标平台 macOS 12+。

使用方式：

```bash
cd 爱问云仓库
chmod +x build_macos.command
./build_macos.command
```

要求：

- macOS；
- Python 3.10+（推荐 python.org 3.12，自带 Tkinter）；
- 如果使用 Homebrew Python，先执行 `brew install python-tk@3.12`；
- 脚本会创建 `.venv-macos` 并安装 `requirements.txt`。

产物：

- `dist/aiwenyun-tools-macos-<arch>.zip`
- `dist/aiwenyun-tools-macos-<arch>.sha256`

脚本会从 `config.py` 读取 `APP_VERSION` 写入 `Info.plist`，构建后自动运行
包内 `--smoke-test`；CI 还会运行 `--ssl-test` 并验证 zip 解压、签名和
证书。

首次打开未签名包：macOS 15+ 不再支持右键 → 打开。双击一次，然后到
「系统设置 → 隐私与安全性 → 安全性」点「仍要打开」；或执行：

```bash
xattr -dr com.apple.quarantine "/Applications/爱问云集成工具.app"
```

### 此前 macOS 问题的规避

- **脚本行尾**：`.gitattributes` 固定 `*.command` / `*.sh` 为 LF，避免
  `bad interpreter: /bin/bash^M`。
- **版本漂移**：旧脚本把 `2.2.1` 写死在 `plutil`；现在只读
  `config.py`，并由 CI preflight 校验 tag。
- **证书校验失败**：打包收集 `certifi`，包内 smoke/ssl 自检覆盖 CA。
- **zip 丢执行位/符号链接**：用 `ditto -c -k --sequesterRsrc --keepParent`
  打包，CI 用 `ditto -x -k` 解压验证。
- **macOS 白屏**：客户端启动走 `open -a` 并在导出后恢复窗口（2.1.2 起）。
- **Python 版本过旧**：脚本明确检查 3.10+，并拒绝复用旧 `.venv-macos`。
- **Gatekeeper 文案**：脚本和文档统一为 macOS 15+ 的「仍要打开」流程，
  不再提示已失效的右键打开。

## 6. 本次发布记录

### v2.3.2 · 2026-11-26

- tag：`v2.3.2`（发布提交 `b09ee74`）。
- 内容：反馈文件导出在常态（未显式筛选季度）下保留「26秋」等学期前缀，
  列表、班级目录和 CSV 文件名与显式筛选 26秋 一致。
- 验证：157 项单测通过；真实登录态按「最近30天」导出 15 个课次反馈、
  39 个 CSV、0 失败，所有路径包含 `26秋`；Windows 包 `--version-file`
  返回 `2.3.2`。
- 构建/发布：`build-release.yml` run `45742407674` **success**；
  preflight、Windows、macOS arm64、macOS x64 全绿，Publish release 首次
  因 Release API 未关联 tag 失败，重跑失败 job 后成功。
- Release 资产：
  - Windows `21,685,262B` / SHA-256
    `4b3015e84801d5b7ba95cb4f9455d6ef1ecf81e2f227fd28fa46ffc96bb3ae93`
  - macOS arm64 `17,973,815B` / SHA-256
    `01917b319660b3f23b035c79497fa5aa838994247350903d1b1e27467957aeee`
  - macOS x64 `19,388,528B` / SHA-256
    `843b600cf33f8459bac5cae0127f471a72d610568acd375e957b380c11b26b9d`
  - `macOS-DIAGNOSE.command` `2,423B`。
- 服务器：`mirror-aiwenyun.yml` run `45742807389` success；三个安装包
  与 `.sha256` 镜像到 `/downloads/aiwenyun/`，大小和 SHA-256 与 Release
  一致。
- 站点：`deploy.yml` run `45742997805`、`45743184277` success；页面与
  软件管理卡片 v2.3.2，`macOS-README.html` 同步更新。
- 线上验收：下载页版本徽标 `v2.3.2`，Windows 包 `--version-file` 返回
  `2.3.2`；三个 `.sha256` 与 Release 资产一致。
- 过程记录：首次 Publish 因 GitHub Release API 未把新 tag 关联到草稿
  Release 而失败（生成了 `untagged-*` 草稿链接）；保留原 run，重跑失败
  job 后按幂等逻辑找到 `v2.3.2` 草稿并完成上传发布，未改动 CI 配置。

### v2.3.1 · 2026-11-26

- tag：`v2.3.1`（发布提交 `c0d69a1`）。
- 构建/发布：`build-release.yml` run `45652516098` **success**；
  preflight、Windows、macOS arm64、macOS x64、Publish release 全绿。
- Release 资产：
  - Windows `21,677,663B` / SHA-256
    `60f85b296b8b16ebb1dafd24cd73c10a1ba5b6ee3a93a763def4e2e5a1fad9c3`
  - macOS arm64 `17,966,062B` / SHA-256
    `ffbe25f91968e1bce62722ba378c2f8fd640ab6e2fc212dd3d1f2ff116400553`
  - macOS x64 `19,380,404B` / SHA-256
    `2896555dd20ae48d1002bbe6ca52c9872b2f2458ee5c273cc4202503f20aa533`
  - `macOS-DIAGNOSE.command` `2,423B`。
- 服务器：`mirror-aiwenyun.yml` run `45655349738` success；镜像按
  GitHub Release API digest 校验，三个文件 SHA-256 与 Release 一致。
- 站点：`deploy.yml` run `45652966740`、`45654404955`、`45655305714`
  success；页面 v2.3.1，`macOS-README.html` 站内 200。
- 线上验收：下载 Windows exe 后 `--version-file` 返回 `2.3.1`；三个
  `.sha256` 与 Release 资产一致。
- 过程记录：2.3.0 的 tag 因 macOS runner 的 UTC 时区测试失败，已改为
  UTC+8 断言；GitHub Release API 曾连续 500，发布 job 改为 `gh` 幂等
  重试后成功。2.3.0 与误带未提交反馈改动的 2.3.1 预发布 tag 已删除，
  本次发布 tag 为 `v2.3.1`。
