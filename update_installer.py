"""Windows exe 一键更新：下载、校验、替换、重启，不保留旧版本备份。

服务器只需提供两个 HTTPS 地址：Windows 单文件 exe 和对应的 .sha256。
替换由独立 PowerShell 进程完成，避免正在运行的 exe 无法覆盖自身。
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from net import _ssl_context

USER_AGENT = 'aiwenyun-tools-updater/1.0'
DOWNLOAD_TIMEOUT = 60.0
MAX_PACKAGE_BYTES = 200 * 1024 * 1024
MAX_CHECKSUM_BYTES = 64 * 1024
_OLD_SUFFIX = '.old-'

ProgressCallback = Callable[[int, int], None]


class UpdateInstallError(RuntimeError):
    """更新包下载、校验或替换失败。"""


def parse_sha256_text(text: str) -> str:
    """从标准 sha256sum 输出或带标签的文本里取 64 位十六进制摘要。"""

    match = re.search(r'\b([0-9a-fA-F]{64})\b', str(text or ''))
    if not match:
        raise UpdateInstallError('校验文件里没有有效的 SHA-256。')
    return match.group(1).lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def is_pe_executable(path: Path) -> bool:
    """检查 MZ / PE 头，防止把 HTML 错误页当成 exe 安装。"""

    try:
        with Path(path).open('rb') as handle:
            if handle.read(2) != b'MZ':
                return False
            handle.seek(0x3C)
            raw_offset = handle.read(4)
            if len(raw_offset) != 4:
                return False
            offset = int.from_bytes(raw_offset, 'little')
            if offset < 0x40 or offset > 16 * 1024 * 1024:
                return False
            handle.seek(offset)
            return handle.read(4) == b'PE\0\0'
    except OSError:
        return False


def is_windows_exe_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(str(url or '').strip())
    if parsed.scheme.lower() != 'https' or not parsed.hostname:
        return False
    if parsed.username or parsed.password:
        return False
    return parsed.path.lower().endswith('.exe')


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
    return urllib.request.urlopen(
        request, timeout=timeout, context=_ssl_context())


def download_file(url: str, dest: Path,
                  progress: ProgressCallback | None = None,
                  timeout: float = DOWNLOAD_TIMEOUT,
                  max_bytes: int = MAX_PACKAGE_BYTES) -> Path:
    """流式下载到 dest，限制大小并拒绝重定向到非 HTTPS。"""

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + '.part')
    try:
        with _open_url(url, timeout) as response:
            final_url = response.geturl()
            if not str(final_url).lower().startswith('https://'):
                raise UpdateInstallError('更新下载被重定向到非 HTTPS 地址。')
            length_text = response.headers.get('Content-Length') or ''
            try:
                expected = int(length_text)
            except (TypeError, ValueError):
                expected = 0
            if expected > max_bytes:
                raise UpdateInstallError('更新包超过大小限制。')
            total = 0
            with part.open('wb') as handle:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise UpdateInstallError('更新包超过大小限制。')
                    handle.write(chunk)
                    if progress is not None:
                        progress(total, expected)
            if expected and total != expected:
                raise UpdateInstallError('更新包下载不完整。')
        part.replace(dest)
        if progress is not None:
            progress(total, expected or total)
        return dest
    except UpdateInstallError:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    except Exception as error:  # noqa: BLE001  网络错误统一给界面提示
        try:
            part.unlink()
        except OSError:
            pass
        raise UpdateInstallError('下载更新包失败：%s' % error) from error


def fetch_sha256(url: str, timeout: float = 10.0) -> str:
    try:
        with _open_url(url, timeout) as response:
            if not str(response.geturl()).lower().startswith('https://'):
                raise UpdateInstallError('校验文件被重定向到非 HTTPS 地址。')
            data = response.read(MAX_CHECKSUM_BYTES + 1)
    except UpdateInstallError:
        raise
    except Exception as error:  # noqa: BLE001
        raise UpdateInstallError('读取 SHA-256 失败：%s' % error) from error
    if len(data) > MAX_CHECKSUM_BYTES:
        raise UpdateInstallError('校验文件过大。')
    return parse_sha256_text(data.decode('utf-8', errors='replace'))


def is_frozen_windows_exe() -> bool:
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return False
    executable = Path(sys.executable)
    return executable.suffix.lower() == '.exe' and executable.is_file()


def can_auto_update(result: dict, frozen: bool | None = None,
                    platform: str | None = None) -> bool:
    """只有打包后的 Windows exe、且服务器给出 exe + 校验值时才自动替换。"""

    if not isinstance(result, dict):
        return False
    platform = sys.platform if platform is None else platform
    frozen = is_frozen_windows_exe() if frozen is None else bool(frozen)
    if platform != 'win32' or not frozen:
        return False
    if not is_windows_exe_url(result.get('download_url')):
        return False
    return bool(result.get('sha256') or result.get('checksum_url'))


def prepare_update(result: dict, work_dir: Path | None = None,
                   progress: ProgressCallback | None = None) -> Path:
    """下载并校验更新包；失败时清掉临时文件，不留下半成品。"""

    url = str(result.get('download_url') or '').strip()
    if not is_windows_exe_url(url):
        raise UpdateInstallError('服务器没有提供 HTTPS Windows exe 更新包。')
    checksum = str(result.get('sha256') or '').strip()
    checksum_url = str(result.get('checksum_url') or '').strip()
    if not checksum and not checksum_url:
        raise UpdateInstallError('服务器没有提供 SHA-256 校验值，已取消自动更新。')

    own_work = work_dir is None
    work = (Path(work_dir) if work_dir is not None
            else Path(tempfile.mkdtemp(prefix='aiwenyun-update-')))
    package = work / 'update.exe'
    try:
        work.mkdir(parents=True, exist_ok=True)
        download_file(url, package, progress=progress)
        if not checksum:
            checksum = fetch_sha256(checksum_url)
        expected = parse_sha256_text(checksum)
        actual = sha256_file(package)
        if actual != expected:
            raise UpdateInstallError('更新包 SHA-256 校验失败。')
        if not is_pe_executable(package):
            raise UpdateInstallError('更新包不是有效的 Windows exe。')
        return package
    except Exception:
        if own_work:
            shutil.rmtree(work, ignore_errors=True)
        else:
            for leftover in (package, package.with_name(package.name + '.part')):
                try:
                    leftover.unlink()
                except OSError:
                    pass
        raise


def _ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_updater_script(target: Path, package: Path, pid: int,
                         parent_pid: int = 0, expected_version: str = '',
                         log_path: Path | None = None) -> str:
    """生成等待主进程退出、探测版本、替换并重启的 PowerShell 脚本。"""

    target = Path(target)
    package = Path(package)
    log_path = Path(log_path) if log_path is not None else (
        Path(tempfile.gettempdir()) / 'aiwenyun-update.log')
    old = target.with_name(
        '%s%s%d-%d' % (target.name, _OLD_SUFFIX, int(pid), time.time_ns()))
    pids = [int(pid)]
    if parent_pid and int(parent_pid) > 0 and int(parent_pid) != int(pid):
        pids.append(int(parent_pid))
    pids_text = ', '.join(str(value) for value in pids)
    return """$ErrorActionPreference = 'Stop'
