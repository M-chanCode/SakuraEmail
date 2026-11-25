from __future__ import annotations

import unittest

from bot.keyboards import (
    broadcast_confirmation_keyboard,
    main_keyboard,
    management_keyboard,
    work_settings_keyboard,
)


class KeyboardTests(unittest.TestCase):
    def test_cabinet_and_thanks_share_one_regular_user_menu_row(self) -> None:
        keyboard = main_keyboard(is_creator=False)
        self.assertEqual(
            [button.text for button in keyboard.keyboard[1]],
            ["Кабинет", "Спасибо!"],
        )

    def test_creator_keeps_settings_label(self) -> None:
        keyboard = main_keyboard(is_creator=True)
        self.assertEqual(
            [button.text for button in keyboard.keyboard[1]],
            ["Настройки", "Спасибо!"],
        )

    def test_work_settings_has_support_request_without_old_report_button(self) -> None:
        keyboard = work_settings_keyboard(notifications_enabled=True)
        buttons = [button for row in keyboard.inline_keyboard for button in row]
        self.assertIn("Оставить заявку", [button.text for button in buttons])
        self.assertIn("new_key_request", [button.callback_data for button in buttons])
        self.assertNotIn("Изменить старый отчёт", [button.text for button in buttons])

    def test_management_has_special_days_button(self) -> None:
        keyboard = management_keyboard()
        callbacks = [
            button.callback_data
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertIn("admin_special_days", callbacks)

    def test_management_can_migrate_telegram_id(self) -> None:
        callbacks = [
            button.callback_data
            for row in management_keyboard().inline_keyboard
            for button in row
        ]
        self.assertIn("admin_migrate_user_id", callbacks)

    def test_management_has_preliminary_points_report(self) -> None:
        callbacks = [
            button.callback_data
            for row in management_keyboard().inline_keyboard
            for button in row
        ]
        self.assertIn("admin_points_preview", callbacks)

    def test_management_has_employee_points_detail(self) -> None:
        callbacks = [
            button.callback_data
            for row in management_keyboard().inline_keyboard
            for button in row
        ]
        self.assertIn("admin_employee_points", callbacks)

    def test_broadcast_preview_can_add_or_replace_sticker(self) -> None:
        without_sticker = broadcast_confirmation_keyboard()
        with_sticker = broadcast_confirmation_keyboard(has_sticker=True)

        self.assertEqual(without_sticker.inline_keyboard[0][0].text, "Добавить стикер")
        self.assertEqual(with_sticker.inline_keyboard[0][0].text, "Заменить стикер")
        self.assertEqual(
            with_sticker.inline_keyboard[0][0].callback_data,
            "admin_broadcast_add_sticker",
        )


if __name__ == "__main__":
    unittest.main()
