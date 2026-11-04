import unittest
from unittest.mock import patch, MagicMock
from media_manager import MediaManager


class TestMediaManager(unittest.TestCase):

    def setUp(self):
        self.manager = MediaManager()

    def test_format_media_report_no_player(self):
        with patch.object(self.manager, "get_player_status", return_value={"has_player": False, "player_name": "", "status": "Inattivo", "title": "", "artist": "", "album": ""}), \
             patch.object(self.manager, "get_volume", return_value=(70, False)):
            report = self.manager.get_media_report()
            self.assertIn("Centro Multimediale PC", report)
            self.assertIn("Nessun lettore", report)
            self.assertIn("70%", report)

    def test_format_media_report_with_player(self):
        with patch.object(self.manager, "get_player_status", return_value={
            "has_player": True,
            "player_name": "Spotify",
            "status": "Playing",
            "title": "Bohemian Rhapsody",
            "artist": "Queen",
            "album": "A Night at the Opera"
        }), patch.object(self.manager, "get_volume", return_value=(85, False)):
            report = self.manager.get_media_report()
            self.assertIn("Spotify", report)
            self.assertIn("Bohemian Rhapsody", report)
            self.assertIn("Queen", report)
            self.assertIn("85%", report)

    def test_volume_adjust(self):
        with patch.object(self.manager, "_set_volume_cmd", return_value=True), \
             patch.object(self.manager, "get_volume", return_value=(80, False)):
            vol, muted = self.manager.volume_up(5)
            self.assertEqual(vol, 80)
            self.assertFalse(muted)

    def test_toggle_mute(self):
        with patch.object(self.manager, "_set_mute_cmd", return_value=True), \
             patch.object(self.manager, "get_volume", return_value=(80, True)):
            vol, muted = self.manager.toggle_mute()
            self.assertTrue(muted)


if __name__ == "__main__":
    unittest.main()
