from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date

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
        return f"{month}_Баллы_{self.report_month.year}.csv"


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


def build_points_csv(rows: tuple[PointsReportRow, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(("ФИО", "Баллы"))
    for row in sorted(rows, key=lambda item: item.full_name.casefold()):
        writer.writerow((row.full_name, format_points(row.points)))
    return output.getvalue().encode("utf-8-sig")


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
