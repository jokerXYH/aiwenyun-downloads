# 项目约定

## 每次任务完成后打包

- 每次完成代码修改或任务后，必须重新运行项目现有打包流程：
  `cmd /c "build.bat < nul"`（等价于 `build.bat` 里的 PyInstaller 命令）。
- 打包产物：`dist\爱问云集成工具.exe`。
- 在最终回复中明确说明是否已打包，并给出 exe 的完整路径；如果打包失败，
  说明失败原因，不要只报告源码修改完成。

## 发布

- 版本号只改 `config.py` 的 `APP_VERSION`；tag 必须为 `v${APP_VERSION}`。
- 完整发布流程见 [`docs/RELEASE.md`](docs/RELEASE.md)：先让
  `build-release.yml` 成功，再更新 jokerx.top 页面并运行
  `mirror-aiwenyun.yml`；不能只看 push 成功。
- macOS 构建使用 `build_macos.command`，版本从 `config.py` 读取，不再写死。
