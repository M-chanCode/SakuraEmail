from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from aiogram.types import BufferedInputFile

from .api import SakuraApiError, SakuraAppsScriptApi
from .config import Settings
from .db import Database
from .decor import PINK_STARS, SAKURA_ANIMATED
from .formatting import MOSCOW_TIMEZONE
from .points_reports import (
    PointsPeriod,
    build_points_csv,
    format_period,
    points_period_for_day,
)


logger = logging.getLogger(__name__)
REPORT_HOUR = 9
REPORT_MINUTE = 30


async def points_report_notification_worker(
    bot: Bot,
    api: SakuraAppsScriptApi,
    database: Database,
    settings: Settings,
    poll_seconds: int = 60,
) -> None:
    while True:
        try:
            await deliver_monthly_points_reports(bot, api, database, settings)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unexpected monthly points report worker error")
        await asyncio.sleep(poll_seconds)


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
    document_bytes = build_points_csv(report.rows)

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
            logger.warning("Telegram failed to deliver points CSV to %s", telegram_id)
            continue
        await database.mark_notification_sent(document_hash, telegram_id)


def monthly_report_greeting(period: PointsPeriod) -> str:
    return (
        f"{SAKURA_ANIMATED} <b>Привет, наш дорогой Арт-директор!</b> "
        f"{PINK_STARS}\n\n"
        "Вот отчёт по баллам "
        f"за <code>{format_period(period)}</code> (｡•̀ᴗ-)✧"
    )


def _delivery_hash(period: PointsPeriod, telegram_id: int, part: str) -> str:
    value = f"monthly-points|{period.report_month.isoformat()}|{telegram_id}|{part}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _moscow_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(MOSCOW_TIMEZONE)
    if value.tzinfo is None:
        return value.replace(tzinfo=MOSCOW_TIMEZONE)
    return value.astimezone(MOSCOW_TIMEZONE)
