"""带连接复用、限速、重试与 token 自动刷新的爱问云 API 客户端。

网络层只做三件事：
1. 用 HTTPS + 默认证书校验访问业务主机；
2. 把 token 放在 ``access-token`` 请求头里（不放 URL，避免日志泄露）；
3. 对 429/5xx/断连做有限重试，对 ``code=2`` 抛 TokenExpired。

所有异常消息在抛出前都会脱敏，避免服务端把 token 原样回显到日志/界面。
"""

from __future__ import annotations

import http.client
import json
import random
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from analyze import invite_code_from_payload
from config import (DEFAULT_INVITE_TIMEOUT, DEFAULT_TIMEOUT, INVITE_PATH,
                    LIVE_INFO_PATH, TASK_TYPES, TIMETABLE_PATH, USER_AGENT)
from token_model import TOKEN_RE

_REDACT_PATTERNS = (
    (TOKEN_RE, '***'),
    (re.compile(r'(?i)(access[_-]?token\s*[:=]\s*)[^\s,;"\']+'), r'\1***'),
)


def redact(text: Any, limit: int = 300) -> str:
    value = str(text or '')
    for pattern, replacement in _REDACT_PATTERNS:
        value = pattern.sub(replacement, value)
    value = value.replace('\r', ' ').replace('\n', ' ')
    return value[:limit]


class ApiError(RuntimeError):
    """接口返回业务错误或不可恢复的响应。"""


class TokenExpired(ApiError):
    """服务端返回 code=2 / HTTP 401/403，需要刷新 token。"""


class NetworkError(ApiError):
    """连接失败、超时、证书错误等。"""


class RateLimitError(ApiError):
    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class AuthError(ApiError):
    """所有候选 token / 主机都无法通过验证。"""


class TokenBucket:
    """线程安全的令牌桶限速器。"""

    def __init__(self, rate: float, burst: int = 1) -> None:
        self.rate = max(0.1, float(rate))
        self.capacity = max(1, int(burst))
        self._tokens = float(self.capacity)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, cancel_event: threading.Event | None = None) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self.rate
            # 加一点抖动，避免所有线程同一时刻打满服务端
            wait += random.uniform(0, min(0.05, wait * 0.2))
            if cancel_event is not None:
                if cancel_event.wait(wait):
                    raise ApiError('已取消')
            else:
                time.sleep(wait)


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str]
    final_path: str


def _abort_connection(connection: http.client.HTTPSConnection) -> None:
    """看门狗：到点还没返回就强行断开，避免线程永远卡在读响应上。"""

    sock = getattr(connection, 'sock', None)
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


