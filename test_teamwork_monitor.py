import os
import unittest
from teamwork_monitor import TeamworkMonitor


class TestTeamworkMonitor(unittest.TestCase):

    def setUp(self):
        self.monitor = TeamworkMonitor(news_path="/home/francesco/Progetti/News")

    def test_get_status_dict(self):
        status = self.monitor.get_status()
        self.assertIsInstance(status, dict)
        self.assertIn("is_active", status)
        self.assertIn("current_status_lines", status)
        self.assertIn("handoff_count", status)
        self.assertTrue(status["is_active"])
        self.assertGreaterEqual(status["handoff_count"], 3)

    def test_format_news_card(self):
        card = self.monitor.format_news_card()
        self.assertIn("Progetti/News", card)
        self.assertIn("Teamwork", card)
        self.assertIn("Survey", card)

    def test_detect_changes(self):
        # Initial check establishes baseline
        has_change, diff = self.monitor.check_for_updates()
        # Immediately after, no change should be detected
        has_change2, diff2 = self.monitor.check_for_updates()
        self.assertFalse(has_change2)


if __name__ == "__main__":
    unittest.main()
