from __future__ import annotations

import datetime
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from feedback_export import (ATTEND_PATH, CONSOLIDATION, CSV_HEADER, HISTORY,
                             PREVIEW, SIGNIN_PATH, STUDENT_DETAIL_PATH,
                             WATCH_PATH, build_feedback_bundles,
                             build_feedback_rows, chinese_number,
                             export_bundle, export_feedback_bundles,
                             feedback_class_label, feedback_component_name,
                             homework_kind, homework_lesson,
                             parse_lesson_number, write_feedback_csv)


def make_student(name, login, uid, group, sign=0, total=0):
    return {
        'username': name,
        'loginname': login,
        'uniqueId': '1_%d' % uid,
        'userId': uid,
        'groupIds': '3874277' if 'β1' in group else '3874278',
        'groupNames': group,
        'signCount': sign,
        'signTotal': total,
    }


class FakeSession:
    def __init__(self):
        self.liveclass = 100

    def fetch_live_info(self, ids):
        return {int(self.liveclass): {
            'startTime': 1794633600000,
            'endTime': 1794640853967,
            'duration': 7216,
            'mediaType': 1,
            'teacherName': '数形许',
        }}

    def post(self, path, payload, **kwargs):
        if path == SIGNIN_PATH:
            return {'code': 0, 'obj': {'total': 2, 'list': [
                make_student('26726陆依桐', 'u1', 10759969,
                             '2026027MA+31β3'),
                make_student('56259陆添琦', 'u2', 11438031,
                             '2026027MA+31β1'),
            ]}}
        if path == ATTEND_PATH:
            return {'code': 0, 'obj': {'total': 1, 'list': [
                {'userId': 10759969, 'watchRecord': 1,
                 'watchDuration': 7084, 'progress': '100%'},
            ]}}
        if path == WATCH_PATH:
            return {'code': 0, 'obj': {
                'listenerInfos': [
                    {'userId': 11438031, 'onlineDuration': 7811,
                     'onstageDuration': 14, 'raiseCount': 2,
                     'joinClassTime': 1794633021399,
                     'leaveClassTime': 1794640853955},
                ],
                'notInClassListenerIds': [],
            }}
        if path == STUDENT_DETAIL_PATH:
            student_id = int(payload.get('studentId') or 0)
            if student_id == 11438031:
                return {'code': 0, 'obj': {
                    'liveclass': {'attendLiveclassNum': 1,
                                  'effectAttendNum': 1,
                                  'onlineDuration': 7811,
                                  'onstageDuration': 14,
                                  'openAudioNum': 2,
                                  'raiseCount': 0, 'star': 0,
                                  'stcNum': 0, 'stcAccuracy': 0},
                    'record': {'watchRecordNum': 0,
                               'watchDuration': 0},
                }}
            return {'code': 0, 'obj': {
                'liveclass': {'attendLiveclassNum': 0,
                              'effectAttendNum': 0,
                              'onlineDuration': 0,
                              'onstageDuration': 0,
                              'openAudioNum': 0,
                              'raiseCount': 0, 'star': 0,
                              'stcNum': 0, 'stcAccuracy': 0},
                'record': {'watchRecordNum': 1,
                           'watchDuration': 7084},
            }}
        if path == 'yxt/servlet/zuoye/getAllZuoyesByZuoyeIdNew':
            return {'code': 0, 'obj': {
                'zuoyeInfo': {'id': 'zy1'},
                'studentZuoyeInfo': [
                    {'studentName': '36673张三', 'studentLoginName': 's1',
                     'status': 11, 'score': 80},
                ],
            }}
        raise AssertionError('unexpected path %s' % path)


class LessonParsingTests(unittest.TestCase):
    def test_chinese_numbers(self):
        self.assertEqual(chinese_number(4), '四')
        self.assertEqual(chinese_number(10), '十')
        self.assertEqual(chinese_number(11), '十一')
        self.assertEqual(chinese_number(12), '十二')
        self.assertEqual(chinese_number(23), '二十三')

    def test_parse_lesson_from_titles_and_rooms(self):
        self.assertEqual(parse_lesson_number('【数·课前预习】专题十一'), 11)
        self.assertEqual(parse_lesson_number('【数·课后巩固】专题十'), 10)
        self.assertEqual(parse_lesson_number('26秋7MA+31（数形许）-11'), 11)
        self.assertEqual(parse_lesson_number('第4节课'), 4)
        self.assertIsNone(parse_lesson_number('2026027MA+31'))

    def test_homework_kind_and_class(self):
        self.assertEqual(homework_kind(
            {'title': '【数·课前预习】专题十一'}), PREVIEW)
        self.assertEqual(homework_kind(
            {'title': '【数·课后巩固】专题十'}), CONSOLIDATION)
        self.assertEqual(homework_kind({'title': '第十周作业'}), None)
        self.assertEqual(homework_lesson(
            {'title': '【数·课前预习】专题十一'}), 11)
        self.assertEqual(feedback_class_label('2026027MA+31作业班'), '7MA+31')
        self.assertEqual(feedback_class_label('26秋7MA+31（数形许）'), '7MA+31')


