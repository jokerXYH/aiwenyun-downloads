# 爱问云集成工具 2.3.6 · 白盒测试与修复报告

- 测试对象：`D:\小程序\爱问云` 根目录全部 `.py` 模块（`gui.py`、`pipeline.py`、`banshu_export.py`、`feedback_export.py` 等 19 个业务模块）
- 测试角色：Python 测试工程师 / 白盒测试
- 测试日期：2026-11-26
- 测试方式：静态 AST 分支梳理 + 离线单元/集成用例 + Tk 界面冒烟；不访问真实爱问云接口
- 业务预期来源：模块 docstring、函数命名、README、现有 `tests/` 断言；反推依据写在各用例的目标/预期列

> 说明：本报告按用户要求固定为「测试用例表 → 发现的 bug 清单 → 修改方案 → 修复后的代码」四段；待确认项单独放在最后。所有修复均未新增第三方依赖，未改变既有业务字段和接口路径。

---

## 1. 测试用例表

### 1.1 环境、依赖与执行命令

| 项 | 值 |
| --- | --- |
| Python | 3.12.10（Windows x64） |
| 运行时依赖 | `openpyxl 3.1.5`、`certifi 2026.01.04`、`Pillow 12.2.0` |
| 测试依赖 | 仅标准库 `unittest`、`unittest.mock`、`tempfile`、`threading`；无新增依赖 |
| 打包依赖 | `pyinstaller>=6.0`（仅构建 exe 使用，测试不需要） |
| 测试命令 | `python -m unittest discover -s tests -v` |
| 打包命令 | `cmd /c "build.bat < nul"` |

本次构建结果：`dist\爱问云集成工具.exe`，21,899,263 字节，构建时间 2026-11-26 11:50:09（PyInstaller 6.20.0 / Python 3.12.10）。

从零安装（可照抄）：

```bat
python -m pip install -r requirements.txt
python -m pip install "pyinstaller>=6.0"
```

`requirements.txt` 原文：

```text
openpyxl>=3.1
certifi>=2024.2.2
Pillow>=10.0

pyinstaller>=6.0
```

无新增/升级依赖；`pyinstaller` 只在构建 exe 时需要。

### 1.2 执行结果与覆盖统计

本次最终执行：

```text
Ran 262 tests in 11.311s
OK
```

其中既有测试 158 条，本次新增白盒审计用例 104 条（`tests/test_whitebox_audit.py`）。新增用例逐条覆盖合法输入、边界值、异常路径、错误参数，并专门覆盖文件不存在、同名文件、目录不可写、权限不足等文件路径。

用标准库 `trace` 做的近似覆盖统计（按可执行行/静态分支点）：

| 模块 | 行覆盖 | 分支覆盖（静态近似） | 已执行函数/总函数 |
| --- | ---: | ---: | ---: |
| analyze.py | 81.9% | 96.9% | 13/13 |
| banshu_export.py | 63.3% | 62.0% | 76/81 |
| cli.py | 66.7% | 37.5% | 3/3 |
| config.py | 62.5% | 76.9% | 13/13 |
| excel.py | 73.5% | 93.1% | 5/5 |
| feedback_export.py | 73.9% | 69.5% | 67/67 |
| gui.py | 55.8% | 40.7% | 137/216 |
| homework_export.py | 65.8% | 60.6% | 29/29 |
| login.py | 70.7% | 84.2% | 13/13 |
| matrix.py | 76.1% | 88.5% | 10/10 |
| net.py | 71.7% | 77.4% | 26/26 |
| pipeline.py | 65.8% | 64.0% | 25/26（嵌套 provider 回调未单测） |
| theme_art.py | 49.9% | 73.3% | 20/20 |
| token_macos.py | 62.0% | 54.1% | 23/24 |
| token_model.py | 62.1% | 100.0% | 9/9 |
| token_source.py | 69.6% | 100.0% | 4/4 |
| token_windows.py | 38.7% | 57.1% | 5/5 |
| update_checker.py | 73.3% | 81.6% | 12/12 |
| update_installer.py | 59.5% | 54.7% | 16/16 |
| **合计** | **62.0%** | **59.1%** | **506/592** |

解释与残余风险：

- 非 GUI 业务函数为 369/376 已执行（98.1%）；未执行的 7 个主要是平台专属/进程入口路径（`banshu_export.main`、`token_macos.CDPClient._read_loop`、`token_macos` 的 `_app_candidates` 等），已做静态审查，但没有在真实 macOS/真实客户端上执行。
- `gui.py` 的 216 个函数/方法（含嵌套回调）中已执行 137 个；其余绝大多数是 Tk 绘制、菜单弹出、窗口生命周期和平台快捷键回调。它们通过 `App` 冒烟、主题切换、设置窗、日志/进度面板实际创建与销毁来覆盖，剩余绘制分支未逐分支自动断言，单独列为“不确定/静态审查”，不据此判定无 bug。
- 覆盖统计用于定位未走到的路径，不能替代逐条用例；用例结果以第 1.3 节和 `unittest -v` 输出为准。

### 1.3 测试用例表

下表每一行都对应 `tests/test_whitebox_audit.py` 中的一个或多个可执行方法；既有 `tests/` 中的 157 条用例继续保留。类别缩写：**合**=合法输入，**边**=边界值，**异**=异常路径，**错**=错误参数，**文**=文件不存在/权限/占用。

