"""真实环境压力测试（手动运行，不属于单测）。

用法：
    python tests/stress_live.py
    python tests/stress_live.py --rounds 2

会读取本机爱问云登录态，拉取实时课堂列表，然后按不同并发 / 速率
重复请求 ``gt/servlet/liveclass/invite``，统计延迟、错误和稳定性。
只打印课堂 ID 与耗时，不输出 token。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import discover_base_urls  # noqa: E402
from net import (ApiError, ApiSession, AuthError, NetworkError,  # noqa: E402
                 RateLimitError, TokenBucket, TokenExpired, authenticate)
from token_source import acquire_tokens  # noqa: E402


def ms(value: float) -> str:
    return '%.0f' % (value * 1000)


def run_round(session: ApiSession, ids: list[int], workers: int,
              rate: float) -> dict:
    bucket = TokenBucket(rate=rate, burst=workers) if rate > 0 else None
    started = time.perf_counter()
    latencies: list[float] = []
    errors: Counter = Counter()
    slow = 0

    def one(class_id: int):
        if bucket is not None:
            bucket.acquire()
        begin = time.perf_counter()
        try:
            code = session.fetch_invite_code(class_id)
            elapsed = time.perf_counter() - begin
            return elapsed, bool(code), ''
        except Exception as error:  # noqa: BLE001
            return time.perf_counter() - begin, False, type(error).__name__

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, class_id) for class_id in ids]
        for future in as_completed(futures):
            elapsed, ok, kind = future.result()
            latencies.append(elapsed)
            if elapsed > 1.0:
                slow += 1
            if not ok:
                errors[kind] += 1

    total = time.perf_counter() - started
    ordered = sorted(latencies)
    return {
        'workers': workers,
        'rate': rate,
        'total': total,
        'ok': len(ids) - sum(errors.values()),
        'errors': errors,
        'p50': statistics.median(ordered),
        'p95': ordered[max(0, int(len(ordered) * 0.95) - 1)],
        'max': ordered[-1],
        'slow': slow,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=2)
    args = parser.parse_args()

    print('读取登录态…', flush=True)
    batch = acquire_tokens(scan_timeout=15)
    base_urls = ([batch.base_url] if batch.base_url else []) + discover_base_urls()
    session, candidate = authenticate(base_urls, batch.candidates, timeout=20)
    print('登录态 OK：%s' % session.base_url, flush=True)

    rows = session.fetch_timetable()
    ids = [int(row['id']) for row in rows]
    print('课堂数：%d' % len(ids), flush=True)
    if not ids:
        print('没有课堂，退出')
        return 1

    print('预热…', flush=True)
    try:
        session.fetch_invite_code(ids[0])
    except Exception as error:  # noqa: BLE001
        print('预热失败：%s' % type(error).__name__)

    configs = [(1, 2.0), (8, 12.0), (16, 24.0), (24, 48.0), (32, 0.0)]
    results = []
    for workers, rate in configs:
        for round_index in range(args.rounds):
            label = '并发 %2d / 限速 %s' % (
                workers, ('%.0f/s' % rate) if rate else '关')
            print('%-22s 第 %d 轮…' % (label, round_index + 1), end='', flush=True)
            result = run_round(session, ids, workers, rate)
            results.append(result)
            print(
                ' %5.2fs  成功 %2d/%2d  p50 %sms  p95 %sms  max %sms  >1s %d  %s' % (
                    result['total'], result['ok'], len(ids),
                    ms(result['p50']), ms(result['p95']), ms(result['max']),
                    result['slow'],
                    dict(result['errors']) or ''),
                flush=True)
            if result['errors'].get('TokenExpired'):
                print('  token 过期，停止（真实导出会自动刷新）')
                return 2

    print('\n汇总：')
    for result in results:
        print('  并发 %2d 限速 %5s  总耗时 %5.2fs  p95 %sms  错误 %s' % (
            result['workers'],
            ('%.0f/s' % result['rate']) if result['rate'] else '关',
            result['total'], ms(result['p95']), dict(result['errors']) or {}))

    worst = max(results, key=lambda item: item['p95'])
    print('\np95 最差：并发 %d / 限速 %s，p95 %sms' % (
        worst['workers'],
        ('%.0f/s' % worst['rate']) if worst['rate'] else '关',
        ms(worst['p95'])))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