def make_history(lesson, rid=None, stamp=None):
    return {
        'shortDesc': '26秋7MA+31（数形许）-%d' % lesson,
        'liveclass': 100,
        'fileCommon': {'recordId': rid or 'r%d' % lesson,
                       'createTime': (stamp if stamp is not None else
                                      1794633600000 - lesson * 86400000)},
    }


def make_homework(lesson, kind, zuoye_id=None):
    title = ('【数·课前预习】专题%s' if kind == PREVIEW
             else '【数·课后巩固】专题%s') % chinese_number(lesson)
    return {
        'zuoyeId': zuoye_id or '%s%d' % (kind, lesson),
        'title': title,
        'groupName': '2026027MA+31作业班',
        'sortTime': 1794633600000 - lesson * 86400000,
    }


class BundleAssemblyTests(unittest.TestCase):
    def test_lesson_11_and_4_include_three_components(self):
        history = [make_history(11), make_history(4)]
        homework = [
            make_homework(11, PREVIEW),
            make_homework(10, CONSOLIDATION),
            make_homework(4, PREVIEW),
            make_homework(3, CONSOLIDATION),
        ]
        bundles = build_feedback_bundles(history, homework)
        self.assertEqual([bundle['lesson'] for bundle in bundles], [4, 11])
        self.assertEqual([bundle['title'] for bundle in bundles],
                         ['第四节课反馈', '第十一节课反馈'])
        for bundle in bundles:
            self.assertEqual(bundle['class'], '7MA+31')
            self.assertFalse(bundle['missing'])
            self.assertIsNotNone(bundle['components'][PREVIEW])
            self.assertIsNotNone(bundle['components'][CONSOLIDATION])
            self.assertIsNotNone(bundle['components'][HISTORY])
        lesson11 = bundles[1]['components']
        self.assertEqual(lesson11[PREVIEW]['title'], '【数·课前预习】专题十一')
        self.assertEqual(lesson11[CONSOLIDATION]['title'], '【数·课后巩固】专题十')
        self.assertEqual(lesson11[HISTORY]['shortDesc'], '26秋7MA+31（数形许）-11')

    def test_class_filter_and_time_desc(self):
        history = [
            make_history(4, stamp=1794000000000),
            make_history(11, stamp=1794633600000),
            {'shortDesc': '26秋7M备课-5', 'liveclass': 200,
             'fileCommon': {'recordId': 'junk', 'createTime': 1794633600000}},
        ]
        homework = [
            make_homework(11, PREVIEW),
            make_homework(10, CONSOLIDATION),
            make_homework(4, PREVIEW),
            make_homework(3, CONSOLIDATION),
            {'zuoyeId': 'junk', 'title': '【数·课后巩固】专题六',
             'groupName': '2026027M备课作业班', 'sortTime': 1794633600000},
        ]
        bundles = build_feedback_bundles(
            history, homework, class_names=['2026027MA+31'])
        self.assertEqual([bundle['class'] for bundle in bundles],
                         ['7MA+31', '7MA+31'])
        self.assertEqual([bundle['lesson'] for bundle in bundles], [11, 4])
        self.assertNotIn('7M备课', {bundle['class'] for bundle in bundles})

    def test_history_only_still_matches_single_class_code(self):
        bundles = build_feedback_bundles(
            [make_history(11)], [], class_names=['2026027MA+31'])
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0]['class'], '7MA+31')
        self.assertEqual(bundles[0]['components'][HISTORY]['shortDesc'],
                         '26秋7MA+31（数形许）-11')

    def test_same_class_name_in_two_terms_stays_separate(self):
        history = [
            {'shortDesc': '26暑7MA+31（数形许）-12',
             '_groupNames': ['2026017MA+31β1'],
             'liveclass': 101,
             'fileCommon': {'recordId': 'summer12',
                            'createTime': 1783000000000}},
            {'shortDesc': '26秋7MA+31（数形许）-12',
             '_groupNames': ['2026027MA+31β1'],
             'liveclass': 102,
             'fileCommon': {'recordId': 'autumn12',
                            'createTime': 1794633600000}},
        ]
        homework = [
            {'zuoyeId': 'summer-preview12',
             'title': '【数·课前预习】专题十二',
             'groupName': '2026017MA+31作业班',
             'sortTime': 1783000000000},
            {'zuoyeId': 'summer-consolidation11',
             'title': '【数·课后巩固】专题十一',
             'groupName': '2026017MA+31作业班',
             'sortTime': 1783000000000},
            {'zuoyeId': 'autumn-preview12',
             'title': '【数·课前预习】专题十二',
             'groupName': '2026027MA+31作业班',
             'sortTime': 1794633600000},
            {'zuoyeId': 'autumn-consolidation11',
             'title': '【数·课后巩固】专题十一',
             'groupName': '2026027MA+31作业班',
             'sortTime': 1794633600000},
        ]
        bundles = build_feedback_bundles(
            history, homework,
            class_names=['2026017MA+31', '2026027MA+31'])
        self.assertEqual(len(bundles), 2)
        by_class = {bundle['class']: bundle for bundle in bundles}
        self.assertEqual(set(by_class), {'26暑7MA+31', '26秋7MA+31'})

        summer = by_class['26暑7MA+31']
        autumn = by_class['26秋7MA+31']
        self.assertEqual(summer['components'][HISTORY]['fileCommon']
                         ['recordId'], 'summer12')
        self.assertEqual(autumn['components'][HISTORY]['fileCommon']
                         ['recordId'], 'autumn12')
        self.assertEqual(summer['components'][PREVIEW]['zuoyeId'],
                         'summer-preview12')
        self.assertEqual(autumn['components'][PREVIEW]['zuoyeId'],
                         'autumn-preview12')
        self.assertEqual(summer['components'][CONSOLIDATION]['zuoyeId'],
                         'summer-consolidation11')
        self.assertEqual(autumn['components'][CONSOLIDATION]['zuoyeId'],
                         'autumn-consolidation11')
        self.assertEqual(
            feedback_component_name(summer, PREVIEW),
            '26暑7MA+31-【数·课前预习】专题十二.csv')

    def test_only_lesson_11_consolidation_marks_lesson_12_missing(self):
        bundles = build_feedback_bundles(
            [], [make_homework(11, CONSOLIDATION)])
        self.assertEqual(len(bundles), 1)
        bundle = bundles[0]
        self.assertEqual(bundle['lesson'], 12)
        self.assertEqual(bundle['title'], '第十二节课反馈')
        self.assertEqual(bundle['components'][CONSOLIDATION]['title'],
                         '【数·课后巩固】专题十一')
        self.assertIsNone(bundle['components'][PREVIEW])
        self.assertIsNone(bundle['components'][HISTORY])
        self.assertEqual(set(bundle['missing']), {PREVIEW, HISTORY})


