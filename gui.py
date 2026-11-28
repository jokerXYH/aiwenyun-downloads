"""Tkinter 图形界面：默认免重启，只读登录态；支持账号密码登录导出板书。"""

from __future__ import annotations

import base64
import ctypes
import datetime
import io
import math
import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import (BOTH, LEFT, RIGHT, X, Y, BooleanVar, IntVar, StringVar,
                     Tk, filedialog, messagebox, ttk)
from tkinter import Text, font as tkfont

try:
    from PIL import Image, ImageDraw, ImageTk
except ImportError:  # pragma: no cover  打包缺 Pillow 时退回文字符号
    Image = None
    ImageDraw = None
    ImageTk = None

from config import (APP_NAME, APP_VERSION, DEFAULT_BASE_URL, load_settings,
                    load_ui_theme, open_path, save_settings, save_ui_theme)
from banshu_export import (BatchCancelled,
                           UNCLASSIFIED_GROUP, class_group,
                           default_out_root, enrich_record_groups,
                           export_records, fetch_class_names, fetch_records,
                           group_board_records, group_display_names,
                           group_sort_key, ordered_group_names,
                           record_matches_class_list, record_time,
                           resolve_range, scan_token,
                           sort_records_newest_first, term_label)
from gui_board_view import build_board_view
from gui_feedback_view import build_feedback_view
from login import LoginError, LoginResult, do_login
from net import ApiSession, TokenExpired
from feedback_export import (COMPONENT_ORDER, CONSOLIDATION, PREVIEW,
                             FeedbackCancelled, build_feedback_bundles,
                             feedback_component_name,
                             default_out_root as feedback_out_root,
                             export_feedback_bundles)
from homework_export import (HomeworkCancelled, fetch_homeworks,
                             homework_group, homework_time, homework_title)
from pipeline import (Cancelled, ExportOptions, ExportSummary, NoClassesError,
                      PipelineError, run_export)
from token_source import client_running, debug_port_open
from theme_art import (THEME_DESCRIPTIONS, THEME_LABELS, THEME_ORDER,
                       color_role, palette_for, render_app_icon, render_hero,
                       render_settings_icon, render_theme_preview,
                       render_user_asset, theme_key)
from update_checker import check_for_update
from update_installer import (can_auto_update, cleanup_stale_update_files,
                              launch_updater, prepare_update)

FONT = 'Microsoft YaHei UI'
MONO = 'Consolas'
UPDATE_BUTTON_TEXT = '一键更新'


# 方向：安静的课堂工具。表面用带一点纸张暖度的中性灰，绿色只留给
# 主操作、选中状态和进度；彩色面积集中到“班级分组列表”和“导出进度”
# 两个内容时刻，不再让标签/按钮抢戏。
#
# 三套主题共用下面这组语义色。启动时按保存的主题覆盖，切换时再把已有
# 控件上的旧颜色按角色替换，所以控件代码仍然只写 BG / CARD / ACCENT。
_DEFAULT_PALETTE = palette_for('light')
ACCENT = _DEFAULT_PALETTE['ACCENT']
ACCENT_DARK = _DEFAULT_PALETTE['ACCENT_DARK']
ACCENT_TEXT = _DEFAULT_PALETTE['ACCENT_TEXT']
ACCENT_SOFT = _DEFAULT_PALETTE['ACCENT_SOFT']
ON_ACCENT = _DEFAULT_PALETTE['ON_ACCENT']
TEXT_DARK = _DEFAULT_PALETTE['TEXT_DARK']
TEXT_MUTED = _DEFAULT_PALETTE['TEXT_MUTED']
TEXT_FAINT = _DEFAULT_PALETTE['TEXT_FAINT']
BG = _DEFAULT_PALETTE['BG']
CARD = _DEFAULT_PALETTE['CARD']
SURFACE = _DEFAULT_PALETTE['SURFACE']
SURFACE_ALT = _DEFAULT_PALETTE['SURFACE_ALT']
BORDER = _DEFAULT_PALETTE['BORDER']
BORDER_STRONG = _DEFAULT_PALETTE['BORDER_STRONG']
CONTROL_BORDER = _DEFAULT_PALETTE['CONTROL_BORDER']
GROUP_BG = _DEFAULT_PALETTE['GROUP_BG']
GROUP_MISC_BG = _DEFAULT_PALETTE['GROUP_MISC_BG']
CHECKED_BG = _DEFAULT_PALETTE['CHECKED_BG']
# 进度卡片失败/取消时的状态色：不抢主色的绿，但一眼能区分
WARN = _DEFAULT_PALETTE['WARN']
DANGER = _DEFAULT_PALETTE['DANGER']
# 以下颜色由主题切换使用，保留模块级别名方便自定义控件直接引用
DISABLED_BG = _DEFAULT_PALETTE['DISABLED_BG']
DISABLED_FG = _DEFAULT_PALETTE['DISABLED_FG']
ACCENT_DISABLED = _DEFAULT_PALETTE['ACCENT_DISABLED']
ON_ACCENT_DISABLED = _DEFAULT_PALETTE['ON_ACCENT_DISABLED']
HOVER_STRONG = _DEFAULT_PALETTE['HOVER_STRONG']
TRACK = _DEFAULT_PALETTE['TRACK']
TRACK_SOFT = _DEFAULT_PALETTE['TRACK_SOFT']
SCROLL = _DEFAULT_PALETTE['SCROLL']
TEXT_BODY = _DEFAULT_PALETTE['TEXT_BODY']
LINK = _DEFAULT_PALETTE['LINK']
PROGRESS = _DEFAULT_PALETTE['PROGRESS']
HERO_TEXT = _DEFAULT_PALETTE['HERO_TEXT']
HERO_MUTED = _DEFAULT_PALETTE['HERO_MUTED']
HERO_BTN_BG = _DEFAULT_PALETTE['HERO_BTN_BG']
HERO_BTN_FG = _DEFAULT_PALETTE['HERO_BTN_FG']
HERO_BTN_BORDER = _DEFAULT_PALETTE['HERO_BTN_BORDER']


def _rgb(value: str) -> tuple[int, int, int]:
    """#rrggbb -> PIL 用的 (r, g, b)。"""

    raw = str(value or '').strip().lstrip('#')
    if len(raw) != 6:
        return (0, 0, 0)
    try:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError:
        return (0, 0, 0)


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

    try:
        value = max(0, int(seconds or 0))
    except (TypeError, ValueError, OverflowError):
        value = 0
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return '%d:%02d:%02d' % (hours, minutes, secs)
    return '%02d:%02d' % (minutes, secs)


