# -*- coding: utf-8 -*-
"""登录与板书导出的纯函数测试（不联网）。"""

import datetime
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from banshu_export import (RANGE_PRESETS, UNCLASSIFIED_GROUP,
                           BatchCancelled, class_base, class_group,
                           class_short_label, collect_pages,
                           enrich_record_groups, export_records,
                           extract_class_names, fallback_class_names,
                           fetch_class_names, fetch_records,
                           group_board_records, group_display_label,
                           group_display_names, group_sort_key,
                           group_term_label, match_class_name,
                           ordered_group_names, order_pages, record_time,
                           resolve_range, sanitize,
                           sort_records_newest_first, term_label,
                           term_from_time)
from login import (LoginError, ROLE_STUDENT, ROLE_TEACHER, build_payload,
                   scrub, transform_passwd)


class BuildPayloadTests(unittest.TestCase):
    def test_basic_shape(self):
        payload = build_payload('teacher01', 'abc123', ROLE_TEACHER,
                                {'osInfo': 'x', 'deviceId': 'windows-1',
                                 'deviceName': 'pc', 'systemInfo': 'y',
                                 'clientVersion': '5.73.370',
                                 'version': '24.8.8_5.73.370'})
        self.assertEqual(payload['rawName'], 'teacher01')
        self.assertEqual(payload['name'], 'teacher01')
        self.assertEqual(payload['loginName'], 'teacher01')
        self.assertEqual(payload['passwd'], 'abc123')
        self.assertEqual(payload['role'], ROLE_TEACHER)
        self.assertEqual(payload['deviceId'], 'windows-1')

    def test_student_role(self):
        payload = build_payload('s1', 'x', ROLE_STUDENT, {})
        self.assertEqual(payload['role'], ROLE_STUDENT)


class TransformPasswdTests(unittest.TestCase):
    MD5 = 'e99a18c428cb38d5f260853678922e03'

    def test_raw_password_is_md5ed(self):
        self.assertEqual(transform_passwd('abc123'), self.MD5)

    def test_plain_md5_passthrough(self):
        self.assertEqual(transform_passwd(self.MD5), self.MD5)

    def test_p_prefix_passthrough(self):
        self.assertEqual(transform_passwd('P__' + self.MD5),
                         'P__' + self.MD5)

    def test_obfuscated_form_to_p_prefix(self):
        obf = 'e99a1a8c428cb38d5f260853678922e03__P'
        self.assertEqual(transform_passwd(obf), 'P__' + self.MD5)

    def test_whitespace_stripped(self):
        self.assertEqual(transform_passwd('  abc123 \n'), self.MD5)

    def test_empty(self):
        self.assertEqual(transform_passwd(''), '')
        self.assertEqual(transform_passwd(None), '')


class ScrubTests(unittest.TestCase):
    def test_password_removed(self):
        text = scrub('登录失败 pw123 结束', 'pw123')
        self.assertNotIn('pw123', text)
        self.assertIn('***', text)

    def test_token_removed(self):
        token = '4041-0-45442-1a2b3c4d5e6f7a8b-1795300000000-5.73.370-PC-ai'
        text = scrub('bad %s token' % token)
        self.assertNotIn('45442', text)
        self.assertNotIn('1795300000000', text)


class NamingTests(unittest.TestCase):
    def test_class_group(self):
        self.assertEqual(class_group('26秋7MAA12（数形许）-11'), '26秋7MAA12')
        self.assertEqual(class_group('26秋7MA+31（数形许）-11'), '26秋7MA+31')
        self.assertEqual(class_group('26秋8M批课-11'), '26秋8M批课')
        self.assertEqual(class_group('专题9斗课'), '专题9斗课')

    def test_class_base_merges_greek_variants(self):
        self.assertEqual(class_base('2026027MA+31β1'), '2026027MA+31')
        self.assertEqual(class_base('2026027MA+31β2（数形许）'), '2026027MA+31')
        self.assertEqual(class_base('2026027MA+31β3'), '2026027MA+31')
        self.assertEqual(class_base('2026027MA+31α1'), '2026027MA+31')
        self.assertEqual(class_base('2026027MA+31作业班'), '2026027MA+31作业班')
        self.assertEqual(class_base('2026027MA+31'), '2026027MA+31')

    def test_class_group_merges_greek_variants(self):
        self.assertEqual(
            class_group('2026027MA+31β2（数形许）-11'), '2026027MA+31')
        self.assertEqual(
            class_group('2026027MA+31α1（数形许）-11'), '2026027MA+31')
        self.assertEqual(
            class_group('2026027MA+31作业班（数形许）-11'),
            '2026027MA+31作业班')

    def test_sanitize(self):
        self.assertEqual(sanitize('a/b:c*d?"<>|'), 'a_b_c_d_____')
        self.assertEqual(sanitize('  '), '未命名课堂')


