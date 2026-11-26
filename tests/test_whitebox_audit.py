# -*- coding: utf-8 -*-
"""白盒审计回归用例：覆盖纯函数、边界、异常与文件读写失败路径。

运行：
    python -m unittest tests.test_whitebox_audit -v
或：
    python -m unittest discover -s tests -v

所有用例离线执行；网络/平台调用均使用 fake 或 mock。
"""

from __future__ import annotations

import datetime
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyze
import banshu_export
import cli
import config
import excel
import feedback_export
import gui
import homework_export
import login
import matrix
import net
import pipeline
import theme_art
import token_macos
import token_model
import token_source
try:
    import token_windows
except Exception:  # pragma: no cover - Windows-only ctypes.WinDLL
    token_windows = None
import update_checker
import update_installer


def _write(path: Path, text: str = 'x') -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


class ConfigBranchTests(unittest.TestCase):
    def test_normalize_valid_and_port_zero(self):
        self.assertEqual(config.normalize_base_url('https://a.cn/base/'),
                         'https://a.cn/base/')
        self.assertEqual(config.normalize_base_url('a.cn'), 'https://a.cn/')
        self.assertEqual(config.normalize_base_url('https://a.cn:8443/x'),
                         'https://a.cn:8443/x/')
        # 显式 :0 不能被当成「没有端口」而悄悄改成 443。
        self.assertEqual(config.normalize_base_url('https://a.cn:0/'),
                         'https://a.cn:0/')

    def test_normalize_rejects_bad_inputs_without_crashing(self):
        for value in ('', '   ', 'http://a.cn/', 'ftp://a.cn/',
                      'https://user:pass@a.cn/', 'https://a.cn:abc/',
                      'https://[::1/', 'https:///path'):
            with self.subTest(value=value):
                self.assertIsNone(config.normalize_base_url(value))

    def test_user_data_dirs_dedupes_and_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            appdata = root / 'roaming'
            local = root / 'local'
            (appdata / 'ai_client').mkdir(parents=True)
            (local / 'AiCloud').mkdir(parents=True)
            with patch.dict(os.environ, {'APPDATA': str(appdata),
                                         'LOCALAPPDATA': str(local)}, clear=False), \
                    patch.object(Path, 'home', return_value=root / 'home'):
                dirs = config.user_data_dirs()
            self.assertIn(appdata / 'ai_client', dirs)
            self.assertIn(local / 'AiCloud', dirs)
            self.assertEqual(len(dirs), len(set(dirs)))

    def test_discover_env_wins_and_rejects_http(self):
        with patch.dict(os.environ, {config.BASE_URL_ENV: 'https://x.aiwenyun.cn/a'},
                        clear=False):
            self.assertEqual(config.discover_base_urls(),
                             ['https://x.aiwenyun.cn/a/'])
        with patch.dict(os.environ, {config.BASE_URL_ENV: 'http://x.aiwenyun.cn'},
                        clear=False):
            urls = config.discover_base_urls()
            self.assertIn(config.DEFAULT_BASE_URL, urls)

    def test_discover_filters_static_and_reads_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / 'ai_client' / 'Network'
            data.mkdir(parents=True)
            (data / 'Network Persistent State').write_text(json.dumps({
                'x': ['https://plaso.example.cn/', 'https://static.example.cn/',
                      'http://plain.example.cn/']
            }), encoding='utf-8')
            with patch.object(config, 'user_data_dirs', return_value=[root / 'ai_client']):
                urls = config.discover_base_urls()
            self.assertIn(config.DEFAULT_BASE_URL, urls)
            self.assertTrue(any('plaso.example.cn' in u for u in urls))
            self.assertFalse(any('static' in u for u in urls))
            self.assertFalse(any(u.startswith('http://') for u in urls))

    def test_settings_roundtrip_and_bad_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {config.SETTINGS_DIR_ENV: tmp}, clear=False):
                self.assertEqual(config.load_settings(), {})
                config.save_ui_theme('dark')
                self.assertEqual(config.load_ui_theme(), 'dark')
                config.save_ui_theme('')
                self.assertEqual(config.load_ui_theme('light'), 'light')
                config.settings_file().write_text('not json', encoding='utf-8')
                self.assertEqual(config.load_settings(), {})
                config.settings_file().write_text('[]', encoding='utf-8')
                self.assertEqual(config.load_settings(), {})

    def test_output_folder_env_and_permission_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {config.OUTPUT_DIR_ENV: tmp}, clear=False):
                self.assertEqual(config.output_folder(), Path(tmp))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'occupied'
            target.write_text('x', encoding='utf-8')
            with patch.dict(os.environ, {config.OUTPUT_DIR_ENV: str(target)},
                            clear=False):
                with self.assertRaises(OSError):
                    config.output_folder()

    def test_client_candidates_and_open_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exe = root / 'AiCloud' / config.CLIENT_EXE
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b'MZ')
            with patch.dict(os.environ,
                            {'ProgramFiles': str(root),
                             'ProgramFiles(x86)': str(root / 'x86'),
                             'LOCALAPPDATA': str(root / 'local')}, clear=False):
                self.assertEqual(config.client_candidates(), [exe])
        with patch.object(config.sys, 'platform', 'win32'), \
                patch.object(config.os, 'startfile') as startfile:
            config.open_path(Path('x.txt'))
            startfile.assert_called_once()
        with patch.object(config.sys, 'platform', 'linux'), \
                patch.object(config.subprocess, 'Popen') as popen:
            config.open_path(Path('x.txt'))
            self.assertEqual(popen.call_args.args[0][0], 'xdg-open')


class AnalyzeBranchTests(unittest.TestCase):
    def test_digits_and_extract_codes(self):
        self.assertEqual(analyze.normalize_digits('１２３abc'), '123abc')
        self.assertEqual(analyze.extract_codes('邀请码：123456'), ['123456'])
        self.assertEqual(analyze.extract_codes(
            '邀请码：123456，inviteCode=654321'), ['123456', '654321'])
        self.assertEqual(analyze.extract_codes('邀请 1234567'), [])
        self.assertEqual(analyze.extract_codes(
            'inviteCode="１２３４５６"'), ['123456'])
        self.assertEqual(analyze.extract_codes(''), [])

    def test_clean_code_branches(self):
        self.assertEqual(analyze._clean_code(123456), '123456')
        self.assertEqual(analyze._clean_code(' １２３４５６ '), '123456')
        for value in (None, True, False, 12345, '12345x', [], 1.2):
            self.assertEqual(analyze._clean_code(value), '')

    def test_six_digit_branch_and_invite_shapes(self):
        self.assertEqual(analyze._six_digit_values({'id': 123456, 'v': '654321'}),
                         {'654321'})
        self.assertEqual(analyze._six_digit_values({'x': [123456, '654321']}),
                         {'123456', '654321'})
        self.assertEqual(analyze._six_digit_values({'x': True}), set())
        self.assertEqual(analyze.invite_code_from_payload(
            {'obj': {'inviteCode': '123456'}}), '123456')
        self.assertEqual(analyze.invite_code_from_payload(
            {'data': [{'roomCode': 654321}]}), '654321')
        self.assertEqual(analyze.invite_code_from_payload('邀请码 123456'),
                         '123456')
        self.assertEqual(analyze.invite_code_from_payload(
            {'obj': {'a': '111111', 'b': '222222'}}), '')
        self.assertEqual(analyze.invite_code_from_payload(None), '')

    def test_find_list_and_totals(self):
        self.assertEqual(analyze._find_list([1]), [1])
        self.assertEqual(analyze._find_list({'obj': {'rows': [2]}}), [2])
        self.assertEqual(analyze._find_list({'result': [3]}), [3])
        self.assertEqual(analyze._find_list({'items': [4]}), [4])
        self.assertEqual(analyze._find_list({'obj': 'x'}), [])
        self.assertEqual(analyze._find_list(None), [])
        self.assertEqual(analyze.timetable_total({'obj': {'total': '2'}}), 2)
        self.assertEqual(analyze.timetable_total({'data': {'count': 'x'}}), 0)
        self.assertEqual(analyze.timetable_total(None), 0)

    def test_timetable_rows_and_live_info(self):
        rows = analyze.timetable_rows({'obj': {'list': [
            {'id': '1', 'topic': ' A-1 ', 'startTime': 10,
             'groups': {'a': 'G1', 'b': 'G1'}, 'teacherName': ' T '},
            {'id': 1, 'topic': 'dup'},
            {'id': 'bad', 'topic': 'x'},
            'not a dict',
        ]}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], 1)
        self.assertEqual(rows[0]['topic'], 'A-1')
        self.assertEqual(rows[0]['groups'], ['G1'])
        self.assertEqual(rows[0]['teacher'], 'T')
        self.assertEqual(analyze.live_info_map({'obj': {'list': [
            {'id': '2', 'topic': 'b'}, {'id': 'x'}, 'bad']}}).keys(), {2})

    def test_enrich_and_format_time(self):
        row = {'topic': 'old', 'groups': ['old'], 'begin': 1, 'end': 2,
               'teacher': 't', 'orgId': 0}
        self.assertIs(analyze.enrich_row(row, None), row)
        result = analyze.enrich_row(row, {
            'topic': 'new', 'groups': {'1': 'G'},
            'startTime': 10, 'endTime': 20, 'teacherName': 'T', 'orgId': 3})
        self.assertEqual(result['topic'], 'new')
        self.assertEqual(result['groups'], ['G'])
        self.assertEqual((result['begin'], result['end']), (10, 20))
        self.assertEqual(analyze.format_time(1792944444464).count('-'), 2)
        for value in (None, 'x', 0, -1, 10 ** 30):
            self.assertEqual(analyze.format_time(value), '')


class MatrixBranchTests(unittest.TestCase):
    def test_lesson_and_course_boundaries(self):
        self.assertEqual(matrix.lesson_from_topic('A-1'), 1)
        self.assertEqual(matrix.lesson_from_topic('A-999'), 999)
        self.assertIsNone(matrix.lesson_from_topic('A-0'))
        self.assertIsNone(matrix.lesson_from_topic('A-1000'))
        self.assertEqual(matrix.lesson_from_topic('第3节课'), 3)
        self.assertEqual(matrix.lesson_from_topic('A（4）'), 4)
        self.assertEqual(matrix.lesson_from_topic('A-1β2'), 1)
        self.assertEqual(matrix.course_from_topic('A（x）-1β2'), 'A（x）')
        self.assertEqual(matrix.course_from_topic(''), '')
        self.assertEqual(matrix.natural_key('A2b10'),
                         ((1, 'a'), (0, 2), (1, 'b'), (0, 10)))
        self.assertEqual(matrix.natural_key(''), ())

    def test_groups_accept_string_dict_list(self):
        items = matrix.normalize_items([
            {'topic': 'A-1', 'groups': 'G1'},
            {'topic': 'A-2', 'groups': {'x': 'G2'}},
            {'topic': 'A-3', 'groups': [1, None, ' G3 ']},
            {'topic': 'A-4', 'groups': 3},
        ])
        self.assertEqual([item['groups'] for item in items],
                         [['G1'], ['G2'], ['1', 'G3'], []])

    def test_missing_lesson_no_collision_and_mixed_types(self):
        items = matrix.normalize_items([
            {'topic': 'A-2', 'code': '222222', 'begin': 10},
            {'topic': 'A', 'code': '111111', 'begin': 20, 'classId': 'x'},
            {'topic': 'A', 'code': '333333', 'begin': 30, 'classId': 3},
        ])
        self.assertEqual([item['lesson'] for item in items], [2, 1, 3])
        result = matrix.build_matrix(items)
        self.assertEqual(result.lessons, [1, 2, 3])
        self.assertEqual(result.code('A', 1), '111111')
        self.assertEqual(result.conflicts, [])

    def test_mixed_class_id_same_begin(self):
        items = matrix.normalize_items([
            {'topic': 'A', 'begin': 5, 'classId': 'x'},
            {'topic': 'A', 'begin': 5, 'classId': 3},
        ])
        self.assertEqual([item['lesson'] for item in items], [1, 2])

    def test_empty_and_conflicts(self):
        self.assertEqual(matrix.build_matrices([]), [])
        result = matrix.build_matrix([
            {'course': 'A', 'lesson': None, 'code': '1'},
            {'course': 'A', 'lesson': 1, 'code': '111111',
             'classId': 7},
            {'course': 'A', 'lesson': 1, 'code': '222222',
             'classId': 8},
        ])
        self.assertEqual(result.width, 1)
        self.assertEqual(result.code('A', 1), '111111')
        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(result.conflicts[0]['ignored'], '222222')


