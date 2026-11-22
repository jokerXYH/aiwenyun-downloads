# -*- coding: utf-8 -*-
"""作业导出（作业框清单 + 每个作业框的学生成绩 CSV）的纯函数测试。

这些用例不联网：接口响应用 dict 直接喂，写盘只写临时目录。
"""

import csv
import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from homework_export import (CSV_HEADER, HomeworkCancelled, class_short_name,
                             fetch_homeworks, statistics_filename,
                             statistics_status,
                             group_homeworks, homework_group, homework_time,
                             homework_title, ordered_groups, safe_filename,
                             score_rows, split_student_name, status_label,
                             summarize, write_csv)


def make_homework(zuoye_id='zy1', title='【数·课后巩固】专题十一',
                  group='2026027MAA71作业班', created='2026-11-20 10:00',
                  total=14, submitted=3, corrected=1):
    stamp = int(datetime.datetime.strptime(
        created, '%Y-%m-%d %H:%M').timestamp() * 1000)
    return {'zuoyeId': zuoye_id, 'title': title, 'groupName': group,
            'groupId': 3874001, 'zuoyesNum': total, 'tijiaoNum': submitted,
            'pigaiNum': corrected, 'sortTime': stamp, 'createTime': stamp}


def make_student(name='36673张三', status=12, score=80, accuracy=80,
                 correct=4, err=1, comment='做得好', login='jifangjyS16802',
                 submitted='2026-11-20 20:37', corrected_at='2026-11-20 23:31',
                 dz=None):
    row = {'studentName': name, 'studentLoginName': login, 'status': status,
           'score': score, 'accuracy': accuracy, 'correctNum': correct,
           'errNum': err, 'comment': comment}
    if submitted:
        row['tijiaoTime'] = int(datetime.datetime.strptime(
            submitted, '%Y-%m-%d %H:%M').timestamp() * 1000)
    if corrected_at:
        row['pigaiTime'] = int(datetime.datetime.strptime(
            corrected_at, '%Y-%m-%d %H:%M').timestamp() * 1000)
    if dz is not None:
        row['score_dz'] = dz
    return row


class FakeSession:
    """按页返回作业清单的假会话。"""

    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def post(self, path, payload):
        self.calls.append((path, payload))
        index = len(self.calls) - 1
        page = self.pages[index] if index < len(self.pages) else {'obj': {}}
        return page


class StatusAndNameTests(unittest.TestCase):
    def test_status_labels(self):
        # 页面徽标用的文案
        self.assertEqual(status_label(9), '未查阅')
        self.assertEqual(status_label(10), '已查阅')      # 打开过但没交
        self.assertEqual(status_label(11), '已提交')
        self.assertEqual(status_label(12), '待订正')
        self.assertEqual(status_label(13), '已订正')
        self.assertEqual(status_label(99), '已完成')
        self.assertEqual(status_label('99'), '已完成')
        self.assertEqual(status_label(77), '状态77')
        self.assertEqual(status_label(None), '')

    def test_statistics_status_labels(self):
        # 统计 CSV 用的文案（从平台真实导出文件反推）
        self.assertEqual(statistics_status(9), '未查阅')
        self.assertEqual(statistics_status(10), '已查阅')
        self.assertEqual(statistics_status(11), '已提交')
        self.assertEqual(statistics_status(12), '已完成')
        self.assertEqual(statistics_status(13), '已完成')
        self.assertEqual(statistics_status(14), '已完成')
        self.assertEqual(statistics_status(99), '已完成')

    def test_split_student_name(self):
        self.assertEqual(split_student_name('36673孟子雯'), ('36673', '孟子雯'))
        self.assertEqual(split_student_name('王晓明'), ('', '王晓明'))
        self.assertEqual(split_student_name(' 123 李四 '), ('123', '李四'))
        self.assertEqual(split_student_name(''), ('', ''))

    def test_safe_filename(self):
        self.assertEqual(safe_filename('【数·课后巩固】专题十一'),
                         '【数·课后巩固】专题十一')
        self.assertEqual(safe_filename('a/b:c*?"<>|'), 'a_b_c_')
        self.assertEqual(safe_filename('   '), '未命名作业')
        self.assertEqual(safe_filename('..'), '未命名作业')

    def test_homework_helpers(self):
        item = make_homework()
        self.assertEqual(homework_title(item), '【数·课后巩固】专题十一')
        self.assertEqual(homework_group(item), '2026027MAA71作业班')
        self.assertTrue(homework_time(item) > 0)
        self.assertEqual(homework_title({}), '未命名作业')
        self.assertEqual(homework_group({}), '未分类')


