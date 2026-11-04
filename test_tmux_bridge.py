import unittest
from unittest.mock import patch, MagicMock
from tmux_bridge import TmuxBridge


class TestTmuxBridge(unittest.TestCase):

    def setUp(self):
        self.bridge = TmuxBridge()

    def test_discover_sessions(self):
        sessions = self.bridge.discover_agy_sessions()
        self.assertIsInstance(sessions, list)
        # News is currently running in 0:3.1
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

    def test_parse_pane_output(self):
        sample = """
  ### Aggiornamento di Avanzamento (Teamwork)
  • Milestone 1 in corso
──────────────────────────────
>
  ● Agent(test: Test Subagent) Running · 1m
──────────────────────────────
? for shortcuts | Gemini 3.8 Flash | 3 subagent(s)
"""
        parsed = self.bridge.parse_pane_content(sample)
        self.assertEqual(parsed["subagents_count"], 3)
        self.assertTrue(parsed["is_waiting_input"])
        self.assertIn("Milestone 1 in corso", parsed["main_text"])


if __name__ == "__main__":
    unittest.main()
