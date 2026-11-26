"""软件更新检查：服务器地址与检查接口都由用户配置，不写死协议路径。

默认把检查接口返回的 JSON/文本里常见的版本字段解析出来；服务器端也可以
直接返回纯版本号文本。HTML 下载页会额外提取 Windows exe 和同名 .sha256
校验链接，交给 update_installer 完成下载、校验、替换和重启。
"""

from __future__ import annotations

import html
import json
import posixpath
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
_CHECKSUM_URL_KEYS = ('sha256_url', 'sha256Url', 'checksum_url',
                      'checksumUrl', 'sha256sum_url', 'sha256sumUrl')
_CHECKSUM_KEYS = ('sha256', 'sha256sum', 'sha256_hash', 'checksum')
_NOTES_KEYS = ('notes', 'releaseNotes', 'release_notes', 'changelog',
               'body', 'description')
_NESTED_KEYS = ('data', 'latest', 'release', 'result', 'obj')
_HREF_RE = re.compile(
    r'''(?:href|data-href|data-url)\s*=\s*["']([^"']+)["']''', re.IGNORECASE)


class UpdateError(RuntimeError):
    """更新检查失败（网络或返回内容不合法）。"""


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """只允许 HTTPS 重定向；避免先连到 http:// 再事后拒绝。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlparse(newurl).scheme.lower() != 'https':
            raise UpdateError('更新地址被重定向到非 HTTPS 地址，已拒绝。')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


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


def _html_links(raw: str, base_url: str) -> list[str]:
    """提取 HTML 里的候选链接，保留页面顺序。"""

    links: list[str] = []
    seen: set[str] = set()
    for match in _HREF_RE.finditer(raw):
        value = html.unescape(match.group(1)).strip()
        if not value or value.startswith(('#', 'javascript:', 'mailto:')):
            continue
        url = urllib.parse.urljoin(base_url, value)
        if url not in seen:
            seen.add(url)
            links.append(url)
    return links


def _select_windows_package(links: list[str]) -> str:
    """从下载页里选 Windows exe；没有 Windows 标记时只在唯一 exe 时采用。"""

    exes = [url for url in links
            if urllib.parse.urlparse(url).path.lower().endswith('.exe')]
    if not exes:
        return ''

    windows = [
        url for url in exes
        if 'windows' in urllib.parse.urlparse(url).path.lower()
        or 'win64' in urllib.parse.urlparse(url).path.lower()
    ]
    if not windows:
        return exes[0] if len(exes) == 1 else ''

    def score(url: str) -> tuple[int, int]:
        path = urllib.parse.urlparse(url).path.lower()
        return (
            1 if 'x64' in path or 'amd64' in path else 0,
            0 if 'mac' in path or 'darwin' in path else 1,
        )

    ranked = max(enumerate(windows),
                 key=lambda item: (score(item[1]), -item[0]))
    return ranked[1]


def _select_checksum(links: list[str], package_url: str) -> str:
    sums = [url for url in links
            if '.sha256' in urllib.parse.urlparse(url).path.lower()]
    if not sums:
        return ''
    if package_url:
        package_stem = posixpath.splitext(posixpath.basename(
            urllib.parse.urlparse(package_url).path))[0].lower()
        for url in sums:
            checksum_stem = posixpath.splitext(posixpath.basename(
                urllib.parse.urlparse(url).path))[0].lower()
            if package_stem and checksum_stem == package_stem:
                return url
    return sums[0] if len(sums) == 1 else ''


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
    checksum_url = (
        _first(payload, _CHECKSUM_URL_KEYS) if payload is not None else '')
    checksum = _first(payload, _CHECKSUM_KEYS) if payload is not None else ''
    notes = _first(payload, _NOTES_KEYS) if payload is not None else ''
    if payload is None:
        links = _html_links(raw, base_url)
        if not download:
            download = _select_windows_package(links)
        if not checksum_url:
            checksum_url = _select_checksum(links, download)
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
    if checksum_url:
        checksum_url = urllib.parse.urljoin(base_url, checksum_url)
    return {
        'version': version,
        'download_url': download,
        'checksum_url': checksum_url,
        'sha256': checksum,
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
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=_ssl_context()),
            _HttpsOnlyRedirectHandler())
        with opener.open(request, timeout=timeout) as response:
            data = response.read(2 * 1024 * 1024 + 1)
    except UpdateError:
        raise
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
        'checksum_url': data.get('checksum_url') or '',
        'sha256': data.get('sha256') or '',
        'server_url': UPDATE_URL,
        'check_url': UPDATE_URL,
        'notes': data.get('notes') or '',
    }
