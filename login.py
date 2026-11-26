"""账号密码登录：custom/usr/doLogin，只读接口，返回登录态 token。

设计原则（与仓库其余模块一致）：
- 密码只在本进程内存中使用，不写日志、不落盘；错误信息自动脱敏；
- 只向 HTTPS 业务主机发送请求；
- 登录态 token 供 GUI 的板书导出使用，不影响邀请码导出原有流程。

请求结构按线上客户端真实发送还原（2026-11-22 CDP 抓包验证）::

    POST /custom/usr/doLogin
    headers: access-token: previewToken, plaso-requestid: <uuid>,
             version: <资源版本>, platform: ai, device: pc
    body: {rawName, name, passwd, loginName, role: 0,
           osInfo, version, deviceId, deviceName, systemInfo, clientVersion}

``passwd`` 的变换规则与客户端一致（模块 482396）::

    输入 32 位 md5              -> 原样发送（手输分支）
    输入混淆形式 <5位hex>a<27位hex>__P -> 发送 "P__" + 还原后的 md5（自动登录分支）
    输入 "P__"+32位md5         -> 原样发送
    输入其他（原文密码）        -> 发送 hex_md5(原文)

deviceId 优先从本机爱问云客户端的 Local Storage（clientUUID）只读获取，
保证与服务端登记的设备一致；取不到时生成本机稳定 ID。
"""

from __future__ import annotations

import ctypes
import hashlib
import http.client
import json
import os
import platform
import re
import socket
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config import (CLIENT_VERSION, CLIENT_VERSION_FULL, DEFAULT_BASE_URL)
from net import _ssl_context, redact

LOGIN_PATH = 'custom/usr/doLogin'
_LOGIN_TIMEOUT = 20.0
_MAX_LOGIN_RESPONSE_BYTES = 16 * 1024 * 1024

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) ai_client/2.0.0 '
              'Chrome/112.0.5615.204 Electron/24.8.8 Safari/537.36')

