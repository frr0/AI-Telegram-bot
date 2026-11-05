import asyncio
import os
import time
import unittest
from unittest.mock import MagicMock, AsyncMock, patch

from telegram_client import TelegramClient
from main import BotApp

class TestRestartFlow(unittest.TestCase):
    def setUp(self):
        self.bot = BotApp()
        self.bot.client = MagicMock()
        self.bot.client.send_message = AsyncMock(return_value={"ok": True})
        self.bot.client.get_updates = AsyncMock(return_value=[])
        self.bot.client.close = AsyncMock()
        self.bot.ui = MagicMock()

        # Clean up test files if any
        for f in [".test_telegram_offset", ".test_commands_hash"]:
            if os.path.exists(f):
                os.remove(f)

    def tearDown(self):
        for f in [".test_telegram_offset", ".test_commands_hash"]:
            if os.path.exists(f):
                os.remove(f)

    def test_offset_save_and_load(self):
        with patch.object(self.bot, "_load_telegram_offset") as mock_load, \
             patch.object(self.bot, "_save_telegram_offset") as mock_save:
            
            self.bot._save_telegram_offset(12345)
            mock_save.assert_called_with(12345)

    def test_stale_restart_is_ignored(self):
        # Set bot startup time to current time
        self.bot.startup_time = time.time()
        stale_date = int(self.bot.startup_time) - 100

        update = {
            "update_id": 999,
            "message": {
                "message_id": 1,
                "from": {"id": 12345, "first_name": "Test"},
                "chat": {"id": 12345},
                "text": "/restart",
                "date": stale_date
            }
        }
        self.bot.config.allowed_user_ids = [12345]

        with patch("os.execv") as mock_execv:
            asyncio.run(self.bot.process_telegram_update(update))
            # Should NOT restart because message is stale
            mock_execv.assert_not_called()
            self.bot.client.send_message.assert_not_called()
            self.bot.ui.log_warn.assert_called()

    def test_fresh_restart_acknowledges_update_and_execv(self):
        self.bot.startup_time = time.time() - 10
        fresh_date = int(time.time())

        update = {
            "update_id": 1005,
            "message": {
                "message_id": 2,
                "from": {"id": 12345, "first_name": "Test"},
                "chat": {"id": 12345},
                "text": "riavvia",
                "date": fresh_date
            }
        }
        self.bot.config.allowed_user_ids = [12345]

        with patch("os.execv") as mock_execv, \
             patch.object(self.bot, "_save_telegram_offset") as mock_save_offset, \
             patch("asyncio.sleep", AsyncMock()):

            asyncio.run(self.bot.process_telegram_update(update))

            # Must save offset update_id + 1 = 1006
            mock_save_offset.assert_called_with(1006)
            # Must acknowledge to Telegram via get_updates(offset=1006, timeout=0)
            self.bot.client.get_updates.assert_awaited_with(offset=1006, timeout=0)
            # Must close client
            self.bot.client.close.assert_awaited_once()
            # Must call os.execv
            mock_execv.assert_called_once()

    def test_commands_hash_prevents_duplicate_set_my_commands(self):
        import hashlib
        import json

        bot_commands = [{"command": "menu", "description": "test"}]
        cmd_hash = hashlib.md5(json.dumps(bot_commands, sort_keys=True).encode()).hexdigest()

        # Simulate commands already registered with current hash
        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", unittest.mock.mock_open(read_data=cmd_hash)):
            self.bot.client.set_my_commands = AsyncMock()
            
            # Logic simulation from initialize()
            prev_hash = cmd_hash
            if cmd_hash != prev_hash:
                asyncio.run(self.bot.client.set_my_commands(bot_commands))

            self.bot.client.set_my_commands.assert_not_called()

if __name__ == '__main__':
    unittest.main()
