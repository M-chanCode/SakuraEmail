from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .api import EmployeePointsDetail, PointsReportRow


RUSSIAN_MONTHS = (
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)

_MONTH_NUMBERS = {
    "январь": 1,
    "января": 1,
    "февраль": 2,
    "февраля": 2,
    "март": 3,
    "марта": 3,
    "апрель": 4,
    "апреля": 4,
    "май": 5,
    "мая": 5,
    "июнь": 6,
    "июня": 6,
    "июль": 7,
    "июля": 7,
    "август": 8,
    "августа": 8,
    "сентябрь": 9,
    "сентября": 9,
    "октябрь": 10,
    "октября": 10,
    "ноябрь": 11,
    "ноября": 11,
    "декабрь": 12,
    "декабря": 12,
}


@dataclass(frozen=True, slots=True)
class PointsPeriod:
    start: date
    end: date
    report_month: date

    @property
    def filename(self) -> str:
        month = RUSSIAN_MONTHS[self.report_month.month - 1]
        return f"{month}_Баллы_{self.report_month.year}.xlsx"


def points_period_for_day(today: date) -> PointsPeriod:
    if today.day <= 24:
        report_month = today.replace(day=1)
        previous_month = _shift_month(report_month, -1)
        return PointsPeriod(
            start=previous_month.replace(day=25),
            end=report_month.replace(day=24),
            report_month=report_month,
        )

    next_month = _shift_month(today.replace(day=1), 1)
    return PointsPeriod(
        start=today.replace(day=25),
        end=next_month.replace(day=24),
        report_month=next_month,
    )


def points_period_for_month(report_month: date) -> PointsPeriod:
    normalized_month = report_month.replace(day=1)
    previous_month = _shift_month(normalized_month, -1)
    return PointsPeriod(
        start=previous_month.replace(day=25),
        end=normalized_month.replace(day=24),
        report_month=normalized_month,
    )


def parse_points_month(value: str) -> date | None:
    normalized = " ".join(value.strip().lower().replace(",", " ").split())
    numeric_match = re.fullmatch(r"(\d{1,2})[.\-/\s](\d{4})", normalized)
    if numeric_match:
        month, year = (int(part) for part in numeric_match.groups())
    else:
        word_match = re.fullmatch(r"([а-яё]+)\s+(\d{4})", normalized)
        if not word_match:
            return None
        month = _MONTH_NUMBERS.get(word_match.group(1), 0)
        year = int(word_match.group(2))
    if month < 1 or month > 12 or year < 2000 or year > 2100:
        return None
    return date(year, month, 1)


def build_points_workbook(rows: tuple[PointsReportRow, ...]) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Баллы"
    worksheet.append(("ФИО", "Баллы"))
    for row in sorted(rows, key=lambda item: item.full_name.casefold()):
        worksheet.append((row.full_name, row.points))

    worksheet.column_dimensions["A"].width = 48
    worksheet.column_dimensions["B"].width = 12
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = worksheet.dimensions

    header_fill = PatternFill(fill_type="solid", fgColor="F4C2D7")
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for cell in worksheet["B"][1:]:
        cell.number_format = "0.####"
        cell.alignment = Alignment(horizontal="center")

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def build_employee_points_workbook(detail: EmployeePointsDetail) -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "Баллы"
    worksheet.append(("Дата", "Артикул", "Кол-во", "Баллы"))
    for row in detail.rows:
        worksheet.append(
            (
                _excel_date(row.report_date),
                row.article,
                row.quantity,
                row.points,
            )
        )

    total_row = worksheet.max_row + 1
    worksheet.cell(total_row, 1, "Итого за месяц")
    worksheet.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=3)
    worksheet.cell(total_row, 4, detail.total_points)

    worksheet.column_dimensions["A"].width = 18
    worksheet.column_dimensions["B"].width = 18
    worksheet.column_dimensions["C"].width = 12
    worksheet.column_dimensions["D"].width = 14
    worksheet.freeze_panes = "A2"
    if detail.rows:
        worksheet.auto_filter.ref = f"A1:D{total_row - 1}"

    header_fill = PatternFill(fill_type="solid", fgColor="F4C2D7")
    total_fill = PatternFill(fill_type="solid", fgColor="FCE4EC")
    for cell in worksheet[1]:
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
    for cell in worksheet[total_row]:
        cell.font = Font(bold=True)
        cell.fill = total_fill
        cell.alignment = Alignment(horizontal="center")
    for cell in worksheet["C"][1:total_row - 1]:
        cell.alignment = Alignment(horizontal="center")
    for cell in worksheet["A"][1:total_row - 1]:
        cell.number_format = "DD.MM.YYYY"
        cell.alignment = Alignment(horizontal="center")
    for cell in worksheet["D"][1:]:
        cell.number_format = "0.####"
        cell.alignment = Alignment(horizontal="center")

    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def format_points(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".").replace(".", ",")


def format_period(period: PointsPeriod) -> str:
    return f"{period.start:%d.%m.%Y}–{period.end:%d.%m.%Y}"


def _shift_month(value: date, offset: int) -> date:
    month_index = value.year * 12 + value.month - 1 + offset
    year, zero_based_month = divmod(month_index, 12)
    return date(year, zero_based_month + 1, 1)


def _excel_date(value: str) -> date | str:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return value