| 编号 | 目标函数 / 分支 | 输入或操作 | 预期 | 类别 | 对应用例 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |
| CFG-01 | `config.normalize_base_url` 正常分支 | `https://a.cn/base/`、`a.cn`、`:8443`、`:0` | 补全协议/斜杠，保留显式端口 | 合/边 | `ConfigBranchTests.test_normalize_valid_and_port_zero` | PASS |
| CFG-02 | `normalize_base_url` 异常分支 | 空串、http、账号密码、`https://a.cn:abc/`、坏 IPv6 | 返回 `None`，不抛异常 | 错/异 | `test_normalize_rejects_bad_inputs_without_crashing` | PASS |
| CFG-03 | `user_data_dirs` 去重/过滤 | 构造 APPDATA/LOCALAPPDATA 下多个目录 | 只返回存在的目录且去重 | 边 | `test_user_data_dirs_dedupes_and_filters` | PASS |
| CFG-04 | `discover_base_urls` 环境变量与静态域名过滤 | `AIWENYUN_BASE_URL`、`Network Persistent State` 含 download/static | 环境变量优先；静态/下载域被跳过 | 合/异 | `test_discover_env_wins_and_rejects_http`、`test_discover_filters_static_and_reads_json` | PASS |
| CFG-05 | `load_settings`/`save_settings`/主题 | 文件不存在、非 JSON、JSON 数组、写入/读取 | 返回 `{}` 或默认主题；不抛 | 文/错 | `test_settings_roundtrip_and_bad_json` | PASS |
| CFG-06 | `output_folder` 文件/权限路径 | 环境变量指向可写目录、指向已存在文件 | 前者返回目录；后者抛 `OSError` 由调用方处理 | 文/权限 | `test_output_folder_env_and_permission_error` | PASS |
| CFG-07 | `client_candidates`/`open_path` 平台分支 | 伪造 ProgramFiles、Windows/Linux | 只返回存在的 exe；Windows 用 `startfile`，Linux 用 `xdg-open` | 合/异 | `test_client_candidates_and_open_path` | PASS |
| AN-01 | `normalize_digits`/`extract_codes` | 全角数字、带标签/不带标签、7 位数字 | 全角转半角；只取 6 位；按出现顺序去重 | 合/边 | `AnalyzeBranchTests.test_digits_and_extract_codes` | PASS |
| AN-02 | `_clean_code` 类型分支 | `int`、全角字符串、`None`、`bool`、浮点、列表 | 仅合法 6 位返回字符串，其余空串 | 错/边 | `test_clean_code_branches` | PASS |
| AN-03 | `_six_digit_values` 深度/忽略键 | 嵌套 dict/list、`id`/`ts` 等键、布尔值 | 忽略 ID/时间字段；深度上限生效 | 边 | `test_six_digit_branch_and_invite_shapes` | PASS |
| AN-04 | `invite_code_from_payload` 多形态 | `obj` dict/list/字符串、`data`、两个候选、空值 | 结构化优先；唯一 6 位才兜底；多个不猜 | 合/异 | `test_six_digit_branch_and_invite_shapes` | PASS |
| AN-05 | `_find_list`/`timetable_total` | `obj.list`、`rows`、`result`、非法 total | 找到列表；非法计数按 0 | 边/错 | `test_find_list_and_totals` | PASS |
| AN-06 | `timetable_rows`/`live_info_map` | 重复 id、字符串 id、坏 id、groups dict | 去重、坏行跳过、groups 归一 | 边/错 | `test_timetable_rows_and_live_info` | PASS |
| AN-07 | `enrich_row`/`format_time` | 空 detail、字段回退、0/负数/超大时间戳 | 保留原值或补全；非法时间返回空串 | 边/错 | `test_enrich_and_format_time` | PASS |
| MAT-01 | `lesson_from_topic`/`course_from_topic` | `-1`、`-999`、`-0`、`-1000`、`第3节课`、括号课次 | 合法课次解析，边界拒绝，课程名剥离课次 | 合/边/错 | `MatrixBranchTests.test_lesson_and_course_boundaries` | PASS |
| MAT-02 | `natural_key`/`Matrix.width`/`Matrix.code` | 数字与非数字混合、空串、空矩阵 | 自然排序；空矩阵宽 0 | 边 | `test_empty_and_conflicts`、`test_lesson_and_course_boundaries` | PASS |
| MAT-03 | `normalize_items` groups 分支 | `groups` 为字符串、dict、list、数字、None | 字符串不再拆成字符；非法转空 | 错 | `test_groups_accept_string_dict_list` | PASS |
| MAT-04 | `assign_missing_lessons` 排序/补号 | 显式第 2 节 + 缺失课次；classId 字符串/整数混排 | 缺失课次从 1 开始且不覆盖显式课次；无 TypeError | 边/异 | `test_missing_lesson_no_collision_and_mixed_types`、`test_mixed_class_id_same_begin` | PASS |
| MAT-05 | `build_matrix` 冲突/空项 | 同班同课次不同邀请码、空 label、None lesson | 保留第一个；记录 conflict；空项跳过 | 合/边 | `test_empty_and_conflicts` | PASS |
| EX-01 | `excel._safe_title` | 非法文件字符、空、40 字符 | 替换非法字符；空回退；截断 31 | 边 | `ExcelBranchTests.test_safe_title` | PASS |
| EX-02 | `write_workbook` 空/多表/明细 | 无矩阵、2 个矩阵、空明细、元数据 | 至少一张表；明细表存在；属性写入 | 合/边 | `test_workbook_variants` | PASS |
| EX-03 | `write_workbook` 文件被占用 | 第一次 `os.replace` 抛 `PermissionError` | 自动改用 `out-1.xlsx` | 文/权限 | `test_permission_fallback_renames` | PASS |
| EX-04 | `write_workbook` 保存失败清理 | `Workbook.save` 抛 `OSError` | 异常上抛，临时文件不残留 | 异/文 | `test_temp_cleaned_when_save_fails` | PASS |
| NET-01 | `redact`/`_as_float` | token、`access_token`、换行、长度限制、非法浮点 | token 脱敏；换行压平；非法值回退 | 安全/边 | `NetBranchTests.test_redact_and_float` | PASS |
| NET-02 | `TokenBucket` 限速/取消 | rate=0、burst=0、取消事件已置位 | rate 下限 0.1、容量 1；取消抛 `ApiError` | 边/异 | `test_token_bucket_clamp_and_cancel` | PASS |
| NET-03 | `_abort_connection` | socket 为 None、shutdown/close 抛 OSError | 静默返回，不再次抛 | 异 | `test_abort_connection` | PASS |
| NET-04 | `HttpTransport.__init__` | http/ftp/无 host、带 path | 非 HTTPS 抛 `ValueError`；prefix 正确 | 错 | `test_transport_rejects_non_https` | PASS |
| NET-05 | `_parse_response` 状态码 | 401/403/429/500/302/199/400/200 | 分别抛 TokenExpired/RateLimitError/ApiError 或返回 | 异/边 | `test_parse_response_status_and_payload` | PASS |
| NET-06 | `_parse_response` 内容 | BOM JSON、`code=2`/`"2"`/7/None、非 UTF-8、非 JSON、数组 | 正确识别登录失效/业务错误/格式错误 | 异/错 | `test_parse_response_status_and_payload` | PASS |
| NET-07 | `post_json` 重试/超限/网络失败 | 首次 OSError 后成功、响应超过 16MB、连续失败 | 重试成功；超限失败；最终 NetworkError | 异/边 | `test_post_json_success_retry_and_oversize` | PASS |
| NET-08 | `ApiSession.headers`/token/close | 空 token、正常 token、关闭后 | 空 token 抛 TokenExpired；关闭清空 | 异/边 | `test_session_headers_and_close` | PASS |
| NET-09 | `fetch_timetable` 分页/去重 | total=3、重复 id、`max_pages=0` | 去重并提前停止；0 页仍安全 | 边 | `test_fetch_timetable_and_live_info` | PASS |
| NET-10 | `fetch_live_info`/`fetch_invite_code` | obj 为 list、坏 id、chunk_size=0、无邀请码 | 坏项跳过；chunk 归一；无码抛 ApiError | 错/异 | `test_fetch_timetable_and_live_info` | PASS |
| NET-11 | `authenticate` | 无候选、第一个失效第二个成功、全部失败 | 返回可用会话并关闭失败会话；否则 AuthError | 异 | `test_authenticate_no_candidate_and_failure` | PASS |
| LOG-01 | `transform_passwd` | 原文、md5、`P__`、混淆形式、大小写、空 | 按客户端规则变换；非法原文走 md5 | 合/边 | `LoginBranchTests.test_transform_passwd_edge_forms` | PASS |
| LOG-02 | `_friendly_error` | obj 为 list、`errLoginTimes`、空 msg、code | 不崩溃；拼出可读错误 | 错/异 | `test_friendly_error_shapes` | PASS |
| LOG-03 | `do_login` | 成功、obj 为 list、空账号/密码 | 成功返回 LoginResult；错误抛 LoginError | 合/错/异 | `test_do_login_success_and_failure` | PASS |
| LOG-04 | `_post_json` | 非 HTTPS、HTTP 500、坏 JSON、数组、超长、RemoteDisconnected | 统一转 LoginError，不泄露密码/token | 错/异 | `test_post_json_http_and_json_errors` | PASS |
| LOG-05 | 设备 ID 读取/持久化 | leveldb 命中、文件不存在、内容损坏、mkdir 权限不足 | 优先真实 ID；否则生成稳定 ID；失败不崩 | 文/权限 | `test_device_id_paths` | PASS |
| LOG-06 | `device_payload`/`scrub` | 无 hostname、密码、None | deviceName 回退 `pc`；密码替换 | 合/边 | `test_device_payload_and_scrub` | PASS |
| TOK-01 | `TokenCandidate` 属性 | 合法/非法 token、metadata | 解析 user/org/version/platform；坏值空 | 合/边 | `TokenModelBranchTests.test_candidate_properties` | PASS |
| TOK-02 | `timestamp_from_generic`/`extract_from_bytes` | 10 位时间戳、无标记、ASCII/UTF-16 | 只收合法时间；两种编码都能提取 | 合/边 | `test_timestamp_and_extract` | PASS |
| TOK-03 | `token_source` 平台入口 | Windows/macOS、debug 端口、acquire full | 返回正确 batch/info；平台分派 | 合/异 | `TokenSourceWindowsTests.test_debug_port_and_client_running`、`test_acquire_tokens_platforms` | PASS |
| TOK-04 | `token_windows` 进程/区域/扫描提示 | tasklist 输出、坏行、拒绝访问、marker 命中 | 解析 pid；不可读跳过；错误提示准确 | 边/异 | `test_windows_pid_and_region_helpers`、`test_scan_tokens_empty_found_and_hints`、`CoverageGapTests.test_token_windows_iter_regions_and_scan_pid` | PASS |
| TOK-05 | `token_macos` bundle/进程/等待 | Info.plist、可执行回退、ps 输出、端口等待 | 找到可执行；过滤 python；超时/取消返回 | 合/文/异 | `TokenMacBranchTests.test_bundle_executable_plist_fallback_and_errors`、`test_process_helpers_and_wait` | PASS |
| TOK-06 | `CDPClient` 握手/帧/调用 | 坏 URL、握手断开、3 种帧长、错误响应 | 失败关闭 socket；帧长度正确；异常转 CDPError | 异/资源 | `test_cdp_frames_and_handshake_cleanup`、`CoverageGapTests.test_cdp_call_evaluate_and_close` | PASS |
| TOK-07 | `read_context`/`acquire_tokens` | 无 page、previewToken、disable_restart | 无页面报错；previewToken 不算登录；未允许重启报错 | 异/边 | `test_read_context_and_acquire` | PASS |
| PIP-01 | `ExportSummary.as_dict` | 含/不含 csv/json/diagnostics | 路径字符串化；elapsed 保留 2 位 | 合/边 | `PipelineBranchTests.test_summary_as_dict` | PASS |
| PIP-02 | `TokenState.current/refresh/close` | generation 不一致、关闭后、refresh 失败 | 复用当前会话；关闭后 AuthError；失败置 auth_failed | 边/异 | `test_token_state_current_refresh_and_generation_guard`、`test_token_state_refresh_failure` | PASS |
| PIP-03 | `Cooldown.wait` 截止时间/取消 | 全局 pause 1s、deadline 0.12s、取消已置位 | 不超过 deadline；取消立即返回 | 边/异 | `test_cooldown_deadline_and_cancel` | PASS |
| PIP-04 | `_call_with_refresh` | 首次 TokenExpired、第二次成功、持续失败 | refresh 一次；持续失败抛 ApiError | 异 | `test_call_with_refresh` | PASS |
| PIP-05 | `_fetch_one` 全部分支 | 成功、取消、超时、无登录态、429、网络、API、未知异常 | 每种路径生成对应 note/diag；不中断整批 | 合/边/异 | `test_fetch_one_paths` | PASS |
| PIP-06 | `_sleep_backoff` | 取消已置位 | 立即返回 | 异 | `test_sleep_backoff_cancel` | PASS |
| PIP-07 | `run_export` 成功输出 | 正常课堂、CSV/JSON/诊断全开 | Excel/CSV/JSON/诊断文件均生成，summary 正确 | 合 | `test_run_export_success_outputs_and_csv_failure` | PASS |
| PIP-08 | `run_export` 可选输出失败 | `_write_csv` 抛 OSError | Excel 保留，csv_path 为 None，不整体失败 | 异/文 | 同上 | PASS |
| PIP-09 | `run_export` 参数/空数据/取消 | 非 HTTPS base、空课堂、取消已置位 | 分别 PipelineError/NoClassesError/Cancelled | 错/异 | `test_run_export_errors` | PASS |
| PIP-10 | `_write_csv/_write_json/_write_diagnostics` | `os.replace` 失败 | 异常上抛，`.tmp` 清理 | 异/文 | `test_write_helpers_cleanup_on_failure` | PASS |
| HW-01 | 作业列表/成绩 obj 非法 | `obj` 为 list、`studentZuoyeInfo` 为字符串 | 列表抛“响应格式异常”；成绩列表安全转空 | 错/异 | `HomeworkBranchTests.test_fetch_helpers_guard_malformed_obj` | PASS |
| HW-02 | `export_homeworks` 应交人数/写失败 | `zuoyesNum='abc'`、输出目录是文件 | 人数按 0；单个文件失败只记 failed，不中断整批 | 错/文 | `test_export_homeworks_bad_expected_and_write_failure` | PASS |
| HW-03 | `write_csv` 权限/原子写 | 目标同名目录、正常路径 | 抛 OSError 且无 .tmp；正常写返回行数 | 文/权限 | `test_write_csv_permission_and_atomic` | PASS |
| FB-01 | `_num`/`_ceil_minutes`/时间/进度 | inf、nan、负数、空、百分比 | 非有限数按 0；分钟向上取整；缺失 `-` | 边/错 | `FeedbackBranchTests.test_numbers_and_time_helpers` | PASS |
| FB-02 | 中文数字/课次/作业类型 | 0、10、11、23、101、负、专题/第N节 | 中文数字正确；课次/类型识别 | 合/边 | `test_chinese_and_lesson_parsing` | PASS |
| FB-03 | 班期身份/投票/反馈包 | 空班名、未分类、202602、平票 | 不生成空班；平票不反推；反馈包缺失项正确 | 合/边 | `test_identity_and_bundle_filters` | PASS |
| FB-04 | `build_feedback_rows` 坏字段 | `mediaType='bad'`、空姓名、非 dict | 课堂类型回退；空姓名跳过；不崩溃 | 错/异 | `test_build_rows_tolerates_bad_media_and_missing` | PASS |
| FB-05 | `_student_detail` 字段类型 | groupIds/begin 非数字、teacher 非数字、正常 | 坏字段返回 `{}`；正常 payload 正确 | 错/边 | `test_student_detail_bad_and_good_fields` | PASS |
| FB-06 | `_attend_students`/`_signin_students` | obj 为 list、分页 2 页、取消、liveclass=0 | 非法抛格式异常；分页合并；取消抛 BatchCancelled | 异/边 | `test_attend_signin_pagination_and_malformed` | PASS |
| FB-07 | `_optional_obj`/文件名/去重/清理 | TokenExpired、普通异常、空 obj、重名、旧文件 | TokenExpired 上抛；普通异常吞掉；只清生成文件 | 异/文 | `test_optional_obj_and_file_helpers`、`test_clean_bundle_folder_only_removes_generated` | PASS |
| FB-08 | `write_feedback_csv`/`export_feedback_bundles` | 目标为目录、空 bundles、out_root 为文件 | 权限失败无残留；空导出 total=0；目录失败记 failed | 文/权限/异 | `test_write_feedback_csv_permission_and_success`、`test_export_bundles_permission_and_empty` | PASS |
| BAN-01 | 命名/学期/排序辅助 | 非法字符、希腊后缀、26秋/2026秋、坏时间 | 文件名安全；学期标签统一；坏时间空 | 合/边 | `BanshuBranchTests.test_naming_and_term_helpers` | PASS |
| BAN-02 | 班级标签学习/显示 | 202602 + 26秋/26暑、同显示名冲突 | 学会期号→季节；冲突回退官方全名 | 合/边 | `test_group_labels_and_learning` | PASS |
| BAN-03 | 班级匹配/提取 | 前后缀、部分匹配、嵌套 dict/list、数字 ID | 完整边界；不误命中；数字 ID 不作为班名 | 边/错 | `test_matching_and_extraction` | PASS |
| BAN-04 | 分组/顺序/筛选 | 作业班、杂项、空班级列表、labels | 杂项最后；列表空不筛选；学期排序 | 合/边 | `test_grouping_and_order` | PASS |
| BAN-05 | `collect_pages`/`order_pages` | cmds/coursewares/smallboards 畸形、非图片、type 排序 | 坏项跳过；类型顺序 board→small→annotate | 异/边 | `test_collect_and_order_pages` | PASS |
| BAN-06 | `fetch_records` 分页 | `code="0"`、obj list、取消、page_size=0、无 total | 兼容字符串 0；畸形报错；200 页上限防死循环 | 异/死循环 | `test_fetch_records_guards_and_cap` | PASS |
| BAN-07 | `bs_manifest` | code=0/缺 signature/status=1/status=2 缺 url/正常 gzip | 缺字段抛 BsUnavailable；正常返回 manifest/prefix | 异/边 | `test_bs_manifest_paths` | PASS |
| BAN-08 | PDF 构建/回读 | 3 元组旧接口、4 元组历史接口、页数不符、坏头、文件不存在 | 两种入参都可写；校验失败返回 False；缺失抛 OSError | 合/边/文 | `test_pdf_build_and_validate` | PASS |
| BAN-09 | `export_class`/`export_records` | 缺 fileId、BsUnavailable、空清单、复用、单课失败 | 跳过/复用/失败隔离；不中止其他课 | 合/异 | `test_export_class_and_records` | PASS |
| BAN-10 | `resolve_range`/`run_board_export` | 全部预设、自定义反向/未来/缺日期、混合 createTime | 预设返回区间；错误参数抛 ValueError；混合类型可排序 | 边/错 | `test_range_and_run_board_export` | PASS |
| BAN-11 | `PageDownloader`/图片/旧接口 | 200、301、非 HTTPS、PNG 解码、空 Edge | 下载/重定向/拒绝明文；PDF 页对象；无渲染器返回 None | 合/异/文 | `CoverageGapTests.test_page_downloader_and_image_helpers` | PASS |
| UPD-01 | 更新 payload | JSON/HTML/纯版本、缺版本、嵌套字段、sha 字段 | 提取版本/下载/校验；缺版本抛 UpdateError | 合/错 | `tests/test_update_checker.py`（既有） | PASS |
| UPD-02 | 版本比较 | 2.3.0>2.2.9、相等、2.2<2.2.1、非数字 | 语义比较；非数字按字符串差异 | 边 | `tests/test_update_checker.py`（既有） | PASS |
| UPD-03 | 更新重定向安全 | 302 到 `http://` | 在发出新请求前拒绝 | 安全 | `UpdateBranchTests.test_redirect_handlers_reject_http` | PASS |
| UPD-04 | 下载/校验/启动保护 | 正常下载、Content-Length 超限、不完整、非 HTTPS、非 win32、缺包 | 成功落盘；异常清理 `.part`；非 exe 拒绝 | 异/文 | `test_download_file_success_and_errors`、`test_fetch_sha256_and_launch_guards` | PASS |
| THM-01 | 色板/角色/颜色工具 | 未知主题、合法/非法颜色、混合比例 | 未知回退 light；颜色角色反查；比例夹紧 | 合/边 | `ThemeBranchTests.test_palette_and_colors` | PASS |
| THM-02 | 素材加载/渲染 | 缺图、解压炸弹、裁剪、hero/icon/preview | 缺图/炸弹返回 None；尺寸正确 | 异/资源 | `test_assets_and_render` | PASS |
| CLI-01 | `cli.build_parser/main` 成功 | `--no-open --no-details --workers 2` | 参数解析正确；返回 0 | 合 | `CliBranchTests.test_parser_and_success` | PASS |
| CLI-02 | CLI 退出码 | NoClasses/Cancelled/PipelineError/未知异常/KeyboardInterrupt | 3/130/4/4/130 | 异 | `test_error_exit_codes` | PASS |
| GUI-01 | `_fmt_clock`/`_rgb` | 62s、1h、NaN、非法颜色 | 时间格式正确；NaN 归 0；非法颜色黑 | 边/错 | `GuiBranchTests.test_helpers_and_version_file` | PASS |
| GUI-02 | `--version-file` | 正常路径、缺参数 | 写版本并返回 0；缺参数返回 2 | 边/错 | 同上 | PASS |
| GUI-03 | `_board_work` 保存目录失败 | out_root 是文件 | 通过队列上报 error，不静默死线程 | 文/异 | `test_board_work_reports_mkdir_failure` | PASS |
| GUI-04 | App 状态冒烟 | 三页切换、主题切换、设置窗、日志/进度、板书/反馈列表、更新结果、关闭 | 不抛异常；关键状态可用 | 合/边 | `test_app_state_smoke` | PASS |

