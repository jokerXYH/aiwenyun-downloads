import tkinter as tk
from tkinter import BOTH, LEFT, RIGHT, X, Y, ttk

from banshu_export import RANGE_PRESETS


def build_board_view(app, content, *, font, progress_panel, log_panel):
    palette = app.palette
    board_outer, board = app._card(content, padding=app.px(16))
    board_outer.grid(row=0, column=0, sticky='nsew')
    board_outer.grid_remove()
    app.board_view = board_outer

    board_head = ttk.Frame(board, style='Card.TFrame')
    board_head.grid(row=0, column=0, sticky='ew')
    ttk.Label(board_head, text='板书导出',
              style='Section.TLabel').pack(side=LEFT)
    ttk.Label(board_head,
              text='按学期+班级分组 · 所选 PDF 直接保存到指定位置',
              style='Muted.TLabel').pack(side=LEFT, anchor='s',
                                         padx=(app.px(12), 0),
                                         pady=(0, app.px(4)))

    # 登录态和登录操作挪到页头右侧：以前它们和查询条件挤在工具栏同
    # 一行里，状态字被按钮、说明文字切成碎片，字号又小，看不清。
    app.login_row = ttk.Frame(board_head, style='Card.TFrame')
    app.login_row.pack(side=RIGHT, anchor='n', pady=(app.px(4), 0))
    app.login_badge = tk.Label(
        app.login_row, text='正在检测本机登录…', bg=palette['CARD'], fg=palette['TEXT_MUTED'],
        font=(font, 10))
    app.login_badge.pack(side=LEFT, padx=(0, app.px(12)))
    app.rescan_button = ttk.Button(
        app.login_row, text='重新检测', style='Secondary.TButton',
        command=lambda: app._start_client_scan(manual=True))
    app.rescan_button.pack(side=LEFT)
    app.use_account_link = tk.Label(
        app.login_row, text='改用账号密码登录', bg=palette['CARD'], fg=palette['LINK'],
        activeforeground=palette['ACCENT_DARK'], font=(font, 10),
        cursor='hand2')
    app.use_account_link.bind(
        '<Button-1>', lambda _e: app._enable_account_login())

    toolbar = tk.Frame(board, bg=palette['SURFACE'], bd=0,
                       highlightbackground=palette['BORDER'], highlightthickness=1)
    toolbar.grid(row=1, column=0, sticky='ew', pady=(app.px(16), 0))
    toolbar_body = ttk.Frame(toolbar, style='Surface.TFrame',
                             padding=(app.px(16), app.px(12)))
    toolbar_body.pack(fill=X)

    # 查询条件只占一行：时间段 / 排序 ｜ 自定义日期，组与组之间用竖线
    # 断开，免得「排序」和「自定义」连成一串读不出边界。
    range_row = ttk.Frame(toolbar_body, style='Surface.TFrame')
    range_row.pack(fill=X)
    ttk.Label(range_row, text='时间段',
              style='SurfaceMuted.TLabel').pack(side=LEFT)
    app.range_box = ttk.Combobox(range_row, textvariable=app.board_range,
                                  values=list(RANGE_PRESETS),
                                  state='readonly', width=12)
    app.range_box.pack(side=LEFT, padx=(app.px(8), app.px(16)))
    app.range_box.bind('<<ComboboxSelected>>',
                        lambda _e: app._auto_list_boards())
    ttk.Label(range_row, text='排序',
              style='SurfaceMuted.TLabel').pack(side=LEFT)
    app.sort_box = ttk.Combobox(range_row, textvariable=app.board_sort,
                                 values=['按上课顺序', '按板书类型'],
                                 state='readonly', width=11)
    app.sort_box.pack(side=LEFT, padx=(app.px(8), app.px(16)))
    tk.Frame(range_row, bg=palette['BORDER_STRONG'], width=1).pack(
        side=LEFT, fill=Y, pady=app.px(4), padx=(0, app.px(16)))
    ttk.Label(range_row, text='自定义日期',
              style='SurfaceMuted.TLabel').pack(side=LEFT)
    app.from_entry = ttk.Entry(range_row, textvariable=app.board_from,
                                width=11)
    app.from_entry.pack(side=LEFT, padx=(app.px(8), app.px(8)))
    ttk.Label(range_row, text='至',
              style='SurfaceMuted.TLabel').pack(side=LEFT)
    app.to_entry = ttk.Entry(range_row, textvariable=app.board_to,
                              width=11)
    app.to_entry.pack(side=LEFT, padx=(app.px(8), 0))
    app.from_entry.bind('<Return>', lambda _e: app._auto_list_boards())
    app.to_entry.bind('<Return>', lambda _e: app._auto_list_boards())
    app.board_list_button = ttk.Button(range_row, text='列出板书',
                                        style='Secondary.TButton',
                                        command=app.on_list_boards,
                                        state='disabled')
    app.board_list_button.pack(side=RIGHT, padx=(app.px(16), 0))

    # 状态说明单独占一行：它时不时会变长（例如提示可能挤下线客户端），
    # 塞进查询行里就会把按钮顶出可视区域，只剩半截字。
    app.hint_label = ttk.Label(
        toolbar_body, textvariable=app.client_hint,
        style='SurfaceHint.TLabel', justify=LEFT,
        wraplength=app.px(520))
    app.hint_label.pack(fill=X, pady=(app.px(8), 0))

    def _fit_hint(event) -> None:
        width = max(app.px(240), int(event.width) - app.px(36))
        current = int(app.hint_label.cget('wraplength') or 0)
        if abs(width - current) > app.px(8):
            app.hint_label.configure(wraplength=width)

    toolbar_body.bind('<Configure>', _fit_hint)

    # 账号密码输入行：只有真的要账号密码时才出现，免密码模式不占地方
    app.account_row = ttk.Frame(toolbar_body, style='Surface.TFrame')
    ttk.Label(app.account_row, text='账号',
              style='SurfaceMuted.TLabel').pack(side=LEFT)
    app.name_entry = ttk.Entry(app.account_row,
                                textvariable=app.login_name, width=15)
    app.name_entry.pack(side=LEFT, padx=(app.px(8), app.px(16)))
    ttk.Label(app.account_row, text='密码',
              style='SurfaceMuted.TLabel').pack(side=LEFT)
    app.pass_entry = ttk.Entry(app.account_row,
                                textvariable=app.login_pass,
                                width=15, show='*')
    app.pass_entry.pack(side=LEFT, padx=(app.px(8), app.px(16)))
    app.login_button = ttk.Button(app.account_row, text='登录',
                                   style='Secondary.TButton',
                                   command=app.on_login)
    app.login_button.pack(side=LEFT)
    app.name_entry.bind('<Return>',
                         lambda _e: app.pass_entry.focus_set())
    app.pass_entry.bind('<Return>', lambda _e: app.on_login())

    # 进度改成独立弹窗，卡片里不再留进度行
    app.board_progress = progress_panel(board, app.on_cancel,
                                        compact=False)

    list_head = ttk.Frame(board, style='Card.TFrame')
    list_head.grid(row=3, column=0, sticky='ew',
                   pady=(app.px(12), app.px(8)))
    ttk.Label(list_head, text='课堂',
              style='SectionSmall.TLabel').pack(side=LEFT)
    ttk.Label(list_head, textvariable=app.board_meta,
              style='Faint.TLabel').pack(side=LEFT, padx=(app.px(12), 0))
    app.clear_button = ttk.Button(list_head, text='清空',
                                   style='Quiet.TButton',
                                   command=app.on_clear_boards,
                                   state='disabled')
    app.clear_button.pack(side=RIGHT, padx=(app.px(8), 0))
    app.select_all_button = ttk.Button(list_head, text='全选',
                                        style='Quiet.TButton',
                                        command=app.on_select_all_boards,
                                        state='disabled')
    app.select_all_button.pack(side=RIGHT, padx=(app.px(8), 0))

    # 班级做成横排按钮：按钮上带学期（26秋7MAA12），窗口放不下时
    # 多余的收进「更多」菜单，不再固定只显示 8 个。
    app.board_class_bar = ttk.Frame(board, style='Card.TFrame')
    app.board_class_bar.grid(row=4, column=0, sticky='ew',
                              pady=(0, app.px(8)))
    app.board_class_bar.bind('<Configure>',
                              app._on_board_class_bar_resize)
    app.board_class_buttons: dict[str, tk.Button] = {}
    app.board_more_button: tk.Button | None = None
    app._board_hidden_count = 0

    app.tree_wrap = tk.Frame(board, bg=palette['CARD'], bd=0,
                              highlightbackground=palette['BORDER'],
                              highlightthickness=1)
    # 列表用 grid 抢剩余高度，日志展开时缩的是日志还是列表由权重决定
    board.columnconfigure(0, weight=1)
    board.rowconfigure(5, weight=1)
    app.tree_wrap.grid(row=5, column=0, sticky='nsew')
    app.board_tree = ttk.Treeview(
        app.tree_wrap, columns=('title', 'date'),
        show='tree headings', height=8, selectmode='none',
        style='Board.Treeview')
    app.board_tree.heading('#0', text='选择', anchor='center')
    app.board_tree.heading('title', text='课堂', anchor='w')
    app.board_tree.heading('date', text='日期', anchor='center')
    app.board_tree.column('#0', width=app.px(72),
                           minwidth=app.px(72), anchor='center',
                           stretch=False)
    app.board_tree.column('title', width=app.px(780),
                           minwidth=app.px(380), anchor='w')
    app.board_tree.column('date', width=app.px(160),
                           minwidth=app.px(132), anchor='center',
                           stretch=False)
    app.board_tree.tag_configure('unchecked', background=palette['CARD'],
                                  foreground=palette['TEXT_DARK'])
    app.board_tree.tag_configure('checked', background=palette['CHECKED_BG'],
                                  foreground=palette['TEXT_DARK'])
    board_scroll = ttk.Scrollbar(app.tree_wrap, orient='vertical',
                                 command=app.board_tree.yview,
                                 style='Vertical.TScrollbar')
    app.board_tree.configure(yscrollcommand=board_scroll.set)
    app.board_tree.pack(side=LEFT, fill=BOTH, expand=True)
    board_scroll.pack(side=RIGHT, fill=Y)
    app.board_tree.bind('<Button-1>', app._on_board_click)

    app.board_empty = tk.Frame(app.tree_wrap, bg=palette['CARD'])
    app.board_empty_image = tk.Label(app.board_empty, bg=palette['CARD'], bd=0)
    app.board_empty_text = tk.Label(
        app.board_empty,
        text='还没有课堂 · 选好时间段后点「列出板书」',
        bg=palette['CARD'], fg=palette['TEXT_FAINT'], font=(font, 10),
        wraplength=app.px(360), justify='center')
    app.board_empty_text.pack(pady=(app.px(8), 0))
    app.board_empty.place(relx=0.5, rely=0.55, anchor='center')
    app.board_empty.lift()

    board_row = ttk.Frame(board, style='Card.TFrame')
    board_row.grid(row=6, column=0, sticky='ew', pady=(app.px(12), 0))
    app.selection_badge = tk.Label(
        board_row, text='已选 0 节', bg=palette['CARD'], fg=palette['TEXT_MUTED'],
        font=(font, 10))
    app.selection_badge.pack(side=LEFT)
    ttk.Label(board_row, text='点整行勾选 · 换班级不影响已勾选',
              style='Faint.TLabel').pack(side=LEFT, padx=(app.px(12), 0))
    app.board_all_button = ttk.Button(
        board_row, text='导出所选板书', style='Primary.TButton',
        command=app.on_export_boards, state='disabled')
    app.board_all_button.pack(side=RIGHT)

    app.board_log = log_panel(
        board, title='板书运行日志',
        subtitle='登录、清单与导出诊断', height=5,
        collapsible=True, expanded=False, px=app.px,
        on_toggle=app.on_toggle_board_log)
    app.board_log.frame.grid(row=7, column=0, sticky='ew',
                              pady=(app.px(12), 0))
    # 窗口被拖矮时把日志收起来，给列表腾高度
    board.bind('<Configure>', app._fit_board_layout)
