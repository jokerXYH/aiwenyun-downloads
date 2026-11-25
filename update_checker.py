"""软件更新检查：服务器地址与检查接口都由用户配置，不写死协议路径。

默认把检查接口返回的 JSON/文本里常见的版本字段解析出来；服务器端也可以
直接返回纯版本号文本。更新包只负责打开下载地址，不在客户端自动安装。
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any, Callable

from config import UPDATE_URL
from net import _ssl_context

USER_AGENT = 'aiwenyun-tools-update-check/1.0'
REQUEST_TIMEOUT = 10.0

_VERSION_KEYS = ('version', 'versionName', 'latest_version', 'latestVersion',
                 'tag_name', 'tag', 'appVersion', 'app_version')
_DOWNLOAD_KEYS = ('download_url', 'downloadUrl', 'url', 'download',
                  'html_url', 'htmlUrl', 'release_url', 'releaseUrl',
                  'page_url', 'pageUrl')
_NOTES_KEYS = ('notes', 'releaseNotes', 'release_notes', 'changelog',
               'body', 'description')
_NESTED_KEYS = ('data', 'latest', 'release', 'result', 'obj')


class UpdateError(RuntimeError):
    """更新检查失败（网络或返回内容不合法）。"""


def _walk_payload(payload: Any, depth: int = 0):
    if depth > 5:
        return
    if isinstance(payload, dict):
        yield payload
        for key in _NESTED_KEYS:
            if key in payload:
                yield from _walk_payload(payload[key], depth + 1)
    elif isinstance(payload, list):
        for item in payload[:10]:
            yield from _walk_payload(item, depth + 1)


def _first(payload: Any, keys: tuple[str, ...]) -> str:
    for item in _walk_payload(payload):
        for key in keys:
            value = item.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                return str(value).strip()
    return ''


def parse_update_payload(text: str, base_url: str = '') -> dict:
    """从 JSON 或纯文本响应中提取版本、下载地址和更新说明。

    不强制某一种服务器实现：常见字段名和纯版本号文本都能识别。
    """

    raw = str(text or '').strip()
    if not raw:
        raise UpdateError('更新接口返回为空。')
    payload: Any = None
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None

    version = _first(payload, _VERSION_KEYS) if payload is not None else ''
    download = _first(payload, _DOWNLOAD_KEYS) if payload is not None else ''
    notes = _first(payload, _NOTES_KEYS) if payload is not None else ''
    if not version:
        # 下载页是 HTML 时优先读版本徽标 / version 字段，避免把
        # viewport 里的 1.0 之类数字误当成软件版本。
        patterns = (
            r'version-badge[^>]*>\s*v?(\d+(?:\.\d+){1,3})',
            r'(?:["\']version["\']|版本)\s*[:：=]\s*["\']?v?'
            r'(\d+(?:\.\d+){1,3})',
            r'\bv(\d+(?:\.\d+){1,3})\b',
        )
        for pattern in patterns:
            match = re.search(pattern, raw, re.IGNORECASE)
            if match:
                version = match.group(1)
                break
        if not version and len(raw) <= 40:
            match = re.fullmatch(r'v?(\d+(?:\.\d+){1,3})', raw,
                                 re.IGNORECASE)
            version = match.group(1) if match else ''
        if not version:
            match = re.search(r'\b\d+(?:\.\d+){2,3}\b', raw)
            version = match.group(0) if match else ''
    if not version:
        raise UpdateError('更新接口没有返回版本号。')
    version = version.strip()
    if version.lower().startswith('v'):
        version = version[1:].strip()
    if download:
        download = urllib.parse.urljoin(base_url, download)
    return {
        'version': version,
        'download_url': download,
        'notes': notes,
        'raw': raw,
    }


def fetch_update_payload(url: str, timeout: float = REQUEST_TIMEOUT) -> str:
    request = urllib.request.Request(url, headers={
        'User-Agent': USER_AGENT,
        'Accept': 'application/json, text/plain, */*',
        'Cache-Control': 'no-cache',
    })
    try:
        with urllib.request.urlopen(
                request, timeout=timeout, context=_ssl_context()) as response:
            data = response.read(2 * 1024 * 1024 + 1)
    except Exception as error:  # noqa: BLE001  网络错误统一给界面友好提示
        raise UpdateError('无法连接更新服务器：%s' % error) from error
    if len(data) > 2 * 1024 * 1024:
        raise UpdateError('更新接口返回内容过大。')
    return data.decode('utf-8', errors='replace')


def version_key(value: str) -> tuple[int, ...]:
    numbers = [int(part) for part in re.findall(r'\d+', str(value or ''))]
    return tuple(numbers)


def is_newer(latest: str, current: str) -> bool:
    left = version_key(latest)
    right = version_key(current)
    if not left or not right:
        return str(latest).strip() != str(current).strip()
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left > right


def check_for_update(current_version: str,
                     fetcher: Callable[[str], str] = fetch_update_payload
                     ) -> dict:
    """检查更新：固定请求项目下载页，返回可直接交给界面展示的结果。"""

    text = fetcher(UPDATE_URL)
    data = parse_update_payload(text, base_url=UPDATE_URL)
    latest = data['version']
    update = is_newer(latest, current_version)
    download = data.get('download_url') or UPDATE_URL
    return {
        'status': 'update' if update else 'latest',
        'current': str(current_version or '').strip(),
        'latest': latest,
        'download_url': download,
        'server_url': UPDATE_URL,
        'check_url': UPDATE_URL,
        'notes': data.get('notes') or '',
    }