class BundleExportTests(unittest.TestCase):
    def test_export_writes_class_and_lesson_folders(self):
        history = [make_history(11), make_history(4)]
        homework = [
            make_homework(11, PREVIEW),
            make_homework(10, CONSOLIDATION),
            make_homework(11, CONSOLIDATION),
            make_homework(4, PREVIEW),
            make_homework(3, CONSOLIDATION),
        ]
        bundles = build_feedback_bundles(history, homework)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            result = export_feedback_bundles(FakeSession(), bundles, out)
            self.assertFalse(result['failed'])
            lesson11 = out / '7MA+31' / '第十一节课反馈'
            lesson4 = out / '7MA+31' / '第四节课反馈'
            lesson12 = out / '7MA+31' / '第十二节课反馈'
            self.assertTrue(lesson11.is_dir())
            self.assertTrue(lesson4.is_dir())
            self.assertTrue(lesson12.is_dir())
            self.assertTrue((lesson11 / '7MA+31-【数·课前预习】专题十一.csv').is_file())
            self.assertTrue((lesson11 / '7MA+31-【数·课后巩固】专题十.csv').is_file())
            self.assertTrue((lesson11 /
                             '历史课堂权限-第十一节课统计报告.csv').is_file())
            self.assertTrue((lesson4 / '7MA+31-【数·课前预习】专题四.csv').is_file())
            self.assertTrue((lesson4 / '7MA+31-【数·课后巩固】专题三.csv').is_file())
            self.assertTrue((lesson4 /
                             '历史课堂权限-第四节课统计报告.csv').is_file())
            self.assertTrue(lesson12.is_dir())
            self.assertEqual(list(lesson12.glob('*.txt')), [])
            self.assertFalse(any(path.suffix.lower() == '.txt'
                                 for path in out.rglob('*')))

    def test_existing_class_folder_is_not_nested_again(self):
        bundles = build_feedback_bundles(
            [make_history(11)],
            [make_homework(11, PREVIEW), make_homework(10, CONSOLIDATION)])
        with tempfile.TemporaryDirectory() as directory:
            class_root = Path(directory) / '7MA+31'
            result = export_feedback_bundles(
                FakeSession(), bundles, class_root)
            self.assertFalse(result['failed'])
            self.assertTrue(
                (class_root / '第十一节课反馈').is_dir())
            self.assertFalse((class_root / '7MA+31').exists())

    def test_same_name_terms_keep_separate_folders_in_class_root(self):
        history = [
            {'shortDesc': '26暑7MA+31（数形许）-12',
             '_groupNames': ['2026017MA+31β1'],
             'liveclass': 101,
             'fileCommon': {'recordId': 'summer12',
                            'createTime': 1783000000000}},
            {'shortDesc': '26秋7MA+31（数形许）-12',
             '_groupNames': ['2026027MA+31β1'],
             'liveclass': 102,
             'fileCommon': {'recordId': 'autumn12',
                            'createTime': 1794633600000}},
        ]
        bundles = build_feedback_bundles(history, [])
        with tempfile.TemporaryDirectory() as directory:
            class_root = Path(directory) / '7MA+31'
            result = export_feedback_bundles(
                FakeSession(), bundles, class_root)
            self.assertFalse(result['failed'])
            history_name = '历史课堂权限-第十二节课统计报告.csv'
            self.assertTrue(
                (class_root / '26暑7MA+31' / '第十二节课反馈' /
                 history_name).is_file())
            self.assertTrue(
                (class_root / '26秋7MA+31' / '第十二节课反馈' /
                 history_name).is_file())

    def test_unselected_file_is_not_written(self):
        bundles = build_feedback_bundles(
            [make_history(11)],
            [make_homework(11, PREVIEW), make_homework(10, CONSOLIDATION)])
        bundle = dict(bundles[0])
        bundle['selected'] = [PREVIEW, HISTORY]
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            folder = out / '7MA+31' / '第十一节课反馈'
            folder.mkdir(parents=True)
            keep = folder / '课前预习-用户备注.txt'
            keep.write_text('keep', encoding='utf-8')
            result = export_feedback_bundles(
                FakeSession(), [bundle], out)
            self.assertTrue((folder / '7MA+31-【数·课前预习】专题十一.csv').is_file())
            self.assertTrue((folder / '历史课堂权限-第十一节课统计报告.csv').is_file())
            self.assertFalse((folder / '7MA+31-【数·课后巩固】专题十.csv').exists())
            self.assertTrue(keep.is_file())
            self.assertFalse(any(path.suffix.lower() == '.txt'
                                 for path in folder.rglob('*')
                                 if path != keep))
            self.assertEqual(result['files'][0]['unselected'],
                             ['上一节课课后巩固作业'])