class ClassGroupingTests(unittest.TestCase):
    def test_match_official_name_in_classroom_name(self):
        names = ['26秋7MAA12', '26秋8MAA12']
        self.assertEqual(
            match_class_name('26秋7MAA12（数形许）-11', names),
            '26秋7MAA12')
        self.assertEqual(
            match_class_name('26秋8MAA12(数形许)-11', names),
            '26秋8MAA12')

    def test_match_teacher_note_and_spacing(self):
        names = ['26秋7MAA12（数形许）']
        self.assertEqual(
            match_class_name('26 秋 7MAA12-11', names),
            '26秋7MAA12（数形许）')

    def test_match_fullwidth_digits(self):
        self.assertEqual(
            match_class_name('２６秋７MAA１２-11', ['26秋7MAA12']),
            '26秋7MAA12')

    def test_prefix_class_does_not_match_longer_series(self):
        names = ['26秋7M']
        self.assertEqual(match_class_name('26秋7M-11', names), '26秋7M')
        self.assertEqual(match_class_name('26秋7MAA12-11', names), '')
        self.assertEqual(match_class_name('26秋7M批课-11', names), '')

    def test_unmatched_goes_to_misc(self):
        records = [
            {'shortDesc': '26秋7MAA12（数形许）-11'},
            {'shortDesc': '26秋7M批课-11'},
            {'shortDesc': '26秋8M备课-12'},
            {'shortDesc': '26秋7MAA12（数形许）-12'},
        ]
        groups = group_board_records(records, ['26秋7MAA12'])
        self.assertEqual(groups['26秋7MAA12'], [0, 3])
        self.assertEqual(groups[UNCLASSIFIED_GROUP], [1, 2])
        self.assertEqual(list(groups)[-1], UNCLASSIFIED_GROUP)

    def test_no_official_list_falls_back_but_keeps_admin_series_misc(self):
        records = [
            {'shortDesc': '26秋7MAA12（数形许）-11'},
            {'shortDesc': '26秋7M批课-11'},
            {'shortDesc': '26秋8M备课-12'},
        ]
        groups = group_board_records(records, [])
        self.assertEqual(groups['26秋7MAA12'], [0])
        self.assertEqual(groups[UNCLASSIFIED_GROUP], [1, 2])
        unknown = group_board_records([{'shortDesc': ''}], [])
        self.assertEqual(list(unknown), [UNCLASSIFIED_GROUP])

    def test_fallback_skips_names_that_do_not_look_like_classes(self):
        names = fallback_class_names([
            {'shortDesc': '26秋7MAA12（数形许）-11'},
            {'shortDesc': '26秋数形许十一讲练课'},
            {'shortDesc': '专题9斗课'},
            {'shortDesc': '确界孙的课堂'},
        ])
        self.assertEqual(names, ['26秋7MAA12'])

    def test_group_merges_greek_variants_of_official_classes(self):
        records = [
            {'shortDesc': '2026027MA+31β1（数形许）-11'},
            {'shortDesc': '2026027MA+31β2（数形许）-11'},
            {'shortDesc': '2026027MA+31α1（数形许）-11'},
            {'shortDesc': '2026027MA+31β3（数形许）-11'},
            {'shortDesc': '2026027MA+31作业班（数形许）-11'},
            {'shortDesc': '专题9斗课'},
        ]
        names = ['2026027MA+31β1', '2026027MA+31β2', '2026027MA+31β3',
                 '2026027MA+31α1', '2026027MA+31作业班']
        groups = group_board_records(records, names)
        self.assertEqual(groups['2026027MA+31'], [0, 1, 2, 3])
        self.assertEqual(groups['2026027MA+31作业班'], [4])
        self.assertEqual(groups[UNCLASSIFIED_GROUP], [5])

    def test_enrich_reports_chunk_progress(self):
        records = [{'liveclass': index} for index in range(1, 61)]
        calls = []

        class FakeSession:
            def fetch_live_info(self, ids, chunk_size=50):
                return {item: {'groups': {'g': '2026027MA+31β1'}}
                        for item in ids}

        enrich_record_groups(FakeSession(), records,
                             on_progress=lambda done, total:
                             calls.append((done, total)))
        self.assertEqual(calls, [(50, 60), (60, 60)])

    def test_live_info_groups_map_lessons_to_official_classes(self):
        records = [
            {'shortDesc': '26秋7MA+31（数形许）-11', 'liveclass': 101},
            {'shortDesc': '26秋7MAA71（数形许）-11', 'liveclass': 102},
            {'shortDesc': '专题9斗课', 'liveclass': 103},
        ]

        class FakeSession:
            def fetch_live_info(self, ids, chunk_size=50):
                return {
                    101: {'groups': {'g1': '2026027MA+31β1',
                                    'g2': '2026027MA+31β3'}},
                    102: {'groups': {'g3': '2026027MAA71β2'}},
                }

        mapping = enrich_record_groups(FakeSession(), records)
        self.assertEqual(mapping[101],
                         ['2026027MA+31β1', '2026027MA+31β3'])
        self.assertEqual(records[0]['_groupNames'],
                         ['2026027MA+31β1', '2026027MA+31β3'])
        groups = group_board_records(records, [])
        self.assertEqual(groups['2026027MA+31'], [0])
        self.assertEqual(groups['2026027MAA71'], [1])
        self.assertEqual(groups[UNCLASSIFIED_GROUP], [2])

    def test_fetch_records_can_be_cancelled_between_pages(self):
        cancel = threading.Event()

        class FakeSession:
            calls = 0

            def post(self, path, body, request_timeout=None):
                self.calls += 1
                cancel.set()
                return {'code': 0, 'obj': {'list': [{'id': self.calls}],
                                            'total': 10}}

        session = FakeSession()
        with self.assertRaises(BatchCancelled):
            fetch_records(session, 0, 1, page_size=1, cancel_event=cancel)
        self.assertEqual(session.calls, 1)

    def test_newest_first_and_group_order_by_latest_lesson(self):
        records = [
            {'shortDesc': 'A-1', 'createTime': 100},
            {'shortDesc': 'A-2', 'fileCommon': {'createTime': 300}},
            {'shortDesc': 'B-1', 'createTime': 200},
            {'shortDesc': 'X-1', 'createTime': 50},
        ]
        ordered = sort_records_newest_first(records)
        self.assertEqual([r['shortDesc'] for r in ordered],
                         ['A-2', 'B-1', 'A-1', 'X-1'])
        self.assertEqual(record_time(ordered[0]), 300)
        groups = {'A': [0, 2], 'B': [1], UNCLASSIFIED_GROUP: [3]}
        self.assertEqual(ordered_group_names(groups, ordered),
                         ['A', 'B', UNCLASSIFIED_GROUP])

    def test_export_records_folders_follow_ui_groups(self):
        records = [
            {'shortDesc': '26秋7MA+31（数形许）-11', 'liveclass': 1},
            {'shortDesc': '专题9斗课', 'liveclass': 2},
        ]
        calls = []

        def fake_export(session, record, out_root, sort_mode, **kwargs):
            calls.append(kwargs.get('group_name'))
            return Path(out_root) / str(kwargs.get('group_name')) / 'x.pdf'

        with patch('banshu_export.export_class', side_effect=fake_export):
            result = export_records(
                object(), records, Path('out'), 'time', max_workers=1,
                group_of=lambda r: ('2026027MA+31'
                                    if r['liveclass'] == 1
                                    else UNCLASSIFIED_GROUP))
        self.assertEqual(calls, ['2026027MA+31', '杂项_未分类'])
        self.assertEqual(set(result['groups']),
                         {'2026027MA+31', '杂项_未分类'})
        self.assertEqual(result['failed'], [])

    def test_extract_class_names_from_common_payloads(self):
        payload = {
            'code': 0,
            'obj': {
                'list': [
                    {'groupName': '26秋7MAA12', 'id': 101},
                    {'name': '26秋8MAA12', 'id': 102},
                ],
                'total': 2,
            },
        }
        self.assertEqual(extract_class_names(payload),
                         ['26秋7MAA12', '26秋8MAA12'])
        self.assertEqual(
            extract_class_names({'obj': {'1': '26秋7MAA12'}}),
            ['26秋7MAA12'])
        self.assertEqual(
            extract_class_names(
                {'obj': {'groups': {'g_101': '26秋7MAA12'}}}),
            ['26秋7MAA12'])

    def test_fetch_class_names_from_group_endpoint(self):
        class FakeSession:
            def __init__(self):
                self.body = None

            def post(self, path, body, request_timeout=None):
                self.assert_path(path)
                self.body = body
                return {'code': 0,
                        'obj': {'list': [{'groupName': '26秋7MAA12'}]}}

            @staticmethod
            def assert_path(path):
                assert path == 'gt/servlet/group/getTeacherGroup', path

            def fetch_timetable(self, page_size=100, max_pages=5):
                raise AssertionError('should not fall back')

        session = FakeSession()
        self.assertEqual(fetch_class_names(session), ['26秋7MAA12'])
        self.assertEqual(session.body, {})

    def test_fetch_class_names_falls_back_to_timetable(self):
        class FakeSession:
            def __init__(self):
                self.posts = 0

            def post(self, path, body, request_timeout=None):
                self.posts += 1
                return {'code': 0, 'obj': {'list': []}}

            def fetch_timetable(self, page_size=100, max_pages=5):
                return [{'groups': ['26秋7MAA12', '26秋8MAA12']}]

        session = FakeSession()
        self.assertEqual(fetch_class_names(session),
                         ['26秋7MAA12', '26秋8MAA12'])
        self.assertEqual(session.posts, 2)

    def test_fetch_class_names_does_not_wait_when_endpoint_is_down(self):
        class FakeSession:
            def __init__(self):
                self.posts = 0
                self.timetable_calls = 0

            def post(self, path, body, request_timeout=None):
                self.posts += 1
                raise RuntimeError('network down')

            def fetch_timetable(self, page_size=100, max_pages=5):
                self.timetable_calls += 1
                return []

        session = FakeSession()
        self.assertEqual(fetch_class_names(session), [])
        self.assertEqual(session.posts, 2)
        self.assertEqual(session.timetable_calls, 0)


