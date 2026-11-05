import unittest
from unittest.mock import patch, MagicMock
from tmux_bridge import TmuxBridge


class TestTmuxBridge(unittest.TestCase):

    def setUp(self):
        self.bridge = TmuxBridge()

    def test_discover_sessions(self):
        sessions = self.bridge.discover_agy_sessions()
        self.assertIsInstance(sessions, list)
        if sessions:
            first = sessions[0]
            self.assertIn("target", first)
            self.assertIn("window_name", first)
            self.assertIn("cwd", first)

    def test_capture_session_screen(self):
        sessions = self.bridge.discover_agy_sessions()
        if sessions:
            target = sessions[0]["target"]
            screen = self.bridge.capture_session_screen(target, lines=15)
            self.assertIsInstance(screen, str)
            self.assertGreater(len(screen), 0)

    def test_format_sessions_card(self):
        card = self.bridge.format_sessions_card()
        self.assertIn("Sessioni Antigravity su tmux", card)

    def test_discover_all_sessions(self):
        all_sessions = self.bridge.discover_all_sessions(include_aibot=True)
        self.assertIsInstance(all_sessions, list)
        card_all = self.bridge.format_sessions_card(only_agy=False)
        self.assertIn("Sessioni Tmux", card_all)

    def test_running_subagents_do_not_require_intervention(self):
        sample = """
  • Milestone 1 completata
  • Squadra di verifica attiva (Gate Check)
──────────────────────────────────────────────────
>
──────────────────────────────────────────────────
  ● Agent(auditor: Milestone 1 Auditor) Running · 3m
  ● Agent(orchestrator: Project Orchestrator) Working · 34m
──────────────────────────────────────────────────
? for shortcuts | Gemini 3.8 Flash | 3 subagent(s)
"""
        parsed = self.bridge.parse_pane_content(sample)
        self.assertEqual(parsed["subagents_count"], 3)
        self.assertFalse(parsed["requires_intervention"], "Running subagents without choices must NOT require intervention!")
        self.assertEqual(len(parsed["choices"]), 0)

    def test_informational_steps_in_pane_not_requiring_intervention(self):
        sample_steps = """
Prossimi passi che il team eseguirà:
1. Setup configurazione package.json
2. Creazione script di build
──────────────────────────────────────────────────
>
──────────────────────────────────────────────────
? for shortcuts | Gemini 3.8 Flash | 0 subagent(s)
"""
        parsed = self.bridge.parse_pane_content(sample_steps)
        self.assertFalse(parsed["requires_intervention"], "Informational future steps must not require user intervention!")
        self.assertEqual(len(parsed["choices"]), 0)

    def test_question_with_choices_requires_intervention(self):
        sample = """
Come desideri procedere per i test?
1. Esegui test unitari
2. Salta e procedi
──────────────────────────────────────────────────
>
──────────────────────────────────────────────────
? for shortcuts | Gemini 3.8 Flash | 0 subagent(s)
"""
        parsed = self.bridge.parse_pane_content(sample)
        self.assertTrue(parsed["requires_intervention"])
        self.assertEqual(len(parsed["choices"]), 2)
        self.assertEqual(parsed["choices"][0], "1. Esegui test unitari")

    def test_check_events_only_notifies_on_choice_or_completion(self):
        running_sample = """
  Milestone 1 in corso
  Running command npm install
──────────────────────
>
──────────────────────
? for shortcuts | 3 subagent(s)
"""
        with patch.object(self.bridge, "discover_agy_sessions", return_value=[{"target": "0:9.1", "window_name": "Test", "cwd": "/tmp"}]):
            # 1. Running state: must NOT notify
            with patch.object(self.bridge, "inspect_pane", return_value=self.bridge.parse_pane_content(running_sample)):
                events = self.bridge.check_for_session_events()
                self.assertEqual(len(events), 0, "No notifications during running execution!")

            # 2. When asking a question: MUST notify
            question_sample = """
Quale configurazione usare?
1. Config A
2. Config B
──────────────────────
>
──────────────────────
? for shortcuts | 0 subagent(s)
"""
            with patch.object(self.bridge, "inspect_pane", return_value=self.bridge.parse_pane_content(question_sample)):
                events = self.bridge.check_for_session_events()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["event_type"], "question")

            # 3. Deduplication: same question must NOT notify again
            with patch.object(self.bridge, "inspect_pane", return_value=self.bridge.parse_pane_content(question_sample)):
                events_dupe = self.bridge.check_for_session_events()
                self.assertEqual(len(events_dupe), 0, "Deduplication must prevent repeating the same question alert!")

            # 4. When all agents finished: MUST notify
            completion_sample = """
Your Teamwork run has finished. All deliverables are complete!
──────────────────────
>
──────────────────────
? for shortcuts | 0 subagent(s)
"""
            self.bridge._was_busy["0:9.1"] = True
            with patch.object(self.bridge, "inspect_pane", return_value=self.bridge.parse_pane_content(completion_sample)):
                events_comp = self.bridge.check_for_session_events()
                self.assertEqual(len(events_comp), 1)
                self.assertEqual(events_comp[0]["event_type"], "completed")

            # 5. When subagents drop to 0 without regex: MUST notify
            plain_finish_sample = """
Build succeeded.
──────────────────────
>
──────────────────────
? for shortcuts | 0 subagent(s)
"""
            self.bridge._was_busy["0:9.1"] = True
            self.bridge._notified_completions.clear()
            with patch.object(self.bridge, "inspect_pane", return_value=self.bridge.parse_pane_content(plain_finish_sample)):
                events_agents_done = self.bridge.check_for_session_events()
                self.assertEqual(len(events_agents_done), 1)
                self.assertEqual(events_agents_done[0]["event_type"], "completed")

    @patch("subprocess.run")
    def test_create_session_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        sname = self.bridge.create_session("my-test-chat", cwd="/tmp")
        self.assertEqual(sname, "my-test-chat")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("tmux", args)
        self.assertIn("new-session", args)
        self.assertIn("my-test-chat", args)

    @patch("subprocess.run")
    def test_create_session_duplicate_retry(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="duplicate session: my_chat"),
            MagicMock(returncode=0, stdout="", stderr="")
        ]
        sname = self.bridge.create_session("my_chat", cwd="/tmp")
        self.assertIsNotNone(sname)
        self.assertTrue(sname.startswith("my_chat_"))
        self.assertEqual(mock_run.call_count, 2)

    def test_create_session_tmux_unavailable(self):
        with patch.object(self.bridge, "is_tmux_available", return_value=False):
            sname = self.bridge.create_session("my_chat")
            self.assertIsNone(sname)

    @patch("subprocess.run")
    def test_send_special_key(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        success = self.bridge.send_key("0:1.0", "C-c")
        self.assertTrue(success)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args, ["tmux", "send-keys", "-t", "0:1.0", "C-c"])

    @patch("subprocess.run")
    def test_kill_session(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        success = self.bridge.kill_session("chat_12345")
        self.assertTrue(success)
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertEqual(args, ["tmux", "kill-session", "-t", "chat_12345"])

    def test_cmd_new_creates_tmux_session(self):
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.send_message = AsyncMock()
        app.tmux = MagicMock()
        app.tmux.is_tmux_available.return_value = True
        app.tmux.create_session.return_value = "chat_20261105_131500"

        import asyncio
        asyncio.run(app.cmd_new(chat_id=12345))

        app.tmux.create_session.assert_called_once()
        app.client.send_message.assert_called_once()
        sent_text = app.client.send_message.call_args[0][1]
        self.assertIn("chat_20261105_131500", sent_text)
        self.assertIn("tmux a -t chat_20261105_131500", sent_text)

    def test_attached_session_routes_message(self):
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.send_message = AsyncMock()
        app.config = MagicMock()
        app.config.is_user_allowed.return_value = True
        app.tmux = MagicMock()
        app.tmux.send_input.return_value = True
        app.tmux.capture_session_screen.return_value = "Prompt output here"
        app.active_tmux_target = "chat_20261105"
        app.active_tmux_name = "chat_20261105"

        import asyncio
        asyncio.run(app.process_telegram_update({"message": {"from": {"id": 12345}, "chat": {"id": 12345}, "text": "ls -la"}}))

        app.tmux.send_input.assert_called_once_with("chat_20261105", "ls -la")

    @patch("subprocess.run")
    def test_discover_sessions_includes_session_name(self, mock_run):
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "chat_20261105:0.0|99999|agy|python3\n"
        mock_run.return_value = mock_res
        sessions = self.bridge.discover_sessions(only_agy=False, include_aibot=True)
        self.assertEqual(len(sessions), 1)
        sess = sessions[0]
        self.assertEqual(sess["target"], "chat_20261105:0.0")
        self.assertEqual(sess["session_name"], "chat_20261105")
        self.assertEqual(sess["name"], "chat_20261105")
        self.assertEqual(sess["window_name"], "agy")

    def test_attach_callback_handles_missing_session_name_key(self):
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.send_message = AsyncMock()
        app.client.answer_callback_query = AsyncMock()
        app.config = MagicMock()
        app.config.is_user_allowed.return_value = True
        app.tmux = MagicMock()
        # Session dicts completely missing 'session_name'
        app.tmux.discover_all_sessions.return_value = [
            {"target": "worker:0.0", "window_name": "worker"},
            {"target": "chat_20261105:0.0", "window_name": "agy"}
        ]
        app.cmd_tmux_screen = AsyncMock()

        update = {
            "update_id": 100,
            "callback_query": {
                "id": "cq100",
                "from": {"id": 12345, "first_name": "Test"},
                "message": {"chat": {"id": 12345}, "message_id": 10},
                "data": "tmux:attach:chat_20261105:0.0"
            }
        }
        import asyncio
        asyncio.run(app.process_telegram_update(update))

        self.assertEqual(app.active_tmux_target, "chat_20261105:0.0")
        self.assertEqual(app.active_tmux_name, "agy")
        app.client.answer_callback_query.assert_any_call("cq100", "🎯 Agganciato a agy!", show_alert=False)

    def test_attach_command_handles_missing_session_name_and_key_variations(self):
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.send_message = AsyncMock()
        app.config = MagicMock()
        app.config.is_user_allowed.return_value = True
        app.tmux = MagicMock()
        # Heterogeneous dictionaries without 'session_name'
        app.tmux.discover_all_sessions.return_value = [
            {"target": "worker:0.0", "window_name": "worker"},
            {"target": "chat_20261105:0.0", "window_name": "agy"}
        ]
        app.cmd_tmux_screen = AsyncMock()

        import asyncio
        update = {
            "update_id": 101,
            "message": {
                "message_id": 2,
                "from": {"id": 12345, "first_name": "Test"},
                "chat": {"id": 12345},
                "text": "/attach agy"
            }
        }
        asyncio.run(app.process_telegram_update(update))

        self.assertEqual(app.active_tmux_target, "chat_20261105:0.0")
        self.assertEqual(app.active_tmux_name, "agy")

    def test_send_command_matches_session_name(self):
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.send_message = AsyncMock()
        app.tmux = MagicMock()
        app.tmux.discover_all_sessions.return_value = [
            {"target": "chat_123:0.0", "window_name": "worker_win", "session_name": "chat_123"}
        ]
        app.tmux.send_input.return_value = True

        import asyncio
        asyncio.run(app.cmd_send("chat_123 status", chat_id=12345))

        app.tmux.send_input.assert_called_once_with("chat_123:0.0", "status")

    def test_screen_capture_markdown_sanitization(self):
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.send_message = AsyncMock()
        app.tmux = MagicMock()
        app.tmux.capture_session_screen.return_value = "```python\nprint('hello')\n```"

        import asyncio
        asyncio.run(app.cmd_tmux_screen("0:1.0", chat_id=12345))

        app.client.send_message.assert_called_once()
        sent_text = app.client.send_message.call_args[1].get("text") or app.client.send_message.call_args[0][1]
        self.assertNotIn("```python", sent_text)
        self.assertIn("'''python", sent_text)

    def test_callback_key_preserves_colon_target(self):
        """Regression test for callback delimiter truncation bug in tmux:key:."""
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.answer_callback_query = AsyncMock()
        app.config.allowed_user_ids = [12345]
        app.tmux = MagicMock()
        app.tmux.send_key.return_value = True
        app.cmd_tmux_screen = AsyncMock()

        test_cases = [
            ("tmux:key:0:1.0:C-c", "0:1.0", "C-c"),
            ("tmux:key:chat_1:0.0:Enter", "chat_1:0.0", "Enter"),
            ("tmux:key:chat_20261105_131500:0.0:C-c", "chat_20261105_131500:0.0", "C-c"),
        ]

        import asyncio
        for cb_data, expected_target, expected_key in test_cases:
            app.tmux.send_key.reset_mock()
            update = {
                "update_id": 100,
                "callback_query": {
                    "id": "cq_key",
                    "from": {"id": 12345},
                    "message": {"chat": {"id": 12345}, "message_id": 42},
                    "data": cb_data
                }
            }
            asyncio.run(app.process_telegram_update(update))
            app.tmux.send_key.assert_called_once_with(expected_target, expected_key)

    def test_callback_send_preserves_colon_target(self):
        """Regression test for callback delimiter truncation bug in tmux:send:."""
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.answer_callback_query = AsyncMock()
        app.config.allowed_user_ids = [12345]
        app.tmux = MagicMock()
        app.tmux.send_input.return_value = True
        app.cmd_tmux_screen = AsyncMock()

        test_cases = [
            ("tmux:send:0:1.0:1", "0:1.0", "1"),
            ("tmux:send:0:1.0:y", "0:1.0", "y"),
            ("tmux:send:chat_20261105_131500:0.0:2", "chat_20261105_131500:0.0", "2"),
        ]

        import asyncio
        for cb_data, expected_target, expected_val in test_cases:
            app.tmux.send_input.reset_mock()
            update = {
                "update_id": 101,
                "callback_query": {
                    "id": "cq_send",
                    "from": {"id": 12345},
                    "message": {"chat": {"id": 12345}, "message_id": 42},
                    "data": cb_data
                }
            }
            asyncio.run(app.process_telegram_update(update))
            app.tmux.send_input.assert_called_once_with(expected_target, expected_val)

    def test_attach_callback_multiwindow_exact_target(self):
        """Regression test: multi-window session attachment must match exact window target."""
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.send_message = AsyncMock()
        app.client.answer_callback_query = AsyncMock()
        app.config.allowed_user_ids = [12345]
        app.tmux = MagicMock()
        app.tmux.discover_all_sessions.return_value = [
            {"target": "chat_1:0.0", "window_name": "win0", "session_name": "chat_1"},
            {"target": "chat_1:1.0", "window_name": "win1", "session_name": "chat_1"}
        ]
        app.cmd_tmux_screen = AsyncMock()

        import asyncio
        update = {
            "update_id": 102,
            "callback_query": {
                "id": "cq_win1",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 42},
                "data": "tmux:attach:chat_1:1.0"
            }
        }
        asyncio.run(app.process_telegram_update(update))

        self.assertEqual(app.active_tmux_target, "chat_1:1.0")
        self.assertEqual(app.active_tmux_name, "win1")

    def test_kill_callbacks_preserve_colon_target(self):
        """Regression test for kill_ask and kill_confirm callbacks with colon target."""
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.edit_message_text = AsyncMock()
        app.client.answer_callback_query = AsyncMock()
        app.client.send_message = AsyncMock()
        app.config.allowed_user_ids = [12345]
        app.tmux = MagicMock()
        app.tmux.kill_session.return_value = True

        target = "chat_20261105:0.0"
        import asyncio

        # 1. kill_ask
        update_ask = {
            "update_id": 103,
            "callback_query": {
                "id": "cq_kask",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 42},
                "data": f"tmux:kill_ask:{target}"
            }
        }
        asyncio.run(app.process_telegram_update(update_ask))
        app.client.edit_message_text.assert_awaited()
        edit_text = app.client.edit_message_text.call_args[1]["text"]
        self.assertIn(target, edit_text)

        # 2. kill_confirm
        update_confirm = {
            "update_id": 104,
            "callback_query": {
                "id": "cq_kconf",
                "from": {"id": 12345},
                "message": {"chat": {"id": 12345}, "message_id": 42},
                "data": f"tmux:kill_confirm:{target}"
            }
        }
        asyncio.run(app.process_telegram_update(update_confirm))
        app.tmux.kill_session.assert_called_with(target)

    def test_screen_capture_boundaries_and_sanitization(self):
        """Regression test for screen markdown sanitization, large buffer truncation, and empty screens."""
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.send_message = AsyncMock()
        app.tmux = MagicMock()

        import asyncio

        # Case A: Nested triple backticks
        app.tmux.capture_session_screen.return_value = "```python\ndef test():\n    pass\n```"
        asyncio.run(app.cmd_tmux_screen("0:1.0", chat_id=12345))
        sent_text = app.client.send_message.call_args[1].get("text") or app.client.send_message.call_args[0][1]
        self.assertNotIn("```python", sent_text)
        self.assertIn("'''python", sent_text)

        # Case B: Large screen buffer (> 4000 chars)
        app.client.send_message.reset_mock()
        app.tmux.capture_session_screen.return_value = "X" * 10000
        asyncio.run(app.cmd_tmux_screen("0:1.0", chat_id=12345))
        sent_text = app.client.send_message.call_args[1].get("text") or app.client.send_message.call_args[0][1]
        self.assertLess(len(sent_text), 4000)
        self.assertIn("X" * 3500, sent_text)
        self.assertNotIn("X" * 3501, sent_text)

        # Case C: Empty screen
        app.client.send_message.reset_mock()
        app.tmux.capture_session_screen.return_value = ""
        asyncio.run(app.cmd_tmux_screen("0:1.0", chat_id=12345))
        sent_text = app.client.send_message.call_args[1].get("text") or app.client.send_message.call_args[0][1]
        self.assertIn("```text\n\n```", sent_text)

    def test_bridge_consumers_resilience_malformed_sessions(self):
        """Regression test: format_sessions_card and check_for_session_events handle malformed dicts safely."""
        malformed_sessions = [
            {},
            {"target": "0:1.0", "window_name": "agy", "is_agy": True},  # Missing subagents_count
            {"target": None, "window_name": None, "session_name": None},
            {"dummy_key": "dummy_val"}
        ]

        # format_sessions_card (only_agy=True and False)
        with patch.object(self.bridge, "discover_sessions", return_value=malformed_sessions):
            card_agy = self.bridge.format_sessions_card(only_agy=True)
            self.assertIsInstance(card_agy, str)
            card_all = self.bridge.format_sessions_card(only_agy=False)
            self.assertIsInstance(card_all, str)

        # check_for_session_events
        with patch.object(self.bridge, "discover_agy_sessions", return_value=malformed_sessions):
            with patch.object(self.bridge, "inspect_pane", return_value={"subagents_count": 0, "requires_intervention": False, "is_completed": False, "choices": [], "snippet": ""}):
                events = self.bridge.check_for_session_events()
                self.assertIsInstance(events, list)

    def test_bot_session_lookup_edge_cases(self):
        """Regression test: BotApp session lookup handles integer names, None targets, and empty dicts."""
        from unittest.mock import AsyncMock
        from main import BotApp
        app = BotApp()
        app.client = MagicMock()
        app.client.send_message = AsyncMock()
        app.config.allowed_user_ids = [12345]
        app.tmux = MagicMock()
        app.tmux.send_input.return_value = True

        app.tmux.discover_all_sessions.return_value = [
            {},
            {"target": None, "session_name": None, "name": None, "window_name": None},
            {"target": "worker:0.0", "window_name": "worker", "name": 9999},
        ]
        app.cmd_tmux_screen = AsyncMock()

        import asyncio
        # /attach command with tricky input
        update = {
            "update_id": 105,
            "message": {
                "message_id": 5,
                "from": {"id": 12345, "first_name": "Test"},
                "chat": {"id": 12345},
                "text": "/attach worker"
            }
        }
        asyncio.run(app.process_telegram_update(update))

        # cmd_send with integer and missing targets
        asyncio.run(app.cmd_send("worker status", chat_id=12345))
        app.client.send_message.assert_awaited()


if __name__ == "__main__":
    unittest.main()