class ScoreRowsTests(unittest.TestCase):
    def test_rows_are_sorted_and_mapped(self):
        students = [make_student('36675乙', score=90),
                    make_student('26029甲', score=40),
                    make_student('36001丙')]
        rows = score_rows(students)
        self.assertEqual([row[1] for row in rows],
                         ['26029甲', '36001丙', '36675乙'])
        self.assertEqual([row[0] for row in rows], ['1', '2', '3'])
        first = rows[0]
        self.assertEqual(first[2], 'jifangjyS16802')     # 用户名
        self.assertEqual(first[3], '已完成')              # 统计导出里 12 也写已完成
        self.assertEqual(first[4], '否')                  # 查阅答案
        self.assertEqual(first[8], '40')                  # 得分
        self.assertEqual(first[9], '80%')                 # 正确率
        self.assertEqual(first[10], '')                   # 总积分
        self.assertEqual(first[11], '')                   # 批改评级（没给就是空）
        self.assertEqual(first[13], '做得好')             # 批改评语

    def test_unsubmitted_rows_keep_empty_cells(self):
        rows = score_rows([make_student('36673张三', status=9, score=None,
                                        accuracy=None, correct=None, err=None,
                                        comment='', submitted=None,
                                        corrected_at=None)])
        row = rows[0]
        self.assertEqual(row[1], '36673张三')
        self.assertEqual(row[3], '未查阅')
        self.assertEqual(row[5], '')
        self.assertEqual(row[8], '')
        self.assertEqual(row[9], '')
        self.assertEqual(len(row), len(CSV_HEADER))

    def test_submitted_status_label(self):
        row = score_rows([make_student(status=11, score=None, accuracy=None,
                                       comment='')])[0]
        self.assertEqual(row[3], '已提交')

    def test_revision_fields(self):
        student = make_student('36673张三', status=13)
        student['grade_dz'] = 'A'
        student['comment_dz'] = '订正得不错'
        student['grade_revision_score'] = 5
        row = score_rows([student])[0]
        self.assertEqual(row[3], '已完成')
        self.assertEqual(row[14], 'A')
        self.assertEqual(row[15], '5')
        self.assertEqual(row[16], '订正得不错')


class SummarizeTests(unittest.TestCase):
    def test_counts(self):
        students = [make_student(status=9, score=None),
                    make_student(status=10, score=None),   # 打开过但没交
                    make_student(status=11, score=None),
                    make_student(status=12, score=80),
                    make_student(status=99, score=100)]
        info = summarize(students)
        self.assertEqual(info['total'], 5)
        self.assertEqual(info['submitted'], 3)
        self.assertEqual(info['corrected'], 2)
        self.assertEqual(info['average'], 90.0)

    def test_empty(self):
        info = summarize([])
        self.assertEqual(info['total'], 0)
        self.assertIsNone(info['average'])


class GroupTests(unittest.TestCase):
    def test_group_and_order(self):
        items = [make_homework('a', group='甲班', created='2026-11-01 10:00'),
                 make_homework('b', group='乙班', created='2026-11-05 10:00'),
                 make_homework('c', group='甲班', created='2026-11-09 10:00'),
                 {'zuoyeId': 'd', 'title': 'x', 'sortTime': 1}]
        groups = group_homeworks(items)
        self.assertEqual(sorted(groups), sorted(['乙班', '甲班', '未分类']))
        self.assertEqual([row['zuoyeId'] for row in groups['甲班']],
                         ['c', 'a'])
        self.assertEqual(ordered_groups(groups), ['甲班', '乙班', '未分类'])