class ProgressPanel:
    """导出进度卡片。

    没有系统标题栏的小卡片：主进度、当前课堂页数、已用/剩余时间都收在
    一张卡上。导出完成、失败或取消后都会自动收起，不再留下一个「导出
    进度」窗口需要用户手动关。
    """

    def __init__(self, parent, cancel_command, compact: bool = False,
                 on_show=None, on_hide=None) -> None:
        self.compact = compact
        self._cancel_command = cancel_command
        self._on_show = on_show
        self._on_hide = on_hide
        self.root = parent.winfo_toplevel()
        try:
            self._scale = max(1.0, float(self.root.winfo_fpixels('1i')) / 96.0)
        except Exception:  # noqa: BLE001
            self._scale = 1.0
        self._width = self._px(500 if not compact else 430)
        self._active = False
        self._visible = False
        self._state = 'idle'          # idle / running / done / failed
        self._fail_cancelled = False
        self._indeterminate = False
        self._target_percent = 0.0
        self._shown_percent = 0.0
        self._sub_target = 0.0
        self._sub_shown = 0.0
        self._sub_visible = False
        self._started = 0.0
        self._elapsed_override = None
        self._phase = 0.0
        self._anim_job = None
        self._hide_job = None
        self._icon_key = None
        self._icon_photo = None
        self._drag_offset = None

        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.transient(self.root)
        self.win.configure(bg=CARD)
        self.win.protocol('WM_DELETE_WINDOW', self.hide)

        self.card = tk.Frame(self.win, bg=CARD,
                             highlightbackground=BORDER,
                             highlightthickness=1)
        self.card.pack(fill=BOTH, expand=True)
        self.stripe = tk.Frame(self.card, bg=ACCENT, height=self._px(2))
        self.stripe.pack(fill=X)
        self.body = tk.Frame(self.card, bg=CARD)
        self.body.pack(fill=BOTH, expand=True,
                       padx=self._px(20), pady=self._px(16))

        head = tk.Frame(self.body, bg=CARD)
        head.pack(fill=X)
        self.icon_canvas = tk.Canvas(head, width=self._px(32),
                                     height=self._px(32), bg=CARD,
                                     highlightthickness=0, bd=0)
        self.icon_canvas.pack(side=LEFT, padx=(0, self._px(12)))
        self.title_var = StringVar(value='')
        self.title_label = tk.Label(
            head, textvariable=self.title_var, bg=CARD, fg=TEXT_DARK,
            anchor='w', font=(FONT, 12, 'bold'))
        self.title_label.pack(side=LEFT, fill=X, expand=True)
        self.percent_var = StringVar(value='')
        self.percent_label = tk.Label(
            head, textvariable=self.percent_var, bg=CARD, fg=LINK,
            anchor='e', font=(MONO, 16, 'bold'))
        self.percent_label.pack(side=RIGHT, padx=(self._px(12), 0))

        self.detail_var = StringVar(value='')
        self.detail_label = tk.Label(
            self.body, textvariable=self.detail_var, bg=CARD, fg=TEXT_MUTED,
            anchor='w', justify=LEFT, font=(FONT, 10),
            wraplength=self._width - self._px(48))
        self.detail_label.pack(fill=X, pady=(self._px(8), 0))

        self.bar_canvas = tk.Canvas(self.body, height=self._px(8), width=1,
                                    bg=CARD, highlightthickness=0, bd=0)
        self.bar_canvas.pack(fill=X, pady=(self._px(12), 0))

        # 子进度固定占位：阶段切换时窗口不会忽高忽低
        self.sub_row = tk.Frame(self.body, bg=CARD)
        self.sub_label_var = StringVar(value='')
        self.sub_label = tk.Label(
            self.sub_row, textvariable=self.sub_label_var, bg=CARD,
            fg=TEXT_FAINT, font=(MONO, 9), anchor='w')
        self.sub_label.pack(side=LEFT)
        self.sub_canvas = tk.Canvas(self.sub_row, height=self._px(4), width=1,
                                    bg=CARD, highlightthickness=0, bd=0)
        self.sub_canvas.pack(side=RIGHT, fill=X, expand=True,
                             padx=(self._px(12), 0))

        self.foot = tk.Frame(self.body, bg=CARD)
        self.foot.pack(fill=X, pady=(self._px(12), 0))
        self.elapsed_var = StringVar(value='')
        self.elapsed_label = tk.Label(
            self.foot, textvariable=self.elapsed_var, bg=CARD, fg=TEXT_FAINT,
            font=(MONO, 9))
        self.elapsed_label.pack(side=LEFT)
        self.remain_var = StringVar(value='')
        self.remain_label = tk.Label(
            self.foot, textvariable=self.remain_var, bg=CARD, fg=TEXT_FAINT,
            font=(MONO, 9))
        self.remain_label.pack(side=LEFT, padx=(self._px(12), 0))
        self.action_button = tk.Label(
            self.foot, text='取消', bg=CARD, fg=LINK,
            font=(FONT, 10), padx=self._px(12),
            pady=self._px(4), cursor='hand2')
        self.action_button.pack(side=RIGHT)
        self._action_enabled = True
        self.action_button.bind('<Button-1>', self._on_action_click)

        # 拖动：没有标题栏，用卡片空白处拖
        for widget in (self.card, self.stripe, self.body, head,
                       self.title_label, self.icon_canvas, self.detail_label,
                       self.bar_canvas, self.elapsed_label):
            widget.bind('<Button-1>', self._start_drag)
            widget.bind('<B1-Motion>', self._drag)
        self.action_button.bind('<Enter>', self._action_hover)
        self.action_button.bind('<Leave>', self._action_leave)
        self.win.bind('<Escape>', lambda _e: self.hide())

    # -- 基础 --------------------------------------------------------
    def _px(self, value: int) -> int:
        return max(1, int(round(value * self._scale)))

    def _accent(self) -> str:
        if self._state != 'failed':
            return PROGRESS
        return WARN if self._fail_cancelled else DANGER

    # -- 显示/隐藏 --------------------------------------------------
    def show(self) -> None:
        self._cancel_hide()
        if self._visible:
            self._render_all()
            self._start_animation()
            return
        self._visible = True
        self.win.update_idletasks()
        self._place()
        self.win.deiconify()
        self.win.lift()
        try:
            self.win.attributes('-topmost', True)
            self.win.after(380, self._drop_topmost)
        except Exception:  # noqa: BLE001
            pass
        if self._on_show is not None:
            try:
                self._on_show()
            except Exception:  # noqa: BLE001
                pass
        self._render_all()
        self._start_animation()

    def hide(self) -> None:
        self._cancel_hide()
        self._stop_animation()
        self._active = False
        self._state = 'idle'
        self._visible = False
        try:
            self.win.withdraw()
        except Exception:  # noqa: BLE001
            pass
        if self._on_hide is not None:
            try:
                self._on_hide()
            except Exception:  # noqa: BLE001
                pass

    def _drop_topmost(self) -> None:
        try:
            self.win.attributes('-topmost', False)
        except Exception:  # noqa: BLE001
            pass

    def _place(self) -> None:
        """贴着主窗口居中偏下，别跑到屏幕外。"""

        try:
            self.win.update_idletasks()
            width = self._width
            height = max(self._px(118), self.win.winfo_reqheight())
            x = self.root.winfo_rootx() + (
                self.root.winfo_width() - width) // 2
            y = (self.root.winfo_rooty() + self.root.winfo_height()
                 - height - self._px(88))
            x = max(0, min(x, self.win.winfo_screenwidth() - width))
            y = max(0, min(y, self.win.winfo_screenheight() - height
                           - self._px(40)))
            self.win.geometry('%dx%d+%d+%d' % (width, height, x, y))
            self.win.update_idletasks()
        except Exception:  # noqa: BLE001
            pass

    def _resize_to_content(self) -> None:
        """完成/失败收起子进度后，贴着底边重新量一次高度。"""

        try:
            self.win.update_idletasks()
            height = max(self._px(112), self.win.winfo_reqheight())
            x = self.win.winfo_x()
            bottom = self.win.winfo_y() + self.win.winfo_height()
            y = max(0, bottom - height)
            self.win.geometry('%dx%d+%d+%d' % (self._width, height, x, y))
            self.win.update_idletasks()
        except Exception:  # noqa: BLE001
            pass

    def _start_drag(self, event) -> None:
        self._drag_offset = (event.x_root - self.win.winfo_x(),
                             event.y_root - self.win.winfo_y())

    def _drag(self, event) -> None:
        if self._drag_offset is None:
            return
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        try:
            self.win.geometry('+%d+%d' % (max(0, x), max(0, y)))
        except Exception:  # noqa: BLE001
            pass

    # -- 按钮 --------------------------------------------------------
    def _primary_action(self) -> None:
        if self._active:
            if self._cancel_command is not None:
                self._cancel_command()
            return
        self.hide()

    def _on_action_click(self, _event=None) -> None:
        if self._action_enabled:
            self._primary_action()

    def _set_button(self, text: str, enabled: bool) -> None:
        self._action_enabled = bool(enabled)
        self.action_button.configure(
            text=text, fg=LINK if enabled else TEXT_FAINT,
            cursor='hand2' if enabled else 'arrow')

    def _action_hover(self, _event=None) -> None:
        if self._action_enabled:
            self.action_button.configure(
                bg=ACCENT_SOFT if self._active else SURFACE_ALT)

    def _action_leave(self, _event=None) -> None:
        self.action_button.configure(bg=CARD)

    def apply_theme(self) -> None:
        """主题切换后重画进度环、进度条和按钮悬停色。"""

        self.card.configure(bg=CARD, highlightbackground=BORDER)
        self.win.configure(bg=CARD)
        self.stripe.configure(bg=ACCENT)
        self._render_all()
        self._render_icon(force=True)
        self._set_button(self.action_button.cget('text'),
                         self._action_enabled)

    # -- 状态 --------------------------------------------------------
    @staticmethod
    def _detail_text(detail: str, hint: str) -> str:
        if not hint:
            return detail
        separator = ('  ' if detail and detail[-1] in '。！？.!?' else '  ·  ')
        return detail + separator + hint

    def begin(self, title: str, detail: str = '',
              indeterminate: bool = False, hint: str = '',
              cancellable: bool = True) -> None:
        self._cancel_hide()
        self._started = time.monotonic()
        self._active = True
        self._state = 'running'
        self._fail_cancelled = False
        self._indeterminate = bool(indeterminate)
        self._target_percent = 0.0
        self._shown_percent = 0.0
        self._sub_target = 0.0
        self._sub_shown = 0.0
        self._elapsed_override = None
        self._phase = 0.0
        self._icon_key = None
        self.title_var.set(title)
        self.detail_var.set(
            self._detail_text(detail, hint if not self.compact else ''))
        if indeterminate:
            self.percent_var.set('进行中')
            self.percent_label.configure(font=(FONT, 11, 'bold'), fg=LINK)
        else:
            self.percent_var.set('0%')
            self.percent_label.configure(font=(MONO, 18, 'bold'), fg=LINK)
        self.stripe.configure(bg=ACCENT)
        self.elapsed_var.set('用时 00:00')
        self.remain_var.set('')
        # 子进度只在真的拿到页数时出现：列清单阶段不占这一行
        self._hide_sub_row()
        self.sub_label_var.set('')
        self._sub_target = 0.0
        self._sub_shown = 0.0
        self._set_button('取消' if cancellable else '请稍候', cancellable)
        self._render_all()
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
                self._indeterminate = True
                self._target_percent = 0.0
                self._shown_percent = 0.0
            self.percent_var.set('进行中')
            self.percent_label.configure(font=(FONT, 11, 'bold'), fg=LINK)
        else:
            if self._indeterminate:
                self._indeterminate = False
                self._shown_percent = 0.0
            self._target_percent = max(0.0, min(100.0, float(percent or 0)))
            self.percent_var.set('%d%%' % int(round(self._target_percent)))
            self.percent_label.configure(font=(MONO, 18, 'bold'), fg=LINK)
        if detail is not None:
            self.detail_var.set(str(detail))
        if elapsed is not None:
            self._elapsed_override = max(0.0, float(elapsed))
        if sub_detail:
            self._ensure_sub_row()
            self.sub_label_var.set(str(sub_detail))
        elif self._sub_visible:
            self.sub_label_var.set('当前课堂页数')
            self._sub_target = 0.0
        if sub_percent is not None:
            self._ensure_sub_row()
            self._sub_target = max(0.0, min(100.0, float(sub_percent)))
        self._render_bars()
        self._render_clock()
        self._start_animation()

    def complete(self, title: str, detail: str, hint: str = '') -> None:
        self._active = False
        self._state = 'done'
        self._fail_cancelled = False
        self._indeterminate = False
        self._target_percent = 100.0
        self.title_var.set(title)
        self.detail_var.set(self._detail_text(detail, hint))
        self.percent_var.set('100%')
        self.percent_label.configure(font=(MONO, 18, 'bold'), fg=LINK)
        self.stripe.configure(bg=ACCENT)
        self.elapsed_var.set('用时 ' + _fmt_clock(self._elapsed()))
        self.remain_var.set('')
        self._set_button('关闭', True)
        self._hide_sub_row()
        self.show()
        self._resize_to_content()
        self._schedule_hide(1800)

    def fail(self, title: str, detail: str, hint: str = '',
             state: str = '已取消') -> None:
        self._active = False
        self._state = 'failed'
        self._fail_cancelled = (state == '已取消')
        self._indeterminate = False
        color = self._accent()
        self.title_var.set(title)
        self.detail_var.set(self._detail_text(detail, hint))
        state_text = state or ''
        if state_text == title:
            state_text = ''
        self.percent_var.set(state_text)
        self.percent_label.configure(font=(FONT, 11, 'bold'), fg=color)
        self.stripe.configure(bg=color)
        self.elapsed_var.set('用时 ' + _fmt_clock(self._elapsed()))
        self.remain_var.set('')
        self._set_button('关闭', True)
        self._hide_sub_row()
        self.show()
        self._resize_to_content()
        self._schedule_hide(3200 if self._fail_cancelled else 3800)

    def tick(self) -> None:
        if self._active:
            self._render_clock()

    # -- 自动收起 ----------------------------------------------------
    def _schedule_hide(self, delay_ms: int) -> None:
        self._cancel_hide()
        self._hide_job = self.win.after(delay_ms, self._auto_hide)

    def _auto_hide(self) -> None:
        self._hide_job = None
        self.hide()

    def _cancel_hide(self) -> None:
        if self._hide_job is not None:
            try:
                self.win.after_cancel(self._hide_job)
            except Exception:  # noqa: BLE001
                pass
            self._hide_job = None

    # -- 动画 --------------------------------------------------------
    def _start_animation(self) -> None:
        if self._visible and self._anim_job is None:
            try:
                self._anim_job = self.win.after(40, self._animate)
            except Exception:  # noqa: BLE001
                self._anim_job = None

    def _stop_animation(self) -> None:
        if self._anim_job is not None:
            try:
                self.win.after_cancel(self._anim_job)
            except Exception:  # noqa: BLE001
                pass
            self._anim_job = None

    def _animate(self) -> None:
        self._anim_job = None
        if not self._visible:
            return
        self._phase += 0.11
        moving = False
        if self._indeterminate and self._state == 'running':
            moving = True
        else:
            delta = self._target_percent - self._shown_percent
            if abs(delta) > 0.05:
                # 完成时让进度条快速补满，别停在「100% 但条没满」的样子
                factor = 0.45 if self._state == 'done' else 0.24
                self._shown_percent += delta * factor
                moving = True
            else:
                self._shown_percent = self._target_percent
            sub_delta = self._sub_target - self._sub_shown
            if abs(sub_delta) > 0.05:
                self._sub_shown += sub_delta * 0.3
                moving = True
            else:
                self._sub_shown = self._sub_target
        self._render_bars()
        self._render_clock()
        if self._indeterminate or self._state == 'running':
            self._render_icon()
        if self._state == 'running' or moving:
            try:
                self._anim_job = self.win.after(40, self._animate)
            except Exception:  # noqa: BLE001
                self._anim_job = None

    # -- 绘制 --------------------------------------------------------
    def _render_all(self) -> None:
        self._render_bars()
        self._render_clock()
        self._render_icon(force=True)

    def _hide_sub_row(self) -> None:
        if self.sub_row.winfo_manager():
            self.sub_row.pack_forget()
        self._sub_visible = False

    def _ensure_sub_row(self) -> None:
        """第一次拿到页数时才把子进度行加进来，列清单时不占位置。"""

        if self.compact or self._sub_visible:
            return
        self.sub_row.pack(fill=X, pady=(self._px(8), 0), before=self.foot)
        self._sub_visible = True
        self._resize_to_content()
        self._render_bars()

    def _elapsed(self) -> float:
        if self._elapsed_override is not None:
            return self._elapsed_override
        return time.monotonic() - self._started if self._started else 0.0

    def _render_clock(self) -> None:
        elapsed = self._elapsed()
        self.elapsed_var.set('用时 ' + _fmt_clock(elapsed))
        if self._state == 'running' and not self._indeterminate:
            percent = max(self._target_percent, self._shown_percent)
            if percent >= 3 and elapsed >= 2:
                remain = elapsed * (100.0 - percent) / percent
                self.remain_var.set('剩余约 ' + _fmt_clock(remain))
            else:
                self.remain_var.set('')
        else:
            self.remain_var.set('')

    def _render_bars(self) -> None:
        self._draw_bar(self.bar_canvas, self._shown_percent / 100.0,
                       self._px(8), self._accent(),
                       indeterminate=(self._indeterminate
                                      and self._state == 'running'))
        if self._sub_visible:
            self._draw_bar(self.sub_canvas, self._sub_shown / 100.0,
                           self._px(4), PROGRESS)

    def _draw_bar(self, canvas, ratio: float, height: int, color: str,
                  indeterminate: bool = False) -> None:
        canvas.delete('all')
        width = canvas.winfo_width()
        if width <= 1:
            width = self._width - self._px(40)
        if width <= height:
            return
        self._round_bar(canvas, 0, width, height, TRACK)
        if indeterminate:
            span = max(height * 2, int(width * 0.30))
            travel = max(1, width - span)
            position = (math.sin(self._phase * 0.9) * 0.5 + 0.5) * travel
            self._round_bar(canvas, position, span, height, color)
        elif ratio > 0:
            filled = int(width * ratio)
            filled = max(height, min(width, filled))
            self._round_bar(canvas, 0, filled, height, color)

    @staticmethod
    def _round_bar(canvas, x: float, width: float, height: int,
                   color: str) -> None:
        if width <= 0:
            return
        radius = height / 2.0
        if width <= height:
            canvas.create_oval(x, 0, x + width, height,
                               fill=color, outline=color)
            return
        canvas.create_oval(x, 0, x + height, height,
                           fill=color, outline=color)
        canvas.create_oval(x + width - height, 0, x + width, height,
                           fill=color, outline=color)
        canvas.create_rectangle(x + radius, 0, x + width - radius, height,
                                fill=color, outline=color)

    def _render_icon(self, force: bool = False) -> None:
        if Image is None or ImageDraw is None or ImageTk is None:
            return
        ratio = 0.0
        if self._state == 'done':
            ratio = 1.0
        elif self._state == 'running' and not self._indeterminate:
            ratio = max(0.0, min(1.0, self._shown_percent / 100.0))
        key = (self._state,
               int(round(ratio * 24)),
               int(self._phase * 3) if self._indeterminate else 0)
        if not force and key == self._icon_key:
            return
        self._icon_key = key
        size = self._px(32)
        scale = 4
        pixels = max(16, size) * scale
        image = Image.new('RGBA', (pixels, pixels), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        pad = 3 * scale
        line = max(2 * scale, int(pixels * 0.075))
        color = self._accent()
        track = _rgb(TRACK) + (255,)
        draw.ellipse((pad, pad, pixels - pad, pixels - pad),
                     outline=track, width=line)
        center = pixels / 2.0
        if self._state == 'failed':
            draw.ellipse((pad, pad, pixels - pad, pixels - pad),
                         outline=color, width=line)
            stroke = max(2 * scale, int(pixels * 0.09))
            draw.line((center, pixels * 0.27, center, pixels * 0.55),
                      fill=color, width=stroke)
            dot = max(2.0, pixels * 0.045)
            draw.ellipse((center - dot, pixels * 0.66 - dot,
                          center + dot, pixels * 0.66 + dot), fill=color)
        elif self._state == 'done':
            draw.ellipse((pad, pad, pixels - pad, pixels - pad),
                         outline=color, width=line)
            stroke = max(2 * scale, int(pixels * 0.09))
            draw.line([(pixels * 0.29, pixels * 0.52),
                       (pixels * 0.44, pixels * 0.67),
                       (pixels * 0.72, pixels * 0.36)],
                      fill=color, width=stroke, joint='curve')
        elif self._indeterminate:
            start = (self._phase * 130.0) % 360.0 - 90.0
            draw.arc((pad, pad, pixels - pad, pixels - pad),
                     start, start + 110.0, fill=color, width=line)
        else:
            end = -90.0 + 360.0 * ratio
            draw.arc((pad, pad, pixels - pad, pixels - pad),
                     -90.0, end, fill=color, width=line)
            dot = max(2.0, pixels * 0.045)
            draw.ellipse((center - dot, center - dot,
                          center + dot, center + dot), fill=color)
        try:
            photo = ImageTk.PhotoImage(
                image.resize((size, size), Image.Resampling.LANCZOS))
            self.icon_canvas.delete('all')
            self.icon_canvas.create_image(size / 2, size / 2, image=photo)
            self._icon_photo = photo
        except Exception:  # noqa: BLE001
            pass


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
                         relief='flat', bg=SURFACE, fg=TEXT_BODY,
                         highlightthickness=0, font=(MONO, 10),
                         padx=self._px(12), pady=self._px(8),
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
        self.theme_key = theme_key(load_ui_theme())
        self._install_palette(self.theme_key)
        self._check_refreshers: list = []
        self._settings_window = None
        self.settings_update_status = StringVar(value='')
        self.update_button = None
        self._apply_window_geometry()
        self.root.configure(bg=BG)
        self.root.protocol('WM_DELETE_WINDOW', self.on_close)
        # 班级条的宽度预算要用真实字体量：按钮文字长度随 DPI 变化
        self._class_chip_font = tkfont.Font(family=FONT, size=10)
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
        self.status = StringVar(
            value='免重启模式：点「导出邀请码 Excel」，直接读取已登录客户端的会话。')
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
        self.board_meta = StringVar(value='识别本机登录态并列出板书后，将按「学期+班级」分组显示')
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
        self.board_group_labels: dict[str, str] = {}   # 官方班名 -> 26秋7MA+31
        self.board_display_order: list[str] = []       # 官方班名，按学期排好
        self.board_active_class = ''
        self._board_relayout_job = None
        self._board_bar_width = 0
        self._board_menu_ref = None
        self._pending_list = False                # 忙碌中收到的时间段切换请求

        # 反馈文件导出：历史课堂专注度反馈，按班级归档
        self.feedback_range = StringVar(value='最近30天')
        self.feedback_from = StringVar(
            value=(today - datetime.timedelta(days=29)).strftime('%Y-%m-%d'))
        self.feedback_to = StringVar(value=today.strftime('%Y-%m-%d'))
        self.feedback_meta = StringVar(
            value='识别本机登录态后，将按班级列出历史课堂')
        self.feedback_items: list[dict] = []
        self.feedback_class_order: list[str] = []
        self.feedback_checked: dict[str, bool] = {}
        self.feedback_file_checked: dict[str, bool] = {}
        self.feedback_expanded: dict[str, bool] = {}
        self.feedback_group_of: dict[str, str] = {}
        self.feedback_active_class = '全部班级'
        self.feedback_class_buttons: dict[str, tk.Button] = {}
        self.feedback_more_button: tk.Button | None = None
        self._feedback_hidden_count = 0
        self._feedback_relayout_job = None
        self._feedback_bar_width = 0
        self._feedback_menu_ref = None
        self._pending_feedback_list = False
        self._pending_feedback_auto = False
        self._update_busy = False
        self._update_installing = False
        self.view = 'invite'

        self._build_ui()
        self._apply_window_icon()
        self.root.after(80, self._drain_queue)
        self.root.after(500, self._tick_progress)
        self.root.after(280, self._start_client_scan)
        self.root.bind('<Control-Key-1>',
                       lambda _e: self._switch_view('invite'))
        self.root.bind('<Control-Key-2>',
                       lambda _e: self._switch_view('board'))
        self.root.bind('<Control-Key-3>',
                       lambda _e: self._switch_view('feedback'))

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

    # ---------- 主题 ----------
    def _install_palette(self, key: str) -> None:
        """把当前主题的语义色写回模块级名字，旧控件代码无需改写。"""

        self.theme_key = theme_key(key)
        palette = palette_for(self.theme_key)
        globals().update(palette)
        self.palette = palette

    def apply_theme(self, key: str, persist: bool = True) -> None:
        """切换主题：背景、图标、ttk 样式和所有已存在控件一起换色。"""

        key = theme_key(key)
        self._install_palette(key)
        self._recolor_widgets()
        self._configure_styles()
        self._refresh_check_widgets()
        self._refresh_tree_styles()
        try:
            self.root.configure(bg=BG)
        except Exception:  # noqa: BLE001
            pass
        self._switch_view(self.view)
        self._refresh_hero_chrome()
        self._render_hero(force=True)
        self._apply_window_icon()

        for panel in (getattr(self, 'invite_progress', None),
                      getattr(self, 'board_progress', None),
                      getattr(self, 'feedback_progress', None)):
            if panel is not None:
                try:
                    panel.apply_theme()
                except Exception:  # noqa: BLE001
                    pass

        # 班级按钮和列表里的勾选图标缓存了旧位图，按当前数据重建一遍。
        try:
            if getattr(self, 'board_groups', None) and self.board_active_class:
                self._build_board_class_buttons()
                self._show_board_class(self.board_active_class)
        except Exception:  # noqa: BLE001
            pass
        try:
            if getattr(self, 'feedback_items', None):
                self._build_feedback_class_buttons()
                self._show_feedback_rows()
        except Exception:  # noqa: BLE001
            pass

        self._refresh_empty_states()
        self._refresh_settings_ui()
        if persist:
            save_ui_theme(key)

    def _recolor_widgets(self) -> None:
        """按颜色角色遍历整棵控件树，把旧主题色替换成新主题色。"""

        options = ('background', 'bg', 'foreground', 'fg',
                   'activebackground', 'activeforeground',
                   'highlightbackground', 'highlightcolor',
                   'selectbackground', 'selectforeground',
                   'insertbackground', 'disabledforeground',
                   'readonlybackground', 'troughcolor', 'selectcolor')
        palette = self.palette

        def mapped(value):
            role = color_role(value)
            return palette.get(role) if role else None

        def recolor(widget) -> None:
            if not isinstance(widget, ttk.Widget):
                for option in options:
                    try:
                        current = str(widget.cget(option))
                    except Exception:  # noqa: BLE001
                        continue
                    target = mapped(current)
                    if target and target.lower() != current.lower():
                        try:
                            widget.configure(**{option: target})
                        except Exception:  # noqa: BLE001
                            pass
                if isinstance(widget, tk.Text):
                    for tag in widget.tag_names():
                        for option in ('background', 'foreground'):
                            try:
                                current = str(widget.tag_cget(tag, option))
                            except Exception:  # noqa: BLE001
                                continue
                            target = mapped(current)
                            if target:
                                try:
                                    widget.tag_configure(tag,
                                                         **{option: target})
                                except Exception:  # noqa: BLE001
                                    pass
                if isinstance(widget, tk.Canvas):
                    for item in widget.find_all():
                        for option in ('fill', 'outline'):
                            try:
                                current = str(widget.itemcget(item, option))
                            except Exception:  # noqa: BLE001
                                continue
                            target = mapped(current)
                            if target:
                                try:
                                    widget.itemconfigure(
                                        item, **{option: target})
                                except Exception:  # noqa: BLE001
                                    pass
            try:
                children = widget.winfo_children()
            except Exception:  # noqa: BLE001
                children = []
            for child in children:
                recolor(child)

        try:
            recolor(self.root)
        except Exception:  # noqa: BLE001
            pass

    def _refresh_check_widgets(self) -> None:
        for refresh in list(getattr(self, '_check_refreshers', [])):
            try:
                refresh()
            except Exception:  # noqa: BLE001
                pass

    def _refresh_tree_styles(self) -> None:
        for name in ('board_tree', 'feedback_tree'):
            tree = getattr(self, name, None)
            if tree is None:
                continue
            try:
                tree.tag_configure('unchecked', background=CARD,
                                   foreground=TEXT_DARK)
                tree.tag_configure('checked', background=CHECKED_BG,
                                   foreground=TEXT_DARK)
                if tree is getattr(self, 'feedback_tree', None):
                    # 展开后的子文件行用另一套底色，和父行一眼分开。
                    tree.tag_configure('child_unchecked', background=SURFACE,
                                       foreground=TEXT_BODY)
                    tree.tag_configure('child_checked',
                                       background=SURFACE_ALT,
                                       foreground=TEXT_BODY)
            except Exception:  # noqa: BLE001
                pass

    # ---------- 设置窗口 ----------
    def open_settings(self) -> None:
        win = getattr(self, '_settings_window', None)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            try:
                win.focus_force()
            except Exception:  # noqa: BLE001
                pass
            return
        self._build_settings_window()

    def _build_settings_window(self) -> None:
        win = tk.Toplevel(self.root)
        self._settings_window = win
        win.title('设置 · %s' % APP_NAME)
        win.configure(bg=BG)
        win.transient(self.root)
        win.resizable(False, False)
        try:
            win.iconphoto(True, *getattr(self, '_window_icons', []))
            if sys.platform == 'win32' and getattr(
                    self, '_theme_icon_path', ''):
                win.iconbitmap(self._theme_icon_path)
        except Exception:  # noqa: BLE001
            pass

        body = tk.Frame(win, bg=BG, padx=self.px(24), pady=self.px(20))
        body.pack(fill=BOTH, expand=True)

        head = tk.Frame(body, bg=BG)
        head.pack(fill=X)
        logo_image = render_app_icon(self.theme_key, self.px(44))
        if logo_image is not None and ImageTk is not None:
            logo_photo = ImageTk.PhotoImage(logo_image)
            logo = tk.Label(head, image=logo_photo, bg=BG, bd=0)
            logo.image = logo_photo
            logo.pack(side=LEFT, padx=(0, self.px(16)))
            self._settings_logo = logo
        title_box = tk.Frame(head, bg=BG)
        title_box.pack(side=LEFT, fill=X, expand=True)
        tk.Label(title_box, text='设置', bg=BG, fg=TEXT_DARK,
                 font=(FONT, 16, 'bold'), anchor='w').pack(anchor='w')
        tk.Label(title_box,
                 text='检查程序更新，或切换界面主题',
                 bg=BG, fg=TEXT_MUTED, font=(FONT, 10), anchor='w').pack(
                     anchor='w', pady=(self.px(4), 0))
        close_button = ttk.Button(head, text='关闭',
                                  style='SettingsQuiet.TButton',
                                  command=win.destroy)
        close_button.pack(side=RIGHT, anchor='n')

        # ---- 检查更新 ----
        update_card = tk.Frame(body, bg=CARD, bd=0, highlightthickness=1,
                               highlightbackground=BORDER)
        update_card.pack(fill=X, pady=(self.px(16), 0))
        update_body = ttk.Frame(update_card, style='Card.TFrame',
                                padding=(self.px(16), self.px(16)))
        update_body.pack(fill=BOTH, expand=True)
        update_head = ttk.Frame(update_body, style='Card.TFrame')
        update_head.pack(fill=X)
        ttk.Label(update_head, text='软件更新',
                  style='SettingsCardTitle.TLabel').pack(side=LEFT)
        ttk.Label(update_head, text='当前 v%s' % APP_VERSION,
                  style='SettingsCardSub.TLabel').pack(side=RIGHT)
        ttk.Label(update_body,
                  text='Windows 打包版会自动下载、校验并安装新版本；'
                       '更新时程序会退出并重启',
                  style='SettingsBody.TLabel', wraplength=self.px(520),
                  justify=LEFT).pack(anchor='w', pady=(self.px(4), 0))
        update_row = ttk.Frame(update_body, style='Card.TFrame')
        update_row.pack(fill=X, pady=(self.px(12), 0))
        self.update_button = ttk.Button(
            update_row, text=UPDATE_BUTTON_TEXT, style='Settings.TButton',
            command=self.on_check_update)
        self.update_button.pack(side=LEFT)
        self.settings_status_label = ttk.Label(
            update_row, textvariable=self.settings_update_status,
            style='SettingsCardSub.TLabel', justify=LEFT,
            wraplength=self.px(380))
        self.settings_status_label.pack(side=LEFT, padx=(self.px(12), 0))
        if not self.settings_update_status.get():
            self.settings_update_status.set('尚未检查更新')

        # ---- 主题 ----
        theme_card = tk.Frame(body, bg=CARD, bd=0, highlightthickness=1,
                              highlightbackground=BORDER)
        theme_card.pack(fill=X, pady=(self.px(16), 0))
        theme_body = ttk.Frame(theme_card, style='Card.TFrame',
                               padding=(self.px(16), self.px(16)))
        theme_body.pack(fill=BOTH, expand=True)
        theme_head = ttk.Frame(theme_body, style='Card.TFrame')
        theme_head.pack(fill=X)
        ttk.Label(theme_head, text='界面主题',
                  style='SettingsCardTitle.TLabel').pack(side=LEFT)
        self.theme_hint_var = StringVar(
            value='当前：%s' % THEME_LABELS.get(self.theme_key,
                                               self.theme_key))
        ttk.Label(theme_head, textvariable=self.theme_hint_var,
                  style='SettingsCardSub.TLabel').pack(side=RIGHT)
        ttk.Label(theme_body,
                  text='背景、窗口图标、按钮和列表同步切换，选择后立即生效并记住',
                  style='SettingsBody.TLabel', justify=LEFT,
                  wraplength=self.px(520)).pack(anchor='w',
                                                pady=(self.px(4), 0))

        theme_grid = tk.Frame(theme_body, bg=CARD)
        theme_grid.pack(fill=X, pady=(self.px(12), 0))
        for index in range(3):
            theme_grid.columnconfigure(index, weight=1, uniform='theme')
        self._theme_tiles: dict[str, tuple] = {}
        self._theme_stickers: dict[str, object] = {}
        self._theme_preview_photos: dict[str, object] = {}
        for index, key in enumerate(THEME_ORDER):
            tile = tk.Frame(theme_grid, bg=CARD, bd=0, highlightthickness=2,
                            highlightbackground=BORDER, cursor='hand2')
            tile.grid(row=0, column=index, sticky='nsew',
                      padx=(0, self.px(8)) if index < 2 else 0)
            preview = render_theme_preview(key, self.px(144), self.px(88))
            if preview is not None and ImageTk is not None:
                photo = ImageTk.PhotoImage(preview)
                self._theme_preview_photos[key] = photo
                preview_label = tk.Label(tile, image=photo, bg=CARD, bd=0)
                preview_label.pack(padx=self.px(8), pady=(self.px(8), 0))
            else:
                preview_label = tk.Label(tile, text='主题预览', bg=CARD,
                                         fg=TEXT_FAINT, bd=0, height=4)
                preview_label.pack(fill=X, padx=self.px(8),
                                   pady=(self.px(8), 0))
            title_row = tk.Frame(tile, bg=CARD, cursor='hand2')
            title_row.pack(fill=X, padx=self.px(8), pady=(self.px(8), 0))
            name = tk.Label(title_row, text=THEME_LABELS[key], bg=CARD,
                            fg=TEXT_DARK, font=(FONT, 11, 'bold'),
                            anchor='w', cursor='hand2')
            name.pack(side=LEFT, anchor='w')
            sticker = None
            if key == 'dog' and ImageTk is not None:
                sticker_img = render_user_asset(
                    'user_asset_04.png',
                    (self.px(44), self.px(44)), CARD,
                    crop=(0.26, 0.30, 0.74, 0.80))
                if sticker_img is not None:
                    sticker_photo = ImageTk.PhotoImage(sticker_img)
                    self._theme_sticker_photos = getattr(
                        self, '_theme_sticker_photos', {})
                    self._theme_sticker_photos[key] = sticker_photo
                    sticker = tk.Label(
                        title_row, image=sticker_photo, bg=CARD, bd=0,
                        highlightthickness=1,
                        highlightbackground=BORDER,
                        padx=self.px(4), pady=self.px(4), cursor='hand2')
                    sticker.pack(side=RIGHT, padx=(self.px(8), 0))
            self._theme_stickers[key] = sticker
            desc = tk.Label(tile, text=THEME_DESCRIPTIONS[key], bg=CARD,
                            fg=TEXT_FAINT, font=(FONT, 9), anchor='w',
                            cursor='hand2')
            desc.pack(fill=X, padx=self.px(8),
                      pady=(self.px(2), self.px(8)))
            self._theme_tiles[key] = (tile, name, desc, preview_label)
            clickable = [tile, title_row, preview_label, name, desc]
            if sticker is not None:
                clickable.append(sticker)
            for widget in clickable:
                widget.bind('<Button-1>',
                            lambda _e, k=key: self.apply_theme(k))
        tk.Label(body,
                 text='浅色与深色共用同一套版式；线条小狗采用奶油暖纸底色',
                 bg=BG, fg=TEXT_FAINT, font=(FONT, 9), anchor='w',
                 justify=LEFT, wraplength=self.px(560)).pack(
                     fill=X, pady=(self.px(12), 0))

        win.bind('<Escape>', lambda _e: win.destroy())
        win.protocol('WM_DELETE_WINDOW', lambda: self._close_settings(win))
        win.bind('<Destroy>', self._on_settings_destroy)
        self._refresh_settings_ui(rebuild_logo=True)
        win.update_idletasks()
        x = self.root.winfo_rootx() + max(
            0, (self.root.winfo_width() - win.winfo_reqwidth()) // 2)
        y = self.root.winfo_rooty() + max(
            0, (self.root.winfo_height() - win.winfo_reqheight()) // 3)
        win.geometry('+%d+%d' % (x, y))
        try:
            win.grab_set()
        except Exception:  # noqa: BLE001
            pass

    def _close_settings(self, win) -> None:
        try:
            win.grab_release()
        except Exception:  # noqa: BLE001
            pass
        win.destroy()

    def _on_settings_destroy(self, event) -> None:
        if event.widget is not getattr(self, '_settings_window', None):
            return
        self._settings_window = None
        self.update_button = None

    def _refresh_settings_ui(self, rebuild_logo: bool = False) -> None:
        win = getattr(self, '_settings_window', None)
        if win is None or not win.winfo_exists():
            return
        if getattr(self, '_settings_logo', None) is not None:
            image = render_app_icon(self.theme_key, self.px(44))
            if image is not None and ImageTk is not None:
                try:
                    photo = ImageTk.PhotoImage(image)
                    self._settings_logo.configure(image=photo)
                    self._settings_logo.image = photo
                    self._settings_logo_photo = photo
                except Exception:  # noqa: BLE001
                    pass
        for key, (tile, name, desc, preview) in getattr(
                self, '_theme_tiles', {}).items():
            selected = key == self.theme_key
            try:
                tile.configure(highlightthickness=2,
                               highlightbackground=ACCENT if selected
                               else BORDER)
                name.configure(text=('✓ ' if selected else '')
                               + THEME_LABELS[key],
                               fg=ACCENT_DARK if selected else TEXT_DARK)
                desc.configure(fg=TEXT_MUTED if selected else TEXT_FAINT)
                for widget in (tile, name, desc, preview):
                    widget.configure(bg=CARD)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.theme_hint_var.set('当前：%s'
                                    % THEME_LABELS.get(self.theme_key,
                                                       self.theme_key))
        except Exception:  # noqa: BLE001
            pass
        if self.settings_update_status.get() and not self._update_busy:
            self._set_update_controls(UPDATE_BUTTON_TEXT, 'normal')

    def _set_update_controls(self, text: str, state: str) -> None:
        button = getattr(self, 'update_button', None)
        if button is None:
            return
        try:
            if button.winfo_exists():
                button.configure(text=text, state=state)
        except Exception:  # noqa: BLE001
            pass

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
        refreshers = getattr(self, '_check_refreshers', None)
        if refreshers is not None:
            refreshers.append(refresh)
        return holder

    def _build_check_icons(self) -> None:
        """生成列表勾选、折叠箭头等位图图标；高 DPI 下也保持锐利。"""

        self.check_icons: dict[str, tk.PhotoImage] = {}
        self.ui_icons: dict[str, tk.PhotoImage] = {}
        if Image is None:
            return
        size = self.px(24)
        child_shift = self.px(14)
        for kind in ('unchecked', 'checked', 'partial'):
            base = self._draw_check_icon(kind, size)
            buffer = io.BytesIO()
            base.save(buffer, format='PNG')
            data = base64.b64encode(buffer.getvalue()).decode('ascii')
            self.check_icons[kind] = tk.PhotoImage(data=data)
            self.check_icons[kind + '_leaf'] = tk.PhotoImage(data=data)
            # 展开后的子文件行：左侧留透明边距，让勾选按钮比父行再右移
            # 一点；收起时子行不显示，父行图标不受影响。
            child = Image.new('RGBA', (size + child_shift, size),
                              (0, 0, 0, 0))
            child.paste(base, (child_shift, 0))
            buffer = io.BytesIO()
            child.save(buffer, format='PNG')
            data = base64.b64encode(buffer.getvalue()).decode('ascii')
            self.check_icons['child_' + kind] = tk.PhotoImage(data=data)

        toggle_size = self.px(16)
        for kind in ('unchecked', 'checked'):
            image = self._draw_check_icon(kind, toggle_size)
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            data = base64.b64encode(buffer.getvalue()).decode('ascii')
            self.check_icons['toggle_' + kind] = tk.PhotoImage(data=data)

        chevron_size = self.px(16)
        for kind in ('chevron_down', 'chevron_up'):
            for tone, color in (('muted', _rgb(TEXT_MUTED) + (255,)),
                                ('accent', _rgb(ACCENT_DARK) + (255,))):
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
        """画勾选框：浅/深色是圆角方框，线条小狗是爪印。"""

        if getattr(self, 'theme_key', 'light') == 'dog':
            return self._draw_paw_check(kind, size)
        scale = 4
        pixels = max(12, size) * scale
        image = Image.new('RGBA', (pixels, pixels), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        pad = max(2, int(pixels * 0.07))
        radius = max(3, int(pixels * 0.24))
        border = max(2, int(pixels * 0.07))
        if kind == 'checked':
            fill, outline = _rgb(ACCENT) + (255,), _rgb(ACCENT_DARK) + (255,)
        elif kind == 'partial':
            fill, outline = (_rgb(ACCENT_SOFT) + (255,),
                             _rgb(ACCENT) + (255,))
        else:
            fill, outline = _rgb(CARD) + (255,), _rgb(BORDER_STRONG) + (255,)
        draw.rounded_rectangle((pad, pad, pixels - pad, pixels - pad),
                               radius=radius, fill=fill, outline=outline,
                               width=border)
        stroke = max(3, int(pixels * 0.10))
        if kind == 'checked':
            draw.line([(pixels * 0.27, pixels * 0.52),
                       (pixels * 0.44, pixels * 0.69),
                       (pixels * 0.75, pixels * 0.31)],
                      fill=_rgb(ON_ACCENT) + (255,), width=stroke,
                      joint='curve')
        elif kind == 'partial':
            draw.line([(pixels * 0.28, pixels * 0.50),
                       (pixels * 0.72, pixels * 0.50)],
                      fill=_rgb(ACCENT_DARK) + (255,), width=stroke)
        return image.resize((max(12, size), max(12, size)),
                            Image.Resampling.LANCZOS)

    def _draw_paw_check(self, kind: str, size: int):
        """线条小狗主题的爪印勾选图标：未选是线稿，选中是暖黄实心。"""

        scale = 4
        pixels = max(12, size) * scale
        image = Image.new('RGBA', (pixels, pixels), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        ink = _rgb(TEXT_DARK) + (255,)
        width = max(2, int(pixels * 0.065))
        if kind == 'checked':
            fill = _rgb(ACCENT) + (255,)
        elif kind == 'partial':
            fill = _rgb(ACCENT_SOFT) + (255,)
        else:
            fill = _rgb(CARD) + (255,)
        cx = pixels * 0.5
        pad_y = pixels * 0.64
        draw.ellipse((cx - pixels * 0.24, pad_y - pixels * 0.16,
                      cx + pixels * 0.24, pad_y + pixels * 0.22),
                     fill=fill, outline=ink, width=width)
        toe = pixels * 0.13
        for dx, dy in ((-0.29, -0.24), (0.0, -0.34), (0.29, -0.24)):
            tx = cx + pixels * dx
            ty = pixels * 0.40 + pixels * dy
            draw.ellipse((tx - toe / 2, ty - toe / 2,
                          tx + toe / 2, ty + toe / 2),
                         fill=fill, outline=ink, width=width)
        if kind == 'checked':
            draw.line([(cx - pixels * 0.11, pad_y),
                       (cx - pixels * 0.02, pad_y + pixels * 0.09),
                       (cx + pixels * 0.13, pad_y - pixels * 0.08)],
                      fill=ink, width=max(2, int(pixels * 0.07)),
                      joint='curve')
        elif kind == 'partial':
            draw.line((cx - pixels * 0.10, pad_y + pixels * 0.02,
                       cx + pixels * 0.10, pad_y + pixels * 0.02),
                      fill=ink, width=max(2, int(pixels * 0.07)))
        return image.resize((max(12, size), max(12, size)),
                            Image.Resampling.LANCZOS)

    def _check_icon(self, kind: str, leaf: bool = False):
        icons = getattr(self, 'check_icons', None)
        if not icons:
            return None
        return icons.get(kind + ('_leaf' if leaf else ''))

    def _refresh_empty_states(self) -> None:
        """列表空态：狗主题用用户提供的并排素材，其他主题只留文字。"""

        if not hasattr(self, 'board_empty'):
            return
        image = None
        if self.theme_key == 'dog':
            image = render_user_asset(
                'user_asset_06.png',
                (self.px(260), self.px(110)), CARD)
        for prefix, frame, label, tree, text in (
                ('board', self.board_empty, self.board_empty_image,
                 self.board_tree, self.board_empty_text),
                ('feedback', self.feedback_empty, self.feedback_empty_image,
                 self.feedback_tree, self.feedback_empty_text)):
            if tree.get_children(''):
                frame.place_forget()
                continue
            photo = None
            if image is not None and ImageTk is not None:
                try:
                    photo = ImageTk.PhotoImage(image)
                except Exception:  # noqa: BLE001
                    photo = None
            if photo is not None:
                setattr(self, '_%s_empty_photo' % prefix, photo)
                label.configure(image=photo)
                if not label.winfo_manager():
                    label.pack(before=text, pady=(0, 0))
            else:
                label.pack_forget()
            frame.place(relx=0.5, rely=0.55, anchor='center')
            frame.lift()

    def _make_tab(self, parent, key: str, label: str):
        """安静的标签页：文字 + 2px 底线，不用整块绿色去抢主按钮。"""

        holder = tk.Frame(parent, bg=BG)
        button = tk.Button(
            holder, text=label, command=lambda: self._switch_view(key),
            bg=BG, fg=TEXT_MUTED, activebackground=BG,
            activeforeground=TEXT_DARK, font=(FONT, 12), bd=0,
            relief='flat', highlightthickness=0, padx=0, pady=self.px(8),
            cursor='hand2')
        button.pack(anchor='w')
        underline = tk.Frame(holder, bg=BG, height=self.px(2))
        underline.pack(fill=X, pady=(self.px(4), 0))
        return holder, button, underline

    def _configure_styles(self) -> None:
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
                        foreground=ON_ACCENT, borderwidth=0, relief='flat',
                        focusthickness=2, focuscolor=ACCENT_TEXT)
        style.map('Primary.TButton',
                  background=[('active', ACCENT_DARK),
                              ('disabled', ACCENT_DISABLED)],
                  foreground=[('disabled', ON_ACCENT_DISABLED)])
        style.configure('Secondary.TButton', font=(FONT, 10),
                        padding=(self.px(12), self.px(8)), background=CARD,
                        foreground=TEXT_DARK, borderwidth=1, relief='solid',
                        bordercolor=BORDER_STRONG, lightcolor=BORDER_STRONG,
                        darkcolor=BORDER_STRONG, focusthickness=2, focuscolor=ACCENT_TEXT)
        style.map('Secondary.TButton',
                  background=[('active', SURFACE_ALT),
                              ('disabled', DISABLED_BG)],
                  foreground=[('disabled', DISABLED_FG)],
                  bordercolor=[('active', BORDER_STRONG), ('disabled', BORDER)])
        style.configure('HeaderUpdate.TButton', font=(FONT, 9),
                        padding=(self.px(12), self.px(4)), background=CARD,
                        foreground=TEXT_DARK, borderwidth=1, relief='solid',
                        bordercolor=BORDER_STRONG, lightcolor=BORDER_STRONG,
                        darkcolor=BORDER_STRONG, focusthickness=2, focuscolor=ACCENT_TEXT)
        style.map('HeaderUpdate.TButton',
                  background=[('active', SURFACE_ALT), ('disabled', BG)],
                  foreground=[('active', TEXT_DARK), ('disabled', TEXT_FAINT)])
        style.configure('Quiet.TButton', font=(FONT, 10),
                        padding=(self.px(8), self.px(4)), background=CARD,
                        foreground=TEXT_MUTED, borderwidth=0, relief='flat',
                        focusthickness=2, focuscolor=ACCENT_TEXT)
        style.map('Quiet.TButton',
                  background=[('active', SURFACE_ALT)],
                  foreground=[('active', TEXT_DARK), ('disabled', DISABLED_FG)])
        style.configure('FooterQuiet.TButton', font=(FONT, 9),
                        padding=(self.px(8), self.px(4)), background=BG,
                        foreground=TEXT_MUTED, borderwidth=0, relief='flat',
                        focusthickness=2, focuscolor=ACCENT_TEXT)
        style.map('FooterQuiet.TButton',
                  background=[('active', SURFACE_ALT)],
                  foreground=[('active', TEXT_DARK), ('disabled', DISABLED_FG)])
        style.configure('Fold.TButton', font=(FONT, 10, 'bold'),
                        padding=(self.px(12), self.px(8)), background=SURFACE_ALT,
                        foreground=TEXT_DARK, borderwidth=1, relief='solid',
                        bordercolor=BORDER_STRONG, lightcolor=BORDER_STRONG,
                        darkcolor=BORDER_STRONG, focusthickness=2, focuscolor=ACCENT_TEXT)
        style.map('Fold.TButton',
                  background=[('active', HOVER_STRONG),
                              ('disabled', DISABLED_BG)],
                  foreground=[('disabled', DISABLED_FG)])
        # 设置窗口里的主题卡片和普通按钮
        style.configure('Settings.TButton', font=(FONT, 10, 'bold'),
                        padding=(self.px(16), self.px(8)), background=ACCENT,
                        foreground=ON_ACCENT, borderwidth=0, relief='flat',
                        focusthickness=2, focuscolor=ACCENT_TEXT)
        style.map('Settings.TButton',
                  background=[('active', ACCENT_DARK),
                              ('disabled', ACCENT_DISABLED)],
                  foreground=[('disabled', ON_ACCENT_DISABLED)])
        style.configure('SettingsQuiet.TButton', font=(FONT, 10),
                        padding=(self.px(12), self.px(8)), background=CARD,
                        foreground=TEXT_DARK, borderwidth=1, relief='solid',
                        bordercolor=BORDER_STRONG, lightcolor=BORDER_STRONG,
                        darkcolor=BORDER_STRONG, focusthickness=2, focuscolor=ACCENT_TEXT)
        style.map('SettingsQuiet.TButton',
                  background=[('active', SURFACE_ALT)],
                  foreground=[('active', TEXT_DARK)])
        style.configure('SettingsTitle.TLabel', background=BG,
                        foreground=TEXT_DARK, font=(FONT, 18, 'bold'))
        style.configure('SettingsSub.TLabel', background=BG,
                        foreground=TEXT_MUTED, font=(FONT, 10))
        style.configure('SettingsCardTitle.TLabel', background=CARD,
                        foreground=TEXT_DARK, font=(FONT, 12, 'bold'))
        style.configure('SettingsCardSub.TLabel', background=CARD,
                        foreground=TEXT_FAINT, font=(FONT, 9))
        style.configure('SettingsBody.TLabel', background=CARD,
                        foreground=TEXT_MUTED, font=(FONT, 10))

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
                        arrowsize=self.px(16), fieldbackground=CARD,
                        foreground=TEXT_DARK, bordercolor=BORDER_STRONG,
                        lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG,
                        arrowcolor=TEXT_MUTED)
        style.map('TCombobox',
                  fieldbackground=[('readonly', CARD)],
                  foreground=[('readonly', TEXT_DARK)],
                  bordercolor=[('focus', ACCENT)])
        style.configure('TSpinbox', padding=(self.px(8), self.px(6)),
                        arrowsize=self.px(16), fieldbackground=CARD,
                        foreground=TEXT_DARK, bordercolor=BORDER_STRONG,
                        lightcolor=BORDER_STRONG, darkcolor=BORDER_STRONG,
                        arrowcolor=TEXT_MUTED)

        style.configure('Accent.Horizontal.TProgressbar',
                        thickness=self.px(8), troughcolor=TRACK,
                        background=ACCENT, borderwidth=0, lightcolor=ACCENT,
                        darkcolor=ACCENT)
        style.configure('Thin.Horizontal.TProgressbar',
                        thickness=self.px(4), troughcolor=TRACK_SOFT,
                        background=ACCENT, borderwidth=0, lightcolor=ACCENT,
                        darkcolor=ACCENT)
        style.configure('Vertical.TScrollbar', arrowsize=self.px(12),
                        width=self.px(8), background=SCROLL,
                        troughcolor=CARD, bordercolor=CARD, lightcolor=CARD,
                        darkcolor=CARD)
        style.configure('Board.Treeview', rowheight=self.px(40),
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

    # ---------- 主题横幅 / 图标 ----------
    def _build_hero(self) -> None:
        """全宽横幅：底色、线稿与小狗都由当前主题绘制。"""

        self.hero_height = self.px(72)
        self.hero = tk.Canvas(self.root, height=self.hero_height, bd=0,
                              highlightthickness=0, bg=BG)
        self.hero.pack(fill=X, side='top')
        self.hero.bind('<Configure>', self._on_hero_resize)
        self._hero_photo = None
        self._hero_logo_photo = None
        self._hero_mascot_photo = None
        self._hero_size = (0, 0)
        self._hero_render_job = None

        self.settings_button = tk.Button(
            self.hero, text='设置', command=self.open_settings,
            takefocus=1, bg=HERO_BTN_BG, fg=HERO_BTN_FG,
            activebackground=ACCENT_SOFT, activeforeground=HERO_BTN_FG,
            font=(FONT, 10), bd=0, relief='flat',
            padx=self.px(12), pady=self.px(4), cursor='hand2',
            highlightthickness=1, highlightbackground=HERO_BTN_BORDER,
            highlightcolor=ACCENT_TEXT)
        self.settings_pill = self.settings_button
        self.settings_button.bind('<Return>',
                                  lambda _e: self.open_settings())
        self.settings_pill.place(relx=1.0, x=-self.px(24),
                                 rely=0.5, anchor='e')
        self._refresh_hero_chrome()
        self.root.after(60, lambda: self._render_hero(force=True))

    def _on_hero_resize(self, _event=None) -> None:
        if self._hero_render_job is not None:
            try:
                self.root.after_cancel(self._hero_render_job)
            except Exception:  # noqa: BLE001
                pass
        self._hero_render_job = self.root.after(
            80, lambda: self._render_hero(force=False))

    def _render_hero(self, force: bool = False) -> None:
        self._hero_render_job = None
        if not hasattr(self, 'hero') or not self.hero.winfo_exists():
            return
        width = max(self.hero.winfo_width(), self.root.winfo_width())
        height = self.hero.winfo_height() or self.hero_height
        if width <= 10 or height <= 10:
            return
        size = (int(width), int(height))
        if not force and size == self._hero_size:
            return
        self._hero_size = size
        self.hero.delete('all')
        if Image is None or ImageTk is None:
            self.hero.configure(bg=BG)
            return
        image = render_hero(self.theme_key, size[0], size[1])
        if image is None:
            self.hero.configure(bg=BG)
            return
        try:
            photo = ImageTk.PhotoImage(image)
        except Exception:  # noqa: BLE001
            return
        self._hero_photo = photo
        self.hero.create_image(0, 0, anchor='nw', image=photo, tags='hero')
        if self.theme_key == 'dog' and self._hero_mascot_photo is None:
            mascot = render_user_asset(
                'user_asset_04.png', (self.px(56), self.px(56)),
                palette_for('dog')['HERO_FROM'],
                crop=(0.26, 0.30, 0.74, 0.80))
            if mascot is not None:
                self._hero_mascot_photo = ImageTk.PhotoImage(mascot)
        if (self.theme_key == 'dog' and self._hero_mascot_photo is not None
                and width >= self.px(760)):
            self.hero.create_image(width - self.px(106), height / 2,
                                   anchor='e', image=self._hero_mascot_photo,
                                   tags='hero')

        pad = self.px(24)
        logo_size = self.px(32)
        logo_image = render_app_icon(self.theme_key, logo_size)
        if logo_image is not None:
            logo_photo = ImageTk.PhotoImage(logo_image)
            self._hero_logo_photo = logo_photo
            self.hero.create_image(pad, height / 2, anchor='w',
                                   image=logo_photo, tags='hero')
        text_x = pad + logo_size + self.px(12)
        self.hero.create_text(
            text_x, height / 2 - self.px(12), text=APP_NAME, anchor='w',
            fill=HERO_TEXT, font=(FONT, 15, 'bold'), tags='hero')
        self.hero.create_text(
            text_x, height / 2 + self.px(12),
            text='只读本机登录态 · 邀请码 Excel · 板书 PDF · 反馈统计 · v%s'
                 % APP_VERSION,
            anchor='w', fill=HERO_MUTED, font=(FONT, 9), tags='hero')
        self.hero.tag_lower('hero')

    def _refresh_hero_chrome(self) -> None:
        """刷新横幅上的设置按钮与窗口图标（不重画整张背景）。"""

        self.settings_button.configure(
            bg=HERO_BTN_BG, fg=HERO_BTN_FG,
            activebackground=ACCENT_SOFT, activeforeground=HERO_BTN_FG,
            highlightbackground=HERO_BTN_BORDER,
            highlightcolor=ACCENT_TEXT)
        if Image is not None and ImageTk is not None:
            icon = render_settings_icon(self.theme_key, self.px(16),
                                        HERO_BTN_FG)
            if icon is not None:
                try:
                    photo = ImageTk.PhotoImage(icon)
                    self._settings_icon_photo = photo
                    self.settings_button.configure(image=photo,
                                                   compound='left')
                except Exception:  # noqa: BLE001
                    pass

    def _apply_window_icon(self) -> None:
        if Image is None or ImageTk is None:
            return
        photos = []
        ico_sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                     (128, 128), (256, 256)]
        for size in (16, 24, 32, 48, 64, 128):
            image = render_app_icon(self.theme_key, size)
            if image is None:
                continue
            try:
                photos.append(ImageTk.PhotoImage(image))
            except Exception:  # noqa: BLE001
                pass
        if photos:
            self._window_icons = photos
            try:
                self.root.iconphoto(True, *photos)
            except Exception:  # noqa: BLE001
                pass
        # Windows 的标题栏会锁定 iconbitmap，必须写一个当前主题的 .ico
        # 再重新设置，iconphoto 单独调用不会替换标题栏小图标。
        if sys.platform == 'win32':
            try:
                big = render_app_icon(self.theme_key, 256)
                if big is not None:
                    target = (Path(tempfile.gettempdir())
                              / ('aiwenyun-theme-%s.ico' % self.theme_key))
                    big.save(target, format='ICO', sizes=ico_sizes)
                    self._theme_icon_path = str(target)
                    self.root.iconbitmap(str(target))
            except Exception:  # noqa: BLE001
                pass
        win = getattr(self, '_settings_window', None)
        if win is not None and win.winfo_exists():
            try:
                win.iconphoto(True, *getattr(self, '_window_icons', []))
                if sys.platform == 'win32' and getattr(
                        self, '_theme_icon_path', ''):
                    win.iconbitmap(self._theme_icon_path)
            except Exception:  # noqa: BLE001
                pass

    def _build_ui(self) -> None:
        for font_name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont',
                          'TkHeadingFont'):
            try:
                tkfont.nametofont(font_name).configure(family=FONT, size=10)
            except Exception:  # noqa: BLE001
                pass
        self._configure_styles()

        self._build_hero()
        outer = ttk.Frame(self.root, padding=(self.px(24), self.px(8),
                                              self.px(24), self.px(12)))
        outer.pack(fill=BOTH, expand=True)

        # ---------- 功能切换 ----------
        tabbar = ttk.Frame(outer)
        tabbar.pack(fill=X, pady=(self.px(2), 0))
        self.tab_buttons: dict[str, tuple[tk.Button, tk.Frame]] = {}
        for key, label in (('invite', '邀请码导出'),
                           ('board', '板书导出'),
                           ('feedback', '反馈文件导出')):
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
        invite_opts.pack(fill=X, pady=(self.px(16), 0))
        ttk.Label(invite_opts, text='从', style='Muted.TLabel').pack(side=LEFT)
        ttk.Spinbox(invite_opts, from_=0, to=365, width=4,
                    textvariable=self.past_days).pack(
                        side=LEFT, padx=(self.px(8), self.px(8)))
        ttk.Label(invite_opts, text='天前开始',
                  style='Muted.TLabel').pack(side=LEFT)
        ttk.Label(invite_opts, text='并发',
                  style='Muted.TLabel').pack(side=LEFT, padx=(self.px(24), 0))
        ttk.Spinbox(invite_opts, from_=1, to=16, width=4,
                    textvariable=self.workers).pack(
                        side=LEFT, padx=(self.px(8), self.px(8)))
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
            tk.Label(row, text='%d.' % number, bg=SURFACE, fg=TEXT_FAINT,
                     font=(MONO, 9), width=3, anchor='nw').pack(
                         side=LEFT, anchor='n')
            tk.Label(row, text=line, bg=SURFACE, fg=TEXT_MUTED,
                     font=(FONT, 10), justify=LEFT, anchor='w',
                     wraplength=self.px(360)).pack(
                         side=LEFT, fill=X, expand=True)
        tk.Frame(tips, bg=BORDER, height=1).pack(
            fill=X, pady=(self.px(12), self.px(8)))
        tk.Label(tips, text='只读：不读取 cookie / 聊天记录 / 通讯录，'
                            'token 不写入文件', bg=SURFACE, fg=TEXT_FAINT,
                 font=(FONT, 9), justify=LEFT, anchor='w',
                 wraplength=self.px(392)).pack(anchor='w')

        self.invite_log = LogPanel(
            invite_view, title='邀请码运行日志',
            subtitle='导出与登录诊断', height=6, px=self.px)
        self.invite_log.frame.grid(row=1, column=0, sticky='nsew',
                                   pady=(self.px(16), 0))

        build_board_view(self, content, font=FONT,
                         progress_panel=ProgressPanel, log_panel=LogPanel)
        build_feedback_view(self, content, font=FONT,
                            progress_panel=ProgressPanel, log_panel=LogPanel)

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
        self.export_results: dict[str, tuple[str, Path | None]] = {}
        self.export_result_row = ttk.Frame(outer)
        self.export_result_detail = StringVar(master=self.root)
        self.export_result_folder = StringVar(master=self.root)
        ttk.Label(self.export_result_row,
                  textvariable=self.export_result_detail).pack(side=LEFT)
        self.export_folder_button = ttk.Button(
            self.export_result_row, text='打开输出目录',
            style='FooterQuiet.TButton', command=self._open_export_folder)
        self.export_folder_button.pack(side=RIGHT)
        tk.Entry(self.export_result_row, textvariable=self.export_result_folder,
                 state='readonly', readonlybackground=CARD, fg=TEXT_DARK,
                 bd=0, highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=ACCENT_TEXT, width=1).pack(
                     side=LEFT, fill=X, expand=True, padx=self.px(10))
        ttk.Button(footer, text='使用说明', style='FooterQuiet.TButton',
                   command=self.show_help).pack(side=LEFT)
        self.open_button = ttk.Button(footer, text='打开上次文件',
                                      style='FooterQuiet.TButton',
                                      command=self.open_last,
                                      state='disabled')
        self.open_button.pack(side=LEFT, padx=(self.px(4), 0))
        ttk.Label(footer, text='保存位置：导出时选择 · 默认「爱问云邀请码」/'
                               '「爱问云板书导出」/「爱问云反馈文件」',
                  style='Footer.TLabel').pack(side=RIGHT)

        self._base_title = '%s %s' % (APP_NAME, APP_VERSION)
        self._switch_view('invite')
        self.append_log('就绪：正在检测本机已登录的爱问云；识别到后可直接列出板书，'
                        '不需要账号密码。未匹配到班级的条目→杂项/未分类。',
                        target='board')
        self._refresh_empty_states()

    def _switch_view(self, view: str) -> None:
        """在邀请码/板书/反馈三个功能页之间切换。"""

        if view not in ('invite', 'board', 'feedback'):
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
                            ('feedback', self.feedback_view)):
            if key == view:
                widget.grid(row=0, column=0, sticky='nsew')
            else:
                widget.grid_remove()
        self._refresh_export_result()

    def _show_export_result(self, view: str, detail: str,
                            folder: Path | None) -> None:
        self.export_results[view] = (detail, folder)
        if self.view == view:
            self._refresh_export_result()

    def _refresh_export_result(self) -> None:
        result = self.export_results.get(self.view)
        if result is None:
            self.export_result_row.pack_forget()
            return
        detail, folder = result
        self.export_result_detail.set('上次导出 · ' + detail)
        self.export_result_folder.set(str(folder) if folder else '无输出目录')
        self.export_folder_button.configure(
            state='normal' if folder and folder.is_dir() else 'disabled')
        self.export_result_row.pack(side='bottom', fill=X,
                                    pady=(self.px(8), 0), before=self.content)

    def _open_export_folder(self) -> None:
        result = self.export_results.get(self.view)
        folder = result[1] if result else None
        try:
            if folder is None or not folder.is_dir():
                raise FileNotFoundError('输出目录不存在')
            open_path(folder)
        except OSError as error:
            messagebox.showwarning(APP_NAME, '无法打开输出目录：%s' % error,
                                   parent=self.root)

    # ---------- 邀请码导出流程 ----------
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

    def _renew_token(self) -> str:
        """邀请码导出期间 token 失效：用界面账号重新登录取新会话。"""

        if not self._relogin():
            raise RuntimeError('界面账号重新登录失败')
        return self.login_result.token

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
            self.account_row.pack(fill=X, pady=(self.px(8), 0),
                                  before=self.hint_label)

        if client_mode and not busy:
            if not self.use_account_link.winfo_manager():
                self.use_account_link.pack(side=LEFT, padx=(self.px(12), 0))
        elif self.use_account_link.winfo_manager():
            self.use_account_link.pack_forget()

        self.board_list_button.configure(
            state='normal' if (has_auth and not busy) else 'disabled')
        self._refresh_board_state()
        self._refresh_feedback_badge(has_auth, client_mode)
        self._refresh_feedback_state()

    def _refresh_feedback_badge(self, has_auth: bool, client_mode: bool) -> None:
        """反馈页右上角的登录态徽标（和板书页保持同一套说法）。"""

        badge = getattr(self, 'feedback_badge', None)
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
        """连上登录态后自动列一次板书，随后接着列一次反馈清单。

        首次连上、清单还是空的，就直接把时间段内的板书拉出来，用户不用
        先点「列出板书」；手动重新检测（``force``）则强制刷新一次。
        两个清单共用同一个忙碌标记，所以反馈清单等板书列完再开始。
        """

        if not self._has_board_auth():
            return
        if self.busy.get():
            self.root.after(400, lambda: self._auto_list_if_empty(force))
            return
        if not self.board_records or force:
            self._pending_feedback_auto = True
            self.on_list_boards()
            return
        if not self.feedback_items or force:
            self.on_list_feedbacks()

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
            self._post('progress_detail', '正在读取爱问云班级列表…')
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
            self._post('progress_detail', '正在读取该时间段的课堂记录…')
            try:
                records = fetch_records(session, from_ms, to_ms,
                                        cancel_event=self.cancel_event)
                if check_cancel():
                    return
                # 用 liveInfo 的 groups 把课堂精确映射到官方班级
                # （26秋7MA+31 -> 2026027MA+31β1/β3），再合并 α/β 变体。
                self._post('progress_detail',
                           '已读取 %d 节课，正在对应官方班级…' % len(records))
                self._thread_log('正在读取课堂对应的班级…')

                def mapping_progress(done, total):
                    self._post(
                        'progress_detail',
                        '已读取 %d 节课，正在对应官方班级 %d / %d…'
                        % (len(records), done, total))

                try:
                    enrich_record_groups(session, records,
                                         on_progress=mapping_progress,
                                         cancel_event=self.cancel_event)
                except (BatchCancelled, TokenExpired):
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
        """列出完成：按「学期+班级」分组，班级做成横排按钮，列表只显示当前班级。"""

        if isinstance(payload, dict):
            records = list(payload.get('records') or [])
            class_names = list(payload.get('class_names') or [])
        else:                       # 兼容旧的纯记录列表调用
            records = list(payload or [])
            class_names = []

        self.board_class_names = [str(name).strip() for name in class_names
                                  if str(name).strip()]
        original_count = len(records)
        if self.board_class_names:
            records = [record for record in records
                       if record_matches_class_list(
                           record, self.board_class_names)]
        hidden_count = original_count - len(records)
        self.board_records = records
        self.board_group_of = {}
        self.board_tree.delete(*self.board_tree.get_children())

        groups = group_board_records(records, self.board_class_names)
        self.board_group_labels = group_display_names(groups, records)
        ordered_names = ordered_group_names(
            groups, records, labels=self.board_group_labels)
        self.board_display_order = list(ordered_names)
        self.board_groups = {name: [records[index] for index in groups[name]]
                             for name in ordered_names}
        for name, rows in self.board_groups.items():
            label = self.board_group_labels.get(name, name)
            for record in rows:
                self.board_group_of[id(record)] = label
        self.board_checked = {self._board_key(record): False
                              for record in records}
        self.board_active_class = (ordered_names[0] if ordered_names else '')
        self._build_board_class_buttons()
        self._show_board_class(self.board_active_class)

        official_count = len(ordered_names) - (
            1 if UNCLASSIFIED_GROUP in groups else 0)
        misc_count = len(groups.get(UNCLASSIFIED_GROUP, []))
        terms = {term_label(self.board_group_labels.get(name, name))
                 for name in ordered_names if name != UNCLASSIFIED_GROUP}
        term_count = len([term for term in terms if term])
        if official_count:
            meta = '共 %d 节课 · %d 个班级' % (len(records), official_count)
            if term_count > 1:
                meta += ' · %d 个学期' % term_count
        else:
            meta = '共 %d 节课' % len(records)
        if misc_count:
            meta += ' · 杂项 %d 节' % misc_count
        if hidden_count:
            meta += ' · 已隐藏 %d 节无匹配班级' % hidden_count
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
            first_label = self.board_group_labels.get(
                self.board_active_class, self.board_active_class)
            self.client_hint.set(
                '已自动列出 %d 节课；点班级按钮（如「%s」）换班，'
                '勾选后点「导出所选板书」。' % (len(records), first_label))
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
                    '共 %d 节课可勾选；按%s分组并加上学期：%d 个班级'
                    '（α/β1/β2/β3 已合并，如「26秋7MA+31」）%s；列表最新在上。'
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
        elif getattr(self, '_pending_feedback_auto', False):
            # 连上登录态后的自动流程：板书列完接着列反馈清单
            self._pending_feedback_auto = False
            self.root.after(120, self.on_list_feedbacks)

    def _board_key(self, record: dict) -> str:
        """课堂记录的唯一键（换班级后勾选不丢）。"""

        return str(record.get('id') or id(record))

    def _board_chip_text(self, name: str) -> str:
        """班级按钮文字：学期+班级，勾选后就地补「已选 N」。"""

        label = self.board_group_labels.get(name, name)
        rows = self.board_groups.get(name) or []
        picked = sum(1 for record in rows
                     if self.board_checked.get(self._board_key(record)))
        return label if not picked else '%s · 已选 %d' % (label, picked)

    def _board_chip_width(self, text: str) -> int:
        return self._class_chip_font.measure(text) + 2 * self.px(12) + 2

    def _board_class_budget(self) -> int:
        """班级条能用的像素宽度：窗口窄时少摆几个，把剩下的收进「更多」。"""

        width = self.board_class_bar.winfo_width()
        if width <= 1:
            width = self.root.winfo_width() - self.px(84)
        return max(self.px(300), width - self.px(2))

    def _board_visible_classes(self) -> tuple[list[str], int]:
        """按当前宽度算出还能摆几个班；当前班永远保证可见。"""

        ordered = list(self.board_display_order or self.board_groups)
        if not ordered:
            return [], 0
        budget = self._board_class_budget()
        gap = self.px(8)
        more_width = (self._board_chip_width('更多 99 个班')
                      + self.px(22) + gap)
        used = 0
        visible: list[str] = []
        for index, name in enumerate(ordered):
            width = self._board_chip_width(self._board_chip_text(name)) + gap
            remaining = len(ordered) - index - 1
            reserve = more_width if remaining else 0
            if visible and used + width + reserve > budget:
                break
            visible.append(name)
            used += width
        if len(visible) < len(ordered):
            active = self.board_active_class
            if active and active not in visible:
                # 从「更多」里选中的班要能立刻看到：用当前班顶掉最后一个，
                # 再按实际宽度往前收，别把「更多」按钮挤出窗口。
                candidate = (list(visible[:-1]) + [active] if visible
                             else [active])
                while len(candidate) > 1:
                    total = sum(self._board_chip_width(
                        self._board_chip_text(item)) + gap
                        for item in candidate) + more_width
                    if total <= budget:
                        break
                    candidate.pop(-2)
                visible = candidate
        hidden = len(ordered) - len(visible)
        return visible, max(0, hidden)

    def _build_board_class_buttons(self) -> None:
        """横排班级按钮：按窗口宽度自适应，放不下的进「更多」菜单。"""

        for child in self.board_class_bar.winfo_children():
            child.destroy()
        self.board_class_buttons = {}
        self.board_more_button = None
        self._board_hidden_count = 0
        if not self.board_groups:
            return
        visible, hidden = self._board_visible_classes()
        gap = self.px(8)
        for name in visible:
            button = tk.Button(
                self.board_class_bar, text=self._board_chip_text(name),
                bd=0, relief='flat',
                highlightthickness=0, padx=self.px(12), pady=self.px(6),
                font=(FONT, 10), bg=SURFACE, fg=TEXT_DARK, cursor='hand2',
                activebackground=SURFACE_ALT, activeforeground=TEXT_DARK,
                command=lambda n=name: self._show_board_class(n))
            button.pack(side=LEFT, padx=(0, gap))
            self.board_class_buttons[name] = button
        if hidden:
            chevron = self._icon('chevron_down_muted')
            more_text = '更多 %d 个班' % hidden
            if chevron is None:
                more_text += ' ▾'
            self.board_more_button = tk.Button(
                self.board_class_bar, text=more_text,
                image=chevron, compound='right' if chevron else 'none',
                bd=0, relief='flat', highlightthickness=0,
                padx=self.px(12), pady=self.px(6), font=(FONT, 10),
                bg=SURFACE_ALT, fg=TEXT_MUTED, cursor='hand2',
                activebackground=SURFACE, activeforeground=TEXT_DARK,
                command=self._show_board_class_menu)
            self.board_more_button.pack(side=LEFT, padx=(0, gap))
            self._board_hidden_count = hidden

    def _schedule_board_class_relayout(self) -> None:
        if not self.board_groups or self._board_relayout_job is not None:
            return
        visible, hidden = self._board_visible_classes()
        if (visible == list(self.board_class_buttons)
                and hidden == self._board_hidden_count):
            return
        self._board_relayout_job = self.root.after(
            80, self._relayout_board_classes)

    def _relayout_board_classes(self) -> None:
        self._board_relayout_job = None
        if not self.board_groups:
            return
        self._build_board_class_buttons()
        self._refresh_board_state()

    def _on_board_class_bar_resize(self, event) -> None:
        if abs(event.width - self._board_bar_width) < self.px(12):
            return
        self._board_bar_width = event.width
        self._schedule_board_class_relayout()

    def _show_board_class_menu(self) -> None:
        """「更多」菜单：按学期分组列出全部班级，选中即切换。"""

        button = self.board_more_button
        if button is None or not self.board_display_order:
            return
        menu = tk.Menu(
            self.root, tearoff=0, font=(FONT, 10), bg=CARD, fg=TEXT_DARK,
            activebackground=ACCENT_SOFT, activeforeground=TEXT_DARK,
            bd=0, relief='flat', activeborderwidth=0)
        submenus: list[tk.Menu] = []
        terms: dict[str, list[str]] = {}
        for name in self.board_display_order:
            label = self.board_group_labels.get(name, name)
            terms.setdefault(term_label(label) or '其他', []).append(name)
        for term, names in terms.items():
            sub = tk.Menu(
                menu, tearoff=0, font=(FONT, 10), bg=CARD, fg=TEXT_DARK,
                activebackground=ACCENT_SOFT, activeforeground=TEXT_DARK,
                bd=0, relief='flat', activeborderwidth=0)
            for name in names:
                label = self.board_group_labels.get(name, name)
                rows = self.board_groups.get(name) or []
                picked = sum(1 for record in rows
                             if self.board_checked.get(
                                 self._board_key(record)))
                text = '%s（%d 节%s）' % (
                    label, len(rows),
                    '，已选 %d' % picked if picked else '')
                if name == self.board_active_class:
                    text = '✓ ' + text
                sub.add_command(
                    label=text,
                    command=lambda n=name: self.root.after(
                        10, lambda n=n: self._show_board_class(n)))
            submenus.append(sub)
            menu.add_cascade(label='%s（%d 个班）' % (term, len(names)),
                             menu=sub)
        # Tk 不会替我们保住子菜单的 Python 引用
        self._board_menu_ref = (menu, submenus)
        try:
            menu.tk_popup(button.winfo_rootx(),
                          button.winfo_rooty() + button.winfo_height()
                          + self.px(2))
        finally:
            try:
                menu.unpost()
            except Exception:  # noqa: BLE001
                pass
            menu.grab_release()

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
        if name and name not in self.board_class_buttons:
            self._build_board_class_buttons()
        self._refresh_board_state()
        self._refresh_empty_states()

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
            button.configure(
                text=self._board_chip_text(name),
                bg=ACCENT if active else SURFACE,
                fg=ON_ACCENT if active else TEXT_DARK,
                activebackground=ACCENT_DARK if active else SURFACE_ALT,
                activeforeground=ON_ACCENT if active else TEXT_DARK)

        can_select = (self._has_board_auth() and not self.busy.get()
                      and bool(total_count))
        for button in (self.select_all_button, self.clear_button):
            button.configure(state='normal' if can_select else 'disabled')
        self.board_all_button.configure(
            state='normal' if (can_select and checked_count) else 'disabled')
        # 勾选后按钮文字会变长，必要时重新算「更多」的折叠位置
        self._schedule_board_class_relayout()

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
        self.board_progress.begin(
            '正在导出板书', '正在读取每节课的板书清单…',
            indeterminate=True,
            hint='多节课并行导出 · 可继续查看列表，不会中断')
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
        try:
            out_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            # 保存目录不可创建（权限/同名文件/磁盘满）时必须回报界面，
            # 否则工作线程静默退出，按钮会一直停在「正在导出」。
            self._post('error', '无法创建保存目录：%s' % error)
            return
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
                      getattr(self, 'feedback_progress', None)):
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
                elif kind == 'progress_detail':
                    panel = self._active_progress()
                    if panel is not None:
                        panel.update(0, detail=str(payload),
                                     indeterminate=True)
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
                elif kind == 'feedbacks_listed':
                    self._feedbacks_listed(payload)
                elif kind == 'feedback_event':
                    self._on_feedback_event(payload)
                elif kind == 'feedback_done':
                    self._finish_feedbacks(payload)
                elif kind == 'feedback_list_error':
                    panel = self._active_progress()
                    self.set_busy(False)
                    if panel is not None:
                        panel.fail('列出反馈失败', str(payload),
                                   hint='确认客户端仍登录后点「重新检测」',
                                   state='失败')
                    self.status.set('列出反馈失败。')
                    self.append_log('列出反馈失败：%s' % payload,
                                    target='feedback')
                    self._refresh_feedback_state()
                elif kind == 'update_checked':
                    self._finish_update_check(payload)
                elif kind == 'update_progress':
                    self._on_update_progress(payload)
                elif kind == 'update_ready':
                    self._on_update_ready(payload)
                elif kind == 'update_failed':
                    self._on_update_failed(payload)
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
        if self._busy_kind == 'feedback':
            return self.feedback_progress
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
        self._show_export_result(
            'invite', '成功 %d 条 · 失败 %d 条' % (summary.success, summary.failed),
            Path(summary.path).parent)
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
        out_root = Path(payload['out_root']) if payload.get('out_root') else None
        self._show_export_result(
            'board', '成功 %d 份 PDF · 失败或跳过 %d 节' % (
                total_files, len(failed)), out_root)
        detail = '%d 份 PDF · %d 个班级%s' % (
            total_files, len(groups),
            ' · %d 节无板书/跳过' % len(failed) if failed else '')
        self.board_progress.complete(
            '板书导出完成', detail,
            hint='可在板书页查看结果和输出目录')
        self.status.set('板书导出完成：%d 份 PDF，%d 个班级%s' % (
            total_files, len(groups),
            '，%d 节无板书/跳过' % len(failed) if failed else ''))
        self.append_log('', target='board')
        self.append_log('===== 板书导出汇总（按学期+班级分组，%s）====='
                        % payload.get('preset', ''), target='board')
        for group_name in sorted(
                groups,
                key=lambda name: (group_sort_key(name), name),
                reverse=True):
            self.append_log('[班级] %s（%d 份 PDF）' % (
                group_name, len(groups[group_name])), target='board')
            for path in groups[group_name]:
                self.append_log('    %s' % Path(path).name, target='board')
        if failed:
            self.append_log('未导出（无板书/生成中/失败）：%d 节：%s'
                            % (len(failed), '、'.join(failed)),
                            target='board')
        if out_root is not None and out_root.is_dir():
            self.append_log('保存位置：%s' % out_root, target='board')
            try:
                open_path(out_root)
            except OSError:
                pass

    # ---------- 反馈文件导出 ----------
    def _feedback_log_rows(self) -> int:
        line_height = max(1, self._log_line_height(self.feedback_log))
        min_tree = self.px(30) + self.px(36) * 2
        room = self.feedback_tree.winfo_height() - min_tree - self.px(28)
        return max(0, min(6, int(room // line_height)))

    def on_toggle_feedback_log(self) -> None:
        """展开/收起反馈日志；只拿列表用不掉的高度。"""

        log = self.feedback_log
        if log.expanded:
            log.set_expanded(False)
            return
        rows = self._feedback_log_rows()
        if rows < 2:
            self.status.set('窗口高度不够：先最大化窗口，再展开「反馈运行日志」。')
            return
        log.text.configure(height=rows)
        log.set_expanded(True)

    def _auto_list_feedbacks(self) -> None:
        """时间段变了就自动重列（有登录态、且不在忙）。"""

        if not self._has_board_auth():
            return
        if self.busy.get():
            self._pending_feedback_list = True
            return
        self.on_list_feedbacks()

    def on_list_feedbacks(self) -> None:
        """按时间段列出历史课堂和作业，供选择后一起导出。"""

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
                self.feedback_range.get(), self.feedback_from.get().strip(),
                self.feedback_to.get().strip())
        except ValueError as error:
            messagebox.showwarning(APP_NAME, str(error))
            return
        self.cancel_event = threading.Event()
        self._busy_kind = 'feedback'
        self.set_busy(True)
        self.feedback_progress.begin(
            '正在列出反馈清单', '正在读取课堂、班级与作业信息…',
            indeterminate=True, hint='只读清单，不会修改课堂数据')
        self.status.set('正在列出课堂与作业…')
        self.append_log('列出反馈清单（%s）…' % self.feedback_range.get(),
                        target='feedback')
        self.worker = threading.Thread(
            target=self._feedback_list_work, args=(from_ms, to_ms),
            daemon=True)
        self.worker.start()

    def _feedback_list_work(self, from_ms: int, to_ms: int) -> None:
        for attempt in range(2):
            try:
                session = self._board_session()
                self._post('progress_detail', '正在读取历史课堂清单…')
                records = fetch_records(
                    session, from_ms, to_ms,
                    cancel_event=self.cancel_event)
                class_names: list[str] = []
                try:
                    class_names = fetch_class_names(session)
                except (BatchCancelled, TokenExpired):
                    raise
                except Exception as error:  # noqa: BLE001
                    self._thread_feedback_log(
                        '班级列表读取失败（%s），将按课堂名分组。' % error)
                if records:
                    self._post(
                        'progress_detail',
                        '已读取 %d 节课，正在对应班级…' % len(records))
                    try:
                        enrich_record_groups(
                            session, records,
                            on_progress=lambda done, total: self._post(
                                'progress_detail',
                                '正在对应班级 %d / %d…' % (done, total)),
                            cancel_event=self.cancel_event)
                    except (BatchCancelled, TokenExpired, HomeworkCancelled):
                        raise
                    except Exception as error:  # noqa: BLE001
                        self._thread_feedback_log(
                            '班级映射读取失败（%s），将按课堂名分组。' % error)
                self._post('progress_detail', '正在读取作业清单…')
                try:
                    homeworks = fetch_homeworks(
                        session, from_ms, to_ms,
                        on_log=self._thread_feedback_log,
                        cancel_event=self.cancel_event)
                except (TokenExpired, HomeworkCancelled):
                    raise
                except Exception as error:  # noqa: BLE001
                    self._thread_feedback_log(
                        '作业清单读取失败（%s），本次只生成课堂权限反馈。'
                        % error)
                    homeworks = []
            except TokenExpired:
                if attempt == 0 and self._renew_board_token():
                    self._thread_feedback_log('登录态已过期，已自动重新获取，继续…')
                    continue
                self._post(
                    'feedback_list_error',
                    '登录态已失效：请确认电脑上的爱问云仍登录后'
                    '点「重新检测」，或改用账号密码登录。')
                return
            except HomeworkCancelled as error:
                self._post('canceled', str(error))
                return
            except BatchCancelled as error:
                self._post('canceled', str(error))
                return
            except Exception as error:  # noqa: BLE001
                self._post('feedback_list_error',
                           '%s: %s' % (type(error).__name__, error))
                return
            self._post('feedbacks_listed', {
                'records': records,
                'class_names': class_names,
                'homeworks': homeworks,
            })
            return

    def _thread_feedback_log(self, message: str) -> None:
        self._post('log', {'target': 'feedback', 'message': str(message)})

    @staticmethod
    def _feedback_record_key(record: dict) -> str:
        file_common = record.get('fileCommon') or {}
        return str(
            record.get('fileId')
            or file_common.get('_id')
            or file_common.get('recordId')
            or record.get('liveclass')
            or id(record))

    def _feedback_key(self, entry: dict) -> str:
        if isinstance(entry, dict) and entry.get('key'):
            return str(entry['key'])
        return self._feedback_record_key(entry)

    def _feedback_group_for_record(self, record: dict) -> str:
        key = self._feedback_record_key(record)
        return (self.feedback_group_of.get(key)
                or class_group(record.get('shortDesc') or '')
                or '未分类')

    def _feedbacks_listed(self, payload) -> None:
        """按「班级 + 课次」组装反馈包：一个课次一行，固定三项内容。"""

        if isinstance(payload, dict):
            records = list(payload.get('records') or [])
            class_names = list(payload.get('class_names') or [])
            homeworks = list(payload.get('homeworks') or [])
        else:
            records = list(payload or [])
            class_names = []
            homeworks = []

        bundles = build_feedback_bundles(
            records, homeworks, class_names=class_names)
        entries = list(bundles)
        self.feedback_group_of = {}
        assignment = {}
        for entry in entries:
            for item in (entry.get('components') or {}).values():
                if isinstance(item, dict):
                    assignment[id(item)] = entry['class']
        for title_label, items in (('history', records),
                                   ('homework', homeworks)):
            for item in items:
                key = (self._feedback_record_key(item)
                       if title_label == 'history'
                       else str(item.get('zuoyeId') or item.get('id')
                                or id(item)))
                label = assignment.get(id(item), '')
                if label:
                    self.feedback_group_of[key] = label

        self.feedback_items = entries
        self.feedback_class_order = sorted(
            {entry['class'] for entry in entries},
            key=lambda name: (
                -max((int(entry.get('time') or 0) for entry in entries
                      if entry['class'] == name), default=0),
                group_sort_key(name), name))
        self.feedback_checked = {entry['key']: False for entry in entries}
        self.feedback_file_checked = {}
        self.feedback_expanded = {}
        for entry in entries:
            for kind in COMPONENT_ORDER:
                self.feedback_file_checked[
                    self._feedback_child_id(entry['key'], kind)] = False
        self.feedback_active_class = '全部班级'
        self._build_feedback_class_buttons()
        self._show_feedback_rows()

        if entries:
            complete = sum(1 for entry in entries if not entry.get('missing'))
            missing = len(entries) - complete
            meta = '共 %d 个课次反馈 · %d 个班级 · 三项齐全 %d · 有缺失 %d' % (
                len(entries), len(self.feedback_class_order), complete, missing)
            self.feedback_meta.set(meta + ' · 按时间降序')
            self.client_hint.set(
                '已组装 %d 个课次反馈；展开课次后直接勾选要导出的文件即可。'
                % len(entries))
            self.status.set('%s；勾选后导出。' % meta)
            self.append_log(
                '%s；按班级分文件夹，每个课次输出「第X节课反馈」。'
                % meta, target='feedback')
            for name in self.feedback_class_order:
                rows = [entry for entry in entries if entry['class'] == name]
                self.append_log(
                    '  [%s] %d 个课次：%s' % (
                        name, len(rows),
                        '、'.join(entry['title'] for entry in rows)),
                    target='feedback')
        else:
            self.feedback_meta.set('该时间段没有可组装的课次反馈')
            self.client_hint.set(
                '该时间段没有可组装的课次反馈：换个时间段'
                '（如「最近30天」）再列出。')
            self.status.set('该时间段没有可组装的课次反馈：换个时间段再列出。')
            self.append_log(
                '该时间段没有课堂或作业，未生成课次反馈。', target='feedback')

        self.set_busy(False)
        self.feedback_progress.hide()
        self._refresh_feedback_state()
        if self._pending_feedback_list:
            self._pending_feedback_list = False
            self.on_list_feedbacks()

    def _feedback_entries_for_class(self, name: str) -> list[dict]:
        if not name or name == '全部班级':
            return list(self.feedback_items)
        return [entry for entry in self.feedback_items
                if entry['class'] == name]

    @staticmethod
    def _feedback_child_id(key: str, kind: str) -> str:
        return '%s|%s' % (key, kind)

    def _feedback_file_selected(self, key: str, kind: str) -> bool:
        return bool(self.feedback_file_checked.get(
            self._feedback_child_id(key, kind), False))

    def _feedback_selected_file_count(self, entry: dict | None = None) -> int:
        rows = [entry] if entry is not None else self.feedback_items
        return sum(
            1 for item in rows for kind in COMPONENT_ORDER
            if self._feedback_file_selected(item['key'], kind))

    def _feedback_entry_selected(self, entry: dict) -> bool:
        return any(self._feedback_file_selected(entry['key'], kind)
                   for kind in COMPONENT_ORDER)

    def _feedback_entry_by_key(self, key: str) -> dict | None:
        return next((entry for entry in self.feedback_items
                     if entry['key'] == key), None)

    def _feedback_check_icon(self, checked: bool, child: bool = False):
        kind = 'checked' if checked else 'unchecked'
        if child:
            icon = self._check_icon('child_' + kind)
            if icon is not None:
                return icon
        return self._check_icon(kind, leaf=True)

    def _feedback_check_cell(self, checked: bool,
                             child: bool = False) -> dict:
        icon = self._feedback_check_icon(checked, child)
        if icon is not None:
            return {'image': icon, 'text': ''}
        glyph = '☑' if checked else '☐'
        return {'text': ('   ' + glyph) if child else glyph}

    def _feedback_row_tag(self, checked: bool, child: bool = False) -> str:
        if child:
            return 'child_checked' if checked else 'child_unchecked'
        return 'checked' if checked else 'unchecked'

    def _update_feedback_row(self, iid: str, checked: bool,
                             child: bool = False) -> None:
        cell = self._feedback_check_cell(checked, child)
        tag = self._feedback_row_tag(checked, child)
        self.feedback_tree.item(iid, tags=(tag,), **cell)

    def _feedback_child_values(self, entry: dict, kind: str) -> tuple:
        selected = self._feedback_file_selected(entry['key'], kind)
        item = (entry.get('components') or {}).get(kind)
        name = feedback_component_name(entry, kind)
        if item is None:
            state = '暂时缺失'
            name = '⚠ ' + name
        else:
            state = '已选择' if selected else '未选择'
        return ('', name, state)

    def _refresh_feedback_children(self, entry: dict) -> None:
        for kind in COMPONENT_ORDER:
            iid = self._feedback_child_id(entry['key'], kind)
            if not self.feedback_tree.exists(iid):
                continue
            self._update_feedback_row(
                iid, self._feedback_file_selected(entry['key'], kind),
                child=True)
            self.feedback_tree.item(
                iid, values=self._feedback_child_values(entry, kind))

    def _feedback_chip_text(self, name: str) -> str:
        if name == '全部班级':
            return '全部 %d' % len(self.feedback_items)
        count = len(self._feedback_entries_for_class(name))
        return '%s %d' % (name, count)

    def _feedback_chip_width(self, text: str) -> int:
        return self._class_chip_font.measure(text) + 2 * self.px(12) + 2

    def _feedback_class_budget(self) -> int:
        width = self.feedback_class_bar.winfo_width()
        if width <= 1:
            width = self.root.winfo_width() - self.px(420)
        return max(self.px(260), width - self.px(2))

    def _feedback_visible_classes(self) -> tuple[list[str], int]:
        ordered = ['全部班级'] + list(self.feedback_class_order)
        if not self.feedback_class_order:
            return [], 0
        budget = self._feedback_class_budget()
        gap = self.px(8)
        more_width = (self._feedback_chip_width('更多 99 个班')
                      + self.px(22) + gap)
        used = 0
        visible: list[str] = []
        for index, name in enumerate(ordered):
            width = self._feedback_chip_width(self._feedback_chip_text(name)) + gap
            remaining = len(ordered) - index - 1
            reserve = more_width if remaining else 0
            if visible and used + width + reserve > budget:
                break
            visible.append(name)
            used += width
        if len(visible) < len(ordered):
            active = self.feedback_active_class
            if active and active not in visible:
                candidate = list(visible[:-1]) + [active]
                while len(candidate) > 1:
                    total = sum(self._feedback_chip_width(
                        self._feedback_chip_text(item)) + gap
                        for item in candidate) + more_width
                    if total <= budget:
                        break
                    candidate.pop(-2)
                visible = candidate
        hidden = len(ordered) - len(visible)
        return visible, max(0, hidden)

    def _build_feedback_class_buttons(self) -> None:
        for child in self.feedback_class_bar.winfo_children():
            child.destroy()
        self.feedback_class_buttons = {}
        self.feedback_more_button = None
        self._feedback_hidden_count = 0
        if not self.feedback_items:
            return
        visible, hidden = self._feedback_visible_classes()
        gap = self.px(8)
        for name in visible:
            button = tk.Button(
                self.feedback_class_bar, text=self._feedback_chip_text(name),
                bd=0, relief='flat', highlightthickness=0,
                padx=self.px(12), pady=self.px(6), font=(FONT, 10),
                bg=CARD, fg=TEXT_DARK, cursor='hand2',
                activebackground=SURFACE_ALT, activeforeground=TEXT_DARK,
                command=lambda n=name: self._show_feedback_class(n))
            button.pack(side=LEFT, padx=(0, gap))
            self.feedback_class_buttons[name] = button
        if hidden:
            chevron = self._icon('chevron_down_muted')
            more_text = '更多 %d 个班' % hidden
            if chevron is None:
                more_text += ' ▾'
            self.feedback_more_button = tk.Button(
                self.feedback_class_bar, text=more_text,
                image=chevron, compound='right' if chevron else 'none',
                bd=0, relief='flat', highlightthickness=0,
                padx=self.px(12), pady=self.px(6), font=(FONT, 10),
                bg=SURFACE_ALT, fg=TEXT_MUTED, cursor='hand2',
                activebackground=SURFACE, activeforeground=TEXT_DARK,
                command=self._show_feedback_class_menu)
            self.feedback_more_button.pack(side=LEFT, padx=(0, gap))
            self._feedback_hidden_count = hidden
        self._refresh_feedback_class_buttons()

    def _schedule_feedback_class_relayout(self) -> None:
        if not self.feedback_items or self._feedback_relayout_job is not None:
            return
        visible, hidden = self._feedback_visible_classes()
        if (visible == list(self.feedback_class_buttons)
                and hidden == self._feedback_hidden_count):
            return
        self._feedback_relayout_job = self.root.after(
            80, self._relayout_feedback_classes)

    def _relayout_feedback_classes(self) -> None:
        self._feedback_relayout_job = None
        if not self.feedback_items:
            return
        self._build_feedback_class_buttons()
        self._refresh_feedback_state()

    def _on_feedback_class_bar_resize(self, event) -> None:
        if abs(event.width - self._feedback_bar_width) < self.px(12):
            return
        self._feedback_bar_width = event.width
        self._schedule_feedback_class_relayout()

    def _show_feedback_class_menu(self) -> None:
        button = self.feedback_more_button
        if button is None or not self.feedback_class_order:
            return
        menu = tk.Menu(
            self.root, tearoff=0, font=(FONT, 10), bg=CARD, fg=TEXT_DARK,
            activebackground=ACCENT_SOFT, activeforeground=TEXT_DARK,
            bd=0, relief='flat', activeborderwidth=0)
        menu.add_command(
            label='%s全部班级' % ('✓ ' if self.feedback_active_class == '全部班级'
                               else ''),
            command=lambda: self._show_feedback_class('全部班级'))
        menu.add_separator()
        for name in self.feedback_class_order:
            rows = self._feedback_entries_for_class(name)
            text = '%s（%d 个课次）' % (name, len(rows))
            if name == self.feedback_active_class:
                text = '✓ ' + text
            menu.add_command(
                label=text,
                command=lambda n=name: self.root.after(
                    10, lambda n=n: self._show_feedback_class(n)))
        self._feedback_menu_ref = menu
        try:
            menu.tk_popup(button.winfo_rootx(),
                          button.winfo_rooty() + button.winfo_height()
                          + self.px(2))
        finally:
            try:
                menu.unpost()
            except Exception:  # noqa: BLE001
                pass
            menu.grab_release()

    def _refresh_feedback_class_buttons(self) -> None:
        enabled = bool(self.feedback_items) and not self.busy.get()
        for name, button in self.feedback_class_buttons.items():
            active = name == self.feedback_active_class
            button.configure(
                state='normal' if enabled else 'disabled',
                bg=ACCENT_SOFT if active else CARD,
                fg=ACCENT_DARK if active else TEXT_DARK,
                highlightbackground=ACCENT if active else BORDER_STRONG,
                highlightcolor=ACCENT if active else BORDER_STRONG,
                font=(FONT, 10, 'bold') if active else (FONT, 10))
        if self.feedback_more_button is not None:
            self.feedback_more_button.configure(
                state='normal' if enabled else 'disabled')

    def _show_feedback_class(self, name: str) -> None:
        self.feedback_active_class = name or '全部班级'
        if (self.feedback_active_class not in self.feedback_class_buttons
                and self.feedback_active_class != '全部班级'):
            self._build_feedback_class_buttons()
        self._refresh_feedback_class_buttons()
        self._show_feedback_rows()

    def _show_feedback_rows(self) -> None:
        """按班级筛选显示课次反馈；父行可展开成三个可选文件。"""

        self.feedback_tree.delete(*self.feedback_tree.get_children())
        selected = self.feedback_active_class or '全部班级'
        for entry in self._feedback_entries_for_class(selected):
            key = entry['key']
            checked = bool(self.feedback_checked.get(key))
            opts = self._feedback_check_cell(checked)
            expanded = bool(self.feedback_expanded.get(key))
            title = ('▾ ' if expanded else '▸ ') + entry['title']
            self.feedback_tree.insert(
                '', 'end', iid=key,
                values=(entry['class'], title,
                        self._feedback_day(entry)),
                tags=(self._feedback_row_tag(checked),),
                open=expanded, **opts)
            for kind in COMPONENT_ORDER:
                child = self._feedback_child_id(key, kind)
                child_checked = self._feedback_file_selected(key, kind)
                child_opts = self._feedback_check_cell(
                    child_checked, child=True)
                self.feedback_tree.insert(
                    key, 'end', iid=child,
                    values=self._feedback_child_values(entry, kind),
                    tags=(self._feedback_row_tag(child_checked, child=True),),
                    **child_opts)
        self._refresh_feedback_state()
        self._refresh_empty_states()

    def _feedback_day(self, entry: dict) -> str:
        stamp = entry.get('time') or 0
        if not stamp:
            return '-'
        try:
            return datetime.datetime.fromtimestamp(
                stamp / 1000).strftime('%Y-%m-%d %H:%M')
        except (TypeError, ValueError, OSError, OverflowError):
            return '-'

    def _refresh_feedback_state(self) -> None:
        selected_entries = [entry for entry in self.feedback_items
                            if self._feedback_entry_selected(entry)]
        files = sum(self._feedback_selected_file_count(entry)
                    for entry in selected_entries)
        total = len(self.feedback_items)
        self.feedback_badge_count.configure(
            text='已选 %d 个文件 · %d 个课次' % (files, len(selected_entries)),
            bg=CARD, fg=ACCENT_DARK if files else TEXT_MUTED,
            font=(FONT, 10, 'bold') if files else (FONT, 10))
        can_select = (self._has_board_auth() and not self.busy.get()
                      and bool(total))
        for button in (self.feedback_select_button,
                       self.feedback_clear_button):
            button.configure(state='normal' if can_select else 'disabled')
        self.feedback_export_button.configure(
            state='normal' if (can_select and files) else 'disabled')
        self.feedback_list_button.configure(
            state='normal' if (self._has_board_auth()
                               and not self.busy.get()) else 'disabled')
        self._refresh_feedback_class_buttons()
        selected = self.feedback_active_class or '全部班级'
        if selected == '全部班级':
            self.feedback_class_hint.configure(
                text='全部班级 · 按时间降序')
        else:
            self.feedback_class_hint.configure(
                text='%s · %d 个课次' % (
                    selected, len(self._feedback_entries_for_class(selected))))

    def _on_feedback_click(self, event) -> None:
        region = self.feedback_tree.identify_region(event.x, event.y)
        if region not in ('cell', 'tree'):
            return
        iid = self.feedback_tree.identify_row(event.y)
        if not iid or self.busy.get():
            return
        if iid not in self.feedback_checked:
            self._toggle_feedback_file(iid)
            return 'break'
        element = self.feedback_tree.identify_element(event.x, event.y)
        if region == 'tree' and 'indicator' in str(element):
            # 点 Treeview 自带的小三角：交给控件展开/收起，稍后记住状态。
            self.root.after(30, lambda i=iid: self._remember_feedback_open(i))
            return
        column = self.feedback_tree.identify_column(event.x)
        if region == 'cell' and column != '#0':
            self._toggle_feedback_expand(iid)
            return 'break'
        self._toggle_feedback(iid)
        return 'break'

    def _remember_feedback_open(self, iid: str) -> None:
        if iid in self.feedback_checked:
            opened = bool(self.feedback_tree.item(iid, 'open'))
            self.feedback_expanded[iid] = opened
            self._update_feedback_parent_title(iid, opened)

    def _update_feedback_parent_title(self, iid: str, opened: bool) -> None:
        entry = self._feedback_entry_by_key(iid)
        if entry is None:
            return
        self.feedback_tree.item(
            iid, values=(entry['class'],
                         ('▾ ' if opened else '▸ ') + entry['title'],
                         self._feedback_day(entry)))

    def _toggle_feedback_expand(self, iid: str) -> None:
        if iid not in self.feedback_checked:
            return
        opened = not bool(self.feedback_tree.item(iid, 'open'))
        self.feedback_expanded[iid] = opened
        self.feedback_tree.item(iid, open=opened)
        self._update_feedback_parent_title(iid, opened)

    def _toggle_feedback(self, iid: str) -> None:
        """父行：整节课反馈的勾选；勾选/取消时三项文件一起切换。"""

        target = not self.feedback_checked.get(iid, False)
        self.feedback_checked[iid] = target
        entry = self._feedback_entry_by_key(iid)
        if entry is not None:
            for kind in COMPONENT_ORDER:
                self.feedback_file_checked[
                    self._feedback_child_id(iid, kind)] = target
            self._refresh_feedback_children(entry)
        self._update_feedback_row(iid, target)
        self._refresh_feedback_state()

    def _toggle_feedback_file(self, iid: str) -> None:
        """子行：只切换这一个文件，其他文件不受影响。"""

        key, _, kind = iid.rpartition('|')
        if key not in self.feedback_checked or not kind:
            return
        current = self._feedback_file_selected(key, kind)
        self.feedback_file_checked[self._feedback_child_id(key, kind)] = (
            not current)
        entry = self._feedback_entry_by_key(key)
        if entry is not None:
            self._refresh_feedback_children(entry)
            selected = self._feedback_entry_selected(entry)
            self.feedback_checked[key] = selected
            self._update_feedback_row(key, selected)
        self._refresh_feedback_state()

    def on_select_all_feedbacks(self) -> None:
        """全选 / 取消当前班级里的所有文件。"""

        rows = self._feedback_entries_for_class(
            self.feedback_active_class or '全部班级')
        all_selected = bool(rows) and all(
            self._feedback_file_selected(entry['key'], kind)
            for entry in rows for kind in COMPONENT_ORDER)
        target = not all_selected
        for entry in rows:
            key = entry['key']
            self.feedback_checked[key] = target
            for kind in COMPONENT_ORDER:
                self.feedback_file_checked[
                    self._feedback_child_id(key, kind)] = target
        self._show_feedback_rows()

    def on_clear_feedbacks(self) -> None:
        for key in list(self.feedback_checked):
            self.feedback_checked[key] = False
            for kind in COMPONENT_ORDER:
                self.feedback_file_checked[
                    self._feedback_child_id(key, kind)] = False
        self._show_feedback_rows()

    def _checked_feedbacks(self) -> list[dict]:
        return [entry for entry in self.feedback_items
                if self._feedback_entry_selected(entry)]

    def on_export_feedbacks(self) -> None:
        if self.busy.get():
            return
        if not self._has_board_auth():
            messagebox.showwarning(APP_NAME, '还没有可用的登录态。')
            return
        picked = self._checked_feedbacks()
        if not picked:
            messagebox.showwarning(APP_NAME, '请先勾选要导出的课次反馈。')
            return
        prepared: list[dict] = []
        for entry in picked:
            selected = [kind for kind in COMPONENT_ORDER
                        if self._feedback_file_selected(entry['key'], kind)]
            bundle = dict(entry)
            bundle['selected'] = selected
            prepared.append(bundle)
        preview = sum(1 for bundle in prepared
                      if PREVIEW in bundle['selected']
                      and (bundle.get('components') or {}).get(PREVIEW))
        consolidation = sum(
            1 for bundle in prepared
            if CONSOLIDATION in bundle['selected']
            and (bundle.get('components') or {}).get(CONSOLIDATION))
        history = sum(1 for bundle in prepared
                      if 'history' in bundle['selected']
                      and (bundle.get('components') or {}).get('history'))
        missing = sum(
            1 for bundle in prepared
            for kind in (bundle.get('missing') or [])
            if kind in bundle['selected'])
        unselected = sum(
            len(set(COMPONENT_ORDER) - set(bundle['selected']))
            for bundle in prepared)
        initial = feedback_out_root()
        initial_dir = initial if initial.exists() else Path.home() / 'Desktop'
        chosen = filedialog.askdirectory(
            title='选择反馈文件保存位置', initialdir=str(initial_dir),
            mustexist=False)
        if not chosen:
            self.status.set('已取消：未选择保存位置。')
            return
        out_root = Path(chosen)
        self.cancel_event = threading.Event()
        self._busy_kind = 'feedback'
        self.set_busy(True)
        self.clear_log('feedback')
        self.feedback_progress.begin(
            '正在导出反馈文件', '正在读取课前预习、课后巩固与课堂权限…',
            indeterminate=True,
            hint='按班级分文件夹 · 每个课次生成「第X节课反馈」')
        self.status.set(
            '正在导出 %d 个课次反馈（预习 %d / 巩固 %d / 权限 %d，'
            '缺失 %d，未选 %d）…'
            % (len(prepared), preview, consolidation, history, missing,
               unselected))
        self.append_log(
            '导出 %d 个课次反馈（预习 %d / 巩固 %d / 权限 %d，缺失 %d，'
            '未选 %d），保存到：%s'
            % (len(prepared), preview, consolidation, history, missing,
               unselected, out_root), target='feedback')
        self.worker = threading.Thread(
            target=self._feedback_export_work,
            args=(prepared, out_root), daemon=True)
        self.worker.start()

    def _feedback_export_work(self, bundles: list[dict],
                              out_root: Path) -> None:
        for attempt in range(2):
            try:
                session = self._board_session()
                result = export_feedback_bundles(
                    session, bundles, out_root,
                    on_event=lambda payload: self._post(
                        'feedback_event', payload),
                    cancel_event=self.cancel_event,
                    logger=self._thread_feedback_log)
            except TokenExpired:
                if attempt == 0 and self._renew_board_token():
                    self._thread_feedback_log('登录态已过期，已自动重新获取，继续…')
                    continue
                self._post(
                    'error',
                    '登录态已失效：请确认电脑上的爱问云仍登录后'
                    '点「重新检测」，或改用账号密码登录。')
                return
            except FeedbackCancelled as error:
                self._post('canceled', str(error))
                return
            except Exception as error:  # noqa: BLE001
                self._post('error',
                           '%s: %s' % (type(error).__name__, error))
                return
            self._post('feedback_done', result)
            return

    def _on_feedback_event(self, payload) -> None:
        if not isinstance(payload, dict):
            return
        phase = str(payload.get('phase') or '')
        done = int(payload.get('done', 0) or 0)
        total = max(1, int(payload.get('total', 1) or 1))
        name = str(payload.get('name') or '')
        group = str(payload.get('group') or '')
        kind = str(payload.get('kind') or '')
        label = '课次反馈' if kind == 'feedback' else (
            '课堂权限' if kind == 'history' else '作业成绩')
        percent = float(payload.get(
            'percent', done / total * 100.0) or 0)
        if phase == 'start':
            detail = '正在生成%s · %s / %s' % (label, group, name)
        elif phase == 'done':
            detail = '已完成 %d / %d 个课次' % (done, total)
        elif phase == 'failed':
            detail = '生成失败，继续下一个 · %s' % name
        else:
            detail = name
        self.feedback_progress.update(
            percent, detail=detail, elapsed=payload.get('elapsed'))
        self.status.set('正在导出反馈：已完成 %d / %d 个课次' % (done, total))

    def _finish_feedbacks(self, payload: dict) -> None:
        self.set_busy(False)
        files = payload.get('files') or []
        failed = payload.get('failed') or []
        complete = sum(1 for item in files if not item.get('missing'))
        out_root = Path(payload['out_root']) if payload.get('out_root') else None
        self._show_export_result(
            'feedback', '已保存 %d 个课次 · 其中缺失 %d 个 · 失败 %d 个' % (
                len(files), len(files) - complete, len(failed)), out_root)
        detail = '%d 个课次反馈（三项齐全 %d · 有缺失 %d）%s' % (
            len(files), complete, len(files) - complete,
            ' · %d 个读取失败' % len(failed) if failed else '')
        self.feedback_progress.complete(
            '反馈文件导出完成', detail,
            hint='可在反馈页查看结果和输出目录')
        self.status.set('反馈文件导出完成：%d 个课次反馈%s' % (
            len(files), '，%d 个失败' % len(failed) if failed else ''))
        self.append_log('', target='feedback')
        self.append_log('===== 反馈文件导出汇总（用时 %ss）====='
                        % payload.get('elapsed', '-'), target='feedback')
        for content in files:
            missing = content.get('missing') or []
            unselected = content.get('unselected') or []
            state = '三项齐全' if not missing else '暂时缺失：%s' % '、'.join(
                str(item) for item in missing)
            if unselected:
                state += ' · 未选择：%s' % '、'.join(
                    str(item) for item in unselected)
            self.append_log(
                '    [%s] %s（%s，共 %d 行）' % (
                    content.get('group') or '-',
                    Path(content.get('path', '')).name, state,
                    content.get('rows', 0)),
                target='feedback')
        if failed:
            self.append_log('读取失败 %d 项：%s' % (
                len(failed), '、'.join(str(x.get('title')) for x in failed)),
                target='feedback')
        if out_root is not None and out_root.is_dir():
            self.append_log('保存位置：%s' % out_root, target='feedback')
            try:
                open_path(out_root)
            except OSError:
                pass
        self._refresh_feedback_state()

    # ---------- 一键更新 ----------
    def on_check_update(self) -> None:
        """设置里的「一键更新」入口。"""

        if self._update_busy:
            return
        if self.busy.get():
            messagebox.showwarning(APP_NAME, '正在导出，请完成后再更新。')
            return
        self._update_busy = True
        self._set_update_controls('检查中…', 'disabled')
        self.settings_update_status.set('正在检查更新…')
        self.status.set('正在检查更新…')
        self.worker = threading.Thread(
            target=self._update_check_work, daemon=True)
        self.worker.start()

    def _update_check_work(self) -> None:
        try:
            result = check_for_update(APP_VERSION)
        except Exception as error:  # noqa: BLE001
            result = {
                'status': 'error',
                'current': APP_VERSION,
                'message': str(error),
            }
        self._post('update_checked', result)

    def _finish_update_check(self, payload) -> None:
        self._update_busy = False
        self._set_update_controls(UPDATE_BUTTON_TEXT, 'normal')
        if not isinstance(payload, dict):
            payload = {'status': 'error', 'message': '更新检查返回格式不正确。'}
        status = payload.get('status')
        if status == 'update':
            latest = payload.get('latest')
            self.settings_update_status.set('发现新版本 v%s。' % latest)
            self.status.set('发现新版本 v%s（当前 v%s）。'
                            % (latest, payload.get('current')))
            if can_auto_update(payload):
                self._prompt_install_update(payload)
                return
            self.settings_update_status.set(
                '发现新版本 v%s；当前环境请手动下载。' % latest)
        elif status == 'latest':
            self.settings_update_status.set(
                '当前已是最新版本 v%s。' % payload.get('current'))
            self.status.set('当前已是最新版本 v%s。' % payload.get('current'))
        else:
            self.settings_update_status.set(
                '检查失败：%s' % (payload.get('message') or '无法连接更新服务器'))
            self.status.set('检查更新失败。')
        self._show_update_result(payload)

    def _prompt_install_update(self, result: dict) -> None:
        latest = str(result.get('latest') or '')
        current = str(result.get('current') or APP_VERSION)
        notes = str(result.get('notes') or '').strip()
        message = (
            '发现新版本 v%s（当前 v%s）。\n\n'
            '将自动下载、校验并替换当前程序，随后自动重启。\n'
            '更新期间程序会退出，请先完成正在进行的导出。'
            % (latest, current))
        if notes:
            message += '\n\n更新说明：\n' + notes[:800]
        if not messagebox.askyesno(APP_NAME, message):
            self.settings_update_status.set('已取消更新，当前 v%s。' % current)
            self.status.set('已取消更新。')
            return
        if self.busy.get():
            messagebox.showwarning(APP_NAME, '正在导出，请完成后再更新。')
            self.settings_update_status.set('当前有导出任务，更新已取消。')
            return
        self._start_update_install(result)

    def _start_update_install(self, result: dict) -> None:
        self._update_busy = True
        self._set_update_controls('更新中…', 'disabled')
        latest = str(result.get('latest') or '')
        self.settings_update_status.set('正在下载 v%s…' % latest)
        self.status.set('正在下载更新 v%s…' % latest)
        self.worker = threading.Thread(
            target=self._update_install_work, args=(result,), daemon=True)
        self.worker.start()

    def _update_install_work(self, result: dict) -> None:
        package = None
        last_percent = [-1]

        def progress(done: int, total: int) -> None:
            percent = int(done * 100 / total) if total > 0 else -1
            if percent == last_percent[0]:
                return
            last_percent[0] = percent
            self._post('update_progress', {
                'version': result.get('latest'),
                'done': done,
                'total': total,
                'percent': percent,
            })

        try:
            package = prepare_update(result, progress=progress)
            launch_updater(
                package, expected_version=str(result.get('latest') or ''))
        except Exception as error:  # noqa: BLE001
            if package is not None:
                try:
                    package.unlink()
                    package.parent.rmdir()
                except OSError:
                    pass
            self._post('update_failed', {'message': str(error)})
            return
        self._post('update_ready', {'version': result.get('latest')})

    def _on_update_progress(self, payload) -> None:
        if not isinstance(payload, dict):
            return
        version = str(payload.get('version') or '')
        percent = payload.get('percent')
        if isinstance(percent, int) and percent >= 0:
            text = '正在下载 v%s… %d%%' % (version, percent)
        else:
            text = '正在下载 v%s…' % version
        self.settings_update_status.set(text)
        self.status.set(text)

    def _on_update_ready(self, payload) -> None:
        version = ''
        if isinstance(payload, dict):
            version = str(payload.get('version') or '')
        self.settings_update_status.set('下载完成，正在安装 v%s…' % version)
        self.status.set('正在安装更新，程序即将重启…')
        self._set_update_controls('正在重启…', 'disabled')
        self.root.after(900, self._exit_for_update)

    def _on_update_failed(self, payload) -> None:
        self._update_busy = False
        self._set_update_controls(UPDATE_BUTTON_TEXT, 'normal')
        message = '更新失败。'
        if isinstance(payload, dict):
            message = str(payload.get('message') or message)
        self.settings_update_status.set('更新失败：%s' % message)
        self.status.set('更新失败。')
        messagebox.showerror(APP_NAME, '自动更新失败：\n%s' % message)

    def _exit_for_update(self) -> None:
        self._update_installing = True
        try:
            self.root.destroy()
        except Exception:  # noqa: BLE001
            pass

    def _show_update_result(self, result: dict) -> None:
        status = str(result.get('status') or 'error')
        current = str(result.get('current') or APP_VERSION)
        latest = str(result.get('latest') or '')
        download = str(result.get('download_url') or '')
        server = str(result.get('server_url') or '')
        notes = str(result.get('notes') or '').strip()

        win = tk.Toplevel(self.root)
        win.title('检查更新')
        win.configure(bg=BG)
        win.transient(self.root)
        win.resizable(False, False)
        try:
            win.iconphoto(True, *getattr(self, '_window_icons', []))
            if sys.platform == 'win32' and getattr(
                    self, '_theme_icon_path', ''):
                win.iconbitmap(self._theme_icon_path)
        except Exception:  # noqa: BLE001
            pass
        try:
            win.grab_set()
        except Exception:  # noqa: BLE001
            pass
        body = ttk.Frame(win, padding=(self.px(20), self.px(16)))
        body.pack(fill=BOTH, expand=True)

        if status == 'update':
            ttk.Label(body, text='发现新版本', style='SectionSmall.TLabel').pack(
                anchor='w')
            ttk.Label(body, text='最新版本：v%s' % latest,
                      style='Muted.TLabel').pack(anchor='w', pady=(self.px(8), self.px(2)))
            ttk.Label(body, text='当前版本：v%s' % current,
                      style='Faint.TLabel').pack(anchor='w')
            if notes:
                text = tk.Text(body, width=48, height=min(8, max(2,
                                  notes.count('\n') + 1)),
                               wrap='word', bg=CARD, fg=TEXT_DARK,
                               relief='solid', bd=1, padx=self.px(8),
                               pady=self.px(8), font=(FONT, 10))
                text.insert('1.0', notes)
                text.configure(state='disabled')
                text.pack(fill=X, pady=(self.px(12), 0))
        elif status == 'latest':
            ttk.Label(body, text='已是最新版本',
                      style='SectionSmall.TLabel').pack(anchor='w')
            ttk.Label(body, text='当前版本：v%s' % current,
                      style='Muted.TLabel').pack(anchor='w', pady=(self.px(8), 0))
            ttk.Label(body, text='暂时没有需要更新的内容',
                      style='Faint.TLabel').pack(anchor='w', pady=(self.px(4), 0))
        else:
            ttk.Label(body, text='检查更新失败',
                      style='SectionSmall.TLabel').pack(anchor='w')
            detail = str(result.get('message') or '无法连接更新服务器')
            ttk.Label(body, text=detail, style='Muted.TLabel',
                      wraplength=self.px(440), justify=LEFT).pack(
                          anchor='w', pady=(self.px(8), 0))

        actions = ttk.Frame(body)
        actions.pack(fill=X, pady=(self.px(16), 0))

        ttk.Button(actions, text='关闭', style='Secondary.TButton',
                   command=win.destroy).pack(side=RIGHT)
        target_url = server or download
        if status == 'update' and target_url:
            def open_download() -> None:
                try:
                    webbrowser.open(target_url)
                    self.status.set('已打开更新下载页')
                except Exception as error:  # noqa: BLE001
                    messagebox.showwarning(APP_NAME, '打开下载页失败：%s' % error)
            ttk.Button(actions, text='打开下载页', style='Primary.TButton',
                       command=open_download).pack(
                           side=RIGHT, padx=(0, self.px(8)))

        win.update_idletasks()
        x = self.root.winfo_rootx() + max(
            0, (self.root.winfo_width() - win.winfo_reqwidth()) // 2)
        y = self.root.winfo_rooty() + max(
            0, (self.root.winfo_height() - win.winfo_reqheight()) // 3)
        win.geometry('+%d+%d' % (x, y))
        try:
            win.lift()
            win.focus_force()
        except Exception:  # noqa: BLE001
            pass

    def on_close(self) -> None:
        if self._update_installing:
            self.root.destroy()
            return
        if self._update_busy:
            if not messagebox.askyesno(APP_NAME, '正在更新，确定要退出吗？'):
                return
        if self.busy.get():
            if not messagebox.askyesno(APP_NAME, '正在导出，确定要退出吗？'):
                return
            self.cancel_event.set()
        self.root.destroy()

    # ---------- 工具 ----------
    def set_busy(self, busy: bool) -> None:
        self.busy.set(busy)
        # 邀请码按钮的文字只跟着「邀请码导出」自己走；其他页在跑时不能显示
        # 「正在导出…」，否则用户会以为邀请码功能也在导出。
        self.export_button.configure(
            state='disabled' if busy else 'normal',
            text='正在导出…' if self.invite_running else '导出邀请码 Excel')
        show_note = busy and self._busy_kind in ('board', 'login', 'feedback')
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
                  'feedback': self.feedback_log}
        if target not in panels:
            if self._busy_kind == 'invite':
                target = 'invite'
            elif self._busy_kind in ('board', 'login'):
                target = 'board'
            elif self._busy_kind == 'feedback':
                target = 'feedback'
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
        messagebox.showinfo(
            APP_NAME,
            '顶部用「邀请码导出 / 板书导出 / 反馈文件导出」三个标签页分开使用。\n\n'
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
            '3. 选时间段（今天 / 最近 7 天 / 30 天 / 一个季度 / 一年 / 三年 / '
            '全部 / 自定义），切换后会自动列出该时段的课堂清单。班级按'
            '「学期 + 班级」显示，例如「26秋7MA+31」「25暑7MAA12」：'
            '官方班级里的 202602 编码会还原成「26秋」，β1、β2、β3、α1 等'
            '分组会合并成同一个班；匹配不到班级的条目统一放在「杂项/未分类」；'
            '列表按时间倒序，最新在上。\n\n'
            '4. 点整行勾选要导出的课；「全选」勾选当前班级。班级按钮上带学期，'
            '窗口放不下的班收进右侧「更多」里，点「更多」按学期分组选择；'
            '勾选别的班不会丢掉已经勾好的课。\n\n'
            '5. 点「导出所选板书」时只导出勾选项，未勾选会被拦截，不会回退为'
            '全量导出；随后弹出保存位置。PDF 按「学期+班级」分文件夹'
            '（如「26秋7MA+31」），文件名与课堂名一致。'
            '排序可选「按上课顺序」或「按板书类型」。\n\n'
            '6. 导出时右下角弹出进度卡片：总百分比、当前第几节、当前课堂的'
            '第几页、已用时间和预计剩余时间，旁边有「取消」。完成后显示'
            '「100%」并自动收起；失败或取消也会停留几秒说明原因后自动收起。'
            '多节课会并行导出，不会中断。\n\n'
            '7. 三个功能的运行日志彼此独立：邀请码日志在邀请码页底部；'
            '板书日志在班级列表下方，默认收起，右侧显示未读条数，点「展开」'
            '查看；导出结束或出错时会自动展开。反馈日志在反馈页底部。\n\n'
            '【反馈文件导出】\n\n'
            '连上登录态后自动列出所选时间段的课堂和作业，按班级分组并按时间'
            '排序。勾选后点「导出所选反馈」：课堂生成与爱问云「历史课堂权限」'
            '一致的学生统计 CSV，作业生成原有成绩明细 CSV；两者按班级分文件夹。'
            '课堂 CSV 包含出勤、学习/直播/互动时长、签到、随堂测、积分、举手、'
            '观看历史课堂、进入/离开时间等字段。\n\n'
            '安全说明：token / 密码只在本进程内存中使用，不写日志、不落盘；'
            '只向 HTTPS 爱问云业务主机发请求。\n\n')

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if '--version-file' in sys.argv:
        # 更新器用它探测新包的版本，避免把错误版本替换进来。
        try:
            index = sys.argv.index('--version-file')
            target = Path(sys.argv[index + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(APP_VERSION, encoding='utf-8')
        except Exception:  # noqa: BLE001
            return 2
        return 0

    try:
        cleanup_stale_update_files()
    except Exception:  # noqa: BLE001
        pass

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
        # CI 用：验证打包后的 .app / exe 能加载 Tkinter 和业务模块。
        import tkinter
        import analyze  # noqa: F401
        import banshu_export  # noqa: F401
        import feedback_export  # noqa: F401
        import login  # noqa: F401
        import pipeline  # noqa: F401
        from net import _ssl_context
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
