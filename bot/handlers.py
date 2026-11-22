from __future__ import annotations

import asyncio
import random
import re
from datetime import datetime
from html import escape
from typing import Any

from aiogram import Bot, F, Router
from aiogram.enums import MessageEntityType
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.filters import BaseFilter, Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message, User

from .access import AccessController, AccessDecision
from .api import SakuraApiError, SakuraAppsScriptApi
from .config import Settings
from .db import Database, SupportRequest, ThanksStats
from .decor import (
    DECORATION_DOT,
    MINIMAL_FLOWER,
    PINK_STARS,
    SAKURA_1,
    SAKURA_2,
    SAKURA_3,
    SAKURA_4,
    SAKURA_ANIMATED,
)
from .formatting import display_license_type, format_datetime
from .formatting import MOSCOW_TIMEZONE
from .keyboards import (
    KEYS_TEXT,
    LEGACY_REQUEST_KEY_TEXT,
    MANAGEMENT_TEXT,
    MY_KEYS_TEXT,
    THANKS_TEXT,
    add_thanks_comment_keyboard,
    application_keyboard,
    broadcast_confirmation_keyboard,
    cancel_action_keyboard,
    close_request_keyboard,
    main_keyboard,
    management_keyboard,
    special_days_keyboard,
    thanks_settings_keyboard,
)
from .special_days import format_special_days, parse_special_day_changes
from .points_reports import build_points_workbook, format_period, points_period_for_day
from .states import (
    ApplicationStates,
    CreatorBroadcastStates,
    CreatorMessageStates,
    SpecialDayStates,
    ThanksCommentStates,
    UserIdMigrationStates,
)


APPLICATION_PROMPT = (
    f"{DECORATION_DOT} Опишите в одном предложении ваш запрос. Вы можете запросить дополнительный "
    "ключ, получить техническую поддержку или просто поблагодарить :3\n\n"
    "При запросе дополнительного ключа сообщите, зачем вам нужен новый ключ."
)
AINO_STICKER_SET_NAME = "AinoStick"


class OwnerCustomEmojiFilter(BaseFilter):
    def __init__(self, owner_id: int) -> None:
        self._owner_id = owner_id

    async def __call__(self, message: Message) -> bool | dict[str, Any]:
        if message.from_user is None or message.from_user.id != self._owner_id:
            return False
        custom_emoji_ids = _custom_emoji_ids(message)
        if not custom_emoji_ids:
            return False
        return {"custom_emoji_ids": custom_emoji_ids}


class StickerPackResponder:
    def __init__(self, sticker_set_name: str) -> None:
        self._sticker_set_name = sticker_set_name
        self._file_ids: tuple[str, ...] = ()
        self._lock = asyncio.Lock()

    async def choose(self, bot: Bot) -> str | None:
        if not self._file_ids:
            async with self._lock:
                if not self._file_ids:
                    sticker_set = await bot.get_sticker_set(
                        name=self._sticker_set_name,
                    )
                    self._file_ids = tuple(
                        sticker.file_id
                        for sticker in sticker_set.stickers
                        if sticker.file_id
                    )
        return random.choice(self._file_ids) if self._file_ids else None