class GroupLabelTests(unittest.TestCase):
    def test_term_and_short_label(self):
        self.assertEqual(term_label('26秋7MAA12（数形许）-11'), '26秋')
        self.assertEqual(term_label('2026暑7MAA12 -3'), '26暑')
        self.assertEqual(term_label('2026027MAA12'), '')
        self.assertEqual(term_label('专题9斗课'), '')
        self.assertEqual(class_short_label('2026027MA+31β1'), '7MA+31')
        self.assertEqual(class_short_label('26秋7MAA12（数形许）'), '7MAA12')
        self.assertEqual(class_short_label('2026027MA+31作业班'),
                         '7MA+31作业班')

    def test_display_label_takes_term_from_classroom_name(self):
        records = [
            {'shortDesc': '26秋7MA+31（数形许）-11',
             'createTime': int(datetime.datetime(2026, 11, 1).timestamp()
                               * 1000)},
            {'shortDesc': '26秋7MA+31（数形许）-10',
             'createTime': int(datetime.datetime(2026, 10, 25).timestamp()
                               * 1000)},
        ]
        labels = group_display_names({'2026027MA+31': [0, 1]}, records)
        self.assertEqual(labels['2026027MA+31'], '26秋7MA+31')
        self.assertEqual(labels['2026027MA+31'],
                         group_display_label('2026027MA+31', records))

    def test_display_label_falls_back_to_lesson_month(self):
        records = [
            {'shortDesc': '专题9斗课',
             'createTime': int(datetime.datetime(2026, 11, 1).timestamp()
                               * 1000)},
        ]
        labels = group_display_names({'2026027MA+31': [0]}, records)
        self.assertEqual(labels['2026027MA+31'], '26秋7MA+31')

    def test_learned_code_season_beats_lesson_month(self):
        records = [
            {'shortDesc': '26秋7MAA12 -1',
             'createTime': int(datetime.datetime(2026, 9, 10).timestamp()
                               * 1000)},
            # 同一期号 202602 的另一个班，课堂名没带学期，且这节在 8 月：
            # 按月份会误判成 26暑，应该用从同批数据学到的「202602 → 秋」
            {'shortDesc': '专题9斗课',
             'createTime': int(datetime.datetime(2026, 8, 20).timestamp()
                               * 1000)},
        ]
        labels = group_display_names({'2026027MAA12': [0],
                                      '2026028MBB12': [1]}, records)
        self.assertEqual(labels['2026027MAA12'], '26秋7MAA12')
        self.assertEqual(labels['2026028MBB12'], '26秋8MBB12')

    def test_term_vote_prefers_most_common_then_newest(self):
        records = [
            {'shortDesc': '26秋7MAA12 -1', 'createTime': 300},
            {'shortDesc': '26秋7MAA12 -2', 'createTime': 200},
            {'shortDesc': '25暑7MAA12 -13', 'createTime': 100},
        ]
        self.assertEqual(group_term_label('2026027MAA12', records), '26秋')
        # 平票时用最新一节课的学期
        tied = [
            {'shortDesc': '26秋7MAA12 -1', 'createTime': 300},
            {'shortDesc': '25暑7MAA12 -13', 'createTime': 100},
        ]
        self.assertEqual(group_term_label('2026027MAA12', tied), '26秋')
        # 次数优先：25暑 出现两次，即使最新一节课是 26秋
        mostly_summer = [
            {'shortDesc': '26秋7MAA12 -1', 'createTime': 300},
            {'shortDesc': '25暑7MAA12 -13', 'createTime': 100},
            {'shortDesc': '25暑7MAA12 -14', 'createTime': 50},
        ]
        self.assertEqual(group_term_label('2026027MAA12', mostly_summer),
                         '25暑')

    def test_colliding_display_labels_fall_back_to_official_name(self):
        records = [
            {'shortDesc': '26秋7MAA12 -1', 'createTime': 100},
            {'shortDesc': '26秋7MAA12 -2', 'createTime': 200},
        ]
        labels = group_display_names({'26秋7MAA12': [0],
                                      '2026027MAA12': [1]}, records)
        self.assertEqual(labels['26秋7MAA12'], '26秋7MAA12')
        self.assertEqual(labels['2026027MAA12'], '2026027MAA12')

    def test_order_prefers_newer_term_over_newer_lesson(self):
        records = [
            {'shortDesc': '25暑8MAA12 -3', 'createTime': 2000},
            {'shortDesc': '26秋8MAA12 -1', 'createTime': 1000},
        ]
        groups = {'2025028MAA12': [0], '2026028MAA12': [1],
                  UNCLASSIFIED_GROUP: []}
        labels = {'2025028MAA12': '25暑8MAA12',
                  '2026028MAA12': '26秋8MAA12'}
        self.assertEqual(
            ordered_group_names(groups, records, labels),
            ['2026028MAA12', '2025028MAA12', UNCLASSIFIED_GROUP])
        self.assertGreater(group_sort_key('26秋8MAA12'),
                           group_sort_key('25暑8MAA12'))
        self.assertGreater(group_sort_key('26秋8MAA12'),
                           group_sort_key('26春8MAA12'))
        self.assertGreater(group_sort_key('26春8MAA12'),
                           group_sort_key('26寒8MAA12'))

    def test_term_from_time_seasons(self):
        def stamp(month):
            return int(datetime.datetime(2026, month, 15).timestamp() * 1000)
        self.assertEqual(term_from_time(stamp(7)), '26暑')
        self.assertEqual(term_from_time(stamp(11)), '26秋')
        self.assertEqual(term_from_time(stamp(1)), '26寒')
        self.assertEqual(term_from_time(stamp(4)), '26春')
        self.assertEqual(term_from_time(0), '')


