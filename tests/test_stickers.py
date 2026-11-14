from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram import Bot
from aiogram.enums import ChatType, MessageEntityType
from aiogram.types import Chat, Message, MessageEntity

from bot.handlers import (
    StickerPackResponder,
    _custom_emoji_ids,
    _message_html,
    _send_sticker_with_retry,
)


class StickerTests(unittest.IsolatedAsyncioTestCase):
    async def test_sticker_pack_is_loaded_once_and_cached(self) -> None:
        bot = AsyncMock(spec=Bot)
        bot.get_sticker_set.return_value = SimpleNamespace(
            stickers=[
                SimpleNamespace(file_id="sticker-1"),
                SimpleNamespace(file_id="sticker-2"),
            ]
        )
        responder = StickerPackResponder("AinoStick")

        first = await responder.choose(bot)
        second = await responder.choose(bot)

        self.assertIn(first, {"sticker-1", "sticker-2"})
        self.assertIn(second, {"sticker-1", "sticker-2"})
        bot.get_sticker_set.assert_awaited_once_with(name="AinoStick")

    async def test_custom_emoji_ids_are_deduplicated(self) -> None:
        custom = SimpleNamespace(
            type=MessageEntityType.CUSTOM_EMOJI,
            custom_emoji_id="emoji-123",
        )
        regular = SimpleNamespace(
            type=MessageEntityType.BOLD,
            custom_emoji_id=None,
        )
        message = SimpleNamespace(
            entities=[custom, regular, custom],
            caption_entities=None,
        )

        self.assertEqual(_custom_emoji_ids(message), ("emoji-123",))

    async def test_custom_emoji_is_preserved_in_relay_html(self) -> None:
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=1, type=ChatType.PRIVATE),
            text="🌸",
            entities=[
                MessageEntity(
                    type=MessageEntityType.CUSTOM_EMOJI,
                    offset=0,
                    length=2,
                    custom_emoji_id="5321445337074780874",
                )
            ],
        )

        self.assertEqual(
            _message_html(message),
            '<tg-emoji emoji-id="5321445337074780874">🌸</tg-emoji>',
        )

    async def test_broadcast_sticker_is_sent_by_file_id(self) -> None:
        bot = AsyncMock(spec=Bot)

        delivered = await _send_sticker_with_retry(bot, 101, "sticker-file-id")

        self.assertTrue(delivered)
        bot.send_sticker.assert_awaited_once_with(101, "sticker-file-id")


if __name__ == "__main__":
    unittest.main()
