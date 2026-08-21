from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


SHEET_NAME = "可注册COM域名"
HEADERS = ("域名", "查询时间", "规律", "固定开头", "固定结尾", "查询网站")


class ExcelStoreError(RuntimeError):
    pass


class ExcelStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser()
        if self.path.suffix.lower() != ".xlsx":
            raise ExcelStoreError("结果文件必须使用 .xlsx 后缀。")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _load_or_create(self):
        if self.path.exists():
            try:
                workbook = load_workbook(self.path)
            except Exception as exc:
                raise ExcelStoreError(f"无法打开Excel文件：{exc}") from exc
            if SHEET_NAME not in workbook.sheetnames:
                sheet = workbook.create_sheet(SHEET_NAME)
                self._initialize_sheet(sheet)
            else:
                sheet = workbook[SHEET_NAME]
                existing_headers = tuple(sheet.cell(1, i + 1).value for i in range(len(HEADERS)))
                if sheet.max_row > 1 and existing_headers != HEADERS:
                    raise ExcelStoreError("所选文件中的结果工作表格式不兼容，请选择新的Excel文件。")
                if sheet.max_row == 1 and all(value is None for value in existing_headers):
                    self._initialize_sheet(sheet)
            return workbook, sheet

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = SHEET_NAME
        self._initialize_sheet(sheet)
        return workbook, sheet

    @staticmethod
    def _initialize_sheet(sheet) -> None:
        sheet.append(HEADERS)
        fill = PatternFill("solid", fgColor="1F4E78")
        for cell in sheet[1]:
            cell.fill = fill
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = "A1:F1"
        widths = (32, 21, 14, 18, 18, 28)
        for index, width in enumerate(widths, start=1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        sheet.row_dimensions[1].height = 24
        sheet.sheet_view.showGridLines = False

    def append_if_new(
        self,
        domain: str,
        checked_at: str,
        pattern: str,
        prefix: str,
        suffix: str,
        site: str,
    ) -> bool:
        workbook, sheet = self._load_or_create()
        normalized = domain.lower()
        existing = {
            str(sheet.cell(row, 1).value).strip().lower()
            for row in range(2, sheet.max_row + 1)
            if sheet.cell(row, 1).value
        }
        if normalized in existing:
            return False
        try:
            time_value = datetime.fromisoformat(checked_at)
            if time_value.tzinfo is not None:
                time_value = time_value.astimezone().replace(tzinfo=None)
        except ValueError:
            time_value = checked_at
        sheet.append((normalized, time_value, pattern, prefix, suffix, site))
        row = sheet.max_row
        sheet.cell(row, 1).hyperlink = f"https://{normalized}"
        sheet.cell(row, 1).style = "Hyperlink"
        sheet.cell(row, 2).number_format = "yyyy-mm-dd hh:mm:ss"
        sheet.auto_filter.ref = f"A1:F{row}"
        self._atomic_save(workbook)
        return True

    def sync_found_rows(self, rows: list[tuple[str, str, str, str, str]], site: str) -> int:
        added = 0
        for domain, checked_at, pattern, prefix, suffix in rows:
            if self.append_if_new(domain, checked_at, pattern, prefix, suffix, site):
                added += 1
        return added

    def _atomic_save(self, workbook) -> None:
        fd, temp_name = tempfile.mkstemp(prefix="domain-results-", suffix=".xlsx", dir=self.path.parent)
        os.close(fd)
        try:
            workbook.save(temp_name)
            os.replace(temp_name, self.path)
        except Exception as exc:
            raise ExcelStoreError(f"保存Excel失败：{exc}") from exc
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
