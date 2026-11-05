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


if __name__ == "__main__":
    unittest.main()