### 1.4 全量函数梳理

`docs/whitebox_function_inventory.csv` 由 AST 生成，列出根目录 19 个业务模块中全部 **592 个函数/方法**（模块、类名.函数名、起止行号、静态分支数），共 593 行（含表头）。用途：

- 逐项核对“哪些函数被用例执行、哪些只做静态审查”；
- 追溯每个分支点所在行，配合第 1.2 节覆盖统计定位缺口；
- GUI 绘制/布局方法的静态审查结论统一记录为“UI 冒烟 + 人工审查”，不冒充已执行分支。

生成命令（可复现）：

```bash
python - <<'PY'
import ast, csv
from pathlib import Path
root = Path('.')
rows = []
for path in sorted(root.glob('*.py')):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rows.append((path.name, node.name, node.lineno,
                         getattr(node, 'end_lineno', node.lineno)))
# 详见 docs/whitebox_function_inventory.csv
PY
```

---

## 2. 发现的 bug 清单

### 2.1 八类风险逐项检查结论

| 检查项 | 结论 | 证据/说明 |
| --- | --- | --- |
| 语法错误 | 未发现 | 全部 19 个业务模块 `python -m py_compile` 通过；`run_gui.py`/`run_cli.py` 入口可导入 |
| 类型错误 | 发现并修复 5 类 | `classId` 混型、`zuoyesNum`、`groupIds/startTime/mediaType`、`createTime`、`chunk_size=0`；对应 BUG-02/04/06/24/27 |
| 索引越界 | 未复现 | 列表分页均有长度/空值检查；PDF 页数校验失败返回 False；未发现可稳定复现的越界。真实服务端异常结构仍列为 Q-14 不确定 |
| 空值/缺失字段 | 发现并修复 5 类 | `obj` 非 dict、`url` 缺失、`signature` 缺失、settings 缺失、空 token；对应 BUG-03/05/08/09 |
| 死循环/卡死 | 发现并修复 1 类 | `fetch_records` 的 `page_size=0`/无 total 分页；加 200 页上限。另修复 `Cooldown.wait` 越过课堂 deadline 的长时间等待（BUG-11） |
| 资源未释放 | 发现并修复 3 类 | CDP 握手失败 socket、CSV/JSON/诊断 `.tmp` 残留、PDF/CSV 非原子写；对应 BUG-13/21/22 |
| 逻辑结果错误 | 发现并修复 6 类 | `groups` 拆字符、课次撞号、`第N节课` 漏识别、数字 groupId 当班级、CSV/JSON 失败导致整体失败、板书排序混型；对应 BUG-17/18/19/20/23/24 |
| 安全隐患 | 发现并修复 1 类 | 更新检查/下载先跟随 HTTP 重定向；另有既有 token 脱敏、HTTPS-only、SHA-256/PE 校验复核通过；对应 BUG-14 |

