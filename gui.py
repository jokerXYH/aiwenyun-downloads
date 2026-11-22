"""Tkinter 图形界面：默认免重启，只读登录态；支持账号密码登录导出板书。"""

from __future__ import annotations

import base64
import ctypes
import datetime
import io
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import (BOTH, LEFT, RIGHT, X, Y, BooleanVar, IntVar, StringVar,
                     Tk, filedialog, messagebox, ttk)
from tkinter import Text, font as tkfont

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover  打包缺 Pillow 时退回文字符号
    Image = None
    ImageDraw = None

from config import APP_NAME, APP_VERSION, DEFAULT_BASE_URL, open_path
from banshu_export import (BatchCancelled, RANGE_PRESETS,
                           UNCLASSIFIED_GROUP, class_group, default_out_root,
                           enrich_record_groups, export_records,
                           fetch_class_names, fetch_records,
                           group_board_records, ordered_group_names,
                           record_time, resolve_range, scan_token,
                           sort_records_newest_first)
from login import LoginError, LoginResult, do_login
from net import ApiSession, TokenExpired
from homework_export import (HomeworkCancelled, class_short_name,
                             default_out_root as homework_out_root,
                             export_homeworks, fetch_homeworks,
                             group_homeworks, homework_group, homework_time,
                             homework_title, ordered_groups,
                             summarize as summarize_homework)
from pipeline import (Cancelled, ExportOptions, ExportSummary, NoClassesError,
                      PipelineError, run_export)
from token_source import client_running, debug_port_open

FONT = 'Microsoft YaHei UI'
MONO = 'Consolas'

# 作业列表里没有对应班级时的分组名（板书用的是 banshu_export 的「杂项/未分类」）
UNCLASSIFIED_GROUP_NAME = '未分类'
# 班级按钮最多摆这么多个（常见 3~6 个班）
HOME_CLASS_LIMIT = 8


def class_button_labels(names: list[str]) -> dict[str, str]:
    """班级按钮上的短标签：2026027MA+31作业班 → 7MA+31。

    如果不同班级压出来是同一个短名（比如 2026017MAA71 和 2026027MAA71），
    这几个就退回用全名，保证一眼能分清。
    """

    short = {name: class_short_name(name) for name in names}
    counts: dict[str, int] = {}
    for value in short.values():
        counts[value] = counts.get(value, 0) + 1
    return {name: (short[name] if counts[short[name]] == 1 else name)
            for name in names}


# 方向：安静的课堂工具。表面用带一点纸张暖度的中性灰，绿色只留给
# 主操作、选中状态和进度；彩色面积集中到“班级分组列表”和“导出进度”
# 两个内容时刻，不再让标签/按钮抢戏。
ACCENT = '#1f6b4e'
ACCENT_DARK = '#14513a'
ACCENT_SOFT = '#eaf2ed'
TEXT_DARK = '#1b241f'
TEXT_MUTED = '#5f6b63'
TEXT_FAINT = '#6b776e'
BG = '#f3f4f0'
CARD = '#ffffff'
SURFACE = '#f7f8f4'
SURFACE_ALT = '#eef1eb'
BORDER = '#e1e5de'
BORDER_STRONG = '#cfd6ce'
GROUP_BG = '#edf3ed'
GROUP_MISC_BG = '#f2f3f0'
CHECKED_BG = '#e1efe6'


def _enable_dpi_awareness() -> None:
    """Windows 高 DPI：避免窗口被系统位图拉伸，导致字体发虚。"""

    if sys.platform != 'win32':
        return
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE；必须赶在创建窗口前调用
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:  # noqa: BLE001
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:  # noqa: BLE001
        pass


def _fmt_clock(seconds: float) -> str:
    """把秒数排成 00:42 / 1:02:03 的等宽格式。"""

    value = max(0, int(seconds or 0))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return '%d:%02d:%02d' % (hours, minutes, secs)
    return '%02d:%02d' % (minutes, secs)


class ProgressPanel:
    """导出进度：独立小弹窗，不再占用页面里的位置。

    任务开始时弹出（跟着主窗口走，居中偏下），完成后 2 秒自动关掉；
    失败/取消时留着，按钮变成「关闭」让用户自己关。
    ``compact=True`` 用于邀请码导出这种不需要子进度条的场景。
    """

    def __init__(self, parent, cancel_command, compact: bool = False,
                 on_show=None, on_hide=None) -> None:
        self.compact = compact
        self._cancel_command = cancel_command
        self._on_show = on_show        # 兼容旧签名，弹窗模式下不再使用
        self._on_hide = on_hide
        self.root = parent.winfo_toplevel()
        try:
            self._scale = max(1.0, float(self.root.winfo_fpixels('1i')) / 96.0)
        except Exception:  # noqa: BLE001
            self._scale = 1.0

        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.title('导出进度')
        self.win.transient(self.root)
        self.win.resizable(False, False)
        self.win.configure(bg=CARD)
        self.win.protocol('WM_DELETE_WINDOW', self.hide)

        self.frame = tk.Frame(self.win, bg=CARD, bd=0,
                              highlightbackground=BORDER,
                              highlightthickness=1)
        self.frame.pack(fill=BOTH, expand=True)
        self.accent = tk.Frame(self.frame, bg=ACCENT, width=self._px(3))
        self.accent.pack(side=LEFT, fill=Y)
        self.body = ttk.Frame(self.frame, style='Card.TFrame',
                              padding=(self._px(16), self._px(12),
                                       self._px(12), self._px(12)))
        self.body.pack(side=LEFT, fill=BOTH, expand=True)

        head = ttk.Frame(self.body, style='Card.TFrame')
        head.pack(fill=X)
        self.title_var = StringVar(value='')
        ttk.Label(head, textvariable=self.title_var,
                  style='ProgressTitle.TLabel').pack(side=LEFT)
        self.cancel_button = ttk.Button(
            head, text='取消导出', style='Quiet.TButton',
            command=self._primary_action, state='disabled')
        self.cancel_button.pack(side=RIGHT)
        self.percent_var = StringVar(value='')
        self.percent_label = tk.Label(
            head, textvariable=self.percent_var, bg=CARD, fg=ACCENT_DARK,
            font=(MONO, 10, 'bold'))
        self.percent_label.pack(side=RIGHT, padx=(0, self._px(12)))
        self.elapsed_var = StringVar(value='')
        tk.Label(head, textvariable=self.elapsed_var, bg=CARD, fg=TEXT_FAINT,
                 font=(MONO, 9)).pack(side=RIGHT, padx=(0, self._px(16)))

        self.detail_var = StringVar(value='')
        self.detail_label = tk.Label(
            self.body, textvariable=self.detail_var, bg=CARD, fg=TEXT_MUTED,
            font=(FONT, 10), anchor='w', justify=LEFT,
            wraplength=self._px(420))
        self.detail_label.pack(fill=X, pady=(self._px(2), self._px(8)))

        self.bar = ttk.Progressbar(
            self.body, mode='determinate', maximum=100, value=0,
            style='Accent.Horizontal.TProgressbar', length=self._px(430))
        self.bar.pack(fill=X)

        self.sub_row = ttk.Frame(self.body, style='Card.TFrame')
        self.sub_detail = StringVar(value='')
        tk.Label(self.sub_row, textvariable=self.sub_detail, bg=CARD,
                 fg=TEXT_FAINT, font=(FONT, 9), anchor='w').pack(side=LEFT)
        self.sub_bar = ttk.Progressbar(
            self.sub_row, mode='determinate', maximum=100, value=0,
            style='Thin.Horizontal.TProgressbar')
        self.sub_bar.pack(side=RIGHT, fill=X, expand=True,
                          padx=(self._px(16), 0))

        self._started = 0.0
        self._active = False
        self._indeterminate = False
        self._auto_hide_job = None

    def _px(self, value: int) -> int:
        return max(1, int(round(value * self._scale)))

    # -- 显示/隐藏 --------------------------------------------------
    def show(self) -> None:
        self._cancel_auto_hide()
        self.win.deiconify()
        # 先显示出来再定位：某些窗口管理器会忽略隐藏窗口的 geometry
        self.win.update_idletasks()
        self._place()
        self.win.lift()
        # 主窗口设为置顶时，Toplevel 会被压在下面，闪一下置顶确保看得见
        try:
            self.win.attributes('-topmost', True)
            self.win.after(400,
                           lambda: self.win.attributes('-topmost', False))
        except Exception:  # noqa: BLE001
            pass

    def hide(self) -> None:
        self._cancel_auto_hide()
        self.win.withdraw()

    def _place(self) -> None:
        """贴着主窗口居中偏下，别跑到屏幕外面。"""

        try:
            self.win.update_idletasks()
            width = max(self._px(460), self.win.winfo_reqwidth())
            height = self.win.winfo_reqheight()
            x = self.root.winfo_rootx() + (
                self.root.winfo_width() - width) // 2
            y = self.root.winfo_rooty() + self.root.winfo_height() - height - self._px(96)
            x = max(0, min(x, self.win.winfo_screenwidth() - width))
            y = max(0, min(y, self.win.winfo_screenheight() - height
                           - self._px(48)))
            self.win.geometry('%dx%d+%d+%d' % (width, height, x, y))
            self.win.update_idletasks()
        except Exception:  # noqa: BLE001
            pass

    def _primary_action(self) -> None:
        if self._active:
            if self._cancel_command is not None:
                self._cancel_command()
            return
        self.hide()

    def _schedule_auto_hide(self, delay_ms: int = 2200) -> None:
        self._cancel_auto_hide()
        self._auto_hide_job = self.win.after(delay_ms, self.hide)

    def _cancel_auto_hide(self) -> None:
        if self._auto_hide_job is not None:
            try:
                self.win.after_cancel(self._auto_hide_job)
            except Exception:  # noqa: BLE001
                pass
            self._auto_hide_job = None

    # -- 状态 --------------------------------------------------------
    def begin(self, title: str, detail: str = '',
              indeterminate: bool = False, hint: str = '',
              cancellable: bool = True) -> None:
        self._cancel_auto_hide()
        self._started = time.monotonic()
        self._active = True
        self.title_var.set(title)
        self.detail_var.set(
            detail + ('  ·  ' + hint if hint and not self.compact else ''))
        self.percent_var.set('')
        self.elapsed_var.set('00:00')
        self.sub_detail.set('')
        self.sub_bar.configure(value=0)
        self.bar.stop()
        self.bar.configure(
            mode='indeterminate' if indeterminate else 'determinate',
            maximum=100, value=0)
        self._indeterminate = bool(indeterminate)
        if indeterminate:
            self.bar.start(12)
        self.cancel_button.configure(
            command=self._primary_action,
            state='normal' if cancellable else 'disabled',
            text='取消' if cancellable else '请稍候')
        if not self.compact:
            self.sub_row.pack(fill=X, pady=(self._px(4), 0))
        self.show()

    def update(self, percent: float, detail: str | None = None,
               elapsed: float | None = None,
               sub_detail: str | None = None,
               sub_percent: float | None = None,
               indeterminate: bool = False) -> None:
        if not self._active:
            return
        if indeterminate:
            if not self._indeterminate:
                self.bar.stop()
                self.bar.configure(mode='indeterminate', maximum=100, value=0)
                self.bar.start(12)
                self._indeterminate = True
            self.percent_var.set('')
        else:
            if self._indeterminate:
                self.bar.stop()
                self.bar.configure(mode='determinate', maximum=100, value=0)
                self._indeterminate = False
            value = max(0.0, min(100.0, float(percent or 0)))
            self.bar.configure(value=value)
            self.percent_var.set('%d%%' % int(round(value)))
        if detail is not None:
            self.detail_var.set(detail)
        if elapsed is None:
            elapsed = time.monotonic() - self._started
        self.elapsed_var.set(_fmt_clock(elapsed))
        if sub_detail is not None:
            self.sub_detail.set(sub_detail)
        if sub_percent is not None:
            self.sub_bar.configure(
                value=max(0.0, min(100.0, float(sub_percent))))

    def complete(self, title: str, detail: str,
                 hint: str = '') -> None:
        self._active = False
        self.bar.stop()
        self.bar.configure(mode='determinate', maximum=100, value=100)
        self.percent_var.set('100%')
        self.elapsed_var.set(_fmt_clock(
            time.monotonic() - self._started if self._started else 0))
        self.title_var.set(title)
        self.detail_var.set(detail + ('  ·  ' + hint if hint else ''))
        self.cancel_button.configure(command=self._primary_action,
                                     state='normal', text='关闭')
        if not self.compact:
            self.sub_row.pack_forget()
        self.show()
        self._schedule_auto_hide()

    def fail(self, title: str, detail: str, hint: str = '',
             state: str = '已取消') -> None:
        self._active = False
        self.bar.stop()
        self.bar.configure(mode='determinate')
        self.title_var.set(title)
        self.detail_var.set(detail + ('  ·  ' + hint if hint else ''))
        self.elapsed_var.set(_fmt_clock(
            time.monotonic() - self._started if self._started else 0))
        self.cancel_button.configure(command=self._primary_action,
                                     state='normal', text='关闭')
        if not self.compact:
            self.sub_row.pack_forget()
        self.show()

    def tick(self) -> None:
        if self._active and self._started:
            self.elapsed_var.set(_fmt_clock(time.monotonic() - self._started))




class LogPanel:
    """每个功能自己的运行日志；板书页的日志可折叠，避免挤占列表。"""

    def __init__(self, parent, title: str, subtitle: str = '',
                 height: int = 6, collapsible: bool = False,
                 expanded: bool = True, px=None, on_toggle=None) -> None:
        self._px = px or (lambda value: value)
        self._on_toggle = on_toggle
        self.frame = tk.Frame(parent, bg=CARD, bd=0,
                              highlightbackground=BORDER,
                              highlightthickness=1)
        self.body = ttk.Frame(
            self.frame, style='Card.TFrame',
            padding=(self._px(16), self._px(12),
                     self._px(16), self._px(12)))
        self.body.pack(fill=BOTH, expand=True)

        head = ttk.Frame(self.body, style='Card.TFrame')
        head.pack(fill=X)
        ttk.Label(head, text=title, style='SectionSmall.TLabel').pack(side=LEFT)
        if subtitle:
            ttk.Label(head, text=subtitle, style='Faint.TLabel').pack(
                side=LEFT, padx=(self._px(12), 0))
        self.toggle_button = None
        if collapsible:
            self.toggle_button = ttk.Button(
                head, text='收起' if expanded else '展开',
                style='Quiet.TButton', command=self._toggle_command)
            self.toggle_button.pack(side=RIGHT)
        self.clear_button = ttk.Button(
            head, text='清空', style='Quiet.TButton', command=self.clear)
        self.clear_button.pack(
            side=RIGHT, padx=(0, self._px(8)) if collapsible else 0)

        self.text = Text(self.body, height=height, width=1, wrap='char',
                         relief='flat', bg=SURFACE, fg='#3d484f',
                         highlightthickness=0, font=(MONO, 10),
                         padx=self._px(12), pady=self._px(10),
                         spacing1=self._px(2), spacing3=self._px(2),
                         state='disabled', selectbackground=ACCENT_SOFT)
        self.scroll = ttk.Scrollbar(self.body, orient='vertical',
                                    command=self.text.yview,
                                    style='Vertical.TScrollbar')
        self.text.configure(yscrollcommand=self.scroll.set)
        self._collapsible = bool(collapsible)
        self._expanded = bool(expanded)
        self._unread = 0
        if self._expanded:
            self._pack_text()
        else:
            self._update_toggle_text()

    # -- 布局 --------------------------------------------------------
    def _pack_text(self) -> None:
        self.text.pack(side=LEFT, fill=BOTH, expand=True,
                       pady=(self._px(8), 0))
        self.scroll.pack(side=RIGHT, fill=Y, pady=(self._px(8), 0))

    def _unpack_text(self) -> None:
        self.text.pack_forget()
        self.scroll.pack_forget()

    def _update_toggle_text(self) -> None:
        if self.toggle_button is None:
            return
        if self._expanded:
            self.toggle_button.configure(text='收起')
        elif self._unread:
            self.toggle_button.configure(
                text='展开 · %d' % min(99, self._unread))
        else:
            self.toggle_button.configure(text='展开')

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        if expanded == self._expanded:
            self._update_toggle_text()
            return
        self._expanded = expanded
        if expanded:
            self._pack_text()
            self._unread = 0
        else:
            self._unpack_text()
        self._update_toggle_text()

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def _toggle_command(self) -> None:
        """按钮回调：交给外面决定要不要展开（例如先算高度）。"""

        if self._on_toggle is None:
            self.toggle()
        else:
            self._on_toggle()

    @property
    def expanded(self) -> bool:
        return self._expanded

    # -- 内容 --------------------------------------------------------
    def append(self, message: str) -> None:
        self.text.configure(state='normal')
        self.text.insert('end', str(message).rstrip() + '\n')
        self.text.see('end')
        self.text.configure(state='disabled')
        if not self._expanded:
            self._unread += 1
            self._update_toggle_text()

    def clear(self) -> None:
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.configure(state='disabled')
        self._unread = 0
        self._update_toggle_text()