class RangeTests(unittest.TestCase):
    NOW = 1795300000000  # 固定“现在”，毫秒

    def _now_str(self, ms):
        return datetime.datetime.fromtimestamp(ms / 1000).strftime('%Y-%m-%d')

    def test_presets(self):
        for preset in ('全部', '最近一年', '最近三年', '自定义'):
            self.assertIn(preset, RANGE_PRESETS)
        day = 86400 * 1000
        for preset, span in (('今天', None), ('最近7天', 7),
                             ('最近30天', 30), ('最近一个季度', 92),
                             ('最近一年', 365), ('最近三年', 3 * 365)):
            if preset == '今天':
                continue
            start, end = resolve_range(preset, now_ms=self.NOW)
            self.assertEqual(end, self.NOW)
            self.assertAlmostEqual((end - start) / day, span, delta=0.01)

    def test_today(self):
        start, end = resolve_range('今天', now_ms=self.NOW)
        self.assertEqual(end, self.NOW)
        self.assertLess(start, end)
        self.assertGreater(end - start, 0)
        self.assertLessEqual(end - start, 86400 * 1000)

    def test_all_is_wide_enough_to_cover_history(self):
        start, end = resolve_range('全部', now_ms=self.NOW)
        self.assertAlmostEqual((end - start) / (86400 * 1000), 10 * 365,
                               delta=1)
        # 「全部」必须比「最近三年」更宽，否则两个预设没有区别
        three_start, _ = resolve_range('最近三年', now_ms=self.NOW)
        self.assertLess(start, three_start)

    def test_custom_full_days(self):
        start, end = resolve_range('自定义', '2026-11-01', '2026-11-03',
                                   now_ms=self.NOW)
        self.assertEqual(datetime.date.fromtimestamp(start / 1000),
                         datetime.date(2026, 11, 1))
        self.assertEqual(datetime.date.fromtimestamp(end / 1000),
                         datetime.date(2026, 11, 3))
        self.assertAlmostEqual((end - start) / (86400 * 1000), 3, delta=0.01)

    def test_custom_future_rejected(self):
        with self.assertRaises(ValueError):
            resolve_range('自定义', '2026-11-01', '2030-01-01',
                          now_ms=self.NOW)

    def test_custom_reversed_rejected(self):
        with self.assertRaises(ValueError):
            resolve_range('自定义', '2026-11-03', '2026-11-01',
                          now_ms=self.NOW)

    def test_custom_missing_rejected(self):
        with self.assertRaises(ValueError):
            resolve_range('自定义', '', '2026-11-01', now_ms=self.NOW)


