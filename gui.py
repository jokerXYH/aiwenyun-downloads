"""Tkinter 图形界面：默认免重启，只读登录态。"""

from __future__ import annotations

import queue
import sys
import threading
from pathlib import Path
from tkinter import (BOTH, LEFT, RIGHT, X, Y, BooleanVar, IntVar, StringVar,
                     Tk, messagebox, ttk)
from tkinter import Text, font as tkfont

from config import APP_NAME, APP_VERSION, open_path
from pipeline import (Cancelled, ExportOptions, ExportSummary, NoClassesError,
                      PipelineError, run_export)
from token_source import debug_port_open

ACCENT = '#1a7a4c'
ACCENT_DARK = '#155f3a'
TEXT_DARK = '#1d2b23'
TEXT_MUTED = '#6e6e73'
BG = '#f4f6f9'
CARD = '#ffffff'
BORDER = '#e1e5ea'


class App:
    def __init__(self) -> None:
        self.root = Tk()
        self.root.title('%s %s' % (APP_NAME, APP_VERSION))
        self.root.geometry('720x620')
        self.root.minsize(660, 560)
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        try:
            base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
            icon = base / 'app.ico'
            if icon.is_file():
                self.root.iconbitmap(str(icon))
        except Exception:
            pass

        self.ui_queue: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.last_path: Path | None = None
        self.summary: ExportSummary | None = None

        self.busy = BooleanVar(value=False)
        self.status = StringVar(value='免重启模式：点「一键导出」，直接读取已登录客户端的会话。')
        self.past_days = IntVar(value=0)
        self.workers = IntVar(value=8)
        self.include_details = BooleanVar(value=True)
        self.open_after = BooleanVar(value=True)
        self.write_csv = BooleanVar(value=False)
        self.write_json = BooleanVar(value=False)

        self._build_ui()
        self.root.after(80, self._drain_queue)

    # ---------- UI ----------
    def _build_ui(self) -> None:
        base = tkfont.nametofont('TkDefaultFont')
        base.configure(family='Microsoft YaHei UI', size=10)
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except Exception:
            pass
        style.configure('Primary.TButton', font=('Microsoft YaHei UI', 12, 'bold'),
                        padding=(18, 12), background=ACCENT, foreground='#ffffff',
                        borderwidth=0)
        style.map('Primary.TButton',
                  background=[('active', ACCENT_DARK), ('disabled', '#9db8a9')],
                  foreground=[('disabled', '#f2f2f2')])
        style.configure('Secondary.TButton', font=('Microsoft YaHei UI', 9),
                        padding=(12, 6))
        style.configure('Card.TFrame', background=CARD)
        style.configure('Card.TLabel', background=CARD, foreground=TEXT_DARK)
        style.configure('Muted.TLabel', background=CARD, foreground=TEXT_MUTED)
        style.configure('Thin.Horizontal.TProgressbar', troughcolor='#e3e8ee',
                        background=ACCENT, borderwidth=0)

        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill=BOTH, expand=True)

        title = ttk.Label(outer, text='爱问云 · 邀请码导出',
                          font=('Microsoft YaHei UI', 18, 'bold'),
                          foreground=TEXT_DARK)
        title.pack(anchor='w')
        subtitle = ttk.Label(
            outer,
            text='免重启 · 只读登录态 · 并发抓取 · 自动排版 Excel',
            font=('Microsoft YaHei UI', 9), foreground=TEXT_MUTED)
        subtitle.pack(anchor='w', pady=(4, 14))

        self.export_button = ttk.Button(outer, text='一键导出 Excel',
                                        style='Primary.TButton',
                                        command=self.on_export)
        self.export_button.pack(fill=X)
        self.cancel_button = ttk.Button(outer, text='取消', style='Secondary.TButton',
                                        command=self.on_cancel, state='disabled')
        self.cancel_button.pack(anchor='e', pady=(6, 0))

        options = ttk.Frame(outer)
        options.pack(fill=X, pady=(12, 0))
        ttk.Label(options, text='从', foreground=TEXT_MUTED).pack(side=LEFT)
        ttk.Spinbox(options, from_=0, to=365, width=5,
                    textvariable=self.past_days).pack(side=LEFT, padx=(4, 2))
        ttk.Label(options, text='天前开始', foreground=TEXT_MUTED).pack(side=LEFT)
        ttk.Label(options, text='并发', foreground=TEXT_MUTED).pack(side=LEFT, padx=(16, 2))
        ttk.Spinbox(options, from_=1, to=16, width=4,
                    textvariable=self.workers).pack(side=LEFT)
        ttk.Checkbutton(options, text='生成明细',
                        variable=self.include_details).pack(side=LEFT, padx=(16, 0))
        ttk.Checkbutton(options, text='完成后打开',
                        variable=self.open_after).pack(side=LEFT, padx=(10, 0))
        ttk.Checkbutton(options, text='同时 CSV',
                        variable=self.write_csv).pack(side=LEFT, padx=(10, 0))
        ttk.Checkbutton(options, text='同时 JSON',
                        variable=self.write_json).pack(side=LEFT, padx=(10, 0))

        self.progress = ttk.Progressbar(outer, mode='determinate',
                                        style='Thin.Horizontal.TProgressbar',
                                        maximum=100, value=0)
        self.progress.pack(fill=X, pady=(12, 0))

        status_row = ttk.Frame(outer)
        status_row.pack(fill=X, pady=(8, 0))
        ttk.Label(status_row, textvariable=self.status,
                  font=('Microsoft YaHei UI', 9), foreground=ACCENT).pack(side=LEFT)

        log_card = ttk.Frame(outer, style='Card.TFrame', padding=1)
        log_card.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.log_box = Text(log_card, height=12, wrap='word', relief='flat',
                            bg=CARD, fg='#333333', highlightthickness=0,
                            font=('Consolas', 9), padx=10, pady=8,
                            state='disabled')
        scrollbar = ttk.Scrollbar(log_card, orient='vertical',
                                  command=self.log_box.yview)
        self.log_box.configure(yscrollcommand=scrollbar.set)
        self.log_box.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        footer = ttk.Frame(outer)
        footer.pack(fill=X, pady=(12, 0))
        ttk.Button(footer, text='使用说明', style='Secondary.TButton',
                   command=self.show_help).pack(side=LEFT)
        self.open_button = ttk.Button(footer, text='打开上次文件',
                                      style='Secondary.TButton',
                                      command=self.open_last, state='disabled')
        self.open_button.pack(side=LEFT, padx=(8, 0))
        ttk.Label(footer, text='结果保存在：桌面\\爱问云邀请码',
                  font=('Microsoft YaHei UI', 9),
                  foreground='#9aa0a6').pack(side=RIGHT)

    # ---------- 导出流程 ----------
    def on_export(self) -> None:
        if self.busy.get():
            return
        allow_restart = False
        if sys.platform == 'darwin' and not debug_port_open():
            if not messagebox.askyesno(
                    APP_NAME,
                    'macOS 版需要关闭并重新打开爱问云，以便在本机 9222 端口'
                    '读取登录态。\n\n未保存的操作会丢失，继续吗？'):
                return
            allow_restart = True

        self.cancel_event = threading.Event()
        self.last_path = None
        self.summary = None
        self.open_button.configure(state='disabled')
        self.progress.configure(mode='indeterminate', value=0)
        self.progress.start(12)
        self.set_busy(True)
        self.clear_log()
        self.append_log('开始导出。登录态只从本机客户端读取，不写入日志/文件。')
        if sys.platform == 'darwin':
            self.append_log('macOS：将重启客户端并仅在本机 127.0.0.1 开启调试端口。')

        options = ExportOptions(
            past_days=max(0, self.past_days.get()),
            workers=max(1, min(16, self.workers.get())),
            include_details=self.include_details.get(),
            open_file=self.open_after.get(),
            write_csv=self.write_csv.get(),
            write_json=self.write_json.get(),
            allow_restart=allow_restart,
        )
        self.worker = threading.Thread(
            target=self._work, args=(options,), daemon=True)
        self.worker.start()

    def _work(self, options: ExportOptions) -> None:
        try:
            run_export(options, on_event=self._post, cancel_event=self.cancel_event)
        except Cancelled as error:
            self._post('canceled', str(error))
        except NoClassesError as error:
            self._post('error', str(error))
        except PipelineError as error:
            self._post('error', str(error))
        except Exception as error:
            self._post('error', '%s: %s' % (type(error).__name__, error))

    def _post(self, kind: str, payload=None) -> None:
        self.ui_queue.put((kind, payload))

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == 'phase':
                    self.status.set(str(payload))
                    self.append_log('▶ ' + str(payload))
                elif kind == 'log':
                    self.append_log(str(payload))
                elif kind == 'progress':
                    done = int(payload.get('done', 0))
                    total = max(1, int(payload.get('total', 1)))
                    self.progress.stop()
                    self.progress.configure(mode='determinate',
                                            maximum=total, value=done)
                    record = payload.get('record') or {}
                    self.status.set('正在取邀请码：%d/%d' % (done, total))
                    topic = str(record.get('topic') or '')[:42]
                    code = record.get('code')
                    if code:
                        self.append_log('[%d/%d] %s  →  %s' % (done, total, topic, code))
                    else:
                        self.append_log('[%d/%d] %s  →  失败：%s' % (
                            done, total, topic, record.get('note') or ''))
                elif kind == 'done':
                    self.finish(payload)
                elif kind == 'canceled':
                    self.set_busy(False)
                    self.progress.stop()
                    self.progress.configure(mode='determinate', value=0)
                    self.status.set('已取消。')
                    self.append_log('已取消。')
                elif kind == 'error':
                    self.set_busy(False)
                    self.progress.stop()
                    self.progress.configure(mode='determinate', value=0)
                    message = str(payload)
                    self.status.set('导出失败，请看下方提示。')
                    self.append_log('失败：' + message)
                    messagebox.showerror(APP_NAME, message)
        except queue.Empty:
            pass
        self.root.after(80, self._drain_queue)

    def finish(self, summary: ExportSummary) -> None:
        self.summary = summary
        self.last_path = summary.path
        self.set_busy(False)
        self.progress.stop()
        self.progress.configure(mode='determinate', maximum=100, value=100)
        self.status.set('完成：成功 %d / %d，用时 %.1fs' % (
            summary.success, summary.total, summary.elapsed))
        self.open_button.configure(state='normal')
        self.append_log('')
        self.append_log('Excel：%s' % summary.path)
        self.append_log('成功 %d / %d，失败 %d，用时 %.2fs' % (
            summary.success, summary.total, summary.failed, summary.elapsed))
        if summary.conflicts:
            self.append_log('注意：%d 个课次有多个不同邀请码，已保留第一个。' % len(summary.conflicts))
        if summary.csv_path:
            self.append_log('CSV：%s' % summary.csv_path)
        if summary.json_path:
            self.append_log('JSON：%s' % summary.json_path)
        if self.open_after.get():
            try:
                open_path(summary.path)
            except OSError as error:
                self.append_log('未能自动打开 Excel：%s' % error)

    def on_cancel(self) -> None:
        if not self.busy.get():
            return
        self.cancel_event.set()
        self.status.set('正在取消…（等待当前请求结束）')
        self.append_log('已请求取消，正在等待进行中的请求结束…')

    def on_close(self) -> None:
        if self.busy.get():
            if not messagebox.askyesno(APP_NAME, '正在导出，确定要退出吗？'):
                return
            self.cancel_event.set()
        self.root.destroy()

    # ---------- 工具 ----------
    def set_busy(self, busy: bool) -> None:
        self.busy.set(busy)
        self.export_button.configure(
            state='disabled' if busy else 'normal',
            text='正在导出…' if busy else '一键导出 Excel')
        self.cancel_button.configure(state='normal' if busy else 'disabled')

    def append_log(self, message: str) -> None:
        self.log_box.configure(state='normal')
        self.log_box.insert('end', message.rstrip() + '\n')
        self.log_box.see('end')
        self.log_box.configure(state='disabled')

    def clear_log(self) -> None:
        self.log_box.configure(state='normal')
        self.log_box.delete('1.0', 'end')
        self.log_box.configure(state='disabled')

    def open_last(self) -> None:
        if self.last_path and Path(self.last_path).is_file():
            try:
                open_path(self.last_path)
            except OSError:
                pass

    def show_help(self) -> None:
        if sys.platform == 'darwin':
            mac_note = (
                'macOS 说明：由于系统权限限制，需要关闭并重新打开爱问云，'
                '仅在本机 127.0.0.1:9222 开启调试端口读取登录态；'
                '导出完成后建议正常重启一次关闭端口。\n\n')
        else:
            mac_note = (
                'Windows 说明：免重启、不开调试端口，只读扫描客户端内存。\n\n')
        messagebox.showinfo(
            APP_NAME,
            mac_note +
            '1. 打开并登录爱问云（老师账号）。\n\n'
            '2. 点「一键导出」。工具读取登录态，自动拉取「实时课堂」，'
            '并发读取邀请码，按「课程 × 第几节课」排成 Excel，并生成「明细」工作表。\n\n'
            '3. 文件保存在桌面「爱问云邀请码」文件夹。\n\n'
            '安全说明：token 只在本进程内存中使用，不写日志、不落盘；'
            '只向 HTTPS 爱问云业务主机发请求。')

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    app = App()
    if '--auto' in sys.argv:
        if '--no-open' in sys.argv:
            app.open_after.set(False)
        app.root.after(600, app.on_export)
    app.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
