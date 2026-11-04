import re
import shutil
import subprocess
from typing import Dict, Any, List, Optional, Tuple


def _run_cmd(cmd: List[str], timeout: float = 3.0) -> Tuple[int, str]:
    """Runs a system command safely and returns (exit_code, stdout)."""
    try:
        res = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return res.returncode, res.stdout.strip()
    except Exception:
        return 1, ""


class MediaManager:
    """
    Controls desktop media playback (MPRIS via busctl / playerctl)
    and audio volume (PipeWire wpctl / PulseAudio pactl).
    """

    def __init__(self):
        pass

    # --- MPRIS Player Discovery & Control ---

    def get_active_mpris_players(self) -> List[str]:
        """Finds all running MPRIS media players on user DBus session."""
        players = []
        if shutil.which("busctl"):
            code, out = _run_cmd(["busctl", "--user", "list"])
            if code == 0:
                for line in out.splitlines():
                    parts = line.split()
                    if parts and parts[0].startswith("org.mpris.MediaPlayer2."):
                        players.append(parts[0])
        elif shutil.which("playerctl"):
            code, out = _run_cmd(["playerctl", "-l"])
            if code == 0:
                for p in out.splitlines():
                    if p.strip():
                        players.append(f"org.mpris.MediaPlayer2.{p.strip()}")
        return players

    def get_player_status(self) -> Dict[str, Any]:
        """Queries the current active player for playback status and metadata."""
        players = self.get_active_mpris_players()
        if not players:
            return {
                "has_player": False,
                "player_name": "",
                "status": "Inattivo",
                "title": "",
                "artist": "",
                "album": ""
            }

        # Prefer active playing player or first available
        target_player = players[0]
        status = "Paused"
        title = ""
        artist = ""
        album = ""

        for p in players:
            code, out = _run_cmd([
                "busctl", "--user", "get-property",
                p, "/org/mpris/MediaPlayer2",
                "org.mpris.MediaPlayer2.Player", "PlaybackStatus"
            ])
            if code == 0 and "Playing" in out:
                target_player = p
                status = "Playing"
                break
            elif code == 0 and "Paused" in out:
                status = "Paused"

        # Clean player display name (e.g. org.mpris.MediaPlayer2.spotify -> Spotify)
        raw_name = target_player.replace("org.mpris.MediaPlayer2.", "").split(".")[0]
        player_name = raw_name.capitalize()

        # Query metadata
        code, out = _run_cmd([
            "busctl", "--user", "call",
            target_player, "/org/mpris/MediaPlayer2",
            "org.freedesktop.DBus.Properties", "Get",
            "ss", "org.mpris.MediaPlayer2.Player", "Metadata"
        ])
        if code == 0:
            # Extract xesam:title
            m_title = re.search(r'"xesam:title"\s+s\s+"([^"]+)"', out)
            if m_title:
                title = m_title.group(1).strip()

            # Extract xesam:artist
            m_artist = re.search(r'"xesam:artist"\s+as\s+\d+\s+"([^"]*)"', out)
            if m_artist:
                artist = m_artist.group(1).strip()

            # Extract xesam:album
            m_album = re.search(r'"xesam:album"\s+s\s+"([^"]+)"', out)
            if m_album:
                album = m_album.group(1).strip()

        return {
            "has_player": True,
            "player_name": player_name,
            "status": status,
            "title": title or "Sconosciuto",
            "artist": artist,
            "album": album,
            "service": target_player
        }

    def _call_player_method(self, method: str) -> Tuple[bool, str]:
        players = self.get_active_mpris_players()
        if not players:
            return False, "Nessun lettore multimediale attivo rilevato."

        target = players[0]
        code, _ = _run_cmd([
            "busctl", "--user", "call",
            target, "/org/mpris/MediaPlayer2",
            "org.mpris.MediaPlayer2.Player", method
        ])
        return (code == 0), target

    def play_pause(self) -> Tuple[bool, str]:
        """Toggles play / pause on active media player."""
        return self._call_player_method("PlayPause")

    def next_track(self) -> Tuple[bool, str]:
        """Skips to next track."""
        return self._call_player_method("Next")

    def prev_track(self) -> Tuple[bool, str]:
        """Goes to previous track."""
        return self._call_player_method("Previous")

    def stop_playback(self) -> Tuple[bool, str]:
        """Stops media playback."""
        return self._call_player_method("Stop")

    # --- Audio Volume Controls (wpctl / pactl) ---

    def get_volume(self) -> Tuple[int, bool]:
        """Returns (volume_percent, is_muted)."""
        # 1. PipeWire wpctl
        if shutil.which("wpctl"):
            code, out = _run_cmd(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
            if code == 0 and "Volume:" in out:
                is_muted = "[MUTED]" in out
                parts = out.replace("[MUTED]", "").split()
                try:
                    vol_float = float(parts[1])
                    return int(round(vol_float * 100)), is_muted
                except (IndexError, ValueError):
                    pass

        # 2. PulseAudio pactl fallback
        if shutil.which("pactl"):
            code, out = _run_cmd(["pactl", "get-sink-volume", "@DEFAULT_SINK@"])
            if code == 0:
                m = re.search(r'(\d+)%', out)
                vol = int(m.group(1)) if m else 50
                code_m, out_m = _run_cmd(["pactl", "get-sink-mute", "@DEFAULT_SINK@"])
                is_muted = "yes" in out_m.lower()
                return vol, is_muted

        return 50, False

    def _set_volume_cmd(self, change: str) -> bool:
        if shutil.which("wpctl"):
            code, _ = _run_cmd(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", change])
            return code == 0
        if shutil.which("pactl"):
            code, _ = _run_cmd(["pactl", "set-sink-volume", "@DEFAULT_SINK@", change])
            return code == 0
        return False

    def _set_mute_cmd(self) -> bool:
        if shutil.which("wpctl"):
            code, _ = _run_cmd(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"])
            return code == 0
        if shutil.which("pactl"):
            code, _ = _run_cmd(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
            return code == 0
        return False

    def volume_up(self, step: int = 5) -> Tuple[int, bool]:
        self._set_volume_cmd(f"{step}%+")
        return self.get_volume()

    def volume_down(self, step: int = 5) -> Tuple[int, bool]:
        self._set_volume_cmd(f"{step}%-")
        return self.get_volume()

    def toggle_mute(self) -> Tuple[int, bool]:
        self._set_mute_cmd()
        return self.get_volume()

    # --- Markdown Report ---

    def get_media_report(self) -> str:
        """Generates an interactive media card."""
        player = self.get_player_status()
        vol, muted = self.get_volume()

        # Volume bar
        vol_clamped = max(0, min(100, vol))
        filled = int(round((vol_clamped / 100.0) * 10))
        vol_bar = f"[{'█' * filled}{'░' * (10 - filled)}]"
        vol_str = f"🔇 Silenziato ({vol}%)" if muted else f"🔊 `{vol}%` `{vol_bar}`"

        if player["has_player"]:
            icon_status = "▶️ In riproduzione" if player["status"] == "Playing" else "⏸️ In pausa"
            artist_line = f"• *👤 Artista:* `{player['artist']}`\n" if player["artist"] else ""
            album_line = f"• *💿 Album:* `{player['album']}`\n" if player["album"] else ""

            report = (
                "🎵 *Centro Multimediale PC*\n\n"
                f"• *📱 Lettore:* `{player['player_name']}` ({icon_status})\n"
                f"• *🎼 Traccia:* *{player['title']}*\n"
                f"{artist_line}{album_line}\n"
                f"• *Volume PC:* {vol_str}"
            )
        else:
            report = (
                "🎵 *Centro Multimediale PC*\n\n"
                "• *📱 Stato:* _Nessun lettore multimediale attivo rilevato_\n"
                "_(Puoi avviare Spotify, browser con YouTube, VLC o MPV sul PC)_\n\n"
                f"• *Volume PC:* {vol_str}"
            )

        return report
