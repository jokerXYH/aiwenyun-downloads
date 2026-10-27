"""Excel 导出：矩阵主表 + 可选明细表，原子写入。"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from matrix import Matrix

FONT_NAME = '微软雅黑'
HEADER_FILL = PatternFill('solid', fgColor='FFE8F1EA')
ZEBRA_FILL = PatternFill('solid', fgColor='FFF7F8F7')
FAIL_FILL = PatternFill('solid', fgColor='FFFDE8E7')
OK_FILL = PatternFill('solid', fgColor='FFEAF6EE')
THIN = Side(style='thin', color='FFD9D9D9')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BODY_FONT = Font(name=FONT_NAME, size=11, color='FF1D1D1F')
HEADER_FONT = Font(name=FONT_NAME, size=11, bold=True, color='FF1F3D2B')
CENTER = Alignment(horizontal='center', vertical='center')
LEFT = Alignment(horizontal='left', vertical='center', indent=1)
WRAP_LEFT = Alignment(horizontal='left', vertical='center', wrap_text=True, indent=1)
HEADER_HEIGHT = 24
ROW_HEIGHT = 20
LABEL_WIDTH = 34
CODE_WIDTH = 11


def _style_header(cell) -> None:
    cell.font = HEADER_FONT
    cell.fill = HEADER_FILL
    cell.border = BORDER
    cell.alignment = CENTER


def _fill_matrix_sheet(sheet, matrix: Matrix) -> None:
    sheet.cell(row=1, column=1, value='班级')
    for column, lesson in enumerate(matrix.lessons, start=2):
        sheet.cell(row=1, column=column, value='第%d节' % lesson)
    for row_index, label in enumerate(matrix.labels, start=2):
        sheet.cell(row=row_index, column=1, value=label)
        for column, lesson in enumerate(matrix.lessons, start=2):
            cell = sheet.cell(row=row_index, column=column,
                              value=matrix.code(label, lesson) or '')
            cell.number_format = '@'
    last_column = len(matrix.lessons) + 1
    last_row = len(matrix.labels) + 1
    for column in range(1, last_column + 1):
        _style_header(sheet.cell(row=1, column=column))
    sheet.row_dimensions[1].height = HEADER_HEIGHT
    for row_index in range(2, last_row + 1):
        sheet.row_dimensions[row_index].height = ROW_HEIGHT
        for column in range(1, last_column + 1):
            cell = sheet.cell(row=row_index, column=column)
            cell.font = BODY_FONT
            cell.border = BORDER
            cell.alignment = LEFT if column == 1 else CENTER
            if row_index % 2 == 0:
                cell.fill = ZEBRA_FILL
    sheet.column_dimensions['A'].width = LABEL_WIDTH
    for column in range(2, last_column + 1):
        sheet.column_dimensions[get_column_letter(column)].width = CODE_WIDTH
    sheet.freeze_panes = 'B2'
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    sheet.page_setup.orientation = 'landscape'
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_title_rows = '1:1'
    sheet.print_options.horizontalCentered = True


DETAIL_HEADERS = [
    ('上课时间', 17, 'date'),
    ('课堂ID', 12, 'classId'),
    ('课堂名称', 38, 'topic'),
    ('课程', 30, 'course'),
    ('课次', 7, 'lesson'),
    ('邀请码', 11, 'code'),
    ('状态', 8, 'status'),
    ('备注', 34, 'note'),
    ('班级/群组', 30, 'groups'),
]


def _fill_detail_sheet(sheet, details: list[dict[str, Any]]) -> None:
    for column, (title, width, _) in enumerate(DETAIL_HEADERS, start=1):
        _style_header(sheet.cell(row=1, column=column, value=title))
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.row_dimensions[1].height = HEADER_HEIGHT

    for row_index, item in enumerate(details, start=2):
        status = '成功' if item.get('code') else '失败'
        values = {
            'date': item.get('date') or '',
            'classId': str(item.get('classId') or ''),
            'topic': item.get('topic') or '',
            'course': item.get('course') or '',
            'lesson': item.get('lesson') if item.get('lesson') is not None else '',
            'code': item.get('code') or '',
            'status': status,
            'note': item.get('note') or '',
            'groups': '、'.join(item.get('groups') or []),
        }
        for column, (_, _, key) in enumerate(DETAIL_HEADERS, start=1):
            cell = sheet.cell(row=row_index, column=column, value=values[key])
            cell.font = BODY_FONT
            cell.border = BORDER
            if key in ('topic', 'course', 'note', 'groups'):
                cell.alignment = WRAP_LEFT
            elif key == 'lesson':
                cell.alignment = CENTER
            else:
                cell.alignment = CENTER if key in ('date', 'classId', 'code', 'status') else LEFT
            if key in ('classId', 'code'):
                cell.number_format = '@'
        status_cell = sheet.cell(row=row_index, column=7)
        status_cell.fill = OK_FILL if item.get('code') else FAIL_FILL
        sheet.row_dimensions[row_index].height = 26
    if details:
        sheet.auto_filter.ref = 'A1:I%d' % (len(details) + 1)
    sheet.freeze_panes = 'A2'
    sheet.sheet_view.showGridLines = False
    sheet.print_title_rows = '1:1'


def write_workbook(path: Path, matrices: list[Matrix],
                   details: list[dict[str, Any]] | None = None,
                   metadata: dict[str, Any] | None = None) -> Path:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for matrix in matrices:
        sheet = workbook.create_sheet(_safe_title(matrix.title))
        _fill_matrix_sheet(sheet, matrix)
    if not workbook.sheetnames:
        workbook.create_sheet('邀请码')
    if details is not None:
        sheet = workbook.create_sheet('明细')
        _fill_detail_sheet(sheet, details)

    metadata = metadata or {}
    workbook.properties.creator = '爱问云邀请码导出'
    workbook.properties.title = '邀请码'
    workbook.properties.description = (
        '导出时间：%s；客户端：%s；接口主机：%s' % (
            metadata.get('exported_at') or datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            metadata.get('client_version') or '未知',
            metadata.get('base_url') or '',
        )
    )
    workbook.active = 0

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.%s.tmp%s' % (path.stem, path.suffix))
    try:
        workbook.save(temp)
        try:
            os.replace(temp, path)
            return path
        except PermissionError:
            # 文件被 Excel 打开时，换一个带时间戳的文件名，保证导出不丢。
            for index in range(1, 20):
                candidate = path.with_name('%s-%d%s' % (path.stem, index, path.suffix))
                if not candidate.exists():
                    os.replace(temp, candidate)
                    return candidate
            raise
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _safe_title(title: str) -> str:
    text = (title or '邀请码').strip()
    for char in '\\/:*?[]':
        text = text.replace(char, ' ')
    return (text or '邀请码')[:31]
