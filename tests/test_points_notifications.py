from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot

from bot.api import PointsReport, PointsReportRow, SakuraAppsScriptApi
from bot.config import Settings
from bot.db import Database
from bot.formatting import MOSCOW_TIMEZONE
from bot.points_notifications import deliver_monthly_points_reports


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
        self.api.get_points_report.return_value = PointsReport(
            start_date="2026-10-25",
            end_date="2026-11-24",
            rows=(PointsReportRow(full_name="Иванова Анна", points=42),),
        )
        self.bot = AsyncMock(spec=Bot)

    async def asyncTearDown(self) -> None:
        await self.database.close()
        self._temporary_directory.cleanup()

    async def test_report_is_sent_at_0930_on_24th_and_only_once(self) -> None:
        before = datetime(2026, 11, 24, 9, 29, tzinfo=MOSCOW_TIMEZONE)
        due = datetime(2026, 11, 24, 9, 30, tzinfo=MOSCOW_TIMEZONE)

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
        self.assertEqual(filenames, {"Ноябрь_Баллы_2026.csv"})

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


if __name__ == "__main__":
    unittest.main()
