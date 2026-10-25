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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from analyze import (enrich_row, format_time, live_info_map, normalize_groups)
from config import (DEFAULT_RATE, DEFAULT_SCAN_TIMEOUT, DEFAULT_TIMEOUT,
                    DEFAULT_WORKERS, discover_base_urls, normalize_base_url,
                    output_folder)
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
               cancel_event: threading.Event) -> dict[str, Any]:
    code = ''
    note = ''
    attempts = 4
    for attempt in range(attempts):
        if cancel_event.is_set():
            note = '已取消'
            break
        session, generation = _safe_current(state)
        if session is None:
            note = _safe_auth_message(state)
            break
        try:
            bucket.acquire(cancel_event)
            code = session.fetch_invite_code(row['id'])
            note = ''
            break
        except TokenExpired:
            try:
                state.refresh(generation)
                note = '登录态已刷新，重试中'
                continue
            except AuthError as error:
                note = redact(error)
                break
        except RateLimitError as error:
            note = redact(error)
            _sleep_backoff(attempt, error.retry_after, cancel_event)
            continue
        except NetworkError as error:
            note = redact(error)
            _sleep_backoff(attempt, 0.0, cancel_event)
            continue
        except ApiError as error:
            note = redact(error)
            if attempt < 1:
                _sleep_backoff(attempt, 0.0, cancel_event)
                continue
            break
        except Exception as error:  # 防御性兜底，避免单条异常终止整个导出
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
                  cancel_event: threading.Event | None = None
                  ) -> tuple[list[dict[str, Any]], bool]:
    cancel_event = cancel_event or threading.Event()
    workers = max(1, min(int(options.workers or DEFAULT_WORKERS), 16))
    bucket = TokenBucket(rate=max(1.0, options.rate), burst=workers)
    results: list[dict[str, Any] | None] = [None] * len(rows)
    canceled = False
    executor = ThreadPoolExecutor(max_workers=workers,
                                  thread_name_prefix='invite')
    future_map = {
        executor.submit(_fetch_one, state, row, bucket, cancel_event): index
        for index, row in enumerate(rows)
    }
    try:
        for future in as_completed(future_map):
            index = future_map[future]
            try:
                record = future.result()
            except Exception as error:
                record = _fallback_record(rows[index], redact(error))
            results[index] = record
            if on_progress is not None:
                done = sum(1 for item in results if item is not None)
                on_progress(done, len(rows), record)
            if cancel_event.is_set() and not canceled:
                canceled = True
                for pending in future_map:
                    pending.cancel()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    if cancel_event.is_set():
        canceled = True
    for index, record in enumerate(results):
        if record is None:
            results[index] = _fallback_record(rows[index], '已取消')
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
    }


def run_export(options: ExportOptions | None = None,
               on_event: Callable[[str, Any], None] | None = None,
               cancel_event: threading.Event | None = None) -> ExportSummary:
    options = options or ExportOptions()
    cancel_event = cancel_event or threading.Event()
    started = time.monotonic()

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
        batch = acquire_tokens(
            scan_timeout=options.scan_timeout,
            allow_restart=options.allow_restart,
            on_event=on_event,
            cancel_event=cancel_event,
        )
    except TokenScanError as error:
        raise PipelineError(redact(error)) from error

    info = batch.info or {}
    if info.get('method') == 'memory':
        _emit(on_event, 'log',
              '扫描 %s 个客户端进程 / %s MB 内存，找到 %d 个候选登录态，用时 %ss' % (
                  info.get('processes', '?'), info.get('mb', '?'),
                  len(batch.candidates), info.get('elapsed', '?')))
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
    try:
        state = TokenState(
            acquire_tokens, base_urls, batch,
            timeout=options.timeout,
            scan_timeout=options.scan_timeout,
            allow_restart=options.allow_restart,
            on_event=on_event,
            cancel_event=cancel_event,
        )
        meta = state.metadata
        _emit(on_event, 'log', '登录态有效：%s（用户 %s，版本 %s，签发时间 %s）' % (
            state.session.base_url,
            meta.get('user_id') or '未知',
            meta.get('version') or '未知',
            format_time(meta.get('issued_at')) or '未知'))
    except AuthError as error:
        if state is not None:
            state.close()
        raise PipelineError(redact(error)) from error

    try:
        check_cancel()
        _emit(on_event, 'phase', '正在读取课堂列表…')
        start_ms = int(time.time() * 1000) - max(0, options.past_days) * DAY_MS
        rows = _call_with_refresh(
            state, lambda session: session.fetch_timetable(start_ms))
        if not rows:
            raise NoClassesError('没有找到实时课堂（这个账号可能还没有排课）。')
        _emit(on_event, 'log', '共 %d 个实时课堂' % len(rows))

        check_cancel()
        _emit(on_event, 'phase', '正在补充课堂详情…')
        try:
            details = _call_with_refresh(
                state, lambda session: session.fetch_live_info(
                    [row['id'] for row in rows]))
            details_map = details if isinstance(details, dict) else live_info_map(details)
            rows = [enrich_row(row, details_map.get(row['id'])) for row in rows]
            _emit(on_event, 'log', '已补充 %d 个课堂详情' % len(details_map))
        except (ApiError, NetworkError) as error:
            _emit(on_event, 'log', '课堂详情获取失败，使用课程表原始字段：%s' % redact(error))

        _emit(on_event, 'phase', '正在取邀请码…')
        results, canceled = fetch_invites(
            state, rows, options,
            on_progress=lambda done, total, record: _emit(
                on_event, 'progress', {'done': done, 'total': total, 'record': record}),
            cancel_event=cancel_event)
        success = sum(1 for item in results if item.get('code'))
        _emit(on_event, 'log', '邀请码完成：成功 %d / %d' % (success, len(results)))

        if not canceled:
            check_cancel()
        _emit(on_event, 'phase', '正在生成 Excel…')
        items = normalize_items(results)
        matrices = build_matrices(items)
        out_dir = Path(options.out_dir) if options.out_dir else output_folder()
        filename = '邀请码-%s.xlsx' % datetime.now().strftime('%Y%m%d-%H%M%S')
        output_path = out_dir / filename
        details_rows = sorted(
            results,
            key=lambda item: (item.get('course') or '', item.get('lesson') or 0))
        meta = state.metadata
        path = write_workbook(
            output_path, matrices,
            details=details_rows if options.include_details else None,
            metadata={
                'exported_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'client_version': meta.get('version') or '',
                'base_url': state.session.base_url,
            })

        csv_path = json_path = None
        if options.write_csv:
            csv_path = _write_csv(path.with_suffix('.csv'), results)
        if options.write_json:
            json_path = _write_json(path.with_suffix('.json'), results)

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
            rows=results,
            csv_path=csv_path,
            json_path=json_path,
        )
        _emit(on_event, 'done', summary)
        return summary
    finally:
        if state is not None:
            state.close()


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