### 2.2 bug 明细

> 分类规则：**① 直接报错/崩溃/卡死/安全问题**；**② 运行成功但输出不符合业务逻辑**。② 同样按 bug 处理。每条都给出最小复现；复现命令均在项目根目录执行。无法稳定复现的项已放入第 5 节“不确定”，不据此判定无 bug。

| 编号 | 类别 | 位置 | 症状与影响 | 最小复现 | 关联用例 |
| --- | --- | --- | --- | --- | --- |
| BUG-01 | ① | `config.normalize_base_url` | 非法端口/坏 IPv6 直接 `ValueError`，环境变量或 `--base-url` 可让程序启动即崩 | `python -c "from config import normalize_base_url; normalize_base_url('https://a.cn:abc/')"` | CFG-02 |
| BUG-02 | ① | `matrix.assign_missing_lessons` | `classId` 同时出现字符串和整数时 `TypeError`，课程表无法生成 | 见下方 R-02 | MAT-04 |
| BUG-03 | ① | `homework_export.fetch_homeworks` / `fetch_scores` | 服务端 `obj` 为 list/字符串时 `AttributeError`，作业导出整体失败 | 见 R-03 | HW-01 |
| BUG-04 | ① | `homework_export.export_homeworks` | `zuoyesNum` 非数字时 `ValueError`；单个 CSV 写入失败会中断整批 | 见 R-04 | HW-02 |
| BUG-05 | ① | `feedback_export._attend_students` / `_signin_students` | `obj` 非 dict 时 `AttributeError`，课堂权限 CSV 无法生成 | 见 R-05 | FB-06 |
| BUG-06 | ① | `feedback_export._student_detail` / `build_feedback_rows` | `groupIds`、`startTime`、`mediaType` 非数字时 `ValueError`，整份 CSV 失败 | 见 R-06 | FB-04/FB-05 |
| BUG-07 | ① | `banshu_export.fetch_records` | `obj` 非 dict 时 `AttributeError`；`page_size=0` 或 total 异常时可能无限翻页卡死 | 见 R-07 | BAN-06 |
| BUG-08 | ① | `banshu_export.bs_manifest` | `genBs` 返回 status=2 但缺 `url` 时 `KeyError`；code 为字符串 `"0"` 被误判失败 | 见 R-08 | BAN-07 |
| BUG-09 | ① | `login._friendly_error` / `do_login` | `obj` 为 list 时 `AttributeError`，登录错误无法转成可读提示 | 见 R-09 | LOG-02/03 |
| BUG-10 | ① | `banshu_export.build_pdf` | 按类型标注/文档传 `(raw,width,height)` 时 `ValueError: not enough values to unpack` | `build_pdf([(b'\0\0\0',1,1)])` | BAN-08 |
| BUG-11 | ① | `pipeline.Cooldown.wait` | 其他线程设置的全局冷却可让单课等待远超 `class_deadline`，表现为“卡住” | 见 R-11 | PIP-03 |
| BUG-12 | ① | `gui._board_work` | 保存目录不可创建时 daemon 线程静默退出，界面永久停在“正在导出” | 见 R-12 | GUI-03 |
| BUG-13 | ① | `token_macos.CDPClient.__init__` / `_bundle_executable` | 握手失败 socket 未关闭；`MacOS` 目录不可读时 `PermissionError` | 见 R-13 | TOK-06/TOK-05 |
| BUG-14 | ① | `update_checker.fetch_update_payload` / `update_installer._open_url` | urllib 会先跟随 302 到 `http://`，再在事后检查时拒绝；明文请求已经发出 | 见 R-14 | UPD-03 |
| BUG-15 | ① | `theme_art.load_user_asset` | Pillow 解压炸弹异常未被捕获，素材损坏时界面启动失败 | 见 R-15 | THM-02 |
| BUG-16 | ① | `gui._fmt_clock` | `NaN/Infinity` 触发 `ValueError/OverflowError` | `_fmt_clock(float('nan'))` | GUI-01 |
| BUG-17 | ② | `matrix.normalize_items` | `groups` 为字符串时被逐字符拆成 `['a','b','c']`，班级列显示错误 | 见 R-17 | MAT-03 |
| BUG-18 | ② | `matrix.assign_missing_lessons` | 缺失课次按桶内序号编号，可能与显式课次撞号，两个课堂被合并/冲突丢弃 | 见 R-18 | MAT-04 |
| BUG-19 | ② | `matrix.lesson_from_topic` | 只认“第9节/第9课”，不认常见的“第9节课”，课次被误判为无 | 见 R-19 | MAT-01 |
| BUG-20 | ② | `banshu_export.fallback_class_names` | 班级字段里混入数字 groupId 时，数字被当作班级名，生成“3”这种假班级按钮/文件夹 | 见 R-20 | BAN-03 |
| BUG-21 | ② | `homework_export.write_csv` | 直接覆盖目标文件，磁盘满/权限中断会留下半截 CSV | 见 R-21 | HW-03 |
| BUG-22 | ② | `pipeline._write_csv/_write_json/_write_diagnostics` | 写入失败时 `.tmp` 残留，长期运行会堆积临时文件 | 见 R-22 | PIP-10 |
| BUG-23 | ① | `pipeline.run_export` | Excel 已生成后 CSV/JSON 写入失败会整体抛错，用户看到“导出失败”且拿不到 summary | 见 R-23 | PIP-08 |
| BUG-24 | ① | `banshu_export.run_board_export` | 记录 `createTime` 混用字符串/整数时排序 `TypeError`，批量列出板书失败 | 见 R-24 | BAN-10 |
| BUG-25 | ① | `banshu_export.collect_pages` | 清单 `cmds/coursewares` 结构异常时 `AttributeError`，单课导出失败 | 见 R-25 | BAN-05 |
| BUG-26 | ① | `login._post_json` | `http.client.RemoteDisconnected` 等 `HTTPException` 未转成 LoginError，界面显示原始异常名 | 见 R-26 | LOG-04 |
| BUG-27 | ① | `net.ApiSession.fetch_live_info` | `chunk_size=0` 时 `range(...,0)` 抛 `ValueError` | `fetch_live_info([1,2], chunk_size=0)` | NET-10 |

### 最小复现（可直接粘贴）

**R-02**

```python
import matrix
matrix.normalize_items([
    {'topic': 'A', 'begin': 5, 'classId': 'x'},
    {'topic': 'A', 'begin': 5, 'classId': 3},
])
# 修复前：TypeError: '<' not supported between instances of 'int' and 'str'
```

**R-03**

```python
import homework_export as h
class S:
    def post(self, *a, **k): return {'obj': [1]}
h.fetch_homeworks(S(), 0, 0)   # AttributeError: 'list' object has no attribute 'get'
h.fetch_scores(S(), 'x')       # 同上
```

**R-04**

```python
import homework_export as h
from pathlib import Path
class S:
    def post(self, *a, **k): return {'obj': {'studentZuoyeInfo': []}}
h.export_homeworks(S(), [{'zuoyeId': 1, 'title': 't',
                          'groupName': 'g', 'zuoyesNum': 'abc'}], Path('out'))
# 修复前：ValueError: invalid literal for int() with base 10: 'abc'
```

**R-05**

```python
import feedback_export as f
class S:
    def post(self, *a, **k): return {'obj': [1]}
f._attend_students(S(), 'r')   # AttributeError
f._signin_students(S(), 1)     # AttributeError
```

**R-06**

```python
import feedback_export as f
f.build_feedback_rows(
    [{'username': '张三', 'userId': '1'}], [], {},
    {'mediaType': 'abc'}, {})
# 修复前：ValueError: invalid literal for int() with base 10: 'abc'

class S:
    def post(self, *a, **k): return {'obj': {}}
f._student_detail(S(), {'groupIds': 'bad', 'userId': '1'},
                  {'startTime': 1}, 0)  # 修复前 int('bad') ValueError
```

**R-07**

```python
import banshu_export as b
class S:
    def post(self, *a, **k):
        return {'code': 0, 'obj': [1]}
b.fetch_records(S(), 0, 1)  # AttributeError

class Endless:
    def post(self, *a, **k):
        return {'code': 0, 'obj': {'list': [{'id': 1}]}}
b.fetch_records(Endless(), 0, 1, page_size=0)
# 修复前：index 不前进，while True 无限请求
```

