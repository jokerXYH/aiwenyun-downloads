"""作业（作业框）清单与每个作业框的学生成绩导出。

接口来自教师端控制台：

* ``homework/zuoyeDaka/teacherListWithDaka`` 作业列表（按 ``mark`` 游标分页，
  每条带作业框标题、班级名、应交/已交/已批数量）；
* ``yxt/servlet/zuoye/getAllZuoyesByZuoyeIdNew`` 单个作业框下全班学生的
  提交与批改记录，含分数、正确率、评语、订正分。

这里只做「读」：拉清单、拉成绩、写 CSV，不提交、不批改、不改任何作业。
"""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from banshu_export import sanitize
from net import ApiSession, TokenExpired

HOMEWORK_LIST_PATH = 'homework/zuoyeDaka/teacherListWithDaka'
HOMEWORK_SCORES_PATH = 'yxt/servlet/zuoye/getAllZuoyesByZuoyeIdNew'

PAGE_LIMIT = 20                 # 服务端每页最多给 20 条
MAX_PAGES = 300                 # 兜底：一次最多翻 300 页（6000 个作业框）
UNCLASSIFIED_GROUP = '未分类'

# 作业状态：和教师端控制台的徽标文案一一对应（来源：控制台里的状态映射
# 9=zuoye_unread 未查阅、10=zuoye_read 已查阅、11=zuoye_submited 已提交、
# 12/14=unrevised 待订正、13=已订正、99=zuoye_completed 已完成）。
# 注意 10 是「打开过但没交」，不能算已提交。
STATUS_LABELS = {
    1: '批改中',
    9: '未查阅',
    10: '已查阅',
    11: '已提交',
    12: '待订正',
    13: '已订正',
    14: '待订正',
    99: '已完成',
}

# 统计 CSV 的「作业状态」文案，用平台自己导出的文件反推出来（拿三份真实导出
# 对过：9=未查阅、11=已提交、12=已完成、99=已完成）。注意这里和页面上的
# 状态徽标不完全一样：徽标把 12 显示成「待订正」，但统计导出写的是「已完成」。
EXPORT_STATUS_LABELS = {
    9: '未查阅',
    10: '已查阅',
    11: '已提交',
    12: '已完成',
    13: '已完成',
    14: '已完成',
    99: '已完成',
}

# 爱问云统计导出的列头原样照抄（注意「评级奖励积分」出现两次，这是
# 平台自己的文件就这样，不要"修正"）。
CSV_HEADER = ('序号', '姓名', '用户名', '作业状态', '查阅答案', '作答时长(min)',
              '订正次数', '错题数', '得分', '正确率', '总积分', '批改评级',
              '评级奖励积分', '批改评语', '订正评级', '评级奖励积分',
              '订正评语', '学生批改评价', '学生批改评语', '学生订正评价',
              '学生订正评语')

_LEADING_NUMBER_RE = re.compile(r'^\s*(\d{2,})\s*')
_ILLEGAL_FS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def log(message: str) -> None:
    print(message, file=sys.stderr)


def status_label(status: Any) -> str:
    try:
        key = int(status)
    except (TypeError, ValueError):
        return ''
    return STATUS_LABELS.get(key, '状态%s' % key)


def split_student_name(text: str) -> tuple[str, str]:
    """学生名常见是「36673张三」，拆成 (学号, 姓名)；拆不出就学号留空。"""

    value = (text or '').strip()
    match = _LEADING_NUMBER_RE.match(value)
    if not match:
        return '', value
    return match.group(1), value[match.end():].strip() or value


def homework_title(item: dict) -> str:
    title = str(item.get('title') or '').strip()
    return title or '未命名作业'


def homework_group(item: dict) -> str:
    group = str(item.get('groupName') or '').strip()
    return group or UNCLASSIFIED_GROUP


_CLASS_YEAR_RE = re.compile(r'^20\d{4}')     # 2026027… 里的「202602」


