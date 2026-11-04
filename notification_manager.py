import os
import shutil
import subprocess
from typing import Tuple, Optional


def format_duration(seconds: float) -> str:
    """Formats execution seconds into human-readable duration."""
    if seconds < 60.0:
        if seconds < 10.0 or seconds % 1 != 0:
            return f"{seconds:.1f}s"
        return f"{int(seconds)}s"
    elif seconds < 3600.0:
        sec_int = int(round(seconds))
        minutes = sec_int // 60
        rem_sec = sec_int % 60
        return f"{minutes}m {rem_sec}s" if rem_sec > 0 else f"{minutes}m"
    else:
        sec_int = int(round(seconds))
        hours = sec_int // 3600
        rem_min = (sec_int % 3600) // 60
        rem_sec = sec_int % 60
        parts = [f"{hours}h"]
        if rem_min > 0:
            parts.append(f"{rem_min}m")
        if rem_sec > 0:
            parts.append(f"{rem_sec}s")
        return " ".join(parts)


def build_completion_header(
    success: bool,
    elapsed: float,
    is_question: bool = False,
    model: str = ""
) -> str:
    """Builds a formatted Markdown push notification header."""
    dur_str = format_duration(elapsed)
    model_tag = f" • 🧠 `{model}`" if model else ""

    if is_question:
        return f"🔔 *Azione Richiesta: Antigravity attende la tua risposta* ⏱️ _{dur_str}_{model_tag}"
    elif success:
        return f"✅ *Task completato con successo* ⏱️ _{dur_str}_{model_tag}"
    else:
        return f"❌ *Task fallito o interrotto* ⏱️ _{dur_str}_{model_tag}"


class NotificationManager:
    """
    Manages desktop notifications (notify-send), audio chimes,
    and push notification dispatching for task completions.
    """

    def __init__(
        self,
        desktop_notifications: bool = True,
        sound_alerts: bool = True,
        min_duration_seconds: float = 0.0
    ):
        self.desktop_notifications = desktop_notifications
        self.sound_alerts = sound_alerts
        self.min_duration_seconds = min_duration_seconds

    def toggle_sound(self) -> bool:
        self.sound_alerts = not self.sound_alerts
        return self.sound_alerts

    def toggle_desktop(self) -> bool:
        self.desktop_notifications = not self.desktop_notifications
        return self.desktop_notifications

    def get_status_report(self) -> str:
        sound_icon = "🔊 Attivo" if self.sound_alerts else "🔇 Disattivato"
        desktop_icon = "🖥️ Attivo" if self.desktop_notifications else "⚪ Disattivato"
        min_dur = f"{self.min_duration_seconds}s" if self.min_duration_seconds > 0 else "Tutti i task (0s)"

        return (
            "🔔 *Impostazioni Notifiche Antigravity*\n\n"
            f"• *Notifiche Desktop (Linux):* {desktop_icon}\n"
            f"• *Suono Notifica PC:* {sound_icon}\n"
            f"• *Soglia Minima:* `{min_dur}`\n"
            f"• *Push Telegram:* 📲 Ricezione automatica attiva con suono/vibrazione"
        )

    def trigger_local_alert(
        self,
        title: str,
        body: str,
        is_question: bool = False,
        urgency: str = "normal"
    ):
        """
        Triggers a Linux desktop notification (notify-send) and plays a chime.
        Safely ignores missing system binaries or headless environments.
        """
        # 1. Desktop Notification
        if self.desktop_notifications and shutil.which("notify-send"):
            try:
                env = os.environ.copy()
                if "DISPLAY" not in env:
                    env["DISPLAY"] = ":1"
                subprocess.Popen(
                    ["notify-send", "-a", "Antigravity Bridge", "-u", urgency, title, body],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass

        # 2. Audio Chime
        if self.sound_alerts:
            sound_name = "dialog-warning" if is_question else "complete"
            if shutil.which("canberra-gtk-play"):
                try:
                    subprocess.Popen(
                        ["canberra-gtk-play", "-i", sound_name],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    return
                except Exception:
                    pass

            # Fallback to paplay with standard system sound
            if shutil.which("paplay"):
                sound_file = f"/usr/share/sounds/freedesktop/stereo/{sound_name}.oga"
                if os.path.exists(sound_file):
                    try:
                        subprocess.Popen(
                            ["paplay", sound_file],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                    except Exception:
                        pass