**R-08**

```python
import banshu_export as b
class S:
    def post(self, path, payload):
        if path.endswith('getBsSign'):
            return {'code': 0, 'obj': {'signature': 's'}}
        return {'code': 0, 'obj': {'status': 2}}   # 缺 url
b.bs_manifest(S(), 'id', poll_delays=(0,))
# 修复前：KeyError: 'url'
```

**R-09**

```python
import login
login._friendly_error({'code': 1, 'obj': [1], 'msg': 'x'}, 'p')
# 修复前：AttributeError: 'list' object has no attribute 'get'
```

**R-11**

```python
import threading, time
from pipeline import Cooldown
c = Cooldown(); c.pause(5)
start = time.monotonic()
c.wait(deadline=time.monotonic() + 0.2)
print(time.monotonic() - start)   # 修复前约 5s，修复后 <0.3s
```

**R-12**

```python
from pathlib import Path
import gui
class Fake:
    def _post(self, kind, payload=None): print(kind, payload)
gui.App._board_work(Fake(), [], 'time', Path('某个已存在的文件'))
# 修复前：NotADirectoryError 直接抛在工作线程，没有任何 error 事件
```

**R-13**

```python
import token_macos
class S:
    def settimeout(self, v): pass
    def sendall(self, b): pass
    def recv(self, n): return b''   # 握手阶段断开
    def close(self): print('closed')
# patch socket.create_connection 返回 S 后构造 CDPClient：
# 修复前握手异常，socket 没有 close()；修复后 close() 必被调用
```

**R-14**

```python
import urllib.request
from update_checker import _HttpsOnlyRedirectHandler
req = urllib.request.Request('https://x/')
_HttpsOnlyRedirectHandler().redirect_request(
    req, None, 302, 'Found', {}, 'http://x/')
# 修复前：urllib 会先请求 http://x/；修复后直接 UpdateError
```

**R-15**

```python
from unittest.mock import patch
import theme_art
with patch.object(theme_art.Image, 'open',
                  side_effect=theme_art.Image.DecompressionBombError('x')):
    theme_art.load_user_asset('x.png')
# 修复前：异常冒出；修复后返回 None
```

**R-17**

```python
import matrix
items = matrix.normalize_items([{'topic': 'A-1', 'groups': 'G1'}])
print(items[0]['groups'])   # 修复前 ['G','1']，修复后 ['G1']
```

**R-18**

```python
import matrix
items = matrix.normalize_items([
    {'topic': 'A-2', 'code': '222222', 'begin': 10},
    {'topic': 'A', 'code': '111111', 'begin': 20},
])
print([(i['topic'], i['lesson']) for i in items])
# 修复前：[('A-2', 2), ('A', 2)]，两个课次撞在第 2 节
# 修复后：[('A-2', 2), ('A', 1)]
```

**R-19**

```python
import matrix
print(matrix.lesson_from_topic('第3节课'))   # 修复前 None，修复后 3
```

**R-20**

```python
import banshu_export
names = banshu_export.fallback_class_names([
    {'groupNames': ['B', 3]},
])
print(names)   # 修复前 ['B', '3']，修复后 ['B']
```

**R-21 / R-22**

```python
# 目标同名目录/磁盘满/替换失败时：
# 修复前 write_csv 直接 path.open('w')，会留下半截文件；
# _write_csv/_write_json/_write_diagnostics 会留下 .tmp。
# 用例：HomeworkBranchTests.test_write_csv_permission_and_atomic
#      PipelineBranchTests.test_write_helpers_cleanup_on_failure
```

**R-23**

```python
# run_export 中 Excel 已经写完，随后 _write_csv 抛 OSError：
# 修复前整个 run_export 抛出，GUI 显示“导出失败”；
# 修复后记录日志、csv_path 为 None，Excel/summary 正常返回。
```

**R-24**

```python
import banshu_export as b
records = [{'shortDesc': 'A-1', 'createTime': 'bad'},
           {'shortDesc': 'A-2', 'createTime': 1}]
# run_board_export -> records.sort(key=...createTime or 0)
# 修复前：TypeError: '<' not supported between 'str' and 'int'
```

**R-25**

```python
import banshu_export as b
b.collect_pages({'cmds': 'bad'})
# 修复前：字符串可迭代，但结构不对时后续 .get/索引异常
# 修复后：结构异常安全返回 []
```

**R-26**

```python
import http.client, login
class Broken:
    def request(self, *a, **k):
        raise http.client.RemoteDisconnected('x')
    def close(self): pass
# patch login.http.client.HTTPSConnection 返回 Broken：
# 修复前抛 RemoteDisconnected；修复后 LoginError('登录请求失败（网络）…')
```

**R-27**

```python
from net import ApiSession
s = ApiSession('https://x.cn/', 'tok')
s.fetch_live_info([1, 2], chunk_size=0)
# 修复前：ValueError: range() arg 3 must not be zero
# 修复后：chunk_size 归 1，正常请求
```

### 既有测试已覆盖、但不属于本次新增 bug 的行为

- 邀请码矩阵冲突保留第一个（`tests/test_core.py`）；
- 卡住看门狗、第二轮重试、取消（`tests/test_core.py`）；
- 板书分页/取消/并发导出（`tests/test_board_login.py`、`test_board_speed.py`）；
- 反馈课次组装、同名班跨学期分目录（`tests/test_feedback_export.py`）；
- 更新包 SHA-256/PE/脚本转义（`tests/test_update_installer.py`）；
- 版本号与 README/工作流一致性（`tests/test_release_metadata.py`）。

---

## 3. 修改方案

> 约束：未新增或升级第三方依赖；未改接口路径、字段名、CSV 列序和业务规则；所有修复都是“对异常输入做防御/原子写/失败隔离”，不改变正常输入下的结果。

| 编号 | 修复方案 | 兼容性说明 |
| --- | --- | --- |
| BUG-01 | `normalize_base_url` 把 `urlparse/port` 包在 `try/except ValueError`，非法返回 `None`；显式 `:0` 保留 | 正常 URL 输出不变；原来会崩的输入现在按“无效地址”处理 |
| BUG-02 | 新增 `_sort_stamp`，`begin/classId` 统一转 int；坏值按 0 | 只影响缺失课次的排序稳定性，不改已有课次 |
| BUG-03 | `fetch_homeworks/fetch_scores` 校验响应为 dict、`list` 为 list；异常转 `RuntimeError` | 正常响应不变；GUI 已有失败隔离 |
| BUG-04 | `zuoyesNum` 用 `try/int`；把 `statistics_filename+write_csv` 放进单条 `try`，失败记 `failed` 后继续 | 文件名/CSV 内容不变；失败不再中断整批 |
| BUG-05 | `_attend_students/_signin_students` 校验 `response/obj/list`，非法抛格式异常 | 正常分页不变；由反馈导出上层记失败 |
| BUG-06 | 新增 `_int_or`；`_student_detail` 统一 `try/int`，坏字段返回 `{}`；`mediaType` 用 `_int_or` | 正常数字/字符串数字结果不变 |
| BUG-07 | `fetch_records` 校验响应结构；`page_size=max(1,int)`；最多 `MAX_RECORD_PAGES=200` 页 | 正常分页结果不变；异常分页改为明确报错，不再卡死 |
| BUG-08 | `bs_manifest` 校验 `getBsSign/genBs` 的 code/obj；缺 `url` 抛 `BsUnavailable`；接受字符串 `"0"` | 正常流程不变；缺字段从崩溃变成“该课无板书/失败” |
| BUG-09 | `_friendly_error/do_login` 对 `obj/myOrg` 做 dict 检查 | 正常响应不变 |
| BUG-10 | `build_pdf` 同时接受 3 元组和 4 元组 | 保留旧调用；按文档调用不再崩 |
| BUG-11 | `Cooldown.wait` 增加 `deadline`，等待时间取 `min(remaining,left)`；`_fetch_one` 传入课堂 deadline | 正常冷却不变；不会越过单课截止时间 |
| BUG-12 | `_board_work` 先 `try: out_root.mkdir`，失败 `_post('error', ...)` 并 return | 正常导出不变；失败时界面能恢复 |
| BUG-13 | `CDPClient.__init__` 握手失败关闭 socket；`_bundle_executable` 的 `iterdir` 捕获 `OSError`；读循环捕获 `UnicodeDecodeError` | 正常 macOS 流程不变；失败不再泄漏/崩溃 |
| BUG-14 | 两个模块新增 `_HttpsOnlyRedirectHandler`，在 `redirect_request` 阶段拒绝非 HTTPS；用 `HTTPSHandler(context=...)` 构造 opener | 只影响重定向；初始 URL 仍必须是 HTTPS |
| BUG-15 | `load_user_asset` 捕获 `Image.DecompressionBombError` | 正常素材不变；恶意/损坏素材返回 None |
| BUG-16 | `_fmt_clock` 捕获 `TypeError/ValueError/OverflowError`，非法归 0 | 正常耗时显示不变 |
| BUG-17 | `normalize_items` 对 `groups` 字符串按单值处理，非 list/tuple/set 归空，过滤 None | 正常 list/dict 不变 |
| BUG-18 | 缺失课次使用“桶内已占用编号之外的第一个正整数” | 仅影响缺失课次；显式课次不再被覆盖 |
| BUG-19 | 课件次正则改为 `(?:节课|节|课)` | 兼容原“第9节/第9课”，新增“第9节课” |
| BUG-20 | `fallback_class_names.add` 过滤纯数字/空白/下划线类 groupId | 正常班级名不变；不再生成数字班 |
| BUG-21 | `write_csv` 临时文件 + `os.replace`，`finally` 清理 | 输出字节与原来一致（BOM/CRLF/末尾无换行） |
| BUG-22 | 三个写入函数临时文件 + `finally` 清理；诊断文件先 mkdir | 正常输出不变 |
| BUG-23 | `run_export` 对 CSV/JSON 分别 `try`，失败只记日志并让 `csv_path/json_path=None` | Excel 与 summary 不再因可选文件丢失 |
| BUG-24 | `run_board_export` 用 `record_time` 排序 | 排序语义不变（按创建时间升序），混合类型不崩 |
| BUG-25 | `collect_pages` 校验 manifest/cmds/coursewares/smallboards 结构，坏项跳过 | 正常清单结果不变 |
| BUG-26 | `_post_json` 捕获 `http.client.HTTPException` | 网络断开统一转 LoginError |
| BUG-27 | `fetch_live_info` 的 `chunk_size=max(1,int(...))` | 正常 chunk 不变；0 不再崩 |