class PageOrderTests(unittest.TestCase):
    MANIFEST = {
        'cmds': [
            [0, 215, 1, 0, 'attach1', 146902, '1.png', 19558],
            [0, 215, 2, 0, 'attach2', 148753, '2.png', 55485],
        ],
        'coursewares': [{
            'sumPage': 3,
            'name': '课件.pdf',
            'bs': [
                {'pageno': 3, 'path': 'F1_2_3.png', 'BsTime': 900000},
                {'pageno': 2, 'path': 'F1_2_2.png', 'BsTime': 300000},
            ],
        }],
        'smallboards': [],
    }

    def test_collect(self):
        pages = collect_pages(self.MANIFEST)
        self.assertEqual(len(pages), 4)
        self.assertEqual({p['kind'] for p in pages},
                         {'board', 'annotate'})

    def test_time_order(self):
        pages = order_pages(collect_pages(self.MANIFEST), 'time')
        self.assertEqual([p['time'] for p in pages],
                         [146902, 148753, 300000, 900000])

    def test_type_order(self):
        pages = order_pages(collect_pages(self.MANIFEST), 'type')
        # 独立板书页在前（按时间），课件批注页在后（按页码）
        self.assertEqual([p['file'] for p in pages],
                         ['1.png', '2.png', 'F1_2_2.png', 'F1_2_3.png'])

    def test_time_and_type_orders_are_distinct(self):
        pages = [
            {'kind': 'board', 'time': 300, 'file': 'b1',
             'pageno': 1, 'cw': 0, 'seq': 0},
            {'kind': 'board', 'time': 100, 'file': 'b2',
             'pageno': 2, 'cw': 0, 'seq': 1},
            {'kind': 'annotate', 'time': 200, 'file': 'a2',
             'pageno': 2, 'cw': 1, 'seq': 2},
            {'kind': 'annotate', 'time': 50, 'file': 'a1',
             'pageno': 1, 'cw': 1, 'seq': 3},
            {'kind': 'small', 'time': 250, 'file': 's1',
             'pageno': 0, 'cw': 0, 'seq': 4},
        ]
        # 按上课顺序：所有类型按课堂时间线交错。
        self.assertEqual([p['file'] for p in order_pages(pages, 'time')],
                         ['a1', 'b2', 'a2', 's1', 'b1'])
        # 按板书类型：独立板书 -> 小黑板 -> 课件批注；类型内保留
        # 原始页序（课件按 cw/pageno），不能退化成按时间排序。
        self.assertEqual([p['file'] for p in order_pages(pages, 'type')],
                         ['b1', 'b2', 's1', 'a1', 'a2'])


class LoginErrorTests(unittest.TestCase):
    def test_error_is_runtime_error(self):
        self.assertTrue(issubclass(LoginError, RuntimeError))


if __name__ == '__main__':
    unittest.main()
