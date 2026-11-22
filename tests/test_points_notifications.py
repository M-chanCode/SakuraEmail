from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot

from bot.access import AccessController
from bot.api import (
    MissingReportsResult,
    PointsReport,
    PointsReportRow,
    SakuraAppsScriptApi,
)
from bot.config import Settings
from bot.db import BotUser, Database
from bot.formatting import MOSCOW_TIMEZONE
from bot.points_notifications import (
    deliver_monthly_points_reports,
    deliver_points_deadline_reminders,
)
from bot.special_days import SpecialDayChange, SpecialDayType


class MonthlyPointsNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self._temporary_directory.name) / "test.sqlite3")
        await self.database.connect()
        for telegram_id, username in ((101, "Owner"), (202, "ArtDirector")):
            await self.database.upsert_user(
                telegram_id=telegram_id,
                username=username,
                first_name=username,
                last_name="",
                role="creator" if telegram_id == 101 else "user",
            )
        self.settings = Settings(
            bot_token="token",
            owner_id=101,
            art_director_id=202,
            apps_script_url="https://example.com/exec",
            bot_api_secret="x" * 32,
            database_path=Path("unused.sqlite3"),
            notification_poll_seconds=3600,
        )
        self.api = AsyncMock(spec=SakuraAppsScriptApi)
        self.access = AsyncMock(spec=AccessController)
        self.access.allowed_registered_users.return_value = [
            BotUser(101, "Owner", "Owner", ""),
            BotUser(202, "ArtDirector", "Art", "Director"),
            BotUser(303, "Employee", "Employee", ""),
        ]
        await self.database.upsert_user(
            telegram_id=303,
            username="Employee",
            first_name="Employee",
            last_name="",
            role="user",
        )
        self.api.get_points_report.return_value = PointsReport(
            start_date="2026-10-25",
            end_date="2026-11-24",
            rows=(PointsReportRow(full_name="Иванова Анна", points=42),),
        )
        self.bot = AsyncMock(spec=Bot)

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._temporary_directory.cleanup()

    async def test_report_is_sent_at_noon_to_owner_and_art_director_only_once(self) -> None:
        before = datetime(2026, 11, 24, 11, 59, tzinfo=MOSCOW_TIMEZONE)
        due = datetime(2026, 11, 24, 12, 0, tzinfo=MOSCOW_TIMEZONE)

        await deliver_monthly_points_reports(
            self.bot,
            self.api,
            self.database,
            self.settings,
            before,
        )
        self.api.get_points_report.assert_not_awaited()

        await deliver_monthly_points_reports(
            self.bot,
            self.api,
            self.database,
            self.settings,
            due,
        )
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.assertEqual(self.bot.send_document.await_count, 2)
        filenames = {
            call.kwargs["document"].filename
            for call in self.bot.send_document.await_args_list
        }
        self.assertEqual(filenames, {"Ноябрь_Баллы_2026.xlsx"})

        await deliver_monthly_points_reports(
            self.bot,
            self.api,
            self.database,
            self.settings,
            due,
        )
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.assertEqual(self.bot.send_document.await_count, 2)
        self.api.get_points_report.assert_awaited_once_with(
            "2026-10-25",
            "2026-11-24",
        )

    async def test_1030_reminder_lists_only_missing_required_work_dates(self) -> None:
        holiday = date(2026, 11, 23)
        working_weekend = date(2026, 11, 21)
        await self.database.apply_special_day_changes(
            [
                SpecialDayChange(holiday, SpecialDayType.HOLIDAY),
                SpecialDayChange(working_weekend, SpecialDayType.WORKING_WEEKEND),
            ]
        )
        self.api.get_missing_work_report_dates.return_value = MissingReportsResult(
            employee_found=True,
            missing_dates=("2026-11-20", "2026-11-21"),
        )
        before = datetime(2026, 11, 24, 10, 29, tzinfo=MOSCOW_TIMEZONE)
        due = datetime(2026, 11, 24, 10, 30, tzinfo=MOSCOW_TIMEZONE)

        await deliver_points_deadline_reminders(
            self.bot,
            self.api,
            self.database,
            self.settings,
            self.access,
            before,
        )
        self.api.get_missing_work_report_dates.assert_not_awaited()

        await deliver_points_deadline_reminders(
            self.bot,
            self.api,
            self.database,
            self.settings,
            self.access,
            due,
        )
        self.assertEqual(self.api.get_missing_work_report_dates.await_count, 2)
        requests = {
            call.args[0]: call.args[1]
            for call in self.api.get_missing_work_report_dates.await_args_list
        }
        self.assertEqual(set(requests), {101, 303})
        report_dates = requests[303]
        self.assertNotIn("2026-11-22", report_dates)
        self.assertNotIn("2026-11-23", report_dates)
        self.assertIn("2026-11-21", report_dates)
        self.assertEqual(self.bot.send_message.await_count, 2)
        messages = {
            call.args[0]: call.args[1]
            for call in self.bot.send_message.await_args_list
        }
        self.assertEqual(set(messages), {101, 303})
        self.assertIn("<blockquote expandable>", messages[303])
        self.assertIn("20.11.2026", messages[303])
        self.assertIn("21.11.2026", messages[303])

        await deliver_points_deadline_reminders(
            self.bot,
            self.api,
            self.database,
            self.settings,
            self.access,
            due,
        )
        self.assertEqual(self.bot.send_message.await_count, 2)


if __name__ == "__main__":
    unittest.main()
