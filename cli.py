"""命令行入口：python -m optimized 或 python run_cli.py"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import APP_NAME, APP_VERSION, DEFAULT_RATE, DEFAULT_WORKERS, open_path
from pipeline import (Cancelled, ExportOptions, NoClassesError, PipelineError,
                      run_export)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='aiwenyun-export',
        description='爱问云邀请码导出 2.0（免重启、并发、只读登录态）')
    parser.add_argument('--out', default='', help='输出目录，默认桌面\\爱问云邀请码')
    parser.add_argument('--past-days', type=int, default=0,
                        help='从几天前开始列课堂（0=从现在）')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS,
                        help='并发线程数，默认 %d' % DEFAULT_WORKERS)
    parser.add_argument('--rate', type=float, default=DEFAULT_RATE,
                        help='每秒请求上限，默认 %.1f' % DEFAULT_RATE)
    parser.add_argument('--base-url', default='',
                        help='手动指定 API 主机（默认自动发现）')
    parser.add_argument('--no-open', action='store_true', help='导出后不自动打开 Excel')
    parser.add_argument('--csv', action='store_true', help='同时输出 CSV')
    parser.add_argument('--json', action='store_true', help='同时输出 JSON')
    parser.add_argument('--no-details', action='store_true', help='不生成明细工作表')
    parser.add_argument('--allow-restart', action='store_true',
                        help='macOS：允许重启爱问云并开启本机 9222 调试端口')
    parser.add_argument('--version', action='version',
                        version='%s %s' % (APP_NAME, APP_VERSION))
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    options = ExportOptions(
        out_dir=Path(args.out) if args.out else None,
        past_days=args.past_days,
        workers=args.workers,
        rate=args.rate,
        include_details=not args.no_details,
        open_file=not args.no_open,
        write_csv=args.csv,
        write_json=args.json,
        base_url=args.base_url or None,
        allow_restart=args.allow_restart,
    )

    def on_event(kind: str, payload) -> None:
        if kind == 'phase':
            print('\n[阶段] %s' % payload, flush=True)
        elif kind == 'log':
            print('       %s' % payload, flush=True)
        elif kind == 'progress':
            done = payload.get('done', 0)
            total = payload.get('total', 0)
            record = payload.get('record') or {}
            mark = record.get('code') or '失败'
            print('\r[%d/%d] %-8s %s' % (done, total, mark, record.get('topic', '')[:40]),
                  end='', flush=True)

    try:
        summary = run_export(options, on_event=on_event)
    except KeyboardInterrupt:
        print('\n已中断。', file=sys.stderr)
        return 130
    except NoClassesError as error:
        print('\n没有课堂：%s' % error, file=sys.stderr)
        return 3
    except Cancelled as error:
        print('\n已取消：%s' % error, file=sys.stderr)
        return 130
    except PipelineError as error:
        print('\n导出失败：%s' % error, file=sys.stderr)
        return 4
    except Exception as error:
        print('\n导出失败：%s: %s' % (type(error).__name__, error), file=sys.stderr)
        return 4

    print('\n')
    print('完成：%s' % summary.path)
    print('成功 %d / %d，用时 %.2fs' % (summary.success, summary.total, summary.elapsed))
    print('登录态：%s  版本 %s' % (summary.base_url, summary.client_version))
    if summary.conflicts:
        print('注意：有 %d 个课次出现多个不同邀请码，已保留第一个。' % len(summary.conflicts))
    if summary.csv_path:
        print('CSV：%s' % summary.csv_path)
    if summary.json_path:
        print('JSON：%s' % summary.json_path)
    if options.open_file:
        try:
            open_path(summary.path)
        except OSError as error:
            print('未能自动打开 Excel：%s' % error)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
