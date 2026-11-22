# -*- coding: utf-8 -*-
"""板书导出加速路径的纯函数测试（不联网）。

覆盖：页面并发下载顺序、PDF 去重与快速校验、批量导出的并发/失败隔离、
取消、非 HTTPS 拒绝，以及 genBs 轮询前的取消。
"""

import io
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from banshu_export import (BatchCancelled, PdfPage, PageDownloader,
                           bs_manifest, build_pdf, build_pdf_pages,
                           export_records, prepare_page_streams, validate_pdf)


def png_bytes(color=(255, 255, 255), size=(80, 60)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    return buffer.getvalue()


class FakeDownloader:
    """只实现 get()，模拟板书页 CDN。"""

    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def get(self, url, cancel_event=None, timeout=None):
        self.calls.append(url)
        if cancel_event is not None and cancel_event.is_set():
            raise BatchCancelled('已取消板书导出。')
        return self.payloads[url.rsplit('/', 1)[-1]]


class PagePrepareTests(unittest.TestCase):
    def test_parallel_download_keeps_page_order_and_dedupes(self):
        downloader = FakeDownloader({
            'a.png': png_bytes('white'),
            'b.png': png_bytes('red'),
        })
        pages = [{'file': 'a.png'}, {'file': 'b.png'}, {'file': 'a.png'}]
        seen = []
        result = prepare_page_streams(
            'https://cdn.example/board', pages, workers=2,
            downloader=downloader,
            on_page=lambda done, total, name: seen.append((done, total, name)))

        self.assertEqual([page.name for page in result],
                         ['a.png', 'b.png', 'a.png'])
        self.assertEqual(downloader.calls,
                         ['https://cdn.example/board/a.png',
                          'https://cdn.example/board/b.png'])
        # 两个线程谁先完成不确定，只校验「每页各回调一次、总数对」
        self.assertEqual(sorted((done, total) for done, total, _name in seen),
                         [(1, 2), (2, 2)])
        self.assertEqual(sorted(name for _d, _t, name in seen),
                         ['a.png', 'b.png'])
        self.assertTrue(all(page.stream for page in result))
        # 压缩流应能解回原始尺寸的 RGB 字节
        self.assertEqual(len(zlib.decompress(result[0].stream)),
                         80 * 60 * 3)

    def test_non_https_download_is_rejected(self):
        with self.assertRaises(RuntimeError):
            PageDownloader().get('http://cdn.example/a.png')


class PdfTests(unittest.TestCase):
    def _page(self, name, color='white'):
        image = Image.new('RGB', (80, 60), color)
        return PdfPage(name, image.width, image.height,
                       zlib.compress(image.tobytes(), 3))

    def test_build_dedupes_same_page_and_validates_fast(self):
        pages = [self._page('a.png'), self._page('b.png'),
                 self._page('a.png')]
        pdf, meta = build_pdf_pages(pages)
        self.assertEqual(pdf.count(b'/Type /Page '), 3)
        self.assertEqual(pdf.count(b'/Subtype /Image'), 2)
        self.assertEqual(len(meta), 2)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'board.pdf'
            path.write_bytes(pdf)
            self.assertTrue(validate_pdf(path, meta=meta,
                                         expected_bytes=pdf,
                                         pages=3))
            with patch('banshu_export.log', lambda *args, **kwargs: None):
                self.assertFalse(validate_pdf(path, meta=meta,
                                              expected_bytes=pdf + b'x',
                                              pages=3))

    def test_legacy_build_and_validation_still_work(self):
        image = Image.new('RGB', (80, 60), 'white')
        raw = image.tobytes()
        images = [('', raw, 80, 60), ('', raw, 80, 60)]
        pdf, meta = build_pdf(images)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'legacy.pdf'
            path.write_bytes(pdf)
            self.assertTrue(validate_pdf(path, images=images, meta=meta))
        self.assertEqual(pdf.count(b'/Type /Page '), 2)
        self.assertEqual(pdf.count(b'/Subtype /Image'), 2)


class BatchTests(unittest.TestCase):
    def _fake_export(self, fail_names=()):
        def fake(session, record, out_root, sort_mode, **kwargs):
            name = record.get('shortDesc') or ''
            if name in fail_names:
                raise RuntimeError('simulated failure')
            return Path(out_root) / 'group' / ('%s.pdf' % name)
        return fake

    def test_parallel_export_collects_groups_and_reports_progress(self):
        records = [{'shortDesc': 'A'}, {'shortDesc': 'B'},
                   {'shortDesc': 'C'}]
        events = []
        with patch('banshu_export.export_class',
                   side_effect=self._fake_export()):
            result = export_records(
                object(), records, Path('out'), 'time', max_workers=3,
                on_event=events.append)
        self.assertEqual(result['total'], 3)
        self.assertEqual(result['failed'], [])
        self.assertEqual(sum(len(paths)
                             for paths in result['groups'].values()), 3)
        self.assertEqual(events[-1]['percent'], 100.0)
        self.assertEqual(events[-1]['done'], 3)
        phases = {event['phase'] for event in events}
        self.assertIn('start', phases)
        self.assertIn('done', phases)

    def test_one_bad_class_does_not_abort_the_batch(self):
        records = [{'shortDesc': 'good'}, {'shortDesc': 'bad'},
                   {'shortDesc': 'also-good'}]
        with patch('banshu_export.export_class',
                   side_effect=self._fake_export({'bad'})), \
                patch('banshu_export.log', lambda *args, **kwargs: None):
            result = export_records(object(), records, Path('out'), 'time',
                                    max_workers=2)
        self.assertEqual(result['failed'], ['bad'])
        self.assertEqual(sum(len(paths)
                             for paths in result['groups'].values()), 2)

    def test_cancel_event_stops_before_any_export(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(BatchCancelled):
            export_records(object(), [{'shortDesc': 'A'}], Path('out'),
                           'time', cancel_event=cancel)

    def test_cancel_during_gen_bs_poll(self):
        class FakeSession:
            def post(self, path, payload, **kwargs):
                if path.endswith('getBsSign'):
                    return {'code': 0, 'obj': {'signature': 'sig'}}
                raise AssertionError('取消后不应再请求 genBs')

        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(BatchCancelled):
            bs_manifest(FakeSession(), 'file-1', cancel_event=cancel)


if __name__ == '__main__':
    unittest.main()
