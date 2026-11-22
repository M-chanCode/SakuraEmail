from __future__ import annotations

import unittest
from datetime import date
from io import BytesIO

from openpyxl import load_workbook

from bot.api import PointsReportRow
from bot.points_reports import build_points_workbook, points_period_for_day


class PointsPeriodTests(unittest.TestCase):
    def test_period_before_cutoff_uses_previous_month_25_to_current_24(self) -> None:
        period = points_period_for_day(date(2026, 11, 22))

        self.assertEqual(period.start, date(2026, 10, 25))
        self.assertEqual(period.end, date(2026, 11, 24))
        self.assertEqual(period.filename, "Ноябрь_Баллы_2026.xlsx")

    def test_period_after_cutoff_starts_next_payroll_month(self) -> None:
        period = points_period_for_day(date(2026, 11, 25))

        self.assertEqual(period.start, date(2026, 11, 25))
        self.assertEqual(period.end, date(2026, 12, 24))
        self.assertEqual(period.filename, "Декабрь_Баллы_2026.xlsx")

    def test_period_crosses_year_boundary(self) -> None:
        period = points_period_for_day(date(2027, 1, 10))

        self.assertEqual(period.start, date(2026, 12, 25))
        self.assertEqual(period.end, date(2027, 1, 24))
        self.assertEqual(period.filename, "Январь_Баллы_2027.xlsx")

    def test_workbook_is_sorted_by_name_and_first_column_is_four_times_wider(self) -> None:
        content = build_points_workbook(
            (
                PointsReportRow(full_name="Яковлева Яна", points=12.5),
                PointsReportRow(full_name="Абрамова Анна", points=10),
            )
        )

        workbook = load_workbook(BytesIO(content), data_only=True)
        worksheet = workbook["Баллы"]
        self.assertEqual(
            list(worksheet.values),
            [("ФИО", "Баллы"), ("Абрамова Анна", 10), ("Яковлева Яна", 12.5)],
        )
        self.assertEqual(worksheet.column_dimensions["A"].width, 48)
        self.assertEqual(worksheet.column_dimensions["B"].width, 12)


if __name__ == "__main__":
    unittest.main()
