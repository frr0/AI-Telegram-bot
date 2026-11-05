import unittest
from unittest.mock import MagicMock
from telegram_client import TelegramClient
from main import BotApp

class TestReplyKeyboardSubmenus(unittest.TestCase):
    def setUp(self):
        self.bot = BotApp()
        self.bot.client = TelegramClient(token="dummy_token")

    def test_main_keyboard_categories(self):
        kb = self.bot.get_reply_keyboard("main")
        flat_buttons = [b["text"] for row in kb["keyboard"] for b in row]
        self.assertIn("🛑 Stop", flat_buttons)
        self.assertIn("➕ Nuova Chat", flat_buttons)
        self.assertIn("🎵 Media", flat_buttons)
        self.assertIn("🖥️ Schermo", flat_buttons)
        self.assertIn("📊 Sistema", flat_buttons)
        self.assertIn("🎛️ Opzioni Agy", flat_buttons)
        self.assertIn("🖥️ Tmux Agy", flat_buttons)
        self.assertIn("📋 Menu", flat_buttons)

    def test_media_submenu(self):
        kb = self.bot.get_reply_keyboard("media")
        self.assertEqual(self.bot.current_reply_menu, "media")
        flat_buttons = [b["text"] for row in kb["keyboard"] for b in row]
        self.assertIn("⏮️ Prec", flat_buttons)
        self.assertIn("⏯️ Play/Pausa", flat_buttons)
        self.assertIn("⏭️ Succ", flat_buttons)
        self.assertIn("🔉 Vol -", flat_buttons)
        self.assertIn("🔊 Vol +", flat_buttons)
        self.assertIn("🔙 Menu Principale", flat_buttons)

    def test_screen_submenu(self):
        kb = self.bot.get_reply_keyboard("screen")
        self.assertEqual(self.bot.current_reply_menu, "screen")
        flat_buttons = [b["text"] for row in kb["keyboard"] for b in row]
        self.assertIn("💡 Accendi Schermo", flat_buttons)
        self.assertIn("💤 Spegni Schermo", flat_buttons)
        self.assertIn("🔒 Blocca PC", flat_buttons)
        self.assertIn("🔓 Sblocca PC", flat_buttons)
        self.assertIn("🔙 Menu Principale", flat_buttons)

    def test_system_submenu(self):
        kb = self.bot.get_reply_keyboard("system")
        self.assertEqual(self.bot.current_reply_menu, "system")
        flat_buttons = [b["text"] for row in kb["keyboard"] for b in row]
        self.assertIn("📊 Hardware", flat_buttons)
        self.assertIn("⚡ Consumi", flat_buttons)
        self.assertIn("📜 Cronologia", flat_buttons)
        self.assertIn("📊 Stato", flat_buttons)
        self.assertIn("🔙 Menu Principale", flat_buttons)

    def test_agy_submenu(self):
        kb = self.bot.get_reply_keyboard("agy")
        self.assertEqual(self.bot.current_reply_menu, "agy")
        flat_buttons = [b["text"] for row in kb["keyboard"] for b in row]
        self.assertIn("📊 Quota Usage", flat_buttons)
        self.assertIn("💳 Crediti G1", flat_buttons)
        self.assertIn("🔌 MCP Servers", flat_buttons)
        self.assertIn("⚡ Skills", flat_buttons)
        self.assertIn("🤖 Agenti", flat_buttons)
        self.assertIn("🧠 Modello AI", flat_buttons)
        self.assertIn("🔙 Menu Principale", flat_buttons)

    def test_tmux_submenu(self):
        kb = self.bot.get_reply_keyboard("tmux")
        self.assertEqual(self.bot.current_reply_menu, "tmux")
        flat_buttons = [b["text"] for row in kb["keyboard"] for b in row]
        self.assertIn("🖥️ Sessioni Agy", flat_buttons)
        self.assertIn("💻 Tutte le Sessioni", flat_buttons)
        self.assertIn("📰 Teamwork News", flat_buttons)
        self.assertIn("🔙 Menu Principale", flat_buttons)

    def test_back_to_main(self):
        self.bot.get_reply_keyboard("media")
        self.assertEqual(self.bot.current_reply_menu, "media")
        kb = self.bot.get_reply_keyboard("main")
        self.assertEqual(self.bot.current_reply_menu, "main")
        flat_buttons = [b["text"] for row in kb["keyboard"] for b in row]
        self.assertIn("🎵 Media", flat_buttons)
        self.assertNotIn("⏮️ Prec", flat_buttons)

    def test_attached_tmux_keyboard(self):
        self.bot.active_tmux_target = "chat_20261105"
        kb = self.bot.get_reply_keyboard()
        flat_buttons = [b["text"] for row in kb["keyboard"] for b in row]
        self.assertIn("🔙 Sgancia (Torna al Bot)", flat_buttons)
        self.assertIn("🛑 Ctrl+C", flat_buttons)
        self.assertIn("👁️ Schermo", flat_buttons)

    def test_detach_command_handling_when_no_active_session(self):
        import asyncio
        self.bot.active_tmux_target = None
        self.bot.config.allowed_user_ids = [12345]
        self.bot.client.send_message = unittest.mock.AsyncMock()

        update = {
            "update_id": 9999,
            "message": {
                "message_id": 1,
                "from": {"id": 12345, "first_name": "Test"},
                "chat": {"id": 12345},
                "text": "🔙 Sgancia (Torna al Bot)",
                "date": 1234567890
            }
        }
        asyncio.run(self.bot.process_telegram_update(update))
        self.assertEqual(self.bot.current_reply_menu, "main")
        self.bot.client.send_message.assert_awaited()

    def test_inline_menu_synchronizes_reply_keyboard_state(self):
        import asyncio
        self.bot.config.allowed_user_ids = [12345]
        self.bot.client.edit_message_text = unittest.mock.AsyncMock()
        self.bot.client.answer_callback_query = unittest.mock.AsyncMock()
        self.bot.client.send_message = unittest.mock.AsyncMock()
        self.bot.tmux = unittest.mock.MagicMock()
        self.bot.tmux.format_sessions_card.return_value = "Sessions card"
        self.bot.tmux.discover_sessions.return_value = []

        # 1. Click menu:tmux -> current_reply_menu should become "tmux"
        self.bot.current_reply_menu = "main"
        update_tmux = {
            "update_id": 1001,
            "callback_query": {
                "id": "cq1",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 10},
                "data": "menu:tmux"
            }
        }
        asyncio.run(self.bot.process_telegram_update(update_tmux))
        self.assertEqual(self.bot.current_reply_menu, "tmux")

        # 2. Click menu:main -> current_reply_menu should become "main"
        update_main = {
            "update_id": 1002,
            "callback_query": {
                "id": "cq2",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 10},
                "data": "menu:main"
            }
        }
        asyncio.run(self.bot.process_telegram_update(update_main))
        self.assertEqual(self.bot.current_reply_menu, "main")

    def test_screen_keyboard_includes_main_menu_button(self):
        kb = self.bot._build_tmux_screen_keyboard("0:1.0")
        flat_callbacks = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("menu:tmux", flat_callbacks)
        self.assertIn("menu:main", flat_callbacks)

        flat_texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
        self.assertTrue(any("Menu Principale" in t for t in flat_texts))
        self.assertTrue(any("Lista Sessioni" in t for t in flat_texts))

    def test_attached_mode_escape_hatches(self):
        import asyncio
        self.bot.config.allowed_user_ids = [12345]
        self.bot.client.send_message = unittest.mock.AsyncMock()
        self.bot.tmux = unittest.mock.MagicMock()
        self.bot.tmux.send_input = unittest.mock.MagicMock()

        escape_words = ["/menu", "menu", "🔙 Menu Principale", "/start", "indietro"]
        for idx, word in enumerate(escape_words):
            self.bot.active_tmux_target = "0:1.0"
            self.bot.active_tmux_name = "worker"
            self.bot.current_reply_menu = "tmux"

            update = {
                "update_id": 2000 + idx,
                "message": {
                    "message_id": 20 + idx,
                    "from": {"id": 12345, "first_name": "User"},
                    "chat": {"id": 12345},
                    "text": word,
                    "date": 1234567890
                }
            }
            asyncio.run(self.bot.process_telegram_update(update))

            self.assertIsNone(self.bot.active_tmux_target, f"Failed to detach on '{word}'")
            self.assertEqual(self.bot.current_reply_menu, "main", f"Failed to set main menu on '{word}'")
            self.bot.tmux.send_input.assert_not_called()

    def test_orphaned_choices_guard(self):
        import asyncio
        self.bot.config.allowed_user_ids = [12345]
        self.bot.client.send_message = unittest.mock.AsyncMock()
        self.bot.handle_prompt = unittest.mock.AsyncMock()

        self.bot.active_tmux_target = None
        self.bot.current_reply_menu = "tmux"

        for choice in ["1", "2", "3", "y", "n"]:
            self.bot.current_reply_menu = "tmux"
            self.bot.handle_prompt.reset_mock()
            update = {
                "update_id": 3000,
                "message": {
                    "message_id": 30,
                    "from": {"id": 12345, "first_name": "User"},
                    "chat": {"id": 12345},
                    "text": choice,
                    "date": 1234567890
                }
            }
            asyncio.run(self.bot.process_telegram_update(update))
            self.assertEqual(self.bot.current_reply_menu, "main")
            self.bot.handle_prompt.assert_not_called()

    def test_screen_keyboard_layout_and_byte_limits(self):
        """Verify screen keyboard layout (5 rows) and Telegram 64-byte callback_data limit."""
        target = "chat_20261105_131500:0.0"
        kb = self.bot._build_tmux_screen_keyboard(target)
        rows = kb.get("inline_keyboard", [])
        self.assertEqual(len(rows), 5, "Screen keyboard must have exactly 5 rows")

        # Row 0: Refresh + Attach
        self.assertEqual([b["callback_data"] for b in rows[0]], [f"tmux:screen:{target}", f"tmux:attach:{target}"])
        # Row 1: Ctrl+C + Enter
        self.assertEqual([b["callback_data"] for b in rows[1]], [f"tmux:key:{target}:C-c", f"tmux:key:{target}:Enter"])
        # Row 2: Choices 1, 2, 3, y, n
        self.assertEqual([b["callback_data"] for b in rows[2]], [
            f"tmux:send:{target}:1", f"tmux:send:{target}:2", f"tmux:send:{target}:3",
            f"tmux:send:{target}:y", f"tmux:send:{target}:n"
        ])
        # Row 3: Close session + Sessions list
        self.assertEqual([b["callback_data"] for b in rows[3]], [f"tmux:kill_ask:{target}", "menu:tmux"])
        # Row 4: Main menu exit
        self.assertEqual([b["callback_data"] for b in rows[4]], ["menu:main"])

        # Telegram 64-byte callback_data limit check
        for row in rows:
            for b in row:
                cb = b.get("callback_data", "")
                self.assertLessEqual(len(cb.encode("utf-8")), 64, f"Callback data exceeds 64 bytes: {cb}")

    def test_screen_keyboard_attached_toggle(self):
        """Verify screen keyboard toggles between Attach and Detach based on active_tmux_target."""
        target = "0:1.0"
        self.bot.active_tmux_target = None
        kb_detached = self.bot._build_tmux_screen_keyboard(target)
        self.assertIn("Aggancia", kb_detached["inline_keyboard"][0][1]["text"])
        self.assertEqual(kb_detached["inline_keyboard"][0][1]["callback_data"], f"tmux:attach:{target}")

        self.bot.active_tmux_target = target
        kb_attached = self.bot._build_tmux_screen_keyboard(target)
        self.assertIn("Sgancia", kb_attached["inline_keyboard"][0][1]["text"])
        self.assertEqual(kb_attached["inline_keyboard"][0][1]["callback_data"], "tmux:detach")
        self.bot.active_tmux_target = None

    def test_exhaustive_escape_hatches_and_passthrough(self):
        """Exhaustively test all escape hatch variations and regular terminal command passthrough."""
        import asyncio
        self.bot.config.allowed_user_ids = [12345]
        self.bot.client.send_message = unittest.mock.AsyncMock()
        self.bot.tmux = unittest.mock.MagicMock()
        self.bot.tmux.send_input = unittest.mock.MagicMock()

        escape_words = [
            "/detach", "DETACH", "  detach  ", "sgancia", "SGANCIA",
            "🔙 sgancia (torna al bot)", "/unfocus", "esci", "ESCI",
            "/menu", "MENU", "menu", "Menu", "🔙 menu principale",
            "🔙 menu", "🔙 indietro", "indietro", "INDIETRO", "/start"
        ]

        for idx, word in enumerate(escape_words):
            self.bot.active_tmux_target = "0:1.0"
            self.bot.active_tmux_name = "test_sess"
            self.bot.current_reply_menu = "tmux"
            self.bot.tmux.send_input.reset_mock()

            update = {
                "update_id": 4000 + idx,
                "message": {
                    "message_id": 40 + idx,
                    "from": {"id": 12345, "first_name": "Tester"},
                    "chat": {"id": 12345},
                    "text": word,
                    "date": 1234567890
                }
            }
            asyncio.run(self.bot.process_telegram_update(update))
            self.assertIsNone(self.bot.active_tmux_target, f"Word '{word}' failed to detach target")
            self.assertEqual(self.bot.current_reply_menu, "main", f"Word '{word}' failed to set main menu")
            self.bot.tmux.send_input.assert_not_called()

        # Normal terminal commands must pass through to tmux
        normal_commands = ["ls -la", "pytest", "cat file.txt", "git status"]
        for idx, cmd in enumerate(normal_commands):
            self.bot.active_tmux_target = "0:1.0"
            self.bot.active_tmux_name = "worker"
            self.bot.tmux.send_input.reset_mock()

            update = {
                "update_id": 5000 + idx,
                "message": {
                    "message_id": 50 + idx,
                    "from": {"id": 12345, "first_name": "Tester"},
                    "chat": {"id": 12345},
                    "text": cmd,
                    "date": 1234567890
                }
            }
            asyncio.run(self.bot.process_telegram_update(update))
            self.assertEqual(self.bot.active_tmux_target, "0:1.0")
            self.bot.tmux.send_input.assert_called_once_with("0:1.0", cmd)

    def test_detached_controls_suppression_and_prompt_passthrough(self):
        """Verify detached control buttons do not leak to prompt, and prompt queries pass cleanly."""
        import asyncio
        self.bot.config.allowed_user_ids = [12345]
        self.bot.client.send_message = unittest.mock.AsyncMock()
        self.bot.handle_prompt = unittest.mock.AsyncMock()
        self.bot.active_tmux_target = None

        # 1. Tmux control buttons in main menu must NOT trigger handle_prompt
        control_buttons = ["👁️ Schermo", "🔄 Aggiorna", "🛑 Ctrl+C", "⏎ Invio"]
        for btn in control_buttons:
            self.bot.current_reply_menu = "main"
            self.bot.handle_prompt.reset_mock()
            update = {
                "update_id": 6000,
                "message": {
                    "message_id": 60,
                    "from": {"id": 12345, "first_name": "Tester"},
                    "chat": {"id": 12345},
                    "text": btn,
                    "date": 1234567890
                }
            }
            asyncio.run(self.bot.process_telegram_update(update))
            self.bot.handle_prompt.assert_not_called()
            self.assertEqual(self.bot.current_reply_menu, "main")

        # 2. Regular user query in main menu MUST trigger handle_prompt
        update_prompt = {
            "update_id": 7000,
            "message": {
                "message_id": 70,
                "from": {"id": 12345, "first_name": "Tester"},
                "chat": {"id": 12345},
                "text": "Spiegami la relatività generale",
                "date": 1234567890
            }
        }
        asyncio.run(self.bot.process_telegram_update(update_prompt))
        self.bot.handle_prompt.assert_awaited_once_with(
            "Spiegami la relatività generale",
            source="telegram",
            chat_id=12345
        )

    def test_rapid_inline_menu_transitions(self):
        """Stress-test sequential callback queries transitioning between menus."""
        import asyncio
        self.bot.config.allowed_user_ids = [12345]
        self.bot.client.edit_message_text = unittest.mock.AsyncMock()
        self.bot.client.answer_callback_query = unittest.mock.AsyncMock()
        self.bot.client.send_message = unittest.mock.AsyncMock()
        self.bot.tmux = unittest.mock.MagicMock()
        self.bot.tmux.format_sessions_card.return_value = "Card"
        self.bot.tmux.discover_sessions.return_value = []

        transitions = [
            ("menu:tmux", "tmux"),
            ("menu:main", "main"),
            ("tmux:view:all", "tmux"),
            ("menu:main", "main"),
            ("tmux:view:agy", "tmux"),
            ("tmux:refresh:all", "tmux"),
            ("tmux:refresh:agy", "tmux"),
            ("menu:main", "main"),
        ]

        for idx, (cb_data, expected_menu) in enumerate(transitions):
            update = {
                "update_id": 8000 + idx,
                "callback_query": {
                    "id": f"cq_trans_{idx}",
                    "from": {"id": 12345},
                    "message": {"chat": {"id": 12345}, "message_id": 80},
                    "data": cb_data
                }
            }
            asyncio.run(self.bot.process_telegram_update(update))
            self.assertEqual(
                self.bot.current_reply_menu,
                expected_menu,
                f"Transition on {cb_data} failed: expected {expected_menu}, got {self.bot.current_reply_menu}"
            )


if __name__ == '__main__':
    unittest.main()