依赖结论：无新增依赖；`Pillow`、`openpyxl`、`certifi` 均为原有 `requirements.txt` 内容。安装命令见 1.1。

---

## 4. 修复后的代码

以下按文件列出全部修复点的最终代码。为控制篇幅，只贴出被修改的函数/分支片段；未贴出的上下文与仓库源码完全一致。完整可执行版本以根目录 `.py` 文件为准。

### 4.1 `config.py`

```python
def normalize_base_url(value: str) -> str | None:
    value = (value or '').strip()
    if not value:
        return None
    if '://' not in value:
        value = 'https://' + value
    try:
        parsed = urlparse(value)
        if parsed.scheme.lower() != 'https' or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None
        netloc = parsed.hostname
        # parsed.port 在端口不是数字（或 IPv6 写法损坏）时会抛 ValueError；
        # 这里统一按「无效地址」处理，避免环境变量/--base-url 直接把程序打崩。
        port = parsed.port
        if port is not None:
            netloc += ':%d' % port
    except ValueError:
        return None
    path = parsed.path.rstrip('/')
    return 'https://%s%s/' % (netloc, path)
```

### 4.2 `matrix.py`

```python
LESSON_PATTERNS = (
    re.compile(r'[-_－—]\s*(\d{1,3})\s*(?:β[123])?\s*$'),
    re.compile(r'第\s*(\d{1,3})\s*(?:节课|节|课)\s*$'),
    re.compile(r'[（(]\s*(\d{1,3})\s*[)）]\s*$'),
)

def normalize_items(raw_items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        topic = str(raw.get('topic') or '').strip()
        groups = raw.get('groups') or []
        if isinstance(groups, dict):
            groups = list(groups.values())
        elif isinstance(groups, str):
            # 单个班级名不能被逐字符拆开；兼容调用方直接传字符串。
            groups = [groups]
        elif not isinstance(groups, (list, tuple, set)):
            groups = []
        groups = [str(name).strip() for name in groups
                  if name is not None and str(name).strip()]
        course = str(raw.get('course') or '').strip() or course_from_topic(topic)
        lesson = raw.get('lesson')
        if isinstance(lesson, str) and lesson.strip().isdigit():
            lesson = int(lesson.strip())
        if not isinstance(lesson, int) or lesson <= 0:
            lesson = lesson_from_topic(topic)
        code = str(raw.get('code') or '').strip()
        items.append({
            'topic': topic,
            'course': course,
            'groups': groups,
            'lesson': lesson,
            'code': code,
            'begin': raw.get('begin') or 0,
            'teacher': str(raw.get('teacher') or '').strip(),
            'orgId': raw.get('orgId'),
            'classId': raw.get('classId') or raw.get('id'),
            'note': str(raw.get('note') or '').strip(),
        })
    assign_missing_lessons(items)
    return items


def _sort_stamp(value: Any) -> int:
    """把 begin/classId 统一成可比较的整数，坏值按 0 处理。"""

    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def assign_missing_lessons(items: list[dict[str, Any]]) -> None:
    if all(item['lesson'] is not None for item in items):
        return
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = item['course'] or item['topic']
        buckets.setdefault(key, []).append(item)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: (_sort_stamp(item.get('begin')),
                                      _sort_stamp(item.get('classId'))))
        used = {item['lesson'] for item in bucket
                if isinstance(item['lesson'], int) and item['lesson'] > 0}
        candidate = 1
        for item in bucket:
            if item['lesson'] is not None:
                continue
            # 只用尚未被显式讲次占用的编号，避免自动编号覆盖真实课次。
            while candidate in used:
                candidate += 1
            item['lesson'] = candidate
            used.add(candidate)
            candidate += 1
```

### 4.3 `homework_export.py`

```python
        data = _list_page(session, mark)
        if not isinstance(data, dict):
            raise RuntimeError('作业列表响应格式异常')
        obj = data.get('obj') or {}
        if not isinstance(obj, dict):
            raise RuntimeError('作业列表响应格式异常')
        rows = obj.get('list') or []
        if not isinstance(rows, list):
            raise RuntimeError('作业列表响应格式异常')
        if not rows:
            break


def fetch_scores(session: ApiSession, zuoye_id: str) -> dict:
    data = session.post(HOMEWORK_SCORES_PATH, {'zuoyeId': zuoye_id})
    if not isinstance(data, dict):
        raise RuntimeError('作业成绩响应格式异常')
    obj = data.get('obj') or {}
    if not isinstance(obj, dict):
        raise RuntimeError('作业成绩响应格式异常')
    students = obj.get('studentZuoyeInfo') or []
    if not isinstance(students, list):
        students = []
    return {'info': obj.get('zuoyeInfo') or {},
            'students': [row for row in students if isinstance(row, dict)]}


def write_csv(path: Path, homework: dict, students: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = score_rows(students)
    lines = [','.join(CSV_HEADER)]
    for row in rows:
        lines.append(','.join(_csv_cell(cell) for cell in row))
    text = '\r\n'.join(lines)
    temp = path.with_name('.%s.tmp%s' % (path.stem, path.suffix))
    try:
        with temp.open('w', encoding='utf-8-sig', newline='') as handle:
            handle.write(text)
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return len(rows)


        # export_homeworks 单条循环内：
        try:
            expected = int(homework.get('zuoyesNum') or 0)
        except (TypeError, ValueError):
            expected = 0
        complete = not expected or len(students) >= expected
        if not complete and logger is not None:
            logger('[!] %s：接口只返回 %d 人，应交 %d 人，可能有学生没取到'
                   % (title, len(students), expected))
        try:
            path = out_root / statistics_filename(homework)
            count = write_csv(path, homework, students)
        except TokenExpired:
            raise
        except Exception as error:  # noqa: BLE001
            message = '%s：%s' % (title, error)
            failed.append({'title': title, 'group': group, 'error': str(error)})
            if logger is not None:
                logger('[%d/%d] [失败] %s' % (index, total, message))
            emit(phase='failed', index=index, done=index, name=title)
            continue
```

### 4.4 `feedback_export.py`

```python
def _num(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    # inf / nan 会让 math.ceil/切片/百分比直接抛异常，按缺失值处理。
    return number if math.isfinite(number) else 0.0


def _int_or(value: Any, default: int = 0) -> int:
    """把接口里可能是字符串/None 的整数安全转成 int。"""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


        # _attend_students / _signin_students 循环内：
        if not isinstance(response, dict):
            raise RuntimeError('签到名单响应格式异常')
        obj = response.get('obj') or {}
        if not isinstance(obj, dict):
            raise RuntimeError('签到名单响应格式异常')
        chunk = obj.get('list') or []
        if not isinstance(chunk, list):
            raise RuntimeError('签到名单响应格式异常')


def _student_detail(session: ApiSession, student: dict, live_info: dict,
                    teacher_id: Any, cancel_event=None) -> dict:
    group_id = str(student.get('groupIds') or '').split(',')[0].strip()
    student_id = _student_id(student)
    if not group_id or not student_id.isdigit():
        return {}
    try:
        begin = int(live_info.get('startTime') or 0)
        end = int(live_info.get('endTime') or 0)
        duration = int(live_info.get('duration') or 0)
        group_value = int(group_id)
        teacher_value = int(teacher_id or 0)
    except (TypeError, ValueError):
        # 接口字段类型异常时只放弃这个学生的聚合数据，不能让整份 CSV 失败。
        return {}
    if not begin:
        return {}
    if not end:
        end = begin + duration * 1000
    payload = {
        'beginTime': begin,
        'endTime': end,
        'groupId': group_value,
        'studentId': int(student_id),
        'teacherId': teacher_value,
    }
    return _optional_obj(
        session, STUDENT_DETAIL_PATH, payload, cancel_event=cancel_event)


        # build_feedback_rows 内：
        class_type = CLASS_TYPE_LABELS.get(
            _int_or(live_info.get('mediaType')), '实时课堂')
```

### 4.5 `banshu_export.py`