class FormatTests(unittest.TestCase):
    def test_header_matches_attachment(self):
        self.assertEqual(len(CSV_HEADER), 22)
        self.assertEqual(CSV_HEADER[0], '学生姓名')
        self.assertEqual(CSV_HEADER[1], ' 用户名')
        self.assertEqual(CSV_HEADER[-1], ' 最后离开课堂时间')

    def test_write_keeps_crlf_bom_and_missing_sign_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'x.csv'
            write_feedback_csv(path, [['张三', 'u1', 'A班', '缺勤',
                                       '0', '语音实时课堂', '课堂', '老师',
                                       '2026-11-14 13:20:00', '121', '0',
                                       '0', '-\t', '-', '-', '-', '-',
                                       '未观看', '-', '-', '-', '-']])
            data = path.read_bytes()
            self.assertTrue(data.startswith(b'\xef\xbb\xbf'))
            self.assertIn(b'\r\n', data)
            self.assertIn(b'"-\t"', data)
            self.assertTrue(data.endswith(b'\r\n'))


class RowsTests(unittest.TestCase):
    def test_merge_attachment_values(self):
        students = [
            make_student('26726陆依桐', 'u1', 10759969,
                         '2026027MA+31β3'),
            make_student('56259陆添琦', 'u2', 11438031,
                         '2026027MA+31β1'),
        ]
        attend = [
            {'userId': 10759969, 'watchRecord': 1,
             'watchDuration': 7084, 'progress': '100%'},
            {'userId': 11438031, 'watchRecord': 0},
        ]
        watch = {'listenerInfos': [
            {'userId': 11438031, 'onlineDuration': 7811,
             'onstageDuration': 14, 'raiseCount': 2,
             'joinClassTime': 1794633021399,
             'leaveClassTime': 1794640853955},
        ], 'notInClassListenerIds': []}
        live = {'startTime': 1794633600000, 'endTime': 1794640853967,
                'duration': 7216, 'mediaType': 1, 'teacherName': '数形许'}
        rows = build_feedback_rows(
            students, attend, watch, live,
            {'shortDesc': '26秋7MA+31（数形许）-10'}, '26秋7MA+31')
        self.assertEqual([row[0] for row in rows],
                         ['26726陆依桐', '56259陆添琦'])
        self.assertEqual(rows[0][2], '2026027MA+31β3')
        self.assertEqual(rows[0][3], '缺勤')
        self.assertEqual(rows[0][4], '119')
        self.assertEqual(rows[0][12], '-\t')
        self.assertEqual(rows[0][17], '已观看')
        self.assertEqual(rows[0][18], '119')
        self.assertEqual(rows[0][19], '100%')
        self.assertEqual(rows[1][2], '2026027MA+31β1')
        self.assertEqual(rows[1][4], '131')
        self.assertEqual(rows[1][10], '131')
        self.assertEqual(rows[1][11], '1')
        # 生产格式化固定用 UTC+8；测试不能再依赖 runner 的本地时区。
        expected_join = datetime.datetime.fromtimestamp(
            1794633021399 / 1000,
            tz=datetime.timezone(datetime.timedelta(hours=8))
        ).strftime('%Y-%m-%d %H:%M:%S')
        self.assertEqual(rows[1][20], expected_join)

    def test_details_drive_attendance_reward_and_mic(self):
        students = [
            make_student('26726陆依桐', 'u1', 10759969,
                         '2026027MA+31β3'),
            make_student('56259陆添琦', 'u2', 11438031,
                         '2026027MA+31β1'),
        ]
        details = {
            '10759969': {
                'liveclass': {'attendLiveclassNum': 0,
                              'effectAttendNum': 0,
                              'openAudioNum': 0, 'raiseCount': 0,
                              'star': 0, 'stcNum': 0, 'stcAccuracy': 0},
                'record': {'watchRecordNum': 1, 'watchDuration': 7084},
            },
            '11438031': {
                'liveclass': {'attendLiveclassNum': 1,
                              'effectAttendNum': 1,
                              'onlineDuration': 7811,
                              'onstageDuration': 14,
                              'openAudioNum': 2, 'raiseCount': 0,
                              'star': 0, 'stcNum': 0, 'stcAccuracy': 0},
                'record': {'watchRecordNum': 0, 'watchDuration': 0},
            },
        }
        rows = build_feedback_rows(
            students, [], {'listenerInfos': [], 'notInClassListenerIds': []},
            {'startTime': 1794633600000, 'endTime': 1794640853967,
             'mediaType': 1, 'teacherName': '数形许'},
            {'shortDesc': '26秋7MA+31（数形许）-10'}, '26秋7MA+31',
            details=details)
        self.assertEqual(rows[0][3], '缺勤')
        self.assertEqual(rows[1][3], '出勤')
        self.assertEqual(rows[1][4], '131')
        self.assertEqual(rows[1][14], '0')
        self.assertEqual(rows[1][15], '0')
        self.assertEqual(rows[1][16], '2')


