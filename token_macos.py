"""macOS：重启爱问云并带本机 9222 调试端口，通过 CDP 读取登录态。

macOS 对跨进程内存读取限制很严（SIP / task_for_pid / hardened runtime），
因此 macOS 版采用与原始 Windows 工具相同的 CDP 方案：

    退出爱问云 → 用 open -a 带 --remote-debugging-address=127.0.0.1
    --remote-debugging-port=9222 重新打开 → Runtime.evaluate 读取
    window.Plaso.userInfo.access_token → 关闭 CDP 连接
    → 再正常打开一次，避免客户端停留在白屏窗口

不会把调试端口暴露到局域网；重启前必须得到用户确认。
"""

from __future__ import annotations

import base64
import json
import os
import plistlib
import socket
import struct
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from config import normalize_base_url
from token_model import TokenBatch, TokenCandidate, TokenScanError, parse_token_metadata

APP_NAMES = ('AiCloud.app', '爱问云.app', 'ai-yxt.app', 'AiWenYun.app')
DEBUG_PORT = 9222
CONTEXT_EXPRESSION = """
(() => {
  const plaso = window.Plaso || {};
  const user = plaso.userInfo || {};
  const app = plaso.appinfo || {};
  return JSON.stringify({
    dhost: app.dhost || location.origin || '',
    accessToken: user.access_token || '',
    requestId: user.plaso_requestid || '',
    version: app.version || '',
    platform: app.oemName || '',
    origin: location.origin || '',
    referer: (location.href || '').split('#')[0]
  });
})()
"""


class MacTokenError(TokenScanError):
    pass


def _app_candidates() -> list[Path]:
    roots = [Path('/Applications'), Path.home() / 'Applications']
    results = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.glob('*.app'):
            name = path.name.lower()
            if any(token in name for token in ('aicloud', '爱问云', 'ai-yxt', 'aiwenyun')):
                results.append(path)
    for name in APP_NAMES:
        path = Path('/Applications') / name
        if path.is_dir() and path not in results:
            results.append(path)
    return results


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
    for candidate in macos.iterdir():
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def find_app() -> Path | None:
    for app in _app_candidates():
        if (app / 'Contents' / 'MacOS').is_dir():
            return app
    return None