$target = %(target)s
$package = %(package)s
$old = %(old)s
$log = %(log)s
$expected = %(expected)s
$pids = @(%(pids)s)

function Write-Log([string]$message) {
    try {
        $line = (Get-Date).ToString('s') + ' ' + $message
        Add-Content -LiteralPath $log -Value $line -Encoding UTF8
    } catch {
    }
}

try {
    $deadline = (Get-Date).AddSeconds(90)
    foreach ($processId in $pids) {
        if ($processId -le 0) { continue }
        while ((Get-Process -Id $processId -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {
            Start-Sleep -Milliseconds 200
        }
    }
    Start-Sleep -Milliseconds 300

    $probe = Join-Path $env:TEMP ('aiwenyun-probe-' + [guid]::NewGuid().ToString('N') + '.txt')
    $probeArgs = '--version-file "' + $probe + '"'
    $probeProc = Start-Process -FilePath $package -ArgumentList $probeArgs -PassThru -WindowStyle Hidden
    if (-not $probeProc.WaitForExit(30000)) {
        try { $probeProc.Kill() } catch {}
        throw '更新包版本检查超时。'
    }
    $actual = ''
    if (Test-Path -LiteralPath $probe) {
        $actual = (Get-Content -LiteralPath $probe -Raw -ErrorAction SilentlyContinue).Trim()
    }
    Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
    if ($expected -and $actual -ne $expected) {
        throw ('更新包版本不匹配：期望 ' + $expected + '，实际 ' + $actual)
    }

    $moved = $false
    for ($i = 0; $i -lt 60; $i++) {
        try {
            Move-Item -LiteralPath $target -Destination $old -Force -ErrorAction Stop
            $moved = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $moved) {
        throw '无法替换正在运行的程序文件。'
    }

    try {
        Move-Item -LiteralPath $package -Destination $target -Force -ErrorAction Stop
    } catch {
        Move-Item -LiteralPath $old -Destination $target -Force -ErrorAction SilentlyContinue
        throw
    }

    $started = Start-Process -FilePath $target -WorkingDirectory (Split-Path -Parent $target) -PassThru
    Start-Sleep -Seconds 3
    if ($started.HasExited) {
        throw ('新版本启动失败，退出码 ' + $started.ExitCode)
    }

    for ($i = 0; $i -lt 20; $i++) {
        Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path -LiteralPath $old)) { break }
        Start-Sleep -Milliseconds 500
    }
    if (Test-Path -LiteralPath $old) {
        Write-Log '旧版本文件删除失败，下次启动会再次清理。'
    } else {
        Write-Log ('更新完成：' + $expected)
    }
    $work = Split-Path -Parent $package
    if ((Split-Path -Leaf $work) -like 'aiwenyun-update-*') {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    exit 0
} catch {
    $message = $_.Exception.Message
    Write-Log ('更新失败：' + $message)
    if (Test-Path -LiteralPath $old) {
        Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
        Move-Item -LiteralPath $old -Destination $target -Force -ErrorAction SilentlyContinue
    }
    $work = Split-Path -Parent $package
    if ((Split-Path -Leaf $work) -like 'aiwenyun-update-*') {
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            ('自动更新失败：' + $message + "`n`n旧版本已恢复。`n日志：" + $log),
            '爱问云集成工具', 'OK', 'Error') | Out-Null
    } catch {
    }
    exit 1
}
""" % {
        'target': _ps_quote(target),
        'package': _ps_quote(package),
        'old': _ps_quote(old),
        'log': _ps_quote(log_path),
        'expected': _ps_quote(expected_version),
        'pids': pids_text,
    }


def _powershell_path() -> str:
    system_root = os.environ.get('SystemRoot', r'C:\Windows')
    candidate = (Path(system_root) / 'System32' / 'WindowsPowerShell'
                 / 'v1.0' / 'powershell.exe')
    if candidate.is_file():
        return str(candidate)
    found = shutil.which('powershell.exe') or shutil.which('powershell')
    if not found:
        raise UpdateInstallError('找不到 PowerShell，无法安装更新。')
    return found


def launch_updater(package: Path, target: Path | None = None,
                   pid: int | None = None, parent_pid: int | None = None,
                   expected_version: str = '',
                   log_path: Path | None = None) -> subprocess.Popen:
    """启动独立更新进程，然后由调用方退出当前程序。"""

    if sys.platform != 'win32':
        raise UpdateInstallError('一键更新只支持 Windows exe。')
    package = Path(package)
    target = Path(target) if target is not None else Path(sys.executable)
    if target.suffix.lower() != '.exe' or not target.is_file():
        raise UpdateInstallError('当前程序不是可替换的 Windows exe。')
    if not package.is_file():
        raise UpdateInstallError('更新包不存在。')
    pid = os.getpid() if pid is None else int(pid)
    parent_pid = os.getppid() if parent_pid is None else int(parent_pid)
    script = build_updater_script(
        target, package, pid, parent_pid=parent_pid,
        expected_version=expected_version, log_path=log_path)
    encoded = base64.b64encode(
        script.encode('utf-16-le')).decode('ascii')
    # DETACHED_PROCESS 会让 Windows PowerShell 直接退出、不执行脚本；
    # CREATE_NO_WINDOW 负责隐藏窗口，NEW_PROCESS_GROUP 让更新器不受
    # 主程序退出时的控制台事件影响。
    flags = 0x08000000 | 0x00000200
    try:
        return subprocess.Popen(
            [_powershell_path(), '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
            close_fds=True, creationflags=flags)
    except OSError as error:
        raise UpdateInstallError('启动更新进程失败：%s' % error) from error


def cleanup_stale_update_files(target: Path | None = None,
                               max_age_seconds: float = 24 * 3600
                               ) -> list[Path]:
    """清理上次更新残留的旧 exe；目标 exe 不存在时绝不删除。"""

    if target is None:
        if not getattr(sys, 'frozen', False):
            return []
        target = Path(sys.executable)
    target = Path(target)
    removed: list[Path] = []
    if not target.is_file():
        return removed
    now = time.time()
    for path in target.parent.glob(target.name + _OLD_SUFFIX + '*'):
        try:
            if (path.is_file()
                    and now - path.stat().st_mtime >= max_age_seconds):
                path.unlink()
                removed.append(path)
        except OSError:
            pass
    for path in Path(tempfile.gettempdir()).glob('aiwenyun-update-*'):
        try:
            if (path.is_dir()
                    and now - path.stat().st_mtime >= max_age_seconds):
                shutil.rmtree(path, ignore_errors=True)
                removed.append(path)
        except OSError:
            pass
    return removed