class App:
    def __init__(self) -> None:
        _enable_dpi_awareness()
        self.root = Tk()
        self.root.title('%s %s' % (APP_NAME, APP_VERSION))
        try:
            dpi = float(self.root.winfo_fpixels('1i'))
            self.ui_factor = max(1.0, dpi / 96.0)
            self.root.tk.call('tk', 'scaling', dpi / 72.0)
        except Exception:  # noqa: BLE001
            self.ui_factor = 1.0
        self._apply_window_geometry()
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
        self.status = StringVar(value='免重启模式：点「导出邀请码 Excel」，直接读取已登录客户端的会话。')
        self.past_days = IntVar(value=0)
        self.workers = IntVar(value=8)
        self.include_details = BooleanVar(value=True)
        self.open_after = BooleanVar(value=True)
        self.write_csv = BooleanVar(value=False)
        self.write_json = BooleanVar(value=False)

        # 板书导出：优先复用本机已登录客户端；没有登录态时才用账号密码
        self.login_name = StringVar(value='')
        self.login_pass = StringVar(value='')
        today = datetime.date.today()
        self.board_range = StringVar(value='最近30天')
        self.board_from = StringVar(
            value=(today - datetime.timedelta(days=29)).strftime('%Y-%m-%d'))
        self.board_to = StringVar(value=today.strftime('%Y-%m-%d'))
        self.board_sort = StringVar(value='按上课顺序')
        self.board_meta = StringVar(value='识别本机登录态并列出板书后，将按班级分组显示')
        self.login_result: LoginResult | None = None
        self.board_token = ''
        self.board_origin = ''                    # 'client' / 'account'
        self._client_scanning = False
        self._scan_manual = False
        self._force_account = False
        self.client_hint = StringVar(value='正在检测本机已登录的爱问云客户端…')
        self._credentials: tuple[str, str] = ('', '')
        self._busy_kind: str = ''
        self.invite_running = False               # 邀请码导出是否正在跑
        self.board_records: list[dict] = []       # 「列出板书」的结果
        self.board_checked: dict[str, bool] = {}  # 课堂键 -> 是否勾选
        self.board_class_names: list[str] = []    # 爱问云班级列表
        self.board_group_of: dict[int, str] = {}  # id(记录) -> 班级名（导出分文件夹用）
        self.board_groups: dict[str, list[dict]] = {}
        self.board_active_class = ''
        self._pending_list = False                # 忙碌中收到的时间段切换请求

        # 作业导出：按班级分组的作业框清单，一个作业框一个 CSV
        self.homework_range = StringVar(value='最近30天')
        self.homework_from = StringVar(
            value=(today - datetime.timedelta(days=29)).strftime('%Y-%m-%d'))
        self.homework_to = StringVar(value=today.strftime('%Y-%m-%d'))
        self.homework_meta = StringVar(
            value='识别本机登录态后，将按班级分组列出作业框')
        self.homework_items: list[dict] = []      # 「列出作业」的结果
        self.homework_groups: dict[str, list[dict]] = {}
        self.homework_class_order: list[str] = []
        self.homework_active_class = ''
        self.homework_checked: dict[str, bool] = {}
        self._pending_homework_list = False
        self._pending_homework_auto = False
        self.view = 'invite'

        self._build_ui()
        self.root.after(80, self._drain_queue)
        self.root.after(500, self._tick_progress)
        self.root.after(280, self._start_client_scan)
        self.root.bind('<Control-Key-1>',
                       lambda _e: self._switch_view('invite'))
        self.root.bind('<Control-Key-2>',
                       lambda _e: self._switch_view('board'))
        self.root.bind('<Control-Key-3>',
                       lambda _e: self._switch_view('homework'))

    def _apply_window_geometry(self) -> None:
        """按屏幕大小定窗口尺寸，避免 1440x900 等小屏底部被任务栏裁掉。"""

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        factor = self.ui_factor
        width = min(int(1320 * factor), int(screen_w * 0.96))
        height = min(int(820 * factor), int(screen_h * 0.90))
        x = max(0, (screen_w - width) // 2)
        y = max(0, (screen_h - height) // 2 - int(18 * factor))
        self.root.geometry('%dx%d+%d+%d' % (width, height, x, y))
        self.root.minsize(min(int(1140 * factor), width),
                          min(int(720 * factor), height))

    def px(self, value: int) -> int:
        """按当前 DPI 缩放一个像素值。"""

        return max(1, int(round(value * self.ui_factor)))

    # ---------- UI ----------
    def _card(self, parent, padding: int = 20):
        """白底细边框卡片；返回 (外框, 内容区)。"""

        outer = tk.Frame(parent, bg=CARD, bd=0,
                         highlightbackground=BORDER, highlightthickness=1)
        body = ttk.Frame(outer, style='Card.TFrame', padding=padding)
        body.pack(fill=BOTH, expand=True)
        return outer, body

    def _check(self, parent, text: str, variable) -> tk.Frame:
        """自绘复选框：图标与文字都可点，避免 clam 主题里那个像「X」的方框。"""

        holder = tk.Frame(parent, bg=CARD, cursor='hand2')
        icon = tk.Label(holder, bg=CARD, bd=0, padx=0, pady=0,
                        cursor='hand2')
        label = tk.Label(holder, text=text, bg=CARD, fg=TEXT_MUTED,
                         font=(FONT, 10), anchor='w', justify=LEFT,
                         cursor='hand2')
        icon.pack(side=LEFT, padx=(0, self.px(8)))
        label.pack(side=LEFT)

        def refresh(*_args) -> None:
            key = 'toggle_checked' if variable.get() else 'toggle_unchecked'
            image = getattr(self, 'check_icons', {}).get(key)
            if image is not None:
                icon.configure(image=image)
                icon.image = image
            else:                       # 缺 Pillow 时退回字符
                icon.configure(text='☑' if variable.get() else '☐',
                               fg=ACCENT_DARK if variable.get() else TEXT_MUTED,
                               font=(FONT, 12))
            label.configure(fg=TEXT_DARK if variable.get() else TEXT_MUTED)

        def toggle(_event=None):
            variable.set(not variable.get())
            return 'break'

        for widget in (holder, icon, label):
            widget.bind('<Button-1>', toggle)
        variable.trace_add('write', refresh)
        refresh()
        return holder

    def _build_check_icons(self) -> None:
        """生成列表勾选、折叠箭头等位图图标；高 DPI 下也保持锐利。"""

        self.check_icons: dict[str, tk.PhotoImage] = {}
        self.ui_icons: dict[str, tk.PhotoImage] = {}
        if Image is None:
            return
        size = self.px(24)
        for kind in ('unchecked', 'checked', 'partial'):
            base = self._draw_check_icon(kind, size)
            buffer = io.BytesIO()
            base.save(buffer, format='PNG')
            data = base64.b64encode(buffer.getvalue()).decode('ascii')
            self.check_icons[kind] = tk.PhotoImage(data=data)
            self.check_icons[kind + '_leaf'] = tk.PhotoImage(data=data)

        toggle_size = self.px(16)
        for kind in ('unchecked', 'checked'):
            image = self._draw_check_icon(kind, toggle_size)
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            data = base64.b64encode(buffer.getvalue()).decode('ascii')
            self.check_icons['toggle_' + kind] = tk.PhotoImage(data=data)

        chevron_size = self.px(16)
        for kind in ('chevron_down', 'chevron_up'):
            for tone, color in (('muted', (95, 107, 99, 255)),
                                ('accent', (31, 107, 78, 255))):
                image = self._draw_chevron(kind, chevron_size, color)
                buffer = io.BytesIO()
                image.save(buffer, format='PNG')
                data = base64.b64encode(buffer.getvalue()).decode('ascii')
                self.ui_icons['%s_%s' % (kind, tone)] = tk.PhotoImage(data=data)

    @staticmethod
    def _draw_chevron(kind: str, size: int, color):
        """画一个 1.5px 线宽的折叠箭头，避免用字体里的细小三角。"""

        scale = 4
        pixels = max(8, size) * scale
        image = Image.new('RGBA', (pixels, pixels), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        width = max(2, int(pixels * 0.11))
        if kind == 'chevron_up':
            points = [(0.24, 0.62), (0.5, 0.36), (0.76, 0.62)]
        else:
            points = [(0.24, 0.38), (0.5, 0.64), (0.76, 0.38)]
        draw.line([(x * pixels, y * pixels) for x, y in points],
                  fill=color, width=width, joint='curve')
        return image.resize((max(8, size), max(8, size)),
                            Image.Resampling.LANCZOS)

    def _icon(self, key: str):
        return getattr(self, 'ui_icons', {}).get(key)

    def _draw_check_icon(self, kind: str, size: int):
        """画一个圆角方形复选框：空、勾选、部分选中。"""

        scale = 4
        pixels = max(12, size) * scale
        image = Image.new('RGBA', (pixels, pixels), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        pad = max(2, int(pixels * 0.07))
        radius = max(3, int(pixels * 0.24))
        border = max(2, int(pixels * 0.07))
        if kind == 'checked':
            fill, outline = (31, 107, 78, 255), (20, 81, 58, 255)
        elif kind == 'partial':
            fill, outline = (234, 242, 237, 255), (31, 107, 78, 255)
        else:
            fill, outline = (255, 255, 255, 255), (163, 174, 165, 255)
        draw.rounded_rectangle((pad, pad, pixels - pad, pixels - pad),
                               radius=radius, fill=fill, outline=outline,
                               width=border)
        stroke = max(3, int(pixels * 0.10))
        if kind == 'checked':
            draw.line([(pixels * 0.27, pixels * 0.52),
                       (pixels * 0.44, pixels * 0.69),
                       (pixels * 0.75, pixels * 0.31)],
                      fill=(255, 255, 255, 255), width=stroke, joint='curve')
        elif kind == 'partial':
            draw.line([(pixels * 0.28, pixels * 0.50),
                       (pixels * 0.72, pixels * 0.50)],
                      fill=(31, 107, 78, 255), width=stroke)
        return image.resize((max(12, size), max(12, size)),
                            Image.Resampling.LANCZOS)

    def _check_icon(self, kind: str, leaf: bool = False):
        icons = getattr(self, 'check_icons', None)
        if not icons:
            return None
        return icons.get(kind + ('_leaf' if leaf else ''))

    def _make_tab(self, parent, key: str, label: str):
        """安静的标签页：文字 + 2px 底线，不用整块绿色去抢主按钮。"""

        holder = tk.Frame(parent, bg=BG)
        button = tk.Button(
            holder, text=label, command=lambda: self._switch_view(key),
            bg=BG, fg=TEXT_MUTED, activebackground=BG,
            activeforeground=TEXT_DARK, font=(FONT, 12), bd=0,
            relief='flat', highlightthickness=0, padx=0, pady=self.px(6),
            cursor='hand2')
        button.pack(anchor='w')
        underline = tk.Frame(holder, bg=BG, height=self.px(2))
        underline.pack(fill=X, pady=(self.px(6), 0))
        return holder, button, underline

    def _build_ui(self) -> None:
        for font_name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont',
                          'TkHeadingFont'):
            try:
                tkfont.nametofont(font_name).configure(family=FONT, size=10)
            except Exception:  # noqa: BLE001
                pass
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except Exception:  # noqa: BLE001
            pass

        style.configure('.', font=(FONT, 10), background=BG,
                        foreground=TEXT_DARK)
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG, foreground=TEXT_DARK)
        style.configure('Card.TFrame', background=CARD)
        style.configure('Surface.TFrame', background=SURFACE)
        style.configure('Card.TLabel', background=CARD, foreground=TEXT_DARK,
                        font=(FONT, 10))
        style.configure('Muted.TLabel', background=CARD, foreground=TEXT_MUTED,
                        font=(FONT, 10))
        style.configure('Faint.TLabel', background=CARD, foreground=TEXT_FAINT,
                        font=(FONT, 9))
        style.configure('SurfaceMuted.TLabel', background=SURFACE,
                        foreground=TEXT_MUTED, font=(FONT, 10))
        style.configure('SurfaceFaint.TLabel', background=SURFACE,
                        foreground=TEXT_FAINT, font=(FONT, 9))
        style.configure('SurfaceHint.TLabel', background=SURFACE,
                        foreground=TEXT_MUTED, font=(FONT, 10))
        style.configure('SurfaceTitle.TLabel', background=SURFACE,
                        foreground=TEXT_DARK, font=(FONT, 11, 'bold'))
        style.configure('Section.TLabel', background=CARD,
                        foreground=TEXT_DARK, font=(FONT, 16, 'bold'))
        style.configure('SectionSmall.TLabel', background=CARD,
                        foreground=TEXT_DARK, font=(FONT, 11, 'bold'))
        style.configure('ProgressTitle.TLabel', background=CARD,
                        foreground=TEXT_DARK, font=(FONT, 11, 'bold'))
        style.configure('HeaderMuted.TLabel', background=BG,
                        foreground=TEXT_MUTED, font=(FONT, 10))
        style.configure('Footer.TLabel', background=BG, foreground=TEXT_FAINT,
                        font=(FONT, 9))
        style.configure('Status.TLabel', background=BG, foreground=TEXT_MUTED,
                        font=(FONT, 10))
        style.configure('Meta.TLabel', background=BG, foreground=TEXT_FAINT,
                        font=(MONO, 9))

        style.configure('Primary.TButton', font=(FONT, 11, 'bold'),
                        padding=(self.px(20), self.px(12)), background=ACCENT,
                        foreground='#ffffff', borderwidth=0, relief='flat',
                        focusthickness=0)
        style.map('Primary.TButton',
                  background=[('active', ACCENT_DARK), ('disabled', '#b7cdc1')],
                  foreground=[('disabled', '#f5f8f5')])
        style.configure('Secondary.TButton', font=(FONT, 10),
                        padding=(self.px(12), self.px(8)), background=CARD,
                        foreground=TEXT_DARK, borderwidth=1, relief='solid',
                        bordercolor=BORDER_STRONG, lightcolor=BORDER_STRONG,
                        darkcolor=BORDER_STRONG, focusthickness=0)
        style.map('Secondary.TButton',
                  background=[('active', SURFACE_ALT), ('disabled', '#f4f5f2')],
                  foreground=[('disabled', '#a8b0a9')],
                  bordercolor=[('active', BORDER_STRONG), ('disabled', BORDER)])
        style.configure('Quiet.TButton', font=(FONT, 10),
                        padding=(self.px(8), self.px(4)), background=CARD,
                        foreground=TEXT_MUTED, borderwidth=0, relief='flat',
                        focusthickness=0)
        style.map('Quiet.TButton',
                  background=[('active', SURFACE_ALT)],
                  foreground=[('active', TEXT_DARK), ('disabled', '#b4bcb5')])
        style.configure('FooterQuiet.TButton', font=(FONT, 9),
                        padding=(self.px(8), self.px(4)), background=BG,
                        foreground=TEXT_MUTED, borderwidth=0, relief='flat',
                        focusthickness=0)
        style.map('FooterQuiet.TButton',
                  background=[('active', SURFACE_ALT)],
                  foreground=[('active', TEXT_DARK), ('disabled', '#b4bcb5')])
        style.configure('Fold.TButton', font=(FONT, 10, 'bold'),
                        padding=(self.px(12), self.px(8)), background=SURFACE_ALT,
                        foreground=TEXT_DARK, borderwidth=1, relief='solid',
                        bordercolor=BORDER_STRONG, lightcolor=BORDER_STRONG,
                        darkcolor=BORDER_STRONG, focusthickness=0)
        style.map('Fold.TButton',
                  background=[('active', '#e5eae2'), ('disabled', '#f2f3f0')],
                  foreground=[('disabled', '#a8b0a9')])

        style.configure('TEntry', padding=(self.px(8), self.px(6)),
                        fieldbackground=CARD, foreground=TEXT_DARK,
                        insertcolor=TEXT_DARK, selectbackground=ACCENT_SOFT,
                        selectforeground=TEXT_DARK, bordercolor=BORDER_STRONG,
                        lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG)
        style.map('TEntry',
                  bordercolor=[('focus', ACCENT)],
                  lightcolor=[('focus', ACCENT)],
                  darkcolor=[('focus', ACCENT)])
        style.configure('TCombobox', padding=(self.px(8), self.px(6)),
                        arrowsize=self.px(15), fieldbackground=CARD,
                        foreground=TEXT_DARK, bordercolor=BORDER_STRONG,
                        lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG,
                        arrowcolor=TEXT_MUTED)
        style.map('TCombobox',
                  fieldbackground=[('readonly', CARD)],
                  foreground=[('readonly', TEXT_DARK)],
                  bordercolor=[('focus', ACCENT)])
        style.configure('TSpinbox', padding=(self.px(8), self.px(6)),
                        arrowsize=self.px(14), fieldbackground=CARD,
                        foreground=TEXT_DARK, bordercolor=BORDER_STRONG,
                        lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG,
                        arrowcolor=TEXT_MUTED)

        style.configure('Accent.Horizontal.TProgressbar',
                        thickness=self.px(8), troughcolor='#e6eae3',
                        background=ACCENT, borderwidth=0, lightcolor=ACCENT,
                        darkcolor=ACCENT)
        style.configure('Thin.Horizontal.TProgressbar',
                        thickness=self.px(4), troughcolor='#edf0e9',
                        background=ACCENT, borderwidth=0, lightcolor=ACCENT,
                        darkcolor=ACCENT)
        style.configure('Vertical.TScrollbar', arrowsize=self.px(12),
                        width=self.px(10), background='#d7ddd5',
                        troughcolor=CARD, bordercolor=CARD, lightcolor=CARD,
                        darkcolor=CARD)
        style.configure('Board.Treeview', rowheight=self.px(36),
                        font=(FONT, 10), background=CARD,
                        fieldbackground=CARD, foreground=TEXT_DARK,
                        borderwidth=0, relief='flat')
        style.map('Board.Treeview',
                  background=[('selected', CHECKED_BG)],
                  foreground=[('selected', TEXT_DARK)])
        style.configure('Board.Treeview.Heading', font=(FONT, 10, 'bold'),
                        background=SURFACE, foreground=TEXT_MUTED,
                        padding=(self.px(12), self.px(8)), relief='flat',
                        borderwidth=0)
        style.map('Board.Treeview.Heading',
                  background=[('active', SURFACE_ALT)],
                  foreground=[('active', TEXT_DARK)])

        self._build_check_icons()

        outer = ttk.Frame(self.root, padding=(self.px(24), self.px(10),
                                              self.px(24), self.px(10)))
        outer.pack(fill=BOTH, expand=True)

        # ---------- 头部 ----------
        # 窗口高度是有限的，头部压成一行：应用名 + 只读说明，下面直接
        # 接标签页。以前 20pt 标题加两行说明，光页头就吃掉一百七十像素。
        header = ttk.Frame(outer)
        header.pack(fill=X)
        ttk.Label(header, text=APP_NAME, font=(FONT, 16, 'bold'),
                  foreground=TEXT_DARK).pack(side=LEFT)
        ttk.Label(header,
                  text='只读读取本机登录态 · 邀请码 Excel · 板书 PDF · 作业成绩 CSV',
                  style='HeaderMuted.TLabel').pack(
                      side=LEFT, anchor='s', padx=(self.px(14), 0),
                      pady=(0, self.px(3)))
        ttk.Label(header, text='v%s' % APP_VERSION,
                  style='Meta.TLabel').pack(side=RIGHT, anchor='s',
                                            pady=(0, self.px(3)))

        tk.Frame(outer, bg=BORDER, height=1).pack(fill=X, pady=(self.px(8), 0))

        # ---------- 功能切换 ----------
        tabbar = ttk.Frame(outer)
        tabbar.pack(fill=X, pady=(self.px(4), 0))
        self.tab_buttons: dict[str, tuple[tk.Button, tk.Frame]] = {}
        for key, label in (('invite', '邀请码导出'),
                           ('board', '板书导出'),
                           ('homework', '作业导出')):
            holder, button, underline = self._make_tab(tabbar, key, label)
            holder.pack(side=LEFT, padx=(0, self.px(24)))
            self.tab_buttons[key] = (button, underline)

        content = ttk.Frame(outer)
        content.pack(fill=BOTH, expand=True, pady=(self.px(8), 0))
        content.columnconfigure(0, weight=1)
        content.rowconfigure(0, weight=1)
        self.content = content

        # ---------- 邀请码视图 ----------
        invite_view = ttk.Frame(content)
        invite_view.grid(row=0, column=0, sticky='nsew')
        invite_view.columnconfigure(0, weight=1)
        invite_view.rowconfigure(1, weight=1)
        self.invite_view = invite_view

        invite_outer, invite = self._card(invite_view, padding=self.px(24))
        invite_outer.grid(row=0, column=0, sticky='ew')
        invite_grid = ttk.Frame(invite, style='Card.TFrame')
        invite_grid.pack(fill=BOTH, expand=True)
        invite_grid.columnconfigure(0, weight=1, minsize=self.px(440))
        invite_grid.columnconfigure(1, weight=0, minsize=self.px(420))

        invite_left = ttk.Frame(invite_grid, style='Card.TFrame')
        invite_left.grid(row=0, column=0, sticky='nw')
        ttk.Label(invite_left, text='邀请码导出',
                  style='Section.TLabel').pack(anchor='w')
        ttk.Label(invite_left,
                  text='免账号 · 读取本机已登录客户端，按课程导出 Excel',
                  style='Muted.TLabel').pack(anchor='w', pady=(4, 16))
        self.export_button = ttk.Button(invite_left, text='导出邀请码 Excel',
                                        style='Primary.TButton', width=23,
                                        command=self.on_export)
        self.export_button.pack(anchor='w')

        self.invite_progress_slot = ttk.Frame(invite_left, style='Card.TFrame')
        self.invite_progress_slot.pack(fill=X)
        self.invite_progress = ProgressPanel(
            self.invite_progress_slot, self.on_cancel, compact=True)
        self.invite_busy_note = ttk.Label(
            invite_left, text='板书功能正在运行 · 邀请码导出暂不可用',
            style='Faint.TLabel')

        invite_opts = ttk.Frame(invite_left, style='Card.TFrame')
        invite_opts.pack(fill=X, pady=(16, 0))
        ttk.Label(invite_opts, text='从', style='Muted.TLabel').pack(side=LEFT)
        ttk.Spinbox(invite_opts, from_=0, to=365, width=4,
                    textvariable=self.past_days).pack(side=LEFT, padx=(6, 6))
        ttk.Label(invite_opts, text='天前开始',
                  style='Muted.TLabel').pack(side=LEFT)
        ttk.Label(invite_opts, text='并发',
                  style='Muted.TLabel').pack(side=LEFT, padx=(22, 0))
        ttk.Spinbox(invite_opts, from_=1, to=16, width=4,
                    textvariable=self.workers).pack(side=LEFT, padx=(6, 6))
        ttk.Label(invite_opts, text='个请求',
                  style='Muted.TLabel').pack(side=LEFT)

        invite_flags = ttk.Frame(invite_left, style='Card.TFrame')
        invite_flags.pack(fill=X, pady=(16, 0))
        invite_flags.columnconfigure(0, weight=1)
        invite_flags.columnconfigure(1, weight=1)
        self._check(invite_flags, '生成明细', self.include_details).grid(
            row=0, column=0, sticky='w', pady=self.px(4))
        self._check(invite_flags, '完成后打开', self.open_after).grid(
            row=0, column=1, sticky='w', pady=self.px(4))
        self._check(invite_flags, '同时导出 CSV', self.write_csv).grid(
            row=1, column=0, sticky='w', pady=self.px(4))
        self._check(invite_flags, '同时导出 JSON', self.write_json).grid(
            row=1, column=1, sticky='w', pady=self.px(4))

        tk.Frame(invite_left, bg=BORDER, height=1).pack(
            fill=X, pady=(self.px(16), self.px(12)))
        ttk.Label(invite_left, text='保存到桌面「爱问云邀请码」',
                  style='Faint.TLabel').pack(anchor='w')

        tips_outer = tk.Frame(invite_grid, bg=SURFACE,
                              highlightbackground=BORDER,
                              highlightthickness=1)
        tips_outer.grid(row=0, column=1, sticky='nw',
                        padx=(self.px(32), 0))
        tips = ttk.Frame(tips_outer, style='Surface.TFrame',
                         padding=(self.px(20), self.px(16)))
        tips.pack(fill=BOTH, expand=True)
        ttk.Label(tips, text='使用步骤',
                  style='SurfaceTitle.TLabel').pack(anchor='w')
        for number, line in enumerate((
                '打开并登录爱问云老师端',
                '点「导出邀请码 Excel」，自动读取本机登录态',
                '并发拉取「实时课堂」的邀请码',
                '按「课程 × 第几节课」排成 Excel',
                '文件保存到桌面「爱问云邀请码」'), 1):
            row = tk.Frame(tips, bg=SURFACE)
            row.pack(fill=X, pady=self.px(4))
            tk.Label(row, text='%02d' % number, bg=SURFACE, fg=ACCENT_DARK,
                     font=(MONO, 9, 'bold'), width=3, anchor='nw').pack(
                         side=LEFT, anchor='n')
            tk.Label(row, text=line, bg=SURFACE, fg=TEXT_MUTED,
                     font=(FONT, 10), justify=LEFT, anchor='w',
                     wraplength=self.px(360)).pack(
                         side=LEFT, fill=X, expand=True)
        tk.Frame(tips, bg=BORDER, height=1).pack(
            fill=X, pady=(self.px(12), self.px(8)))
        tk.Label(tips, text='只读：不读取 cookie / 聊天记录 / 通讯录；'
                            'token 不写入文件。', bg=SURFACE, fg=TEXT_FAINT,
                 font=(FONT, 9), justify=LEFT, anchor='w',
                 wraplength=self.px(392)).pack(anchor='w')

        self.invite_log = LogPanel(
            invite_view, title='邀请码运行日志',
            subtitle='导出与登录诊断', height=6, px=self.px)
        self.invite_log.frame.grid(row=1, column=0, sticky='nsew',
                                   pady=(self.px(16), 0))

        # ---------- 板书视图 ----------
        board_outer, board = self._card(content, padding=self.px(14))
        board_outer.grid(row=0, column=0, sticky='nsew')
        board_outer.grid_remove()
        self.board_view = board_outer

        board_head = ttk.Frame(board, style='Card.TFrame')
        board_head.grid(row=0, column=0, sticky='ew')
        ttk.Label(board_head, text='板书导出',
                  style='Section.TLabel').pack(side=LEFT)
        ttk.Label(board_head,
                  text='按班级分组勾选 · 只导出所选 · PDF 按班级分文件夹',
                  style='Muted.TLabel').pack(side=LEFT, anchor='s',
                                             padx=(self.px(12), 0),
                                             pady=(0, self.px(3)))

        # 登录态和登录操作挪到页头右侧：以前它们和查询条件挤在工具栏同
        # 一行里，状态字被按钮、说明文字切成碎片，字号又小，看不清。
        self.login_row = ttk.Frame(board_head, style='Card.TFrame')
        self.login_row.pack(side=RIGHT, anchor='n', pady=(self.px(4), 0))
        self.login_badge = tk.Label(
            self.login_row, text='正在检测本机登录…', bg=CARD, fg=TEXT_MUTED,
            font=(FONT, 10))
        self.login_badge.pack(side=LEFT, padx=(0, self.px(12)))
        self.rescan_button = ttk.Button(
            self.login_row, text='重新检测', style='Secondary.TButton',
            command=lambda: self._start_client_scan(manual=True))
        self.rescan_button.pack(side=LEFT)
        self.use_account_link = tk.Label(
            self.login_row, text='改用账号密码登录', bg=CARD, fg=ACCENT,
            activeforeground=ACCENT_DARK, font=(FONT, 9, 'underline'),
            cursor='hand2')
        self.use_account_link.bind(
            '<Button-1>', lambda _e: self._enable_account_login())

        toolbar = tk.Frame(board, bg=SURFACE, bd=0,
                           highlightbackground=BORDER, highlightthickness=1)
        toolbar.grid(row=1, column=0, sticky='ew', pady=(self.px(14), 0))
        toolbar_body = ttk.Frame(toolbar, style='Surface.TFrame',
                                 padding=(self.px(16), self.px(10)))
        toolbar_body.pack(fill=X)

        # 查询条件只占一行：时间段 / 排序 ｜ 自定义日期，组与组之间用竖线
        # 断开，免得「排序」和「自定义」连成一串读不出边界。
        range_row = ttk.Frame(toolbar_body, style='Surface.TFrame')
        range_row.pack(fill=X)
        ttk.Label(range_row, text='时间段',
                  style='SurfaceMuted.TLabel').pack(side=LEFT)
        self.range_box = ttk.Combobox(range_row, textvariable=self.board_range,
                                      values=list(RANGE_PRESETS),
                                      state='readonly', width=12)
        self.range_box.pack(side=LEFT, padx=(self.px(8), self.px(18)))
        self.range_box.bind('<<ComboboxSelected>>',
                            lambda _e: self._auto_list_boards())
        ttk.Label(range_row, text='排序',
                  style='SurfaceMuted.TLabel').pack(side=LEFT)
        self.sort_box = ttk.Combobox(range_row, textvariable=self.board_sort,
                                     values=['按上课顺序', '按板书类型'],
                                     state='readonly', width=11)
        self.sort_box.pack(side=LEFT, padx=(self.px(8), self.px(18)))
        tk.Frame(range_row, bg=BORDER_STRONG, width=1).pack(
            side=LEFT, fill=Y, pady=self.px(4), padx=(0, self.px(18)))
        ttk.Label(range_row, text='自定义日期',
                  style='SurfaceMuted.TLabel').pack(side=LEFT)
        self.from_entry = ttk.Entry(range_row, textvariable=self.board_from,
                                    width=11)
        self.from_entry.pack(side=LEFT, padx=(self.px(8), self.px(6)))
        ttk.Label(range_row, text='至',
                  style='SurfaceMuted.TLabel').pack(side=LEFT)
        self.to_entry = ttk.Entry(range_row, textvariable=self.board_to,
                                  width=11)
        self.to_entry.pack(side=LEFT, padx=(self.px(6), 0))
        self.from_entry.bind('<Return>', lambda _e: self._auto_list_boards())
        self.to_entry.bind('<Return>', lambda _e: self._auto_list_boards())
        self.board_list_button = ttk.Button(range_row, text='列出板书',
                                            style='Secondary.TButton',
                                            command=self.on_list_boards,
                                            state='disabled')
        self.board_list_button.pack(side=RIGHT, padx=(self.px(16), 0))

        # 状态说明单独占一行：它时不时会变长（例如提示可能挤下线客户端），
        # 塞进查询行里就会把按钮顶出可视区域，只剩半截字。
        self.hint_label = ttk.Label(
            toolbar_body, textvariable=self.client_hint,
            style='SurfaceHint.TLabel', justify=LEFT,
            wraplength=self.px(520))
        self.hint_label.pack(fill=X, pady=(self.px(6), 0))

        def _fit_hint(event) -> None:
            width = max(self.px(240), int(event.width) - self.px(36))
            current = int(self.hint_label.cget('wraplength') or 0)
            if abs(width - current) > self.px(8):
                self.hint_label.configure(wraplength=width)

        toolbar_body.bind('<Configure>', _fit_hint)

        # 账号密码输入行：只有真的要账号密码时才出现，免密码模式不占地方
        self.account_row = ttk.Frame(toolbar_body, style='Surface.TFrame')
        ttk.Label(self.account_row, text='账号',
                  style='SurfaceMuted.TLabel').pack(side=LEFT)
        self.name_entry = ttk.Entry(self.account_row,
                                    textvariable=self.login_name, width=15)
        self.name_entry.pack(side=LEFT, padx=(self.px(8), self.px(14)))
        ttk.Label(self.account_row, text='密码',
                  style='SurfaceMuted.TLabel').pack(side=LEFT)
        self.pass_entry = ttk.Entry(self.account_row,
                                    textvariable=self.login_pass,
                                    width=15, show='*')
        self.pass_entry.pack(side=LEFT, padx=(self.px(8), self.px(14)))
        self.login_button = ttk.Button(self.account_row, text='登录',
                                       style='Secondary.TButton',
                                       command=self.on_login)
        self.login_button.pack(side=LEFT)
        self.name_entry.bind('<Return>',
                             lambda _e: self.pass_entry.focus_set())
        self.pass_entry.bind('<Return>', lambda _e: self.on_login())

        # 进度改成独立弹窗，卡片里不再留进度行
        self.board_progress = ProgressPanel(board, self.on_cancel,
                                            compact=False)

        list_head = ttk.Frame(board, style='Card.TFrame')
        list_head.grid(row=3, column=0, sticky='ew',
                       pady=(self.px(12), self.px(6)))
        ttk.Label(list_head, text='课堂',
                  style='SectionSmall.TLabel').pack(side=LEFT)
        ttk.Label(list_head, textvariable=self.board_meta,
                  style='Faint.TLabel').pack(side=LEFT, padx=(12, 0))
        self.clear_button = ttk.Button(list_head, text='清空',
                                       style='Quiet.TButton',
                                       command=self.on_clear_boards,
                                       state='disabled')
        self.clear_button.pack(side=RIGHT, padx=(8, 0))
        self.select_all_button = ttk.Button(list_head, text='全选',
                                            style='Quiet.TButton',
                                            command=self.on_select_all_boards,
                                            state='disabled')
        self.select_all_button.pack(side=RIGHT, padx=(8, 0))

        # 班级做成横排按钮（和作业页一致），点一下换班
        self.board_class_bar = ttk.Frame(board, style='Card.TFrame')
        self.board_class_bar.grid(row=4, column=0, sticky='ew',
                                  pady=(0, self.px(6)))
        self.board_class_buttons: dict[str, tk.Button] = {}

        self.tree_wrap = tk.Frame(board, bg=CARD, bd=0,
                                  highlightbackground=BORDER,
                                  highlightthickness=1)
        # 列表用 grid 抢剩余高度，日志展开时缩的是日志还是列表由权重决定
        board.columnconfigure(0, weight=1)
        board.rowconfigure(5, weight=1)
        self.tree_wrap.grid(row=5, column=0, sticky='nsew')
        self.board_tree = ttk.Treeview(
            self.tree_wrap, columns=('title', 'date'),
            show='tree headings', height=8, selectmode='none',
            style='Board.Treeview')
        self.board_tree.heading('#0', text='选择', anchor='center')
        self.board_tree.heading('title', text='课堂', anchor='w')
        self.board_tree.heading('date', text='日期', anchor='center')
        self.board_tree.column('#0', width=self.px(72),
                               minwidth=self.px(72), anchor='center',
                               stretch=False)
        self.board_tree.column('title', width=self.px(780),
                               minwidth=self.px(380), anchor='w')
        self.board_tree.column('date', width=self.px(160),
                               minwidth=self.px(132), anchor='center',
                               stretch=False)
        self.board_tree.tag_configure('unchecked', background=CARD,
                                      foreground=TEXT_DARK)
        self.board_tree.tag_configure('checked', background=CHECKED_BG,
                                      foreground=TEXT_DARK)
        board_scroll = ttk.Scrollbar(self.tree_wrap, orient='vertical',
                                     command=self.board_tree.yview,
                                     style='Vertical.TScrollbar')
        self.board_tree.configure(yscrollcommand=board_scroll.set)
        self.board_tree.pack(side=LEFT, fill=BOTH, expand=True)
        board_scroll.pack(side=RIGHT, fill=Y)
        self.board_tree.bind('<Button-1>', self._on_board_click)

        board_row = ttk.Frame(board, style='Card.TFrame')
        board_row.grid(row=6, column=0, sticky='ew', pady=(self.px(10), 0))
        self.selection_badge = tk.Label(
            board_row, text='已选 0 节', bg=CARD, fg=TEXT_MUTED,
            font=(FONT, 10))
        self.selection_badge.pack(side=LEFT)
        ttk.Label(board_row, text='点整行勾选 · 换班级不影响已勾选',
                  style='Faint.TLabel').pack(side=LEFT, padx=(12, 0))
        self.board_all_button = ttk.Button(
            board_row, text='导出所选板书', style='Primary.TButton',
            command=self.on_export_boards, state='disabled')
        self.board_all_button.pack(side=RIGHT)

        self.board_log = LogPanel(
            board, title='板书运行日志',
            subtitle='登录、清单与导出诊断', height=5,
            collapsible=True, expanded=False, px=self.px,
            on_toggle=self.on_toggle_board_log)
        self.board_log.frame.grid(row=7, column=0, sticky='ew',
                                  pady=(self.px(10), 0))
        # 窗口被拖矮时把日志收起来，给列表腾高度
        board.bind('<Configure>', self._fit_board_layout)

        # ---------- 作业视图 ----------
        homework_outer, home = self._card(content, padding=self.px(14))
        homework_outer.grid(row=0, column=0, sticky='nsew')
        homework_outer.grid_remove()
        self.homework_view = homework_outer

        home_head = ttk.Frame(home, style='Card.TFrame')
        home_head.grid(row=0, column=0, sticky='ew')
        ttk.Label(home_head, text='作业导出',
                  style='Section.TLabel').pack(side=LEFT)
        ttk.Label(home_head,
                  text='按班级分组 · 一个作业框一个 CSV（与爱问云统计导出同格式）',
                  style='Muted.TLabel').pack(side=LEFT, anchor='s',
                                             padx=(self.px(12), 0),
                                             pady=(0, self.px(3)))
        self.homework_badge = tk.Label(
            home_head, text='○ 等待登录态', bg=CARD, fg=TEXT_MUTED,
            font=(FONT, 10))
        self.homework_badge.pack(side=RIGHT)

        home_toolbar = tk.Frame(home, bg=SURFACE, bd=0,
                                highlightbackground=BORDER,
                                highlightthickness=1)
        home_toolbar.grid(row=1, column=0, sticky='ew', pady=(self.px(14), 0))
        home_body = ttk.Frame(home_toolbar, style='Surface.TFrame',
                              padding=(self.px(16), self.px(10)))
        home_body.pack(fill=X)

        home_range_row = ttk.Frame(home_body, style='Surface.TFrame')
        home_range_row.pack(fill=X)
        ttk.Label(home_range_row, text='时间段',
                  style='SurfaceMuted.TLabel').pack(side=LEFT)
        self.homework_range_box = ttk.Combobox(
            home_range_row, textvariable=self.homework_range,
            values=list(RANGE_PRESETS), state='readonly', width=12)
        self.homework_range_box.pack(side=LEFT,
                                     padx=(self.px(8), self.px(18)))
        self.homework_range_box.bind(
            '<<ComboboxSelected>>', lambda _e: self._auto_list_homeworks())
        tk.Frame(home_range_row, bg=BORDER_STRONG, width=1).pack(
            side=LEFT, fill=Y, pady=self.px(4), padx=(0, self.px(18)))
        ttk.Label(home_range_row, text='自定义日期',
                  style='SurfaceMuted.TLabel').pack(side=LEFT)
        self.homework_from_entry = ttk.Entry(
            home_range_row, textvariable=self.homework_from, width=11)
        self.homework_from_entry.pack(side=LEFT, padx=(self.px(8), self.px(6)))
        ttk.Label(home_range_row, text='至',
                  style='SurfaceMuted.TLabel').pack(side=LEFT)
        self.homework_to_entry = ttk.Entry(
            home_range_row, textvariable=self.homework_to, width=11)
        self.homework_to_entry.pack(side=LEFT, padx=(self.px(6), 0))
        self.homework_from_entry.bind(
            '<Return>', lambda _e: self._auto_list_homeworks())
        self.homework_to_entry.bind(
            '<Return>', lambda _e: self._auto_list_homeworks())
        self.homework_list_button = ttk.Button(
            home_range_row, text='列出作业', style='Secondary.TButton',
            command=self.on_list_homeworks, state='disabled')
        self.homework_list_button.pack(side=RIGHT, padx=(self.px(16), 0))

        self.homework_hint = ttk.Label(
            home_body, textvariable=self.client_hint,
            style='SurfaceHint.TLabel', justify=LEFT,
            wraplength=self.px(520))
        self.homework_hint.pack(fill=X, pady=(self.px(6), 0))

        # 进度用独立弹窗，不再占卡片里的位置
        self.homework_progress = ProgressPanel(home, self.on_cancel,
                                               compact=False)

        home_list_head = ttk.Frame(home, style='Card.TFrame')
        home_list_head.grid(row=3, column=0, sticky='ew',
                            pady=(self.px(12), self.px(6)))
        ttk.Label(home_list_head, text='作业框',
                  style='SectionSmall.TLabel').pack(side=LEFT)
        ttk.Label(home_list_head, textvariable=self.homework_meta,
                  style='Faint.TLabel').pack(side=LEFT, padx=(self.px(12), 0))
        self.homework_clear_button = ttk.Button(
            home_list_head, text='清空', style='Quiet.TButton',
            command=self.on_clear_homeworks, state='disabled')
        self.homework_clear_button.pack(side=RIGHT, padx=(self.px(8), 0))
        self.homework_select_button = ttk.Button(
            home_list_head, text='全选', style='Quiet.TButton',
            command=self.on_select_all_homeworks, state='disabled')
        self.homework_select_button.pack(side=RIGHT, padx=(self.px(8), 0))

        # 班级做成横排按钮：竖着的分组行不好找班，横着最多摆 8 个
        self.homework_class_bar = ttk.Frame(home, style='Card.TFrame')
        self.homework_class_bar.grid(row=4, column=0, sticky='ew',
                                     pady=(0, self.px(6)))
        self.homework_class_buttons: dict[str, tk.Button] = {}

        home.columnconfigure(0, weight=1)
        home.rowconfigure(5, weight=1)
        self.homework_tree_wrap = tk.Frame(home, bg=CARD, bd=0,
                                           highlightbackground=BORDER,
                                           highlightthickness=1)
        self.homework_tree_wrap.grid(row=5, column=0, sticky='nsew')
        self.homework_tree = ttk.Treeview(
            self.homework_tree_wrap, columns=('title', 'progress', 'date'),
            show='tree headings', height=8, selectmode='none',
            style='Board.Treeview')
        self.homework_tree.heading('#0', text='选择', anchor='center')
        self.homework_tree.heading('title', text='作业框', anchor='w')
        self.homework_tree.heading('progress', text='提交 / 批改',
                                   anchor='center')
        self.homework_tree.heading('date', text='布置时间', anchor='center')
        self.homework_tree.column('#0', width=self.px(72),
                                  minwidth=self.px(72), anchor='center',
                                  stretch=False)
        self.homework_tree.column('title', width=self.px(700),
                                  minwidth=self.px(380), anchor='w')
        self.homework_tree.column('progress', width=self.px(140),
                                  minwidth=self.px(120), anchor='center',
                                  stretch=False)
        self.homework_tree.column('date', width=self.px(160),
                                  minwidth=self.px(132), anchor='center',
                                  stretch=False)
        for tag, bg, fg in (('unchecked', CARD, TEXT_DARK),
                            ('checked', CHECKED_BG, TEXT_DARK)):
            self.homework_tree.tag_configure(tag, background=bg,
                                             foreground=fg,
                                             font=(FONT, 10))
        homework_scroll = ttk.Scrollbar(self.homework_tree_wrap,
                                        orient='vertical',
                                        command=self.homework_tree.yview,
                                        style='Vertical.TScrollbar')
        self.homework_tree.configure(yscrollcommand=homework_scroll.set)
        self.homework_tree.pack(side=LEFT, fill=BOTH, expand=True)
        homework_scroll.pack(side=RIGHT, fill=Y)
        self.homework_tree.bind('<Button-1>', self._on_homework_click)

        home_row = ttk.Frame(home, style='Card.TFrame')
        home_row.grid(row=6, column=0, sticky='ew', pady=(self.px(10), 0))
        self.homework_badge_count = tk.Label(
            home_row, text='已选 0 个', bg=CARD, fg=TEXT_MUTED,
            font=(FONT, 10))
        self.homework_badge_count.pack(side=LEFT)
        ttk.Label(home_row, text='点整行勾选 · 换班级不影响已勾选 · '
                                 '直接输出 CSV，不套文件夹',
                  style='Faint.TLabel').pack(side=LEFT, padx=(12, 0))
        self.homework_export_button = ttk.Button(
            home_row, text='导出所选作业', style='Primary.TButton',
            command=self.on_export_homeworks, state='disabled')
        self.homework_export_button.pack(side=RIGHT)

        self.homework_log = LogPanel(
            home, title='作业运行日志',
            subtitle='清单、成绩与导出诊断', height=5,
            collapsible=True, expanded=False, px=self.px,
            on_toggle=self.on_toggle_homework_log)
        self.homework_log.frame.grid(row=7, column=0, sticky='ew',
                                     pady=(self.px(10), 0))

        # ---------- 状态 / 页脚 ----------
        # 先占住窗口底部，再让内容区拿剩余高度：窗口再矮，底部主按钮
        # 和状态行也不会被列表挤出去。
        footer = ttk.Frame(outer)
        status_row = ttk.Frame(outer)
        footer.pack(side='bottom', fill=X, pady=(self.px(4), 0),
                    before=self.content)
        status_row.pack(side='bottom', fill=X, pady=(self.px(8), 0),
                        before=self.content)
        ttk.Label(status_row, textvariable=self.status,
                  style='Status.TLabel').pack(side=LEFT)
        ttk.Button(footer, text='使用说明', style='FooterQuiet.TButton',
                   command=self.show_help).pack(side=LEFT)
        self.open_button = ttk.Button(footer, text='打开上次文件',
                                      style='FooterQuiet.TButton',
                                      command=self.open_last,
                                      state='disabled')
        self.open_button.pack(side=LEFT, padx=(self.px(4), 0))
        ttk.Label(footer, text='保存位置：桌面「爱问云邀请码」'
                               '·「爱问云板书导出」·「爱问云作业统计」',
                  style='Footer.TLabel').pack(side=RIGHT)

        self._base_title = '%s %s' % (APP_NAME, APP_VERSION)
        self.export_button.focus_set()
        self._switch_view('invite')
        self.append_log('就绪：正在检测本机已登录的爱问云；识别到后可直接列出板书，'
                        '不需要账号密码。未匹配到班级的条目→杂项/未分类。',
                        target='invite')

    def _switch_view(self, view: str) -> None:
        """在三个功能页之间切换。"""

        if view not in ('invite', 'board', 'homework'):
            return
        self.view = view
        for key, (button, underline) in self.tab_buttons.items():
            active = key == view
            button.configure(
                fg=TEXT_DARK if active else TEXT_MUTED,
                font=(FONT, 12, 'bold') if active else (FONT, 12))
            underline.configure(bg=ACCENT if active else BG)
        for key, widget in (('invite', self.invite_view),
                            ('board', self.board_view),
                            ('homework', self.homework_view)):
            if key == view:
                widget.grid(row=0, column=0, sticky='nsew')
            else:
                widget.grid_remove()

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
        self._busy_kind = 'invite'
        self.invite_running = True
        self.board_progress.hide()
        self.invite_progress.show()
        self.invite_progress.begin(
            '正在导出邀请码', '正在读取本机登录态…',
            indeterminate=True, hint='导出期间可以继续切到板书页')
        self.set_busy(True)
        self.clear_log('invite')
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
            # 仅当板书区确实用了账号登录会话时，邀请码导出才复用它；
            # 客户端免密码模式下仍走原来的只读内存扫描。
            preferred_token=(self.login_result.token
                             if self.board_origin == 'account'
                             and self.login_result else ''),
            renew_token=(self._renew_token
                         if self.board_origin == 'account' and self.login_result
                         else None),
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

    # ---------- 登录态：优先本机已登录客户端 ----------
    def _has_board_auth(self) -> bool:
        return bool(self.board_token)

    def _client_mode(self) -> bool:
        return (self.board_origin == 'client' and bool(self.board_token)
                and not self._force_account)

    def _board_session(self) -> ApiSession:
        return ApiSession(DEFAULT_BASE_URL, token=self.board_token,
                          platform='ai')

    def _start_client_scan(self, manual: bool = False) -> None:
        """只读检测本机已登录的爱问云客户端，拿到 token 就免密码使用。"""

        if self._client_scanning or self.busy.get():
            return
        if self.board_origin == 'account' and not manual:
            return
        if self.board_origin == 'account' and manual:
            if not messagebox.askyesno(
                    APP_NAME,
                    '重新检测会优先读取本机爱问云登录态。\n\n'
                    '如果电脑客户端没有登录，仍可继续使用账号密码。继续吗？'):
                return
        self._client_scanning = True
        self._scan_manual = bool(manual)
        self._force_account = False
        self.client_hint.set('正在只读检测本机已登录的爱问云客户端，'
                             '不需要输入账号密码…')
        self.append_log('正在检测本机爱问云登录态（只读扫描，不登录、不挤下线）…',
                        target='board')
        self._refresh_auth_ui()
        threading.Thread(target=self._client_scan_work, daemon=True).start()

    def _client_scan_work(self) -> None:
        if sys.platform != 'win32':
            self._post('client_auth', {
                'token': '',
                'reason': 'macOS 请使用账号密码登录（需要重启客户端读取登录态）。'})
            return
        try:
            if not client_running():
                self._post('client_auth', {
                    'token': '',
                    'reason': '未检测到运行中的爱问云客户端（ai-yxt.exe）；'
                              '请先打开并登录电脑客户端，或改用账号密码。'})
                return
            token = scan_token()
        except Exception as error:  # noqa: BLE001
            self._post('client_auth', {'token': '', 'reason': str(error)})
            return
        self._post('client_auth', {'token': token, 'reason': ''})

    def _client_auth_done(self, payload: dict) -> None:
        self._client_scanning = False
        manual = self._scan_manual
        self._scan_manual = False
        token = str((payload or {}).get('token') or '')
        reason = str((payload or {}).get('reason') or '')
        if token:
            if self.board_origin == 'account' and not manual:
                self._refresh_auth_ui()
                return          # 已有账号会话：忽略启动时迟到的检测结果
            self.board_token = token
            self.board_origin = 'client'
            self._force_account = False
            self.client_hint.set('已连上爱问云：正在自动列出所选时间段的板书，'
                                 '不需要再点「列出板书」。')
            self.append_log('已识别本机爱问云登录态：免密码使用，'
                            '不会把电脑客户端挤下线。', target='board')
            self.status.set('就绪：已识别本机登录态，正在列出板书…')
            self._refresh_auth_ui()
            # 连上就直接列一遍，省掉「先点列出板书再等」这一步；
            # 手动重新检测时也重列一次，保证看到的是最新清单。
            self.root.after(120, lambda: self._auto_list_if_empty(manual))
            return
        if self.board_origin != 'account':
            self.board_token = ''
            self.board_origin = ''
        self.client_hint.set(reason or '未检测到本机登录态，请用账号密码登录。')
        self.append_log('未检测到可用的本机登录态：%s' % (reason or '未知原因'),
                        target='board')
        if manual:
            self._expand_board_log_quietly()
        self._refresh_auth_ui()

    def _refresh_auth_ui(self) -> None:
        """统一刷新登录态徽标、账号输入框和板书按钮。"""

        if not hasattr(self, 'login_badge'):
            return
        has_auth = self._has_board_auth()
        client_mode = self._client_mode()
        busy = self.busy.get()
        if self._client_scanning and not has_auth:
            text, fg = '○ 正在检测本机登录…', TEXT_FAINT
        elif client_mode:
            text, fg = '● 已识别本机登录 · 免密码', ACCENT_DARK
        elif has_auth:
            who = ''
            if self.login_result is not None:
                who = self.login_result.name or self.login_result.login_name or ''
            text = ('● 已登录 · %s' % who) if who else '● 已登录'
            fg = ACCENT_DARK
        else:
            text, fg = '○ 未登录', TEXT_MUTED
        self.login_badge.configure(text=text, bg=CARD, fg=fg)

        # 客户端登录态可用时把账号密码输入框锁上，避免误登录挤掉客户端；
        # 首次检测期间也先锁上，等结果出来再决定。
        account_enabled = (not busy and not client_mode
                           and not (self._client_scanning and not has_auth))
        state = 'normal' if account_enabled else 'disabled'
        self.name_entry.configure(state=state)
        self.pass_entry.configure(state=state)
        self.login_button.configure(
            state=state,
            text='登录中…' if (busy and self._busy_kind == 'login')
            else ('免密码' if client_mode else '登录'))
        self.rescan_button.configure(
            state='disabled' if (busy or self._client_scanning) else 'normal')

        # 免密码模式下整行账号密码都不出现（登录态已由页头的徽标说明），
        # 只有真要账号密码时才把这一行加进查询区。
        if client_mode:
            if self.account_row.winfo_manager():
                self.account_row.pack_forget()
        elif not self.account_row.winfo_manager():
            self.account_row.pack(fill=X, pady=(self.px(10), 0),
                                  before=self.hint_label)

        if client_mode and not busy:
            if not self.use_account_link.winfo_manager():
                self.use_account_link.pack(side=LEFT, padx=(self.px(12), 0))
        elif self.use_account_link.winfo_manager():
            self.use_account_link.pack_forget()

        self.board_list_button.configure(
            state='normal' if (has_auth and not busy) else 'disabled')
        self._refresh_board_state()
        self._refresh_homework_badge(has_auth, client_mode)
        self._refresh_homework_state()

    def _refresh_homework_badge(self, has_auth: bool, client_mode: bool) -> None:
        """作业页右上角的登录态徽标（和板书页保持同一套说法）。"""

        badge = getattr(self, 'homework_badge', None)
        if badge is None:
            return
        if self._client_scanning and not has_auth:
            text, fg = '○ 正在检测本机登录…', TEXT_FAINT
        elif client_mode:
            text, fg = '● 已识别本机登录 · 免密码', ACCENT_DARK
        elif has_auth:
            text, fg = '● 已登录', ACCENT_DARK
        else:
            text, fg = '○ 未登录', TEXT_MUTED
        badge.configure(text=text, bg=CARD, fg=fg)

    def _enable_account_login(self) -> None:
        """用户主动选择账号密码登录（会提示可能挤掉电脑客户端）。"""

        if self.busy.get():
            return
        self._force_account = True
        self.board_token = ''
        self.board_origin = ''
        self.client_hint.set('将改用账号密码登录；同一账号登录可能使电脑上的'
                             '爱问云客户端下线。')
        self.append_log('已切换为账号密码登录（可能挤掉电脑客户端）。',
                        target='board')
        self._refresh_auth_ui()
        self.name_entry.focus_set()

    def on_login(self) -> None:
        if self.busy.get():
            return
        name = self.login_name.get().strip()
        password = self.login_pass.get()
        if not name or not password:
            messagebox.showwarning(APP_NAME, '请输入账号和密码。')
            return
        self._busy_kind = 'login'
        self.set_busy(True)
        self.board_progress.show()
        self.board_progress.begin(
            '正在登录爱问云', '账号密码只在本进程内存中使用，不写日志、不落盘…',
            indeterminate=True, cancellable=False,
            hint='登录成功后会自动列出所选时间段的板书')
        self.status.set('正在登录…')
        self.append_log('正在用账号密码登录爱问云（密码只在本进程内存中使用）…',
                        target='board')
        self.worker = threading.Thread(target=self._login_work,
                                       args=(name, password), daemon=True)
        self.worker.start()

    def _login_work(self, name: str, password: str) -> None:
        try:
            result = do_login(name, password, mytype='teacher',
                              base_url=DEFAULT_BASE_URL)
        except LoginError as error:
            self._post('login_error', str(error))
            return
        except Exception as error:  # noqa: BLE001
            self._post('login_error', '%s: %s' % (type(error).__name__, error))
            return
        self._post('login_ok', (name, password, result))

    def _login_done(self, name: str, password: str,
                    result: LoginResult) -> None:
        self.login_result = result
        self.board_token = result.token
        self.board_origin = 'account'
        self._force_account = False
        self._credentials = (name, password)
        self.set_busy(False)
        self.board_progress.hide()
        who = result.name or name
        org = '（%s）' % result.org_name if result.org_name else ''
        self.client_hint.set('已用账号登录；同一账号可能使电脑客户端下线。')
        self.status.set('登录成功：选时间段后点「列出板书」。')
        self.board_list_button.focus_set()
        self.append_log('登录成功：%s%s' % (who, org), target='board')
        self.on_list_boards()

    def _relogin(self) -> bool:
        """token 失效时用内存里的账号密码重登一次（工作线程调用）。"""

        name, password = self._credentials
        if not name or not password:
            return False
        try:
            result = do_login(name, password, mytype='teacher',
                              base_url=DEFAULT_BASE_URL)
        except Exception:  # noqa: BLE001
            return False
        self.login_result = result
        self.board_token = result.token
        self.board_origin = 'account'
        return True

    def _renew_board_token(self) -> bool:
        """板书 token 过期：客户端模式重扫本机，账号模式重新登录。"""

        if self.board_origin == 'client':
            if sys.platform != 'win32':
                return False
            try:
                token = scan_token()
            except Exception:  # noqa: BLE001
                return False
            if token:
                self.board_token = token
                return True
            return False
        return self._relogin()

    def _renew_token(self) -> str:
        """邀请码导出期间 token 失效：用界面账号重新登录取新会话。"""

        if not self._relogin():
            raise RuntimeError('界面账号重新登录失败')
        return self.login_result.token

    # ---------- 板书导出（先列出勾选，再导出所选） ----------
    def _auto_list_boards(self) -> None:
        """时间段变更后的自动刷新：已有登录态即重新列出；忙碌则记下待刷新。"""

        if not self._has_board_auth():
            return
        if self.busy.get():
            self._pending_list = True
            return
        self.on_list_boards()

    def _auto_list_if_empty(self, force: bool = False) -> None:
        """连上登录态后自动列一次板书，随后接着列一次作业框。

        首次连上、清单还是空的，就直接把时间段内的板书拉出来，用户不用
        先点「列出板书」；手动重新检测（``force``）则强制刷新一次。
        两个清单共用同一个忙碌标记，所以作业框等板书列完再开始。
        """

        if not self._has_board_auth():
            return
        if self.busy.get():
            self.root.after(400, lambda: self._auto_list_if_empty(force))
            return
        if not self.board_records or force:
            self._pending_homework_auto = True
            self.on_list_boards()
            return
        if not self.homework_items or force:
            self.on_list_homeworks()

    def on_list_boards(self) -> None:
        """按所选时间段列出板书清单（不下载板书，仅取课堂列表）。"""

        if self.busy.get():
            return
        if not self._has_board_auth():
            messagebox.showwarning(
                APP_NAME,
                '还没有可用的登录态。\n\n'
                '请确认电脑上的爱问云已登录并点「重新检测」；'
                '或点「改用账号密码登录」。')
            return
        try:
            from_ms, to_ms = resolve_range(
                self.board_range.get(), self.board_from.get().strip(),
                self.board_to.get().strip())
        except ValueError as error:
            messagebox.showwarning(APP_NAME, str(error))
            return
        self.cancel_event = threading.Event()
        self._busy_kind = 'board'
        self.set_busy(True)
        self.board_progress.show()
        self.board_progress.begin(
            '正在列出板书', '正在读取课堂与班级信息…',
            indeterminate=True, hint='只读清单，不下载板书页')
        self.status.set('正在列出板书…')
        span = '%s ~ %s' % (
            datetime.datetime.fromtimestamp(from_ms / 1000).strftime('%Y-%m-%d'),
            datetime.datetime.fromtimestamp(to_ms / 1000).strftime('%Y-%m-%d'))
        self.append_log('列出板书（%s，%s）…' % (self.board_range.get(), span),
                        target='board')
        self.worker = threading.Thread(
            target=self._list_boards_work,
            args=(from_ms, to_ms), daemon=True)
        self.worker.start()

    def _list_boards_work(self, from_ms: int, to_ms: int) -> None:
        def check_cancel() -> bool:
            if self.cancel_event.is_set():
                self._post('canceled', '已取消列出板书。')
                return True
            return False

        for attempt in range(2):    # token 失效自动重取一次
            if check_cancel():
                return
            session = self._board_session()
            class_names: list[str] = []
            self._thread_log('正在读取爱问云班级列表…')
            try:
                class_names = fetch_class_names(session)
                if check_cancel():
                    return
                if class_names:
                    self._thread_log('已读取爱问云班级列表：%d 个班级。'
                                     % len(class_names))
                else:
                    self._thread_log('未读取到班级列表，将按课堂名识别班级。')
            except Exception as error:  # noqa: BLE001
                # 班级列表只是展示分组用；读取失败不影响继续列出板书。
                self._thread_log('班级列表读取失败（%s），将按课堂名识别班级。'
                                 % error)
            try:
                records = fetch_records(session, from_ms, to_ms)
                if check_cancel():
                    return
                # 用 liveInfo 的 groups 把课堂精确映射到官方班级
                # （26秋7MA+31 -> 2026027MA+31β1/β3），再合并 α/β 变体。
                self._thread_log('正在读取课堂对应的班级…')
                try:
                    enrich_record_groups(session, records)
                except TokenExpired:
                    raise
                except Exception as error:  # noqa: BLE001
                    self._thread_log('班级映射读取失败（%s），将按课堂名匹配。'
                                     % error)
                else:
                    mapped = sum(1 for r in records if r.get('_groupNames'))
                    self._thread_log('班级映射完成：%d / %d 节课对应到官方班级。'
                                     % (mapped, len(records)))
                if check_cancel():
                    return
            except TokenExpired:
                if attempt == 0 and self._renew_board_token():
                    self._thread_log('登录态已过期，已自动重新获取，继续…')
                    continue
                self._post(
                    'board_auth_lost',
                    '登录态已失效：请确认电脑上的爱问云仍处于登录状态后'
                    '点「重新检测」，或改用账号密码登录。')
                return
            except BatchCancelled as error:
                self._post('canceled', str(error))
                return
            except Exception as error:  # noqa: BLE001
                self._post('error', '%s: %s' % (type(error).__name__, error))
                return
            if check_cancel():
                return
            records = sort_records_newest_first(records)
            self._post('board_listed',
                       {'records': records, 'class_names': class_names})
            return

    def _boards_listed(self, payload) -> None:
        """列出完成：按班级分组，班级做成横排按钮，列表只显示当前班级。"""

        if isinstance(payload, dict):
            records = list(payload.get('records') or [])
            class_names = list(payload.get('class_names') or [])
        else:                       # 兼容旧的纯记录列表调用
            records = list(payload or [])
            class_names = []

        self.board_records = records
        self.board_class_names = [str(name).strip() for name in class_names
                                  if str(name).strip()]
        self.board_group_of = {}
        self.board_tree.delete(*self.board_tree.get_children())

        groups = group_board_records(records, self.board_class_names)
        ordered_names = ordered_group_names(groups, records)
        self.board_groups = {name: [records[index] for index in groups[name]]
                             for name in ordered_names}
        for name, rows in self.board_groups.items():
            for record in rows:
                self.board_group_of[id(record)] = name
        self.board_checked = {self._board_key(record): False
                              for record in records}
        self.board_active_class = (ordered_names[0] if ordered_names else '')
        self._build_board_class_buttons()
        self._show_board_class(self.board_active_class)

        official_count = len(ordered_names) - (
            1 if UNCLASSIFIED_GROUP in groups else 0)
        misc_count = len(groups.get(UNCLASSIFIED_GROUP, []))
        if official_count:
            meta = '共 %d 节课 · %d 个班级' % (len(records), official_count)
        else:
            meta = '共 %d 节课' % len(records)
        if misc_count:
            meta += ' · 杂项 %d 节' % misc_count
        meta += ' · 最新在上'
        self.board_meta.set(meta if records else '该时段没有课堂')
        self._refresh_board_state()

        self.set_busy(False)
        self.board_progress.hide()
        if not records:
            self.client_hint.set('该时间段没有课堂：换个时间段（如「最近30天」）'
                                 '就会自动重列。')
            self.status.set('该时段没有课堂：换个时间段（如「最近30天」）再列出。')
            self.append_log('该时段没有课堂；请换个时间段（如「最近30天」）再试。',
                            target='board')
        else:
            self.client_hint.set('已自动列出 %d 节课；点上面的班级按钮换班，'
                                 '勾选后点「导出所选板书」。' % len(records))
            if official_count:
                self.status.set(
                    '共 %d 节课（%d 个班级%s），最新在上；勾选后点「导出所选板书」'
                    % (len(records), official_count,
                       '、杂项 %d 节' % misc_count if misc_count else ''))
            else:
                self.status.set(
                    '共 %d 节课，均归入「杂项/未分类」，勾选后点「导出所选板书」'
                    % len(records))
            mapped = sum(1 for r in records if r.get('_groupNames'))
            if mapped:
                source = '课堂对应的官方班级'
            elif self.board_class_names:
                source = '爱问云班级列表'
            else:
                source = '课堂名兜底识别'
            if official_count:
                self.append_log(
                    '共 %d 节课可勾选；按%s分组：%d 个班级'
                    '（α/β1/β2/β3 已合并）%s；列表最新在上。'
                    % (len(records), source, official_count,
                       '，杂项/未分类 %d 节' % misc_count if misc_count else ''),
                    target='board')
            else:
                self.append_log(
                    '共 %d 节课可勾选；按%s未匹配到班级，已归入杂项/未分类。'
                    % (len(records), source), target='board')
        if self._pending_list:      # 列出期间用户又切换了时间段
            self._pending_list = False
            self.on_list_boards()
        elif getattr(self, '_pending_homework_auto', False):
            # 连上登录态后的自动流程：板书列完接着列作业框
            self._pending_homework_auto = False
            self.root.after(120, self.on_list_homeworks)

    def _board_key(self, record: dict) -> str:
        """课堂记录的唯一键（换班级后勾选不丢）。"""

        return str(record.get('id') or id(record))

    def _build_board_class_buttons(self) -> None:
        """横排班级按钮，最多显示 8 个班。"""

        for child in self.board_class_bar.winfo_children():
            child.destroy()
        self.board_class_buttons = {}
        self.board_class_labels = class_button_labels(list(self.board_groups))
        for name in list(self.board_groups)[:HOME_CLASS_LIMIT]:
            button = tk.Button(
                self.board_class_bar, text=self.board_class_labels[name],
                bd=0, relief='flat',
                highlightthickness=0, padx=self.px(14), pady=self.px(6),
                font=(FONT, 10), bg=SURFACE, fg=TEXT_DARK, cursor='hand2',
                activebackground=SURFACE_ALT, activeforeground=TEXT_DARK,
                command=lambda n=name: self._show_board_class(n))
            button.pack(side=LEFT, padx=(0, self.px(8)))
            self.board_class_buttons[name] = button
        rest = len(self.board_groups) - HOME_CLASS_LIMIT
        if rest > 0:
            tk.Label(self.board_class_bar, text='另有 %d 个班未显示' % rest,
                     bg=CARD, fg=TEXT_MUTED, font=(FONT, 9)).pack(
                         side=LEFT, padx=(self.px(4), 0))

    def _show_board_class(self, name: str) -> None:
        """切到某个班：表格里只列这个班的课堂。"""

        self.board_active_class = name
        self.board_tree.delete(*self.board_tree.get_children())
        for record in self.board_groups.get(name) or []:
            key = self._board_key(record)
            checked = bool(self.board_checked.get(key))
            icon = self._check_icon('checked' if checked else 'unchecked',
                                    leaf=True)
            opts = ({'image': icon, 'text': ''} if icon
                    else {'text': '☑' if checked else '☐'})
            created = record_time(record)
            day = (datetime.datetime.fromtimestamp(created / 1000)
                   .strftime('%Y-%m-%d') if created else '-')
            self.board_tree.insert(
                '', 'end', iid=key,
                values=(record.get('shortDesc') or '未命名课堂', day),
                tags=('checked' if checked else 'unchecked',), **opts)
        self._refresh_board_state()

    def on_toggle_board_log(self) -> None:
        """展开/收起板书日志；展开时只拿列表用不掉的高度。

        日志以前一展开就把列表压成一条表头，所以这里按列表当前高度
        换算日志能占几行：列表至少留两行，剩下的才是日志的。
        """

        log = self.board_log
        if log.expanded:
            log.set_expanded(False)
            return
        rows = self._board_log_rows()
        if rows < 2:
            self.status.set('窗口高度不够：先最大化窗口，再展开「板书运行日志」。')
            return
        log.text.configure(height=rows)
        log.set_expanded(True)
        self.root.update_idletasks()
        if self.board_tree.winfo_height() < self.px(30) + self.px(36) * 2:
            # 兜底：算漏了就退回收起状态，别把列表压没
            log.set_expanded(False)
            self.status.set('窗口高度不够：先最大化窗口，再展开「板书运行日志」。')

    def _expand_board_log_quietly(self) -> None:
        """出错时顺手展开日志；位置不够就不展开，不额外报警。"""

        if self.board_log.expanded:
            return
        rows = self._board_log_rows()
        if rows >= 2:
            self.board_log.text.configure(height=rows)
            self.board_log.set_expanded(True)

    def _board_log_rows(self) -> int:
        """列表之外还能给日志几行（2~6 行，0 表示放不下）。"""

        line_height = max(1, self._log_line_height(self.board_log))
        min_tree = self.px(30) + self.px(36) * 2        # 表头 + 两行
        # 展开时日志会多出标题下的间距和文本框内边距，先扣掉再算行数
        room = self.board_tree.winfo_height() - min_tree - self.px(28)
        return max(0, min(6, int(room // line_height)))

    def _log_line_height(self, log) -> int:
        """日志文本一行占多少像素（含行间距）。"""

        try:
            font = tkfont.Font(font=log.text.cget('font'))
            return int(font.metrics('linespace')) + self.px(4)
        except Exception:  # noqa: BLE001
            return self.px(22)

    def _fit_board_layout(self, event=None) -> None:
        """窗口被拖矮时优先保住板书列表，别让日志把列表挤成一条表头。

        展开日志时已经算过一次行数，但用户可能在日志展开的状态下把窗口
        拖小；这种情况下直接把日志收起来，列表才有行可以点。
        """

        log = getattr(self, 'board_log', None)
        if log is None or not log.expanded:
            return
        height = (event.height if event is not None
                  else self.board_view.winfo_height())
        if height < self.px(480):
            log.set_expanded(False)
            self.status.set('窗口太矮，已自动收起「板书运行日志」，给列表腾出高度。')

    def _refresh_board_state(self) -> None:
        """同步勾选计数、班级按钮高亮与导出按钮状态。"""

        checked_count = sum(1 for value in self.board_checked.values() if value)
        total_count = len(self.board_records)
        self.selection_badge.configure(
            text='已选 %d / %d 节' % (checked_count, total_count),
            bg=CARD, fg=ACCENT_DARK if checked_count else TEXT_MUTED,
            font=(FONT, 10, 'bold') if checked_count else (FONT, 10))

        for name, button in self.board_class_buttons.items():
            active = name == self.board_active_class
            rows = self.board_groups.get(name) or []
            picked = sum(1 for record in rows
                         if self.board_checked.get(self._board_key(record)))
            short = getattr(self, 'board_class_labels', {}).get(name, name)
            label = short if not picked else '%s · 已选 %d' % (short, picked)
            button.configure(
                text=label,
                bg=ACCENT if active else SURFACE,
                fg='#ffffff' if active else TEXT_DARK,
                activebackground=ACCENT_DARK if active else SURFACE_ALT,
                activeforeground='#ffffff' if active else TEXT_DARK)

        can_select = (self._has_board_auth() and not self.busy.get()
                      and bool(total_count))
        for button in (self.select_all_button, self.clear_button):
            button.configure(state='normal' if can_select else 'disabled')
        self.board_all_button.configure(
            state='normal' if (can_select and checked_count) else 'disabled')

    def _on_board_click(self, event) -> None:
        """点整行勾选/取消。"""

        if self.board_tree.identify_region(event.x, event.y) not in ('cell',
                                                                     'tree'):
            return
        iid = self.board_tree.identify_row(event.y)
        if not iid or self.busy.get():
            return
        self._toggle_board(iid)
        return 'break'

    def _toggle_board(self, iid: str) -> None:
        self.board_checked[iid] = not self.board_checked.get(iid, False)
        checked = bool(self.board_checked.get(iid))
        tags = ('checked' if checked else 'unchecked',)
        icon = self._check_icon('checked' if checked else 'unchecked',
                                leaf=True)
        if icon is not None:
            self.board_tree.item(iid, image=icon, text='', tags=tags)
        else:
            self.board_tree.item(iid, text='☑' if checked else '☐',
                                 tags=tags)
        self._refresh_board_state()

    def _checked_records(self) -> list[dict]:
        """当前勾选的板书记录（按列出顺序）。"""

        return [record for record in self.board_records
                if self.board_checked.get(self._board_key(record))]

    def on_select_all_boards(self) -> None:
        """全选 / 取消当前班级的课堂（其他班的勾选不动）。"""

        rows = self.board_groups.get(self.board_active_class) or []
        keys = [self._board_key(record) for record in rows]
        target = (not all(self.board_checked.get(key) for key in keys)
                  if keys else False)
        for key in keys:
            self.board_checked[key] = target
        self._show_board_class(self.board_active_class)
        self.status.set('已%s当前班级 %d 节'
                        % ('全选' if target else '取消全选', len(rows)))

    def on_clear_boards(self) -> None:
        for key in list(self.board_checked):
            self.board_checked[key] = False
        self._show_board_class(self.board_active_class)
        self.status.set('已清空勾选')

    # ---------- 导出所选 ----------
    def on_export_boards(self) -> None:
        """导出所选：只导出勾选的板书；未勾选直接拦截，不回退全量。"""

        if self.busy.get():
            return
        if not self._has_board_auth():
            messagebox.showwarning(APP_NAME, '还没有可用的登录态，请先重新检测或登录。')
            return
        records = self._checked_records()
        if not records:
            messagebox.showwarning(
                APP_NAME,
                '请先勾选要导出的板书。\n\n'
                '未勾选任何条目时不会导出（也不会整段全量导出）。')
            return
        # 批量下载：先选保存位置
        initial = default_out_root()
        initial_dir = initial if initial.exists() else Path.home() / 'Desktop'
        chosen = filedialog.askdirectory(
            title='选择板书保存位置', initialdir=str(initial_dir),
            mustexist=False)
        if not chosen:
            self.status.set('已取消：未选择保存位置。')
            return
        out_root = Path(chosen)
        sort_mode = ('type' if self.board_sort.get() == '按板书类型'
                     else 'time')
        self.cancel_event = threading.Event()
        self._busy_kind = 'board'
        self.set_busy(True)
        self.clear_log('board')
        self.board_progress.show()
        self.board_progress.begin(
            '正在导出板书', '正在读取每节课的板书清单…',
            indeterminate=True,
            hint='多节课并行导出 · 可折叠班级查看，不会中断')
        self.status.set('正在导出所选板书…')
        names = '、'.join((r.get('shortDesc') or '') for r in records[:3])
        if len(records) > 3:
            names += ' 等 %d 节' % len(records)
        self.append_log('导出所选 %d 节板书（%s，%s）'
                        % (len(records), names, self.board_sort.get()),
                        target='board')
        self.append_log('保存位置：%s' % out_root, target='board')
        self.worker = threading.Thread(
            target=self._board_work, args=(records, sort_mode, out_root),
            daemon=True)
        self.worker.start()

    def _group_for_record(self, record: dict) -> str:
        """板书导出时的班级文件夹名：与列表里看到的分组保持一致。"""

        group = self.board_group_of.get(id(record))
        return group or class_group(record.get('shortDesc') or '')

    def _board_work(self, records: list[dict], sort_mode: str,
                    out_root: Path) -> None:
        out_root.mkdir(parents=True, exist_ok=True)
        for attempt in range(2):    # token 失效自动重取一次
            session = self._board_session()
            try:
                result = export_records(
                    session, records, out_root, sort_mode,
                    logger=self._thread_log,
                    cancel_event=self.cancel_event,
                    group_of=self._group_for_record,
                    on_event=lambda payload: self._post(
                        'banshu_event', payload))
            except TokenExpired:
                if attempt == 0 and self._renew_board_token():
                    self._thread_log('登录态已过期，已自动重新获取，继续导出…')
                    continue
                self._post(
                    'board_auth_lost',
                    '登录态已失效：请确认电脑上的爱问云仍处于登录状态后'
                    '点「重新检测」，或改用账号密码登录。')
                return
            except BatchCancelled as error:
                self._post('canceled', str(error))
                return
            except Exception as error:  # noqa: BLE001
                self._post('error', '%s: %s' % (type(error).__name__, error))
                return
            self._post('banshu_done', {
                'groups': {group: [str(p) for p in paths]
                           for group, paths in result['groups'].items()},
                'failed': result['failed'],
                'out_root': str(out_root),
                'elapsed': result.get('elapsed'),
                'preset': '所选 %d 节' % len(records)})
            return

    def _thread_log(self, message: str) -> None:
        self._post('log', {'target': 'board', 'message': str(message)})

    def _post(self, kind: str, payload=None) -> None:
        self.ui_queue.put((kind, payload))

    def _tick_progress(self) -> None:
        """没有新事件时也让耗时继续走，进度面板不会像卡死。"""

        for panel in (getattr(self, 'invite_progress', None),
                      getattr(self, 'board_progress', None),
                      getattr(self, 'homework_progress', None)):
            if panel is not None:
                panel.tick()
        self.root.after(500, self._tick_progress)

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == 'phase':
                    text = str(payload)
                    self.status.set(text)
                    self.append_log('▶ ' + text, target='invite')
                    panel = self._active_progress()
                    if panel is not None:
                        panel.update(0, detail=text, indeterminate=True)
                elif kind == 'log':
                    if isinstance(payload, dict):
                        self.append_log(str(payload.get('message') or ''),
                                        target=payload.get('target'))
                    else:
                        self.append_log(str(payload))
                elif kind == 'progress':
                    done = int(payload.get('done', 0))
                    total = max(1, int(payload.get('total', 1)))
                    percent = done / total * 100.0
                    record = payload.get('record') or {}
                    topic = str(record.get('topic') or '')[:42]
                    self.invite_progress.update(
                        percent,
                        detail='正在取邀请码：%d / %d · %s'
                               % (done, total, topic or '…'))
                    self.status.set('正在取邀请码：%d/%d' % (done, total))
                    code = record.get('code')
                    if code:
                        self.append_log('[%d/%d] %s  →  %s'
                                        % (done, total, topic, code),
                                        target='invite')
                    else:
                        self.append_log('[%d/%d] %s  →  失败：%s' % (
                            done, total, topic, record.get('note') or ''),
                            target='invite')
                elif kind == 'done':
                    self.finish(payload)
                elif kind == 'login_ok':
                    name, password, result = payload
                    self._login_done(name, password, result)
                elif kind == 'login_error':
                    panel = self._active_progress()
                    self.set_busy(False)
                    self._expand_board_log_quietly()
                    if panel is not None:
                        panel.fail('登录失败', str(payload),
                                   hint='检查账号密码，或改用本机登录态',
                                   state='重试')
                    self.status.set('登录失败。')
                    self.append_log('登录失败：%s' % payload, target='board')
                    messagebox.showerror(APP_NAME, str(payload))
                elif kind == 'client_auth':
                    self._client_auth_done(payload)
                elif kind == 'board_auth_lost':
                    self.board_token = ''
                    self.board_origin = ''
                    panel = self._active_progress()
                    self.set_busy(False)
                    self._expand_board_log_quietly()
                    if panel is not None:
                        panel.fail('登录态已失效', str(payload),
                                   hint='点「重新检测」或改用账号密码登录',
                                   state='需重登')
                    message = str(payload)
                    self.client_hint.set(message)
                    self.status.set('登录态已失效：请重新检测或改用账号密码登录。')
                    self.append_log('登录态失效：' + message, target='board')
                    self._refresh_auth_ui()
                    messagebox.showerror(APP_NAME, message)
                elif kind == 'board_listed':
                    self._boards_listed(payload)
                elif kind == 'banshu_event':
                    self._on_board_event(payload)
                elif kind == 'banshu_progress':
                    # 兼容旧版 export_records 的逐节回调
                    done = int(payload.get('done', 0))
                    total = max(1, int(payload.get('total', 1)))
                    self.board_progress.update(
                        done / total * 100.0,
                        detail='正在导出板书：%d / %d 节 · %s'
                               % (done, total, payload.get('name') or ''))
                    self.status.set('正在导出板书：%d/%d（%s）' % (
                        done, total, payload.get('name', '')))
                elif kind == 'banshu_done':
                    self._finish_boards(payload)
                elif kind == 'homeworks_listed':
                    self._homeworks_listed(payload)
                elif kind == 'homework_event':
                    self._on_homework_event(payload)
                elif kind == 'homework_done':
                    self._finish_homeworks(payload)
                elif kind == 'homework_list_error':
                    panel = self._active_progress()
                    self.set_busy(False)
                    if panel is not None:
                        panel.fail('列出作业失败', str(payload),
                                   hint='确认客户端仍登录后点「重新检测」',
                                   state='失败')
                    self.status.set('列出作业失败。')
                    self.append_log('列出作业失败：%s' % payload,
                                    target='homework')
                    self._refresh_homework_state()
                elif kind == 'canceled':
                    panel = self._active_progress()
                    if self._busy_kind == 'invite':
                        self.invite_running = False
                    self.set_busy(False)
                    if self._busy_kind == 'board':
                        self._expand_board_log_quietly()
                    if panel is not None:
                        panel.fail('已取消', str(payload),
                                   hint='可以重新勾选后再导出',
                                   state='已取消')
                    self.status.set('已取消。')
                    self.append_log('已取消。')
                elif kind == 'error':
                    panel = self._active_progress()
                    if self._busy_kind == 'invite':
                        self.invite_running = False
                    self.set_busy(False)
                    if self._busy_kind == 'board':
                        self._expand_board_log_quietly()
                    message = str(payload)
                    if panel is not None:
                        panel.fail('导出失败', message,
                                   hint='详情见运行日志或弹窗',
                                   state='已结束')
                    self.status.set('导出失败，请看下方提示。')
                    self.append_log('失败：' + message)
                    messagebox.showerror(APP_NAME, message)
        except queue.Empty:
            pass
        except Exception as error:  # noqa: BLE001
            # 单个事件处理异常不能把进度刷新循环彻底停掉。
            try:
                self.append_log('界面事件处理失败：%s: %s'
                                % (type(error).__name__, error))
            except Exception:  # noqa: BLE001
                pass
        self.root.after(80, self._drain_queue)

    def _active_progress(self):
        if self._busy_kind == 'invite':
            return self.invite_progress
        if self._busy_kind in ('board', 'login'):
            return self.board_progress
        if self._busy_kind == 'homework':
            return self.homework_progress
        return None

    def _on_board_event(self, payload) -> None:
        """把导出线程的进度事件写进板书页自己的进度面板。"""

        if not isinstance(payload, dict):
            return
        phase = str(payload.get('phase') or '')
        done = int(payload.get('done', 0) or 0)
        total = max(1, int(payload.get('total', 1) or 1))
        percent = float(payload.get('percent', done / total * 100.0) or 0)
        name = str(payload.get('name') or '')
        pages_done = int(payload.get('pages_done') or 0)
        pages_total = int(payload.get('pages_total') or 0)
        active = int(payload.get('active') or 0)
        elapsed = payload.get('elapsed')
        if phase == 'start':
            detail = '第 %d / %d 节 · %s' % (
                int(payload.get('index') or 0), total, name)
            if active > 1:
                detail += ' · 同时 %d 节' % active
        elif phase == 'manifest':
            detail = '正在读取板书清单 · %s' % name
        elif phase == 'pages':
            detail = '正在下载并压缩板书页 · %s' % name
        elif phase == 'pdf':
            detail = '正在生成 PDF · %s' % name
        elif phase == 'write':
            detail = '正在写入 PDF · %s' % name
        elif phase in ('done', 'skipped'):
            detail = '已完成 %d / %d 节' % (done, total)
            if active > 1:
                detail += ' · 同时 %d 节' % active
        else:
            detail = name
        sub_detail = ''
        sub_percent = None
        if pages_total:
            sub_detail = '当前课堂 %d / %d 页' % (pages_done, pages_total)
            sub_percent = pages_done / pages_total * 100.0
        self.board_progress.update(
            percent, detail=detail, elapsed=elapsed,
            sub_detail=sub_detail, sub_percent=sub_percent,
            indeterminate=(phase in ('start', 'manifest')
                           and percent <= 0.01))
        if phase == 'pages' and pages_total:
            self.status.set('正在导出板书：已完成 %d / %d 节 · 当前 %d / %d 页'
                            % (done, total, pages_done, pages_total))
        elif phase in ('done', 'skipped'):
            self.status.set('正在导出板书：已完成 %d / %d 节' % (done, total))
        elif name:
            self.status.set('正在导出板书：已完成 %d / %d 节 · 正在处理 %s'
                            % (done, total, name))

    def finish(self, summary: ExportSummary) -> None:
        self.summary = summary
        self.last_path = summary.path
        self.invite_running = False
        self.set_busy(False)
        self.invite_progress.complete(
            '邀请码导出完成',
            '成功 %d / %d · 用时 %.1fs' % (
                summary.success, summary.total, summary.elapsed),
            hint='Excel 已保存，可点「打开上次文件」')
        self.status.set('完成：成功 %d / %d，用时 %.1fs' % (
            summary.success, summary.total, summary.elapsed))
        self.open_button.configure(state='normal')
        self.append_log('', target='invite')
        self.append_log('Excel：%s' % summary.path, target='invite')
        self.append_log('成功 %d / %d，失败 %d，用时 %.2fs' % (
            summary.success, summary.total, summary.failed, summary.elapsed),
            target='invite')
        if summary.conflicts:
            self.append_log('注意：%d 个课次有多个不同邀请码，已保留第一个。'
                            % len(summary.conflicts), target='invite')
        if summary.csv_path:
            self.append_log('CSV：%s' % summary.csv_path, target='invite')
        if summary.json_path:
            self.append_log('JSON：%s' % summary.json_path, target='invite')
        if self.open_after.get():
            try:
                open_path(summary.path)
            except OSError as error:
                self.append_log('未能自动打开 Excel：%s' % error,
                                target='invite')

    def on_cancel(self) -> None:
        if not self.busy.get():
            return
        self.cancel_event.set()
        self.status.set('正在取消…（等待当前请求结束）')
        self.append_log('已请求取消，正在等待进行中的请求结束…')

    def _finish_boards(self, payload: dict) -> None:
        """板书导出完成：分组汇总 + 打开输出文件夹。"""

        self.set_busy(False)
        # 导出结束后把日志展开，汇总就在板书页里；高度不够就保持收起，
        # 免得汇总把列表挤没（日志按钮上会显示未读条数）。
        self._expand_board_log_quietly()
        groups = payload.get('groups') or {}
        failed = payload.get('failed') or []
        total_files = sum(len(paths) for paths in groups.values())
        detail = '%d 份 PDF · %d 个班级%s' % (
            total_files, len(groups),
            ' · %d 节无板书/跳过' % len(failed) if failed else '')
        self.board_progress.complete(
            '板书导出完成', detail,
            hint='保存位置已打开，可继续折叠查看班级')
        self.status.set('板书导出完成：%d 份 PDF，%d 个班级%s' % (
            total_files, len(groups),
            '，%d 节无板书/跳过' % len(failed) if failed else ''))
        self.append_log('', target='board')
        self.append_log('===== 板书导出汇总（按班级分组，%s）====='
                        % payload.get('preset', ''), target='board')
        for group_name in sorted(groups):
            self.append_log('[班级] %s（%d 份 PDF）' % (
                group_name, len(groups[group_name])), target='board')
            for path in groups[group_name]:
                self.append_log('    %s' % Path(path).name, target='board')
        if failed:
            self.append_log('未导出（无板书/生成中/失败）：%d 节：%s'
                            % (len(failed), '、'.join(failed)),
                            target='board')
        out_root = Path(payload.get('out_root') or '')
        if out_root.is_dir():
            self.append_log('保存位置：%s' % out_root, target='board')
            try:
                open_path(out_root)
            except OSError:
                pass

    # ---------- 作业导出 ----------
    def _homework_log_rows(self) -> int:
        line_height = max(1, self._log_line_height(self.homework_log))
        min_tree = self.px(30) + self.px(36) * 2
        room = self.homework_tree.winfo_height() - min_tree - self.px(28)
        return max(0, min(6, int(room // line_height)))

    def on_toggle_homework_log(self) -> None:
        """展开/收起作业日志；同样只拿列表用不掉的高度。"""

        log = self.homework_log
        if log.expanded:
            log.set_expanded(False)
            return
        rows = self._homework_log_rows()
        if rows < 2:
            self.status.set('窗口高度不够：先最大化窗口，再展开「作业运行日志」。')
            return
        log.text.configure(height=rows)
        log.set_expanded(True)

    def _auto_list_homeworks(self) -> None:
        """时间段变了就自动重列（有登录态、且不在忙）。"""

        if not self._has_board_auth():
            return
        if self.busy.get():
            self._pending_homework_list = True
            return
        self.on_list_homeworks()

    def on_list_homeworks(self) -> None:
        if self.busy.get():
            return
        if not self._has_board_auth():
            messagebox.showwarning(
                APP_NAME,
                '还没有可用的登录态。\n\n'
                '请确认电脑上的爱问云已登录并点「重新检测」；'
                '或点「改用账号密码登录」。')
            return
        try:
            from_ms, to_ms = resolve_range(
                self.homework_range.get(), self.homework_from.get().strip(),
                self.homework_to.get().strip())
        except ValueError as error:
            messagebox.showwarning(APP_NAME, str(error))
            return
        self.cancel_event = threading.Event()
        self._busy_kind = 'homework'
        self.set_busy(True)
        self.homework_progress.show()
        self.homework_progress.begin(
            '正在列出作业框', '正在读取班级与作业清单…',
            indeterminate=True, hint='只读清单，不下载作业内容')
        self.status.set('正在列出作业框…')
        self.append_log('列出作业框（%s）…' % self.homework_range.get(),
                        target='homework')
        self.worker = threading.Thread(
            target=self._homework_list_work, args=(from_ms, to_ms),
            daemon=True)
        self.worker.start()

    def _homework_list_work(self, from_ms: int, to_ms: int) -> None:
        for attempt in range(2):
            try:
                session = self._board_session()
                items = fetch_homeworks(session, from_ms, to_ms,
                                        on_log=self._thread_homework_log,
                                        cancel_event=self.cancel_event)
            except TokenExpired:
                if attempt == 0 and self._renew_board_token():
                    self._thread_homework_log('登录态已过期，已自动重新获取，继续…')
                    continue
                self._post('homework_list_error',
                           '登录态已失效：请确认电脑上的爱问云仍登录后'
                           '点「重新检测」，或改用账号密码登录。')
                return
            except HomeworkCancelled as error:
                self._post('canceled', str(error))
                return
            except Exception as error:  # noqa: BLE001
                self._post('homework_list_error',
                           '%s: %s' % (type(error).__name__, error))
                return
            self._post('homeworks_listed', items)
            return

    def _thread_homework_log(self, message: str) -> None:
        self._post('log', {'target': 'homework', 'message': str(message)})

    def _homeworks_listed(self, items) -> None:
        """按班级分组：班级做成横排按钮，列表只显示当前班级的作业框。"""

        self.homework_items = list(items or [])
        self.homework_groups = group_homeworks(self.homework_items)
        self.homework_class_order = ordered_groups(self.homework_groups)
        self.homework_checked = {self._homework_key(row): False
                                 for row in self.homework_items}
        self.homework_active_class = (self.homework_class_order[0]
                                      if self.homework_class_order else '')
        self._build_homework_class_buttons()
        self._show_homework_class(self.homework_active_class)

        total = len(self.homework_items)
        group_count = len([name for name in self.homework_class_order
                           if name != UNCLASSIFIED_GROUP_NAME])
        if total:
            meta = '共 %d 个作业框' % total
            if group_count:
                meta += ' · %d 个班级' % group_count
            self.homework_meta.set(meta + ' · 最新在上')
            self.client_hint.set(
                '已自动列出 %d 个作业框；点上面的班级按钮换班，'
                '勾选后点「导出所选作业」。' % total)
            self.status.set('%s；勾选后点「导出所选作业」。' % meta)
            self.append_log('%s，按班级分组；每个作业框会导出一个同名 CSV。'
                            % meta, target='homework')
        else:
            self.homework_meta.set('该时间段没有作业框')
            self.client_hint.set('该时间段没有作业：换个时间段（如「最近30天」）'
                                 '就会自动重列。')
            self.status.set('该时间段没有作业框：换个时间段再列出。')
            self.append_log('该时间段没有作业框。', target='homework')

        self.set_busy(False)
        self.homework_progress.hide()
        self._refresh_homework_state()
        if self._pending_homework_list:
            self._pending_homework_list = False
            self.on_list_homeworks()

    def _homework_key(self, row: dict) -> str:
        """作业框唯一键：优先 zuoyeId，保证换班级后勾选不丢。"""

        return str(row.get('zuoyeId') or row.get('id') or id(row))

    def _build_homework_class_buttons(self) -> None:
        """横排班级按钮，最多显示 8 个班（留够常见 3~6 个班的位置）。"""

        for child in self.homework_class_bar.winfo_children():
            child.destroy()
        self.homework_class_buttons = {}
        self.homework_class_labels = class_button_labels(
            self.homework_class_order)
        for name in self.homework_class_order[:HOME_CLASS_LIMIT]:
            button = tk.Button(
                self.homework_class_bar, text=self.homework_class_labels[name],
                bd=0, relief='flat',
                highlightthickness=0, padx=self.px(14), pady=self.px(6),
                font=(FONT, 10), bg=SURFACE, fg=TEXT_DARK, cursor='hand2',
                activebackground=SURFACE_ALT, activeforeground=TEXT_DARK,
                command=lambda n=name: self._show_homework_class(n))
            button.pack(side=LEFT, padx=(0, self.px(8)))
            self.homework_class_buttons[name] = button
        rest = len(self.homework_class_order) - HOME_CLASS_LIMIT
        if rest > 0:
            tk.Label(self.homework_class_bar,
                     text='另有 %d 个班未显示' % rest, bg=CARD,
                     fg=TEXT_MUTED, font=(FONT, 9)).pack(
                         side=LEFT, padx=(self.px(4), 0))

    def _show_homework_class(self, name: str) -> None:
        """切到某个班：表格里只列这个班的作业框。"""

        self.homework_active_class = name
        self.homework_tree.delete(*self.homework_tree.get_children())
        rows = self.homework_groups.get(name) or []
        for row in rows:
            key = self._homework_key(row)
            checked = bool(self.homework_checked.get(key))
            icon = self._check_icon('checked' if checked else 'unchecked',
                                    leaf=True)
            opts = ({'image': icon, 'text': ''} if icon
                    else {'text': '☑' if checked else '☐'})
            self.homework_tree.insert(
                '', 'end', iid=key,
                values=(homework_title(row),
                        self._homework_progress_text(row),
                        self._homework_day(row)),
                tags=('checked' if checked else 'unchecked',), **opts)
        self._refresh_homework_state()

    def _homework_progress_text(self, row: dict) -> str:
        submit = row.get('tijiaoNum') or 0
        total = row.get('zuoyesNum') or 0
        pigai = row.get('pigaiNum') or 0
        if not total:
            return '应交未知'
        return '已交 %s/%s · 已批 %s' % (submit, total, pigai)

    def _homework_day(self, row: dict) -> str:
        stamp = homework_time(row)
        if not stamp:
            return '-'
        return datetime.datetime.fromtimestamp(stamp / 1000).strftime(
            '%Y-%m-%d')

    def _refresh_homework_state(self) -> None:
        checked = sum(1 for value in self.homework_checked.values() if value)
        total = len(self.homework_items)
        self.homework_badge_count.configure(
            text='已选 %d / %d 个' % (checked, total),
            bg=CARD, fg=ACCENT_DARK if checked else TEXT_MUTED,
            font=(FONT, 10, 'bold') if checked else (FONT, 10))

        for name, button in self.homework_class_buttons.items():
            active = name == self.homework_active_class
            rows = self.homework_groups.get(name) or []
            picked = sum(1 for row in rows
                         if self.homework_checked.get(self._homework_key(row)))
            short = getattr(self, 'homework_class_labels', {}).get(name, name)
            label = short if not picked else '%s · 已选 %d' % (short, picked)
            button.configure(
                text=label,
                bg=ACCENT if active else SURFACE,
                fg='#ffffff' if active else TEXT_DARK,
                activebackground=ACCENT_DARK if active else SURFACE_ALT,
                activeforeground='#ffffff' if active else TEXT_DARK)

        can_select = (self._has_board_auth() and not self.busy.get()
                      and bool(total))
        for button in (self.homework_select_button,
                       self.homework_clear_button):
            button.configure(state='normal' if can_select else 'disabled')
        self.homework_export_button.configure(
            state='normal' if (can_select and checked) else 'disabled')
        self.homework_list_button.configure(
            state='normal' if (self._has_board_auth()
                               and not self.busy.get()) else 'disabled')

    def _on_homework_click(self, event) -> None:
        region = self.homework_tree.identify_region(event.x, event.y)
        if region not in ('cell', 'tree'):
            return
        iid = self.homework_tree.identify_row(event.y)
        if not iid:
            return
        if self.busy.get():
            return
        self._toggle_homework(iid)
        return 'break'

    def _toggle_homework(self, iid: str) -> None:
        self.homework_checked[iid] = not self.homework_checked.get(iid, False)
        checked = bool(self.homework_checked.get(iid))
        tags = ('checked' if checked else 'unchecked',)
        icon = self._check_icon('checked' if checked else 'unchecked',
                                leaf=True)
        if icon is not None:
            self.homework_tree.item(iid, image=icon, text='', tags=tags)
        else:
            self.homework_tree.item(iid, text='☑' if checked else '☐',
                                    tags=tags)
        self._refresh_homework_state()

    def on_select_all_homeworks(self) -> None:
        """全选 / 取消当前班级的作业框（其他班级的勾选不动）。"""

        rows = self.homework_groups.get(self.homework_active_class) or []
        keys = [self._homework_key(row) for row in rows]
        target = not all(self.homework_checked.get(key) for key in keys) \
            if keys else False
        for key in keys:
            self.homework_checked[key] = target
        self._show_homework_class(self.homework_active_class)

    def on_clear_homeworks(self) -> None:
        for key in list(self.homework_checked):
            self.homework_checked[key] = False
        self._show_homework_class(self.homework_active_class)

    def _checked_homeworks(self) -> list[dict]:
        """所有班级里勾选的作业框（按列出顺序）。"""

        return [row for row in self.homework_items
                if self.homework_checked.get(self._homework_key(row))]

    def on_export_homeworks(self) -> None:
        if self.busy.get():
            return
        if not self._has_board_auth():
            messagebox.showwarning(APP_NAME, '还没有可用的登录态。')
            return
        picked = self._checked_homeworks()
        if not picked:
            messagebox.showwarning(APP_NAME, '请先勾选要导出的作业框。')
            return
        initial = homework_out_root()
        initial_dir = initial if initial.exists() else Path.home() / 'Desktop'
        chosen = filedialog.askdirectory(title='选择作业 CSV 保存位置',
                                         initialdir=str(initial_dir),
                                         mustexist=False)
        if not chosen:
            self.status.set('已取消：未选择保存位置。')
            return
        out_root = Path(chosen)
        self.cancel_event = threading.Event()
        self._busy_kind = 'homework'
        self.set_busy(True)
        self.clear_log('homework')
        self.homework_progress.show()
        self.homework_progress.begin(
            '正在导出作业成绩', '正在逐个读取作业框的学生成绩…',
            indeterminate=True,
            hint='一个作业框一个 CSV，文件名和爱问云的统计导出一样')
        self.status.set('正在导出 %d 个作业框…' % len(picked))
        self.append_log('导出 %d 个作业框的成绩，保存到：%s'
                        % (len(picked), out_root), target='homework')
        self.worker = threading.Thread(
            target=self._homework_export_work,
            args=(picked, out_root), daemon=True)
        self.worker.start()

    def _homework_export_work(self, picked: list[dict],
                              out_root: Path) -> None:
        for attempt in range(2):
            try:
                session = self._board_session()
                result = export_homeworks(
                    session, picked, out_root,
                    on_event=lambda payload: self._post(
                        'homework_event', payload),
                    cancel_event=self.cancel_event,
                    logger=self._thread_homework_log)
            except TokenExpired:
                if attempt == 0 and self._renew_board_token():
                    self._thread_homework_log('登录态已过期，已自动重新获取，继续…')
                    continue
                self._post('error',
                           '登录态已失效：请确认电脑上的爱问云仍登录后'
                           '点「重新检测」，或改用账号密码登录。')
                return
            except HomeworkCancelled as error:
                self._post('canceled', str(error))
                return
            except Exception as error:  # noqa: BLE001
                self._post('error',
                           '%s: %s' % (type(error).__name__, error))
                return
            self._post('homework_done', result)
            return

    def _on_homework_event(self, payload) -> None:
        if not isinstance(payload, dict):
            return
        phase = str(payload.get('phase') or '')
        done = int(payload.get('done', 0) or 0)
        total = max(1, int(payload.get('total', 1) or 1))
        name = str(payload.get('name') or '')
        percent = (done / total * 100.0) if total else 100.0
        if phase == 'start':
            detail = '第 %d / %d 个作业框 · %s' % (
                int(payload.get('index') or 0), total, name)
        elif phase == 'done':
            detail = '已完成 %d / %d 个' % (done, total)
        elif phase == 'failed':
            detail = '读取失败，继续下一个 · %s' % name
        else:
            detail = name
        self.homework_progress.update(percent, detail=detail,
                                      elapsed=payload.get('elapsed'))
        self.status.set('正在导出作业成绩：已完成 %d / %d 个' % (done, total))

    def _finish_homeworks(self, payload: dict) -> None:
        self.set_busy(False)
        files = payload.get('files') or []
        failed = payload.get('failed') or []
        partial = [item for item in files if item.get('complete') is False]
        detail = '%d 个 CSV%s%s' % (
            len(files),
            ' · %d 个失败' % len(failed) if failed else '',
            ' · %d 个人数不足' % len(partial) if partial else '')
        self.homework_progress.complete(
            '作业导出完成', detail,
            hint='保存位置即将打开，可继续勾选别的作业框')
        self.status.set('作业导出完成：%d 个 CSV%s%s' % (
            len(files),
            '，%d 个失败' % len(failed) if failed else '',
            '，%d 个人数不足' % len(partial) if partial else ''))
        self.append_log('', target='homework')
        self.append_log('===== 作业导出汇总（用时 %ss）====='
                        % payload.get('elapsed', '-'), target='homework')
        for content in files:
            flag = '' if content.get('complete', True) else '（人数不足！）'
            self.append_log('    %s（%d 名学生）%s'
                            % (Path(content.get('path', '')).name,
                               content.get('rows', 0), flag),
                            target='homework')
        if failed:
            self.append_log('失败 %d 个：%s' % (
                len(failed), '、'.join(str(x.get('title')) for x in failed)),
                target='homework')
        out_root = Path(payload.get('out_root') or '')
        if out_root.is_dir():
            self.append_log('保存位置：%s' % out_root, target='homework')
            try:
                open_path(out_root)
            except OSError:
                pass
        self._refresh_homework_state()

    def on_close(self) -> None:
        if self.busy.get():
            if not messagebox.askyesno(APP_NAME, '正在导出，确定要退出吗？'):
                return
            self.cancel_event.set()
        self.root.destroy()

    # ---------- 工具 ----------
    def set_busy(self, busy: bool) -> None:
        self.busy.set(busy)
        # 邀请码按钮的文字只跟着「邀请码导出」自己走；板书在跑时不能显示
        # 「正在导出…」，否则用户会以为邀请码功能也在导出。
        self.export_button.configure(
            state='disabled' if busy else 'normal',
            text='正在导出…' if self.invite_running else '导出邀请码 Excel')
        show_note = busy and self._busy_kind in ('board', 'login', 'homework')
        if show_note and not self.invite_busy_note.winfo_manager():
            self.invite_busy_note.pack(
                anchor='w', pady=(self.px(8), 0),
                after=self.invite_progress_slot)
        elif not show_note and self.invite_busy_note.winfo_manager():
            self.invite_busy_note.pack_forget()
        # 进度面板和运行日志都会从列表身上切高度，两个一起开列表就没了；
        # 开跑时先收起日志，进度面板负责讲当前在干什么。
        if show_note and self.board_log.expanded:
            self.board_log.set_expanded(False)
        self._refresh_auth_ui()

    def _log_panel(self, target=None):
        panels = {'invite': self.invite_log, 'board': self.board_log,
                  'homework': self.homework_log}
        if target not in panels:
            if self._busy_kind == 'invite':
                target = 'invite'
            elif self._busy_kind in ('board', 'login'):
                target = 'board'
            elif self._busy_kind == 'homework':
                target = 'homework'
            else:
                target = self.view
        return panels.get(target, self.board_log)

    def append_log(self, message: str, target=None) -> None:
        panel = self._log_panel(target)
        if panel is not None:
            panel.append(message)

    def clear_log(self, target=None) -> None:
        panel = self._log_panel(target)
        if panel is not None:
            panel.clear()

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
            '顶部用「邀请码导出 / 板书导出」两个标签页分开使用。\n\n'
            '【邀请码导出】\n\n'
            '1. 打开并登录爱问云（老师账号），点「导出邀请码 Excel」。\n\n'
            '2. 工具读取登录态，自动拉取「实时课堂」，并发读取邀请码，'
            '按「课程 × 第几节课」排成 Excel，文件保存在桌面「爱问云邀请码」。'
            '导出进度显示在本页主按钮下方。\n\n'
            '【板书导出】\n\n'
            '1. 打开程序会自动只读检测本机已登录的爱问云客户端：检测到就直接'
            '复用它的登录态，不需要账号密码，也不会把电脑客户端挤下线。\n\n'
            '2. 检测不到时才需要账号密码登录（密码只在本进程内存中使用，'
            '不写日志、不落盘）。注意：同一账号登录可能使电脑上的爱问云下线。\n\n'
            '3. 选时间段（全部 / 今天 / 最近 7 天 / 30 天 / 一个季度 / 自定义），'
            '切换后会自动列出该时段的课堂清单。班级以爱问云官方班级为准：'
            'β1、β2、β3、α1 等分组会合并成同一个班，例如「2026027MA+31」；'
            '匹配不到班级的条目统一放在「杂项/未分类」；列表按时间倒序，最新在上。\n\n'
            '4. 点整行勾选要导出的课；「全选」选中全部；点班级行可整组勾选。'
            '每个班级行右侧的「收起 / 展开」用于折叠班级，右上角按钮可一次'
            '折叠或展开全部班级。\n\n'
            '5. 点「导出所选板书」时只导出勾选项，未勾选会被拦截，不会回退为'
            '全量导出；随后弹出保存位置。PDF 按班级分文件夹，文件名与课堂名一致。'
            '排序可选「按上课顺序」或「按板书类型」。\n\n'
            '6. 导出进度显示在板书页内：总百分比、已完成节数、当前课堂页数和'
            '已用时间，都在列表上方，旁边有「取消」。多节课会并行导出，'
            '可折叠班级查看，不会中断。\n\n'
            '7. 两个功能的运行日志彼此独立：邀请码日志在邀请码页底部；'
            '板书日志在班级列表下方，默认收起，右侧显示未读条数，点「展开」'
            '查看；导出结束或出错时会自动展开。\n\n'
            '安全说明：token / 密码只在本进程内存中使用，不写日志、不落盘；'
            '只向 HTTPS 爱问云业务主机发请求。\n\n' + mac_note)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if '--ssl-test' in sys.argv:
        # CI/诊断用：用打包后的证书上下文真实连一次业务主机。
        import socket
        from urllib.parse import urlparse

        from net import _ssl_context

        host = urlparse(DEFAULT_BASE_URL).hostname or 'www.aiwenyun.cn'
        context = _ssl_context()
        print('CA certs: %d' % len(context.get_ca_certs()))
        with socket.create_connection((host, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                print('TLS ok: %s %s' % (host, tls.version()))
        return 0

    if '--smoke-test' in sys.argv:
        # CI 用：验证打包后的 .app / exe 能加载 Tkinter、openpyxl 和业务模块。
        import tkinter
        from openpyxl import Workbook  # noqa: F401
        import analyze  # noqa: F401
        import banshu_export  # noqa: F401
        import login  # noqa: F401
        import matrix  # noqa: F401
        from net import _ssl_context
        import pipeline  # noqa: F401
        if banshu_export.PilImage is None:
            raise SystemExit('Pillow missing: board export needs Pillow')
        if sys.platform == 'darwin':
            import token_macos  # noqa: F401
        else:
            import token_windows  # noqa: F401
        ca_count = len(_ssl_context().get_ca_certs())
        if ca_count < 10:
            raise SystemExit('CA bundle missing: %d' % ca_count)
        print('smoke ok: Tk %s, CA %d' % (tkinter.TkVersion, ca_count))
        return 0
    app = App()
    if '--auto' in sys.argv:
        if '--no-open' in sys.argv:
            app.open_after.set(False)
        app.root.after(600, app.on_export)
    app.run()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
