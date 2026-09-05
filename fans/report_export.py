"""
Shared "professional Excel report" builder — v2.1.19 UX pass, section 33.

Raw per-row exports still exist (report_claims etc. build their own
worksheet when they need bespoke columns/types), but a plain
openpyxl.Workbook().active with a single header row reads as a raw data
dump, not an administrative report. This gives every NEW export in this
pass (Beneficiary Master List, and future ones) a consistent, readable
shape without each view re-implementing formatting:

- report title + generated timestamp + generated-by + applied-filter summary
- styled/frozen/wrapped header row with autofilter
- readable column widths (no #### from unnecessarily narrow columns)
- alternating rows are left to the caller's data (no fabricated totals)

Deliberately minimal — this is formatting only, not a report engine. Callers
pass already-computed rows; this module never queries the database.
"""
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from django.utils import timezone as _tz

HEADER_FILL = PatternFill(start_color='1A3A6B', end_color='1A3A6B', fill_type='solid')
HEADER_FONT = Font(color='FFFFFF', bold=True)
TITLE_FONT = Font(bold=True, size=14, color='1A3A6B')
META_FONT = Font(size=9, color='666666')
TOTALS_FONT = Font(bold=True)
TOTALS_BORDER = Border(top=Side(style='thin', color='1A3A6B'))
CURRENCY_FORMAT = u'#,##0.00'
DATETIME_FORMAT = 'yyyy-mm-dd hh:mm'


def build_report_workbook(*, title, sheet_name, headers, rows, generated_by, filters=None,
                           column_widths=None, currency_columns=None, date_columns=None,
                           totals_row=None):
    """
    Build a single-sheet, formatted report workbook.

    `rows` is a list of lists (already-formatted cell values — numbers as
    real numbers/Decimals so Excel's own currency/number formatting applies,
    not pre-stringified; datetimes as real `datetime`/`date` objects, not
    pre-formatted strings, when the column is listed in `date_columns`).
    `filters` is an optional dict of applied filter values (falsy values are
    skipped) shown as a one-line summary under the title.

    `currency_columns` / `date_columns` are 0-indexed column positions (into
    `headers`/each row) that get a real PHP-currency / datetime Excel number
    format applied, instead of a plain unformatted number or date string —
    this is what avoids Excel rendering a narrow date column as `########`.
    `totals_row`, if given, is one more row (same length as `headers`, use
    None for cells with nothing to total) appended after the data with a
    bold font and a top border, never counted as a data row for the
    autofilter/freeze-pane range.

    Returns the Workbook; caller is responsible for wb.save(response).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]  # Excel sheet-name length limit

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max(len(headers), 1))
    ws.cell(row=1, column=1, value=f'FANS-C — {title}').font = TITLE_FONT

    generated_line = f'Generated: {_tz.localtime(_tz.now()).strftime("%Y-%m-%d %H:%M")}  ·  By: {generated_by}'
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max(len(headers), 1))
    ws.cell(row=2, column=1, value=generated_line).font = META_FONT

    filter_summary = ''
    if filters:
        applied = ', '.join(f'{k}={v}' for k, v in filters.items() if v)
        filter_summary = f'Applied Filters: {applied}' if applied else 'Applied Filters: None'
        ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=max(len(headers), 1))
        ws.cell(row=3, column=1, value=filter_summary).font = META_FONT

    header_row = 4 if filter_summary else 3
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical='center')

    currency_columns = set(currency_columns or [])
    date_columns = set(date_columns or [])

    for row_idx, row in enumerate(rows, start=header_row + 1):
        for col_idx, value in enumerate(row, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            zero_indexed = col_idx - 1
            if value is not None and zero_indexed in currency_columns:
                cell.number_format = CURRENCY_FORMAT
                cell.alignment = Alignment(horizontal='right')
            elif value is not None and zero_indexed in date_columns:
                cell.number_format = DATETIME_FORMAT

    last_col_letter = get_column_letter(max(len(headers), 1))
    last_data_row = header_row + len(rows)
    ws.auto_filter.ref = f'A{header_row}:{last_col_letter}{max(last_data_row, header_row)}'
    ws.freeze_panes = f'A{header_row + 1}'

    if totals_row:
        totals_row_idx = last_data_row + 1
        for col_idx, value in enumerate(totals_row, start=1):
            if value is None:
                continue
            cell = ws.cell(row=totals_row_idx, column=col_idx, value=value)
            cell.font = TOTALS_FONT
            cell.border = TOTALS_BORDER
            if (col_idx - 1) in currency_columns:
                cell.number_format = CURRENCY_FORMAT
                cell.alignment = Alignment(horizontal='right')

    widths = column_widths or [max(12, min(32, len(str(h)) + 4)) for h in headers]
    for col_idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.row_dimensions[header_row].height = 28
    return wb
