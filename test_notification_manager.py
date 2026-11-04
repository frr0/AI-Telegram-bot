import unittest
from notification_manager import (
    format_duration,
    build_completion_header,
    NotificationManager
)

class TestNotificationManager(unittest.TestCase):

    def test_format_duration(self):
        self.assertEqual(format_duration(0.4), "0.4s")
        self.assertEqual(format_duration(45), "45s")
        self.assertEqual(format_duration(75), "1m 15s")
        self.assertEqual(format_duration(3665), "1h 1m 5s")

    def test_build_completion_header_success(self):
        header = build_completion_header(
            success=True,
            elapsed=12.5,
            is_question=False,
            model="Gemini 3.8 Flash (Medium)"
        )
        self.assertIn("✅", header)
        self.assertIn("12.5s", header)
        self.assertIn("Gemini 3.8 Flash", header)

    def test_build_completion_header_question(self):
        header = build_completion_header(
            success=True,
            elapsed=35.0,
            is_question=True,
            model="Claude Sonnet 5.5"
        )
        self.assertIn("🔔", header)
        self.assertIn("attende", header.lower())
        self.assertIn("35s", header)

    def test_build_completion_header_failure(self):
        header = build_completion_header(
            success=False,
            elapsed=4.0,
            is_question=False
        )
        self.assertIn("❌", header)
        self.assertIn("4.0s", header)

    def test_notification_manager_toggles(self):
        mgr = NotificationManager()
        self.assertTrue(mgr.sound_alerts)
        mgr.toggle_sound()
        self.assertFalse(mgr.sound_alerts)
        mgr.toggle_sound()
        self.assertTrue(mgr.sound_alerts)

        self.assertTrue(mgr.desktop_notifications)
        mgr.toggle_desktop()
        self.assertFalse(mgr.desktop_notifications)
        mgr.toggle_desktop()
        self.assertTrue(mgr.desktop_notifications)

    def test_notification_manager_status_report(self):
        mgr = NotificationManager()
        report = mgr.get_status_report()
        self.assertIn("Notifiche", report)
        self.assertIn("Desktop", report)
        self.assertIn("Suono", report)

if __name__ == "__main__":
    unittest.main()