class HttpTransport:
    """线程本地连接池；每个工作线程复用自己的 HTTPS 连接。"""

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme.lower() != 'https' or not parsed.hostname:
            raise ValueError('只允许 HTTPS 业务主机：%s' % base_url)
        self.base_url = base_url
        self.host = parsed.hostname
        self.port = parsed.port or 443
        self.prefix = parsed.path.rstrip('/')
        self.timeout = timeout
        self._context = ssl.create_default_context()
        self._local = threading.local()

    def _connection(self) -> http.client.HTTPSConnection:
        connection = getattr(self._local, 'connection', None)
        if connection is None:
            connection = http.client.HTTPSConnection(
                self.host, self.port, timeout=self.timeout,
                context=self._context)
            self._local.connection = connection
        return connection

    def _drop_connection(self) -> None:
        connection = getattr(self._local, 'connection', None)
        self._local.connection = None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass

    def close(self) -> None:
        self._drop_connection()

    def post_json(self, path: str, payload: dict[str, Any],
                  headers: dict[str, str],
                  deadline: float | None = None,
                  request_timeout: float | None = None) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False,
                          separators=(',', ':')).encode('utf-8')
        request_path = '%s/%s' % (self.prefix, path.lstrip('/'))
        started = time.monotonic()
        last_error: Exception | None = None
        for attempt in range(2):
            # deadline 是整次课堂抓取的总截止时间（monotonic 秒）
            if deadline is not None and deadline - time.monotonic() < 0.5:
                raise NetworkError('请求超时：%s' % path)
            timeout = request_timeout or self.timeout
            if deadline is not None:
                timeout = max(0.8, min(timeout, deadline - time.monotonic()))
            # 空闲超过 5 秒的连接可能已被服务端/WAF 悄悄关掉，直接换新连接，
            # 避免复用半死连接时干等到 socket 超时（这就是“爬到一半卡住”的来源）。
            last_used = getattr(self._local, 'last_used', 0.0)
            if last_used and time.monotonic() - last_used > 5.0:
                self._drop_connection()
            connection = self._connection()
            connection.timeout = timeout
            watchdog = threading.Timer(timeout + 1.2, _abort_connection,
                                       args=(connection,))
            watchdog.daemon = True
            watchdog.start()
            try:
                connection.connect()
                if connection.sock is not None:
                    connection.sock.settimeout(timeout)
                connection.request('POST', request_path, body=body,
                                   headers=headers)
                response = connection.getresponse()
                raw = response.read()
                status = response.status
                response_headers = {
                    key.lower(): value for key, value in response.getheaders()
                }
                if response.will_close:
                    self._drop_connection()
                response_payload = self._parse_response(
                    status, raw, response_headers, path)
                return response_payload
            except TokenExpired:
                raise
            except (RateLimitError, ApiError):
                raise
            except (http.client.HTTPException, OSError, ssl.SSLError) as error:
                self._drop_connection()
                last_error = error
                if attempt == 0:
                    continue
                raise NetworkError(
                    '请求失败：%s（%s，%.1fs）' % (
                        path, redact(error), time.monotonic() - started)) from error
            finally:
                self._local.last_used = time.monotonic()
                if watchdog is not None:
                    watchdog.cancel()
        raise NetworkError('请求失败：%s（%s）' % (path, redact(last_error)))

    @staticmethod
    def _parse_response(status: int, raw: bytes, headers: dict[str, str],
                        path: str) -> dict[str, Any]:
        if status in (401, 403):
            raise TokenExpired(
                '登录态失效（HTTP %d）：%s' % (status, path))
        if status == 429:
            retry_after = _as_float(headers.get('retry-after'), 0.0)
            raise RateLimitError(
                '请求过于频繁（HTTP 429）：%s' % path, retry_after)
        if status >= 500:
            raise ApiError('服务端错误（HTTP %d）：%s' % (status, path))
        if 300 <= status < 400:
            raise ApiError('接口返回重定向，已拒绝：%s' % path)
        if status < 200 or status >= 300:
            raise ApiError('接口返回 HTTP %d：%s' % (status, path))

        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError as error:
            raise ApiError('响应不是 UTF-8：%s' % path) from error
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            snippet = text.strip().replace('\n', ' ')[:120]
            raise ApiError('响应不是 JSON：%s（%s）' % (path, redact(snippet))) from error
        if not isinstance(parsed, dict):
            raise ApiError('响应格式异常：%s' % path)

        code = parsed.get('code')
        if str(code) == '2':
            raise TokenExpired(
                redact(parsed.get('msg') or 'access_token 已失效'))
        if code not in (0, None) and str(code) != '0':
            raise ApiError('接口返回 code=%s：%s' % (
                code, redact(parsed.get('msg') or '')))
        return parsed


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class ApiSession:
    """一次已认证的会话；token 刷新时创建新实例。"""

    def __init__(self, base_url: str, token: str, platform: str = 'ai',
                 version: str = '', timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url
        self.platform = platform or 'ai'
        self.version = version or ''
        self._token = token
        self._lock = threading.Lock()
        self.transport = HttpTransport(base_url, timeout=timeout)

    def token(self) -> str:
        with self._lock:
            return self._token

    def set_token(self, token: str) -> None:
        with self._lock:
            self._token = token

    def close(self) -> None:
        self.transport.close()
        with self._lock:
            self._token = ''

    def headers(self) -> dict[str, str]:
        token = self.token()
        if not token:
            raise TokenExpired('登录态为空')
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/plain, */*',
            'access-token': token,
            'platform': self.platform,
            'device': 'pc',
            'User-Agent': USER_AGENT,
        }
        if self.version:
            headers['version'] = self.version
        return headers

    def post(self, path: str, payload: dict[str, Any],
             deadline: float | None = None,
             request_timeout: float | None = None) -> dict[str, Any]:
        return self.transport.post_json(path, payload, self.headers(),
                                        deadline=deadline,
                                        request_timeout=request_timeout)

    def validate(self) -> None:
        """用一次最小请求验证 token 与主机是否匹配。"""

        self.post(TIMETABLE_PATH, {
            'startTime': int(time.time() * 1000),
            'taskType': list(TASK_TYPES),
            'pageSize': 1,
            'pageStart': 0,
        })

    def fetch_timetable(self, start_ms: int | None = None,
                        page_size: int = 100,
                        max_pages: int = 20) -> list[dict[str, Any]]:
        start = int(start_ms if start_ms is not None else time.time() * 1000)
        rows: list[dict[str, Any]] = []
        seen: set[int] = set()
        total = 0
        for page in range(max(1, max_pages)):
            payload = self.post(TIMETABLE_PATH, {
                'startTime': start,
                'taskType': list(TASK_TYPES),
                'pageSize': page_size,
                'pageStart': page,
            })
            page_rows = _timetable_rows(payload)
            total = _timetable_total(payload) or total
            fresh = 0
            for row in page_rows:
                class_id = row.get('id')
                if not class_id or class_id in seen:
                    continue
                seen.add(class_id)
                rows.append(row)
                fresh += 1
            if not page_rows or fresh == 0:
                break
            if total and len(rows) >= total:
                break
        return rows

    def fetch_live_info(self, ids: list[int],
                        chunk_size: int = 50) -> dict[int, dict[str, Any]]:
        details: dict[int, dict[str, Any]] = {}
        for start in range(0, len(ids), chunk_size):
            chunk = [int(value) for value in ids[start:start + chunk_size]]
            if not chunk:
                continue
            payload = self.post(LIVE_INFO_PATH, {'ids': chunk})
            items = payload.get('obj')
            if isinstance(items, dict):
                items = items.get('list') or list(items.values())
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    class_id = int(item.get('id'))
                except (TypeError, ValueError):
                    continue
                details[class_id] = item
        return details

    def fetch_invite_code(self, class_id: int,
                          deadline: float | None = None) -> str:
        payload = self.post(INVITE_PATH, {'id': int(class_id)},
                            deadline=deadline,
                            request_timeout=DEFAULT_INVITE_TIMEOUT)
        code = invite_code_from_payload(payload)
        if not code:
            raise ApiError('响应里没有 6 位邀请码')
        return code


def _timetable_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    # 延迟导入避免循环依赖
    from analyze import timetable_rows
    return timetable_rows(payload)


def _timetable_total(payload: dict[str, Any]) -> int:
    from analyze import timetable_total
    return timetable_total(payload)


def authenticate(base_urls: list[str], candidates: list,
                 timeout: float = DEFAULT_TIMEOUT) -> tuple[ApiSession, Any]:
    """用候选 token × 候选主机做最小验证，返回第一个可用会话。"""

    last_error: Exception | None = None
    tried = 0
    for base_url in base_urls[:3]:
        for candidate in candidates[:5]:
            tried += 1
            session = None
            try:
                session = ApiSession(
                    base_url, candidate.value,
                    platform=candidate.platform,
                    version=candidate.version,
                    timeout=timeout)
                session.validate()
                return session, candidate
            except (TokenExpired, NetworkError, ApiError) as error:
                last_error = error
                if session is not None:
                    try:
                        session.close()
                    except Exception:
                        pass
    if tried == 0:
        raise AuthError('没有可用的登录态候选')
    raise AuthError('登录态验证失败：%s' % redact(last_error))