class BundleTests(unittest.TestCase):
    def test_export_contains_permission_and_homework(self):
        record = {
            'shortDesc': '26秋7MA+31（数形许）-10',
            'liveclass': 100,
            'fileId': 'f1',
            'fileCommon': {'recordId': 'r1', 'createTime': 1794633600000},
        }
        homework = {
            'zuoyeId': 'zy1',
            'title': '第十周作业',
            'groupName': '2026027MA+31作业班',
            'sortTime': 1794633600000,
        }
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            result = export_bundle(
                FakeSession(), [record], [homework], out,
                group_of=lambda item: '26秋7MA+31')
            self.assertFalse(result['failed'])
            self.assertEqual(len(result['files']), 2)
            kinds = {item['kind'] for item in result['files']}
            self.assertEqual(kinds, {'课堂权限', '作业成绩'})
            history = next(path for path in out.rglob('*.csv')
                           if '统计报告' in path.name)
            homework_path = next(path for path in out.rglob('*.csv')
                                 if '统计报告' not in path.name)
            self.assertTrue(history.is_relative_to(out / '26秋7MA+31'))
            self.assertTrue(homework_path.is_relative_to(
                out / '2026027MA+31作业班'))
            self.assertIn('学生姓名, 用户名', history.read_text(
                encoding='utf-8-sig'))
            self.assertIn('序号,姓名', homework_path.read_text(
                encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