```python
MAX_RECORD_PAGES = 200


def fetch_records(session: ApiSession, date_from_ms: int, date_to_ms: int,
                  page_size: int = 50,
                  cancel_event=None) -> list[dict]:
    rows: list[dict] = []
    index = 0
    total = None
    page_size = max(1, int(page_size))
    for _page in range(MAX_RECORD_PAGES):
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消列出板书。')
        resp = session.post(LIST_RECORD, {
            'dateFrom': int(date_from_ms), 'dateTo': int(date_to_ms),
            'indexStart': index, 'pageSize': page_size,
            'groupId': None, 'xx': ''})
        if not isinstance(resp, dict):
            raise RuntimeError('listRecord 响应格式异常')
        code = resp.get('code')
        if not (code in (0, None) or str(code) == '0'):
            raise RuntimeError('listRecord 返回 code=%s' % code)
        obj = resp.get('obj') or {}
        if not isinstance(obj, dict):
            raise RuntimeError('listRecord 响应格式异常')
        chunk = obj.get('list') or []
        if not isinstance(chunk, list):
            raise RuntimeError('listRecord 响应格式异常')
        rows.extend(item for item in chunk if isinstance(item, dict))
        raw_total = obj.get('total')
        if raw_total is not None:
            try:
                total = int(raw_total)
            except (TypeError, ValueError):
                pass
        if len(chunk) < page_size or (total is not None and len(rows) >= total):
            return rows
        index += page_size
    raise RuntimeError('listRecord 分页超过上限（%d 页），已停止以免无限请求'
                       % MAX_RECORD_PAGES)


def bs_manifest(session: ApiSession, file_common_id: str,
                gen_bs_type: int = 0,
                downloader: PageDownloader | None = None,
                cancel_event=None,
                poll_delays: tuple[float, ...] = BS_POLL_DELAYS
                ) -> tuple[dict, str]:
    resp = session.post(GET_BS_SIGN, {'fileCommonId': file_common_id,
                                       'genBsType': gen_bs_type})
    if not isinstance(resp, dict):
        raise BsUnavailable('getBsSign 响应格式异常')
    obj = resp.get('obj') or {}
    code = resp.get('code')
    if (not (code in (0, None) or str(code) == '0')
            or not isinstance(obj, dict) or not obj.get('signature')):
        raise BsUnavailable('getBsSign 失败 code=%s' % code)
    payload = {k: v for k, v in obj.items() if k != 'signature'}
    payload['signature'] = obj['signature']

    last: dict = {}
    gs: dict = {}
    for attempt, delay in enumerate(poll_delays, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消板书导出。')
        if delay:
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    raise BatchCancelled('已取消板书导出。')
            else:
                time.sleep(delay)
        resp = session.post(GEN_BS, payload)
        if not isinstance(resp, dict):
            raise BsUnavailable('genBs 响应格式异常')
        code = resp.get('code')
        if not (code in (0, None) or str(code) == '0'):
            raise BsUnavailable('genBs 失败 code=%s' % code)
        item = resp.get('obj') or {}
        if not isinstance(item, dict):
            raise BsUnavailable('genBs 响应格式异常')
        last = gs = item
        if str(gs.get('status')) == '2':
            break
    else:
        raise BsUnavailable('板书生成中，稍后重试（status=%s）'
                            % last.get('status'))

    url = str(gs.get('url') or '').strip()
    if not url:
        raise BsUnavailable('genBs 未返回板书包地址')
    raw = http_get(url, downloader=downloader,
                   cancel_event=cancel_event)
    try:
        data = gzip.decompress(raw)
    except OSError:
        data = raw
    try:
        manifest = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BsUnavailable('板书清单不是有效 JSON') from error
    if not isinstance(manifest, dict):
        raise BsUnavailable('板书清单格式异常')
    return manifest, str(gs.get('pathprefix') or '').rstrip('/')


def build_pdf(images: list[tuple[bytes, int, int]]) -> tuple[bytes, list[dict]]:
    """兼容旧接口：把 RGB 图像序列写成 PDF。

    历史调用方传 ``(name, raw, width, height)``，新类型标注是
    ``(raw, width, height)``；两种都接受，避免按文档调用时直接解包失败。
    """

    pages = []
    for image in images:
        if len(image) == 4:
            _name, raw, width, height = image
        else:
            raw, width, height = image
        pages.append(PdfPage('', width, height,
                             zlib.compress(raw, PDF_ZLIB_LEVEL)))
    return build_pdf_pages(pages, dedupe=False)


def fallback_class_names(records: list[dict]) -> list[str]:
    # 仅展示 add() 的过滤条件；其余逻辑未改。
    def add(value: Any) -> None:
        ...
        text = str(value or '').strip()
        if (text and text not in names and len(text) <= 80
                and not re.fullmatch(r'[\d\s:_-]+', text)):
            names.append(text)


def collect_pages(manifest: dict) -> list[dict]:
    pages: list[dict] = []
    if not isinstance(manifest, dict):
        return pages
    cmds = manifest.get('cmds') or []
    if not isinstance(cmds, list):
        cmds = []
    for seq, cmd in enumerate(cmds):
        if isinstance(cmd, list) and len(cmd) >= 7 and isinstance(cmd[6], str):
            name = cmd[6]
            if not name.lower().endswith(IMAGE_EXT):
                continue
            stamp = cmd[5] if isinstance(cmd[5], (int, float)) else 0
            pages.append({'kind': 'board', 'time': stamp, 'file': name,
                          'pageno': seq + 1, 'cw': 0, 'seq': seq})

    coursewares = manifest.get('coursewares') or []
    if not isinstance(coursewares, list):
        coursewares = []
    for cw_index, cw in enumerate(coursewares, 1):
        if not isinstance(cw, dict):
            continue
        entries = cw.get('bs') or []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get('path') or ''
            if not isinstance(name, str) or not name.lower().endswith(IMAGE_EXT):
                continue
            stamp = entry.get('BsTime', 0) or 0
            pages.append({'kind': 'annotate', 'time': stamp, 'file': name,
                          'pageno': entry.get('pageno', 0), 'cw': cw_index,
                          'seq': stamp})

    smallboards = manifest.get('smallboards') or []
    if not isinstance(smallboards, list):
        smallboards = []
    for seq, small in enumerate(smallboards):
        # 以下循环体未改
        ...
    return pages
```

`run_board_export` 的排序行：

```python
    records = fetch_records(session, date_from_ms, date_to_ms)
    records.sort(key=record_time)
```

### 4.6 `login.py`

```python
    try:
        connection.request('POST', request_path, body=body, headers=headers)
        response = connection.getresponse()
        status = response.status
        raw = response.read(_MAX_LOGIN_RESPONSE_BYTES + 1)
    except (OSError, http.client.HTTPException) as error:
        raise LoginError('登录请求失败（网络）：%s' % scrub(error)) from error
    finally:
        connection.close()


def _friendly_error(resp: dict[str, Any], password: str) -> LoginError:
    obj = resp.get('obj') or {}
    if not isinstance(obj, dict):
        obj = {}
    message = scrub(resp.get('msg') or '', password)
    code = resp.get('code')
    if not message:
        message = '登录失败（code=%s）' % code
    if message[-1:] not in '。！？':
        message += '。'
    times = obj.get('errLoginTimes')
    if isinstance(times, (int, float)) and times:
        message += '（今日已连续失败 %d 次，次数用尽会暂时锁定）' % int(times)
    if code and str(code) != '0':
        message += '（code=%s）' % code
    return LoginError(message)


def do_login(login_name: str, password: str, mytype: str = 'teacher',
             base_url: str = DEFAULT_BASE_URL) -> LoginResult:
    login_name = (login_name or '').strip()
    password = password or ''
    if not login_name or not password:
        raise LoginError('请输入账号和密码。')

    role = ROLE_STUDENT if mytype == 'student' else ROLE_TEACHER
    payload = build_payload(login_name, transform_passwd(password), role,
                            device_payload())
    payload['role'] = role

    resp = _post_json(base_url, LOGIN_PATH, payload)
    code = resp.get('code')
    obj = resp.get('obj') or {}
    if not isinstance(obj, dict):
        obj = {}
    token = obj.get('access_token') or obj.get('token')

    if not ((code == 0 or code is None) and token):
        raise _friendly_error(resp, password)

    my_org = obj.get('myOrg') or {}
    if not isinstance(my_org, dict):
        my_org = {}
    return LoginResult(
        token=token,
        login_name=login_name,
        name=obj.get('name') or login_name,
        org_id=my_org.get('id') or obj.get('myOrgId'),
        org_name=my_org.get('shortName') or my_org.get('name') or '',
        role=('student' if role == ROLE_STUDENT else 'teacher'),
        raw_keys=sorted(obj.keys()),
    )
```

### 4.7 `pipeline.py`

```python
    def wait(self, cancel_event: threading.Event | None = None,
             deadline: float | None = None) -> None:
        while True:
            with self._lock:
                remaining = self._until - time.monotonic()
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    return
                # 冷却不能让单个课堂越过自己的总截止时间。
                remaining = min(remaining, left)
            if remaining <= 0:
                return
            if cancel_event is not None:
                if cancel_event.wait(min(remaining, 0.5)):
                    return
            else:
                time.sleep(min(remaining, 0.5))


        # _fetch_one 内：
        if cooldown is not None:
            cooldown.wait(cancel_event, deadline=deadline)
            if cancel_event.is_set():
                note = '已取消'
                break


        # run_export 内：
        csv_path = json_path = None
        if options.write_csv:
            try:
                csv_path = _write_csv(path.with_suffix('.csv'), public_results)
            except (OSError, TypeError, ValueError) as error:
                _emit(on_event, 'log', 'CSV 写入失败：%s' % redact(error))
        if options.write_json:
            try:
                json_path = _write_json(path.with_suffix('.json'), public_results)
            except (OSError, TypeError, ValueError) as error:
                _emit(on_event, 'log', 'JSON 写入失败：%s' % redact(error))


def _write_csv(path: Path, results: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.%s.tmp%s' % (path.stem, path.suffix))
    try:
        with temp.open('w', encoding='utf-8-sig', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['上课时间', '课堂名称', '课堂ID', '课程', '课次',
                             '邀请码', '状态', '备注'])
            for item in results:
                writer.writerow([
                    item.get('date'), item.get('topic'), item.get('classId'),
                    item.get('course'), item.get('lesson'), item.get('code'),
                    '成功' if item.get('code') else '失败', item.get('note'),
                ])
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return path


def _write_json(path: Path, results: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.%s.tmp%s' % (path.stem, path.suffix))
    try:
        temp.write_text(json.dumps({
            'exportedAt': datetime.now().isoformat(timespec='seconds'),
            'items': results,
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return path


def _write_diagnostics(path: Path, payload: dict[str, Any]) -> Path:
    target = path.with_name(path.stem + '-诊断.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name('.%s.tmp%s' % (target.stem, target.suffix))
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding='utf-8')
        os.replace(temp, target)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return target
```

