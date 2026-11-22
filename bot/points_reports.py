from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .api import PointsReportRow


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
