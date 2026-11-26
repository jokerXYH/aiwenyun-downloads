"""把课堂数据整理成「课程 × 课次」矩阵（纯函数）。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

LESSON_PATTERNS = (
    re.compile(r'[-_－—]\s*(\d{1,3})\s*(?:β[123])?\s*$'),
    re.compile(r'第\s*(\d{1,3})\s*(?:节课|节|课)\s*$'),
    re.compile(r'[（(]\s*(\d{1,3})\s*[)）]\s*$'),
)
VARIANT_RE = re.compile(r'\s*β[123]\s*$')
TRAILING_PUNCT_RE = re.compile(r'[\s\-_－—:：]+$')


def lesson_from_topic(topic: str) -> int | None:
    text = (topic or '').strip()
    for pattern in LESSON_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            value = int(match.group(1))
        except (TypeError, ValueError):
            continue
        if 0 < value <= 999:
            return value
    return None


def course_from_topic(topic: str) -> str:
    text = (topic or '').strip()
    for pattern in LESSON_PATTERNS:
        match = pattern.search(text)
        if match:
            text = text[:match.start()]
            break
    text = VARIANT_RE.sub('', text)
    text = TRAILING_PUNCT_RE.sub('', text)
    return text.strip()


def natural_key(text: str) -> tuple:
    parts = re.split(r'(\d+)', text or '')
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in parts if part)


@dataclass
class Matrix:
    title: str
    labels: list[str] = field(default_factory=list)
    lessons: list[int] = field(default_factory=list)
    cells: dict[str, dict[int, str]] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)

    def code(self, label: str, lesson: int) -> str:
        return self.cells.get(label, {}).get(lesson, '')

    @property
    def width(self) -> int:
        return len(self.lessons)


def normalize_items(raw_items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        topic = str(raw.get('topic') or '').strip()
        groups = raw.get('groups') or []
        if isinstance(groups, dict):
            groups = list(groups.values())
        elif isinstance(groups, str):
            # 单个班级名不能被逐字符拆开；兼容调用方直接传字符串。
            groups = [groups]
        elif not isinstance(groups, (list, tuple, set)):
            groups = []
        groups = [str(name).strip() for name in groups
                  if name is not None and str(name).strip()]
        course = str(raw.get('course') or '').strip() or course_from_topic(topic)
        lesson = raw.get('lesson')
        if isinstance(lesson, str) and lesson.strip().isdigit():
            lesson = int(lesson.strip())
        if not isinstance(lesson, int) or lesson <= 0:
            lesson = lesson_from_topic(topic)
        code = str(raw.get('code') or '').strip()
        items.append({
            'topic': topic,
            'course': course,
            'groups': groups,
            'lesson': lesson,
            'code': code,
            'begin': raw.get('begin') or 0,
            'teacher': str(raw.get('teacher') or '').strip(),
            'orgId': raw.get('orgId'),
            'classId': raw.get('classId') or raw.get('id'),
            'note': str(raw.get('note') or '').strip(),
        })
    assign_missing_lessons(items)
    return items


def _sort_stamp(value: Any) -> int:
    """把 begin/classId 统一成可比较的整数，坏值按 0 处理。"""

    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def assign_missing_lessons(items: list[dict[str, Any]]) -> None:
    if all(item['lesson'] is not None for item in items):
        return
    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = item['course'] or item['topic']
        buckets.setdefault(key, []).append(item)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: (_sort_stamp(item.get('begin')),
                                      _sort_stamp(item.get('classId'))))
        used = {item['lesson'] for item in bucket
                if isinstance(item['lesson'], int) and item['lesson'] > 0}
        candidate = 1
        for item in bucket:
            if item['lesson'] is not None:
                continue
            # 只用尚未被显式讲次占用的编号，避免自动编号覆盖真实课次。
            while candidate in used:
                candidate += 1
            item['lesson'] = candidate
            used.add(candidate)
            candidate += 1


def build_matrix(items: list[dict[str, Any]], title: str = '邀请码') -> Matrix:
    matrix = Matrix(title=title)
    for item in items:
        label = item['course'] or item['topic']
        lesson = item['lesson']
        code = str(item.get('code') or '').strip()
        if not label or lesson is None:
            continue
        row = matrix.cells.setdefault(label, {})
        existing = row.get(lesson, '')
        if not existing:
            row[lesson] = code
        elif code and code != existing:
            matrix.conflicts.append({
                'label': label,
                'lesson': lesson,
                'kept': existing,
                'ignored': code,
                'classId': item.get('classId'),
            })
    matrix.labels = sorted(matrix.cells, key=natural_key)
    matrix.lessons = sorted(
        {lesson for row in matrix.cells.values() for lesson in row})
    return matrix


def build_matrices(items: list[dict[str, Any]]) -> list[Matrix]:
    matrix = build_matrix(items, '邀请码')
    return [matrix] if matrix.labels else []
