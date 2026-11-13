from __future__ import annotations

import unittest
from datetime import date

from bot.special_days import (
    SpecialDay,
    SpecialDayType,
    format_special_days,
    parse_special_day_changes,
)


class SpecialDayFormattingTests(unittest.TestCase):
    def test_changes_support_all_types_deletion_and_last_value_wins(self) -> None:
        changes = parse_special_day_changes(
            "01.01.2027 праздник\n"
            "31.12.2026 сокращённый день\n"
            "07.11.2026 рабочий день в выходной\n"
            "01.01.2027 удалить"
        )

        self.assertEqual(len(changes), 3)
        by_date = {change.work_date: change.day_type for change in changes}
        self.assertIsNone(by_date[date(2027, 1, 1)])
        self.assertEqual(
            by_date[date(2026, 12, 31)],
            SpecialDayType.SHORTENED,
        )
        self.assertEqual(
            by_date[date(2026, 11, 7)],
            SpecialDayType.WORKING_WEEKEND,
        )

    def test_invalid_line_reports_its_number(self) -> None:
        with self.assertRaisesRegex(ValueError, "Строка 2"):
            parse_special_day_changes("01.01.2027 праздник\nневерная строка")

    def test_calendar_groups_and_formats_every_type(self) -> None:
        text = format_special_days(
            [
                SpecialDay(date(2027, 1, 1), SpecialDayType.HOLIDAY),
                SpecialDay(date(2026, 12, 31), SpecialDayType.SHORTENED),
                SpecialDay(date(2026, 11, 7), SpecialDayType.WORKING_WEEKEND),
            ]
        )

        self.assertIn("Праздники · нерабочие дни", text)
        self.assertIn("Сокращённые рабочие дни", text)
        self.assertIn("Рабочие дни в выходные", text)
        self.assertIn("<code>01.01.2027</code>", text)
        self.assertIn("<code>31.12.2026</code>", text)
        self.assertIn("<code>07.11.2026</code>", text)


if __name__ == "__main__":
    unittest.main()