def _process_lines() -> list[tuple[int, str]]:
    try:
        output = subprocess.check_output(
            ['ps', '-axo', 'pid=,command='], text=True, errors='replace', timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    result = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        try:
            result.append((int(parts[0]), parts[1]))
        except ValueError:
            continue
    return result


def running_processes(app: Path | None = None) -> list[tuple[int, str]]:
    hints = ['ai-yxt', 'aicloud', '爱问云', 'aiwenyun']
    if app is not None:
        hints.append(str(app))
    found = []
    for pid, command in _process_lines():
        lowered = command.lower()
        if any(hint.lower() in lowered for hint in hints):
            # 排除本工具自己的 python/进程
            if 'python' in lowered and 'ai-yxt' not in lowered:
                continue
            found.append((pid, command))
    return found


def client_running() -> bool:
    return bool(running_processes(find_app()))


def debug_port_open(host: str = '127.0.0.1', port: int = DEBUG_PORT,
                    timeout: float = 1.2) -> bool:
    try:
        with urllib.request.urlopen(
                'http://%s:%d/json/version' % (host, port), timeout=timeout):
            return True
    except OSError:
        return False


def quit_app(app: Path, cancel_event: threading.Event | None = None) -> None:
    hints = [str(app), app.name]
    executable = _bundle_executable(app)
    if executable is not None:
        hints.append(str(executable))
    for hint in dict.fromkeys(hints):
        try:
            subprocess.run(['pkill', '-TERM', '-f', hint], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            return
        if not running_processes(app):
            return
        time.sleep(0.25)
    for hint in dict.fromkeys(hints):
        try:
            subprocess.run(['pkill', '-KILL', '-f', hint], timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
    time.sleep(0.5)


def launch_app(app: Path, port: int = DEBUG_PORT) -> None:
    executable = _bundle_executable(app)
    if executable is None:
        raise MacTokenError('没有找到爱问云可执行文件：%s' % app)
    # 等 Electron 的 helper 进程完全退出，避免新实例拿到旧的单例锁/缓存后白屏。
    time.sleep(0.8)
    # 必须通过 LaunchServices 的 open -a 启动；直接执行 Contents/MacOS 下的
    # 二进制会让部分 Electron 客户端窗口白屏。
    result = subprocess.run(
        ['open', '-a', str(app), '--args',
         '--remote-debugging-address=127.0.0.1',
         '--remote-debugging-port=%d' % port],
        capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise MacTokenError('启动爱问云失败：%s' % (detail or 'open 返回 %d' % result.returncode))


def restore_app(app: Path) -> None:
    """关闭调试实例，按正常方式重新打开，避免用户看到白屏窗口。"""
    quit_app(app)
    subprocess.run(
        ['open', '-a', str(app)],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class CDPError(RuntimeError):
    pass


def list_targets(host: str = '127.0.0.1', port: int = DEBUG_PORT) -> list[dict[str, Any]]:
    url = 'http://%s:%d/json/list' % (host, port)
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            data = json.load(response)
    except OSError as error:
        raise CDPError('无法连接本机调试接口 %s:%d：%s' % (host, port, error)) from error
    return [item for item in data if item.get('webSocketDebuggerUrl')]


class CDPClient:
    """最小 WebSocket CDP 客户端（标准库实现）。"""

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
        self._handshake(parsed.hostname, parsed.port or 80, parsed.path)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            'GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n'
            'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
            'Sec-WebSocket-Version: 13\r\n\r\n' % (path, host, port, key))
        self._socket.sendall(request.encode())
        response = b''
        while b'\r\n\r\n' not in response:
            chunk = self._socket.recv(4096)
            if not chunk:
                raise CDPError('调试接口在握手阶段断开')
            response += chunk
        if b' 101 ' not in response.split(b'\r\n', 1)[0]:
            raise CDPError('调试接口握手失败')

    def _send(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        mask = os.urandom(4)
        masked = bytes(value ^ mask[index % 4]
                       for index, value in enumerate(data))
        length = len(masked)
        if length < 126:
            header = bytes([129, 128 | length])
        elif length < 65536:
            header = bytes([129, 254]) + struct.pack('!H', length)
        else:
            header = bytes([129, 255]) + struct.pack('!Q', length)
        with self._lock:
            self._socket.sendall(header + mask + masked)

    def _recv_exact(self, size: int) -> bytes:
        data = b''
        while len(data) < size:
            chunk = self._socket.recv(size - len(data))
            if not chunk:
                raise CDPError('调试连接已关闭')
            data += chunk
        return data

    def _read_frame(self) -> dict[str, Any] | None:
        header = self._recv_exact(2)
        opcode = header[0] & 15
        masked = bool(header[1] & 128)
        length = header[1] & 127
        if length == 126:
            length = struct.unpack('!H', self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack('!Q', self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b''
        data = self._recv_exact(length)
        if masked:
            data = bytes(value ^ mask[index % 4]
                         for index, value in enumerate(data))
        if opcode == 8:
            return None
        if opcode != 1:
            return {}
        return json.loads(data.decode('utf-8'))

    def _read_loop(self) -> None:
        try:
            while not self._closed:
                message = self._read_frame()
                if message is None:
                    break
                if not message:
                    continue
                message_id = message.get('id')
                if isinstance(message_id, int):
                    with self._condition:
                        self._responses[message_id] = message
                        self._condition.notify_all()
                    continue
        except (OSError, CDPError, json.JSONDecodeError):
            pass
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._condition:
            self._next_id += 1
            message_id = self._next_id
        self._send({'id': message_id, 'method': method, 'params': params or {}})
        deadline = time.monotonic() + self._timeout
        with self._condition:
            while message_id not in self._responses:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._closed:
                    raise CDPError('调试调用超时：%s' % method)
                self._condition.wait(min(remaining, 0.2))
            response = self._responses.pop(message_id)
        if 'error' in response:
            raise CDPError('%s 失败：%s' % (
                method, response['error'].get('message')))
        return response.get('result', {})

    def evaluate(self, expression: str) -> Any:
        result = self.call('Runtime.evaluate', {
            'expression': expression,
            'returnByValue': True,
            'awaitPromise': True,
        })
        if result.get('exceptionDetails'):
            raise CDPError(result['exceptionDetails'].get('text', '页面脚本执行失败'))
        return result.get('result', {}).get('value')

    def close(self) -> None:
        self._closed = True
        try:
            self._socket.close()
        except OSError:
            pass


def read_context(host: str = '127.0.0.1', port: int = DEBUG_PORT) -> dict[str, Any]:
    targets = list_targets(host, port)
    pages = [item for item in targets if item.get('type') == 'page']
    if not pages:
        raise MacTokenError('没有找到爱问云页面，请先打开客户端窗口')
    pages.sort(key=lambda item: 0 if 'index.html' in item.get('url', '') else 1)
    last_error: Exception | None = None
    for target in pages:
        client = None
        try:
            client = CDPClient(target['webSocketDebuggerUrl'])
            payload = json.loads(client.evaluate(CONTEXT_EXPRESSION))
        except (CDPError, json.JSONDecodeError, TypeError) as error:
            last_error = error
            continue
        finally:
            if client is not None:
                client.close()
        token = str(payload.get('accessToken') or '').strip()
        base_url = normalize_base_url(str(payload.get('dhost') or ''))
        if token and token.lower() != 'previewtoken' and base_url:
            return {
                'base_url': base_url,
                'token': token,
                'version': str(payload.get('version') or ''),
                'platform': str(payload.get('platform') or 'ai'),
                'origin': str(payload.get('origin') or ''),
                'referer': str(payload.get('referer') or ''),
            }
        last_error = MacTokenError('页面登录态尚未就绪')
    raise MacTokenError('读取登录态失败：%s' % (last_error or '未知错误'))


def _wait_for_port(port: int, timeout: float,
                   cancel_event: threading.Event | None = None) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            return False
        if debug_port_open(port=port):
            return True
        time.sleep(0.5)
    return False


def _wait_for_context(port: int, timeout: float,
                      cancel_event: threading.Event | None = None) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            raise MacTokenError('已取消')
        try:
            return read_context(port=port)
        except (MacTokenError, CDPError) as error:
            last_error = error
            time.sleep(2)
    raise MacTokenError('等待登录超时：%s' % (last_error or '登录态未就绪'))


def acquire_tokens(scan_timeout: float = 8.0, allow_restart: bool = False,
                   on_event: Callable[[str, Any], None] | None = None,
                   cancel_event: threading.Event | None = None) -> TokenBatch:
    app = find_app()
    if app is None:
        raise MacTokenError('没有找到爱问云 App（/Applications/AiCloud.app 等）')
    if on_event:
        on_event('log', '找到客户端：%s' % app)

    if debug_port_open():
        if on_event:
            on_event('log', '检测到已存在的 9222 调试端口，直接读取登录态')
        context = _wait_for_context(DEBUG_PORT, 20, cancel_event)
        restarted = False
    else:
        if not allow_restart:
            raise MacTokenError(
                'macOS 版需要重启爱问云并开启本机 9222 调试端口。'
                '请确认重启后重试，或使用 --allow-restart。')
        if on_event:
            on_event('phase', '正在重启爱问云并开启本机调试端口…')
        quit_app(app, cancel_event)
        if cancel_event is not None and cancel_event.is_set():
            raise MacTokenError('已取消')
        launch_app(app)
        restarted = True
        try:
            if not _wait_for_port(DEBUG_PORT, 60, cancel_event):
                raise MacTokenError('重启后没能连上调试端口，请手动打开爱问云后重试')
            if on_event:
                on_event('log', '客户端已启动，等待登录态…')
            context = _wait_for_context(DEBUG_PORT, 150, cancel_event)
        except Exception:
            # 启动/读取失败时不要把用户留在白屏的调试实例里。
            try:
                restore_app(app)
            except Exception:
                pass
            raise

    token = context['token']
    metadata = parse_token_metadata(token)
    candidate = TokenCandidate(
        value=token,
        pid=0,
        address=0,
        timestamp=int(metadata.get('issued_at') or int(time.time() * 1000)),
        source='cdp',
    )
    return TokenBatch(
        candidates=[candidate],
        base_url=context['base_url'],
        platform=context.get('platform') or 'ai',
        info={
            'app': str(app),
            'restarted': restarted,
            'version': context.get('version') or '',
            'method': 'cdp',
        },
    )
