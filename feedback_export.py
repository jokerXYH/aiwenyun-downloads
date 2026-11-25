"""历史课堂权限 / 专注度反馈与作业成绩 CSV 导出。

课堂权限 CSV 的列序、表头空格、BOM、CRLF 和缺失值写法与教师端
「历史课堂权限 → 全部导出」的统计报告保持一致。
"""

from __future__ import annotations

import datetime
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from banshu_export import (BatchCancelled, class_base, class_folder_label,
                           class_group, class_output_dir, group_sort_key,
                           pick_group_base, record_group_names, record_time,
                           sanitize)
from homework_export import (fetch_scores, homework_group, homework_time,
                             homework_title, safe_filename,
                             write_csv as write_homework_csv)
from net import ApiSession, TokenExpired

FeedbackCancelled = BatchCancelled

PREVIEW = 'preview'
CONSOLIDATION = 'consolidation'
HISTORY = 'history'
COMPONENT_ORDER = (PREVIEW, CONSOLIDATION, HISTORY)
COMPONENT_LABELS = {
    PREVIEW: '本节课课前预习作业',
    CONSOLIDATION: '上一节课课后巩固作业',
    HISTORY: '本节课历史课堂权限',
}

ATTEND_PATH = 'yxt/servlet/record/attend'
WATCH_PATH = 'yxt/servlet/record/getLiveClassWatch'
SIGNIN_PATH = 'yxt/servlet/record/nc/getLcUserSignIn'
STUDENT_DETAIL_PATH = 'statistics/liveclass/nct/groupStuDetail'
ROSTER_PATH = 'gt/servlet/liveClass/getLCStuDetails'

PAGE_SIZE = 100
MAX_PAGES = 200
FEEDBACK_DETAIL_WORKERS = 3
NO_VALUE = '-'
MISSING_SIGN = '-\t'

CSV_HEADER = (
    '学生姓名',
    ' 用户名',
    ' 班级',
    ' 是否有效出勤',
    ' 学习时长(分钟)',
    ' 课堂类型',
    ' 课堂名称',
    ' 任课老师',
    ' 课堂开始时间',
    ' 开课时长(分钟)',
    ' 直播在线时长(分钟)',
    ' 互动时长(分钟)',
    ' 签到次数',
    ' 随堂测正确率',
    ' 奖励积分数',
    ' 举手次数',
    ' 开麦次数',
    ' 是否观看历史课堂',
    ' 历史课堂观看时长(分钟)',
    ' 历史课堂观看进度',
    ' 首次进入直播时间',
    ' 最后离开课堂时间',
)

CLASS_TYPE_LABELS = {
    1: '语音实时课堂',
    2: '视频实时课堂',
}

_LEADING_NUMBER_RE = re.compile(r'^\s*(\d{2,})')


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _ceil_minutes(seconds: Any) -> str:
    value = _num(seconds)
    if value <= 0:
        return '0'
    return str(int(math.ceil(value / 60.0)))


_CN_TZ = datetime.timezone(datetime.timedelta(hours=8))


def _fmt_time(milliseconds: Any, seconds: bool = True) -> str:
    try:
        stamp = int(milliseconds or 0)
    except (TypeError, ValueError):
        return NO_VALUE
    if stamp <= 0:
        return NO_VALUE
    try:
        moment = datetime.datetime.fromtimestamp(stamp / 1000, tz=_CN_TZ)
    except (TypeError, ValueError, OSError, OverflowError):
        return NO_VALUE
    return moment.strftime(
        '%Y-%m-%d %H:%M:%S' if seconds else '%Y-%m-%d %H:%M')


def _student_number(name: str) -> int:
    match = _LEADING_NUMBER_RE.match(name or '')
    try:
        return int(match.group(1)) if match else 0
    except (TypeError, ValueError):
        return 0


def _user_id(value: Any) -> str:
    text = str(value or '').strip()
    if '_' in text:
        text = text.rsplit('_', 1)[-1]
    return text


