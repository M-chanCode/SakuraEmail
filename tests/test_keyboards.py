from __future__ import annotations

import unittest

from bot.keyboards import main_keyboard, management_keyboard, work_settings_keyboard


class KeyboardTests(unittest.TestCase):
    def test_settings_and_thanks_share_one_main_menu_row(self) -> None:
        keyboard = main_keyboard(is_creator=False)
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


if __name__ == "__main__":
    unittest.main()
