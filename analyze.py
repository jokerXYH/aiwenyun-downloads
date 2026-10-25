"""接口响应解析：只做纯函数，便于单元测试和复用。"""

from __future__ import annotations

import json
import re
from typing import Any

CODE_RE = re.compile(r'邀请码[^0-9]{0,6}(\d{6})(?!\d)')
FIELD_RE = re.compile(
    r'["\']?(?:inviteCode|invite_code|invitationCode|invitation_code)'
    r'["\']?\s*[:=]\s*["\']?(\d{6})(?!\d)')
CODE_VALUE_RE = re.compile(r'^\d{6}$')
FULLWIDTH_RE = re.compile('[０-９]')

_INVITE_KEYS = (
    'code', 'inviteCode', 'invite_code', 'invitationCode',
    'invitation_code', 'roomCode', 'room_code',
)
_ID_KEYS = ('id', 'taskId', 'classId', 'liveId', 'courseId')
_TOPIC_KEYS = ('topic', 'title', 'name', 'className', 'courseName')
_BEGIN_KEYS = ('beginTime', 'startTime', 'begin')
_END_KEYS = ('endTime', 'end')


def normalize_digits(text: str) -> str:
    return FULLWIDTH_RE.sub(lambda ch: chr(ord(ch.group(0)) - 65248), text or '')


def extract_codes(text: str) -> list[str]:
    """按出现顺序提取带明确标签的 6 位邀请码并去重。"""

    source = normalize_digits(text or '')
    if not source:
        return []
    codes: list[str] = []
    for pattern in (CODE_RE, FIELD_RE):
        for match in pattern.finditer(source):
            value = match.group(1)
            if value not in codes:
                codes.append(value)
    return codes


def _clean_code(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ''
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str):
        return ''
    value = normalize_digits(value).strip()
    if CODE_VALUE_RE.match(value):
        return value
    return ''


def invite_code_from_payload(payload: Any) -> str:
    """优先读取结构化字段，取不到再用带标签的正则兜底。

    正常响应：``{"code":0,"obj":{"code":"457288"}}``
    ``obj`` 也可能是列表（批量接口）。
    """

    if isinstance(payload, dict):
        for container in (payload.get('obj'), payload.get('data'), payload):
            candidates = container if isinstance(container, list) else [container]
            for item in candidates:
                if not isinstance(item, dict):
                    continue
                for key in _INVITE_KEYS:
                    code = _clean_code(item.get(key))
                    if code:
                        return code

    # 兜底：只匹配 "邀请码...123456" 或 "inviteCode": "123456" 这类明确语义，
    # 不会把时间戳、ID 等普通 6 位数字误认成邀请码。
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(payload or '')
    codes = extract_codes(text)
    return codes[0] if codes else ''


def _find_list(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for container in (payload.get('obj'), payload.get('data'), payload.get('result')):
        if isinstance(container, list):
            return container
        if isinstance(container, dict):
            for key in ('list', 'rows', 'items', 'records'):
                value = container.get(key)
                if isinstance(value, list):
                    return value
    for key in ('list', 'rows', 'items', 'records'):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def timetable_total(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    for container in (payload.get('obj'), payload.get('data'), payload):
        if isinstance(container, dict):
            for key in ('total', 'count', 'totalCount'):
                try:
                    value = int(container.get(key) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value:
                    return value
    return 0


def _first(item: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ''):
            return value
    return default


def normalize_groups(value: Any) -> list[str]:
    """把 groups 统一成名称列表；兼容 dict、list[str]、list[dict]。"""

    if isinstance(value, dict):
        raw = list(value.values())
    elif isinstance(value, list):
        raw = value
    elif isinstance(value, str):
        raw = [value]
    else:
        raw = []
    result: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = _first(item, ('name', 'groupName', 'className', 'title'), '')
        else:
            name = item
        name = str(name or '').strip()
        if name and name not in result:
            result.append(name)
    return result


def timetable_rows(payload: Any) -> list[dict[str, Any]]:
    """把课程表响应整理成统一的行结构。"""

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in _find_list(payload):
        if not isinstance(item, dict):
            continue
        raw_id = _first(item, _ID_KEYS)
        try:
            class_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if class_id in seen:
            continue
        seen.add(class_id)
        rows.append({
            'id': class_id,
            'topic': str(_first(item, _TOPIC_KEYS, '') or '').strip(),
            'begin': _first(item, _BEGIN_KEYS, 0) or 0,
            'end': _first(item, _END_KEYS, 0) or 0,
            'taskType': item.get('taskType'),
            'groups': normalize_groups(item.get('groups')),
            'teacher': str(item.get('teacherName') or '').strip(),
        })
    return rows


def live_info_map(payload: Any) -> dict[int, dict[str, Any]]:
    """把 liveInfo 响应整理成 ``{课堂 id: 详情}``。"""

    details: dict[int, dict[str, Any]] = {}
    for item in _find_list(payload):
        if not isinstance(item, dict):
            continue
        try:
            class_id = int(_first(item, ('id', 'classId', 'liveId')))
        except (TypeError, ValueError):
            continue
        details[class_id] = item
    return details


def enrich_row(row: dict[str, Any], detail: dict[str, Any] | None) -> dict[str, Any]:
    """用课堂详情补全/纠正课程表里的字段。"""

    if not detail:
        return row
    topic = str(detail.get('topic') or row.get('topic') or '').strip()
    groups = normalize_groups(detail.get('groups')) or row.get('groups') or []
    begin = detail.get('startTime') or detail.get('beginTime') or row.get('begin') or 0
    end = detail.get('endTime') or row.get('end') or 0
    return {
        **row,
        'topic': topic or row.get('topic', ''),
        'begin': begin,
        'end': end,
        'groups': groups,
        'teacher': str(detail.get('teacherName') or row.get('teacher') or '').strip(),
        'orgId': detail.get('orgId') or row.get('orgId'),
    }


def format_time(milliseconds: Any) -> str:
    from datetime import datetime
    try:
        value = int(milliseconds)
    except (TypeError, ValueError):
        return ''
    if value <= 0:
        return ''
    try:
        return datetime.fromtimestamp(value / 1000).strftime('%Y-%m-%d %H:%M')
    except (OverflowError, OSError, ValueError):
        return ''
