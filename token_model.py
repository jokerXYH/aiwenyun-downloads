"""跨平台 token 模型：正则、候选对象、纯函数提取。

Windows 与 macOS 的登录态获取方式不同，但 token 的格式和后续验证逻辑
完全一致，因此把纯逻辑放在这里，避免平台相关模块互相依赖。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field


class TokenScanError(RuntimeError):
    """无法从客户端获取登录态。"""


TOKEN_RE = re.compile(
    r'(?<![0-9A-Za-z_.-])'
    r'(?P<org>\d{3,6})-(?P<flag>\d{1,6})-(?P<user>\d{3,8})-'
    r'(?P<uuid>[0-9A-Fa-f]{16,64})-(?P<ts>\d{10,16})-'
    r'(?P<ver>[0-9A-Za-z_.]{1,48})-PC-(?P<platform>[A-Za-z0-9]{1,16})'
    r'(?![0-9A-Za-z_.-])'
)

GENERIC_TOKEN_RE = re.compile(
    r'(?<![0-9A-Za-z_.-])'
    r'\d{3,6}-\d{1,6}-\d{3,8}-[0-9A-Za-z_.-]{20,100}-'
    r'\d{10,16}-[0-9A-Za-z_.]{1,48}-PC-[A-Za-z0-9]{1,16}'
    r'(?![0-9A-Za-z_.-])'
)

ASCII_MARKER = b'-PC-'
UTF16_MARKER = '-PC-'.encode('utf-16le')


@dataclass(frozen=True)
class TokenCandidate:
    value: str
    pid: int = 0
    address: int = 0
    timestamp: int = 0
    source: str = ''

    @property
    def user_id(self) -> str:
        match = TOKEN_RE.fullmatch(self.value)
        return match.group('user') if match else ''

    @property
    def org_id(self) -> str:
        match = TOKEN_RE.fullmatch(self.value)
        return match.group('org') if match else ''

    @property
    def version(self) -> str:
        match = TOKEN_RE.fullmatch(self.value)
        return match.group('ver') if match else ''

    @property
    def platform(self) -> str:
        match = TOKEN_RE.fullmatch(self.value)
        return match.group('platform') if match else 'ai'

    def __repr__(self) -> str:
        return 'TokenCandidate(len=%d, pid=%d, source=%s)' % (
            len(self.value), self.pid, self.source or '-')


@dataclass
class TokenBatch:
    candidates: list[TokenCandidate]
    base_url: str = ''
    platform: str = ''
    info: dict = field(default_factory=dict)


def timestamp_from_generic(value: str) -> int:
    parts = value.split('-')
    for part in reversed(parts):
        if part.isdigit() and len(part) >= 10:
            timestamp = int(part)
            if 1_500_000_000_000 <= timestamp <= int(time.time() * 1000) + 86400_000:
                return timestamp
    return 0


def extract_from_bytes(data: bytes, pid: int = 0,
                       base_address: int = 0) -> list[TokenCandidate]:
    """从一段内存字节里提取 token 候选（ASCII + UTF-16LE）。"""

    found: dict[str, TokenCandidate] = {}

    def add_match(text: str, offset: int) -> None:
        for match in TOKEN_RE.finditer(text):
            value = match.group(0)
            found.setdefault(value, TokenCandidate(
                value=value, pid=pid,
                address=base_address + offset + match.start(),
                timestamp=int(match.group('ts')), source='memory'))
        for match in GENERIC_TOKEN_RE.finditer(text):
            value = match.group(0)
            timestamp = timestamp_from_generic(value)
            if timestamp:
                found.setdefault(value, TokenCandidate(
                    value=value, pid=pid,
                    address=base_address + offset + match.start(),
                    timestamp=timestamp, source='memory'))

    position = 0
    while True:
        position = data.find(ASCII_MARKER, position)
        if position < 0:
            break
        start = max(0, position - 180)
        end = min(len(data), position + 40)
        add_match(data[start:end].decode('latin1', 'replace'), start)
        position += 1

    position = 0
    while True:
        position = data.find(UTF16_MARKER, position)
        if position < 0:
            break
        start = max(0, position - 360)
        end = min(len(data), position + 80)
        window = data[start:end]
        for shift in (0, 1):
            add_match(window[shift:].decode('utf-16le', 'ignore'), start + shift)
        position += 1

    return list(found.values())


def parse_token_metadata(value: str) -> dict:
    match = TOKEN_RE.fullmatch(value or '')
    if not match:
        return {}
    return {
        'org_id': match.group('org'),
        'user_id': match.group('user'),
        'issued_at': int(match.group('ts')),
        'version': match.group('ver'),
        'platform': match.group('platform'),
        'length': len(value),
    }
