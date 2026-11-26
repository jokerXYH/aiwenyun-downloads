# -*- coding: utf-8 -*-
"""爱问云板书 PDF 导出（只读抓取，不改动爱问云客户端）。

在「邀请码导出」工具链（token_windows / net）基础上扩展：

用法::

    python banshu_export.py --liveclass 23095510            # 单课（默认按上课顺序）
    python banshu_export.py --liveclass 23095510 --sort type  # 单课（按板书类型分段）
    python banshu_export.py --quarter --classes "26秋7MAA12,26秋7MA+31"  # 一季度批量
    python banshu_export.py --quarter --from 2026-08-21 --to 2026-11-21

参数::

    --liveclass ID   单课导出（课堂编号，如 23095510）
    --file-id ID     单课导出（fileCommon._id，与 --liveclass 二选一）
    --quarter        批量导出一个季度（默认最近 92 天，可用 --from/--to 指定）
    --classes "A,B"  批量时只导出课堂名包含 A 或 B 的课（逗号分隔，缺省=全部）
    --sort time|type 排序方式（不写死，两者都支持）：
                     time = 按上课顺序（全部板书页按课堂内时间戳）
                     type = 按板书类型分段（独立板书页 -> 小黑板 -> 课件批注页）
    --out DIR        输出根目录（默认 桌面\\爱问云板书导出）

输出::

    <out>/<班级名>/<课堂名>.pdf     文件名与课堂名一致
    <out>/<班级名>/<课堂名>.pdf              --sort type 时（文件名不变）

板书数据链路（全部只读）::

    listRecord -> getBsSign({fileCommonId}) -> upime/bs/genBs
      -> banshu/info.plist (gzip JSON) -> 逐页 PNG (file.plaso.com)

PDF 为本文件内置的纯 Flate 写入器生成（无外部 PDF 依赖），写出后立即
回读校验：新版用整文件比对 + 页数检查（不再逐页解压），全部一致才报告成功。

导出加速：多节课并发（默认 3 路）+ 单课板书页并发（默认 4 路），下载线程
复用 HTTPS keep-alive 连接；genBs 用渐进的短轮询；每页下载后立刻压缩成
PDF 图像流，避免整节课 RGB 原图同时占内存。
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import http.client
import io
import json
import os
import re
import ssl
import sys
import threading
import time
import unicodedata
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from net import (ApiSession, RateLimitError, TokenExpired,  # noqa: E402
                 _ssl_context)
from token_model import TokenScanError           # noqa: E402

try:
    from PIL import Image as PilImage
except ImportError:  # pragma: no cover
    PilImage = None

BASE = 'https://www.aiwenyun.cn/'
LIST_RECORD = 'liveclassgo/api/v1/history/listRecord'
GROUP_LIST = 'gt/servlet/group/getTeacherGroup'
GET_BS_SIGN = 'yxt/servlet/sign/getBsSign'
GEN_BS = 'upime/bs/genBs'
UNCLASSIFIED_GROUP = '杂项/未分类'

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/89.0.4389.128 Safari/537.36')
ILLEGAL_FS = re.compile(r'[\\/:*?"<>|]')
LESSON_TAIL = re.compile(r'（[^）]*）\s*|-\d+$')
# 班级分组后缀：2026027MA+31β1 / β2 / β3 / α1 都归到 2026027MA+31
GREEK_SUFFIX = re.compile(r'[Α-Ωα-ω]\s*\d*$')
GREEK_CHAR = re.compile(r'[Α-Ωα-ω]')
# 从课堂名兜底识别班级时，只认「26秋7...」这类像班级的前缀
CLASS_LIKE = re.compile(
    r'^(?:20)?\d{2}(?:春|夏|秋|冬|暑|寒)\d|^\d{4}\d{2}\d')
# 「26秋」「2026秋」这类学期前缀；分组标签统一压成两位年份
TERM_PREFIX = re.compile(r'^(?:20)?(\d{2})\s*(春|夏|暑|秋|冬|寒)')
# 官方班级编码：2026027MA+31 = 2026 年第 02 期（具体季节从课堂名里学）
OFFICIAL_CODE = re.compile(r'^20(\d{2})(\d{2})')
# 没有学期文字时按上课月份兜底（7-8 月暑、9-12 月秋、1-2 月寒、3-6 月春）
_MONTH_SEASON = {1: '寒', 2: '寒', 3: '春', 4: '春', 5: '春',
                 6: '春', 7: '暑', 8: '暑', 9: '秋', 10: '秋',
                 11: '秋', 12: '秋'}
# 学期排序：同一日历年里按 寒→春→暑→秋 的先后；显示时倒序，最新学期在前
_SEASON_RANK = {'寒': 0, '春': 1, '暑': 2, '秋': 3}


class BatchCancelled(RuntimeError):
    """用户取消批量导出。"""


def _env_int(name: str, default: int, low: int = 1, high: int = 16) -> int:
    """读取并发等整数环境变量；非法值回退默认，避免界面被环境搞崩。"""

    try:
        value = int(os.environ.get(name, ''))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


# 导出加速参数（均可用环境变量覆盖，便于服务端限速时调小）：
# - 班级并发：多个课堂同时走 getBsSign/genBs/下载，互不等待；
# - 页面并发：单个课堂的多页 PNG 同时下载，复用线程内长连接；
# - genBs 轮询：服务端合成板书通常几秒内完成，原来的 15s 首等太保守。
CLASS_WORKERS = _env_int('AIWENYUN_BOARD_CLASS_WORKERS', 3, 1, 8)
PAGE_WORKERS = _env_int('AIWENYUN_BOARD_PAGE_WORKERS', 4, 1, 8)
LIVE_INFO_WORKERS = _env_int('AIWENYUN_LIVE_INFO_WORKERS', 3, 1, 8)
PAGE_TIMEOUT = 30
PAGE_RETRIES = 3
PDF_ZLIB_LEVEL = 3
BS_POLL_DELAYS = (0.0, 2.5, 4.0, 6.0, 8.0, 10.0, 12.0)
# listRecord 分页兜底：服务端 mark/total 异常时，不能让列表接口无限翻页。
MAX_RECORD_PAGES = 200


def log(*args: Any) -> None:
    """打印日志；windowed 打包（无控制台，stdout 为 None）时静默忽略。"""

    try:
        print(*args, flush=True)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 登录态

def scan_token() -> str:
    """只读扫描客户端进程内存，取登录态 token。"""

    if sys.platform != 'win32':
        raise TokenScanError(
            '当前系统不支持内存扫描；macOS 请用 --token 传入登录态。')
    # 延迟导入：token_windows 只适用于 Windows；macOS 版导入本模块时不应失败
    from token_windows import ScanStats, scan_tokens

    stats = ScanStats()
    candidates = scan_tokens(stats=stats)
    if not candidates:
        raise TokenScanError('没有在爱问云进程里找到登录态。')
    log('[token] 只读内存扫描 %.2fs，命中 %d 个候选' % (stats.elapsed, len(candidates)))
    return candidates[0].value


# ---------------------------------------------------------------- 命名

def sanitize(name: str) -> str:
    """去掉文件名非法字符。"""

    clean = ILLEGAL_FS.sub('_', (name or '').strip())
    return clean.rstrip('. ') or '未命名课堂'


def class_base(name: str) -> str:
    """把班级名/课堂名归一到「班」这一层。

    「2026027MA+31β1」→「2026027MA+31」
    「2026027MA+31α1（数形许）」→「2026027MA+31」
    「2026027MA+31作业班」保持原样（作业班是独立班级）。

    去掉教师注记、结尾讲次编号与 α/β1/β2/β3 这类分组后缀；
    并用 NFKC 统一全半角，便于匹配。
    """

    value = unicodedata.normalize('NFKC', (name or '').strip())
    value = re.sub(r'\([^)]*\)', '', value).strip()
    value = re.sub(r'-\d+$', '', value).strip()
    value = GREEK_SUFFIX.sub('', value).strip()
    return value


def class_group(short_desc: str) -> str:
    """从课堂名提取班级名（合并 α/β 分组后缀）。

    「26秋7MAA12（数形许）-11」→「26秋7MAA12」；
    「2026027MA+31β2（数形许）-11」→「2026027MA+31」。
    去掉（教师）注记、讲次编号与 α/β1 这类后缀；无讲次编号的按原名成组。
    """

    raw = (short_desc or '').strip()
    return sanitize(class_base(raw) or raw)


def term_label(text: str) -> str:
    """提取学期标签：26秋7MAA12 → 26秋；2026秋7MAA12 → 26秋。

    只认「年份 + 春/夏/暑/秋/冬/寒」开头的写法；认不出返回空串。
    """

    value = unicodedata.normalize('NFKC', (text or '').strip())
    match = TERM_PREFIX.match(value)
    if not match:
        return ''
    return '%s%s' % (match.group(1), match.group(2))


def class_short_label(name: str) -> str:
    """班级按钮/文件夹用的短名：只去掉学期与官方编码，保留「作业班」。

    「2026027MA+31β1」→「7MA+31」；
    「26秋7MAA12（数形许）」→「7MAA12」；
    「2026027MA+31作业班」→「7MA+31作业班」（作业班是独立班级，不能抹掉）。
    """

    value = class_base(name) or (name or '').strip()
    value = TERM_PREFIX.sub('', value).strip()
    value = re.sub(r'^20\d{4}', '', value).strip()
    return value or class_base(name) or (name or '').strip()


def class_folder_label(name: str) -> str:
    """反馈文件用的班级名：去掉学期和「作业班」，让同一班的教学/作业合到一起。

    「2026027MA+31作业班」→「7MA+31」；
    「26秋7MA+31（数形许）」→「7MA+31」。
    """

    value = class_short_label(name)
    for suffix in ('作业班', '班级'):
        if value.endswith(suffix):
            value = value[:-len(suffix)].strip()
    return value or class_short_label(name)


def class_output_dir(out_root: Path, group_name: str) -> Path:
    """班级输出目录；如果用户选的文件夹本身就是这个班，就不再套一层。

    这样选「D:\\导出\\7MA+31」导出时，文件直接落在这个已有文件夹里；
    选「D:\\导出」这种根目录时，才按班级新建子文件夹。
    """

    root = Path(out_root)
    target = sanitize(group_name)
    folder = class_folder_label(root.name)
    wanted = class_folder_label(group_name)
    if folder and wanted and _compact_name(folder) == _compact_name(wanted):
        return root
    return root / target


def term_from_time(ms: int) -> str:
    """按上课月份兜底推断学期：2026-11 → 26秋。"""

    if not ms:
        return ''
    try:
        moment = datetime.datetime.fromtimestamp(int(ms) / 1000)
    except (TypeError, ValueError, OSError):
        return ''
    season = _MONTH_SEASON.get(moment.month, '')
    if not season:
        return ''
    return '%02d%s' % (moment.year % 100, season)


def official_term_code(name: str) -> tuple[str, str] | None:
    """官方班级名里的 (两位年份, 期号)：2026027MA+31 → ('26', '02')。"""

    match = OFFICIAL_CODE.match(class_base(name))
    if not match:
        return None
    return match.group(1), match.group(2)


def learned_term_codes(groups: dict[str, list[int]],
                       records: list[dict]) -> dict[tuple[str, str], str]:
    """从课堂名里学「官方期号 → 季节」：202602 → 秋。

    官方班级名只有 202602 这种编码，季节靠同一班课堂名里的「26秋」
    反推。同一期号出现多个季节时取出现次数最多的。
    """

    votes: dict[tuple[str, str], dict[str, int]] = {}
    for name, indexes in (groups or {}).items():
        code = official_term_code(name)
        if code is None:
            continue
        for index in indexes:
            if not 0 <= index < len(records):
                continue
            term = term_label(records[index].get('shortDesc') or '')
            if not term:
                continue
            season = term[2:]
            seasons = votes.setdefault(code, {})
            seasons[season] = seasons.get(season, 0) + 1
    return {code: max(seasons, key=seasons.get)
            for code, seasons in votes.items()}


def group_term_label(name: str, records: list[dict] | None = None,
                     code_seasons: dict[tuple[str, str], str] | None = None
                     ) -> str:
    """一个班级对应的学期标签，优先课堂名，其次记录里的课堂名，再看编码/日期。

    官方班级名「2026027MA+31」本身只有编码，学期优先从同一班课堂名
    （「26秋7MA+31（数形许）-11」）里取；取不到时用同批数据学到的
    「202602 → 秋」映射，最后才按最新一节课的月份兜底。
    同一班跨学期补课时，以出现次数最多的学期为准，平票用最新一节课的学期。
    """

    direct = term_label(class_base(name))
    if direct:
        return direct

    counts: dict[str, int] = {}
    newest_term, newest_stamp = '', -1
    for record in records or []:
        term = term_label(record.get('shortDesc') or '')
        if not term:
            continue
        counts[term] = counts.get(term, 0) + 1
        stamp = record_time(record)
        if stamp > newest_stamp:
            newest_stamp, newest_term = stamp, term
    if counts:
        most = max(counts.values())
        winners = [term for term, count in counts.items() if count == most]
        if newest_term in winners:
            return newest_term
        return max(winners, key=group_sort_key)

    code = official_term_code(name)
    if code is not None and code_seasons and code in code_seasons:
        return code[0] + code_seasons[code]

    stamps = [record_time(record) for record in records or []]
    stamps = [stamp for stamp in stamps if stamp]
    if stamps:
        return term_from_time(max(stamps))
    return ''


def group_display_label(name: str,
                        records: list[dict] | None = None,
                        code_seasons: dict[tuple[str, str], str] | None = None
                        ) -> str:
    """班级在界面/文件夹里的显示名：学期 + 班级，如「26秋7MA+31」。

    认不出学期时退回官方班级名（如「2026027MA+31」），宁可长一点，
    也不要把两个学期的同名班混成一个。
    """

    base = class_base(name) or (name or '').strip()
    term = group_term_label(base, records, code_seasons)
    if not term:
        return base
    return term + class_short_label(base)


def group_display_names(groups: dict[str, list[int]],
                        records: list[dict]) -> dict[str, str]:
    """把官方班级名映射成「学期+班级」显示名。

    先整批学一次「官方期号 → 季节」，再逐班生成显示名；万一两个官方班
    压出同一个显示名（例如官方名与课堂名兜底名重复），这些班退回官方
    全名，保证按钮和导出文件夹不会混在一起。
    """

    code_seasons = learned_term_codes(groups, records)
    labels: dict[str, str] = {}
    for name, indexes in (groups or {}).items():
        rows = [records[index] for index in indexes
                if 0 <= index < len(records)]
        labels[name] = group_display_label(name, rows, code_seasons)
    counts: dict[str, int] = {}
    for label in labels.values():
        counts[label] = counts.get(label, 0) + 1
    for name, label in list(labels.items()):
        if counts.get(label, 0) > 1:
            labels[name] = class_base(name) or name
    return labels


def group_sort_key(label: str) -> tuple[int, int]:
    """按「学期」排序：年份大在前；同一年的寒假最早、秋季最晚。"""

    term = term_label(label or '')
    if not term:
        return (0, 0)
    return (int(term[:2]), _SEASON_RANK.get(term[2:], 0))


def _normalized_name(text: str) -> str:
    """班级名匹配用的归一化：全半角统一、空白折叠、忽略大小写。"""

    value = unicodedata.normalize('NFKC', text or '')
    return re.sub(r'\s+', ' ', value).strip().casefold()


def _compact_name(text: str) -> str:
    value = unicodedata.normalize('NFKC', text or '')
    return re.sub(r'\s+', '', value).casefold()


def _name_variants(name: str) -> list[str]:
    """班级名的匹配变体（原名、去掉注记/讲次/α-β 后缀后的班名）。"""

    raw = (name or '').strip()
    if not raw:
        return []
    base = class_base(raw)
    variants: list[str] = []
    for value in (raw, base):
        value = value.strip()
        if value and value not in variants:
            variants.append(value)
    return variants


def _boundary_ok(text: str, start: int, end: int) -> bool:
    """班级名前后的边界检查，避免 26秋7M 误命中 26秋7MAA12/批课。

    班级名后面允许直接跟 α/β 这类分组字母（2026027MA+31β1），
    这样用班名「2026027MA+31」也能命中它的 β/α 变体。
    """

    before = text[start - 1] if start > 0 else ''
    after = text[end] if end < len(text) else ''
    if before and before.isalnum():
        return False
    if after and after.isalnum() and not GREEK_CHAR.match(after):
        return False
    return True


def _contains_class_name(text: str, needle: str) -> bool:
    for haystack, target in ((text, needle),
                             (_compact_name(text), _compact_name(needle))):
        if not target:
            continue
        start = 0
        while True:
            position = haystack.find(target, start)
            if position < 0:
                break
            if _boundary_ok(haystack, position, position + len(target)):
                return True
            start = position + 1
    return False


def match_class_name(short_desc: str, class_names: list[str]) -> str:
    """在课堂名中匹配爱问云班级名；返回最长命中的原始班级名。

    匹配时会把班级名的 α/β 分组后缀去掉后再找一次，因此
    「2026027MA+31β2」既能命中全名，也能被「2026027MA+31」命中。
    匹配不到返回空字符串；要求完整边界，避免把「26秋7M」误当成
    「26秋7MAA12」或「26秋7M批课」的班级。
    """

    text = _normalized_name(short_desc)
    if not text:
        return ''
    best = ''
    best_len = 0
    for raw_name in class_names or []:
        for variant in _name_variants(raw_name):
            needle = _normalized_name(variant)
            if (len(needle) >= 2 and len(needle) > best_len
                    and _contains_class_name(text, needle)):
                best = raw_name.strip()
                best_len = len(needle)
    return best


def _is_lesson_admin_name(name: str) -> bool:
    """没有班级列表时的兜底：备课/批课是课次系列，不当作正式班级。"""

    return any(word in (name or '') for word in ('备课', '批课'))


def _is_plausible_class(name: str) -> bool:
    """课堂名兜底分组的准入：只认「26秋7…」这类像班级的前缀。"""

    return bool(CLASS_LIKE.match(_normalized_name(name)))


def fallback_class_names(records: list[dict]) -> list[str]:
    """班级列表不可用时，从板书记录里提取可用于分组的班级名。

    先看记录自带的班级/群组字段；没有时再从课堂名提取，并把
    「7M批课 / 7M备课」这类课次系列、以及「专题9斗课」这类不像班级
    的名字排除，留给「杂项/未分类」。
    """

    names: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, dict):
            for key in ('name', 'groupName', 'className', 'title'):
                if value.get(key):
                    add(value[key])
                    break
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return
        text = str(value or '').strip()
        if (text and text not in names and len(text) <= 80
                and not re.fullmatch(r'[\d\s:_-]+', text)):
            names.append(text)

    for record in records or []:
        for key in ('className', 'groupName', 'classNames', 'groupNames',
                    'groups'):
            add(record.get(key))
    if names:
        return names

    for record in records or []:
        group = class_group(record.get('shortDesc') or '')
        if (group and group != '未命名课堂'
                and _is_plausible_class(group)
                and not _is_lesson_admin_name(group)):
            add(group)
    return names


def extract_class_names(payload: Any) -> list[str]:
    """从 getTeacherGroup 响应中提取班级名（兼容常见嵌套结构）。"""

    names: list[str] = []

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        text = value.strip()
        if (not text or len(text) > 80 or text.lower().startswith('http')
                or re.fullmatch(r'[\d\s:_-]+', text)):
            return
        if text not in names:
            names.append(text)

    name_keys = {'name', 'groupName', 'className', 'shortName', 'title',
                 'groupTitle', 'group_name', 'class_name'}
    nested_keys = {'obj', 'data', 'result', 'list', 'rows', 'items',
                   'records', 'groups', 'groupList', 'group_list',
                   'children', 'childList'}

    def walk(value: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key)
                if key_text in name_keys and isinstance(item, str):
                    add(item)
                elif key_text in nested_keys:
                    if (key_text in {'groups', 'groupList', 'group_list'}
                            and isinstance(item, dict)):
                        for sub_item in item.values():
                            if isinstance(sub_item, str):
                                add(sub_item)
                            else:
                                walk(sub_item, depth + 1)
                    else:
                        walk(item, depth + 1)
                elif isinstance(item, str) and (
                        key_text.isdigit() or key_text.lower() in
                        {'group', 'class', 'value', 'label'}):
                    # 兼容 {"1": "26秋7MAA12"} 这类 id -> 名称映射
                    add(item)
                elif isinstance(item, (dict, list)):
                    walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    add(item)
                elif isinstance(item, (dict, list)):
                    walk(item, depth + 1)

    walk(payload)
    return names


def fetch_class_names(session: ApiSession) -> list[str]:
    """只读读取爱问云班级列表；失败时回退到课程表里的群组名。"""

    attempts = (
        {},
        {'indexStart': 0, 'pageSize': 200},
    )
    responded = False
    for body in attempts:
        try:
            payload = session.post(GROUP_LIST, body, request_timeout=6.0)
            responded = True
        except TokenExpired:
            raise
        except Exception:  # noqa: BLE001
            continue
        names = extract_class_names(payload)
        if names:
            return names

    # 接口本身不可用时不再额外请求课程表，避免拖慢板书列表。
    if not responded:
        return []

    # 兜底：课程表的 groups 字段通常就是爱问云的班级/群组名称。
    try:
        rows = session.fetch_timetable(page_size=100, max_pages=5)
    except TokenExpired:
        raise
    except Exception:  # noqa: BLE001
        return []
    names: list[str] = []
    for row in rows:
        for name in row.get('groups') or []:
            text = str(name or '').strip()
            if text and text not in names:
                names.append(text)
    return names


GROUP_NAMES_KEY = '_groupNames'


def record_group_names(record: dict) -> list[str]:
    """读取 liveInfo 里带的官方班级名（由 enrich_record_groups 写入）。"""

    value = record.get(GROUP_NAMES_KEY)
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item or '').strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


def enrich_record_groups(session: ApiSession,
                         records: list[dict],
                         on_progress=None,
                         cancel_event=None) -> dict[int, list[str]]:
    """用 liveInfo 接口给每节板书记录补上官方班级名（只读）。

    liveInfo 的 ``groups`` 形如 ``{3874277: '2026027MA+31β1', ...}``，
    这是把「26秋7MA+31」课堂精确对应到「2026027MA+31」班级的可靠来源。
    返回 ``{liveclass: [班级名]}``；接口失败时由调用方决定是否忽略。
    记录很多（最近三年）时按 50 个一批请求，``on_progress(done, total)``
    让界面能报出「正在对应官方班级 150 / 600」。
    """

    wanted: list[int] = []
    for record in records or []:
        try:
            liveclass = int(record.get('liveclass') or 0)
        except (TypeError, ValueError):
            continue
        if liveclass and liveclass not in wanted:
            wanted.append(liveclass)
    if not wanted:
        return {}

    details: dict[int, dict] = {}
    total = len(wanted)
    chunks = [wanted[start:start + 50]
              for start in range(0, total, 50)]
    if cancel_event is not None and cancel_event.is_set():
        raise BatchCancelled('已取消列出板书。')

    if len(chunks) <= 1 or LIVE_INFO_WORKERS <= 1:
        done = 0
        for chunk in chunks:
            if cancel_event is not None and cancel_event.is_set():
                raise BatchCancelled('已取消列出板书。')
            details.update(session.fetch_live_info(chunk))
            done += len(chunk)
            if on_progress is not None:
                try:
                    on_progress(done, total)
                except Exception:  # noqa: BLE001
                    pass
    else:
        pool = ThreadPoolExecutor(
            max_workers=min(LIVE_INFO_WORKERS, len(chunks)),
            thread_name_prefix='liveinfo')
        futures = [pool.submit(session.fetch_live_info, chunk)
                   for chunk in chunks]
        done = 0
        try:
            for chunk, future in zip(chunks, futures):
                if cancel_event is not None and cancel_event.is_set():
                    raise BatchCancelled('已取消列出板书。')
                details.update(future.result())
                done += len(chunk)
                if on_progress is not None:
                    try:
                        on_progress(done, total)
                    except Exception:  # noqa: BLE001
                        pass
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    mapping: dict[int, list[str]] = {}
    for liveclass, info in (details or {}).items():
        groups = (info or {}).get('groups') or {}
        names: list[str] = []
        if isinstance(groups, dict):
            names = [str(value).strip() for value in groups.values()
                     if str(value or '').strip()]
        elif isinstance(groups, (list, tuple)):
            for item in groups:
                if isinstance(item, dict):
                    value = item.get('groupName') or item.get('name') or ''
                else:
                    value = item
                text = str(value or '').strip()
                if text:
                    names.append(text)
        unique: list[str] = []
        for name in names:
            if name not in unique:
                unique.append(name)
        if unique:
            mapping[int(liveclass)] = unique

    for record in records or []:
        try:
            liveclass = int(record.get('liveclass') or 0)
        except (TypeError, ValueError):
            continue
        names = mapping.get(liveclass)
        if names:
            record[GROUP_NAMES_KEY] = list(names)
    return mapping


def _canonical_class_names(class_names: list[str] | None) -> list[str]:
    """官方班级名归一成「班」：α/β 变体合并，保留首次出现的顺序。"""

    canonical: list[str] = []
    seen: set[str] = set()
    for raw in class_names or []:
        base = class_base(raw) or (raw or '').strip()
        key = _normalized_name(base)
        if base and key not in seen:
            seen.add(key)
            canonical.append(base)
    return canonical


def pick_group_base(bases: list[str], short_desc: str = '') -> str:
    """一节课可能挂在 β1、β3 等多个分组下：优先选课堂名里出现的班。"""

    unique: list[str] = []
    for base in bases:
        base = (base or '').strip()
        if base and base not in unique:
            unique.append(base)
    if not unique:
        return ''
    if len(unique) == 1:
        return unique[0]
    text = _compact_name(short_desc)
    for base in unique:
        if _compact_name(base) in text:
            return base
    return max(unique, key=len)


def record_group_base(record: dict,
                      class_names: list[str] | None = None) -> str:
    """返回一条记录应归入的班级（已合并 α/β 后缀）；分不出返回空串。

    优先用 liveInfo 的官方班级名；没有时再拿课堂名去匹配班级列表。
    """

    bases = [class_base(name) for name in record_group_names(record)]
    base = pick_group_base(bases, record.get('shortDesc') or '')
    if base:
        return base
    matched = match_class_name(record.get('shortDesc') or '',
                               _canonical_class_names(class_names))
    return class_base(matched) if matched else ''


def record_matches_class_list(record: dict,
                              class_names: list[str] | None) -> bool:
    """课堂班级代号是否落在用户班级列表里；列表为空时不筛选。

    只复用现有归一化规则（学期、α/β 分组、作业班后缀）和
    ``match_class_name``，不扩展新的班级代号匹配规则。
    """

    names = _canonical_class_names(class_names)
    if not names:
        return True
    allowed = {class_folder_label(name) for name in names if name}
    candidates = list(record_group_names(record))
    candidates.append(class_group(record.get('shortDesc') or ''))
    for candidate in candidates:
        if candidate and class_folder_label(candidate) in allowed:
            return True
    return bool(match_class_name(record.get('shortDesc') or '', names))


def group_board_records(records: list[dict],
                        class_names: list[str] | None = None
                        ) -> dict[str, list[int]]:
    """按爱问云班级名分组，返回 ``{班级名: [记录下标]}``。

    优先使用 liveInfo 精确映射的官方班级名（先调用
    enrich_record_groups）；没有时再拿课堂名匹配班级列表。两者都
    匹配不到的记录统一放入「杂项/未分类」。α/β1/β2/β3 等变体会合并
    到同一个班（如「2026027MA+31」）。
    """

    names = _canonical_class_names(class_names)
    if not names:
        names = _canonical_class_names(fallback_class_names(records))
    groups: dict[str, list[int]] = {}
    for index, record in enumerate(records or []):
        group = record_group_base(record, names) or UNCLASSIFIED_GROUP
        groups.setdefault(group, []).append(index)
    if UNCLASSIFIED_GROUP in groups:
        # 未分类永远排在最后，避免与正式班级混排
        groups[UNCLASSIFIED_GROUP] = groups.pop(UNCLASSIFIED_GROUP)
    return groups


def record_time(record: dict) -> int:
    """板书记录的创建时间（毫秒）；缺少字段返回 0。"""

    value = ((record.get('fileCommon') or {}).get('createTime')
             or record.get('createTime') or 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def sort_records_newest_first(records: list[dict]) -> list[dict]:
    """按上课时间倒序：最新的在最上面。"""

    return sorted(records or [], key=record_time, reverse=True)


def ordered_group_names(groups: dict[str, list[int]],
                        records: list[dict],
                        labels: dict[str, str] | None = None) -> list[str]:
    """班级顺序：先按学期（26秋 → 26暑 → 25秋…），同学期最新一节课的班在前；
    杂项永远最后。

    ``labels`` 是可选的「官方班级名 → 显示名」映射；给了就按显示名里的
    学期排序，没给则退回原来的「最新一节课」排序。
    """

    labels = labels or {}

    def latest(indexes: list[int]) -> int:
        return max((record_time(records[index]) for index in indexes
                    if 0 <= index < len(records)), default=0)

    official = [name for name in groups if name != UNCLASSIFIED_GROUP]
    official.sort(
        key=lambda name: (group_sort_key(labels.get(name, name)),
                          latest(groups[name]), name),
        reverse=True)
    if UNCLASSIFIED_GROUP in groups:
        official.append(UNCLASSIFIED_GROUP)
    return official


# ---------------------------------------------------------------- 数据接口

class PageDownloader:
    """线程内复用 HTTPS 长连接的图片下载器。

    板书页是一堆小 PNG，原来的 ``urllib.urlopen`` 每页都重新做一次 TLS
    握手（100~300ms），页数多的课堂一半时间花在握手。这里让每个下载线程
    复用自己的 keep-alive 连接，并在空闲过久时主动换新，避免半死连接。
    """

    def __init__(self, timeout: int = PAGE_TIMEOUT,
                 retries: int = PAGE_RETRIES) -> None:
        self.timeout = max(5, int(timeout))
        self.retries = max(1, int(retries))
        self._context = _ssl_context()
        self._local = threading.local()

    # -- 连接 --------------------------------------------------------
    def _close(self) -> None:
        connection = getattr(self._local, 'connection', None)
        self._local.connection = None
        self._local.key = None
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass

    def _connection(self, host: str, port: int) -> http.client.HTTPSConnection:
        key = (host, port)
        connection = getattr(self._local, 'connection', None)
        if connection is not None and getattr(self._local, 'key', None) != key:
            self._close()
            connection = None
        if connection is None:
            connection = http.client.HTTPSConnection(
                host, port, timeout=self.timeout, context=self._context)
            self._local.connection = connection
            self._local.key = key
        return connection

    # -- 下载 --------------------------------------------------------
    def get(self, url: str, cancel_event=None,
            timeout: float | None = None) -> bytes:
        target = urlparse(url)
        if target.scheme.lower() != 'https' or not target.hostname:
            raise RuntimeError('只允许 HTTPS 下载：%s' % url)
        path = quote(target.path or '/', safe="/?=&%:+,;@!$'()*[]~")
        if target.query:
            path += '?' + quote(target.query, safe="=&%:+,;@!$'()*[]~/")
        host = target.hostname
        port = target.port or 443

        last: Exception | None = None
        for attempt in range(self.retries):
            if cancel_event is not None and cancel_event.is_set():
                raise BatchCancelled('已取消板书导出。')
            last_used = getattr(self._local, 'last_used', 0.0)
            if last_used and time.monotonic() - last_used > 20.0:
                self._close()
            connection = self._connection(host, port)
            connection.timeout = timeout or self.timeout
            try:
                connection.request('GET', path, headers={
                    'User-Agent': USER_AGENT,
                    'Accept': 'image/*,*/*',
                    'Connection': 'keep-alive',
                })
                response = connection.getresponse()
                data = response.read()
                if response.will_close:
                    self._close()
                if response.status in (301, 302, 303, 307, 308):
                    location = response.getheader('Location') or ''
                    if location and attempt + 1 < self.retries:
                        return self.get(urljoin(url, location), cancel_event,
                                        timeout)
                    raise RuntimeError('下载重定向失败：%s' % url)
                if response.status == 429:
                    retry_after = response.getheader('Retry-After') or ''
                    try:
                        wait = min(5.0, max(0.5, float(retry_after)))
                    except (TypeError, ValueError):
                        wait = 1.0
                    last = RuntimeError('下载过于频繁（HTTP 429）')
                    self._wait(wait, cancel_event)
                    continue
                if response.status != 200:
                    raise RuntimeError('下载失败 HTTP %d：%s'
                                       % (response.status, url))
                self._local.last_used = time.monotonic()
                return data
            except BatchCancelled:
                raise
            except (http.client.HTTPException, OSError, ssl.SSLError,
                    RuntimeError) as error:
                self._close()
                last = error
                if attempt + 1 < self.retries:
                    self._wait(min(2.0, 0.4 * (attempt + 1)), cancel_event)
        raise RuntimeError('下载失败 %s（%s）' % (url, last))

    @staticmethod
    def _wait(seconds: float, cancel_event=None) -> None:
        if cancel_event is not None:
            if cancel_event.wait(seconds):
                raise BatchCancelled('已取消板书导出。')
        else:
            time.sleep(seconds)


def http_get(url: str, timeout: int = 60, retries: int = 3,
             downloader: PageDownloader | None = None,
             cancel_event=None) -> bytes:
    """兼容旧调用：下载一个 URL（默认临时新建下载器）。"""

    client = downloader or PageDownloader(timeout=timeout, retries=retries)
    return client.get(url, cancel_event=cancel_event, timeout=timeout)


def fetch_records(session: ApiSession, date_from_ms: int, date_to_ms: int,
                  page_size: int = 50,
                  cancel_event=None) -> list[dict]:
    """分页拉取一个时间段的全部历史课堂记录。

    最近三年/全部可能有很多页；给了 ``cancel_event`` 就在每页之间检查，
    用户点「取消」后不必等剩下所有页拉完。
    """

    rows: list[dict] = []
    index = 0
    total = None
    page_size = max(1, int(page_size))
    for _page in range(MAX_RECORD_PAGES):
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消列出板书。')
        resp = session.post(LIST_RECORD, {
            'dateFrom': int(date_from_ms), 'dateTo': int(date_to_ms),
            'indexStart': index, 'pageSize': page_size,
            'groupId': None, 'xx': ''})
        if not isinstance(resp, dict):
            raise RuntimeError('listRecord 响应格式异常')
        code = resp.get('code')
        if not (code in (0, None) or str(code) == '0'):
            raise RuntimeError('listRecord 返回 code=%s' % code)
        obj = resp.get('obj') or {}
        if not isinstance(obj, dict):
            raise RuntimeError('listRecord 响应格式异常')
        chunk = obj.get('list') or []
        if not isinstance(chunk, list):
            raise RuntimeError('listRecord 响应格式异常')
        rows.extend(item for item in chunk if isinstance(item, dict))
        raw_total = obj.get('total')
        if raw_total is not None:
            try:
                total = int(raw_total)
            except (TypeError, ValueError):
                pass
        if len(chunk) < page_size or (total is not None and len(rows) >= total):
            return rows
        index += page_size
    raise RuntimeError('listRecord 分页超过上限（%d 页），已停止以免无限请求'
                       % MAX_RECORD_PAGES)


class BsUnavailable(RuntimeError):
    """该课没有可导出的板书（未生成/生成中/无板书）。"""


def bs_manifest(session: ApiSession, file_common_id: str,
                gen_bs_type: int = 0,
                downloader: PageDownloader | None = None,
                cancel_event=None,
                poll_delays: tuple[float, ...] = BS_POLL_DELAYS
                ) -> tuple[dict, str]:
    """getBsSign -> genBs -> 下载板书包 info.plist，返回 (清单, 资源前缀)。

    genBs 是懒生成接口：首次请求触发服务端合成，status=1 表示还在生成，
    status=2 表示就绪。旧实现第一次等待 15 秒，即使服务端 3 秒就绪也要
    白等；这里改为 2.5~12 秒的渐进轮询，总预算约 42 秒，提前就绪就提前
    返回。轮询等待期间可被取消。
    """

    resp = session.post(GET_BS_SIGN, {'fileCommonId': file_common_id,
                                       'genBsType': gen_bs_type})
    if not isinstance(resp, dict):
        raise BsUnavailable('getBsSign 响应格式异常')
    obj = resp.get('obj') or {}
    code = resp.get('code')
    if (not (code in (0, None) or str(code) == '0')
            or not isinstance(obj, dict) or not obj.get('signature')):
        raise BsUnavailable('getBsSign 失败 code=%s' % code)
    payload = {k: v for k, v in obj.items() if k != 'signature'}
    payload['signature'] = obj['signature']

    last: dict = {}
    gs: dict = {}
    for attempt, delay in enumerate(poll_delays, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消板书导出。')
        if delay:
            if cancel_event is not None:
                if cancel_event.wait(delay):
                    raise BatchCancelled('已取消板书导出。')
            else:
                time.sleep(delay)
        resp = session.post(GEN_BS, payload)
        if not isinstance(resp, dict):
            raise BsUnavailable('genBs 响应格式异常')
        code = resp.get('code')
        if not (code in (0, None) or str(code) == '0'):
            raise BsUnavailable('genBs 失败 code=%s' % code)
        item = resp.get('obj') or {}
        if not isinstance(item, dict):
            raise BsUnavailable('genBs 响应格式异常')
        last = gs = item
        if str(gs.get('status')) == '2':
            break
    else:
        raise BsUnavailable('板书生成中，稍后重试（status=%s）'
                            % last.get('status'))

    url = str(gs.get('url') or '').strip()
    if not url:
        raise BsUnavailable('genBs 未返回板书包地址')
    raw = http_get(url, downloader=downloader,
                   cancel_event=cancel_event)
    try:
        data = gzip.decompress(raw)
    except OSError:
        data = raw
    try:
        manifest = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BsUnavailable('板书清单不是有效 JSON') from error
    if not isinstance(manifest, dict):
        raise BsUnavailable('板书清单格式异常')
    return manifest, str(gs.get('pathprefix') or '').rstrip('/')


# ---------------------------------------------------------------- 板书页收集

IMAGE_EXT = ('.png', '.jpg', '.jpeg', '.gif', '.bmp')


def collect_pages(manifest: dict) -> list[dict]:
    """从板书清单收集全部板书页。

    每页: {kind, time, file, pageno, cw}
    kind: board=独立板书页, annotate=课件批注页, small=小黑板
    time: 课堂内相对毫秒（上课顺序依据）
    """

    pages: list[dict] = []
    if not isinstance(manifest, dict):
        return pages
    cmds = manifest.get('cmds') or []
    if not isinstance(cmds, list):
        cmds = []
    for seq, cmd in enumerate(cmds):
        # cmds 页: [0, 215, 页号, 0, "attach1", 时间ms, "1.png", 字节?]
        if isinstance(cmd, list) and len(cmd) >= 7 and isinstance(cmd[6], str):
            name = cmd[6]
            if not name.lower().endswith(IMAGE_EXT):
                continue
            stamp = cmd[5] if isinstance(cmd[5], (int, float)) else 0
            pages.append({'kind': 'board', 'time': stamp, 'file': name,
                          'pageno': seq + 1, 'cw': 0, 'seq': seq})

    coursewares = manifest.get('coursewares') or []
    if not isinstance(coursewares, list):
        coursewares = []
    for cw_index, cw in enumerate(coursewares, 1):
        if not isinstance(cw, dict):
            continue
        entries = cw.get('bs') or []
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get('path') or ''
            if not isinstance(name, str) or not name.lower().endswith(IMAGE_EXT):
                continue
            stamp = entry.get('BsTime', 0) or 0
            pages.append({'kind': 'annotate', 'time': stamp, 'file': name,
                          'pageno': entry.get('pageno', 0), 'cw': cw_index,
                          'seq': stamp})

    smallboards = manifest.get('smallboards') or []
    if not isinstance(smallboards, list):
        smallboards = []
    for seq, small in enumerate(smallboards):
        # 小黑板条目结构未见实例，按含图片字段兜底解析
        names = []
        if isinstance(small, dict):
            for value in small.values():
                if isinstance(value, str) and value.lower().endswith(IMAGE_EXT):
                    names.append(value)
                elif isinstance(value, list):
                    names.extend(v for v in value
                                 if isinstance(v, str) and v.lower().endswith(IMAGE_EXT))
        stamp = 0
        if isinstance(small, dict):
            stamp = small.get('BsTime') or small.get('time') or 0
        for name in names:
            pages.append({'kind': 'small', 'time': stamp, 'file': name,
                          'pageno': 0, 'cw': 0, 'seq': seq})

    return pages


KIND_ORDER = {'board': 0, 'small': 1, 'annotate': 2}


def order_pages(pages: list[dict], sort_mode: str) -> list[dict]:
    """排序，两种模式必须保持独立。

    time=按讲课顺序：所有类型按课堂时间线交错，和爱问云“按讲课顺序下载”一致；
    type=按板书类型：独立板书 -> 小黑板 -> 课件批注，类型内保留原始页序
    （课件按 cw/pageno，避免把同一份课件的页打乱）。
    """

    if sort_mode == 'type':
        return sorted(pages, key=lambda p: (KIND_ORDER.get(p['kind'], 9),
                                            p['cw'], p['pageno'], p['time']))
    return sorted(pages, key=lambda p: (p['time'], p['seq']))


# ---------------------------------------------------------------- 图像获取

@dataclass
class PdfPage:
    """一页已解码并压缩好的板书图片（PDF 图像流）。"""

    name: str
    width: int
    height: int
    stream: bytes


def _page_url(pathprefix: str, name: str) -> str:
    prefix = (pathprefix or '').rstrip('/')
    return '%s/%s' % (prefix, name.lstrip('/')) if prefix else name


def _prepare_one_page(downloader: PageDownloader, pathprefix: str,
                      name: str, cancel_event=None) -> PdfPage:
    data = downloader.get(_page_url(pathprefix, name),
                          cancel_event=cancel_event)
    try:
        with PilImage.open(io.BytesIO(data)) as image:
            rgb = image.convert('RGB')
            width, height = rgb.size
            raw = rgb.tobytes()
    except Exception as error:  # noqa: BLE001
        raise RuntimeError('板书页解码失败 %s（%s）' % (name, error)) from error
    # 解码后立刻压缩，只保留压缩流，避免整节课几十页 RGB 原图同时占内存。
    return PdfPage(name, width, height, zlib.compress(raw, PDF_ZLIB_LEVEL))


def prepare_page_streams(pathprefix: str, pages: list[dict],
                         workers: int = PAGE_WORKERS,
                         cancel_event=None,
                         downloader: PageDownloader | None = None,
                         on_page=None) -> list[PdfPage]:
    """并发下载去重后的板书页并压缩，返回与 ``pages`` 对应的序列。

    ``on_page(done, total, name)`` 在每页完成时回调，供进度面板显示
    “当前课堂 7/18 页”。下载线程复用各自的 HTTPS 长连接；单页失败会
    取消其余排队任务并抛出，交给上层重试或记录失败。
    """

    if PilImage is None:
        raise RuntimeError('需要 Pillow（本机已安装）来读取板书 PNG。')

    client = downloader or PageDownloader()
    unique: list[str] = []
    for page in pages:
        name = page['file']
        if name not in unique:
            unique.append(name)
    total = len(unique)
    results: dict[str, PdfPage] = {}

    def worker(name: str) -> PdfPage:
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消板书导出。')
        return _prepare_one_page(client, pathprefix, name, cancel_event)

    workers = max(1, min(int(workers or 1), total or 1))
    if workers == 1 or total <= 1:
        for done, name in enumerate(unique, 1):
            results[name] = worker(name)
            if on_page is not None:
                on_page(done, total, name)
    else:
        pool = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix='banshu-page')
        futures = {pool.submit(worker, name): name for name in unique}
        done = 0
        try:
            for future in as_completed(futures):
                name = futures[future]
                results[name] = future.result()
                done += 1
                if on_page is not None:
                    on_page(done, total, name)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    return [results[page['file']] for page in pages]


def load_page_images(pathprefix: str, pages: list[dict],
                     delay: float = 0.0, workers: int = PAGE_WORKERS,
                     cancel_event=None,
                     downloader: PageDownloader | None = None,
                     on_page=None
                     ) -> list[tuple[str, bytes, int, int]]:
    """下载去重后的板书页图片，返回 [(file, RGB字节, 宽, 高)]。

    保留此接口供单页调试；正式导出走 :func:`prepare_page_streams`，
    只保留压缩后的 PDF 图像流，内存占用小得多。
    """

    if PilImage is None:
        raise RuntimeError('需要 Pillow（本机已安装）来读取板书 PNG。')

    client = downloader or PageDownloader()
    unique: list[str] = []
    for page in pages:
        if page['file'] not in unique:
            unique.append(page['file'])
    total = len(unique)
    cache: dict[str, tuple[bytes, int, int]] = {}

    def worker(name: str) -> tuple[bytes, int, int]:
        data = client.get(_page_url(pathprefix, name),
                          cancel_event=cancel_event)
        with PilImage.open(io.BytesIO(data)) as image:
            rgb = image.convert('RGB')
            return rgb.tobytes(), rgb.width, rgb.height

    workers = max(1, min(int(workers or 1), total or 1))
    if workers == 1 or total <= 1:
        for done, name in enumerate(unique, 1):
            cache[name] = worker(name)
            if delay:
                time.sleep(delay)
            if on_page is not None:
                on_page(done, total, name)
    else:
        pool = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix='banshu-page')
        futures = {pool.submit(worker, name): name for name in unique}
        done = 0
        try:
            for future in as_completed(futures):
                name = futures[future]
                cache[name] = future.result()
                done += 1
                if on_page is not None:
                    on_page(done, total, name)
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    return [(page['file'],) + cache[page['file']] for page in pages]


# ---------------------------------------------------------------- PDF 写入

def build_pdf_pages(pages: list[PdfPage], dedupe: bool = True
                    ) -> tuple[bytes, list[dict]]:
    """把已压缩的页面流写成单文件 PDF。

    纯 zlib(FlateDecode) 写入器，不依赖外部 PDF 库。
    同名页面复用同一个图像对象，减少重复页面的体积和处理时间。
    ``dedupe=False`` 时每页各写一个图像对象（兼容旧的 build_pdf 调用）。
    返回 ``(PDF字节, [{offset,width,height,length}])``。
    """

    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    image_id_by_name: dict[str, int] = {}
    meta_by_obj: dict[int, dict] = {}
    stream_rel: dict[int, int] = {}
    page_image_ids: list[int] = []

    for page in pages:
        key = page.name if dedupe else ''
        image_obj = image_id_by_name.get(key) if key else None
        if image_obj is None:
            comp = page.stream
            header = ('<< /Type /XObject /Subtype /Image /Width %d /Height %d '
                      '/ColorSpace /DeviceRGB /BitsPerComponent 8 '
                      '/Filter /FlateDecode /Length %d >>\nstream\n'
                      % (page.width, page.height, len(comp))).encode()
            body = header + comp + b'\nendstream'
            image_obj = add(body)
            stream_rel[image_obj] = body.find(b'stream\n') + len(b'stream\n')
            meta_by_obj[image_obj] = {
                'length': len(comp), 'width': page.width,
                'height': page.height}
            if key:
                image_id_by_name[key] = image_obj
        page_image_ids.append(image_obj)

    pages_obj = add(b'')  # 占位，稍后回填
    page_obj_ids: list[int] = []
    for page, image_obj in zip(pages, page_image_ids):
        stream = ('q %d 0 0 %d 0 0 cm /Im Do Q'
                  % (page.width, page.height)).encode()
        content_obj = add(('<< /Length %d >>\nstream\n' % len(stream)).encode()
                          + stream + b'\nendstream')
        page_obj_ids.append(add(
            ('<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] '
             '/Resources << /ProcSet [/PDF /ImageC] '
             '/XObject << /Im %d 0 R >> >> /Contents %d 0 R >>'
             % (pages_obj, page.width, page.height,
                image_obj, content_obj)).encode()))

    kids = ' '.join('%d 0 R' % n for n in page_obj_ids)
    objs[pages_obj - 1] = ('<< /Type /Pages /Kids [%s] /Count %d >>'
                           % (kids, len(page_obj_ids))).encode()
    catalog = add(('<< /Type /Catalog /Pages %d 0 R >>' % pages_obj).encode())

    buf = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for number, body in enumerate(objs, 1):
        offsets.append(len(buf))
        prefix = b'%d 0 obj\n' % number
        buf += prefix + body + b'\nendobj\n'
        meta = meta_by_obj.get(number)
        if meta is not None:
            # 直接按对象内的 stream 标记定位，避免对大 PDF 反复全文件扫描。
            meta['offset'] = offsets[number] + len(prefix) + stream_rel[number]
    xref_at = len(buf)
    buf += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1)
    for offset in offsets[1:]:
        buf += b'%010d 00000 n \n' % offset
    buf += (b'trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n'
            % (len(objs) + 1, catalog, xref_at))

    image_meta = [meta_by_obj[obj_id] for obj_id in sorted(meta_by_obj)]
    return bytes(buf), image_meta


def build_pdf(images: list[tuple[bytes, int, int]]) -> tuple[bytes, list[dict]]:
    """兼容旧接口：把 RGB 图像序列写成 PDF。

    历史调用方传 ``(name, raw, width, height)``，新类型标注是
    ``(raw, width, height)``；两种都接受，避免按文档调用时直接解包失败。
    """

    pages = []
    for image in images:
        if len(image) == 4:
            _name, raw, width, height = image
        else:
            raw, width, height = image
        pages.append(PdfPage('', width, height,
                             zlib.compress(raw, PDF_ZLIB_LEVEL)))
    return build_pdf_pages(pages, dedupe=False)


def validate_pdf(pdf_path: Path, images: list[tuple[bytes, int, int]] | None = None,
                 meta: list[dict] | None = None,
                 expected_bytes: bytes | None = None,
                 pages: int | None = None) -> bool:
    """回读校验 PDF 头尾、页数与内容。

    新路径用 ``expected_bytes`` 做整文件比对（只读一次、不做 zlib 解压），
    比逐页解压再比对快一个量级；旧的逐页解压路径保留给调试/外部调用。
    """

    data = pdf_path.read_bytes()
    if not data.startswith(b'%PDF-') or not data.rstrip().endswith(b'%%EOF'):
        log('    !! PDF 头尾结构异常')
        return False
    expected_count = pages if pages is not None else (
        len(images) if images is not None else None)
    count = data.count(b'/Type /Page ')
    if expected_count is not None and count != expected_count:
        log('    !! 页数不符：PDF=%d 预期=%d' % (count, expected_count))
        return False
    if expected_bytes is not None:
        if data != expected_bytes:
            log('    !! PDF 内容与生成结果不一致')
            return False
        return True
    if images is None or meta is None:
        return True
    for index, ((_name, raw, _width, _height), m) in enumerate(zip(images, meta)):
        chunk = data[m['offset']:m['offset'] + m['length']]
        if zlib.decompress(chunk) != raw:
            log('    !! 第 %d 页图像流与源图不一致' % (index + 1))
            return False
    return True


def render_blank_check(pdf_path: Path) -> bool | None:
    """用 Edge 无头渲染 PDF 首屏并检查像素方差，判断是否空白。

    返回 True=有内容 / False=疑似空白 / None=渲染器不可用（不作为结论）。
    """

    exe = None
    for base in (r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
                 r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
                 r'C:\Program Files\Google\Chrome\Application\chrome.exe'):
        if Path(base).is_file():
            exe = base
            break
    if exe is None or PilImage is None:
        return None
    import subprocess
    import tempfile
    tmp = Path(tempfile.gettempdir()) / ('banshu_check_%d.png' % time.time())
    cmd = [exe, '--headless=new', '--disable-gpu', '--no-first-run',
           '--window-size=900,1100', '--screenshot=%s' % tmp,
           'file:///%s' % str(pdf_path).replace('\\', '/')]
    try:
        subprocess.run(cmd, timeout=45, capture_output=True)
        if not tmp.is_file():
            return None
        with PilImage.open(tmp) as im:
            stat = im.convert('L').resize((160, 200)).getstatistics()
        tmp.unlink(missing_ok=True)
        # 全白/全黑画面的标准差都接近 0；正常板书页面标准差远大于 10
        return bool(stat.stddev > 10.0)
    except Exception:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return None


# ---------------------------------------------------------------- 导出单课

def export_class(session: ApiSession, record: dict, out_root: Path,
                 sort_mode: str, logger=None,
                 group_name: str | None = None,
                 cancel_event=None,
                 page_workers: int = PAGE_WORKERS,
                 downloader: PageDownloader | None = None,
                 on_page=None, on_phase=None,
                 poll_delays: tuple[float, ...] = BS_POLL_DELAYS
                 ) -> Path | None:
    """导出一节课的板书为 PDF，返回 PDF 路径；无板书返回 None。

    logger 缺省用模块级 log（打印）；GUI 传自己的回调。
    group_name 指定输出子文件夹（通常就是界面上看到的班级名）。
    ``on_page(done, total, name)`` 用于板书页进度，
    ``on_phase(phase)`` 用于阶段切换（manifest/pages/pdf/write）。
    """

    started = time.monotonic()
    emit = logger or log
    short_desc = record.get('shortDesc') or '未命名课堂'
    file_common = (record.get('fileCommon') or {})
    file_common_id = record.get('fileId') or file_common.get('_id')
    if not file_common_id:
        emit('  [跳过] %s：缺少 fileId' % short_desc)
        return None
    if cancel_event is not None and cancel_event.is_set():
        raise BatchCancelled('已取消板书导出。')

    group = class_output_dir(
        out_root, group_name or class_group(short_desc))
    # 文件名只保留课堂名；同批里出现重名才加「（2）」，不挂课堂编号
    index = int(record.get('_nameIndex') or 1)
    stem = sanitize(short_desc)
    if index > 1:
        stem = '%s（%d）' % (stem, index)
    clean_name = '%s.pdf' % stem

    if on_phase is not None:
        on_phase('manifest')
    try:
        manifest, pathprefix = bs_manifest(
            session, file_common_id, downloader=downloader,
            cancel_event=cancel_event, poll_delays=poll_delays)
    except BsUnavailable as error:
        emit('  [跳过] %s：%s' % (short_desc, error))
        return None

    pages = collect_pages(manifest)
    if not pages:
        emit('  [跳过] %s：板书清单为空' % short_desc)
        return None
    ordered = order_pages(pages, sort_mode)
    if on_phase is not None:
        on_phase('pages')
    page_streams = prepare_page_streams(
        pathprefix, ordered, workers=page_workers,
        cancel_event=cancel_event, downloader=downloader, on_page=on_page)

    if on_phase is not None:
        on_phase('pdf')
    pdf_bytes, meta = build_pdf_pages(page_streams)

    # 幂等：同名且内容一致 -> 复用；内容变了（同一节课重新导出）-> 直接覆盖
    group.mkdir(parents=True, exist_ok=True)
    pdf_path = group / clean_name
    if pdf_path.exists() and pdf_path.stat().st_size == len(pdf_bytes):
        reused = pdf_path.read_bytes() == pdf_bytes
    else:
        reused = False
    if reused:
        emit('  [复用] %s：内容一致，不重复写入' % short_desc)
    else:
        if on_phase is not None:
            on_phase('write')
        pdf_path.write_bytes(pdf_bytes)

    # 快速校验：整文件比对 + 页数，不再逐页解压 RGB。
    ok = validate_pdf(pdf_path, meta=meta, expected_bytes=pdf_bytes,
                      pages=len(page_streams))
    if not ok:
        emit('  [失败] %s：PDF 回读校验未通过' % short_desc)
        pdf_path.unlink(missing_ok=True)
        return None
    elapsed = time.monotonic() - started
    emit('  [成功] %s -> %s（%d 页，%.1f MB，%.1fs）'
         % (short_desc, pdf_path.name, len(page_streams),
            len(pdf_bytes) / 1048576, elapsed))
    return pdf_path


# ---------------------------------------------------------------- 批量（GUI/CLI 共用）

class _BatchProgress:
    """跨线程汇总批量进度，并按固定间隔节流回调。

    GUI 的导出是多节课并行的，单看“第几节”会跳；这里用每节课的完成比例
    加权成总百分比，同时带上当前课堂的页数进度，进度条就能连续往前走。
    """

    def __init__(self, total: int, on_progress=None, on_event=None,
                 logger=None, min_interval: float = 0.12) -> None:
        self.total = max(1, int(total))
        self.on_progress = on_progress
        self.on_event = on_event
        self.emit_log = logger or log
        self.min_interval = max(0.0, float(min_interval))
        self._lock = threading.Lock()
        self._fractions = [0.0] * self.total
        self._completed = 0
        self._active: dict[int, dict] = {}
        self._started_at = time.monotonic()
        self._last_emit = 0.0

    def begin(self, index: int, record: dict) -> None:
        with self._lock:
            self._active[index] = record
        if self.on_progress is not None:
            try:
                self.on_progress(index + 1, self.total, record)
            except Exception:  # noqa: BLE001
                pass
        self._emit('start', index, record, force=True)

    def phase(self, index: int, record: dict, phase: str) -> None:
        with self._lock:
            base = {'manifest': 0.03, 'pages': 0.05}.get(phase)
            if base is not None:
                self._fractions[index] = max(self._fractions[index], base)
        self._emit(phase, index, record, force=(phase == 'manifest'))

    def pages(self, index: int, record: dict, done: int, total: int) -> None:
        fraction = 0.05 + 0.9 * (done / max(1, total))
        with self._lock:
            self._fractions[index] = max(self._fractions[index], min(0.95, fraction))
        self._emit('pages', index, record, pages_done=done,
                   pages_total=total)

    def finish(self, index: int, record: dict, ok: bool) -> None:
        with self._lock:
            self._completed += 1
            self._fractions[index] = 1.0
            self._active.pop(index, None)
        self._emit('done' if ok else 'skipped', index, record, force=True)

    def _emit(self, phase: str, index: int, record: dict,
              force: bool = False, pages_done: int = 0,
              pages_total: int = 0) -> None:
        if self.on_event is None:
            return
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_emit < self.min_interval:
                return
            self._last_emit = now
            percent = sum(self._fractions) / self.total * 100.0
            payload = {
                'phase': phase,
                'done': self._completed,
                'total': self.total,
                'percent': round(percent, 1),
                'elapsed': round(now - self._started_at, 1),
                'active': len(self._active),
                'index': index + 1,
                'name': record.get('shortDesc') or '未命名课堂',
                'pages_done': int(pages_done),
                'pages_total': int(pages_total),
            }
        try:
            self.on_event(payload)
        except Exception:  # noqa: BLE001  进度回调绝不能打断导出
            pass


def export_records(session: ApiSession, records: list[dict], out_root: Path,
                   sort_mode: str, logger=None, cancel_event=None,
                   on_progress=None, group_of=None,
                   max_workers: int = CLASS_WORKERS,
                   page_workers: int = PAGE_WORKERS,
                   on_event=None,
                   downloader: PageDownloader | None = None) -> dict:
    """导出指定的一批课（GUI 勾选流程用），返回汇总。

    records 为调用方筛选后的列表（例如 GUI 勾选的条目）；
    on_progress(done, total, record) 在每节课开始时回调；
    on_event(payload) 回调整体进度（百分比、当前课堂、页数进度等）。
    group_of(record) 可选：返回该课的输出子文件夹（班级名）；缺省用
    liveInfo 映射或课堂名前缀。

    多节课默认 3 路并发，每节课内部 4 路并发下载板书页；网络等待被
    重叠起来，整批导出比原来的全串行快数倍。服务端限速时可调小。
    """

    total = len(records)
    if total == 0:
        return {'groups': {}, 'failed': [], 'total': 0, 'elapsed': 0.0}
    # 同名课堂（同一班里两节课同名）才加「（2）」区分；重新导出同一节课
    # 直接覆盖原来的 PDF，不在文件名后面挂课堂编号。
    name_seen: dict[str, int] = {}
    for record in records:
        key = sanitize(record.get('shortDesc') or '未命名课堂')
        name_seen[key] = name_seen.get(key, 0) + 1
        record['_nameIndex'] = name_seen[key]
    emit = logger or log
    client = downloader or PageDownloader()
    tracker = _BatchProgress(total, on_progress=on_progress,
                             on_event=on_event, logger=emit)
    started = time.monotonic()
    outcomes: dict[int, tuple[str, Path | None]] = {}
    stops: list[BaseException] = []

    def work(index: int, record: dict) -> tuple[str, Path | None]:
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消板书导出。')
        short_desc = record.get('shortDesc') or '未命名课堂'
        group_name = ''
        if group_of is not None:
            try:
                group_name = str(group_of(record) or '').strip()
            except Exception:  # noqa: BLE001
                group_name = ''
        folder = sanitize(
            group_name or record_group_base(record) or class_group(short_desc))
        tracker.begin(index, record)

        def class_log(message):
            text = str(message).strip()
            if total > 1:
                emit('[%d/%d] %s' % (index + 1, total, text))
            else:
                emit(text)

        def page_cb(done: int, pages_total: int, _name: str) -> None:
            tracker.pages(index, record, done, pages_total)

        def phase_cb(phase: str) -> None:
            tracker.phase(index, record, phase)

        try:
            path = None
            for attempt in range(2):
                try:
                    path = export_class(
                        session, record, out_root, sort_mode, logger=class_log,
                        group_name=folder, cancel_event=cancel_event,
                        page_workers=page_workers, downloader=client,
                        on_page=page_cb, on_phase=phase_cb)
                    break
                except RateLimitError as error:
                    if attempt:
                        raise
                    wait = min(8.0, max(1.0, float(
                        getattr(error, 'retry_after', 0) or 2.0)))
                    class_log('  [限速] %s：%s，%.1fs 后重试'
                              % (short_desc, error, wait))
                    if cancel_event is not None:
                        if cancel_event.wait(wait):
                            raise BatchCancelled('已取消板书导出。')
                    else:
                        time.sleep(wait)
        except (BatchCancelled, TokenExpired):
            # 取消/登录态失效由上层统一处理，不在这里把总进度推到 100%。
            raise
        except Exception as error:  # noqa: BLE001
            tracker.finish(index, record, False)
            class_log('  [失败] %s：%s: %s'
                      % (short_desc, type(error).__name__, error))
            return folder, None
        tracker.finish(index, record, path is not None)
        return folder, path

    if max_workers <= 1 or total <= 1:
        for index, record in enumerate(records):
            try:
                outcomes[index] = work(index, record)
            except BaseException as error:  # noqa: BLE001
                stops.append(error)
                break
    else:
        pool = ThreadPoolExecutor(max_workers=min(max_workers, total),
                                  thread_name_prefix='banshu-class')
        futures = {pool.submit(work, index, record): (index, record)
                   for index, record in enumerate(records)}
        try:
            for future in as_completed(futures):
                try:
                    index, record = futures[future]
                    outcomes[index] = future.result()
                except BaseException as error:  # noqa: BLE001
                    stops.append(error)
                    for pending in futures:
                        pending.cancel()
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

    for error in stops:
        if isinstance(error, (BatchCancelled, TokenExpired)):
            raise error
    if stops:
        raise stops[0]

    groups: dict[str, list[Path]] = {}
    failed: list[str] = []
    for index, record in enumerate(records):
        outcome = outcomes.get(index)
        if outcome is None:
            failed.append(record.get('shortDesc') or '未命名课堂')
            continue
        folder, path = outcome
        if path is None:
            failed.append(record.get('shortDesc') or '未命名课堂')
        else:
            groups.setdefault(folder, []).append(path)
    return {'groups': groups, 'failed': failed, 'total': total,
            'elapsed': round(time.monotonic() - started, 1)}


def run_board_export(session: ApiSession, out_root: Path, sort_mode: str,
                     date_from_ms: int, date_to_ms: int,
                     classes_filter: list[str] | None = None,
                     logger=None, cancel_event=None,
                     on_progress=None) -> dict:
    """导出一个时间段的全部板书，按班级分组，返回汇总。

    （CLI 批量入口；GUI 勾选流程改用 fetch_records + export_records。）
    on_progress(done, total, record) 在每节课开始前回调。
    返回 {'groups': {班级名: [Path]}, 'failed': [课堂名], 'total': n}
    """

    records = fetch_records(session, date_from_ms, date_to_ms)
    records.sort(key=record_time)
    # 用 liveInfo 的 groups 精确映射官方班级（26秋7MA+31 -> 2026027MA+31β1）
    try:
        enrich_record_groups(session, records)
    except TokenExpired:
        raise
    except Exception as error:  # noqa: BLE001
        (logger or log)('班级映射读取失败（%s），将按课堂名分组' % error)
    if classes_filter:
        records = [r for r in records
                   if any(k in (r.get('shortDesc') or '') for k in classes_filter)]
    return export_records(session, records, out_root, sort_mode,
                          logger=logger, cancel_event=cancel_event,
                          on_progress=on_progress)


# ---------------------------------------------------------------- 时间段预设

RANGE_PRESETS = ('今天', '最近7天', '最近30天', '最近一个季度',
                 '最近一年', '最近三年', '全部', '自定义')
_DAY_MS = 86400 * 1000


def day_start(ms: int) -> int:
    """当天 00:00 的毫秒时间戳（本地时区）。"""

    day = datetime.datetime.fromtimestamp(ms / 1000)
    return int(day.replace(hour=0, minute=0, second=0,
                           microsecond=0).timestamp() * 1000)


def resolve_range(preset: str, custom_from: str = '',
                  custom_to: str = '', now_ms: int | None = None) -> tuple[int, int]:
    """时间段预设 -> (起始毫秒, 结束毫秒)。

    preset: 今天/最近7天/最近30天/最近一个季度/最近一年/最近三年/全部/自定义。
    「全部」给 10 年上界（服务端只返回实际存在的记录），避免和「最近三年」
    完全重复；自定义时 custom_from/custom_to 为 YYYY-MM-DD，结束日取当天 23:59:59。
    """

    now = int(now_ms if now_ms is not None else time.time() * 1000)
    if preset == '今天':
        return day_start(now), now
    if preset == '最近7天':
        return now - 7 * _DAY_MS, now
    if preset == '最近30天':
        return now - 30 * _DAY_MS, now
    if preset == '最近一个季度':
        return now - 92 * _DAY_MS, now
    if preset == '最近一年':
        return now - 365 * _DAY_MS, now
    if preset == '最近三年':
        return now - 3 * 365 * _DAY_MS, now
    if preset == '全部':
        return now - 10 * 365 * _DAY_MS, now
    # 自定义
    if not custom_from or not custom_to:
        raise ValueError('自定义时间段需要填写开始与结束日期（YYYY-MM-DD）。')
    from_ms = parse_date(custom_from)
    to_ms = parse_date(custom_to) + _DAY_MS - 1   # 结束日全天
    if to_ms < from_ms:
        raise ValueError('结束日期不能早于开始日期。')
    if to_ms > now + _DAY_MS:
        raise ValueError('结束日期不能晚于今天。')
    return from_ms, to_ms


# ---------------------------------------------------------------- 入口

def parse_date(text: str) -> int:
    return int(datetime.datetime.strptime(text, '%Y-%m-%d')
               .replace(hour=0, minute=0, second=0).timestamp() * 1000)


def default_out_root() -> Path:
    home = Path.home()
    for cand in (home / 'Desktop', home / 'OneDrive' / 'Desktop'):
        if cand.is_dir():
            return cand / '爱问云板书导出'
    return home / 'Documents' / '爱问云板书导出'


def main() -> int:
    parser = argparse.ArgumentParser(
        prog='banshu-export',
        description='爱问云板书 PDF 导出（只读抓取；单课/季度批量；按班级分组）')
    parser.add_argument('--liveclass', type=int, default=None,
                        help='单课导出：课堂编号')
    parser.add_argument('--file-id', default=None,
                        help='单课导出：fileCommon._id')
    parser.add_argument('--quarter', action='store_true',
                        help='批量导出一个季度（默认最近 92 天）')
    parser.add_argument('--from', dest='date_from', default=None,
                        help='季度起点 YYYY-MM-DD（默认 92 天前）')
    parser.add_argument('--to', dest='date_to', default=None,
                        help='季度终点 YYYY-MM-DD（默认今天）')
    parser.add_argument('--classes', default='',
                        help='批量时按课堂名过滤（逗号分隔，缺省=全部）')
    parser.add_argument('--sort', choices=['time', 'type'], default='time',
                        help='排序：time=按上课顺序（默认）；type=按板书类型分段')
    parser.add_argument('--token', default='',
                        help='直接给登录态 token（缺省从客户端内存只读扫描）')
    parser.add_argument('--out', default='', help='输出根目录')
    args = parser.parse_args()

    if not (args.quarter or args.liveclass or args.file_id):
        parser.error('需要 --liveclass / --file-id / --quarter 之一')

    out_root = Path(args.out) if args.out else default_out_root()
    out_root.mkdir(parents=True, exist_ok=True)
    log('输出根目录: %s' % out_root)

    token = args.token or scan_token()
    session = ApiSession(BASE, token=token, platform='ai')

    if not args.quarter:
        # 单课：先在最近一年里找这条记录（拿课堂名与 fileId）
        to_ms = time.time() * 1000
        from_ms = to_ms - 365 * 86400 * 1000
        records = fetch_records(session, from_ms, to_ms)
        wanted = None
        for record in records:
            if (args.liveclass is not None
                    and record.get('liveclass') == args.liveclass):
                wanted = record
                break
            if args.file_id and (record.get('fileId') == args.file_id
                                  or (record.get('fileCommon') or {}
                                      ).get('_id') == args.file_id):
                wanted = record
                break
        if wanted is None:
            # 单课直接按 fileCommonId 走（课堂名缺失时兜底命名）
            wanted = {'shortDesc': '课堂%s' % (args.liveclass or args.file_id),
                     'liveclass': args.liveclass or 0,
                     'fileId': args.file_id or ''}
        log('单课导出: %s' % wanted.get('shortDesc'))
        export_class(session, wanted, out_root, args.sort)
        return 0

    # 批量：一个季度
    to_ms = (parse_date(args.date_to) if args.date_to
             else int(time.time() * 1000))
    from_ms = (parse_date(args.date_from) if args.date_from
               else to_ms - 92 * 86400 * 1000)
    log('季度范围: %s ~ %s' % (
        datetime.datetime.fromtimestamp(from_ms / 1000).strftime('%Y-%m-%d'),
        datetime.datetime.fromtimestamp(to_ms / 1000).strftime('%Y-%m-%d')))

    keywords = [k.strip() for k in args.classes.split(',') if k.strip()]
    result = run_board_export(
        session, out_root, args.sort, from_ms, to_ms,
        classes_filter=keywords or None,
        on_progress=lambda done, total, rec: log(
            '[%d/%d] %s' % (done, total, rec.get('shortDesc') or '')))

    done = result['groups']
    failed = result['failed']
    log('')
    log('===== 批量导出汇总（按班级分组）=====')
    for group_name in sorted(done):
        log('[班级] %s（%d 份 PDF）' % (group_name, len(done[group_name])))
        for path in sorted(done[group_name]):
            log('    %s  %.1f MB' % (path.name, path.stat().st_size / 1048576))
    if failed:
        log('未导出（无板书/生成中/失败）: %d 节: %s' % (len(failed), '、'.join(failed)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
