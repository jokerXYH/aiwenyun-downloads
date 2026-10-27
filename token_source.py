"""跨平台登录态获取入口。

Windows：只读扫描 ai-yxt.exe 内存（免重启、无调试端口）。
macOS：重启爱问云并开启本机 9222 调试端口，通过 CDP 读取登录态。
"""

from __future__ import annotations

import sys
import urllib.request
from typing import Any, Callable

from token_model import (TOKEN_RE, TokenBatch, TokenCandidate, TokenScanError,
                         extract_from_bytes as _extract_from_bytes,
                         parse_token_metadata)

__all__ = [
    'TOKEN_RE', 'TokenBatch', 'TokenCandidate', 'TokenScanError',
    'acquire_tokens', 'client_running', 'debug_port_open',
    'parse_token_metadata',
]


def platform_name() -> str:
    return sys.platform


def debug_port_open(port: int = 9222, host: str = '127.0.0.1',
                    timeout: float = 1.2) -> bool:
    try:
        with urllib.request.urlopen(
                'http://%s:%d/json/version' % (host, port), timeout=timeout):
            return True
    except OSError:
        return False


def client_running() -> bool:
    if sys.platform == 'darwin':
        from token_macos import client_running as mac_running
        return mac_running()
    from token_windows import list_client_pids
    return bool(list_client_pids())


def acquire_tokens(scan_timeout: float = 8.0, allow_restart: bool = False,
                   on_event: Callable[[str, Any], None] | None = None,
                   cancel_event=None, full: bool = False) -> TokenBatch:
    if sys.platform == 'darwin':
        from token_macos import acquire_tokens as mac_acquire
        return mac_acquire(
            scan_timeout=scan_timeout, allow_restart=allow_restart,
            on_event=on_event, cancel_event=cancel_event)

    from token_windows import ScanStats, scan_tokens
    stats = ScanStats()
    candidates = scan_tokens(timeout=scan_timeout, stats=stats,
                             stop_after_first=not full)
    return TokenBatch(
        candidates=candidates,
        info={
            'processes': stats.processes,
            'mb': round(stats.bytes_read / 1048576, 1),
            'elapsed': round(stats.elapsed, 2),
            'regions': stats.regions,
            'method': 'memory',
            'full': full,
            'partial': stats.partial,
        },
    )