def class_short_name(group_name: str) -> str:
    """班级名压成导出文件名里那一段：2026027MA+31作业班 → 7MA+31。"""

    text = str(group_name or '').strip()
    for suffix in ('作业班', '班级'):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
    text = _CLASS_YEAR_RE.sub('', text).strip()
    if text:
        return text
    return str(group_name or '').strip()


def homework_time(item: dict) -> int:
    for key in ('sortTime', 'createTime', 'updateTime'):
        try:
            value = int(item.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def safe_filename(text: str, fallback: str = '未命名作业') -> str:
    """CSV 文件名：保留作业框原名，只去掉文件系统不认的字符。"""

    clean = _ILLEGAL_FS.sub('_', (text or '').strip()).strip(' .')
    clean = re.sub(r'\s+', ' ', clean)
    return clean[:90] or fallback


# ---------------------------------------------------------------- 拉清单

def _list_page(session: ApiSession, mark: str = '',
               group_id: int | None = None) -> dict:
    payload: dict[str, Any] = {'limit': PAGE_LIMIT}
    if mark:
        payload['mark'] = mark
    if group_id:
        payload['groupId'] = int(group_id)
    return session.post(HOMEWORK_LIST_PATH, payload)


def fetch_homeworks(session: ApiSession, date_from_ms: int, date_to_ms: int,
                    on_log: Callable[[str], None] | None = None,
                    cancel_event: threading.Event | None = None) -> list[dict]:
    """按时间段拉作业框清单（新的在前，翻页直到越过起始时间）。"""

    def say(message: str) -> None:
        if on_log is not None:
            on_log(message)
        else:
            log(message)

    items: list[dict] = []
    seen: set[str] = set()
    mark = ''
    below = 0
    for page in range(MAX_PAGES):
        if cancel_event is not None and cancel_event.is_set():
            raise HomeworkCancelled('已取消列出作业')
        data = _list_page(session, mark)
        obj = data.get('obj') or {}
        rows = obj.get('list') or []
        if not rows:
            break
        newest = 0
        for row in rows:
            zuoye_id = str(row.get('zuoyeId') or row.get('id') or '')
            stamp = homework_time(row)
            newest = max(newest, stamp)
            if zuoye_id and zuoye_id not in seen:
                seen.add(zuoye_id)
                items.append(row)
        if not obj.get('hasMore'):
            break
        mark = str(obj.get('mark') or '')
        if not mark:
            break
        # 只有在「连续两页都比时间段还旧」时才停，避免列表被置顶/改过顺序
        # 时提前收工，漏掉后面还落在这个时间段里的作业框。
        if newest and newest < date_from_ms:
            below += 1
            if below >= 2:
                break
        else:
            below = 0
        say('已读取 %d 个作业框，继续翻页…' % len(items))

    result = [row for row in items
              if _in_range(homework_time(row), date_from_ms, date_to_ms)]
    result.sort(key=homework_time, reverse=True)
    say('共 %d 个作业框落在所选时间段内。' % len(result))
    return result


def _in_range(stamp: int, date_from_ms: int, date_to_ms: int) -> bool:
    if not stamp:
        return True
    if date_from_ms and stamp < date_from_ms:
        return False
    if date_to_ms and stamp > date_to_ms:
        return False
    return True


class HomeworkCancelled(RuntimeError):
    """用户取消了作业导出。"""


# ---------------------------------------------------------------- 拉成绩

def fetch_scores(session: ApiSession, zuoye_id: str) -> dict:
    """一个作业框下全班学生的成绩记录。"""

    data = session.post(HOMEWORK_SCORES_PATH, {'zuoyeId': zuoye_id})
    obj = data.get('obj') or {}
    students = obj.get('studentZuoyeInfo') or []
    return {'info': obj.get('zuoyeInfo') or {},
            'students': [row for row in students if isinstance(row, dict)]}


def _number(value: Any) -> str:
    if value is None or value == '':
        return ''
    if isinstance(value, bool):
        return '1' if value else '0'
    return str(value)


def statistics_status(status: Any) -> str:
    """统计 CSV 用的作业状态文案（未查阅 / 已提交 / 已完成 / …）。"""

    try:
        key = int(status)
    except (TypeError, ValueError):
        return ''
    return EXPORT_STATUS_LABELS.get(key, STATUS_LABELS.get(key, ''))


def statistic_row(index: int, student: dict) -> list[str]:
    """一个学生对应统计 CSV 的一行（列序与爱问云导出的文件一致）。"""

    return [
        str(index),
        str(student.get('studentName') or ''),
        str(student.get('studentLoginName') or ''),
        statistics_status(student.get('status')),
        '否',                                     # 查阅答案
        _number(student.get('tijiaoDuration')),    # 作答时长(min)
        _number(student.get('dingzhengNum')),      # 订正次数
        _number(student.get('errNum')),            # 错题数
        _number(student.get('score')),             # 得分
        _percent(student.get('accuracy')),         # 正确率
        '',                                        # 总积分
        _number(student.get('grade')),             # 批改评级
        _number(student.get('grade_score')),       # 评级奖励积分
        str(student.get('comment') or ''),         # 批改评语
        _number(student.get('grade_dz')),          # 订正评级
        _number(student.get('grade_revision_score')),   # 评级奖励积分（平台重复列）
        str(student.get('comment_dz') or ''),      # 订正评语
        '',                                        # 学生批改评价
        '',                                        # 学生批改评语
        '',                                        # 学生订正评价
        '',                                        # 学生订正评语
    ]


def _percent(value: Any) -> str:
    text = _number(value)
    return '%s%%' % text if text else ''


def score_rows(students: list[dict]) -> list[list[str]]:
    """按学号排序后，排成统计 CSV 的数据行（含序号）。"""

    def sort_key(row: dict):
        number, _name = split_student_name(str(row.get('studentName') or ''))
        return (number or '999999', str(row.get('studentName') or ''))

    rows = []
    for index, student in enumerate(sorted(students, key=sort_key), 1):
        rows.append(statistic_row(index, student))
    return rows


def summarize(students: list[dict]) -> dict:
    """统计应交 / 已交 / 已批 / 平均分，用于界面与日志。"""

    total = len(students)
    submitted = 0
    corrected = 0
    scores: list[float] = []
    for row in students:
        status = row.get('status')
        try:
            code = int(status)
        except (TypeError, ValueError):
            code = 0
        # 9/10 分别是未查阅、已查阅（打开过但没交），都不算已交
        if code not in (0, 1, 9, 10):
            submitted += 1
        score = row.get('score')
        if score not in (None, '') and code not in (0, 1, 9, 10):
            corrected += 1
            try:
                scores.append(float(score))
            except (TypeError, ValueError):
                pass
    average = round(sum(scores) / len(scores), 1) if scores else None
    return {'total': total, 'submitted': submitted, 'corrected': corrected,
            'average': average}


def _csv_cell(value: str) -> str:
    """平台的文件是「有值才加引号，空值留空」，这里照抄。"""

    if value == '':
        return ''
    return '"%s"' % value.replace('"', '""')


def write_csv(path: Path, homework: dict, students: list[dict]) -> int:
    """写一个作业框的统计 CSV，格式与爱问云「作业框 → 统计」导出的一致。

    - UTF-8 带 BOM、CRLF 换行；
    - 表头 21 列（「评级奖励积分」重复两次）；
    - 每个学生一行，有值的格子加双引号，空值什么都不写。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = score_rows(students)
    # 表头这一行平台不加引号，数据行才加；照抄才能和它的文件逐字节一致
    lines = [','.join(CSV_HEADER)]
    for row in rows:
        lines.append(','.join(_csv_cell(cell) for cell in row))
    # 末尾不加多余换行：平台的文件最后一行后面就是 EOF
    text = '\r\n'.join(lines)
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        handle.write(text)
    return len(rows)


# ---------------------------------------------------------------- 导出

def export_homeworks(session: ApiSession, homeworks: list[dict],
                     out_root: Path,
                     on_event: Callable[[dict], None] | None = None,
                     cancel_event: threading.Event | None = None,
                     logger: Callable[[str], None] | None = None) -> dict:
    """逐个作业框拉成绩并写 CSV；返回 {'files': [...], 'failed': [...]}。"""

    files: list[dict] = []
    failed: list[dict] = []
    total = len(homeworks)
    started = time.monotonic()

    def emit(**payload) -> None:
        if on_event is not None:
            payload.setdefault('total', total)
            payload.setdefault('elapsed', round(time.monotonic() - started, 1))
            on_event(payload)

    for index, homework in enumerate(homeworks, 1):
        if cancel_event is not None and cancel_event.is_set():
            raise HomeworkCancelled('已取消作业导出')
        title = homework_title(homework)
        group = homework_group(homework)
        emit(phase='start', index=index, done=index - 1, name=title)
        try:
            data = fetch_scores(session, str(homework.get('zuoyeId')
                                             or homework.get('id') or ''))
        except TokenExpired:
            raise
        except Exception as error:  # noqa: BLE001
            message = '%s：%s' % (title, error)
            failed.append({'title': title, 'group': group, 'error': str(error)})
            if logger is not None:
                logger('[%d/%d] [失败] %s' % (index, total, message))
            emit(phase='failed', index=index, done=index, name=title)
            continue

        students = data['students']
        info = summarize(students)
        # 人多的时候最怕悄悄少人：和「应交」对不上就明确标出来
        expected = int(homework.get('zuoyesNum') or 0)
        complete = not expected or len(students) >= expected
        if not complete and logger is not None:
            logger('[!] %s：接口只返回 %d 人，应交 %d 人，可能有学生没取到'
                   % (title, len(students), expected))
        # 文件名照爱问云导出的样式：班级-作业框名.csv，直接放在所选目录里，
        # 不再套一层班级文件夹。
        path = out_root / statistics_filename(homework)
        count = write_csv(path, homework, students)
        files.append({'title': title, 'group': group, 'path': str(path),
                      'rows': count, 'summary': info, 'complete': complete,
                      'expected': expected})
        if logger is not None:
            logger('[%d/%d] [成功] %s -> %s（%d 名学生，已批 %d，平均分 %s）'
                   % (index, total, title, path.name, count,
                      info['corrected'],
                      '-' if info['average'] is None else info['average']))
        emit(phase='done', index=index, done=index, name=title,
             percent=index / total * 100.0 if total else 100.0)

    return {'files': files, 'failed': failed,
            'elapsed': round(time.monotonic() - started, 1),
            'out_root': str(out_root)}


def statistics_filename(homework: dict) -> str:
    """统计 CSV 的文件名：``班级-作业框名.csv``。

    班级取 ``2026027MA+31作业班`` 里的 ``7MA+31``（去掉年份和「作业班」），
    只有拿不到班级名时才退回 groupId。
    """

    group_name = str(homework.get('groupName') or '').strip()
    if group_name:
        prefix = class_short_name(group_name)
    else:
        group_id = homework.get('groupId')
        prefix = str(group_id).strip() if group_id not in (None, '') else ''
    title = safe_filename(homework_title(homework))
    if prefix:
        return '%s-%s.csv' % (prefix, title)
    return '%s.csv' % title


def group_homeworks(items: list[dict]) -> dict[str, list[dict]]:
    """按班级名分组，组内保持「新的在前」。"""

    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(homework_group(item), []).append(item)
    for rows in groups.values():
        rows.sort(key=homework_time, reverse=True)
    return groups


def ordered_groups(groups: dict[str, list[dict]]) -> list[str]:
    """班级顺序：未分类放最后，其余按最新作业时间倒序。"""

    def key(name: str):
        rows = groups.get(name) or [{}]
        return (name == UNCLASSIFIED_GROUP, -homework_time(rows[0]), name)

    return sorted(groups, key=key)


def default_out_root() -> Path:
    """保存位置：桌面「爱问云作业统计」。"""

    home = Path.home()
    for candidate in (home / 'Desktop', home / 'OneDrive' / 'Desktop',
                      home / '桌面', home):
        if candidate.is_dir():
            return candidate / '爱问云作业统计'
    return home / '爱问云作业统计'
