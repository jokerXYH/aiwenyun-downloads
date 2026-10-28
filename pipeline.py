"""导出流水线：读取登录态 → 课程表 → 邀请码 → Excel。

关键优化
========
1. Windows 免重启：只读扫描客户端内存中的 token。
   macOS 因系统权限限制，重启客户端并只用本机 9222 调试端口读取。
2. 课程表一次取 100 条，33 个课堂只需 1 次请求。
3. 邀请码并发抓取（默认 8 线程 + 12 req/s 令牌桶），比串行快 3-5 倍。
4. token 过期时自动重新获取/验证。
5. 单个课堂失败不影响整体；已完成的结果照常导出。
6. 输出采用临时文件 + 原子替换，Excel 占用文件时自动改名。
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from analyze import (enrich_row, format_time, live_info_map, normalize_groups)
from config import (APP_NAME, APP_VERSION, DEFAULT_CLASS_DEADLINE,
                    DEFAULT_RATE, DEFAULT_RETRY_BUDGET, DEFAULT_SCAN_TIMEOUT,
                    DEFAULT_STALL_TIMEOUT, DEFAULT_TIMEOUT, DEFAULT_WORKERS,
                    discover_base_urls, normalize_base_url, output_folder)
from excel import write_workbook
from matrix import build_matrices, course_from_topic, lesson_from_topic, normalize_items
from net import (ApiError, ApiSession, AuthError, NetworkError, RateLimitError,
                 TokenBucket, TokenExpired, authenticate, redact)
from token_source import (TokenBatch, TokenScanError, acquire_tokens,
                          client_running, parse_token_metadata, platform_name)

DAY_MS = 86400000


class PipelineError(RuntimeError):
    pass


class NoClientError(PipelineError):
    pass


class NoClassesError(PipelineError):
    pass


class Cancelled(PipelineError):
    pass


@dataclass
class ExportOptions:
    out_dir: Path | None = None
    past_days: int = 0
    workers: int = DEFAULT_WORKERS
    rate: float = DEFAULT_RATE
    include_details: bool = True
    open_file: bool = True
    write_csv: bool = False
    write_json: bool = False
    base_url: str | None = None
    scan_timeout: float = DEFAULT_SCAN_TIMEOUT
    timeout: float = DEFAULT_TIMEOUT
    allow_restart: bool = False
    class_deadline: float = DEFAULT_CLASS_DEADLINE
    stall_timeout: float = DEFAULT_STALL_TIMEOUT
    retry_budget: float = DEFAULT_RETRY_BUDGET
    diagnostics: bool = True


@dataclass
class ExportSummary:
    path: Path
    total: int
    success: int
    failed: int
    elapsed: float
    base_url: str
    client_version: str
    token_issued_at: int
    canceled: bool = False
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    csv_path: Path | None = None
    json_path: Path | None = None
    diagnostics_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'path': str(self.path),
            'total': self.total,
            'success': self.success,
            'failed': self.failed,
            'elapsed': round(self.elapsed, 2),
            'base_url': self.base_url,
            'client_version': self.client_version,
            'token_issued_at': self.token_issued_at,
            'canceled': self.canceled,
            'conflicts': self.conflicts,
            'csv': str(self.csv_path) if self.csv_path else None,
            'json': str(self.json_path) if self.json_path else None,
            'diagnostics': str(self.diagnostics_path) if self.diagnostics_path else None,
        }


class TokenState:
    """线程安全的 token/session 容器；token 过期时自动刷新。"""

    def __init__(self, provider: Callable[..., TokenBatch],
                 base_urls: list[str], batch: TokenBatch,
                 timeout: float = DEFAULT_TIMEOUT,
                 scan_timeout: float = DEFAULT_SCAN_TIMEOUT,
                 allow_restart: bool = False,
                 on_event: Callable[[str, Any], None] | None = None,
                 cancel_event: threading.Event | None = None) -> None:
        self.provider = provider
        self.base_urls = base_urls
        self.timeout = timeout
        self.scan_timeout = scan_timeout
        self.allow_restart = allow_restart
        self.on_event = on_event
        self.cancel_event = cancel_event
        self.batch = batch
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._sessions: list[ApiSession] = []
        self._generation = 0
        self._auth_failed = False
        self._last_error = ''
        self.session, self.candidate = authenticate(
            base_urls, batch.candidates, timeout=timeout)
        self._sessions.append(self.session)
        self.metadata = parse_token_metadata(self.candidate.value)

    def current(self) -> tuple[ApiSession, int]:
        with self._lock:
            if self._auth_failed:
                raise AuthError(self._last_error or '登录态已失效')
            return self.session, self._generation

    def refresh(self, seen_generation: int) -> tuple[ApiSession, int]:
        with self._refresh_lock:
            with self._lock:
                if self._generation != seen_generation:
                    return self.session, self._generation
                if self._auth_failed:
                    raise AuthError(self._last_error or '登录态已失效')

            try:
                session = None
                candidate = None
                last_error: Exception | None = None
                for attempt in range(2):
                    try:
                        batch = self.provider(
                            scan_timeout=self.scan_timeout,
                            allow_restart=self.allow_restart,
                            on_event=self.on_event,
                            cancel_event=self.cancel_event,
                            full=(attempt > 0),
                        )
                        session, candidate = authenticate(
                            self.base_urls, batch.candidates, timeout=self.timeout)
                        self.batch = batch
                        break
                    except (TokenScanError, AuthError) as error:
                        last_error = error
                        if attempt == 0:
                            time.sleep(1.5)
                if session is None or candidate is None:
                    raise last_error or AuthError('登录态刷新失败')
            except (TokenScanError, AuthError) as error:
                with self._lock:
                    self._auth_failed = True
                    self._last_error = '登录态刷新失败：%s' % redact(error)
                raise AuthError(self._last_error) from error

            with self._lock:
                self.session = session
                self.candidate = candidate
                self._sessions.append(session)
                self._generation += 1
                self.metadata = parse_token_metadata(candidate.value)
                return session, self._generation

    def close(self) -> None:
        with self._lock:
            self._auth_failed = True
            self._last_error = '会话已关闭'
            sessions = list(self._sessions)
            self._sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception:
                pass


def _emit(callback: Callable[[str, Any], None] | None,
          kind: str, payload: Any = None) -> None:
    if callback is not None:
        callback(kind, payload)


class Cooldown:
    """全局冷却闸门：某个请求超时/被限流时，让所有线程一起缓一缓再重试。"""

    def __init__(self) -> None:
        self._until = 0.0
        self._lock = threading.Lock()

    def pause(self, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            self._until = max(self._until, time.monotonic() + seconds)

    def wait(self, cancel_event: threading.Event | None = None) -> None:
        while True:
            with self._lock:
                remaining = self._until - time.monotonic()
            if remaining <= 0:
                return
            if cancel_event is not None:
                if cancel_event.wait(min(remaining, 0.5)):
                    return
            else:
                time.sleep(min(remaining, 0.5))


def _call_with_refresh(state: TokenState, function: Callable[[ApiSession], Any]):
    last_error: Exception | None = None
    for _ in range(2):
        session, generation = state.current()
        try:
            return function(session)
        except TokenExpired as error:
            last_error = error
            state.refresh(generation)
    raise ApiError('登录态失效：%s' % redact(last_error))


def _fetch_one(state: TokenState, row: dict[str, Any], bucket: TokenBucket,
               cancel_event: threading.Event,
               cooldown: Cooldown | None = None,
               class_deadline: float | None = None,
               max_attempts: int = 3) -> dict[str, Any]:
    """抓一个课堂的邀请码。

    三条硬约束，专门对付「爬到一半卡住」：
    1. 每个课堂有总截止时间（默认 22 秒），到点立刻放弃，不无限重试；
    2. 单次请求 socket 超时 6 秒 + 线程看门狗，半死连接最多卡 6 秒出头；
    3. 网络类失败会让全局冷却闸门暂停一下，避免 8 个线程一起重试把链路打爆。
    """

    started = time.monotonic()
    deadline = started + (class_deadline or DEFAULT_CLASS_DEADLINE)
    code = ''
    note = ''
    attempts = 0
    errors: list[str] = []

    while attempts < max_attempts:
        if cancel_event.is_set():
            note = '已取消'
            break
        if time.monotonic() >= deadline:
            note = note or '请求超时'
            break
        session, generation = _safe_current(state)
        if session is None:
            note = _safe_auth_message(state)
            break
        if cooldown is not None:
            cooldown.wait(cancel_event)
            if cancel_event.is_set():
                note = '已取消'
                break
        try:
            bucket.acquire(cancel_event)
        except ApiError:
            note = '已取消'
            break

        attempts += 1
        try:
            code = session.fetch_invite_code(row['id'], deadline=deadline)
            note = ''
            break
        except TokenExpired:
            errors.append('token')
            note = '登录态过期'
            try:
                state.refresh(generation)
                note = '登录态已刷新，重试中'
                continue
            except AuthError as error:
                note = redact(error)
                break
        except RateLimitError as error:
            errors.append('rate')
            note = redact(error)
            pause = max(float(error.retry_after or 0), 1.0)
            if cooldown is not None:
                cooldown.pause(pause)
            else:
                _sleep_backoff(attempts - 1, error.retry_after, cancel_event)
            continue
        except NetworkError as error:
            errors.append('network')
            note = redact(error)
            if cooldown is not None:
                cooldown.pause(0.7 + random.uniform(0, 0.6))
            _sleep_backoff(attempts - 1, 0.0, cancel_event)
            continue
        except ApiError as error:
            errors.append('api')
            note = redact(error)
            if attempts >= 2:
                break
            _sleep_backoff(attempts - 1, 0.0, cancel_event)
            continue
        except Exception as error:  # 防御性兜底，避免单条异常终止整个导出
            errors.append('unexpected')
            note = redact(error)
            break

    topic = str(row.get('topic') or '').strip()
    return {
        'date': format_time(row.get('begin')),
        'topic': topic,
        'course': course_from_topic(topic),
        'lesson': lesson_from_topic(topic),
        'classId': row['id'],
        'code': code,
        'note': note,
        'groups': normalize_groups(row.get('groups')),
        'teacher': row.get('teacher') or '',
        'orgId': row.get('orgId'),
        'begin': row.get('begin') or 0,
        '_diag': {
            'attempts': attempts,
            'elapsed': round(time.monotonic() - started, 3),
            'errors': errors,
        },
    }


def _safe_current(state: TokenState) -> tuple[ApiSession | None, int]:
    try:
        return state.current()
    except AuthError:
        return None, 0


def _safe_auth_message(state: TokenState) -> str:
    try:
        state.current()
    except AuthError as error:
        return redact(error)
    return '登录态不可用'


def _sleep_backoff(attempt: int, retry_after: float,
                   cancel_event: threading.Event) -> None:
    delay = max(retry_after, 0.4 * (2 ** attempt))
    delay += random.uniform(0, 0.25)
    if cancel_event.wait(delay):
        return


def fetch_invites(state: TokenState, rows: list[dict[str, Any]],
                  options: ExportOptions,
                  on_progress: Callable[[int, int, dict[str, Any]], None] | None = None,
                  cancel_event: threading.Event | None = None,
                  fetch_one: Callable[..., dict[str, Any]] = _fetch_one,
                  on_stats: Callable[[dict[str, Any]], None] | None = None,
                  ) -> tuple[list[dict[str, Any]], bool]:
    """并发抓取 + 卡住看门狗 + 失败课堂第二轮串行重试。"""

    cancel_event = cancel_event or threading.Event()
    workers = max(1, min(int(options.workers or DEFAULT_WORKERS), 16))
    bucket = TokenBucket(rate=max(1.0, options.rate), burst=workers)
    cooldown = Cooldown()
    results: list[dict[str, Any] | None] = [None] * len(rows)
    canceled = False
    stalled = False
    first_started = time.monotonic()

    executor = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix='invite')
    futures = {
        executor.submit(fetch_one, state, row, bucket, cancel_event,
                        cooldown=cooldown,
                        class_deadline=options.class_deadline): index
        for index, row in enumerate(rows)
    }
    pending = set(futures)
    last_progress = first_started
    try:
        while pending:
            done, pending = wait(pending, timeout=0.5,
                                 return_when=FIRST_COMPLETED)
            for future in done:
                index = futures[future]
                try:
                    record = future.result()
                except Exception as error:
                    record = _fallback_record(rows[index], redact(error))
                results[index] = record
                last_progress = time.monotonic()
                if on_progress is not None:
                    done_count = sum(1 for item in results if item is not None)
                    on_progress(done_count, len(rows), record)

            if cancel_event.is_set():
                canceled = True
                for future in pending:
                    future.cancel()
                break

            # 看门狗：超过 stall_timeout 一个课堂都没完成，判定为链路卡死
            if pending and time.monotonic() - last_progress > options.stall_timeout:
                stalled = True
                for future in pending:
                    future.cancel()
                break
    finally:
        executor.shutdown(wait=not stalled, cancel_futures=True)

    for index, record in enumerate(results):
        if record is None:
            note = '已取消' if cancel_event.is_set() else (
                '等待超时' if stalled else '未完成')
            results[index] = _fallback_record(rows[index], note)

    first_elapsed = time.monotonic() - first_started
    first_success = sum(1 for item in results if item and item.get('code'))
    first_failed = len(results) - first_success

    # 第二轮：只重试第一轮失败的课堂，串行 + 冷却，避免再次并发雪崩
    retry_attempted = 0
    retry_recovered = 0
    retry_started = time.monotonic()
    failed_count = sum(1 for item in results if item and not item.get('code'))
    # 卡住时如果只剩个别课堂，仍值得串行再试一次；大面积失败就不硬撑了
    may_retry = ((not stalled) or failed_count <= 3) and options.retry_budget > 0
    if may_retry and not cancel_event.is_set():
        retry_indices = [
            index for index, item in enumerate(results)
            if item is not None and not item.get('code')
            and '已取消' not in (item.get('note') or '')
            and '登录态刷新失败' not in (item.get('note') or '')
            and '登录态失效' not in (item.get('note') or '')
        ]
        for index in retry_indices:
            if time.monotonic() - retry_started >= options.retry_budget:
                break
            if cancel_event.is_set():
                canceled = True
                break
            retry_attempted += 1
            record = fetch_one(
                state, rows[index], bucket, cancel_event,
                cooldown=cooldown,
                class_deadline=min(12.0, options.class_deadline),
                max_attempts=2,
            )
            results[index] = record
            if record.get('code'):
                retry_recovered += 1
            if on_progress is not None:
                done_count = sum(1 for item in results if item is not None)
                on_progress(done_count, len(rows), record)
            cancel_event.wait(0.6)

    if cancel_event.is_set():
        canceled = True

    latencies = sorted(
        item['_diag']['elapsed'] for item in results
        if item and item.get('_diag') and item['_diag'].get('elapsed')
    )

    def percentile(ratio: float) -> float:
        if not latencies:
            return 0.0
        index = min(len(latencies) - 1,
                    max(0, int(round(len(latencies) * ratio)) - 1))
        return latencies[index]

    attempts = [
        item['_diag']['attempts'] for item in results
        if item and item.get('_diag')
    ]
    stats = {
        'total': len(results),
        'stalled': stalled,
        'firstPass': {
            'elapsed': round(first_elapsed, 2),
            'success': first_success,
            'failed': first_failed,
        },
        'retry': {
            'attempted': retry_attempted,
            'recovered': retry_recovered,
            'elapsed': round(time.monotonic() - retry_started, 2),
        },
        'latency': {
            'p50': round(percentile(0.50), 3),
            'p95': round(percentile(0.95), 3),
            'max': round(latencies[-1], 3) if latencies else 0.0,
        },
        'attempts': {
            'avg': round(sum(attempts) / len(attempts), 2) if attempts else 0,
            'max': max(attempts) if attempts else 0,
        },
        'failures': [
            {
                'classId': item.get('classId'),
                'topic': item.get('topic'),
                'note': item.get('note'),
                'attempts': (item.get('_diag') or {}).get('attempts', 0),
                'elapsed': (item.get('_diag') or {}).get('elapsed', 0),
            }
            for item in results if item and not item.get('code')
        ],
    }
    if on_stats is not None:
        on_stats(stats)

    return [item for item in results if item is not None], canceled


def _fallback_record(row: dict[str, Any], note: str) -> dict[str, Any]:
    topic = str(row.get('topic') or '').strip()
    return {
        'date': format_time(row.get('begin')),
        'topic': topic,
        'course': course_from_topic(topic),
        'lesson': lesson_from_topic(topic),
        'classId': row.get('id'),
        'code': '',
        'note': note,
        'groups': normalize_groups(row.get('groups')),
        'teacher': row.get('teacher') or '',
        'orgId': row.get('orgId'),
        'begin': row.get('begin') or 0,
        '_diag': {'attempts': 0, 'elapsed': 0.0, 'errors': []},
    }


def _restore_macos_client(scan_info: dict[str, Any],
                          on_event: Callable[[str, Any], None] | None) -> None:
    """导出结束后把 macOS 客户端从调试实例恢复成正常窗口。"""

    if sys.platform != 'darwin':
        return
    if not scan_info.get('restarted') or not scan_info.get('app'):
        return
    try:
        from token_macos import restore_app

        restore_app(Path(scan_info['app']))
        _emit(on_event, 'log', '已恢复正常客户端窗口')
    except Exception as error:  # noqa: BLE001
        _emit(on_event, 'log', '恢复客户端窗口失败：%s' % error)


def run_export(options: ExportOptions | None = None,
               on_event: Callable[[str, Any], None] | None = None,
               cancel_event: threading.Event | None = None) -> ExportSummary:
    options = options or ExportOptions()
    cancel_event = cancel_event or threading.Event()
    started = time.monotonic()
    timings: dict[str, float] = {}

    def mark(name: str, since: float) -> None:
        timings[name] = round(time.monotonic() - since, 2)

    def check_cancel() -> None:
        if cancel_event.is_set():
            raise Cancelled('用户已取消')

    check_cancel()
    if platform_name() == 'darwin':
        if not client_running() and not options.allow_restart:
            raise NoClientError(
                'macOS 版需要重启爱问云并开启本机调试端口；请确认重启后重试。')
        _emit(on_event, 'phase', '正在读取登录态（macOS：重启客户端 + 本机调试端口）…')
    else:
        if not client_running():
            raise NoClientError('没有找到运行中的爱问云客户端（ai-yxt.exe）。')
        _emit(on_event, 'phase', '正在读取登录态（免重启，只读内存）…')

    try:
        scan_started = time.monotonic()
        batch = acquire_tokens(
            scan_timeout=options.scan_timeout,
            allow_restart=options.allow_restart,
            on_event=on_event,
            cancel_event=cancel_event,
        )
        mark('scan', scan_started)
    except TokenScanError as error:
        raise PipelineError(redact(error)) from error

    info = batch.info or {}
    scan_info = dict(info)
    scan_info['candidates'] = len(batch.candidates)
    if info.get('method') == 'memory':
        suffix = '（快速扫描）' if info.get('partial') else ''
        _emit(on_event, 'log',
              '扫描 %s 个客户端进程 / %s MB 内存，找到 %d 个候选登录态，用时 %ss%s' % (
                  info.get('processes', '?'), info.get('mb', '?'),
                  len(batch.candidates), info.get('elapsed', '?'), suffix))
    else:
        _emit(on_event, 'log',
              '已通过 CDP 读取登录态（客户端：%s，重启：%s）' % (
                  info.get('app', '爱问云'),
                  '是' if info.get('restarted') else '否'))

    if options.base_url:
        base_url = normalize_base_url(options.base_url)
        if not base_url:
            raise PipelineError('无效的 --base-url，必须是 HTTPS 地址')
        base_urls = [base_url]
    else:
        base_urls = []
        if batch.base_url:
            base_urls.append(batch.base_url)
        for candidate_url in discover_base_urls():
            if candidate_url not in base_urls:
                base_urls.append(candidate_url)
        if not base_urls:
            base_urls = ['https://www.aiwenyun.cn/']

    check_cancel()
    _emit(on_event, 'phase', '正在验证登录态…')
    state = None

    def build_state(current_batch):
        return TokenState(
            acquire_tokens, base_urls, current_batch,
            timeout=options.timeout,
            scan_timeout=options.scan_timeout,
            allow_restart=options.allow_restart,
            on_event=on_event,
            cancel_event=cancel_event,
        )

    try:
        auth_started = time.monotonic()
        try:
            state = build_state(batch)
        except AuthError:
            info_now = batch.info or {}
            if info_now.get('method') != 'memory' or info_now.get('full'):
                raise
            # 快速扫描只看了第一个有 token 的进程，候选失效时再做完整扫描
            _emit(on_event, 'log', '快速扫描到的登录态不可用，重新完整扫描…')
            batch = acquire_tokens(
                scan_timeout=options.scan_timeout,
                allow_restart=options.allow_restart,
                on_event=on_event,
                cancel_event=cancel_event,
                full=True,
            )
            scan_info.update(batch.info or {})
            scan_info['candidates'] = len(batch.candidates)
            _emit(on_event, 'log',
                  '完整扫描：%s 个进程 / %s MB，%s 个候选，用时 %ss' % (
                      scan_info.get('processes', '?'), scan_info.get('mb', '?'),
                      scan_info.get('candidates', '?'), scan_info.get('elapsed', '?')))
            state = build_state(batch)
        mark('auth', auth_started)
        meta = state.metadata
        _emit(on_event, 'log', '登录态有效：%s（用户 %s，版本 %s，签发时间 %s）' % (
            state.session.base_url,
            meta.get('user_id') or '未知',
            meta.get('version') or '未知',
            format_time(meta.get('issued_at')) or '未知'))
    except AuthError as error:
        if state is not None:
            state.close()
        _restore_macos_client(scan_info, on_event)
        raise PipelineError(redact(error)) from error
    except TokenScanError as error:
        if state is not None:
            state.close()
        _restore_macos_client(scan_info, on_event)
        raise PipelineError(redact(error)) from error
    except Exception:
        _restore_macos_client(scan_info, on_event)
        raise

    try:
        check_cancel()
        _emit(on_event, 'phase', '正在读取课堂列表…')
        timetable_started = time.monotonic()
        start_ms = int(time.time() * 1000) - max(0, options.past_days) * DAY_MS
        rows = _call_with_refresh(
            state, lambda session: session.fetch_timetable(start_ms))
        mark('timetable', timetable_started)
        if not rows:
            raise NoClassesError('没有找到实时课堂（这个账号可能还没有排课）。')
        _emit(on_event, 'log', '共 %d 个实时课堂' % len(rows))

        check_cancel()
        _emit(on_event, 'phase', '正在补充课堂详情…')
        details_started = time.monotonic()
        try:
            details = _call_with_refresh(
                state, lambda session: session.fetch_live_info(
                    [row['id'] for row in rows]))
            details_map = details if isinstance(details, dict) else live_info_map(details)
            rows = [enrich_row(row, details_map.get(row['id'])) for row in rows]
            _emit(on_event, 'log', '已补充 %d 个课堂详情' % len(details_map))
        except (ApiError, NetworkError) as error:
            _emit(on_event, 'log', '课堂详情获取失败，使用课程表原始字段：%s' % redact(error))
        mark('details', details_started)

        _emit(on_event, 'phase', '正在取邀请码…')
        invites_started = time.monotonic()
        invites_stats: dict[str, Any] = {}
        results, canceled = fetch_invites(
            state, rows, options,
            on_progress=lambda done, total, record: _emit(
                on_event, 'progress', {'done': done, 'total': total, 'record': record}),
            cancel_event=cancel_event,
            on_stats=lambda stats: invites_stats.update(stats))
        mark('invites', invites_started)
        if invites_stats.get('stalled'):
            _emit(on_event, 'log',
                  '邀请码阶段长时间没有响应，已触发卡住保护并提前收尾')
        recovered = (invites_stats.get('retry') or {}).get('recovered') or 0
        if recovered:
            _emit(on_event, 'log', '第二轮重试补回 %d 个邀请码' % recovered)
        success = sum(1 for item in results if item.get('code'))
        _emit(on_event, 'log', '邀请码完成：成功 %d / %d' % (success, len(results)))

        if not canceled:
            check_cancel()
        _emit(on_event, 'phase', '正在生成 Excel…')
        public_results = [
            {key: value for key, value in item.items() if key != '_diag'}
            for item in results
        ]
        items = normalize_items(public_results)
        matrices = build_matrices(items)
        out_dir = Path(options.out_dir) if options.out_dir else output_folder()
        filename = '邀请码-%s.xlsx' % datetime.now().strftime('%Y%m%d-%H%M%S')
        output_path = out_dir / filename
        details_rows = sorted(
            public_results,
            key=lambda item: (item.get('course') or '', item.get('lesson') or 0))
        meta = state.metadata
        excel_started = time.monotonic()
        path = write_workbook(
            output_path, matrices,
            details=details_rows if options.include_details else None,
            metadata={
                'exported_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'client_version': meta.get('version') or '',
                'base_url': state.session.base_url,
            })
        mark('excel', excel_started)

        csv_path = json_path = None
        if options.write_csv:
            csv_path = _write_csv(path.with_suffix('.csv'), public_results)
        if options.write_json:
            json_path = _write_json(path.with_suffix('.json'), public_results)

        diagnostics_path = None
        if options.diagnostics:
            try:
                diagnostics_path = _write_diagnostics(path, {
                    'app': APP_NAME,
                    'version': APP_VERSION,
                    'exportedAt': datetime.now().isoformat(timespec='seconds'),
                    'platform': sys.platform,
                    'baseUrl': state.session.base_url,
                    'clientVersion': meta.get('version') or '',
                    'tokenIssuedAt': int(meta.get('issued_at') or 0),
                    'timings': timings,
                    'scan': scan_info,
                    'invites': invites_stats,
                    'failures': [
                        {
                            'classId': item.get('classId'),
                            'topic': item.get('topic'),
                            'note': item.get('note'),
                        }
                        for item in public_results if not item.get('code')
                    ],
                })
                _emit(on_event, 'log', '诊断信息：%s' % diagnostics_path)
            except OSError as error:
                _emit(on_event, 'log', '诊断信息写入失败：%s' % error)

        summary = ExportSummary(
            path=path,
            total=len(results),
            success=success,
            failed=len(results) - success,
            elapsed=time.monotonic() - started,
            base_url=state.session.base_url,
            client_version=meta.get('version') or '',
            token_issued_at=int(meta.get('issued_at') or 0),
            canceled=canceled,
            conflicts=matrices[0].conflicts if matrices else [],
            rows=public_results,
            csv_path=csv_path,
            json_path=json_path,
            diagnostics_path=diagnostics_path,
        )
        _emit(on_event, 'done', summary)
        return summary
    finally:
        if state is not None:
            state.close()
        _restore_macos_client(scan_info, on_event)


def _write_csv(path: Path, results: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.%s.tmp%s' % (path.stem, path.suffix))
    with temp.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['上课时间', '课堂名称', '课堂ID', '课程', '课次',
                         '邀请码', '状态', '备注'])
        for item in results:
            writer.writerow([
                item.get('date'), item.get('topic'), item.get('classId'),
                item.get('course'), item.get('lesson'), item.get('code'),
                '成功' if item.get('code') else '失败', item.get('note'),
            ])
    os.replace(temp, path)
    return path


def _write_json(path: Path, results: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.%s.tmp%s' % (path.stem, path.suffix))
    temp.write_text(json.dumps({
        'exportedAt': datetime.now().isoformat(timespec='seconds'),
        'items': results,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)
    return path


def _write_diagnostics(path: Path, payload: dict[str, Any]) -> Path:
    """把本次导出的耗时/失败原因写到 Excel 旁边，方便排查别的机器卡住。"""

    target = path.with_name(path.stem + '-诊断.json')
    temp = target.with_name('.%s.tmp%s' % (target.stem, target.suffix))
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding='utf-8')
    os.replace(temp, target)
    return target
