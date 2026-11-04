import unittest
from unittest.mock import patch, MagicMock
from hardware_monitor import HardwareMonitor, render_progress_bar


class TestHardwareMonitor(unittest.TestCase):

    def setUp(self):
        self.monitor = HardwareMonitor()

    def test_render_progress_bar(self):
        bar_0 = render_progress_bar(0, length=10)
        self.assertEqual(bar_0, "[░░░░░░░░░░]")

        bar_50 = render_progress_bar(50, length=10)
        self.assertEqual(bar_50, "[█████░░░░░]")

        bar_100 = render_progress_bar(100, length=10)
        self.assertEqual(bar_100, "[██████████]")

        bar_capped = render_progress_bar(120, length=10)
        self.assertEqual(bar_capped, "[██████████]")

    def test_get_hardware_metrics(self):
        metrics = self.monitor.get_hardware_metrics()
        self.assertIn("cpu_percent", metrics)
        self.assertIn("ram_percent", metrics)
        self.assertIn("disk_percent", metrics)
        self.assertIn("uptime_str", metrics)
        self.assertIsInstance(metrics["cpu_percent"], (int, float))
        self.assertIsInstance(metrics["ram_percent"], (int, float))
        self.assertIsInstance(metrics["disk_percent"], (int, float))

    def test_get_hardware_report(self):
        report = self.monitor.get_hardware_report()
        self.assertIn("Monitor Risorse Hardware PC", report)
        self.assertIn("CPU", report)
        self.assertIn("RAM", report)
        self.assertIn("Disco", report)


if __name__ == "__main__":
    unittest.main()