class ExcelBranchTests(unittest.TestCase):
    def test_safe_title(self):
        self.assertEqual(excel._safe_title('a/b:c*?[]'), 'a b c    ')
        self.assertEqual(excel._safe_title(''), '邀请码')
        self.assertEqual(len(excel._safe_title('x' * 40)), 31)

    def test_workbook_variants(self):
        empty = excel.write_workbook(
            Path(tempfile.mkdtemp()) / 'empty.xlsx', [])
        from openpyxl import load_workbook
        self.assertEqual(load_workbook(empty).sheetnames, ['邀请码'])
        m1 = matrix.Matrix('M1', ['A'], [1], {'A': {1: '123456'}})
        m2 = matrix.Matrix('M2', [], [], {})
        target = Path(tempfile.mkdtemp()) / 'two.xlsx'
        path = excel.write_workbook(
            target, [m1, m2], details=[],
            metadata={'exported_at': 't', 'client_version': 'v',
                      'base_url': 'u'})
        workbook = load_workbook(path)
        self.assertEqual(workbook.sheetnames, ['M1', 'M2', '明细'])
        self.assertIn('t', workbook.properties.description)

    def test_permission_fallback_renames(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'out.xlsx'
            calls = {'n': 0}
            real_replace = os.replace

            def flaky(src, dst):
                calls['n'] += 1
                if calls['n'] == 1:
                    raise PermissionError('locked')
                return real_replace(src, dst)

            with patch.object(excel.os, 'replace', side_effect=flaky):
                actual = excel.write_workbook(target, [])
            self.assertEqual(actual.name, 'out-1.xlsx')
            self.assertFalse(target.exists())

    def test_temp_cleaned_when_save_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'out.xlsx'
            with patch.object(excel.Workbook, 'save',
                              side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    excel.write_workbook(target, [])
            self.assertEqual(list(Path(tmp).glob('*.tmp*')), [])


class _FakeResponse:
    def __init__(self, status=200, body=b'{"code":0}', headers=None,
                 will_close=False):
        self.status = status
        self._body = body
        self._headers = headers or []
        self.will_close = will_close

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def getheaders(self):
        return self._headers


class _FakeConnection:
    def __init__(self, responses):
        self.responses = list(responses)
        self.closed = False
        self.requests = []
        self.timeout = 0
        self.sock = MagicMock()

    def connect(self):
        return None

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


class NetBranchTests(unittest.TestCase):
    def test_redact_and_float(self):
        token = '4041-0-454429-' + 'a' * 32 + '-1792944444464-1.0-PC-ai'
        text = 'x %s\naccess_token: abc123' % token
        cleaned = net.redact(text)
        self.assertNotIn(token, cleaned)
        self.assertNotIn('\n', cleaned)
        self.assertIn('***', cleaned)
        self.assertEqual(net.redact(None), '')
        self.assertEqual(net.redact('abcdef', limit=2), 'ab')
        self.assertEqual(net._as_float('1.5', 0), 1.5)
        self.assertEqual(net._as_float('x', 9), 9)
        self.assertEqual(net._as_float(None, 9), 9)

    def test_token_bucket_clamp_and_cancel(self):
        bucket = net.TokenBucket(rate=0, burst=0)
        self.assertEqual(bucket.rate, 0.1)
        self.assertEqual(bucket.capacity, 1)
        bucket.acquire()
        event = threading.Event()
        event.set()
        with self.assertRaises(net.ApiError):
            bucket.acquire(event)

    def test_abort_connection(self):
        net._abort_connection(MagicMock(sock=None))
        sock = MagicMock()
        sock.shutdown.side_effect = OSError('x')
        sock.close.side_effect = OSError('x')
        net._abort_connection(MagicMock(sock=sock))
        sock.shutdown.assert_called_once()

    def test_transport_rejects_non_https(self):
        for url in ('http://x/', 'ftp://x/', 'https:///x'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    net.HttpTransport(url)
        transport = net.HttpTransport('https://x.cn/base/')
        self.assertEqual((transport.host, transport.port, transport.prefix),
                         ('x.cn', 443, '/base'))
        transport.close()

    def test_parse_response_status_and_payload(self):
        cases = [(401, net.TokenExpired), (403, net.TokenExpired),
                 (429, net.RateLimitError), (500, net.ApiError),
                 (302, net.ApiError), (199, net.ApiError),
                 (400, net.ApiError)]
        for status, expected in cases:
            with self.subTest(status=status):
                with self.assertRaises(expected):
                    net.HttpTransport._parse_response(
                        status, b'{}', {}, '/p')
        self.assertEqual(net.HttpTransport._parse_response(
            200, b'{"code":0,"obj":{}}', {}, '/p')['obj'], {})
        self.assertEqual(net.HttpTransport._parse_response(
            200, b'\xef\xbb\xbf{"code":"0"}', {}, '/p')['code'], '0')
        with self.assertRaises(net.TokenExpired):
            net.HttpTransport._parse_response(
                200, b'{"code":2,"msg":"expired"}', {}, '/p')
        with self.assertRaises(net.TokenExpired):
            net.HttpTransport._parse_response(
                200, b'{"code":"2"}', {}, '/p')
        with self.assertRaises(net.ApiError):
            net.HttpTransport._parse_response(
                200, b'{"code":7,"msg":"bad"}', {}, '/p')
        with self.assertRaises(net.ApiError):
            net.HttpTransport._parse_response(200, b'\xff', {}, '/p')
        with self.assertRaises(net.ApiError):
            net.HttpTransport._parse_response(200, b'not json', {}, '/p')
        with self.assertRaises(net.ApiError):
            net.HttpTransport._parse_response(200, b'[]', {}, '/p')
        rate = None
        try:
            net.HttpTransport._parse_response(
                429, b'', {'retry-after': '2.5'}, '/p')
        except net.RateLimitError as error:
            rate = error
        self.assertEqual(rate.retry_after, 2.5)

    def test_post_json_success_retry_and_oversize(self):
        transport = net.HttpTransport('https://x.cn/')
        ok = _FakeResponse(200, b'{"code":0}')
        connection = _FakeConnection([ok])
        with patch.object(transport, '_connection', return_value=connection):
            self.assertEqual(transport.post_json('/p', {'a': 1}, {}),
                             {'code': 0})
        self.assertEqual(connection.requests[0][1], '/p')

        retry_connection = _FakeConnection([
            OSError('boom'), _FakeResponse(200, b'{"code":0}')])
        with patch.object(transport, '_connection',
                          return_value=retry_connection), \
                patch.object(net.time, 'sleep'):
            self.assertEqual(transport.post_json('/p', {}, {}), {'code': 0})
        self.assertEqual(len(retry_connection.requests), 2)

        big_connection = _FakeConnection([
            _FakeResponse(200, b'x' * (net.MAX_RESPONSE_BYTES + 2))])
        with patch.object(transport, '_connection',
                          return_value=big_connection):
            with self.assertRaises(net.ApiError):
                transport.post_json('/p', {}, {})
        with patch.object(net.time, 'sleep'):
            failed = _FakeConnection([OSError('a'), OSError('b')])
            with patch.object(transport, '_connection',
                              return_value=failed):
                with self.assertRaises(net.NetworkError):
                    transport.post_json('/p', {}, {})

    def test_session_headers_and_close(self):
        session = net.ApiSession('https://x.cn/', 'tok', platform='',
                                 version='v')
        self.assertEqual(session.platform, 'ai')
        self.assertEqual(session.headers()['access-token'], 'tok')
        self.assertEqual(session.headers()['version'], 'v')
        session.set_token('')
        with self.assertRaises(net.TokenExpired):
            session.headers()
        session.set_token('tok2')
        self.assertEqual(session.token(), 'tok2')
        session.close()
        self.assertEqual(session.token(), '')

    def test_fetch_timetable_and_live_info(self):
        session = net.ApiSession('https://x.cn/', 'tok')
        pages = [
            {'obj': {'total': 3, 'list': [
                {'id': 1, 'topic': 'A'}, {'id': 2, 'topic': 'B'}]}},
            {'obj': {'total': 3, 'list': [
                {'id': 2, 'topic': 'B'}, {'id': 3, 'topic': 'C'}]}},
        ]
        with patch.object(session, 'post', side_effect=pages):
            rows = session.fetch_timetable(page_size=2)
        self.assertEqual([row['id'] for row in rows], [1, 2, 3])
        with patch.object(session, 'post',
                          return_value={'obj': {'total': 0, 'list': []}}):
            self.assertEqual(session.fetch_timetable(max_pages=0), [])

        calls = []

        def fake_post(path, payload, **kwargs):
            calls.append(payload)
            return {'obj': {'list': [{'id': 1}, {'id': 'x'}, 'bad']}}

        with patch.object(session, 'post', side_effect=fake_post):
            details = session.fetch_live_info([1, 2], chunk_size=0)
        self.assertEqual(details, {1: {'id': 1}})
        self.assertEqual(len(calls), 2)
        with patch.object(session, 'post',
                          return_value={'obj': {'list': []}}):
            with self.assertRaises(net.ApiError):
                session.fetch_invite_code(1)

    def test_authenticate_no_candidate_and_failure(self):
        with self.assertRaises(net.AuthError):
            net.authenticate([], [])
        candidate = MagicMock(value='v', platform='ai', version='1')
        bad = MagicMock()
        bad.validate.side_effect = net.TokenExpired('x')
        good = MagicMock()
        good.validate.return_value = None
        with patch.object(net, 'ApiSession', side_effect=[bad, good]) as factory:
            session, chosen = net.authenticate(
                ['https://a.cn/'], [candidate, candidate])
        self.assertIs(session, good)
        self.assertIs(chosen, candidate)
        bad.close.assert_called_once()
        with patch.object(net, 'ApiSession', side_effect=net.TokenExpired('x')):
            with self.assertRaises(net.AuthError):
                net.authenticate(['https://a.cn/'], [candidate])


class LoginBranchTests(unittest.TestCase):
    def test_transform_passwd_edge_forms(self):
        md5 = 'e99a18c428cb38d5f260853678922e03'
        self.assertEqual(login.transform_passwd('P__bad'), login.hashlib.md5(
            b'P__bad').hexdigest())
        self.assertEqual(login.transform_passwd('E99A18C428CB38D5F260853678922E03'),
                         login.hashlib.md5(
                             b'E99A18C428CB38D5F260853678922E03').hexdigest())
        self.assertEqual(login.transform_passwd(None), '')
        self.assertEqual(login.transform_passwd(
            'e99a1a8c428cb38d5f260853678922e03__P'), 'P__' + md5)

    def test_friendly_error_shapes(self):
        error = login._friendly_error(
            {'code': 1, 'obj': [1], 'msg': '密码错误'}, 'pw')
        self.assertIn('密码错误', str(error))
        self.assertIn('code=1', str(error))
        error = login._friendly_error(
            {'code': 2, 'obj': {'errLoginTimes': 3}, 'msg': ''}, '')
        self.assertIn('今日已连续失败 3 次', str(error))
        self.assertIn('code=2', str(error))

    def test_do_login_success_and_failure(self):
        with patch.object(login, 'device_payload', return_value={}), \
                patch.object(login, '_post_json', return_value={
                    'code': 0, 'obj': {'access_token': 'tok', 'name': 'N',
                                       'myOrg': {'id': 1, 'shortName': 'O'}}}):
            result = login.do_login('u', 'p')
        self.assertEqual(result.token, 'tok')
        self.assertEqual(result.name, 'N')
        self.assertEqual(result.org_id, 1)
        self.assertEqual(result.org_name, 'O')
        with patch.object(login, 'device_payload', return_value={}), \
                patch.object(login, '_post_json', return_value={
                    'code': 1, 'obj': [1], 'msg': 'bad'}):
            with self.assertRaises(login.LoginError):
                login.do_login('u', 'p')
        with self.assertRaises(login.LoginError):
            login.do_login('', 'p')
        with self.assertRaises(login.LoginError):
            login.do_login('u', '')

    def test_post_json_http_and_json_errors(self):
        with self.assertRaises(login.LoginError):
            login._post_json('http://x/', 'p', {})

        class Broken:
            def request(self, *args, **kwargs):
                raise __import__('http.client').client.RemoteDisconnected('x')

            def close(self):
                pass

        with patch.object(login.http.client, 'HTTPSConnection',
                          return_value=Broken()):
            with self.assertRaises(login.LoginError):
                login._post_json('https://x.cn/', 'p', {})

        def run(status=200, raw=b'{"code":0}'):
            connection = _FakeConnection([_FakeResponse(status, raw)])
            with patch.object(login.http.client, 'HTTPSConnection',
                              return_value=connection), \
                    patch.object(login, '_MAX_LOGIN_RESPONSE_BYTES', 10):
                return login._post_json('https://x.cn/', 'p', {})

        self.assertEqual(run(), {'code': 0})
        with self.assertRaises(login.LoginError):
            run(status=500)
        with self.assertRaises(login.LoginError):
            run(raw=b'not json')
        with self.assertRaises(login.LoginError):
            run(raw=b'[]')
        with self.assertRaises(login.LoginError):
            run(raw=b'x' * 20)

    def test_device_id_paths(self):
        device = 'windows-12345678-1234-1234-1234-123456789abc'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            storage = root / 'leveldb'
            storage.mkdir()
            (storage / '000001.log').write_bytes(
                b'xx' + device.encode('ascii') + b'yy')
            with patch.object(login, '_client_storage_dirs',
                              return_value=[storage]):
                self.assertEqual(login._read_client_device_id(), device)
            target = root / 'id' / 'device-id.txt'
            target.parent.mkdir(parents=True)
            target.write_text(device, encoding='ascii')
            self.assertEqual(login._stable_device_id(target), device)
            target.write_text('bad', encoding='ascii')
            generated = login._stable_device_id(target)
            self.assertNotEqual(generated, 'bad')
            self.assertTrue(target.read_text(encoding='ascii').startswith(
                'windows-'))
            with patch.object(Path, 'mkdir', side_effect=OSError('denied')):
                self.assertTrue(login._stable_device_id(
                    root / 'no' / 'device-id.txt').startswith('windows-'))
        with patch.object(login, '_read_client_device_id', return_value=device):
            self.assertEqual(login.client_device_id(), device)
        with patch.object(login, '_read_client_device_id', return_value=''), \
                patch.object(login, '_stable_device_id', return_value='s'):
            self.assertEqual(login.client_device_id(), 's')

    def test_device_payload_and_scrub(self):
        with patch.object(login, 'client_device_id', return_value='d'), \
                patch.object(login.socket, 'gethostname', return_value=''), \
                patch.object(login, '_total_ram_gb', return_value='1G'):
            payload = login.device_payload()
        self.assertEqual(payload['deviceId'], 'd')
        self.assertEqual(payload['deviceName'], 'pc')
        self.assertEqual(login.scrub('pw x', 'pw'), '*** x')
        self.assertEqual(login.scrub(None), '')


class TokenModelBranchTests(unittest.TestCase):
    def test_candidate_properties(self):
        token = '4041-0-454429-' + 'a' * 32 + '-1792944444464-1.0-PC-ai'
        candidate = token_model.TokenCandidate(token, pid=1, address=2,
                                               timestamp=3, source='m')
        self.assertEqual((candidate.user_id, candidate.org_id,
                          candidate.version, candidate.platform),
                         ('454429', '4041', '1.0', 'ai'))
        self.assertIn('len=', repr(candidate))
        bad = token_model.TokenCandidate('bad')
        self.assertEqual((bad.user_id, bad.org_id, bad.version), ('', '', ''))
        self.assertEqual(bad.platform, 'ai')
        self.assertEqual(token_model.parse_token_metadata(token)['length'],
                         len(token))
        self.assertEqual(token_model.parse_token_metadata('bad'), {})

    def test_timestamp_and_extract(self):
        self.assertEqual(token_model.timestamp_from_generic(
            '1-2-3-abc-1792944444464-1-PC-ai'), 1792944444464)
        self.assertEqual(token_model.timestamp_from_generic(
            '1-2-3-abc-123-1-PC-ai'), 0)
        token = '4041-0-454429-' + 'a' * 32 + '-1792944444464-1.0-PC-ai'
        ascii_data = b'\x00' + token.encode() + b'\x00'
        found = token_model.extract_from_bytes(ascii_data, 1, 10)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].source, 'memory')
        utf16 = ('\x00' + token + '\x00').encode('utf-16le')
        self.assertEqual(len(token_model.extract_from_bytes(utf16)), 1)
        self.assertEqual(token_model.extract_from_bytes(b'no marker'), [])


@unittest.skipUnless(token_windows is not None, 'Windows-only module')
class TokenSourceWindowsTests(unittest.TestCase):
    def test_debug_port_and_client_running(self):
        with patch.object(token_source.urllib.request, 'urlopen') as opened:
            opened.return_value.__enter__.return_value = object()
            self.assertTrue(token_source.debug_port_open())
        with patch.object(token_source.urllib.request, 'urlopen',
                          side_effect=OSError('no')):
            self.assertFalse(token_source.debug_port_open())
        with patch.object(token_source.sys, 'platform', 'win32'), \
                patch.object(token_windows, 'list_client_pids',
                             return_value=[1]):
            self.assertTrue(token_source.client_running())
        with patch.object(token_source.sys, 'platform', 'darwin'), \
                patch.object(token_macos, 'client_running',
                             return_value=False):
            self.assertFalse(token_source.client_running())

    def test_acquire_tokens_platforms(self):
        token = '4041-0-454429-' + 'a' * 32 + '-1792944444464-1.0-PC-ai'
        candidate = token_model.TokenCandidate(token)
        stats = token_windows.ScanStats(processes=1, bytes_read=2,
                                        elapsed=0.1, regions=2,
                                        marker_hits=3, partial=True)
        with patch.object(token_source.sys, 'platform', 'win32'), \
                patch.object(token_windows, 'scan_tokens',
                             return_value=[candidate]) as scan, \
                patch.object(token_windows, 'ScanStats', return_value=stats):
            batch = token_source.acquire_tokens(full=True)
        self.assertEqual(batch.candidates, [candidate])
        self.assertEqual(batch.info['method'], 'memory')
        self.assertTrue(batch.info['full'])
        self.assertTrue(batch.info['partial'])
        self.assertFalse(scan.call_args.kwargs['stop_after_first'])
        mac_batch = token_model.TokenBatch([candidate], info={'method': 'cdp'})
        with patch.object(token_source.sys, 'platform', 'darwin'), \
                patch.object(token_macos, 'acquire_tokens',
                             return_value=mac_batch):
            self.assertIs(token_source.acquire_tokens(), mac_batch)

    def test_windows_pid_and_region_helpers(self):
        output = ('"ai-yxt.exe","123","Console","1","10 K"\r\n'
                  '"other.exe","9","Console","1","1 K"\r\n'
                  '"ai-yxt.exe","bad"\r\n')
        with patch.object(token_windows.subprocess, 'check_output',
                          return_value=output):
            self.assertEqual(token_windows.list_client_pids(), [123])
        with patch.object(token_windows.subprocess, 'check_output',
                          side_effect=OSError('x')):
            with self.assertRaises(token_model.TokenScanError):
                token_windows.list_client_pids()
        self.assertTrue(token_windows._is_private_readable(
            token_windows.MEM_COMMIT, 0x04, token_windows.MEM_PRIVATE))
        self.assertFalse(token_windows._is_private_readable(
            token_windows.MEM_COMMIT, 0x01, token_windows.MEM_PRIVATE))
        self.assertFalse(token_windows._is_private_readable(
            token_windows.MEM_COMMIT, 0x100, token_windows.MEM_PRIVATE))
        self.assertFalse(token_windows._is_private_readable(
            0, 0x04, token_windows.MEM_PRIVATE))

    def test_scan_tokens_empty_found_and_hints(self):
        with patch.object(token_windows, 'list_client_pids', return_value=[]):
            with self.assertRaises(token_model.TokenScanError):
                token_windows.scan_tokens()
        token = '4041-0-454429-' + 'a' * 32 + '-1792944444464-1.0-PC-ai'
        candidate = token_model.TokenCandidate(token, timestamp=9)
        stats = token_windows.ScanStats(denied_processes=1)
        with patch.object(token_windows, 'list_client_pids', return_value=[1]), \
                patch.object(token_windows, '_scan_pid',
                             return_value=[candidate]):
            found = token_windows.scan_tokens(stats=stats,
                                               stop_after_first=True)
        self.assertEqual(found, [candidate])
        self.assertTrue(stats.partial)
        with patch.object(token_windows, 'list_client_pids', return_value=[1]), \
                patch.object(token_windows, '_scan_pid', return_value=[]):
            stats = token_windows.ScanStats(marker_hits=4)
            with self.assertRaises(token_model.TokenScanError) as context:
                token_windows.scan_tokens(stats=stats)
            self.assertIn('token 标记', str(context.exception))


class _PSession:
    base_url = 'https://x.cn/'

    def __init__(self, code='123456'):
        self.code = code
        self.closed = False
        self.calls = []

    def fetch_timetable(self, start_ms=None):
        return [{'id': 1, 'topic': 'A-1', 'begin': 1792944444000}]

    def fetch_live_info(self, ids):
        return {1: {'topic': 'A-1', 'groups': {'1': 'G'}}}

    def fetch_invite_code(self, class_id, deadline=None):
        self.calls.append(class_id)
        if self.code is None:
            raise net.ApiError('no code')
        return self.code

    def close(self):
        self.closed = True


class _PState:
    def __init__(self, session=None, code='123456'):
        self.session = session or _PSession(code)
        self.generation = 0
        self.metadata = {'version': '1.0', 'issued_at': 1, 'user_id': '1'}
        self.closed = False
        self.refresh_count = 0

    def current(self):
        return self.session, self.generation

    def refresh(self, seen):
        self.refresh_count += 1
        self.generation += 1
        return self.session, self.generation

    def close(self):
        self.closed = True
        self.session.close()


class PipelineBranchTests(unittest.TestCase):
    def test_summary_as_dict(self):
        summary = pipeline.ExportSummary(
            path=Path('x.xlsx'), total=2, success=1, failed=1, elapsed=1.234,
            base_url='u', client_version='v', token_issued_at=3,
            canceled=True, conflicts=[{'a': 1}], csv_path=Path('x.csv'))
        data = summary.as_dict()
        self.assertEqual(data['elapsed'], 1.23)
        self.assertTrue(data['canceled'])
        self.assertEqual(data['csv'], 'x.csv')
        self.assertIsNone(data['json'])
        self.assertIsNone(data['diagnostics'])

    def test_token_state_current_refresh_and_generation_guard(self):
        session = _PSession()
        token = token_model.TokenCandidate(
            '4041-0-454429-' + 'a' * 32 + '-1792944444464-1.0-PC-ai')
        with patch.object(pipeline, 'authenticate',
                          return_value=(session, token)):
            state = pipeline.TokenState(lambda **kwargs: None,
                                        ['https://x.cn/'],
                                        token_model.TokenBatch([token]))
        self.assertIs(state.current()[0], session)
        self.assertEqual(state.metadata['user_id'], '454429')
        self.assertEqual(state.refresh(999), (session, 0))
        state.close()
        with self.assertRaises(net.AuthError):
            state.current()
        self.assertTrue(session.closed)

    def test_token_state_refresh_failure(self):
        session = _PSession()
        token = token_model.TokenCandidate(
            '4041-0-454429-' + 'a' * 32 + '-1792944444464-1.0-PC-ai')
        with patch.object(pipeline, 'authenticate',
                          return_value=(session, token)):
            state = pipeline.TokenState(
                lambda **kwargs: (_ for _ in ()).throw(
                    token_model.TokenScanError('no scan')),
                ['https://x.cn/'], token_model.TokenBatch([token]))
        with patch.object(pipeline.time, 'sleep'):
            with self.assertRaises(net.AuthError):
                state.refresh(0)
        with self.assertRaises(net.AuthError):
            state.current()
        state.close()

    def test_cooldown_deadline_and_cancel(self):
        cooldown = pipeline.Cooldown()
        cooldown.pause(1.0)
        started = time.monotonic()
        cooldown.wait(deadline=time.monotonic() + 0.12)
        self.assertLess(time.monotonic() - started, 0.5)
        event = threading.Event()
        event.set()
        started = time.monotonic()
        cooldown.wait(event)
        self.assertLess(time.monotonic() - started, 0.2)
        cooldown.pause(0)
        cooldown.wait()

    def test_call_with_refresh(self):
        state = _PState()
        calls = {'n': 0}

        def func(session):
            calls['n'] += 1
            if calls['n'] == 1:
                raise net.TokenExpired('expired')
            return 'ok'

        self.assertEqual(pipeline._call_with_refresh(state, func), 'ok')
        self.assertEqual(state.refresh_count, 1)

        def always(session):
            raise net.TokenExpired('expired')

        with self.assertRaises(net.ApiError):
            pipeline._call_with_refresh(state, always)

    def test_fetch_one_paths(self):
        row = {'id': 1, 'topic': 'A-1', 'begin': 1792944444000}
        event = threading.Event()
        bucket = net.TokenBucket(1000, 10)

        record = pipeline._fetch_one(
            _PState(), row, bucket, event, class_deadline=5)
        self.assertEqual(record['code'], '123456')
        self.assertEqual(record['lesson'], 1)

        canceled = threading.Event()
        canceled.set()
        record = pipeline._fetch_one(
            _PState(), row, bucket, canceled, class_deadline=5)
        self.assertIn('取消', record['note'])
        self.assertEqual(record['_diag']['attempts'], 0)

        record = pipeline._fetch_one(
            _PState(), row, bucket, event, class_deadline=-1)
        self.assertIn('超时', record['note'])

        auth_state = _PState()

        def no_current():
            raise net.AuthError('no auth')

        auth_state.current = no_current
        record = pipeline._fetch_one(
            auth_state, row, bucket, event, class_deadline=5)
        self.assertIn('no auth', record['note'])

        class RateSession(_PSession):
            def fetch_invite_code(self, class_id, deadline=None):
                if not self.calls:
                    self.calls.append(class_id)
                    raise net.RateLimitError('rate', 0.01)
                return '654321'

        cooldown = pipeline.Cooldown()
        record = pipeline._fetch_one(
            _PState(RateSession()), row, bucket, event,
            cooldown=cooldown, class_deadline=5)
        self.assertEqual(record['code'], '654321')
        self.assertIn('rate', record['_diag']['errors'])

        class NetSession(_PSession):
            def fetch_invite_code(self, class_id, deadline=None):
                self.calls.append(class_id)
                if len(self.calls) == 1:
                    raise net.NetworkError('net')
                return '111111'

        with patch.object(pipeline, '_sleep_backoff'):
            record = pipeline._fetch_one(
                _PState(NetSession()), row, bucket, event,
                cooldown=pipeline.Cooldown(), class_deadline=5)
        self.assertEqual(record['code'], '111111')
        self.assertIn('network', record['_diag']['errors'])

        class ApiSession(_PSession):
            def fetch_invite_code(self, class_id, deadline=None):
                self.calls.append(class_id)
                raise net.ApiError('api')

        with patch.object(pipeline, '_sleep_backoff'):
            record = pipeline._fetch_one(
                _PState(ApiSession()), row, bucket, event,
                cooldown=pipeline.Cooldown(), class_deadline=5)
        self.assertIn('api', record['_diag']['errors'])
        self.assertEqual(record['code'], '')

        class BadSession(_PSession):
            def fetch_invite_code(self, class_id, deadline=None):
                raise RuntimeError('boom')

        record = pipeline._fetch_one(
            _PState(BadSession()), row, bucket, event, class_deadline=5)
        self.assertIn('unexpected', record['_diag']['errors'])

    def test_sleep_backoff_cancel(self):
        event = threading.Event()
        event.set()
        started = time.monotonic()
        pipeline._sleep_backoff(5, 10, event)
        self.assertLess(time.monotonic() - started, 0.2)

    def test_run_export_success_outputs_and_csv_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            options = pipeline.ExportOptions(
                out_dir=out, preferred_token='tok', workers=1, rate=1000,
                include_details=True, write_csv=True, write_json=True,
                diagnostics=True, open_file=False)
            state = _PState()
            with patch.object(pipeline, 'TokenState',
                              return_value=state), \
                    patch.object(pipeline.time, 'sleep'):
                summary = pipeline.run_export(options)
            self.assertEqual(summary.success, 1)
            self.assertTrue(summary.path.is_file())
            self.assertTrue(summary.csv_path.is_file())
            self.assertTrue(summary.json_path.is_file())
            self.assertTrue(summary.diagnostics_path.is_file())
            self.assertTrue(state.closed)

        with tempfile.TemporaryDirectory() as tmp:
            options = pipeline.ExportOptions(
                out_dir=Path(tmp), preferred_token='tok', workers=1,
                rate=1000, include_details=False, write_csv=True,
                open_file=False, diagnostics=False)
            state = _PState()
            with patch.object(pipeline, 'TokenState', return_value=state), \
                    patch.object(pipeline, '_write_csv',
                                 side_effect=OSError('denied')):
                summary = pipeline.run_export(options)
            self.assertTrue(summary.path.is_file())
            self.assertIsNone(summary.csv_path)

    def test_run_export_errors(self):
        with self.assertRaises(pipeline.PipelineError):
            pipeline.run_export(pipeline.ExportOptions(
                preferred_token='tok', base_url='http://x/', open_file=False))
        empty = _PSession()
        empty.fetch_timetable = lambda start_ms=None: []
        state = _PState(empty)
        with patch.object(pipeline, 'TokenState', return_value=state):
            with self.assertRaises(pipeline.NoClassesError):
                pipeline.run_export(pipeline.ExportOptions(
                    preferred_token='tok', out_dir=Path(tempfile.mkdtemp()),
                    open_file=False))
        event = threading.Event()
        event.set()
        with self.assertRaises(pipeline.Cancelled):
            pipeline.run_export(pipeline.ExportOptions(preferred_token='tok'),
                                cancel_event=event)

    def test_write_helpers_cleanup_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'x.csv'
            with patch.object(pipeline.os, 'replace',
                              side_effect=OSError('denied')):
                with self.assertRaises(OSError):
                    pipeline._write_csv(target, [])
            self.assertEqual(list(Path(tmp).glob('*.tmp*')), [])
            target = Path(tmp) / 'x.json'
            with patch.object(pipeline.os, 'replace',
                              side_effect=OSError('denied')):
                with self.assertRaises(OSError):
                    pipeline._write_json(target, [])
            self.assertEqual(list(Path(tmp).glob('*.tmp*')), [])
            target = Path(tmp) / 'x.xlsx'
            with patch.object(pipeline.os, 'replace',
                              side_effect=OSError('denied')):
                with self.assertRaises(OSError):
                    pipeline._write_diagnostics(target, {})
            self.assertEqual(list(Path(tmp).glob('*.tmp*')), [])


class HomeworkBranchTests(unittest.TestCase):
    def test_fetch_helpers_guard_malformed_obj(self):
        with self.assertRaises(RuntimeError):
            homework_export.fetch_homeworks(_PSessionPost([1]), 0, 0)
        with self.assertRaises(RuntimeError):
            homework_export.fetch_scores(_PSessionPost([1]), 'x')
        data = homework_export.fetch_scores(
            _PSessionPost({'studentZuoyeInfo': 'bad',
                           'zuoyeInfo': {'x': 1}}), 'x')
        self.assertEqual(data['students'], [])
        self.assertEqual(data['info'], {'x': 1})

    def test_export_homeworks_bad_expected_and_write_failure(self):
        students = {'studentZuoyeInfo': [
            {'studentName': '1甲', 'studentLoginName': 'u',
             'status': 11, 'score': 80}]}

        class Session:
            def post(self, path, payload):
                return {'obj': students}

        with tempfile.TemporaryDirectory() as tmp:
            result = homework_export.export_homeworks(
                Session(), [{'zuoyeId': 1, 'title': 't', 'groupName': 'g',
                             'zuoyesNum': 'abc'}], Path(tmp))
            self.assertEqual(len(result['files']), 1)
            self.assertTrue(result['files'][0]['complete'])
            self.assertEqual(result['files'][0]['expected'], 0)

        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / 'notdir'
            out_file.write_text('x', encoding='utf-8')
            result = homework_export.export_homeworks(
                Session(), [{'zuoyeId': 1, 'title': 't', 'groupName': 'g'}],
                out_file)
            self.assertEqual(result['files'], [])
            self.assertEqual(len(result['failed']), 1)

    def test_write_csv_permission_and_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'x.csv'
            target.mkdir()
            with self.assertRaises(OSError):
                homework_export.write_csv(target, {}, [])
            self.assertEqual(list(Path(tmp).glob('*.tmp*')), [])
        with tempfile.TemporaryDirectory() as tmp:
            count = homework_export.write_csv(
                Path(tmp) / 'x.csv', {}, [])
            self.assertEqual(count, 0)
            self.assertTrue((Path(tmp) / 'x.csv').is_file())


class _PSessionPost:
    def __init__(self, obj):
        self.obj = obj

    def post(self, path, payload):
        return {'obj': self.obj}


class FeedbackBranchTests(unittest.TestCase):
    def test_numbers_and_time_helpers(self):
        self.assertEqual(feedback_export._num('1.5'), 1.5)
        self.assertEqual(feedback_export._num('bad'), 0.0)
        self.assertEqual(feedback_export._num(float('inf')), 0.0)
        self.assertEqual(feedback_export._ceil_minutes(61), '2')
        self.assertEqual(feedback_export._ceil_minutes(0), '0')
        self.assertEqual(feedback_export._fmt_time(0), '-')
        self.assertEqual(feedback_export._fmt_time('bad'), '-')
        self.assertNotEqual(feedback_export._fmt_time(1792944444464), '-')
        self.assertEqual(feedback_export._progress_label(''), '-')
        self.assertEqual(feedback_export._progress_label('50%'), '50%')
        self.assertEqual(feedback_export._progress_label('50'), '50%')
        self.assertEqual(feedback_export._progress_label('x'), 'x')

    def test_chinese_and_lesson_parsing(self):
        self.assertEqual(feedback_export.chinese_number(0), '零')
        self.assertEqual(feedback_export.chinese_number(10), '十')
        self.assertEqual(feedback_export.chinese_number(11), '十一')
        self.assertEqual(feedback_export.chinese_number(23), '二十三')
        self.assertEqual(feedback_export.chinese_number(101), '一百零一')
        self.assertEqual(feedback_export.chinese_number(-1), '-1')
        self.assertEqual(feedback_export.parse_chinese_number('十一'), 11)
        self.assertEqual(feedback_export.parse_chinese_number('一百零二'), 102)
        self.assertIsNone(feedback_export.parse_chinese_number('abc'))
        self.assertIsNone(feedback_export.parse_chinese_number(''))
        self.assertEqual(feedback_export.parse_lesson_number('专题十一'), 11)
        self.assertEqual(feedback_export.parse_lesson_number('第4节课'), 4)
        self.assertEqual(feedback_export.parse_lesson_number('A-12（x）'), 12)
        self.assertIsNone(feedback_export.parse_lesson_number('无课次'))
        self.assertEqual(feedback_export.homework_kind(
            {'title': '【数·课前预习】专题十'}), 'preview')
        self.assertEqual(feedback_export.homework_kind(
            {'title': '【数·课后巩固】专题九'}), 'consolidation')
        self.assertIsNone(feedback_export.homework_kind({'title': 'x'}))
        self.assertEqual(feedback_export.homework_lesson(
            {'title': '【数·课前预习】专题十一'}), 11)

    def test_identity_and_bundle_filters(self):
        self.assertIsNone(feedback_export._class_identity(''))
        self.assertIsNone(feedback_export._class_identity('未分类'))
        self.assertIsNotNone(feedback_export._class_identity(
            '未分类', allow_unclassified=True))
        self.assertEqual(feedback_export._period_code('2026027MA+31'),
                         '202602')
        self.assertEqual(feedback_export._vote_best({'a': 1, 'b': 2}), 'b')
        self.assertEqual(feedback_export._vote_unique({'a': 1, 'b': 1}), '')
        bundles = feedback_export.build_feedback_bundles(
            [{'shortDesc': '26秋7MA+31（x）-10',
              'liveclass': 1, 'fileCommon': {'recordId': 'r'}},
             'bad'],
            [{'title': '【数·课前预习】专题十',
              'groupName': '2026027MA+31作业班',
              'sortTime': 1792944444000}])
        self.assertEqual(len(bundles), 1)
        self.assertEqual(bundles[0]['lesson'], 10)
        self.assertEqual(bundles[0]['missing'], ['consolidation'])
        self.assertTrue(feedback_export._needs_lesson_bundles(
            [], [{'title': '【数·课前预习】专题十'}]))
        self.assertEqual(feedback_export._bucket_labels({}), {})

    def test_build_rows_tolerates_bad_media_and_missing(self):
        rows = feedback_export.build_feedback_rows(
            [{'username': '张三', 'userId': '1', 'groupNames': 'G'},
             {'username': '', 'loginname': ''}, 'bad'],
            [], {}, {'mediaType': 'bad'}, {'shortDesc': 'A-1'})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], '实时课堂')
        self.assertEqual(rows[0][3], '缺勤')

    def test_student_detail_bad_and_good_fields(self):
        class Session:
            def __init__(self):
                self.payload = None

            def post(self, path, payload):
                self.payload = payload
                return {'obj': {'a': 1}}

        session = Session()
        self.assertEqual(feedback_export._student_detail(
            session, {'groupIds': 'bad', 'userId': '1'},
            {'startTime': 1}, 0), {})
        self.assertEqual(feedback_export._student_detail(
            session, {'groupIds': '2', 'userId': '1'},
            {'startTime': 'bad'}, 0), {})
        detail = feedback_export._student_detail(
            session, {'groupIds': '2,3', 'userId': '1'},
            {'startTime': 1000, 'duration': 2}, '4')
        self.assertEqual(detail, {'a': 1})
        self.assertEqual(session.payload, {
            'beginTime': 1000, 'endTime': 3000, 'groupId': 2,
            'studentId': 1, 'teacherId': 4})

    def test_attend_signin_pagination_and_malformed(self):
        class Session:
            def __init__(self, pages):
                self.pages = list(pages)

            def post(self, path, payload):
                return self.pages.pop(0)

        with patch.object(feedback_export, 'PAGE_SIZE', 2):
            session = Session([
                {'obj': {'list': [{'uniqueId': '1'}, {'uniqueId': '2'}],
                         'total': 4}},
                {'obj': {'list': [{'uniqueId': '3'}, {'uniqueId': '4'}],
                         'total': 4}},
            ])
            students = feedback_export._attend_students(session, 'r')
            self.assertEqual([s['uniqueId'] for s in students],
                             ['1', '2', '3', '4'])
            session = Session([
                {'obj': {'list': [{'uniqueId': '1'}, {'uniqueId': '2'}],
                         'total': 4}},
                {'obj': {'list': [{'uniqueId': '3'}, {'uniqueId': '4'}],
                         'total': 4}},
            ])
            students = feedback_export._signin_students(session, 1)
            self.assertEqual([s['uniqueId'] for s in students],
                             ['1', '2', '3', '4'])
        with self.assertRaises(RuntimeError):
            feedback_export._attend_students(
                Session([{'obj': [1]}]), 'r')
        with self.assertRaises(RuntimeError):
            feedback_export._signin_students(
                Session([{'obj': [1]}]), 1)
        self.assertEqual(feedback_export._signin_students(
            Session([]), 0), [])
        self.assertEqual(feedback_export._roster_students(
            Session([{'obj': 'bad'}]), 1), [])

    def test_optional_obj_and_file_helpers(self):
        class Session:
            def __init__(self, error=None, obj=None):
                self.error = error
                self.obj = obj

            def post(self, path, payload):
                if self.error:
                    raise self.error
                return {'obj': self.obj}

        self.assertEqual(feedback_export._optional_obj(
            Session(obj={'a': 1}), 'p', {}), {'a': 1})
        self.assertEqual(feedback_export._optional_obj(
            Session(obj=[1]), 'p', {}), {})
        self.assertEqual(feedback_export._optional_obj(
            Session(error=RuntimeError('x')), 'p', {}), {})
        with self.assertRaises(net.TokenExpired):
            feedback_export._optional_obj(
                Session(error=net.TokenExpired('x')), 'p', {})

        self.assertEqual(feedback_export._dedupe_name('a.csv', {}, Path('.')),
                         'a.csv')
        seen = {}
        self.assertEqual(feedback_export._dedupe_name(
            'a.csv', seen, Path('.')), 'a.csv')
        self.assertEqual(feedback_export._dedupe_name(
            'a.csv', seen, Path('.')), 'a（2）.csv')
        self.assertTrue(feedback_export._missing_filename('history').endswith(
            '暂时缺失'))
        bundle = {'lesson': 3, 'class': 'C', 'components': {
            'history': {'shortDesc': 'A-3'}}}
        self.assertIn('统计报告', feedback_export.feedback_component_name(
            bundle, 'history'))
        self.assertIn('暂时缺失', feedback_export.feedback_component_name(
            bundle, 'preview'))
        self.assertTrue(feedback_export._legacy_component_filename(
            'preview', {'title': 'T'}).startswith('课前预习-'))
        self.assertEqual(feedback_export._legacy_component_filename(
            'history', {}), '')

    def test_clean_bundle_folder_only_removes_generated(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            keep = folder / '用户文件.txt'
            keep.write_text('keep', encoding='utf-8')
            old = folder / feedback_export._missing_filename('history')
            old.write_text('old', encoding='utf-8')
            feedback_export._clean_bundle_folder(
                folder, {'components': {}, 'lesson': 1})
            self.assertTrue(keep.exists())
            self.assertFalse(old.exists())
            feedback_export._clean_bundle_folder(
                folder / 'missing', {'components': {}, 'lesson': 1})

    def test_write_feedback_csv_permission_and_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'x.csv'
            target.mkdir()
            with self.assertRaises(OSError):
                feedback_export.write_feedback_csv(target, [])
            self.assertEqual(list(Path(tmp).glob('*.tmp*')), [])
            path = Path(tmp) / 'ok.csv'
            self.assertEqual(feedback_export.write_feedback_csv(
                path, [['a', 'b']]), 1)
            self.assertTrue(path.read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_export_bundles_permission_and_empty(self):
        self.assertEqual(feedback_export.export_feedback_bundles(
            object(), [], Path('.'))['total'], 0)
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / 'file'
            out_file.write_text('x', encoding='utf-8')
            bundle = {
                'class': 'C', 'lesson': 1, 'title': '第1节课反馈',
                'components': {'history': {'shortDesc': 'A-1'}},
                'selected': ['history'],
            }
            result = feedback_export.export_feedback_bundles(
                object(), [bundle], out_file)
            self.assertEqual(result['total'], 1)
            self.assertEqual(len(result['failed']), 1)


class BanshuBranchTests(unittest.TestCase):
    def test_naming_and_term_helpers(self):
        self.assertEqual(banshu_export.sanitize('a/b:c*?"<>|'),
                         'a_b_c______')
        self.assertEqual(banshu_export.sanitize('  '), '未命名课堂')
        self.assertEqual(banshu_export.class_base('Aβ1（x）-2'), 'A')
        self.assertEqual(banshu_export.class_base('A作业班'), 'A作业班')
        self.assertEqual(banshu_export.class_short_label('26秋7MAA12（x）'),
                         '7MAA12')
        self.assertEqual(banshu_export.class_folder_label(
            '2026027MA+31作业班'), '7MA+31')
        self.assertEqual(banshu_export.term_label('2026秋7MA'), '26秋')
        self.assertEqual(banshu_export.term_label('x'), '')
        self.assertEqual(banshu_export.term_from_time(1792944444000), '26秋')
        self.assertEqual(banshu_export.term_from_time(0), '')
        self.assertEqual(banshu_export.term_from_time('bad'), '')
        self.assertEqual(banshu_export.official_term_code(
            '2026027MA+31'), ('26', '02'))
        self.assertIsNone(banshu_export.official_term_code('26秋7MA'))
        self.assertEqual(banshu_export.group_sort_key('26秋A'), (26, 3))
        self.assertEqual(banshu_export.group_sort_key('x'), (0, 0))

    def test_group_labels_and_learning(self):
        records = [
            {'shortDesc': '26秋7MA+31（x）-11', 'createTime': 10},
            {'shortDesc': '26暑7MA+31（x）-10', 'createTime': 20},
        ]
        groups = {'2026027MA+31': [0, 1]}
        labels = banshu_export.group_display_names(groups, records)
        self.assertIn('7MA+31', labels['2026027MA+31'])
        self.assertEqual(banshu_export.group_term_label(
            '2026027MA+31', [records[0]]), '26秋')
        self.assertEqual(banshu_export.group_display_label(
            '26秋7MA+31', records), '26秋7MA+31')
        self.assertEqual(banshu_export.learned_term_codes(
            groups, records), {('26', '02'): '秋'})
        self.assertEqual(banshu_export.class_output_dir(
            Path('out') / '7MA+31', '26秋7MA+31'), Path('out') / '7MA+31')

    def test_matching_and_extraction(self):
        self.assertEqual(banshu_export.match_class_name(
            '26秋7MAA12-11', ['26秋7MAA12']), '26秋7MAA12')
        self.assertEqual(banshu_export.match_class_name(
            '26秋7MAA12-11', []), '')
        self.assertEqual(banshu_export.match_class_name(
            '26秋7MAA12-11', ['26秋7M']), '')
        names = banshu_export.fallback_class_names([
            {'className': {'name': 'A'}},
            {'groupNames': ['B', 3]},
            {'shortDesc': '26秋7MAA12-1'},
        ])
        self.assertEqual(names, ['A', 'B'])
        self.assertEqual(banshu_export.fallback_class_names(
            [{'shortDesc': '26秋7MAA12-1'}]), ['26秋7MAA12'])
        payload = {'obj': {'list': [
            {'groupName': 'A', 'id': 1},
            {'groups': {'1': 'B'}},
            {'1': 'C'},
            {'group_name': 'http://x'},
            {'name': '12345'},
        ]}}
        self.assertEqual(banshu_export.extract_class_names(payload),
                         ['A', 'B', 'C'])
        self.assertEqual(banshu_export.record_group_names(
            {'_groupNames': {'1': 'A', '2': 'A'}}), ['A', 'A'])
        self.assertEqual(banshu_export.record_group_names(
            {'_groupNames': ' A '}), ['A'])
        self.assertEqual(banshu_export.record_group_names({}), [])

    def test_grouping_and_order(self):
        records = [
            {'shortDesc': '26秋7MAA12-1'},
            {'shortDesc': '26秋7M批课-2'},
        ]
        groups = banshu_export.group_board_records(
            records, ['26秋7MAA12'])
        self.assertEqual(groups['26秋7MAA12'], [0])
        self.assertEqual(list(groups)[-1], banshu_export.UNCLASSIFIED_GROUP)
        self.assertTrue(banshu_export.record_matches_class_list(
            records[0], ['26秋7MAA12']))
        self.assertFalse(banshu_export.record_matches_class_list(
            records[1], ['26秋7MAA12']))
        self.assertTrue(banshu_export.record_matches_class_list(records[1], []))
        ordered = banshu_export.ordered_group_names(
            {'A': [0], banshu_export.UNCLASSIFIED_GROUP: [1]}, records,
            {'A': '26秋7MAA12'})
        self.assertEqual(ordered[-1], banshu_export.UNCLASSIFIED_GROUP)

    def test_collect_and_order_pages(self):
        manifest = {
            'cmds': [
                [0, 0, 1, 0, 'a', 100, '1.png', 0],
                [0, 0, 1, 0, 'a', 100, 'x.txt', 0],
                'bad',
            ],
            'coursewares': [
                {'bs': [{'path': '2.jpg', 'BsTime': 200, 'pageno': 1},
                        {'path': 'x'}]},
                'bad',
            ],
            'smallboards': [
                {'a': '3.png', 'BsTime': 300, 'b': ['4.png', 'x']},
                'bad',
            ],
        }
        pages = banshu_export.collect_pages(manifest)
        self.assertEqual([page['file'] for page in pages],
                         ['1.png', '2.jpg', '3.png', '4.png'])
        self.assertEqual(banshu_export.collect_pages(None), [])
        self.assertEqual(banshu_export.collect_pages({'cmds': 'bad'}),
                         [])
        ordered = banshu_export.order_pages(pages, 'type')
        self.assertEqual([page['kind'] for page in ordered],
                         ['board', 'small', 'small', 'annotate'])
        self.assertEqual(banshu_export.order_pages(pages, 'time')[0]['time'],
                         100)

    def test_fetch_records_guards_and_cap(self):
        class Session:
            def __init__(self, payload):
                self.payload = payload
                self.calls = 0

            def post(self, path, payload):
                self.calls += 1
                return self.payload

        session = Session({'code': '0', 'obj': {
            'list': [{'id': 1}], 'total': 1}})
        self.assertEqual(banshu_export.fetch_records(session, 0, 1), [{'id': 1}])
        with self.assertRaises(RuntimeError):
            banshu_export.fetch_records(Session({'code': 0, 'obj': [1]}), 0, 1)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(banshu_export.BatchCancelled):
            banshu_export.fetch_records(Session({}), 0, 1,
                                        cancel_event=cancel)
        endless = Session({'code': 0, 'obj': {'list': [{'id': 1}]}})
        with patch.object(banshu_export, 'MAX_RECORD_PAGES', 2):
            with self.assertRaises(RuntimeError):
                banshu_export.fetch_records(endless, 0, 1, page_size=1)
        self.assertEqual(endless.calls, 2)
        endless = Session({'code': 0, 'obj': {'list': [{'id': 1}]}})
        with patch.object(banshu_export, 'MAX_RECORD_PAGES', 2):
            with self.assertRaises(RuntimeError):
                banshu_export.fetch_records(endless, 0, 1, page_size=0)

    def test_bs_manifest_paths(self):
        class Session:
            def __init__(self, gen):
                self.gen = gen
                self.calls = []

            def post(self, path, payload):
                self.calls.append((path, payload))
                if path.endswith('getBsSign'):
                    return {'code': '0', 'obj': {
                        'signature': 's', 'a': 1}}
                return self.gen

        manifest = {'cmds': []}
        import gzip
        packed = gzip.compress(json.dumps(manifest).encode('utf-8'))
        session = Session({'code': 0, 'obj': {
            'status': 2, 'url': 'https://x/p', 'pathprefix': 'https://x//'}})
        with patch.object(banshu_export, 'http_get', return_value=packed):
            parsed, prefix = banshu_export.bs_manifest(session, 'id')
        self.assertEqual(parsed, manifest)
        self.assertEqual(prefix, 'https://x')
        with self.assertRaises(banshu_export.BsUnavailable):
            banshu_export.bs_manifest(
                Session({'code': 0, 'obj': {'status': 2}}), 'id',
                poll_delays=(0,))
        with self.assertRaises(banshu_export.BsUnavailable):
            banshu_export.bs_manifest(
                Session({'code': 0, 'obj': {'status': 1}}), 'id',
                poll_delays=(0,))
        with self.assertRaises(banshu_export.BsUnavailable):
            banshu_export.bs_manifest(
                type('S', (), {'post': lambda self, *a: {
                    'code': 0, 'obj': {}}})(), 'id', poll_delays=(0,))

    def test_pdf_build_and_validate(self):
        import zlib
        page = banshu_export.PdfPage(
            'a.png', 1, 1, zlib.compress(b'\x00\x00\x00'))
        pdf, meta = banshu_export.build_pdf_pages([page, page])
        self.assertTrue(pdf.startswith(b'%PDF-'))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.pdf'
            path.write_bytes(pdf)
            self.assertTrue(banshu_export.validate_pdf(
                path, meta=meta, expected_bytes=pdf, pages=2))
            self.assertFalse(banshu_export.validate_pdf(
                path, expected_bytes=pdf, pages=1))
            path.write_bytes(b'bad')
            self.assertFalse(banshu_export.validate_pdf(path, pages=0))
            with self.assertRaises(OSError):
                banshu_export.validate_pdf(Path(tmp) / 'missing.pdf')
        legacy, legacy_meta = banshu_export.build_pdf(
            [(b'\x00\x00\x00', 1, 1)])
        self.assertTrue(legacy.startswith(b'%PDF-'))
        self.assertEqual(len(legacy_meta), 1)

    def test_export_class_and_records(self):
        record = {'shortDesc': 'A-1', 'fileId': 'f1'}
        manifest = {'cmds': [[0, 0, 1, 0, 'a', 1, '1.png', 0]]}
        page = banshu_export.PdfPage(
            '1.png', 1, 1, __import__('zlib').compress(b'\x00\x00\x00'))
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(banshu_export, 'bs_manifest',
                              return_value=(manifest, 'https://x')), \
                 patch.object(banshu_export, 'prepare_page_streams',
                              return_value=[page]):
                path = banshu_export.export_class(
                    object(), record, Path(tmp), 'time', logger=lambda _m: None)
            self.assertTrue(path.is_file())
            with patch.object(banshu_export, 'bs_manifest',
                              return_value=(manifest, 'https://x')), \
                 patch.object(banshu_export, 'prepare_page_streams',
                              return_value=[page]):
                logs = []
                again = banshu_export.export_class(
                    object(), record, Path(tmp), 'time', logger=logs.append)
            self.assertEqual(again, path)
            self.assertTrue(any('复用' in line for line in logs))
            self.assertIsNone(banshu_export.export_class(
                object(), {'shortDesc': 'x'}, Path(tmp), 'time'))
            with patch.object(banshu_export, 'bs_manifest',
                              side_effect=banshu_export.BsUnavailable('no')):
                self.assertIsNone(banshu_export.export_class(
                    object(), record, Path(tmp), 'time'))
        self.assertEqual(banshu_export.export_records(
            object(), [], Path('.'), 'time')['total'], 0)
        records = [{'shortDesc': 'A-1'}, {'shortDesc': 'B-1'}]

        def fake_export(session, item, out_root, sort_mode, **kwargs):
            if item['shortDesc'] == 'B-1':
                raise RuntimeError('bad')
            return Path(out_root) / 'A.pdf'

        with patch.object(banshu_export, 'export_class',
                          side_effect=fake_export):
            result = banshu_export.export_records(
                object(), records, Path('out'), 'time', max_workers=1)
        self.assertEqual(result['total'], 2)
        self.assertEqual(len(result['failed']), 1)

    def test_range_and_run_board_export(self):
        now = 1792944444000
        for preset in banshu_export.RANGE_PRESETS[:-1]:
            start, end = banshu_export.resolve_range(preset, now_ms=now)
            self.assertLessEqual(start, end)
        with self.assertRaises(ValueError):
            banshu_export.resolve_range('自定义')
        with self.assertRaises(ValueError):
            banshu_export.resolve_range('自定义', '2026-01-02', '2026-01-01')
        with self.assertRaises(ValueError):
            banshu_export.resolve_range('自定义', '2026-01-01', '2999-01-01')
        self.assertEqual(banshu_export.day_start(now) % 1000, 0)
        self.assertGreater(banshu_export.parse_date('2026-01-01'), 0)
        with patch.object(banshu_export, 'fetch_records', return_value=[]), \
                patch.object(banshu_export, 'enrich_record_groups'), \
                patch.object(banshu_export, 'export_records',
                             return_value={'groups': {}, 'failed': [],
                                           'total': 0}):
            result = banshu_export.run_board_export(
                object(), Path('out'), 'time', 0, 1)
        self.assertEqual(result['total'], 0)
        mixed = [{'shortDesc': 'A-1', 'createTime': 'bad'},
                 {'shortDesc': 'A-2', 'createTime': 1}]
        with patch.object(banshu_export, 'fetch_records', return_value=mixed), \
                patch.object(banshu_export, 'enrich_record_groups'), \
                patch.object(banshu_export, 'export_records',
                             return_value={'groups': {}, 'failed': [],
                                           'total': 2}) as export:
            banshu_export.run_board_export(object(), Path('out'), 'time', 0, 1)
        self.assertEqual(export.call_args.args[1][0]['shortDesc'], 'A-1')


class UpdateBranchTests(unittest.TestCase):
    def test_redirect_handlers_reject_http(self):
        request = __import__('urllib.request').request.Request('https://x/')
        with self.assertRaises(update_checker.UpdateError):
            update_checker._HttpsOnlyRedirectHandler().redirect_request(
                request, None, 302, 'Found', {}, 'http://x/')
        with self.assertRaises(update_installer.UpdateInstallError):
            update_installer._HttpsOnlyRedirectHandler().redirect_request(
                request, None, 302, 'Found', {}, 'http://x/')

    def test_download_file_success_and_errors(self):
        class Response:
            def __init__(self, body=b'abc', length='3', url='https://x/a'):
                self.body = body
                self.headers = {'Content-Length': length}
                self.url = url

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return self.url

            def read(self, size):
                data, self.body = self.body[:size], self.body[size:]
                return data

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / 'a.bin'
            with patch.object(update_installer, '_open_url',
                              return_value=Response()):
                self.assertEqual(update_installer.download_file(
                    'https://x/a.exe', dest), dest)
            self.assertEqual(dest.read_bytes(), b'abc')
            with patch.object(update_installer, '_open_url',
                              return_value=Response(length='9')):
                with self.assertRaises(update_installer.UpdateInstallError):
                    update_installer.download_file(
                        'https://x/a.exe', Path(tmp) / 'b.bin')
            with patch.object(update_installer, '_open_url',
                              return_value=Response(url='http://x')):
                with self.assertRaises(update_installer.UpdateInstallError):
                    update_installer.download_file(
                        'https://x/a.exe', Path(tmp) / 'c.bin')
            with patch.object(update_installer, '_open_url',
                              return_value=Response(length='4')):
                with self.assertRaises(update_installer.UpdateInstallError):
                    update_installer.download_file(
                        'https://x/a.exe', Path(tmp) / 'd.bin')
            self.assertEqual(list(Path(tmp).glob('*.part')), [])

    def test_fetch_sha256_and_launch_guards(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def geturl(self):
                return 'https://x/s'

            def read(self, size):
                return b'a' * 64

        with patch.object(update_installer, '_open_url',
                          return_value=Response()):
            self.assertEqual(update_installer.fetch_sha256('https://x/s'),
                             'a' * 64)
        with self.assertRaises(update_installer.UpdateInstallError):
            update_installer.launch_updater(Path('x'))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'x.exe'
            target.write_bytes(b'MZ')
            with patch.object(update_installer.sys, 'platform', 'linux'):
                with self.assertRaises(update_installer.UpdateInstallError):
                    update_installer.launch_updater(
                        Path(tmp) / 'p.exe', target=target)
            with self.assertRaises(update_installer.UpdateInstallError):
                update_installer.launch_updater(
                    Path(tmp) / 'missing.exe', target=target)

    def test_version_and_cleanup_edges(self):
        self.assertTrue(update_checker.is_newer('2.3.2.1', '2.3.2'))
        self.assertTrue(update_checker.is_newer('abc', '2.3.2'))
        self.assertFalse(update_checker.is_newer('2.3.2', '2.3.2'))
        self.assertEqual(update_checker.version_key('v2.3.2'), (2, 3, 2))
        self.assertFalse(update_installer.can_auto_update(None))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'x.exe'
            target.write_bytes(b'MZ')
            old = Path(tmp) / 'x.exe.old-1-1'
            old.write_bytes(b'x')
            os.utime(old, (time.time() - 99, time.time() - 99))
            removed = update_installer.cleanup_stale_update_files(
                target=target, max_age_seconds=0)
            self.assertIn(old, removed)


class ThemeBranchTests(unittest.TestCase):
    def test_palette_and_colors(self):
        self.assertEqual(theme_art.theme_key('bad'), 'light')
        self.assertEqual(theme_art.palette_for('bad'), theme_art.THEMES['light'])
        self.assertEqual(theme_art.color_role('#1f6b4e'), 'ACCENT')
        self.assertEqual(theme_art.color_role('#e8ece5'), 'TRACK')
        self.assertIsNone(theme_art.color_role(''))
        self.assertEqual(theme_art._hex('#ff0000'), (255, 0, 0))
        self.assertEqual(theme_art._mix('#000000', '#ffffff', 0.5),
                         (128, 128, 128))
        self.assertEqual(theme_art._rgba('#000000', 0), (0, 0, 0, 0))

    def test_assets_and_render(self):
        self.assertTrue(str(theme_art.user_asset_path('x.png')).endswith(
            'x.png'))
        with patch.object(theme_art, 'user_asset_path',
                          return_value=Path(tempfile.gettempdir()) / 'no.png'):
            self.assertIsNone(theme_art.load_user_asset('no.png'))
            self.assertIsNone(theme_art.render_user_asset('no.png', (10, 10)))
        with patch.object(theme_art.Image, 'open',
                          side_effect=theme_art.Image.DecompressionBombError('x')):
            self.assertIsNone(theme_art.load_user_asset('x.png'))
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'x.png'
            from PIL import Image
            Image.new('RGBA', (10, 8), (255, 0, 0, 255)).save(source)
            with patch.object(theme_art, 'user_asset_path',
                              return_value=source):
                image = theme_art.render_user_asset(
                    'x.png', (20, 20), background='#ffffff',
                    crop=(0, 0, 0.5, 1))
            self.assertEqual(image.size, (20, 20))
        for key in ('light', 'dark', 'dog', 'bad'):
            with self.subTest(key=key):
                self.assertEqual(theme_art.render_hero(key, 320, 64).size,
                                 (320, 64))
                self.assertEqual(theme_art.render_app_icon(key, 32).size,
                                 (32, 32))
                self.assertEqual(theme_art.render_settings_icon(key, 16).size,
                                 (16, 16))
                self.assertEqual(theme_art.render_theme_preview(
                    key, 144, 88).size, (144, 88))


class TokenMacBranchTests(unittest.TestCase):
    def test_bundle_executable_plist_fallback_and_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / 'AiCloud.app'
            macos = app / 'Contents' / 'MacOS'
            macos.mkdir(parents=True)
            exe = macos / 'AiCloud'
            exe.write_bytes(b'x')
            plist = app / 'Contents' / 'Info.plist'
            with plist.open('wb') as handle:
                token_macos.plistlib.dump(
                    {'CFBundleExecutable': 'AiCloud'}, handle)
            self.assertEqual(token_macos._bundle_executable(app), exe)
            self.assertEqual(token_macos.find_app.__name__, 'find_app')
            missing = Path(tmp) / 'Missing.app'
            missing.mkdir()
            self.assertIsNone(token_macos._bundle_executable(missing))
        with patch.object(token_macos.Path, 'iterdir',
                          side_effect=PermissionError('denied')):
            with tempfile.TemporaryDirectory() as tmp:
                app = Path(tmp) / 'x.app'
                (app / 'Contents' / 'MacOS').mkdir(parents=True)
                self.assertIsNone(token_macos._bundle_executable(app))

    def test_process_helpers_and_wait(self):
        output = ' 123 /Applications/AiCloud.app/Contents/MacOS/AiCloud\n' \
                 'bad line\n 456 /usr/bin/python\n'
        with patch.object(token_macos.subprocess, 'check_output',
                          return_value=output):
            self.assertEqual(len(token_macos._process_lines()), 2)
            running = token_macos.running_processes(Path('/Applications/AiCloud.app'))
            self.assertEqual([pid for pid, _ in running], [123])
        with patch.object(token_macos.subprocess, 'check_output',
                          side_effect=OSError('x')):
            self.assertEqual(token_macos._process_lines(), [])
        with patch.object(token_macos, 'debug_port_open',
                          side_effect=[False, True]), \
                patch.object(token_macos.time, 'sleep'):
            self.assertTrue(token_macos._wait_for_port(9222, 1))
        cancel = threading.Event()
        cancel.set()
        self.assertFalse(token_macos._wait_for_port(9222, 1, cancel))
        with patch.object(token_macos, 'read_context',
                          side_effect=[token_macos.CDPError('x'),
                                       {'ok': 1}]), \
                patch.object(token_macos.time, 'sleep'):
            self.assertEqual(token_macos._wait_for_context(9222, 1), {'ok': 1})
        with patch.object(token_macos, 'read_context',
                          side_effect=token_macos.CDPError('x')), \
                patch.object(token_macos.time, 'sleep'):
            with self.assertRaises(token_macos.MacTokenError):
                token_macos._wait_for_context(9222, 0.01)

    def test_cdp_frames_and_handshake_cleanup(self):
        with self.assertRaises(token_macos.CDPError):
            token_macos.CDPClient('http://x/')

        class FakeSocket:
            def __init__(self, incoming=b''):
                self.incoming = incoming
                self.sent = b''
                self.closed = False

            def settimeout(self, value):
                pass

            def sendall(self, data):
                self.sent += data

            def recv(self, size):
                data, self.incoming = self.incoming[:size], self.incoming[size:]
                return data

            def close(self):
                self.closed = True

        fake = FakeSocket(b'')
        with patch.object(token_macos.socket, 'create_connection',
                          return_value=fake):
            with self.assertRaises(token_macos.CDPError):
                token_macos.CDPClient('ws://127.0.0.1:9222/x')
        self.assertTrue(fake.closed)

        client = token_macos.CDPClient.__new__(token_macos.CDPClient)
        client._lock = threading.Lock()
        client._socket = FakeSocket()
        for size in (3, 200, 70000):
            client._send({'x': 'a' * size})
        self.assertTrue(client._socket.sent.startswith(b'\x81'))

        frame = b'\x81' + bytes([len(b'{"id":1}')]) + b'{"id":1}'
        client._socket.incoming = frame
        self.assertEqual(client._read_frame(), {'id': 1})
        client._socket.incoming = b'\x88\x00'
        self.assertIsNone(client._read_frame())
        client._socket.incoming = b'\x89\x00'
        self.assertEqual(client._read_frame(), {})

    def test_read_context_and_acquire(self):
        class FakeClient:
            def __init__(self, value):
                self.value = value
                self.closed = False

            def evaluate(self, expression):
                return self.value

            def close(self):
                self.closed = True

        with patch.object(token_macos, 'list_targets', return_value=[
                {'type': 'page', 'url': 'index.html',
                 'webSocketDebuggerUrl': 'ws://x'}]):
            with patch.object(token_macos, 'CDPClient',
                              return_value=FakeClient(json.dumps({
                                  'accessToken': 'tok',
                                  'dhost': 'https://a.aiwenyun.cn',
                                  'version': '1'}))):
                context = token_macos.read_context()
        self.assertEqual(context['token'], 'tok')
        self.assertEqual(context['base_url'], 'https://a.aiwenyun.cn/')
        with patch.object(token_macos, 'list_targets', return_value=[]):
            with self.assertRaises(token_macos.MacTokenError):
                token_macos.read_context()
        with patch.object(token_macos, 'find_app',
                          return_value=Path('/Applications/AiCloud.app')), \
                patch.object(token_macos, 'debug_port_open',
                             return_value=True), \
                patch.object(token_macos, '_wait_for_context',
                             return_value={'token': 'tok',
                                           'base_url': 'https://a.aiwenyun.cn/',
                                           'platform': '', 'version': '1'}):
            batch = token_macos.acquire_tokens()
        self.assertEqual(batch.candidates[0].value, 'tok')
        self.assertEqual(batch.info['restarted'], False)
        with patch.object(token_macos, 'find_app',
                          return_value=Path('/Applications/AiCloud.app')), \
                patch.object(token_macos, 'debug_port_open',
                             return_value=False):
            with self.assertRaises(token_macos.MacTokenError):
                token_macos.acquire_tokens(allow_restart=False)


class CliBranchTests(unittest.TestCase):
    def _summary(self):
        return pipeline.ExportSummary(
            path=Path('x.xlsx'), total=1, success=1, failed=0, elapsed=0.1,
            base_url='u', client_version='v', token_issued_at=0)

    def test_parser_and_success(self):
        parser = cli.build_parser()
        args = parser.parse_args(['--no-open', '--no-details', '--workers', '2'])
        self.assertTrue(args.no_open)
        self.assertTrue(args.no_details)
        self.assertEqual(args.workers, 2)
        with patch.object(cli, 'run_export',
                          return_value=self._summary()):
            self.assertEqual(cli.main(['--no-open', '--no-details']), 0)

    def test_error_exit_codes(self):
        with patch.object(cli, 'run_export',
                          side_effect=cli.NoClassesError('none')):
            self.assertEqual(cli.main([]), 3)
        with patch.object(cli, 'run_export',
                          side_effect=cli.Cancelled('cancel')):
            self.assertEqual(cli.main([]), 130)
        with patch.object(cli, 'run_export',
                          side_effect=cli.PipelineError('bad')):
            self.assertEqual(cli.main([]), 4)
        with patch.object(cli, 'run_export', side_effect=RuntimeError('x')):
            self.assertEqual(cli.main([]), 4)
        with patch.object(cli, 'run_export', side_effect=KeyboardInterrupt):
            self.assertEqual(cli.main([]), 130)


class GuiBranchTests(unittest.TestCase):
    def test_helpers_and_version_file(self):
        self.assertEqual(gui._fmt_clock(62), '01:02')
        self.assertEqual(gui._fmt_clock(3662), '1:01:02')
        self.assertEqual(gui._fmt_clock(float('nan')), '00:00')
        self.assertEqual(gui._rgb('#ff0000'), (255, 0, 0))
        self.assertEqual(gui._rgb('bad'), (0, 0, 0))
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'v.txt'
            with patch.object(gui.sys, 'argv',
                              ['gui.py', '--version-file', str(target)]):
                self.assertEqual(gui.main(), 0)
            self.assertEqual(target.read_text(encoding='utf-8'),
                             config.APP_VERSION)
        with patch.object(gui.sys, 'argv', ['gui.py', '--version-file']):
            self.assertEqual(gui.main(), 2)

    def test_board_work_reports_mkdir_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / 'file'
            out_file.write_text('x', encoding='utf-8')
            app = gui.App.__new__(gui.App)
            posted = []
            app._post = lambda kind, payload=None: posted.append((kind, payload))
            gui.App._board_work(app, [], 'time', out_file)
        self.assertEqual(posted[0][0], 'error')
        self.assertIn('保存目录', posted[0][1])

    def test_app_state_smoke(self):
        try:
            app = gui.App()
        except Exception as error:  # pragma: no cover - 无显示环境
            self.skipTest('Tk unavailable: %s' % error)
        app.root.withdraw()
        try:
            app._switch_view('bad')
            app._switch_view('board')
            app._switch_view('feedback')
            app._switch_view('invite')
            app._refresh_auth_ui()
            app.set_busy(True)
            app.set_busy(False)
            app.board_token = 'tok'
            app.board_origin = 'client'
            record = {'shortDesc': '26秋7MA+31（数形许）-1',
                      'liveclass': 1, 'fileId': 'f1'}
            app._boards_listed({'records': [record],
                                'class_names': ['26秋7MA+31']})
            key = app._board_key(record)
            app._toggle_board(key)
            self.assertEqual(len(app._checked_records()), 1)
            app.on_select_all_boards()
            app.on_clear_boards()
            app._on_board_event({'phase': 'pages', 'done': 1, 'total': 1,
                                 'name': 'A', 'percent': 50,
                                 'pages_done': 1, 'pages_total': 2})
            app._finish_boards({'groups': {}, 'failed': [],
                                'out_root': str(Path(tempfile.gettempdir())
                                                / 'missing-out'),
                                'preset': 'x'})
            homework = {'zuoyeId': 'z', 'title': '【数·课前预习】专题十',
                        'groupName': '2026027MA+31作业班',
                        'sortTime': 1792944444000}
            app._feedbacks_listed({'records': [record],
                                   'class_names': ['26秋7MA+31'],
                                   'homeworks': [homework]})
            if app.feedback_items:
                entry = app.feedback_items[0]
                app._toggle_feedback(entry['key'])
                child = app._feedback_child_id(entry['key'], 'preview')
                app._toggle_feedback_file(child)
                app._show_feedback_class('全部班级')
                app.on_select_all_feedbacks()
                app.on_clear_feedbacks()
            app._on_feedback_event({'phase': 'done', 'done': 1, 'total': 1,
                                    'name': '反馈', 'percent': 100})
            app._finish_feedbacks({'files': [], 'failed': [],
                                   'out_root': str(Path(tempfile.gettempdir())
                                                   / 'missing-out'),
                                   'elapsed': 0})
            app.invite_progress.begin('x', 'y', indeterminate=True)
            app.invite_progress.update(50, detail='d', sub_detail='s',
                                       sub_percent=20)
            app.invite_progress.tick()
            app.invite_progress.fail('f', 'd', state='失败')
            app.invite_log.append('hello')
            app.invite_log.set_expanded(True)
            app.invite_log.set_expanded(False)
            app.invite_log.clear()
            app.apply_theme('dark', persist=False)
            app.apply_theme('light', persist=False)
            app.open_settings()
            settings = app._settings_window
            self.assertIsNotNone(settings)
            app._refresh_settings_ui()
            app._close_settings(settings)
            app._post('log', 'x')
            app._drain_queue()
            app._show_update_result({'status': 'error', 'current': '1',
                                     'message': 'm'})
            app.open_after.set(False)
            app.finish(self._summary())
            app.on_close()
        finally:
            try:
                app.root.destroy()
            except Exception:
                pass

    def _summary(self):
        return pipeline.ExportSummary(
            path=Path('x.xlsx'), total=1, success=1, failed=0, elapsed=0.1,
            base_url='u', client_version='v', token_issued_at=0)


class CoverageGapTests(unittest.TestCase):
    def test_feedback_helpers_and_legacy_export(self):
        self.assertEqual(feedback_export._student_number('123张三'), 123)
        self.assertEqual(feedback_export._student_number('张三'), 0)
        self.assertEqual(feedback_export.file_id(
            {'fileCommon': {'_id': 'x'}}), 'x')
        self.assertEqual(feedback_export.feedback_class_name(
            {'shortDesc': '26秋7MA+31-1'}), '26秋7MA+31')
        with patch.object(feedback_export, 'export_bundle',
                          return_value={'ok': 1}) as export:
            self.assertEqual(feedback_export.export_feedback(
                object(), [], Path('out')), {'ok': 1})
            export.assert_called_once()
        self.assertIsInstance(feedback_export.default_out_root(), Path)

    def test_homework_log_and_default_root(self):
        with patch.object(homework_export, 'print') as printed:
            homework_export.log('hello')
            printed.assert_called_once()
            self.assertEqual(printed.call_args.args[0], 'hello')
        self.assertIsInstance(homework_export.default_out_root(), Path)

    def test_login_device_paths_and_ram(self):
        self.assertIsInstance(login._client_storage_dirs(), list)
        self.assertTrue(login._device_id_file().name.endswith('device-id.txt'))
        ram = login._total_ram_gb()
        self.assertIsInstance(ram, str)

    def test_net_connection_pool_and_session_post(self):
        transport = net.HttpTransport('https://x.cn/')
        first = MagicMock()
        second = MagicMock()
        with patch.object(net.http.client, 'HTTPSConnection',
                          side_effect=[first, second]) as factory:
            self.assertIs(transport._connection(), first)
            self.assertIs(transport._connection(), first)
            transport._drop_connection()
            self.assertIs(transport._connection(), second)
        factory.assert_called()
        transport.close()
        session = net.ApiSession('https://x.cn/', 'tok')
        with patch.object(session.transport, 'post_json',
                          return_value={'code': 0}) as post:
            self.assertEqual(session.post('p', {'a': 1}), {'code': 0})
            session.validate()
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[-1].args[0], config.TIMETABLE_PATH)

    def test_theme_drawing_helpers(self):
        from PIL import Image, ImageDraw
        image = Image.new('RGB', (20, 20), 'white')
        draw = ImageDraw.Draw(image)
        self.assertEqual(theme_art._vertical_gradient((4, 4), '#000000',
                                                      '#ffffff').size, (4, 4))
        points = theme_art._star_points(10, 10, 5, 2)
        self.assertEqual(len(points), 10)
        theme_art._draw_star(draw, 10, 10, 5, '#000000')
        theme_art._draw_paw(draw, 10, 10, 8, '#000000')

    def test_token_source_platform_name(self):
        self.assertEqual(token_source.platform_name(), sys.platform)

    @unittest.skipUnless(token_windows is not None, 'Windows-only module')
    def test_token_windows_iter_regions_and_scan_pid(self):
        mbi = token_windows._MBI()
        mbi.BaseAddress = 0x1000
        mbi.RegionSize = 0x100
        mbi.State = token_windows.MEM_COMMIT
        mbi.Protect = 0x04
        mbi.Type = token_windows.MEM_PRIVATE

        def query(handle, address, pointer, size):
            pointer._obj.BaseAddress = mbi.BaseAddress
            pointer._obj.RegionSize = mbi.RegionSize
            pointer._obj.State = mbi.State
            pointer._obj.Protect = mbi.Protect
            pointer._obj.Type = mbi.Type
            return 1 if not address.value else 0

        with patch.object(token_windows._kernel32, 'VirtualQueryEx',
                          side_effect=query):
            regions = list(token_windows._iter_regions(1))
        self.assertEqual(len(regions), 1)
        self.assertEqual(regions[0][0], 0x1000)
        with patch.object(token_windows._kernel32, 'OpenProcess',
                          return_value=0):
            stats = token_windows.ScanStats()
            self.assertEqual(token_windows._scan_pid(1, time.monotonic() + 1,
                                                     stats=stats), [])
            self.assertEqual(stats.denied_processes, 1)

    def test_token_macos_app_and_network_helpers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / 'AiCloud.app'
            (app / 'Contents' / 'MacOS').mkdir(parents=True)
            with patch.object(token_macos, '_app_candidates',
                              return_value=[app]):
                self.assertEqual(token_macos.find_app(), app)
            with patch.object(token_macos, 'running_processes',
                              return_value=[(1, 'x')]):
                self.assertTrue(token_macos.client_running())
            with patch.object(token_macos.urllib.request, 'urlopen') as opened:
                opened.return_value.__enter__.return_value = object()
                self.assertTrue(token_macos.debug_port_open())
            with patch.object(token_macos.urllib.request, 'urlopen',
                              side_effect=OSError('x')):
                self.assertFalse(token_macos.debug_port_open())
            with patch.object(token_macos, '_bundle_executable',
                              return_value=app / 'Contents' / 'MacOS' / 'x'), \
                    patch.object(token_macos.subprocess, 'run') as run, \
                    patch.object(token_macos, 'running_processes',
                                 return_value=[]):
                run.return_value = MagicMock(returncode=0, stdout='', stderr='')
                token_macos.launch_app(app)
                token_macos.quit_app(app)
                token_macos.restore_app(app)
        with patch.object(token_macos.urllib.request, 'urlopen',
                          side_effect=OSError('x')):
            with self.assertRaises(token_macos.CDPError):
                token_macos.list_targets()

    def test_update_fetch_and_installer_helpers(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size):
                return b'{"version": "2.3.2"}'

        opener = MagicMock()
        opener.open.return_value = Response()
        with patch.object(update_checker.urllib.request, 'build_opener',
                          return_value=opener):
            self.assertIn('2.3.2', update_checker.fetch_update_payload(
                'https://x/'))
        opener = MagicMock()
        opener.open.return_value = Response()
        with patch.object(update_installer.urllib.request, 'build_opener',
                          return_value=opener):
            response = update_installer._open_url('https://x/a.exe', 1)
            self.assertIs(response.__enter__(), response)
        with patch.object(update_installer.sys, 'platform', 'win32'), \
                patch.object(update_installer.sys, 'frozen', True,
                             create=True), \
                patch.object(update_installer.sys, 'executable',
                             str(Path(tempfile.gettempdir()) / 'x.exe')):
            self.assertFalse(update_installer.is_frozen_windows_exe())
        with patch.object(update_installer.Path, 'is_file',
                          return_value=True):
            self.assertTrue(update_installer._powershell_path().endswith(
                'powershell.exe'))
        with patch.object(update_installer.Path, 'is_file',
                          return_value=False), \
                patch.object(update_installer.shutil, 'which',
                             return_value=''):
            with self.assertRaises(update_installer.UpdateInstallError):
                update_installer._powershell_path()

    def test_banshu_ranges_and_progress(self):
        self.assertIsInstance(banshu_export.default_out_root(), Path)
        self.assertEqual(banshu_export._page_url('', 'a.png'), 'a.png')
        self.assertEqual(banshu_export._page_url('https://x/', '/a.png'),
                         'https://x/a.png')
        event = threading.Event()
        event.set()
        with self.assertRaises(banshu_export.BatchCancelled):
            banshu_export.PageDownloader._wait(0.1, event)
        banshu_export.PageDownloader._wait(0)
        events = []
        tracker = banshu_export._BatchProgress(
            1, on_event=events.append, logger=lambda _m: None)
        tracker.begin(0, {'shortDesc': 'A'})
        tracker.phase(0, {'shortDesc': 'A'}, 'manifest')
        tracker.pages(0, {'shortDesc': 'A'}, 1, 2)
        tracker.finish(0, {'shortDesc': 'A'}, True)
        self.assertEqual(events[-1]['percent'], 100.0)
        self.assertEqual(events[-1]['phase'], 'done')

    def test_page_downloader_and_image_helpers(self):
        class Response:
            def __init__(self, status=200, body=b'png', location=''):
                self.status = status
                self._body = body
                self._location = location
                self.will_close = False

            def read(self):
                return self._body

            def getheader(self, name):
                return self._location if name == 'Location' else ''

        class Connection:
            def __init__(self, responses):
                self.responses = list(responses)
                self.timeout = 0
                self.calls = []

            def request(self, method, path, headers=None):
                self.calls.append((method, path))

            def getresponse(self):
                return self.responses.pop(0)

            def close(self):
                pass

        downloader = banshu_export.PageDownloader()
        connection = Connection([Response(200, b'ok')])
        with patch.object(downloader, '_connection', return_value=connection):
            self.assertEqual(downloader.get('https://x/a.png'), b'ok')
        with self.assertRaises(RuntimeError):
            downloader.get('http://x/a.png')
        with patch.object(downloader, '_connection',
                          side_effect=[Connection([Response(
                              301, location='https://x/b.png')]),
                                       Connection([Response(200, b'ok2')])]):
            with patch.object(banshu_export.PageDownloader, '_close'):
                self.assertEqual(downloader.get('https://x/a.png'), b'ok2')

        from PIL import Image
        import io
        buffer = io.BytesIO()
        Image.new('RGB', (2, 2), 'white').save(buffer, format='PNG')
        png = buffer.getvalue()

        class Downloader:
            def get(self, url, cancel_event=None, timeout=None):
                return png

        page = banshu_export._prepare_one_page(
            Downloader(), 'https://x', 'a.png')
        self.assertEqual((page.width, page.height), (2, 2))
        images = banshu_export.load_page_images(
            'https://x', [{'file': 'a.png'}], downloader=Downloader())
        self.assertEqual(images[0][0], 'a.png')
        self.assertEqual((images[0][2], images[0][3]), (2, 2))
        with patch.object(banshu_export, 'PageDownloader') as factory:
            factory.return_value.get.return_value = b'ok'
            self.assertEqual(banshu_export.http_get('https://x/a'), b'ok')
        with patch.object(banshu_export.Path, 'is_file', return_value=False):
            self.assertIsNone(banshu_export.render_blank_check(Path('x.pdf')))

    def test_cdp_call_evaluate_and_close(self):
        client = token_macos.CDPClient.__new__(token_macos.CDPClient)
        client._timeout = 1
        client._next_id = 0
        client._lock = threading.Lock()
        client._responses = {}
        client._condition = threading.Condition()
        client._closed = False
        client._socket = MagicMock()

        def send(payload):
            client._responses[payload['id']] = {
                'result': {'result': {'value': 1}}}

        client._send = send
        result = client.call('Runtime.evaluate')
        self.assertEqual(result['result']['value'], 1)
        self.assertEqual(client.evaluate('x'), 1)
        client._send = lambda payload: client._responses.update({
            payload['id']: {'error': {'message': 'bad'}}})
        with self.assertRaises(token_macos.CDPError):
            client.call('x')
        client.close()
        self.assertTrue(client._closed)
        client._socket.close.assert_called_once()
