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

    def test_filter_intermediate_updates_no_event(self):
        sample = """
  Milestone 1 in corso
  Running command npm install
"""
        with patch.object(self.bridge, "discover_agy_sessions", return_value=[{"target": "0:9.1", "window_name": "Test", "cwd": "/tmp"}]), \
             patch.object(self.bridge, "inspect_pane", return_value={
                 "main_text": sample,
                 "is_waiting_input": False,
                 "choices": [],
                 "subagents_count": 2,
                 "snippet": "Milestone 1 in corso"
             }):
            # First call establishes baseline
            self.bridge.check_for_session_events()
            # Second call with slightly different intermediate text
            with patch.object(self.bridge, "inspect_pane", return_value={
                "main_text": sample + "\nNext command",
                "is_waiting_input": False,
                "choices": [],
                "subagents_count": 2,
                "snippet": "Next command"
            }):
                events = self.bridge.check_for_session_events()
                self.assertEqual(len(events), 0, "Intermediate progress must NOT generate notifications!")

    def test_detect_choice_question(self):
        sample = """
Quale opzione preferisci?
1. Opzione A
2. Opzione B
>
"""
        with patch.object(self.bridge, "discover_agy_sessions", return_value=[{"target": "0:9.1", "window_name": "Test", "cwd": "/tmp"}]):
            self.bridge._last_content_hashes["0:9.1"] = "old_hash"
            self.bridge._last_waiting_state["0:9.1"] = False
            with patch.object(self.bridge, "inspect_pane", return_value={
                "main_text": sample,
                "is_waiting_input": True,
                "choices": ["1. Opzione A", "2. Opzione B"],
                "subagents_count": 1,
                "snippet": "Quale opzione preferisci?"
            }):
                events = self.bridge.check_for_session_events()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["event_type"], "question")
                self.assertEqual(len(events[0]["choices"]), 2)

    def test_detect_task_completion(self):
        sample = """
Your Teamwork run has finished. All milestones verified and deliverables ready!
"""
        with patch.object(self.bridge, "discover_agy_sessions", return_value=[{"target": "0:9.1", "window_name": "Test", "cwd": "/tmp"}]):
            self.bridge._last_content_hashes["0:9.1"] = "old_hash"
            self.bridge._last_waiting_state["0:9.1"] = False
            with patch.object(self.bridge, "inspect_pane", return_value={
                "main_text": sample,
                "is_waiting_input": True,
                "choices": [],
                "subagents_count": 0,
                "snippet": "Your Teamwork run has finished."
            }):
                events = self.bridge.check_for_session_events()
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["event_type"], "completed")


if __name__ == "__main__":
    unittest.main()