def _progress_label(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return NO_VALUE
    if text.endswith('%'):
        return text
    try:
        return '%d%%' % int(float(text))
    except (TypeError, ValueError):
        return text


def record_id(record: dict) -> str:
    file_common = record.get('fileCommon') or {}
    return str(record.get('recordId') or file_common.get('recordId') or '')


def file_id(record: dict) -> str:
    file_common = record.get('fileCommon') or {}
    return str(record.get('fileId') or file_common.get('_id') or '')


def liveclass_id(record: dict) -> Any:
    return record.get('liveclass') or record.get('liveClassId')


def feedback_class_name(record: dict) -> str:
    """优先用 liveInfo 映射的官方班级，失败时从课堂名兜底。"""

    names = record_group_names(record)
    base = pick_group_base(
        [class_base(name) for name in names],
        record.get('shortDesc') or '')
    if base:
        return base
    return class_group(record.get('shortDesc') or '') or '未分类'


_CN_DIGITS = {
    '零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
    '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
}
_CN_UNITS = {'十': 10, '百': 100, '千': 1000}
_NUMBER_TEXT = '0-9零〇一二两三四五六七八九十百千'


def chinese_number(value: int) -> str:
    """把课次数字转成中文：11 -> 十一，4 -> 四，23 -> 二十三。"""

    try:
        number = int(value)
    except (TypeError, ValueError):
        return str(value)
    if number < 0:
        return str(number)
    if number == 0:
        return '零'
    digits = '零一二三四五六七八九'
    if number < 10:
        return digits[number]
    if number < 20:
        return '十' + (digits[number % 10] if number % 10 else '')
    if number < 100:
        tens, ones = divmod(number, 10)
        return digits[tens] + '十' + (digits[ones] if ones else '')
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        text = digits[hundreds] + '百'
        if not rest:
            return text
        if rest < 10:
            return text + '零' + digits[rest]
        return text + chinese_number(rest)
    return str(number)


def parse_chinese_number(text: str) -> int | None:
    """解析「十一」「二十三」「一百零二」这类中文数字。"""

    value = unicodedata.normalize('NFKC', str(text or '')).strip()
    if not value:
        return None
    if value.isdigit():
        try:
            return int(value)
        except ValueError:
            return None

    total = 0
    current = 0
    seen = False
    for char in value:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
            seen = True
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            if not seen:
                current = 1
            total += (current or 1) * unit
            current = 0
            seen = True
        elif char in '零〇':
            continue
        else:
            return None
    if not seen:
        return None
    return total + current


def parse_lesson_number(text: str) -> int | None:
    """从课堂名/作业标题里取课次：专题十一 -> 11，第4节课 -> 4，-11 -> 11。"""

    value = unicodedata.normalize('NFKC', str(text or '')).strip()
    if not value:
        return None
    patterns = (
        r'专题\s*([%s]+)' % _NUMBER_TEXT,
        r'第\s*([%s]+)\s*(?:节|课|讲)' % _NUMBER_TEXT,
        r'[-_－—]\s*(\d{1,3})\s*(?:[（(][^）)]*[）)])?\s*$',
        r'[-_－—]\s*(\d{1,3})(?=\s*[（(])',
    )
    for pattern in patterns:
        matches = re.findall(pattern, value)
        for candidate in reversed(matches):
            number = parse_chinese_number(candidate)
            if number and number > 0:
                return number
    return None


def history_lesson(record: dict) -> int | None:
    """一节课的课次；优先课堂名末尾的「-11」，再看主题/完整名称。"""

    short_desc = str(record.get('shortDesc') or '').strip()
    number = parse_lesson_number(short_desc)
    if number:
        return number
    for key in ('topic', 'title', 'name'):
        number = parse_lesson_number(str(record.get(key) or ''))
        if number:
            return number
    return None


def homework_kind(item: dict) -> str | None:
    """作业框类型：课前预习 / 课后巩固。"""

    title = homework_title(item)
    if '课前预习' in title or ('课前' in title and '预习' in title):
        return PREVIEW
    if '课后巩固' in title or ('课后' in title and '巩固' in title):
        return CONSOLIDATION
    return None


def homework_lesson(item: dict) -> int | None:
    """作业框对应的课次：专题十一 -> 11。"""

    return parse_lesson_number(homework_title(item))


def feedback_class_label(name: str) -> str:
    """反馈文件里的班级：统一成「7MA+31」，把作业班并回教学班。"""

    return sanitize(class_folder_label(name or '未分类'))


def lesson_feedback_title(lesson: int) -> str:
    return '第%s节课反馈' % chinese_number(lesson)


def _prefer_latest(current: dict | None, candidate: dict,
                   time_of: Callable[[dict], int]) -> dict:
    if current is None:
        return candidate
    try:
        return candidate if time_of(candidate) >= time_of(current) else current
    except Exception:  # noqa: BLE001
        return current


def build_feedback_bundles(history_records: list[dict],
                           homework_items: list[dict],
                           group_of: Callable[[dict], str] | None = None,
                           class_names: list[str] | None = None
                           ) -> list[dict]:
    """把课堂权限和作业按「班级 + 课次」组装成反馈包。

    每个反馈包固定包含三项：
    * 本节课课前预习作业；
    * 上一节课课后巩固作业；
    * 本节课历史课堂权限。

    第 N 节课有课后巩固作业时，也会生成第 N+1 节课反馈，让下一次课的
    预习/权限还没出来时明确标注「暂时缺失」，而不是假装不存在。

    给了 ``class_names``（用户班级列表）时，只保留课堂班级代号能匹配到
    这些班级的记录；匹配不到班级的课堂不会进入反馈列表。匹配仍沿用现有
    班级归一化规则（学期、α/β 后缀、作业班后缀），不额外扩展。
    """

    buckets: dict[str, dict] = {}
    allowed = ({feedback_class_label(name) for name in class_names or []
                if str(name or '').strip()}
               if class_names else None)

    def record_label(record: dict) -> str:
        values = [
            (group_of(record) if group_of is not None else ''),
            feedback_class_name(record),
            class_group(record.get('shortDesc') or ''),
        ]
        for value in values:
            label = feedback_class_label(value)
            if not value or label == '未分类':
                continue
            if allowed is None or label in allowed:
                return label
        return ''

    def bucket(label: str) -> dict:
        return buckets.setdefault(label, {
            'class': label,
            'history': {},
            'preview': {},
            'consolidation': {},
        })

    history_labels: set[str] = set()
    for record in history_records or []:
        if not isinstance(record, dict):
            continue
        lesson = history_lesson(record)
        if not lesson:
            continue
        label = record_label(record)
        if not label:
            continue
        history_labels.add(label)
        target = bucket(label)
        target['history'][lesson] = _prefer_latest(
            target['history'].get(lesson), record, record_time)

    # 作业必须归到真实课堂所属的班；只有作业、没有课堂的“班”不生成反馈。
    homework_allowed = history_labels or allowed
    for item in homework_items or []:
        if not isinstance(item, dict):
            continue
        kind = homework_kind(item)
        lesson = homework_lesson(item)
        if not kind or not lesson:
            continue
        label = feedback_class_label(homework_group(item))
        if homework_allowed is not None and label not in homework_allowed:
            continue
        target = bucket(label)
        target[kind][lesson] = _prefer_latest(
            target[kind].get(lesson), item, homework_time)

    bundles: list[dict] = []
    for label in buckets:
        target = buckets[label]
        lessons = set(target['history']) | set(target['preview'])
        for lesson in target['consolidation']:
            if lesson >= 1:
                lessons.add(lesson + 1)
        for lesson in sorted(lessons):
            if lesson < 1:
                continue
            components = {
                PREVIEW: target['preview'].get(lesson),
                CONSOLIDATION: target['consolidation'].get(lesson - 1),
                HISTORY: target['history'].get(lesson),
            }
            if not any(components.values()):
                continue
            missing = [kind for kind in COMPONENT_ORDER
                       if components[kind] is None]
            stamps = []
            if components[HISTORY] is not None:
                stamps.append(record_time(components[HISTORY]))
            for kind in (PREVIEW, CONSOLIDATION):
                if components[kind] is not None:
                    stamps.append(homework_time(components[kind]))
            bundles.append({
                'class': label,
                'lesson': lesson,
                'title': lesson_feedback_title(lesson),
                'key': 'l:%s:%d' % (label, lesson),
                'components': components,
                'missing': missing,
                'time': max(stamps) if stamps else 0,
            })
    bundles.sort(key=lambda item: (
        -int(item.get('time') or 0),
        group_sort_key(item.get('class') or ''),
        item.get('class') or '',
        -int(item.get('lesson') or 0)))
    return bundles


def _attend_students(session: ApiSession, rid: str,
                     cancel_event=None) -> list[dict]:
    students: list[dict] = []
    seen: set[str] = set()
    total = 0
    for page in range(MAX_PAGES):
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消导出反馈。')
        response = session.post(ATTEND_PATH, {
            'recordId': rid,
            'pageNum': page,
            'pageSize': PAGE_SIZE,
        })
        obj = response.get('obj') or {}
        chunk = obj.get('list') or []
        try:
            total = int(obj.get('total') or total or 0)
        except (TypeError, ValueError):
            total = total or 0
        for item in chunk:
            if not isinstance(item, dict):
                continue
            key = str(item.get('uniqueId') or item.get('userId') or '')
            if key and key not in seen:
                seen.add(key)
                students.append(item)
        if not chunk or len(chunk) < PAGE_SIZE:
            break
        if total and len(students) >= total:
            break
    return students


def _signin_students(session: ApiSession, liveclass: Any,
                     cancel_event=None) -> list[dict]:
    """签到接口同时给出完整名单和每个学生的官方班级。"""

    if not liveclass:
        return []
    students: list[dict] = []
    seen: set[str] = set()
    total = 0
    for page in range(MAX_PAGES):
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消导出反馈。')
        response = session.post(SIGNIN_PATH, {
            'liveclassId': int(liveclass),
            'sortKey': 1,
            'sortOrder': -1,
            'pageNum': page,
            'pageSize': PAGE_SIZE,
        })
        obj = response.get('obj') or {}
        chunk = obj.get('list') or []
        try:
            total = int(obj.get('total') or total or 0)
        except (TypeError, ValueError):
            total = total or 0
        for item in chunk:
            if not isinstance(item, dict):
                continue
            key = str(item.get('uniqueId') or '')
            if key and key not in seen:
                seen.add(key)
                students.append(item)
        if not chunk or len(chunk) < PAGE_SIZE:
            break
        if total and len(students) >= total:
            break
    return students


def _roster_students(session: ApiSession, liveclass: Any,
                     cancel_event=None) -> list[dict]:
    if not liveclass:
        return []
    if cancel_event is not None and cancel_event.is_set():
        raise BatchCancelled('已取消导出反馈。')
    response = session.post(ROSTER_PATH, {'liveClassId': int(liveclass)})
    items = response.get('obj') or []
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _optional_obj(session: ApiSession, path: str, payload: dict,
                  cancel_event=None) -> dict:
    if cancel_event is not None and cancel_event.is_set():
        raise BatchCancelled('已取消导出反馈。')
    try:
        response = session.post(path, payload)
        obj = response.get('obj')
        return obj if isinstance(obj, dict) else {}
    except TokenExpired:
        raise
    except Exception:  # noqa: BLE001  观看/详情接口失败不阻断整份名单
        return {}


def _live_info(session: ApiSession, record: dict) -> dict:
    liveclass = liveclass_id(record)
    if liveclass:
        try:
            return session.fetch_live_info([int(liveclass)]).get(
                int(liveclass), {}) or {}
        except TokenExpired:
            raise
        except Exception:  # noqa: BLE001
            pass
    return {}


def _by_user(rows: Any) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        key = _user_id(item.get('userId') or item.get('uniqueId'))
        if key:
            result[key] = item
    return result


def _student_id(student: dict) -> str:
    return _user_id(student.get('userId') or student.get('uniqueId'))


def _student_detail(session: ApiSession, student: dict, live_info: dict,
                    teacher_id: Any, cancel_event=None) -> dict:
    """单学生聚合数据：出勤、开麦、积分、举手、随堂测、历史观看。"""

    group_id = str(student.get('groupIds') or '').split(',')[0].strip()
    student_id = _student_id(student)
    if not group_id or not student_id.isdigit():
        return {}
    begin = live_info.get('startTime')
    end = live_info.get('endTime')
    if not begin:
        return {}
    if not end:
        try:
            end = int(begin) + int(live_info.get('duration') or 0) * 1000
        except (TypeError, ValueError):
            end = begin
    payload = {
        'beginTime': int(begin),
        'endTime': int(end),
        'groupId': int(group_id),
        'studentId': int(student_id),
        'teacherId': int(teacher_id or 0),
    }
    return _optional_obj(
        session, STUDENT_DETAIL_PATH, payload, cancel_event=cancel_event)


def build_feedback_rows(students: list[dict], attend: list[dict],
                        watch: dict, live_info: dict,
                        record: dict, fallback_class: str = '',
                        details: dict[str, dict] | None = None
                        ) -> list[list[str]]:
    """把签到名单、观看记录和直播数据合成附件同款 22 列。

    列格式参考教师端「历史课堂权限」导出：表头从第二列起带一个
    前导空格；缺失签到写作 ``-\t``；分钟数向上取整。
    """

    attend_map = _by_user(attend)
    listener_map = _by_user((watch or {}).get('listenerInfos'))
    watch_map = _by_user((watch or {}).get('notInClassListenerIds'))
    detail_map = details or {}

    start_time = live_info.get('startTime')
    end_time = live_info.get('endTime')
    duration_seconds = _num(live_info.get('duration') or record.get('duration'))
    if start_time and end_time:
        try:
            span = (int(end_time) - int(start_time)) / 1000.0
            if span > 0:
                duration_seconds = span
        except (TypeError, ValueError):
            pass
    duration_minutes = _ceil_minutes(duration_seconds)
    class_type = CLASS_TYPE_LABELS.get(
        int(live_info.get('mediaType') or 0), '实时课堂')
    teacher = str(
        live_info.get('teacherName')
        or record.get('teacherName')
        or record.get('teacher')
        or '').strip()
    topic = str(record.get('shortDesc') or live_info.get('topic')
                or '未命名课堂').strip()
    start_text = _fmt_time(start_time or record.get('startTime'))

    rows: list[tuple[tuple, list[str]]] = []
    for student in students:
        if not isinstance(student, dict):
            continue
        key = _user_id(student.get('userId') or student.get('uniqueId'))
        listener = listener_map.get(key) or {}
        watch_item = watch_map.get(key) or {}
        attend_item = attend_map.get(key) or {}
        detail = detail_map.get(key) or {}
        detail_live = detail.get('liveclass') or {}
        detail_record = detail.get('record') or {}
        name = str(student.get('username') or student.get('name') or '').strip()
        login_name = str(
            student.get('loginname') or student.get('loginName') or '').strip()
        if not name and not login_name:
            continue
        class_name = str(student.get('groupNames') or fallback_class or '').strip()
        sign_count = int(_num(student.get('signCount')))
        sign_total = int(_num(student.get('signTotal')))
        online_seconds = _num(
            detail_live.get('onlineDuration')
            if detail_live else listener.get('onlineDuration'))
        interaction_seconds = _num(
            detail_live.get('onstageDuration')
            if detail_live else listener.get('onstageDuration'))
        watch_seconds = _num(
            detail_record.get('watchDuration')
            if detail_record else (
                attend_item.get('watchDuration')
                or watch_item.get('watchDuration')))
        watch_record = int(_num(
            detail_record.get('watchRecordNum')
            if detail_record else (
                attend_item.get('watchRecord')
                if attend_item else (1 if watch_item.get('isWatch') else 0))))
        watched = watch_record == 1 or watch_seconds > 0
        progress = attend_item.get('progress')
        if progress in (None, '') and watch_item.get('progress') is not None:
            progress = watch_item.get('progress')
        join_time = listener.get('joinClassTime')
        leave_time = listener.get('leaveClassTime')
        attended = int(_num(detail_live.get('attendLiveclassNum')))
        effective = detail_live.get('effectAttendNum')
        if effective is None:
            attended_now = online_seconds > 0
        else:
            attended_now = _num(effective) > 0
        reward = _num(detail_live.get('star')
                      if detail_live else listener.get('scoreFromClass'))
        raise_count = _num(detail_live.get('raiseCount')
                           if detail_live else listener.get('raiseCount'))
        open_mic = _num(detail_live.get('openAudioNum'))
        stc_num = _num(detail_live.get('stcNum'))
        stc_accuracy = _num(detail_live.get('stcAccuracy'))
        study_seconds = online_seconds + watch_seconds

        row = [
            name,
            login_name,
            class_name,
            '出勤' if attended_now else '缺勤',
            _ceil_minutes(study_seconds),
            class_type,
            topic,
            teacher,
            start_text,
            duration_minutes,
            _ceil_minutes(online_seconds),
            _ceil_minutes(interaction_seconds),
            str(sign_count) if sign_total > 0 else MISSING_SIGN,
            ('%d%%' % int(stc_accuracy)) if stc_num > 0 else NO_VALUE,
            ('%g' % reward) if attended > 0 else NO_VALUE,
            ('%g' % raise_count) if attended > 0 else NO_VALUE,
            ('%g' % open_mic) if attended > 0 else NO_VALUE,
            '已观看' if watched else '未观看',
            _ceil_minutes(watch_seconds) if watched else NO_VALUE,
            _progress_label(progress) if watched else NO_VALUE,
            _fmt_time(join_time),
            _fmt_time(leave_time),
        ]
        rows.append(((
            int(_num(key)) if key.isdigit() else _student_number(name),
            name, login_name), row))
    rows.sort(key=lambda item: item[0])
    return [row for _key, row in rows]


def _fetch_student_details(session: ApiSession, students: list[dict],
                           live_info: dict, teacher_id: Any,
                           cancel_event=None) -> dict[str, dict]:
    """批量拉学生详情；固定小并发，保持原有单学生失败不阻断策略。"""

    targets = []
    for student in students:
        key = _user_id(student.get('userId') or student.get('uniqueId'))
        if key:
            targets.append((key, student))
    details: dict[str, dict] = {}

    def load(student: dict) -> tuple[str, dict]:
        key = _user_id(student.get('userId') or student.get('uniqueId'))
        return key, _student_detail(
            session, student, live_info, teacher_id,
            cancel_event=cancel_event)

    if len(targets) <= 1 or FEEDBACK_DETAIL_WORKERS <= 1:
        for key, student in targets:
            if cancel_event is not None and cancel_event.is_set():
                raise BatchCancelled('已取消导出反馈。')
            detail = _student_detail(
                session, student, live_info, teacher_id,
                cancel_event=cancel_event)
            if detail:
                details[key] = detail
        return details

    pool = ThreadPoolExecutor(
        max_workers=min(FEEDBACK_DETAIL_WORKERS, len(targets)),
        thread_name_prefix='feedback-detail')
    futures = [pool.submit(load, student) for _key, student in targets]
    try:
        for future in futures:
            if cancel_event is not None and cancel_event.is_set():
                raise BatchCancelled('已取消导出反馈。')
            key, detail = future.result()
            if detail:
                details[key] = detail
    except BaseException:
        for future in futures:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    return details


def fetch_feedback_rows(session: ApiSession, record: dict,
                        class_name: str = '', cancel_event=None
                        ) -> list[list[str]]:
    rid = record_id(record)
    if not rid:
        raise ValueError('%s：缺少 recordId' % (
            record.get('shortDesc') or '未命名课堂'))
    live_info = _live_info(session, record)
    students = _signin_students(
        session, liveclass_id(record), cancel_event=cancel_event)
    if not students:
        students = _attend_students(session, rid, cancel_event=cancel_event)
    if not students:
        students = _roster_students(
            session, liveclass_id(record), cancel_event=cancel_event)
    attend = _attend_students(session, rid, cancel_event=cancel_event)
    watch = _optional_obj(
        session, WATCH_PATH, {'recordId': rid}, cancel_event=cancel_event)
    teacher_id = record.get('teacher') or live_info.get('teacher') or 0
    details = _fetch_student_details(
        session, students, live_info, teacher_id,
        cancel_event=cancel_event)
    return build_feedback_rows(
        students, attend, watch, live_info, record,
        fallback_class=class_name or feedback_class_name(record),
        details=details)


def _csv_cell(value: Any) -> str:
    text = '' if value is None else str(value)
    if text == '':
        return ''
    if any(char in text for char in ',"\r\n\t'):
        return '"%s"' % text.replace('"', '""')
    return text


def write_feedback_csv(path: Path, rows: list[list[str]]) -> int:
    """UTF-8 BOM + CRLF，缺失值/引号规则与附件一致，原子写入。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.%s.tmp%s' % (path.stem, path.suffix))
    lines = [','.join(CSV_HEADER)]
    lines.extend(','.join(_csv_cell(cell) for cell in row) for row in rows)
    text = '\r\n'.join(lines) + '\r\n'
    try:
        temp.write_text(text, encoding='utf-8-sig', newline='')
        temp.replace(path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return len(rows)


def feedback_filename(record: dict) -> str:
    title = sanitize(record.get('shortDesc') or '未命名课堂')[:80]
    return '%s统计报告.csv' % title


def _homework_filename(item: dict) -> str:
    stamp = homework_time(item)
    try:
        prefix = datetime.datetime.fromtimestamp(
            int(stamp) / 1000, tz=_CN_TZ).strftime('%Y%m%d-%H%M')
    except (TypeError, ValueError, OSError, OverflowError):
        prefix = '00000000-0000'
    title = safe_filename(homework_title(item))[:80]
    return '%s-%s.csv' % (prefix, title)


def _dedupe_name(name: str, seen: dict[str, int], folder: Path) -> str:
    key = '%s|%s' % (folder, name)
    count = seen.get(key, 0) + 1
    seen[key] = count
    if count <= 1:
        return name
    if name.lower().endswith('.csv'):
        return name[:-4] + '（%d）.csv' % count
    return '%s（%d）' % (name, count)


def _homework_class(item: dict) -> str:
    return homework_group(item) or '未分类'


def _component_filename(kind: str, bundle: dict, item: dict) -> str:
    if kind == PREVIEW:
        return '课前预习-%s.csv' % safe_filename(homework_title(item))
    if kind == CONSOLIDATION:
        return '课后巩固-%s.csv' % safe_filename(homework_title(item))
    return '历史课堂权限-第%s节课统计报告.csv' % chinese_number(
        bundle['lesson'])


def feedback_component_name(bundle: dict, kind: str) -> str:
    """展开区显示/导出时使用的单个文件名；缺项返回暂时缺失标记名。"""

    item = (bundle.get('components') or {}).get(kind)
    if item is None:
        return _missing_filename(kind)
    return _component_filename(kind, bundle, item)


def _missing_filename(kind: str) -> str:
    return '%s-暂时缺失' % COMPONENT_LABELS[kind].replace(
        '本节课', '').replace('上一节课', '')


def _clean_bundle_folder(folder: Path, bundle: dict) -> None:
    """重复导出同一课次时只清掉已知的生成文件，避免误删用户文件。"""

    if not folder.is_dir():
        return
    names = {'反馈说明.txt'}
    for kind in COMPONENT_ORDER:
        names.add(_missing_filename(kind))
        names.add('%s.txt' % _missing_filename(kind))
        if (bundle.get('components') or {}).get(kind) is not None:
            names.add(feedback_component_name(bundle, kind))
    for name in names:
        path = folder / name
        if path.is_file():
            try:
                path.unlink()
            except OSError:
                pass


def export_feedback_bundles(session: ApiSession,
                            bundles: list[dict],
                            out_root: Path,
                            on_event: Callable[[dict], None] | None = None,
                            cancel_event=None,
                            logger: Callable[[str], None] | None = None
                            ) -> dict:
    """导出按课次组装好的反馈包。

    输出结构：``<保存位置>/<班级>/第X节课反馈/``，只写实际选中的 CSV
    文件；缺项或未选中的项只在列表/日志里标注，不再生成 txt 说明文件。
    """

    def emit(**payload) -> None:
        if on_event is not None:
            try:
                on_event(payload)
            except Exception:  # noqa: BLE001
                pass

    def log(message: str) -> None:
        if logger is not None:
            logger(message)

    ordered = sorted(
        [bundle for bundle in bundles or [] if isinstance(bundle, dict)],
        key=lambda item: (group_sort_key(str(item.get('class') or '')),
                          str(item.get('class') or ''),
                          int(item.get('lesson') or 0)))
    total = len(ordered)
    started = datetime.datetime.now()
    groups: dict[str, list[str]] = {}
    files: list[dict] = []
    failed: list[dict] = []

    for index, bundle in enumerate(ordered, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消导出反馈。')
        class_name = str(bundle.get('class') or '未分类')
        title = str(bundle.get('title') or lesson_feedback_title(
            int(bundle.get('lesson') or 0)))
        emit(phase='start', index=index, done=index - 1, total=total,
             kind='feedback', name=title, group=class_name,
             elapsed=(datetime.datetime.now() - started).total_seconds())
        try:
            class_dir = class_output_dir(out_root, class_name)
            folder = class_dir / sanitize(title)
            folder.mkdir(parents=True, exist_ok=True)
            _clean_bundle_folder(folder, bundle)
            statuses: list[dict] = []
            total_rows = 0
            selected = bundle.get('selected')
            for kind in COMPONENT_ORDER:
                item = (bundle.get('components') or {}).get(kind)
                label = COMPONENT_LABELS[kind]
                if selected is not None and kind not in selected:
                    statuses.append({
                        'kind': kind, 'label': label, 'present': False,
                        'selected': False, 'skipped': True,
                        'name': '', 'path': '', 'rows': 0,
                    })
                    continue
                if item is None:
                    statuses.append({
                        'kind': kind, 'label': label, 'present': False,
                        'selected': True,
                        'name': '', 'path': '', 'rows': 0,
                    })
                    continue
                try:
                    if kind == HISTORY:
                        rows = fetch_feedback_rows(
                            session, item, class_name,
                            cancel_event=cancel_event)
                        name = feedback_component_name(bundle, kind)
                        path = folder / name
                        count = write_feedback_csv(path, rows)
                    else:
                        data = fetch_scores(session, str(
                            item.get('zuoyeId') or item.get('id') or ''))
                        students = data.get('students') or []
                        name = feedback_component_name(bundle, kind)
                        path = folder / name
                        count = write_homework_csv(path, item, students)
                except (BatchCancelled, TokenExpired):
                    raise
                except Exception as error:  # noqa: BLE001
                    statuses.append({
                        'kind': kind, 'label': label, 'present': False,
                        'selected': True,
                        'name': '', 'path': '', 'rows': 0,
                        'error': str(error),
                    })
                    failed.append({
                        'kind': kind, 'title': title, 'group': class_name,
                        'error': str(error),
                    })
                    log('[%d/%d] [失败] %s / %s：%s'
                        % (index, total, title, label, error))
                    continue
                total_rows += count
                statuses.append({
                    'kind': kind, 'label': label, 'present': True,
                    'selected': True,
                    'name': path.name, 'path': str(path), 'rows': count,
                })

            missing = [status['label'] for status in statuses
                       if not status.get('present')
                       and not status.get('skipped')]
            unselected = [status['label'] for status in statuses
                          if status.get('skipped')]
            files.append({
                'kind': '课次反馈', 'title': title, 'group': class_name,
                'class': class_name, 'lesson': bundle.get('lesson'),
                'path': str(folder), 'rows': total_rows,
                'components': statuses, 'missing': missing,
                'unselected': unselected,
            })
            groups.setdefault(class_name, []).append(str(folder))
            state = '三项齐全' if not missing else '缺失：%s' % '、'.join(missing)
            log('[%d/%d] %s / %s -> %s（%s）'
                % (index, total, class_name, title, folder.name, state))
            emit(phase='done', index=index, done=index, total=total,
                 kind='feedback', name=title, group=class_name,
                 percent=index / total * 100.0,
                 elapsed=(datetime.datetime.now() - started).total_seconds())
        except (BatchCancelled, TokenExpired):
            raise
        except Exception as error:  # noqa: BLE001
            failed.append({'kind': 'feedback', 'title': title,
                           'group': class_name, 'error': str(error)})
            log('[%d/%d] [失败] %s / %s：%s'
                % (index, total, class_name, title, error))
            emit(phase='failed', index=index, done=index, total=total,
                 kind='feedback', name=title, group=class_name,
                 error=str(error),
                 elapsed=(datetime.datetime.now() - started).total_seconds())

    return {
        'groups': groups,
        'files': files,
        'failed': failed,
        'total': total,
        'elapsed': round(
            (datetime.datetime.now() - started).total_seconds(), 1),
        'out_root': str(out_root),
    }


def _needs_lesson_bundles(history_records: list[dict],
                          homework_items: list[dict]) -> bool:
    """有「课前预习/课后巩固」作业标题和课次时，旧入口也走课次组装。"""

    has_homework = any(
        homework_kind(item) and homework_lesson(item)
        for item in homework_items or [] if isinstance(item, dict))
    return bool(has_homework)


def export_bundle(session: ApiSession,
                  history_records: list[dict],
                  homework_items: list[dict],
                  out_root: Path,
                  group_of: Callable[[dict], str] | None = None,
                  on_event: Callable[[dict], None] | None = None,
                  cancel_event=None,
                  logger: Callable[[str], None] | None = None,
                  assemble: bool | None = None) -> dict:
    """兼容旧入口：可自动按课次组装，也保留逐项导出的旧行为。"""

    if assemble is None:
        assemble = _needs_lesson_bundles(history_records, homework_items)
    if assemble:
        bundles = build_feedback_bundles(
            history_records, homework_items, group_of=group_of)
        return export_feedback_bundles(
            session, bundles, out_root, on_event=on_event,
            cancel_event=cancel_event, logger=logger)

    def emit(**payload) -> None:
        if on_event is not None:
            try:
                on_event(payload)
            except Exception:  # noqa: BLE001
                pass

    def log(message: str) -> None:
        if logger is not None:
            logger(message)

    tasks: list[tuple[tuple, str, dict]] = []
    for record in history_records:
        label = ((str(group_of(record) if group_of else '').strip())
                 or feedback_class_name(record))
        tasks.append(((
            group_sort_key(label), label, record_time(record), 0), 'history',
            record))
    for item in homework_items:
        label = _homework_class(item)
        tasks.append(((
            group_sort_key(label), label, homework_time(item), 1), 'homework',
            item))
    tasks.sort(key=lambda task: task[0])

    total = len(tasks)
    started = datetime.datetime.now()
    groups: dict[str, list[str]] = {}
    files: list[dict] = []
    failed: list[dict] = []
    seen: dict[str, int] = {}

    for index, (_sort, kind, item) in enumerate(tasks, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消导出反馈。')
        if kind == 'history':
            title = str(item.get('shortDesc') or '未命名课堂')
            group = ((str(group_of(item) if group_of else '').strip())
                     or feedback_class_name(item))
        else:
            title = homework_title(item)
            group = _homework_class(item)
        emit(phase='start', index=index, done=index - 1, total=total,
             kind=kind, name=title, group=group,
             elapsed=(datetime.datetime.now() - started).total_seconds())
        try:
            folder = class_output_dir(out_root, group)
            if kind == 'history':
                rows = fetch_feedback_rows(
                    session, item, group, cancel_event=cancel_event)
                name = _dedupe_name(
                    feedback_filename(item), seen, folder)
                path = folder / name
                count = write_feedback_csv(path, rows)
                label = '课堂权限'
            else:
                data = fetch_scores(session, str(
                    item.get('zuoyeId') or item.get('id') or ''))
                students = data.get('students') or []
                name = _dedupe_name(_homework_filename(item), seen, folder)
                path = folder / name
                count = write_homework_csv(path, item, students)
                label = '作业成绩'
            files.append({'kind': label, 'title': title, 'group': group,
                          'path': str(path), 'rows': count})
            groups.setdefault(group, []).append(str(path))
            log('[%d/%d] %s -> %s（%d 名学生）'
                % (index, total, title, path.name, count))
            emit(phase='done', index=index, done=index, total=total,
                 kind=kind, name=title, group=group,
                 percent=index / total * 100.0,
                 elapsed=(datetime.datetime.now() - started).total_seconds())
        except (BatchCancelled, TokenExpired):
            raise
        except Exception as error:  # noqa: BLE001
            failed.append({'kind': kind, 'title': title,
                           'group': group, 'error': str(error)})
            log('[%d/%d] [失败] %s：%s' % (index, total, title, error))
            emit(phase='failed', index=index, done=index, total=total,
                 kind=kind, name=title, group=group, error=str(error),
                 elapsed=(datetime.datetime.now() - started).total_seconds())

    return {
        'groups': groups,
        'files': files,
        'failed': failed,
        'total': total,
        'elapsed': round(
            (datetime.datetime.now() - started).total_seconds(), 1),
        'out_root': str(out_root),
    }


def export_feedback(session: ApiSession, records: list[dict],
                    out_root: Path,
                    group_of: Callable[[dict], str] | None = None,
                    on_event: Callable[[dict], None] | None = None,
                    cancel_event=None,
                    logger: Callable[[str], None] | None = None) -> dict:
    """兼容旧调用：只导出课堂权限 CSV。"""

    return export_bundle(
        session, records, [], out_root, group_of=group_of,
        on_event=on_event, cancel_event=cancel_event, logger=logger)


def default_out_root() -> Path:
    """保存位置：桌面「爱问云反馈文件」。"""

    home = Path.home()
    for candidate in (home / 'Desktop', home / 'OneDrive' / 'Desktop', home):
        if candidate.is_dir():
            return candidate / '爱问云反馈文件'
    return home / '爱问云反馈文件'