### 4.8 `gui.py`

```python
def _fmt_clock(seconds: float) -> str:
    """把秒数排成 00:42 / 1:02:03 的等宽格式。"""

    try:
        value = max(0, int(seconds or 0))
    except (TypeError, ValueError, OverflowError):
        value = 0
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return '%d:%02d:%02d' % (hours, minutes, secs)
    return '%02d:%02d' % (minutes, secs)


    def _board_work(self, records: list[dict], sort_mode: str,
                    out_root: Path) -> None:
        try:
            out_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            # 保存目录不可创建（权限/同名文件/磁盘满）时必须回报界面，
            # 否则工作线程静默退出，按钮会一直停在「正在导出」。
            self._post('error', '无法创建保存目录：%s' % error)
            return
        for attempt in range(2):    # token 失效自动重取一次
            ...
```

### 4.9 `token_macos.py`

```python
def _bundle_executable(app: Path) -> Path | None:
    macos = app / 'Contents' / 'MacOS'
    if not macos.is_dir():
        return None
    try:
        plist_path = app / 'Contents' / 'Info.plist'
        with plist_path.open('rb') as handle:
            plist = plistlib.load(handle)
        name = plist.get('CFBundleExecutable')
        if name:
            candidate = macos / name
            if candidate.is_file():
                return candidate
    except (OSError, plistlib.InvalidFileException, ValueError):
        pass
    try:
        for candidate in macos.iterdir():
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    except OSError:
        return None
    return None


class CDPClient:
    def __init__(self, ws_url: str, timeout: float = 20.0) -> None:
        parsed = urlparse(ws_url)
        if parsed.scheme != 'ws' or not parsed.hostname:
            raise CDPError('无效的 WebSocket 地址：%s' % ws_url)
        self._timeout = timeout
        self._socket = socket.create_connection(
            (parsed.hostname, parsed.port or 80), timeout=timeout)
        self._socket.settimeout(timeout)
        self._next_id = 0
        self._lock = threading.Lock()
        self._responses: dict[int, dict[str, Any]] = {}
        self._condition = threading.Condition()
        self._closed = False
        try:
            self._handshake(parsed.hostname, parsed.port or 80, parsed.path)
        except BaseException:
            # 握手失败时构造函数不会返回，调用方拿不到 client 来 close()；
            # 在这里关掉 socket，避免每次失败泄漏一个连接。
            try:
                self._socket.close()
            except OSError:
                pass
            raise
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # _read_loop 的异常捕获新增 UnicodeDecodeError
        except (OSError, CDPError, json.JSONDecodeError,
                UnicodeDecodeError):
            pass
```

### 4.10 `update_checker.py` / `update_installer.py`

```python
# update_checker.py
class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """只允许 HTTPS 重定向；避免先连到 http:// 再事后拒绝。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme.lower() != 'https':
            raise UpdateError('更新地址被重定向到非 HTTPS 地址，已拒绝。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_update_payload(url: str, timeout: float = REQUEST_TIMEOUT) -> str:
    request = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'application/json, text/plain, */*',
        'Cache-Control': 'no-cache',
    })
    try:
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ssl_context()),
            _HttpsOnlyRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            data = response.read(2 * 1024 * 1024 + 1)
    except UpdateError:
        raise
    except Exception as error:  # noqa: BLE001
        raise UpdateError('无法连接更新服务器：%s' % error) from error
    if len(data) > 2 * 1024 * 1024:
        raise UpdateError('更新接口返回内容过大。')
    return data.decode('utf-8', errors='replace')


# update_installer.py
class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """只允许 HTTPS 重定向；避免更新包先被带到明文 HTTP。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme.lower() != 'https':
            raise UpdateInstallError('更新地址被重定向到非 HTTPS 地址。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url: str, timeout: float):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != 'https' or not parsed.hostname:
        raise UpdateInstallError('更新地址必须是 HTTPS。')
    if parsed.username or parsed.password:
        raise UpdateInstallError('更新地址不能包含账号密码。')
    request = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'application/octet-stream, application/json, */*',
        'Cache-Control': 'no-cache',
    })
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=_ssl_context()),
        _HttpsOnlyRedirectHandler())
    return opener.open(request, timeout=timeout)
```

### 4.11 `net.py` 与 `theme_art.py`

```python
# net.py
    def fetch_live_info(self, ids: list[int],
                        chunk_size: int = 50) -> dict[int, dict[str, Any]]:
        details: dict[int, dict[str, Any]] = {}
        chunk_size = max(1, int(chunk_size))
        for start in range(0, len(ids), chunk_size):
            ...


# theme_art.py
    try:
        with Image.open(user_asset_path(name)) as source:
            return source.convert('RGBA')
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
```

---

## 5. 待确认项（实现方法与预期效果可能不符）

以下项目**不是**本次已确认的 bug；其中标注“不确定”的，因缺少真实服务端/真实 macOS/真实客户端环境，无法稳定复现，按用户要求不据此判定无 bug。请用户逐项决策。

| 编号 | 事项 | 依据/风险 | 需要用户确认 |
| --- | --- | --- | --- |
| Q-01 | 作业统计 CSV 的「总积分」列始终为空 | `homework_export.statistic_row` 第 11 列固定 `''`；既有测试也断言空。平台真实导出是否为空未验证 | 是否应从 `student.star`/其他字段填充？ |
| Q-02 | macOS 账号登录的设备信息 | `login.device_payload` 的 `osInfo` 固定拼 `Windows_NT`，`_stable_device_id` 生成 `windows-` 前缀 | macOS 服务端是否接受？是否应改成平台真实信息？ |
| Q-03 | 更新页版本兜底正则 | 无版本徽标时会取 HTML 中第一个 `\d+\.\d+\.\d+`，可能命中 CSS/库版本 | 是否要求下载页必须提供 `version`/徽标，否则视为检查失败？ |
| Q-04 | 板书 CLI 未找到课堂时的返回值 | `banshu_export.main` 找不到 `--liveclass` 时用兜底 record 继续，缺 fileId 时 `export_class` 返回 None，但 `main` 仍返回 0 | 是否应返回非 0 并提示“未找到课堂”？ |
| Q-05 | `class_deadline=0` 的语义 | `_fetch_one` 用 `class_deadline or DEFAULT_CLASS_DEADLINE`，0 会变成默认 22 秒 | 0 应表示“立即超时”还是“使用默认”？ |
| Q-06 | PDF 写入原子性 | `export_class` 直接 `pdf_path.write_bytes(pdf_bytes)`，磁盘满/中断可能留半截 PDF | 是否需要临时文件 + 原子替换？ |
| Q-07 | 反馈包部分写入 | `export_feedback_bundles` 逐项写 CSV，后一项失败时前面的文件已落盘 | 是否要求整包事务（失败则清理已写文件）？ |
| Q-08 | 缺失时间的作业是否算在时间段内 | `homework_export._in_range` 对 `stamp=0` 返回 True | 是否应排除无时间作业？ |
| Q-09 | “全部”时间段只回溯 10 年 | `resolve_range('全部')` 使用 `now - 10*365 天`；客户端实际数据可能更久 | 10 年是否足够？ |
| Q-10 | 中文课次 110 的写法 | `chinese_number(110)` 返回“一百十”，标准写法通常“一百一十”；课次一般 <100，当前不影响 | 是否需要修正 100 以上写法？ |
| Q-11 | 反馈时间固定 UTC+8 | `_fmt_time` 使用固定 `+08:00`，不受运行机器时区影响 | 是否只面向中国时区用户？ |
| Q-12 | 单课 PDF 复用判定 | 仅按文件大小 + 内容比对，同名不同课可能被覆盖/复用 | 是否应把课堂 ID 纳入文件名或复用键？ |
| Q-13 | 更新器替换脚本的 90 秒等待 | 进程退出慢于 90 秒时只提示“无法替换”，未无限等待 | 90 秒是否合适？ |
| Q-14 | 不确定：真实服务端分页/字段类型 | 本次用 fake 验证了 `obj` 非 dict、`total` 异常等，但真实服务端是否会出现未知 | 需要真实账号回归；未复现前不判定无 bug |
| Q-15 | 不确定：macOS 重启/恢复与 Gatekeeper | 代码路径已静态审查 + 单测 mock，未在真实 macOS 15 上执行 | 需要 macOS 真机回归 |
| Q-16 | 不确定：Windows 内存扫描 token 格式变化 | 只测了正则/窗口提取；真实客户端升级后的格式未验证 | 需要真实客户端回归；扫描失败提示已覆盖 |

---

## 附：本次新增/修改文件

- 新增：`tests/test_whitebox_audit.py`（104 条用例）
- 新增：`docs/whitebox_function_inventory.csv`（592 个函数/方法的静态清单）
- 修改：`config.py`、`matrix.py`、`homework_export.py`、`feedback_export.py`、`banshu_export.py`、`login.py`、`pipeline.py`、`gui.py`、`token_macos.py`、`update_checker.py`、`update_installer.py`、`theme_art.py`、`net.py`
- 未改：`requirements.txt`（无新增依赖）、接口路径、CSV 表头/列序、邀请码/板书/反馈业务字段

最终验证命令：

```bat
python -m unittest discover -s tests -v
cmd /c "build.bat < nul"
```

构建产物：`dist\爱问云集成工具.exe`（如构建成功，路径以实际为准）。
