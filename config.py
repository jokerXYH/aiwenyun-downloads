"""全局配置、路径发现与 API 主机发现。

设计原则：
- 默认模式不启动调试端口、不重启客户端。
- 只读取本机客户端已有的登录态（进程内存只读扫描）。
- 只向 HTTPS 业务主机发送邀请码相关请求，不向第三方上传任何数据。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

APP_NAME = '爱问云邀请码导出'
APP_VERSION = '2.0.1'
DEFAULT_BASE_URL = 'https://www.aiwenyun.cn/'
CLIENT_EXE = 'ai-yxt.exe'
CREATE_NO_WINDOW = 0x08000000

DEFAULT_WORKERS = 8
DEFAULT_RATE = 12.0
DEFAULT_TIMEOUT = 12.0
# 邀请码单个请求的 socket 超时：正常 0.3s 左右，6 秒还没回就当链路卡了
DEFAULT_INVITE_TIMEOUT = 6.0
DEFAULT_SCAN_TIMEOUT = 8.0
# 单个课堂最多折腾多久；超过就记为失败，交给第二轮重试
DEFAULT_CLASS_DEADLINE = 22.0
# 整轮长时间没有任何课堂完成时，判定为「卡住」，收尾并进入重试
DEFAULT_STALL_TIMEOUT = 28.0
# 第二轮重试的总时间预算
DEFAULT_RETRY_BUDGET = 30.0

TIMETABLE_PATH = 'gt/servlet/timeTable/getTimeTable'
INVITE_PATH = 'gt/servlet/liveclass/invite'
LIVE_INFO_PATH = 'gt/servlet/liveClass/liveInfo'
TASK_TYPES = [1, 2]

USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/89.0.4389.128 Safari/537.36'
)

BASE_URL_ENV = 'AIWENYUN_BASE_URL'
OUTPUT_DIR_ENV = 'AIWENYUN_OUTPUT_DIR'

_URL_RE = re.compile(r'https://[A-Za-z0-9.-]+(?::\d+)?')


def user_data_dirs() -> list[Path]:
    """爱问云可能使用的 Electron 用户数据目录，按优先级排序。"""

    appdata = Path(os.environ.get('APPDATA', ''))
    localappdata = Path(os.environ.get('LOCALAPPDATA', ''))
    home = Path.home()
    candidates = [
        appdata / 'ai_client',
        appdata / '爱问云',
        localappdata / 'ai_client',
        localappdata / 'AiCloud',
        # macOS Electron 用户数据
        home / 'Library' / 'Application Support' / 'ai_client',
        home / 'Library' / 'Application Support' / 'AiCloud',
        home / 'Library' / 'Application Support' / '爱问云',
    ]
    seen = set()
    result = []
    for path in candidates:
        key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        if path.is_dir():
            result.append(path)
    return result


def discover_base_urls() -> list[str]:
    """发现业务 API 主机。

    优先使用 `AIWENYUN_BASE_URL`；否则从 Chromium 的
    ``Network Persistent State`` 中提取访问过的爱问云/Plaso 主机，
    最后加入默认主机。返回顺序即尝试顺序。
    """

    env = (os.environ.get(BASE_URL_ENV) or '').strip()
    if env:
        url = normalize_base_url(env)
        if url:
            return [url]

    urls = [DEFAULT_BASE_URL]
    seen = {DEFAULT_BASE_URL.lower()}

    def add(value: str) -> None:
        url = normalize_base_url(value)
        if not url:
            return
        host = (urlparse(url).hostname or '').lower()
        if not any(word in host for word in ('aiwenyun', 'plaso')):
            return
        # 下载/日志/静态资源域名不是业务 API，跳过以免无谓尝试。
        if any(part in host for part in ('download', 'wwwr', 'static', 'cdn',
                                         'img', 'resource', 'log.')):
            return
        if url.lower() in seen:
            return
        seen.add(url.lower())
        urls.append(url)

    for data_dir in user_data_dirs():
        path = data_dir / 'Network' / 'Network Persistent State'
        try:
            raw = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue

        # 文件是 JSON，但结构可能随 Chromium 版本变化；正则兜底。
        for match in _URL_RE.finditer(raw):
            add(match.group(0))

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            for value in _walk_strings(payload):
                if value.startswith('https://'):
                    add(value)

        if len(urls) >= 4:
            break

    return urls[:4]


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def normalize_base_url(value: str) -> str | None:
    value = (value or '').strip()
    if not value:
        return None
    if '://' not in value:
        value = 'https://' + value
    parsed = urlparse(value)
    if parsed.scheme.lower() != 'https' or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    netloc = parsed.hostname
    if parsed.port:
        netloc += ':%d' % parsed.port
    path = parsed.path.rstrip('/')
    return 'https://%s%s/' % (netloc, path)


def output_folder() -> Path:
    """桌面/OneDrive 桌面/Documents 下的「爱问云邀请码」目录。"""

    env = (os.environ.get(OUTPUT_DIR_ENV) or '').strip()
    if env:
        folder = Path(env).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    home = Path.home()
    for desktop in (home / 'Desktop', home / 'OneDrive' / 'Desktop'):
        if desktop.is_dir():
            folder = desktop / '爱问云邀请码'
            try:
                folder.mkdir(parents=True, exist_ok=True)
                return folder
            except OSError:
                # macOS TCC / 权限受限时退回 Documents
                continue
    folder = home / 'Documents' / '爱问云邀请码'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def client_candidates() -> list[Path]:
    """常见的客户端安装路径。"""

    paths = [
        Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'AiCloud' / CLIENT_EXE,
        Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'AiCloud' / CLIENT_EXE,
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'AiCloud' / CLIENT_EXE,
    ]
    return [path for path in paths if path.is_file()]


def open_path(path: Path) -> None:
    """跨平台打开文件/文件夹。"""

    target = str(path)
    if sys.platform == 'win32':
        os.startfile(target)
        return
    command = 'open' if sys.platform == 'darwin' else 'xdg-open'
    subprocess.Popen([command, target], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
