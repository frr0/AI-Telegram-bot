import os
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
import psutil


def render_progress_bar(percent: float, length: int = 10) -> str:
    """Renders a text progress bar like [████░░░░░░]."""
    clamped = max(0.0, min(100.0, float(percent)))
    filled_len = int(round((clamped / 100.0) * length))
    empty_len = length - filled_len
    return f"[{'█' * filled_len}{'░' * empty_len}]"


def format_bytes(bytes_val: float) -> str:
    """Formats bytes into GB or MB."""
    gb = bytes_val / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.1f} GB"
    mb = bytes_val / (1024 ** 2)
    return f"{mb:.0f} MB"


class HardwareMonitor:
    """Collects real-time hardware metrics (CPU, RAM, Disks, Temps, Battery)."""

    def get_hardware_metrics(self) -> Dict[str, Any]:
        # 1. CPU
        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_count = psutil.cpu_count(logical=True) or 1
        load_avg = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (0.0, 0.0, 0.0)

        # 2. Temperatures
        cpu_temp: Optional[float] = None
        temps_dict: Dict[str, float] = {}
        if hasattr(psutil, "sensors_temperatures"):
            try:
                sensors_temps = psutil.sensors_temperatures() or {}
                for chip, entries in sensors_temps.items():
                    for entry in entries:
                        label = entry.label or chip
                        if entry.current and entry.current > 0:
                            temps_dict[f"{chip}_{label}"] = entry.current
                            if cpu_temp is None and any(k in chip.lower() or k in label.lower() for k in ["cpu", "k10temp", "core", "tctl", "thinkpad"]):
                                cpu_temp = entry.current
                if cpu_temp is None and temps_dict:
                    cpu_temp = next(iter(temps_dict.values()))
            except Exception:
                pass

        # 3. RAM & Swap
        vmem = psutil.virtual_memory()
        swap = psutil.swap_memory()

        # 4. Disk (Root filesystem)
        disk = psutil.disk_usage("/")

        # 5. Battery
        bat_percent: Optional[float] = None
        bat_plugged: Optional[bool] = None
        bat_time_str: Optional[str] = None
        try:
            bat = psutil.sensors_battery()
            if bat:
                bat_percent = round(bat.percent, 1)
                bat_plugged = bat.power_plugged
                if bat.secsleft and bat.secsleft > 0 and bat.secsleft != psutil.POWER_TIME_UNLIMITED:
                    hrs = bat.secsleft // 3600
                    mins = (bat.secsleft % 3600) // 60
                    bat_time_str = f"~{hrs}h {mins}m" if hrs > 0 else f"~{mins}m"
        except Exception:
            pass

        # 6. Uptime
        try:
            boot_time = psutil.boot_time()
            uptime_seconds = int(time.time() - boot_time)
            up_hrs = uptime_seconds // 3600
            up_mins = (uptime_seconds % 3600) // 60
            uptime_str = f"{up_hrs}h {up_mins}m" if up_hrs > 0 else f"{up_mins}m"
        except Exception:
            uptime_str = "N/D"

        return {
            "cpu_percent": cpu_percent,
            "cpu_count": cpu_count,
            "load_avg": load_avg,
            "cpu_temp": cpu_temp,
            "ram_used_gb": vmem.used / (1024 ** 3),
            "ram_total_gb": vmem.total / (1024 ** 3),
            "ram_percent": vmem.percent,
            "swap_used_gb": swap.used / (1024 ** 3),
            "swap_total_gb": swap.total / (1024 ** 3),
            "swap_percent": swap.percent,
            "disk_used_gb": disk.used / (1024 ** 3),
            "disk_total_gb": disk.total / (1024 ** 3),
            "disk_percent": disk.percent,
            "battery_percent": bat_percent,
            "battery_plugged": bat_plugged,
            "battery_time_left_str": bat_time_str,
            "uptime_str": uptime_str,
        }

    def get_hardware_report(self) -> str:
        """Formats an interactive Telegram Markdown card for hardware statistics."""
        m = self.get_hardware_metrics()

        # Indicators
        cpu_bar = render_progress_bar(m["cpu_percent"])
        ram_bar = render_progress_bar(m["ram_percent"])
        disk_bar = render_progress_bar(m["disk_percent"])

        temp_str = f" • 🌡️ `{m['cpu_temp']:.0f}°C`" if m["cpu_temp"] is not None else ""
        load_str = f"`{m['load_avg'][0]:.2f}, {m['load_avg'][1]:.2f}, {m['load_avg'][2]:.2f}`"

        # Battery line
        bat_line = ""
        if m["battery_percent"] is not None:
            bat_icon = "🔌" if m["battery_plugged"] else "🔋"
            bat_status = "In carica" if m["battery_plugged"] else "A batteria"
            time_part = f" • {m['battery_time_left_str']} residui" if m['battery_time_left_str'] else ""
            bat_bar = render_progress_bar(m["battery_percent"])
            bat_line = (
                f"\n• *{bat_icon} Batteria:* `{m['battery_percent']:.1f}%` ({bat_status}{time_part})\n"
                f"  `{bat_bar}` `{m['battery_percent']:.0f}%`"
            )

        report = (
            "📊 *Monitor Risorse Hardware PC*\n\n"
            f"• *⚡ CPU ({m['cpu_count']} Core):* `{m['cpu_percent']:.1f}%`{temp_str}\n"
            f"  `{cpu_bar}` `{m['cpu_percent']:.0f}%` • Carico: {load_str}\n\n"
            f"• *🧠 RAM:* `{m['ram_used_gb']:.1f} GB / {m['ram_total_gb']:.1f} GB`\n"
            f"  `{ram_bar}` `{m['ram_percent']:.0f}%`\n\n"
            f"• *💽 Disco (/):* `{m['disk_used_gb']:.1f} GB / {m['disk_total_gb']:.1f} GB`\n"
            f"  `{disk_bar}` `{m['disk_percent']:.0f}%`\n"
            f"{bat_line}\n"
            f"• *⏱️ Uptime Sistema:* `{m['uptime_str']}`"
        )
        return report
