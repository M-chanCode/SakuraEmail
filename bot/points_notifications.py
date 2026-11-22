from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile

from .access import AccessController
from .api import SakuraApiError, SakuraAppsScriptApi
from .config import Settings
from .db import Database
from .decor import PINK_STARS, SAKURA_ANIMATED
from .formatting import MOSCOW_TIMEZONE
from .keyboards import work_reminder_keyboard
from .points_reports import (
    PointsPeriod,
    build_points_workbook,
    format_period,
    points_period_for_day,
)
from .special_days import SpecialDayType


logger = logging.getLogger(__name__)
DEADLINE_REMINDER_HOUR = 10
DEADLINE_REMINDER_MINUTE = 30
REPORT_HOUR = 12
REPORT_MINUTE = 0


async def points_report_notification_worker(
    bot: Bot,
    api: SakuraAppsScriptApi,
    database: Database,
    settings: Settings,
    access: AccessController,
    poll_seconds: int = 60,
) -> None:
    while True:
        try:
            await deliver_points_deadline_reminders(
                bot,
                api,
                database,
                settings,
                access,
            )
            await deliver_monthly_points_reports(bot, api, database, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected monthly points report worker error")
        await asyncio.sleep(poll_seconds)


async def deliver_points_deadline_reminders(
    bot: Bot,
    api: SakuraAppsScriptApi,
    database: Database,
    settings: Settings,
    access: AccessController,
    now: datetime | None = None,
) -> None:
    moscow_now = _moscow_now(now)
    if moscow_now.day != 24:
        return
    if (moscow_now.hour, moscow_now.minute) < (
        DEADLINE_REMINDER_HOUR,
        DEADLINE_REMINDER_MINUTE,
    ):
        return

    users = await access.allowed_registered_users()
    if users is None:
        logger.warning("Points deadline reminder recipients could not be verified")
        return

    excluded_ids = {settings.owner_id, settings.art_director_id}
    recipients = [user for user in users if user.telegram_id not in excluded_ids]
    if not recipients:
        return

    period = points_period_for_day(moscow_now.date())
    work_dates = await _period_work_dates(database, period)
    date_keys = [value.isoformat() for value in work_dates]

    for user in recipients:
        notification_hash = _delivery_hash(period, user.telegram_id, "deadline-reminder")
        if await database.notification_was_sent(notification_hash):
            continue

        try:
            result = await api.get_missing_work_report_dates(
                user.telegram_id,
                date_keys,
            )
        except SakuraApiError:
            logger.warning(
                "Monthly missing-report check is unavailable for Telegram ID %s",
                user.telegram_id,
            )
            continue
        if not result.employee_found:
            logger.warning(
                "Monthly missing-report check did not find Telegram ID %s",
                user.telegram_id,
            )
            continue

        missing_keys = set(result.missing_dates)
        missing_dates = [
            value for value in work_dates if value.isoformat() in missing_keys
        ]
        try:
            await bot.send_message(
                user.telegram_id,
                format_points_deadline_reminder(period, missing_dates),
                reply_markup=(work_reminder_keyboard() if missing_dates else None),
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            continue
        except TelegramAPIError:
            logger.warning(
                "Telegram failed to deliver points deadline reminder to %s",
                user.telegram_id,
            )
            continue
        await database.mark_notification_sent(notification_hash, user.telegram_id)


async def deliver_monthly_points_reports(
    bot: Bot,
    api: SakuraAppsScriptApi,
    database: Database,
    settings: Settings,
    now: datetime | None = None,
) -> None:
    moscow_now = _moscow_now(now)
    if moscow_now.day != 24:
        return
    if (moscow_now.hour, moscow_now.minute) < (REPORT_HOUR, REPORT_MINUTE):
        return

    period = points_period_for_day(moscow_now.date())
    recipients = tuple(dict.fromkeys((settings.art_director_id, settings.owner_id)))
    pending_recipients = []
    for telegram_id in recipients:
        greeting_hash = _delivery_hash(period, telegram_id, "greeting")
        document_hash = _delivery_hash(period, telegram_id, "document")
        if not (
            await database.notification_was_sent(greeting_hash)
            and await database.notification_was_sent(document_hash)
        ):
            pending_recipients.append(telegram_id)
    if not pending_recipients:
        return

    try:
        report = await api.get_points_report(
            period.start.isoformat(),
            period.end.isoformat(),
        )
    except SakuraApiError:
        logger.warning("Monthly points report API is temporarily unavailable")
        return
    document_bytes = build_points_workbook(report.rows)

    for telegram_id in pending_recipients:
        if not await database.user_exists(telegram_id):
            logger.warning(
                "Monthly points recipient %s has not started the bot",
                telegram_id,
            )
            continue

        greeting_hash = _delivery_hash(period, telegram_id, "greeting")
        if not await database.notification_was_sent(greeting_hash):
            try:
                await bot.send_message(telegram_id, monthly_report_greeting(period))
            except (TelegramForbiddenError, TelegramBadRequest):
                continue
            except TelegramAPIError:
                logger.warning("Telegram failed to deliver points greeting to %s", telegram_id)
                continue
            await database.mark_notification_sent(greeting_hash, telegram_id)

        document_hash = _delivery_hash(period, telegram_id, "document")
        if await database.notification_was_sent(document_hash):
            continue
        try:
            await bot.send_document(
                telegram_id,
                document=BufferedInputFile(document_bytes, filename=period.filename),
                caption=(
                    f"{PINK_STARS} Баллы за период "
                    f"<code>{format_period(period)}</code>"
                ),
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            continue
        except TelegramAPIError:
            logger.warning("Telegram failed to deliver points workbook to %s", telegram_id)
            continue
        await database.mark_notification_sent(document_hash, telegram_id)


def monthly_report_greeting(period: PointsPeriod) -> str:
    return (
        f"{SAKURA_ANIMATED} <b>Привет, наш дорогой Арт-директор!</b> "
        f"{PINK_STARS}\n\n"
        "Вот отчёт по баллам "
        f"за <code>{format_period(period)}</code> (｡•̀ᴗ-)✧"
    )


def format_points_deadline_reminder(
    period: PointsPeriod,
    missing_dates: list[date],
) -> str:
    lines = [
        f"{SAKURA_ANIMATED} <b>До итоговой выгрузки баллов осталось полтора часа!</b>",
        "",
        "Проверьте, пожалуйста, все отчёты за период "
        f"<code>{format_period(period)}</code> (｡•̀ᴗ-)✧",
    ]
    if not missing_dates:
        lines.extend(["", "Все отчёты уже заполнены. Спасибо! ♡"])
        return "\n".join(lines)

    weekdays = ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС")
    missing_lines = "\n".join(
        f"{weekdays[value.weekday()]} • <code>{value:%d.%m.%Y}</code>"
        for value in missing_dates
    )
    lines.extend(
        [
            "",
            "Не заполнены отчёты за:",
            f"<blockquote expandable>{missing_lines}</blockquote>",
            "Пожалуйста, заполните их до формирования итоговой таблицы.",
        ]
    )
    return "\n".join(lines)


async def _period_work_dates(
    database: Database,
    period: PointsPeriod,
) -> tuple[date, ...]:
    special_days = {
        item.work_date: item.day_type
        for item in await database.list_special_days(period.start, period.end)
    }
    result: list[date] = []
    current = period.start
    while current <= period.end:
        day_type = special_days.get(current)
        if day_type != SpecialDayType.HOLIDAY and (
            current.weekday() < 5
            or day_type in {SpecialDayType.SHORTENED, SpecialDayType.WORKING_WEEKEND}
        ):
            result.append(current)
        current += timedelta(days=1)
    return tuple(result)


def _delivery_hash(period: PointsPeriod, telegram_id: int, part: str) -> str:
    value = f"monthly-points|{period.report_month.isoformat()}|{telegram_id}|{part}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _moscow_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(MOSCOW_TIMEZONE)
    if value.tzinfo is None:
        return value.replace(tzinfo=MOSCOW_TIMEZONE)
    return value.astimezone(MOSCOW_TIMEZONE)
