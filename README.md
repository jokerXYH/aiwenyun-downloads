# 爱问云集成工具 2.3.5

Windows / macOS 下载与源码。用于读取本机已登录的爱问云客户端，导出实时课堂邀请码 Excel、板书 PDF 和历史课堂专注度反馈 CSV。

软件管理页：<https://jokerx.top/projects/software/aiwenyun/>

## 下载

在 [Releases](https://github.com/jokerXYH/aiwenyun-downloads/releases/latest) 下载：

| 平台 | 文件 |
| --- | --- |
| Windows 10/11 x64 | `aiwenyun-tools-windows-x64.exe` |
| macOS Apple Silicon | `aiwenyun-tools-macos-arm64.zip` |
| macOS Intel | `aiwenyun-tools-macos-x64.zip` |

每个 Release 同时提供 `.sha256` 校验文件。

## 2.3.5 更新

- 白盒审计修复：课程表缺失课次不再与显式课次撞号，`第N节课` 能正确识别，
  `groups` 为字符串时不再被拆成单字符。
- 作业/反馈/登录遇到服务端字段类型异常时不再崩溃：`obj` 非对象、
  应交人数非数字、`mediaType/groupIds/startTime` 非法值均按缺失处理。
- 板书列表分页增加 200 页上限，避免服务端分页异常时无限请求；
  `genBs` 缺 `url`、清单结构异常时按“该课无板书/失败”处理。
- 全局冷却不会让单课等待越过课堂截止时间；CSV/JSON/诊断文件写入失败
  不再让已生成的 Excel 整体报错，临时文件会清理。
- 更新检查与更新包下载只允许 HTTPS 重定向，不再先请求明文 HTTP。
- Windows/macOS 打包版继续使用同一套 `APP_VERSION`；单元测试 261 条通过。

## 功能

- **邀请码导出**：读取实时课堂，按课程 × 课次导出邀请码 Excel。
- **板书导出**：按时间段列出课堂，按「学期 + 班级」分组，勾选后导出 PDF。
- **反馈文件导出**：列出历史课堂和作业，按「学期 + 班级」归档，同时导出课堂权限 CSV 与作业成绩 CSV。
- 三个功能页共用登录态；Windows 优先只读读取本机客户端，不要求账号密码。
- 连上登录态后自动列出板书、历史课堂与作业，不用先点按钮。

## 登录态

### Windows

- 免重启、不开 9222 调试端口。
- 只读扫描爱问云进程内存中的登录态（快速扫描约 0.3 秒）。
- 界面启用 Per-Monitor DPI 感知并按系统 DPI 缩放。
- 如果本机客户端未登录，可改用账号密码登录；同一账号可能使电脑客户端下线。

### macOS

- 板书/反馈页使用账号密码登录；密码只在本进程内存中使用，不写日志、不落盘。
- 未签名/未公证的包首次打开会被 Gatekeeper 拦截。macOS 15+ 已不支持
  右键 → 打开：先双击一次，然后到「系统设置 → 隐私与安全性 → 安全性」
  点「仍要打开」；或在终端执行：
  `xattr -dr com.apple.quarantine "/Applications/爱问云集成工具.app"`。
- 如果提示 `CERTIFICATE_VERIFY_FAILED ... unable to get local issuer certificate`，
  这是 macOS 打包版找不到 CA 根证书导致的。当前源码已改为优先使用
  `certifi`，重新构建即可。

## 邀请码导出

读取「实时课堂」列表和课堂详情，按「课程 × 第几节课」生成邀请码 Excel：

- 默认 8 线程 + 12 req/s；支持超时、全局冷却、第二轮串行重试和诊断 JSON；
- 输出 `邀请码-YYYYMMDD-HHMMSS.xlsx`（矩阵 + 明细），可选 CSV / JSON；
- 默认保存到桌面「爱问云邀请码」，文件被 Excel 占用时自动换名。

命令行：

```bash
python run_cli.py --help
```

macOS 需要关闭并重新打开爱问云，仅在本机 `127.0.0.1:9222` 开启调试端口读取登录态；GUI 会弹窗确认，CLI 需要 `--allow-restart`，导出结束后自动恢复正常客户端窗口。

## 板书导出

板书列表按「学期 + 班级」分组，点整行即可勾选：

1. 选时间段（默认最近 30 天；也支持今天 / 最近 7 天 / 最近一个季度 /
   最近一年 / 最近三年 / 全部 / 自定义起止日期），切换后会自动列出该时段的
   课堂清单；
2. 清单按「学期 + 班级」分组：官方班级里的学期编码会按课堂名还原，例如
   `202602…` → `26秋…`、`202601…` → `26暑…`；β1 / β2 / β3 / α1 等分组
   后缀会合并为同一个班。按钮上直接显示 `26秋7MA+31`、`25暑7MAA12` 这样的
   名字，同名班不会被两个学期混在一起（`作业班` 仍独立）；匹配不到班级的
   条目统一归入「杂项/未分类」，列表按时间倒序，最新的在最上面；
3. 班级按钮按窗口宽度自适应，放不下的班收进右侧「更多」菜单，菜单再按
   学期分组列出全部班级；点「更多」里任意一个班会把它顶到可见区并高亮，
   勾选别的班不会丢掉已经勾好的课；
4. 点「导出所选板书」——**只导出勾选项**，并先弹出**保存位置选择**；
   未勾选任何课时会被拦截，不会回退为全量导出；
5. 排序可选「按上课顺序」或「按板书类型」；PDF 按「学期+班级」分文件夹
   保存（如 `26秋7MA+31/`），文件名与课堂名一致。

**进度与日志**：导出时右下角弹出无标题栏的进度卡片，显示总百分比、当前
第几节、当前课堂第几页、已用时间和预计剩余时间，旁边有「取消」；完成后
显示 100% 与勾选标记，1.8 秒后自动收起，失败或取消也会停留几秒说明原因
后自动收起。板书日志在班级列表下方，默认收起，右侧显示未读条数，导出
结束或出错时会自动展开，也可以随时点「展开 / 收起」查看。

### 导出加速

- **班级并发 3 路 + 单课页面并发 4 路**：一节在等 `genBs` 时，其他课已经在
  下载；页数多的课也不会一页一页排队。可用
  `AIWENYUN_BOARD_CLASS_WORKERS`、`AIWENYUN_BOARD_PAGE_WORKERS`
  环境变量调小（服务端限速时）或调大（带宽充足时）。
- **HTTPS 长连接复用**：下载线程复用自己的 keep-alive 连接，避免每页重新
  TLS 握手；空闲过久自动换新，避免半死连接。
- **缩短 `genBs` 轮询**：从「0/15/20/30 秒」改为
  「0/2.5/4/6/8/10/12 秒」渐进轮询，服务端提前合成完就提前返回。
- **PDF 更省 CPU/内存**：每页下载后立即压缩成图像流，不再把整节课的 RGB
  原图同时留在内存；同名页复用同一个 PDF 图像对象；修掉了逐页全文件扫描
  定位 stream 的 O(n²) 写法；回读校验改为整文件比对 + 页数检查，不再逐页
  zlib 解压。
- **失败隔离**：一节课出错只记这一节失败，不再中断整批；429 会按
  `Retry-After` 退避重试一次；取消时页面下载和轮询会尽快停下。

板书数据来自爱问云板书接口（`yxt/servlet/sign/getBsSign` → `upime/bs/genBs`
→ 服务端合成的逐页板书 PNG），全程只读。命令行用法见
`python banshu_export.py --help`。

## 反馈文件导出

按「班级 + 课次」组装反馈包，每个课次一个 `第X节课反馈` 文件夹：

- **本节课课前预习作业**：作业标题为「课前预习 / 专题X」的作业框，导出学生成绩 CSV；
- **上一节课课后巩固作业**：作业标题为「课后巩固 / 专题X-1」的作业框，导出学生成绩 CSV；
- **本节课历史课堂权限**：对应课次的历史课堂统计报告 CSV；
- 三项内容直接放在 `第X节课反馈/` 里，不再按文件类型多套一层文件夹；
- 反馈列表里的每个课次都可以展开，展开后三个文件各有一个可选按钮；
  直接勾选任意一个或多个文件即可导出，不需要先勾选课次再取消其他文件；
  取消某个文件的选择后，该文件在列表里显示「未选择」，导出时不会写入；
- 缺少某一项时只在列表里用 `⚠` 和「暂时缺失」标注，导出文件夹里不会
  生成 `.txt` 说明文件，也不会用假数据填充；例如只有第 11 节课课后巩固时，
  会生成 `第十二节课反馈/`，其中只包含实际选中的 CSV 文件。

匹配时统一成教学班短名（如 `2026027MA+31作业班` 和 `26秋7MA+31` 都归到
`7MA+31`），但显示、文件夹和 CSV 文件名保留学期，常态导出也会写成
`26秋7MA+31`。同名班的不同班期（如 `202601` / `202602`）会按学期或班级
编号分开，不会把第 12 节课的权限串到另一个学期。反馈页顶部有可点击的
班级按钮，切换班级不会丢掉已勾选项。只展示课堂班级代号能匹配到用户
班级列表的课堂；匹配不到班级的课堂不会出现在列表和班级按钮里。反馈
列表按时间降序排列。课前预习 / 课后巩固 CSV 的文件名前缀使用对应班级
（如 `26秋7MA+31-【数·课前预习】专题十二.csv`），内容里的原有字样不变。
保存位置可以选根目录，也可以直接选已有的班级文件夹；选已存在的班级
文件夹时不会再生成同名子文件夹。

课堂权限 CSV 包含教师端「历史课堂权限」页的全部学生字段：

- 学生姓名、用户名、班级、是否有效出勤；
- 学习时长、课堂类型、课堂名称、任课老师、课堂开始时间、开课时长；
- 直播在线时长、互动时长、签到次数、随堂测正确率、奖励积分、举手次数、开麦次数；
- 是否观看历史课堂、观看时长、观看进度、首次进入/最后离开直播时间。

数据接口（全部只读）：

- `liveclassgo/api/v1/history/listRecord`：历史课堂清单；
- `yxt/servlet/record/nc/getLcUserSignIn`：完整学生名单、班级和签到；
- `yxt/servlet/record/attend` / `getLiveClassWatch`：观看与直播互动记录；
- `statistics/liveclass/nct/groupStuDetail`：学生出勤、开麦、积分、举手、随堂测汇总；
- `homework/zuoyeDaka/teacherListWithDaka` / `yxt/servlet/zuoye/getAllZuoyesByZuoyeIdNew`：作业清单与成绩。

保存位置由导出时选择。文件按
`<班级>/<课堂名>统计报告.csv` 与 `<班级>/<时间>-<作业框名>.csv` 归档，
同一名称重复导出时才追加 `（2）`。

## 软件更新

设置页有「一键更新」入口，固定检查本软件现有下载页，运行时不读取或改写
其他地址。

- 检查地址固定为 `UPDATE_URL`（`config.py` 中唯一一处定义），指向
  <https://jokerx.top/projects/software/aiwenyun/>；
- 兼容下载页返回 JSON（读取 `version` / `url` / `sha256` / `notes`）或
  HTML 下载页；HTML 会提取 Windows exe 和同名 `.sha256` 链接；
- Windows 打包版发现新版本后，确认一次即自动下载、校验 SHA-256、探测新包
  版本、退出旧程序、替换 exe 并重启；更新成功后删除旧 exe 和临时更新包，
  不保留 `.bak` / 备份，失败日志保留在 `%TEMP%\aiwenyun-update.log`；
- 源码运行、macOS、或服务器没有提供 exe / 校验值时，只提示并打开下载页，
  不自动安装；
- 新版本重启后，标题栏和设置页显示的版本应与服务器下载页版本一致。

## 安全

- token / 密码只在本进程内存中使用，不写日志、不写导出文件。
- 错误信息中的 `access_token` 自动脱敏。
- 只允许 HTTPS 业务主机，使用系统/内置证书校验，不跟随重定向。
- 更新包只从 HTTPS 下载，必须通过服务器 `.sha256` 校验和 PE 头检查；
  替换脚本不保留旧版本备份。
- 不读取 cookie、聊天记录或通讯录；账号登录会读取本机客户端设备 ID。
- 只读操作：不提交、不批改、不改动任何反馈或课堂数据。

## 源码与构建

源码在本仓库根目录。Windows 使用 `build.bat`；macOS 使用
`build_macos.command`（需要 macOS、Python 3.10+ 且带 Tkinter）：

```bash
chmod +x build_macos.command   # 首次或 zip 解压后
./build_macos.command
```

脚本会生成 `dist/aiwenyun-tools-macos-<arch>.zip` 和对应 `.sha256`，
并从 `config.py` 读取 `APP_VERSION` 写入 `Info.plist`。未签名包首次打开
按 README 的 Gatekeeper 步骤处理；详细说明见 [`docs/RELEASE.md`](docs/RELEASE.md)。

GitHub Actions 会分别在 Windows、macOS arm64、macOS x64 上构建并发布 Release：

```text
.github/workflows/build-release.yml
```

发布到 GitHub Release 和 jokerx.top 的完整规则见
[`docs/RELEASE.md`](docs/RELEASE.md)。

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
| `gui.py` / `run_gui.py` | Tkinter 界面（邀请码 + 板书 + 反馈文件导出） |
| `cli.py` / `run_cli.py` | 邀请码导出命令行 |
| `pipeline.py` | 邀请码流水线、并发与 token 刷新 |
| `matrix.py` / `excel.py` | 邀请码矩阵与 Excel 写入 |
| `login.py` | 账号密码登录（custom/usr/doLogin，只读） |
| `banshu_export.py` | 板书 PDF 导出（单课 / 批量 / 按班级分组） |
| `homework_export.py` | 作业清单、学生成绩、作业 CSV |
| `feedback_export.py` | 历史课堂权限 + 作业成绩 CSV 导出 |
| `token_source.py` | 跨平台登录态门面 |
| `token_windows.py` | Windows 只读内存扫描 |
| `token_macos.py` | macOS 登录态辅助 |
| `token_model.py` | token 正则与跨平台模型 |
| `net.py` | HTTPS、连接复用、重试、脱敏 |
| `analyze.py` | 课程表响应解析 |
| `config.py` | 版本、默认参数、API 路径、系统工具 |
| `tests/` | 单元测试（不联网） |