def build_router(
    settings: Settings,
    database: Database,
    api: SakuraAppsScriptApi,
    access: AccessController,
) -> Router:
    router = Router(name="sakuraemail")
    sticker_responder = StickerPackResponder(AINO_STICKER_SET_NAME)

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext, bot: Bot) -> None:
        await _remove_action_prompt(bot, state)
        await state.clear()
        user = _require_user(message.from_user)
        is_creator = user.id == settings.owner_id
        await _save_user(database, user, is_creator)

        greeting = (
            f"{SAKURA_ANIMATED} <b>Добро пожаловать, {escape(user.first_name)}!</b>\n\n"
            "Я помогу получить лицензионный ключ для макроса, заполнить рабочий "
            "отчёт и напомню о важных делах."
        )
        if is_creator:
            greeting += "\n\nДля вас доступен раздел управления."

        await message.answer(
            greeting,
            reply_markup=main_keyboard(is_creator),
        )

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext, bot: Bot) -> None:
        await _remove_action_prompt(bot, state)
        await state.clear()
        user = _require_user(message.from_user)
        await message.answer(
            "Действие отменено.",
            reply_markup=main_keyboard(user.id == settings.owner_id),
        )

    @router.callback_query(F.data == "cancel_action")
    async def cancel_action(callback: CallbackQuery, state: FSMContext) -> None:
        await _cancel_current_action(callback, state)

    @router.message(StateFilter(None), OwnerCustomEmojiFilter(settings.owner_id))
    async def show_custom_emoji_ids(
        message: Message,
        custom_emoji_ids: tuple[str, ...],
    ) -> None:
        lines = [
            f"{MINIMAL_FLOWER} <b>ID пользовательского эмодзи</b>",
            "",
            *(f"<code>{escape(value)}</code>" for value in custom_emoji_ids),
            "",
            "Пришлите нужный ID техническому специалисту и укажите, где использовать эмодзи.",
        ]
        await message.answer("\n".join(lines))

    @router.message(StateFilter(None), F.sticker)
    async def answer_with_random_sticker(message: Message, bot: Bot) -> None:
        try:
            sticker_file_id = await sticker_responder.choose(bot)
            if sticker_file_id:
                await message.answer_sticker(sticker=sticker_file_id)
            else:
                await message.answer(f"В наборе пока не нашлось доступных стикеров {SAKURA_1}")
        except TelegramAPIError:
            await message.answer(
                f"Сейчас не получилось выбрать стикер из набора. Попробуйте ещё раз чуть позже {SAKURA_1}"
            )

    @router.callback_query(F.data == "new_key_request")
    async def begin_application(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await callback.answer()
        await _remove_action_prompt(bot, state)
        await state.clear()
        await state.set_state(ApplicationStates.waiting_for_reason)
        if callback.message is not None:
            prompt = await callback.message.answer(
                APPLICATION_PROMPT,
                reply_markup=cancel_action_keyboard(),
            )
            await _remember_action_prompt(state, prompt)

    @router.message(ApplicationStates.waiting_for_reason)
    async def receive_application(message: Message, state: FSMContext, bot: Bot) -> None:
        user = _require_user(message.from_user)
        reason = (message.text or "").strip()
        if not reason:
            await message.answer("Пожалуйста, отправьте запрос одним текстовым сообщением.")
            return
        if len(reason) > 2000:
            await message.answer("Сообщение слишком длинное. Сократите его до 2000 символов.")
            return

        is_creator = user.id == settings.owner_id
        await _save_user(database, user, is_creator)
        request_id = await database.create_support_request(user.id, user.username, reason)
        await _remove_action_prompt(bot, state)
        await state.clear()

        await message.answer(
            f"{PINK_STARS} Заявка отправлена создателю.",
            reply_markup=main_keyboard(is_creator),
        )

        username = f"@{escape(user.username)}" if user.username else "тег отсутствует"
        owner_text = (
            f"💌 <b>Новая заявка №{request_id}</b>\n"
            f"Пользователь: {username}\n"
            f"Telegram ID: <code>{user.id}</code>\n\n"
            f"Запрос:\n{escape(reason)}"
        )
        try:
            await bot.send_message(
                settings.owner_id,
                owner_text,
                reply_markup=close_request_keyboard(request_id),
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            # The request remains available in the creator panel.
            pass

    @router.message(
        StateFilter(None),
        F.text.in_({KEYS_TEXT, MY_KEYS_TEXT, LEGACY_REQUEST_KEY_TEXT}),
    )
    async def request_key(message: Message) -> None:
        user = _require_user(message.from_user)
        is_creator = user.id == settings.owner_id
        await _save_user(database, user, is_creator)

        await message.answer(
            f"{SAKURA_2} Ищу ваши актуальные ключи…",
            reply_markup=main_keyboard(is_creator),
        )
        try:
            keys = await api.get_active_keys(user.id)
        except SakuraApiError:
            await message.answer(
                "Не удалось получить данные о ключах. Попробуйте немного позже.",
                reply_markup=application_keyboard(),
            )
            return

        if not keys:
            await message.answer(
                "Активных ключей нет",
                reply_markup=application_keyboard(),
            )
            return

        lines = [f"{SAKURA_3} <b>Ваши активные ключи</b>"]
        has_used_key = False
        for index, license_key in enumerate(keys, start=1):
            lines.append("")
            if len(keys) > 1:
                lines.append(f"<b>Ключ {index}</b>")
            lines.append(f"<blockquote><code>{escape(license_key.key)}</code></blockquote>")
            lines.append(
                f"Вид подписки: {escape(display_license_type(license_key.license_type))}"
            )
            lines.append(f"Статус: {escape(license_key.status)}")
            if license_key.expires_at:
                lines.append(f"Действует до: {escape(format_datetime(license_key.expires_at))}")
            if license_key.status_code == "used":
                has_used_key = True

        if has_used_key:
            lines.extend(
                [
                    "",
                    "Ключ уже был использован. Для запроса нового оставьте заявку",
                ]
            )

        await message.answer(
            "\n".join(lines),
            reply_markup=application_keyboard(),
        )

    @router.message(StateFilter(None), F.text == THANKS_TEXT)
    async def thanks(message: Message, bot: Bot) -> None:
        user = _require_user(message.from_user)
        is_creator = user.id == settings.owner_id
        await _save_user(database, user, is_creator)

        if is_creator:
            stats = await database.get_thanks_stats()
            await message.answer(
                _format_thanks_stats(stats),
                reply_markup=thanks_settings_keyboard(stats.notifications_enabled),
            )
            return

        gratitude_id = await database.record_thanks(user.id, user.username)
        if gratitude_id is None:
            await message.answer(
                "Спасибо уже отправлено 💗 Повторить можно один раз через сутки."
            )
            return

        await message.answer(
            "💗 <b>Спасибо! Я передала вашу благодарность создателю</b> (˶ᵔ ᵕ ᵔ˶)♡\n\n"
            f"Вы можете добавить комментарий к своему «Спасибо!». Добавить? {SAKURA_1}",
            reply_markup=add_thanks_comment_keyboard(gratitude_id),
        )
        if await database.thanks_notifications_enabled():
            username = f"@{escape(user.username)}" if user.username else escape(user.full_name)
            try:
                await bot.send_message(
                    settings.owner_id,
                    f"{username} сказал(а) спасибо! (づ｡◕‿‿◕｡)づ ♡",
                )
            except TelegramAPIError:
                pass

    @router.callback_query(F.data.startswith("thanks_comment:"))
    async def begin_thanks_comment(
        callback: CallbackQuery,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        user = callback.from_user
        try:
            gratitude_id = int((callback.data or "").split(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer("Некорректная благодарность", show_alert=True)
            return

        if not await database.can_comment_on_thanks(gratitude_id, user.id):
            await callback.answer("Комментарий уже добавлен или благодарность не найдена")
            if callback.message is not None:
                try:
                    await callback.message.edit_reply_markup(reply_markup=None)
                except TelegramBadRequest:
                    pass
            return

        await _remove_action_prompt(bot, state)
        await state.clear()
        await state.set_state(ThanksCommentStates.waiting_for_comment)
        await state.update_data(gratitude_id=gratitude_id)
        await callback.answer()

        if callback.message is not None:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
            prompt = await callback.message.answer(
                f"{SAKURA_4} <b>Добавьте комментарий к своему «Спасибо!»</b>\n\n"
                "Напишите его одним сообщением — я бережно передам создателю "
                "(｡•̀ᴗ-)✧",
                reply_markup=cancel_action_keyboard(),
            )
            await _remember_action_prompt(state, prompt)

    @router.message(ThanksCommentStates.waiting_for_comment)
    async def receive_thanks_comment(message: Message, state: FSMContext, bot: Bot) -> None:
        user = _require_user(message.from_user)
        comment = (message.text or "").strip()
        if not comment:
            await message.answer(f"Напишите комментарий одним текстовым сообщением {SAKURA_1}")
            return
        if len(comment) > 1500:
            await message.answer("Комментарий слишком длинный. Сократите его до 1500 символов.")
            return

        data = await state.get_data()
        gratitude_id = int(data.get("gratitude_id", 0))
        if not gratitude_id:
            await state.clear()
            await message.answer("Не смогла найти благодарность. Нажмите «Спасибо!» ещё раз.")
            return

        added = await database.add_thanks_comment(gratitude_id, user.id, comment)
        if not added:
            await _remove_action_prompt(bot, state)
            await state.clear()
            await message.answer("К этой благодарности комментарий уже добавлен 💗")
            return

        username = f"@{escape(user.username)}" if user.username else escape(user.full_name)
        owner_text = (
            f"💗 <b>Комментарий {username} к «Спасибо!»:</b>\n\n"
            f"<blockquote>{escape(comment)}</blockquote>\n"
            "Спасибо за тёплые слова! (˶˃ ᵕ ˂˶)♡"
        )
        try:
            await bot.send_message(settings.owner_id, owner_text)
        except TelegramAPIError:
            await database.delete_thanks_comment(gratitude_id, user.id)
            await message.answer(
                "Не получилось передать комментарий. Попробуйте отправить его ещё раз чуть позже."
            )
            return

        await _remove_action_prompt(bot, state)
        await state.clear()
        await message.answer(
            "💌 Комментарий передан создателю. Спасибо за тёплые слова! (◕‿◕)♡",
            reply_markup=main_keyboard(user.id == settings.owner_id),
        )

    @router.message(StateFilter(None), F.text == MANAGEMENT_TEXT)
    async def management(message: Message) -> None:
        user = _require_user(message.from_user)
        if user.id != settings.owner_id:
            return
        await message.answer(
            f"{SAKURA_ANIMATED} <b>Управление SakuraEmail</b>",
            reply_markup=management_keyboard(),
        )

    @router.callback_query(F.data == "admin_stats")
    async def admin_stats(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        users_count, pending_count = await database.get_stats()
        await callback.answer()
        if callback.message is not None:
            await callback.message.answer(
                "📊 <b>Статистика</b>\n"
                f"Пользователей: {users_count}\n"
                f"Открытых заявок: {pending_count}"
            )

    @router.callback_query(F.data == "admin_points_preview")
    async def admin_points_preview(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer("Готовлю предварительный отчёт…")
        if callback.message is None:
            return

        period = points_period_for_day(datetime.now(MOSCOW_TIMEZONE).date())
        try:
            report = await api.get_points_report(
                period.start.isoformat(),
                period.end.isoformat(),
            )
        except SakuraApiError:
            await callback.message.answer(
                "Сейчас не удалось подготовить отчёт по баллам. Попробуйте немного позже."
            )
            return

        await callback.message.answer_document(
            BufferedInputFile(build_points_workbook(report.rows), filename=period.filename),
            caption=(
                f"{PINK_STARS} <b>Предварительные баллы</b>\n"
                f"Период: <code>{format_period(period)}</code>"
            ),
        )

    @router.callback_query(F.data == "admin_refresh_access")
    async def admin_refresh_access(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return

        await callback.answer("Обновляю список доступа…")
        summary = await access.refresh_now()
        if callback.message is None:
            return
        if summary is None:
            await callback.message.answer(
                "Сейчас не удалось обновить список доступа. Попробуйте немного позже."
            )
            return

        await callback.message.answer(
            f"{PINK_STARS} <b>Список доступа обновлён</b>\n\n"
            f"Всего пользователей бота: {summary.total}\n"
            f"Допущено: {summary.allowed}\n"
            f"Заблокировано: {summary.blocked}\n"
            f"Не найдено: {summary.missing}"
        )
        if summary.missing_users:
            lines = []
            for missing_user in summary.missing_users:
                username = (
                    f"@{escape(missing_user.username)}"
                    if missing_user.username
                    else "тег отсутствует"
                )
                full_name = escape(missing_user.full_name or "имя отсутствует")
                lines.append(
                    f"{DECORATION_DOT} {username} — {full_name} — "
                    f"<code>{missing_user.telegram_id}</code>"
                )
            for text in _chunk_access_lines(lines):
                await callback.message.answer(text)

    @router.callback_query(F.data == "admin_migrate_user_id")
    async def begin_user_id_migration(
        callback: CallbackQuery,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        await _remove_action_prompt(bot, state)
        await state.clear()
        await state.set_state(UserIdMigrationStates.waiting_for_ids)
        if callback.message is not None:
            prompt = await callback.message.answer(
                f"{SAKURA_1} <b>Перенос Telegram ID</b>\n\n"
                "Сначала укажите новый ID сотрудника в рабочем списке. Затем "
                "отправьте сюда старый и новый ID через пробел:\n"
                "<code>111111111 222222222</code>\n\n"
                "График, настройки уведомлений, заявки и благодарности будут "
                "перенесены на новую учётную запись.",
                reply_markup=cancel_action_keyboard(),
            )
            await _remember_action_prompt(state, prompt)

    @router.message(UserIdMigrationStates.waiting_for_ids)
    async def migrate_user_id(
        message: Message,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        user = _require_user(message.from_user)
        if user.id != settings.owner_id:
            return

        ids = re.findall(r"\d+", message.text or "")
        if len(ids) != 2:
            await message.answer(
                "Отправьте ровно два ID: сначала старый, затем новый.\n"
                "Например: <code>111111111 222222222</code>",
                reply_markup=cancel_action_keyboard(),
            )
            return
        old_id, new_id = (int(value) for value in ids)
        if old_id <= 0 or new_id <= 0 or old_id == new_id:
            await message.answer(
                "ID должны быть положительными и отличаться друг от друга. "
                "Попробуйте ещё раз.",
                reply_markup=cancel_action_keyboard(),
            )
            return
        if not await database.user_exists(old_id):
            await message.answer(
                f"Старая запись <code>{old_id}</code> в боте не найдена. "
                "Проверьте ID и попробуйте ещё раз.",
                reply_markup=cancel_action_keyboard(),
            )
            return

        summary = await access.refresh_now()
        if summary is None:
            await message.answer(
                "Сейчас не удалось проверить новый ID. Попробуйте немного позже.",
                reply_markup=cancel_action_keyboard(),
            )
            return
        if await database.get_access_status(new_id) != "allowed":
            await message.answer(
                f"Новый ID <code>{new_id}</code> пока не допущен. Проверьте, "
                "что он указан у сотрудника и не заблокирован, затем повторите.",
                reply_markup=cancel_action_keyboard(),
            )
            return

        if not await database.migrate_user_id(old_id, new_id):
            await message.answer(
                "Не удалось найти старую запись. Проверьте ID и повторите.",
                reply_markup=cancel_action_keyboard(),
            )
            return

        await _remove_action_prompt(bot, state)
        await state.clear()
        await message.answer(
            f"{PINK_STARS} <b>Telegram ID перенесён</b>\n\n"
            f"Старый: <code>{old_id}</code>\n"
            f"Новый: <code>{new_id}</code>\n\n"
            "Старая запись больше не будет появляться в списке «Не найдено». "
            "Попросите сотрудника открыть бота и отправить /start.",
            reply_markup=main_keyboard(True),
        )

    @router.callback_query(F.data == "admin_requests")
    async def admin_requests(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        if callback.message is None:
            return

        requests = await database.list_pending_requests()
        if not requests:
            await callback.message.answer("Открытых заявок нет.")
            return

        await callback.message.answer(f"💌 Открытых заявок: {len(requests)}")
        for request in requests:
            await callback.message.answer(
                _format_request(request),
                reply_markup=close_request_keyboard(request.request_id),
            )

    @router.callback_query(F.data == "admin_special_days")
    async def admin_special_days(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        if callback.message is None:
            return
        days = await database.list_special_days()
        await callback.message.answer(
            format_special_days(days),
            reply_markup=special_days_keyboard(),
        )

    @router.callback_query(F.data == "admin_special_days_edit")
    async def edit_special_days(
        callback: CallbackQuery,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        await _remove_action_prompt(bot, state)
        await state.clear()
        await state.set_state(SpecialDayStates.waiting_for_changes)
        if callback.message is None:
            return
        prompt = await callback.message.answer(
            f"{SAKURA_1} <b>Изменение особенных дней</b>\n\n"
            "Отправьте даты одним сообщением, каждую с новой строки:\n"
            "<code>01.01.2027 праздник\n"
            "31.12.2026 сокращенный\n"
            "07.11.2026 рабочий</code>\n\n"
            "Чтобы убрать дату из календаря:\n"
            "<code>01.01.2027 удалить</code>\n\n"
            "Повторное добавление даты заменит её прежний тип.",
            reply_markup=cancel_action_keyboard(),
        )
        await _remember_action_prompt(state, prompt)

    @router.message(SpecialDayStates.waiting_for_changes)
    async def save_special_days(
        message: Message,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        user = _require_user(message.from_user)
        if user.id != settings.owner_id:
            return
        try:
            changes = parse_special_day_changes(message.text or "")
        except ValueError as exc:
            await message.answer(
                f"Не получилось прочитать изменения. {escape(str(exc))}\n\n"
                "Попробуйте ещё раз или нажмите «Отменить».",
                reply_markup=cancel_action_keyboard(),
            )
            return

        await database.apply_special_day_changes(changes)
        await _remove_action_prompt(bot, state)
        await state.clear()
        days = await database.list_special_days()
        await message.answer(
            f"{PINK_STARS} Календарь обновлён.\n\n{format_special_days(days)}",
            reply_markup=special_days_keyboard(),
        )

    @router.callback_query(F.data == "admin_message_user")
    async def begin_creator_message(
        callback: CallbackQuery,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        await _remove_action_prompt(bot, state)
        await state.clear()
        await state.set_state(CreatorMessageStates.waiting_for_username)
        if callback.message is not None:
            prompt = await callback.message.answer(
                "Отправьте тег пользователя, которому хотите написать. Например: @username\n\n"
                "Пользователь должен хотя бы один раз запустить бота через /start.",
                reply_markup=cancel_action_keyboard(),
            )
            await _remember_action_prompt(state, prompt)

    @router.message(CreatorMessageStates.waiting_for_username)
    async def receive_creator_message_username(
        message: Message,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        user = _require_user(message.from_user)
        if user.id != settings.owner_id:
            return

        raw_username = (message.text or "").strip()
        username = raw_username[1:] if raw_username.startswith("@") else raw_username
        if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
            await message.answer("Отправьте корректный Telegram-тег в формате @username.")
            return

        recipient = await database.find_user_by_username(username)
        if recipient is None:
            await message.answer(
                "Этот пользователь ещё не запускал бота."
            )
            return
        if await access.check(recipient.telegram_id) != AccessDecision.ALLOWED:
            await message.answer("У этого пользователя сейчас нет доступа к боту.")
            return

        await _remove_action_prompt(bot, state)
        await state.set_state(CreatorMessageStates.waiting_for_message)
        await state.update_data(
            recipient_id=recipient.telegram_id,
            recipient_username=recipient.username or username,
        )
        prompt = await message.answer(
            f"Теперь отправьте сообщение для @{escape(recipient.username or username)}.\n\n"
            "Можно использовать оформление Telegram и кастомные эмодзи.",
            reply_markup=cancel_action_keyboard(),
        )
        await _remember_action_prompt(state, prompt)

    @router.message(CreatorMessageStates.waiting_for_message)
    async def send_creator_message(message: Message, state: FSMContext, bot: Bot) -> None:
        user = _require_user(message.from_user)
        if user.id != settings.owner_id:
            return

        plain_text = (message.text or "").strip()
        if not plain_text:
            await message.answer("Отправьте сообщение текстом.")
            return
        html_text = _message_html(message)
        if len(html_text) > 3500:
            await message.answer("Сообщение слишком длинное. Сократите его до 3500 символов.")
            return

        data = await state.get_data()
        recipient_id = int(data.get("recipient_id", 0))
        recipient_username = str(data.get("recipient_username", "пользователь"))
        if not recipient_id:
            await state.clear()
            await message.answer("Получатель потерян. Начните отправку сообщения заново.")
            return

        try:
            await bot.send_message(
                recipient_id,
                "💌 <b>Сообщение от создателя</b>\n\n" + html_text,
            )
        except (TelegramForbiddenError, TelegramBadRequest):
            await message.answer(
                "Не удалось отправить сообщение. Возможно, пользователь заблокировал бота."
            )
            return

        await _remove_action_prompt(bot, state)
        await state.clear()
        await message.answer(
            f"Сообщение для @{escape(recipient_username)} отправлено.",
            reply_markup=main_keyboard(True),
        )

    @router.callback_query(F.data == "admin_broadcast")
    async def begin_broadcast(
        callback: CallbackQuery,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await callback.answer()
        await _remove_action_prompt(bot, state)
        await state.clear()
        await state.set_state(CreatorBroadcastStates.waiting_for_message)
        if callback.message is not None:
            prompt = await callback.message.answer(
                "📣 <b>Сообщение для всех</b>\n\n"
                "Отправьте текст одним сообщением. Можно использовать оформление "
                "Telegram: <b>жирный</b>, <i>курсив</i>, <u>подчёркивание</u>, "
                "<s>зачёркивание</s>, <code>моноширинный шрифт</code>, ссылки, "
                "спойлеры, цитаты и кастомные эмодзи. Перед рассылкой я покажу "
                "превью, где при желании можно добавить один стикер AinoStick.",
                reply_markup=cancel_action_keyboard(),
            )
            await _remember_action_prompt(state, prompt)

    @router.message(CreatorBroadcastStates.waiting_for_message)
    async def preview_broadcast(
        message: Message,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        if message.from_user is None or message.from_user.id != settings.owner_id:
            return
        text = (message.text or "").strip()
        if not text:
            await message.answer("Отправьте сообщение текстом.")
            return
        html_text = _message_html(message)
        if len(html_text) > 3500:
            await message.answer("Сообщение слишком длинное. Сократите его до 3500 символов.")
            return

        await _remove_action_prompt(bot, state)
        await state.set_state(CreatorBroadcastStates.confirming)
        await state.update_data(
            broadcast_html=html_text,
            broadcast_sticker_file_id="",
        )
        preview = await message.answer(
            "📣 <b>Так сообщение увидят пользователи:</b>\n\n" + html_text,
            reply_markup=broadcast_confirmation_keyboard(),
        )
        await _remember_action_prompt(state, preview)

    @router.callback_query(F.data == "admin_broadcast_add_sticker")
    async def begin_broadcast_sticker(
        callback: CallbackQuery,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        if await state.get_state() != CreatorBroadcastStates.confirming.state:
            await callback.answer("Эта рассылка уже завершена", show_alert=True)
            return
        await callback.answer()
        await _remove_action_prompt(bot, state)
        await state.set_state(CreatorBroadcastStates.waiting_for_sticker)
        if callback.message is not None:
            prompt = await callback.message.answer(
                "Пришлите один стикер из набора <b>AinoStick</b>. "
                "Он будет отправлен пользователям сразу после текста.",
                reply_markup=cancel_action_keyboard(),
            )
            await _remember_action_prompt(state, prompt)

    @router.message(CreatorBroadcastStates.waiting_for_sticker, F.sticker)
    async def receive_broadcast_sticker(
        message: Message,
        state: FSMContext,
        bot: Bot,
    ) -> None:
        if message.from_user is None or message.from_user.id != settings.owner_id:
            return
        sticker = message.sticker
        if sticker is None or sticker.set_name != AINO_STICKER_SET_NAME:
            await message.answer(
                "Подойдёт только стикер из набора AinoStick. Пришлите другой "
                "или нажмите «Отменить»."
            )
            return

        await _remove_action_prompt(bot, state)
        await state.set_state(CreatorBroadcastStates.confirming)
        await state.update_data(broadcast_sticker_file_id=sticker.file_id)
        await message.answer_sticker(sticker=sticker.file_id)
        confirmation = await message.answer(
            "Стикер добавлен к рассылке. Он будет отправлен после сообщения.",
            reply_markup=broadcast_confirmation_keyboard(has_sticker=True),
        )
        await _remember_action_prompt(state, confirmation)

    @router.message(CreatorBroadcastStates.waiting_for_sticker)
    async def reject_non_sticker_for_broadcast(message: Message) -> None:
        if message.from_user is None or message.from_user.id != settings.owner_id:
            return
        await message.answer(
            "Пришлите именно стикер из набора AinoStick или нажмите «Отменить»."
        )

    @router.callback_query(F.data == "admin_broadcast_cancel")
    async def cancel_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        await state.clear()
        await callback.answer("Рассылка отменена")
        if callback.message is not None:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass
            await callback.message.answer(
                "Рассылка отменена.",
                reply_markup=main_keyboard(True),
            )

    @router.callback_query(F.data == "admin_broadcast_send")
    async def send_broadcast(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return
        if await state.get_state() != CreatorBroadcastStates.confirming.state:
            await callback.answer("Эта рассылка уже завершена", show_alert=True)
            return

        data = await state.get_data()
        html_text = str(data.get("broadcast_html", "")).strip()
        sticker_file_id = str(data.get("broadcast_sticker_file_id", "")).strip()
        if not html_text:
            await state.clear()
            await callback.answer("Текст сообщения потерялся", show_alert=True)
            return

        await state.set_state(CreatorBroadcastStates.sending)
        await callback.answer("Начинаю рассылку…")
        if callback.message is not None:
            try:
                await callback.message.edit_reply_markup(reply_markup=None)
            except TelegramBadRequest:
                pass

        recipients = await access.allowed_registered_users()
        if recipients is None:
            await state.clear()
            if callback.message is not None:
                await callback.message.answer(
                    "Сейчас не удалось проверить получателей. Рассылка не началась."
                )
            return

        delivered = 0
        unavailable = 0
        sticker_unavailable = 0
        outgoing_text = "📣 <b>Общее сообщение</b>\n\n" + html_text
        for recipient in recipients:
            if not await _send_message_with_retry(
                bot,
                recipient.telegram_id,
                outgoing_text,
            ):
                unavailable += 1
                await asyncio.sleep(0.05)
                continue

            delivered += 1
            if sticker_file_id and not await _send_sticker_with_retry(
                bot,
                recipient.telegram_id,
                sticker_file_id,
            ):
                sticker_unavailable += 1
            await asyncio.sleep(0.05)

        await state.clear()
        if callback.message is not None:
            summary = (
                f"{PINK_STARS} <b>Рассылка завершена</b>\n"
                f"Доставлено: {delivered}\n"
                f"Недоступны в Telegram: {unavailable}"
            )
            if sticker_file_id:
                summary += f"\nСтикер не доставлен: {sticker_unavailable}"
            await callback.message.answer(
                summary,
                reply_markup=main_keyboard(True),
            )

    @router.callback_query(F.data.startswith("close_request:"))
    async def close_request(callback: CallbackQuery, bot: Bot) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return

        try:
            request_id = int((callback.data or "").split(":", 1)[1])
        except (IndexError, ValueError):
            await callback.answer("Некорректная заявка", show_alert=True)
            return

        telegram_id = await database.close_support_request(request_id)
        if telegram_id is None:
            await callback.answer("Заявка уже выполнена")
            if callback.message is not None:
                await callback.message.edit_reply_markup(reply_markup=None)
            return

        user_notified = await access.check(telegram_id) == AccessDecision.ALLOWED
        if user_notified:
            try:
                await bot.send_message(
                    telegram_id,
                    f"{SAKURA_2} Ваша заявка №{request_id} закрыта. Если понадобится помощь, "
                    "вы всегда можете оставить новую.",
                )
            except (TelegramForbiddenError, TelegramBadRequest):
                user_notified = False

        await callback.answer(
            "Заявка выполнена"
            if user_notified
            else "Заявка выполнена, но пользователь недоступен",
            show_alert=not user_notified,
        )
        if callback.message is not None:
            await callback.message.edit_reply_markup(reply_markup=None)

    @router.callback_query(F.data == "toggle_thanks_notifications")
    async def toggle_thanks_notifications(callback: CallbackQuery) -> None:
        if not _is_owner(callback.from_user, settings):
            await callback.answer("Недостаточно прав", show_alert=True)
            return

        currently_enabled = await database.thanks_notifications_enabled()
        await database.set_thanks_notifications_enabled(not currently_enabled)
        stats = await database.get_thanks_stats()
        await callback.answer(
            "Уведомления включены" if stats.notifications_enabled else "Уведомления отключены"
        )
        if callback.message is not None:
            await callback.message.edit_text(
                _format_thanks_stats(stats),
                reply_markup=thanks_settings_keyboard(stats.notifications_enabled),
            )

    return router


async def _save_user(database: Database, user: User, is_creator: bool) -> None:
    await database.upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name or "",
        last_name=user.last_name or "",
        role="creator" if is_creator else "user",
    )


async def _remember_action_prompt(state: FSMContext, message: Message) -> None:
    await state.update_data(
        action_prompt_chat_id=message.chat.id,
        action_prompt_message_id=message.message_id,
    )


async def _cancel_current_action(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    tracked_chat_id = data.get("action_prompt_chat_id")
    tracked_message_id = data.get("action_prompt_message_id")
    if (
        callback.message is not None
        and tracked_chat_id is not None
        and tracked_message_id is not None
        and (
            callback.message.chat.id != int(tracked_chat_id)
            or callback.message.message_id != int(tracked_message_id)
        )
    ):
        await callback.answer("Это действие уже завершено")
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        return

    current_state = await state.get_state()
    await state.clear()
    await callback.answer("Действие отменено" if current_state else "Действие уже завершено")
    if callback.message is not None:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


async def _remove_action_prompt(bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    chat_id = data.get("action_prompt_chat_id")
    message_id = data.get("action_prompt_message_id")
    if chat_id is None or message_id is None:
        return
    try:
        await bot.edit_message_reply_markup(
            chat_id=int(chat_id),
            message_id=int(message_id),
            reply_markup=None,
        )
    except TelegramBadRequest:
        pass


def _require_user(user: User | None) -> User:
    if user is None:
        raise RuntimeError("Telegram update does not contain a user")
    return user


def _is_owner(user: User, settings: Settings) -> bool:
    return user.id == settings.owner_id


def _format_request(request: SupportRequest) -> str:
    username = f"@{escape(request.username)}" if request.username else "тег отсутствует"
    return (
        f"💌 <b>Заявка №{request.request_id}</b>\n"
        f"Пользователь: {username}\n"
        f"Telegram ID: <code>{request.telegram_id}</code>\n"
        f"Создана: {escape(format_datetime(request.created_at))}\n\n"
        f"{escape(request.reason)}"
    )


def _chunk_access_lines(lines: list[str], limit: int = 3500) -> list[str]:
    title = "⚠️ <b>Не найдены данные сотрудников:</b>"
    chunks: list[str] = []
    current = title
    for line in lines:
        candidate = current + "\n" + line
        if len(candidate) > limit and current != title:
            chunks.append(current)
            current = title + "\n" + line
        else:
            current = candidate
    if current != title:
        chunks.append(current)
    return chunks


def _custom_emoji_ids(message: Message) -> tuple[str, ...]:
    result: list[str] = []
    entities = tuple(message.entities or ()) + tuple(message.caption_entities or ())
    for entity in entities:
        if entity.type != MessageEntityType.CUSTOM_EMOJI or not entity.custom_emoji_id:
            continue
        if entity.custom_emoji_id not in result:
            result.append(entity.custom_emoji_id)
    return tuple(result)


def _message_html(message: Message) -> str:
    return message.html_text.strip()


def _format_thanks_stats(stats: ThanksStats) -> str:
    notifications = "включены" if stats.notifications_enabled else "отключены"
    return (
        "💗 <b>Спасибо</b>\n"
        f"За сегодня: {stats.today}\n"
        f"За всё время: {stats.total}\n"
        f"Уведомления: {notifications}"
    )


async def _send_message_with_retry(
    bot: Bot,
    telegram_id: int,
    text: str,
) -> bool:
    try:
        await bot.send_message(telegram_id, text)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await bot.send_message(telegram_id, text)
        except TelegramAPIError:
            return False
    except TelegramAPIError:
        return False
    return True


async def _send_sticker_with_retry(
    bot: Bot,
    telegram_id: int,
    sticker_file_id: str,
) -> bool:
    try:
        await bot.send_sticker(telegram_id, sticker_file_id)
    except TelegramRetryAfter as exc:
        await asyncio.sleep(exc.retry_after)
        try:
            await bot.send_sticker(telegram_id, sticker_file_id)
        except TelegramAPIError:
            return False
    except TelegramAPIError:
        return False
    return True