_MD5_RE = re.compile(r'^[0-9a-f]{32}$')
_OBFUSCATED_RE = re.compile(r'^[0-9a-f]{5}a[0-9a-f]{27}__P$')
_DEVICE_ID_RE = re.compile(
    rb'windows-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

ROLE_TEACHER = 0
ROLE_STUDENT = 1


class LoginError(RuntimeError):
    """登录失败（账号密码不对、被锁、网络异常等）。"""


@dataclass
class LoginResult:
    token: str
    login_name: str = ''
    name: str = ''
    org_id: Any = None
    org_name: str = ''
    role: str = 'teacher'
    raw_keys: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 密码变换

def transform_passwd(text: str) -> str:
    """把用户输入的任意形式转成 doLogin 的 passwd 字段（纯函数，可测试）。"""

    value = (text or '').strip()
    if not value:
        return value
    if _OBFUSCATED_RE.match(value):
        # 混淆形式（客户端记住密码的存储格式）-> P__ + 还原 md5
        return 'P__' + value[:5] + value[6:-3]
    if value.startswith('P__') and _MD5_RE.match(value[3:]):
        return value
    if _MD5_RE.match(value):
        return value
    return hashlib.md5(value.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------- 设备信息

def _client_storage_dirs() -> list[Path]:
    """客户端 Local Storage 的候选目录；只读，不修改客户端数据。"""

    home = Path.home()
    candidates = [
        home / 'AppData' / 'Roaming' / 'ai_client' / 'Local Storage' / 'leveldb',
        home / 'Library' / 'Application Support' / 'ai_client'
        / 'Local Storage' / 'leveldb',
    ]
    return [path for path in candidates if path.is_dir()]


def _device_id_file() -> Path:
    """稳定备用设备 ID 的保存位置（不写安装目录）。"""

    if sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    elif os.name == 'nt':
        base = Path(os.environ.get('APPDATA') or Path.home())
    else:
        base = Path(os.environ.get('XDG_CONFIG_HOME') or
                    Path.home() / '.config')
    return base / 'aiwenyun-tools' / 'device-id.txt'


def _read_client_device_id() -> str:
    """从本机爱问云客户端 Local Storage 只读读取 deviceId（clientUUID）。"""

    utf16 = b'w\x00i\x00n\x00d\x00o\x00w\x00s\x00-\x00'
    for storage in _client_storage_dirs():
        try:
            for file in sorted(storage.glob('*')):
                if not file.is_file():
                    continue
                data = file.read_bytes()
                for needle in (b'windows-', utf16):
                    start = 0
                    while True:
                        i = data.find(needle, start)
                        if i < 0:
                            break
                        start = i + 1
                        window = data[i:i + 140]
                        match = _DEVICE_ID_RE.search(window)
                        if match:
                            return match.group(0).decode('ascii')
        except OSError:
            continue
    return ''


def _stable_device_id(path: Path | None = None) -> str:
    """读不到客户端 deviceId 时，生成并持久化一个稳定值。"""

    target = Path(path) if path is not None else _device_id_file()
    try:
        saved = target.read_text(encoding='ascii').strip()
    except (OSError, UnicodeError):
        saved = ''
    if _DEVICE_ID_RE.fullmatch(saved.encode('ascii', 'ignore')):
        return saved
    generated = 'windows-%s' % uuid.uuid4()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name('.%s.tmp%s' % (target.stem, target.suffix))
        temp.write_text(generated, encoding='ascii')
        temp.replace(target)
    except OSError:
        pass
    return generated


def client_device_id(path: Path | None = None) -> str:
    """优先客户端真实 deviceId；取不到时复用持久化的稳定 ID。"""

    found = _read_client_device_id()
    if found:
        return found
    return _stable_device_id(path)


def _total_ram_gb() -> str:
    try:
        class MemoryStatus(ctypes.Structure):
            _fields_ = [('dwLength', ctypes.c_ulong),
                        ('dwMemoryLoad', ctypes.c_ulong),
                        ('ullTotalPhys', ctypes.c_ulonglong)]
        status = MemoryStatus()
        status.dwLength = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return '%.2fG' % (status.ullTotalPhys / 1073741824)
    except Exception:  # noqa: BLE001
        return ''


def device_payload() -> dict[str, str]:
    """构造设备信息字段（osInfo / deviceId / deviceName / systemInfo 等）。"""

    os_info = '%s %s Windows_NT%s %s' % (
        platform.processor() or 'CPU', _total_ram_gb(),
        platform.version(), platform.machine())
    return {
        'osInfo': os_info,
        'deviceId': client_device_id(),
        'deviceName': socket.gethostname() or 'pc',
        'systemInfo': '%s %s 2.0.0' % (platform.version(), platform.machine()),
        'clientVersion': CLIENT_VERSION,
        'version': CLIENT_VERSION_FULL,
    }


def build_payload(login_name: str, passwd: str, role: int = ROLE_TEACHER,
                  device: dict[str, str] | None = None) -> dict[str, Any]:
    """构造 doLogin 请求体（纯函数，便于测试）。"""

    payload: dict[str, Any] = {
        'rawName': login_name,
        'name': login_name,
        'passwd': passwd,
        'loginName': login_name,
        'role': role,
    }
    payload.update(device or {})
    return payload


def scrub(text: Any, password: str = '', limit: int = 300) -> str:
    """错误信息脱敏：token 按既定规则，密码原值一律替换。"""

    value = redact(text, limit=limit)
    if password:
        value = value.replace(password, '***')
    return value


# ---------------------------------------------------------------- 请求

def _post_json(base_url: str, path: str, payload: dict[str, Any],
               timeout: float = _LOGIN_TIMEOUT) -> dict[str, Any]:
    """POST JSON，返回原始响应（code 可能为非 0，由调用方解释）。"""

    parsed = urlparse(base_url)
    if parsed.scheme.lower() != 'https' or not parsed.hostname:
        raise LoginError('只允许 HTTPS 业务主机：%s' % base_url)
    prefix = parsed.path.rstrip('/')
    request_path = '%s/%s' % (prefix, path.lstrip('/'))
    body = json.dumps(payload, ensure_ascii=False,
                      separators=(',', ':')).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*',
        'access-token': 'previewToken',
        'plaso-requestid': uuid.uuid4().hex,
        'platform': 'ai',
        'device': 'pc',
        'version': CLIENT_VERSION,
        'User-Agent': USER_AGENT,
    }
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port or 443, timeout=timeout,
        context=_ssl_context())
    try:
        connection.request('POST', request_path, body=body, headers=headers)
        response = connection.getresponse()
        status = response.status
        raw = response.read(_MAX_LOGIN_RESPONSE_BYTES + 1)
    except (OSError, http.client.HTTPException) as error:
        raise LoginError('登录请求失败（网络）：%s' % scrub(error)) from error
    finally:
        connection.close()

    if status != 200:
        raise LoginError('登录接口返回 HTTP %d' % status)
    if len(raw) > _MAX_LOGIN_RESPONSE_BYTES:
        raise LoginError('登录响应过大，已拒绝。')
    try:
        result = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LoginError('登录响应不是 JSON') from error
    if not isinstance(result, dict):
        raise LoginError('登录响应格式异常')
    return result


def _friendly_error(resp: dict[str, Any], password: str) -> LoginError:
    """把服务端错误转成可读、脱敏的 LoginError。"""

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
    """账号密码登录，成功返回含 token 的 LoginResult。

    password 支持四种输入：原文密码、32 位 md5、P__ 前缀 md5、
    客户端混淆形式（33 位 + __P），均按客户端规则变换后提交。
    """

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
