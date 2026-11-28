import tkinter as tk
from tkinter import BOTH, LEFT, RIGHT, X, Y, ttk

from banshu_export import RANGE_PRESETS


def build_feedback_view(app, content, *, font, progress_panel, log_panel):
    palette = app.palette
    feedback_outer, feedback = app._card(content, padding=app.px(16))
    feedback_outer.grid(row=0, column=0, sticky='nsew')
    feedback_outer.grid_remove()
    app.feedback_view = feedback_outer
    feedback.columnconfigure(0, weight=1)

    feedback_head = ttk.Frame(feedback, style='Card.TFrame')
    feedback_head.grid(row=0, column=0, sticky='ew')
    ttk.Label(feedback_head, text='反馈文件导出',
              style='Section.TLabel').pack(side=LEFT)
    ttk.Label(feedback_head,
              text='展开课次选文件 · 按班级和课次归档',
              style='Muted.TLabel').pack(side=LEFT, anchor='s',
                                         padx=(app.px(12), 0),
                                         pady=(0, app.px(4)))
    app.feedback_badge = tk.Label(
        feedback_head, text='○ 等待登录态', bg=palette['CARD'], fg=palette['TEXT_MUTED'],
        font=(font, 10))
    app.feedback_badge.pack(side=RIGHT)

    feedback_toolbar = tk.Frame(feedback, bg=palette['SURFACE'], bd=0,
                                highlightbackground=palette['BORDER'],
                                highlightthickness=1)
    feedback_toolbar.grid(row=1, column=0, sticky='ew',
                          pady=(app.px(16), 0))
    feedback_body = ttk.Frame(feedback_toolbar, style='Surface.TFrame',
                              padding=(app.px(16), app.px(12)))
    feedback_body.pack(fill=X)

    feedback_range_row = ttk.Frame(feedback_body, style='Surface.TFrame')
    feedback_range_row.pack(fill=X)
    ttk.Label(feedback_range_row, text='时间段',
              style='SurfaceMuted.TLabel').pack(side=LEFT)
    app.feedback_range_box = ttk.Combobox(
        feedback_range_row, textvariable=app.feedback_range,
        values=list(RANGE_PRESETS), state='readonly', width=12)
    app.feedback_range_box.pack(side=LEFT,
                                 padx=(app.px(8), app.px(16)))
    app.feedback_range_box.bind(
        '<<ComboboxSelected>>', lambda _e: app._auto_list_feedbacks())
    tk.Frame(feedback_range_row, bg=palette['BORDER_STRONG'], width=1).pack(
        side=LEFT, fill=Y, pady=app.px(4), padx=(0, app.px(16)))
    ttk.Label(feedback_range_row, text='自定义日期',
              style='SurfaceMuted.TLabel').pack(side=LEFT)
    app.feedback_from_entry = ttk.Entry(
        feedback_range_row, textvariable=app.feedback_from, width=11)
    app.feedback_from_entry.pack(side=LEFT, padx=(app.px(8), app.px(8)))
    ttk.Label(feedback_range_row, text='至',
              style='SurfaceMuted.TLabel').pack(side=LEFT)
    app.feedback_to_entry = ttk.Entry(
        feedback_range_row, textvariable=app.feedback_to, width=11)
    app.feedback_to_entry.pack(side=LEFT, padx=(app.px(8), 0))
    app.feedback_from_entry.bind(
        '<Return>', lambda _e: app._auto_list_feedbacks())
    app.feedback_to_entry.bind(
        '<Return>', lambda _e: app._auto_list_feedbacks())
    app.feedback_list_button = ttk.Button(
        feedback_range_row, text='列出反馈', style='Secondary.TButton',
        command=app.on_list_feedbacks, state='disabled')
    app.feedback_list_button.pack(side=RIGHT, padx=(app.px(16), 0))

    feedback_filter_row = ttk.Frame(feedback_body, style='Surface.TFrame')
    feedback_filter_row.pack(fill=X, pady=(app.px(8), 0))
    ttk.Label(feedback_filter_row, text='班级',
              style='SurfaceMuted.TLabel').pack(side=LEFT)
    # 反馈页也把班级做成横排按钮：每个班一眼能点，不再只藏在下拉框里。
    app.feedback_class_bar = ttk.Frame(
        feedback_filter_row, style='Surface.TFrame')
    app.feedback_class_bar.pack(
        side=LEFT, fill=X, expand=True, padx=(app.px(8), 0))
    app.feedback_class_bar.bind(
        '<Configure>', app._on_feedback_class_bar_resize)
    app.feedback_class_buttons = {}
    app.feedback_more_button = None
    app._feedback_hidden_count = 0
    app.feedback_class_hint = ttk.Label(
        feedback_filter_row, text='全部班级 · 按时间降序',
        style='SurfaceFaint.TLabel')
    app.feedback_class_hint.pack(side=RIGHT, padx=(app.px(12), 0))

    app.feedback_hint = ttk.Label(
        feedback_body, textvariable=app.client_hint,
        style='SurfaceHint.TLabel', justify=LEFT,
        wraplength=app.px(520))
    app.feedback_hint.pack(fill=X, pady=(app.px(8), 0))

    # 进度用独立弹窗，不占列表高度
    app.feedback_progress = progress_panel(feedback, app.on_cancel,
                                           compact=False)

    feedback_list_head = ttk.Frame(feedback, style='Card.TFrame')
    feedback_list_head.grid(row=2, column=0, sticky='ew',
                            pady=(app.px(12), app.px(8)))
    ttk.Label(feedback_list_head, text='反馈文件',
              style='SectionSmall.TLabel').pack(side=LEFT)
    ttk.Label(feedback_list_head, textvariable=app.feedback_meta,
              style='Faint.TLabel').pack(side=LEFT, padx=(app.px(12), 0))
    app.feedback_clear_button = ttk.Button(
        feedback_list_head, text='清空', style='Quiet.TButton',
        command=app.on_clear_feedbacks, state='disabled')
    app.feedback_clear_button.pack(side=RIGHT, padx=(app.px(8), 0))
    app.feedback_select_button = ttk.Button(
        feedback_list_head, text='全选', style='Quiet.TButton',
        command=app.on_select_all_feedbacks, state='disabled')
    app.feedback_select_button.pack(side=RIGHT, padx=(app.px(8), 0))

    feedback.rowconfigure(3, weight=1)
    app.feedback_tree_wrap = tk.Frame(feedback, bg=palette['CARD'], bd=0,
                                       highlightbackground=palette['BORDER'],
                                       highlightthickness=1)
    app.feedback_tree_wrap.grid(row=3, column=0, sticky='nsew')
    app.feedback_tree = ttk.Treeview(
        app.feedback_tree_wrap,
        columns=('class', 'title', 'date'),
        show='tree headings', height=9, selectmode='none',
        style='Board.Treeview')
    app.feedback_tree.heading('#0', text='选择', anchor='center')
    app.feedback_tree.heading('class', text='班级', anchor='w')
    app.feedback_tree.heading('title', text='名称', anchor='w')
    app.feedback_tree.heading('date', text='时间', anchor='center')
    app.feedback_tree.column('#0', width=app.px(84),
                              minwidth=app.px(84), anchor='center',
                              stretch=False)
    app.feedback_tree.column('class', width=app.px(230),
                              minwidth=app.px(150), anchor='w')
    app.feedback_tree.column('title', width=app.px(500),
                              minwidth=app.px(300), anchor='w')
    app.feedback_tree.column('date', width=app.px(150),
                              minwidth=app.px(132), anchor='center',
                              stretch=False)
    for tag, bg, fg, font_size in (
            ('unchecked', palette['CARD'], palette['TEXT_DARK'], 10),
            ('checked', palette['CHECKED_BG'], palette['TEXT_DARK'], 10),
            ('child_unchecked', palette['SURFACE'], palette['TEXT_BODY'], 9),
            ('child_checked', palette['SURFACE_ALT'], palette['TEXT_BODY'], 9)):
        app.feedback_tree.tag_configure(
            tag, background=bg, foreground=fg, font=(font, font_size))
    feedback_scroll = ttk.Scrollbar(app.feedback_tree_wrap,
                                    orient='vertical',
                                    command=app.feedback_tree.yview,
                                    style='Vertical.TScrollbar')
    app.feedback_tree.configure(yscrollcommand=feedback_scroll.set)
    app.feedback_tree.pack(side=LEFT, fill=BOTH, expand=True)
    feedback_scroll.pack(side=RIGHT, fill=Y)
    app.feedback_tree.bind('<Button-1>', app._on_feedback_click)
    app.feedback_tree.bind('<<TreeviewOpen>>',
                            lambda _e: app._remember_feedback_open(
                                app.feedback_tree.focus()))
    app.feedback_tree.bind('<<TreeviewClose>>',
                            lambda _e: app._remember_feedback_open(
                                app.feedback_tree.focus()))

    app.feedback_empty = tk.Frame(app.feedback_tree_wrap, bg=palette['CARD'])
    app.feedback_empty_image = tk.Label(app.feedback_empty, bg=palette['CARD'],
                                         bd=0)
    app.feedback_empty_text = tk.Label(
        app.feedback_empty,
        text='还没有反馈文件 · 连接登录态后点「列出反馈」',
        bg=palette['CARD'], fg=palette['TEXT_FAINT'], font=(font, 10),
        wraplength=app.px(380), justify='center')
    app.feedback_empty_text.pack(pady=(app.px(8), 0))
    app.feedback_empty.place(relx=0.5, rely=0.55, anchor='center')
    app.feedback_empty.lift()

    feedback_row = ttk.Frame(feedback, style='Card.TFrame')
    feedback_row.grid(row=4, column=0, sticky='ew', pady=(app.px(12), 0))
    app.feedback_badge_count = tk.Label(
        feedback_row, text='已选 0 项', bg=palette['CARD'], fg=palette['TEXT_MUTED'],
        font=(font, 10))
    app.feedback_badge_count.pack(side=LEFT)
    ttk.Label(feedback_row, text='点选择列勾选 · 点名称展开 · '
                                 '每个文件可单独选择',
              style='Faint.TLabel').pack(side=LEFT, padx=(app.px(12), 0))
    app.feedback_export_button = ttk.Button(
        feedback_row, text='导出所选反馈', style='Primary.TButton',
        command=app.on_export_feedbacks, state='disabled')
    app.feedback_export_button.pack(side=RIGHT)

    app.feedback_log = log_panel(
        feedback, title='反馈运行日志',
        subtitle='课堂权限、作业成绩与导出诊断', height=5,
        collapsible=True, expanded=False, px=app.px,
        on_toggle=app.on_toggle_feedback_log)
    app.feedback_log.frame.grid(row=5, column=0, sticky='ew',
                                 pady=(app.px(12), 0))