class FetchHomeworksTests(unittest.TestCase):
    def test_pagination_and_range(self):
        page1 = {'obj': {'list': [
            make_homework('z1', created='2026-11-20 10:00'),
            make_homework('z2', created='2026-11-10 10:00')],
            'hasMore': True, 'mark': 'm1'}}
        page2 = {'obj': {'list': [
            make_homework('z3', created='2026-10-01 10:00'),
            make_homework('z4', created='2026-09-01 10:00')],
            'hasMore': False, 'mark': ''}}
        session = FakeSession([page1, page2])
        start = int(datetime.datetime(2026, 10, 20).timestamp() * 1000)
        end = int(datetime.datetime(2026, 11, 22).timestamp() * 1000)
        items = fetch_homeworks(session, start, end, on_log=lambda _m: None)
        self.assertEqual([item['zuoyeId'] for item in items], ['z1', 'z2'])
        self.assertEqual(session.calls[0][1]['limit'], 20)
        self.assertEqual(session.calls[1][1]['mark'], 'm1')

    def test_duplicate_ids_are_dropped(self):
        page1 = {'obj': {'list': [make_homework('z1')], 'hasMore': True,
                         'mark': 'm1'}}
        page2 = {'obj': {'list': [make_homework('z1')], 'hasMore': False}}
        session = FakeSession([page1, page2])
        items = fetch_homeworks(session, 0, 99999999999999,
                                on_log=lambda _m: None)
        self.assertEqual(len(items), 1)

    def test_cancel(self):
        import threading
        event = threading.Event()
        event.set()
        with self.assertRaises(HomeworkCancelled):
            fetch_homeworks(FakeSession([]), 0, 0, cancel_event=event)


class WriteCsvTests(unittest.TestCase):
    def test_csv_shape(self):
        homework = make_homework()
        students = [make_student('36673张三', score=80),
                    make_student('26029李四', status=9, score=None,
                                 submitted=None, corrected_at=None)]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / statistics_filename(homework)
            count = write_csv(path, homework, students)
            self.assertEqual(count, 2)
            self.assertEqual(path.name, '7MAA71-【数·课后巩固】专题十一.csv')
            raw = path.read_bytes()
            with path.open(encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.reader(handle))
        self.assertTrue(raw.startswith(b'\xef\xbb\xbf'), '需要 UTF-8 BOM')
        self.assertIn(b'\r\n', raw, '需要 CRLF 换行')
        self.assertEqual(tuple(rows[0]), CSV_HEADER)
        self.assertEqual(len(rows), 1 + 2)
        self.assertEqual(rows[1][1], '26029李四')
        self.assertEqual(rows[2][1], '36673张三')
        self.assertIn('"1","26029李四","jifangjyS16802","未查阅","否",',
                      raw.decode('utf-8-sig'))
        self.assertIn(',,', raw.decode('utf-8-sig'), '空值不写引号')


class FilenameTests(unittest.TestCase):
    def test_statistics_filename(self):
        self.assertEqual(statistics_filename(make_homework()),
                         '7MAA71-【数·课后巩固】专题十一.csv')
        self.assertEqual(statistics_filename({'title': 'x/y'}),
                         'x_y.csv')

    def test_class_short_name(self):
        self.assertEqual(class_short_name('2026027MA+31作业班'), '7MA+31')
        self.assertEqual(class_short_name('2026027MAA71作业班'), '7MAA71')
        self.assertEqual(class_short_name('2026017MAA71作业班'), '7MAA71')
        self.assertEqual(class_short_name('高三1班'), '高三1班')
        self.assertEqual(class_short_name(''), '')

    def test_filename_uses_class_name(self):
        item = make_homework(group='2026027MA+31作业班')
        self.assertEqual(statistics_filename(item),
                         '7MA+31-【数·课后巩固】专题十一.csv')
        fallback = make_homework(group='')
        fallback['groupName'] = ''
        self.assertEqual(statistics_filename(fallback),
                         '3874001-【数·课后巩固】专题十一.csv')


if __name__ == '__main__':
    unittest.main()
