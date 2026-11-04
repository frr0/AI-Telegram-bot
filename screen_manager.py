import os
import shutil
import subprocess
from typing import Tuple


def _get_display_env() -> dict:
    """Builds an environment dictionary with proper DISPLAY and WAYLAND_DISPLAY paths."""
    env = os.environ.copy()
    uid = os.getuid()
    if "XDG_RUNTIME_DIR" not in env:
        env["XDG_RUNTIME_DIR"] = f"/run/user/{uid}"

    if "WAYLAND_DISPLAY" not in env:
        runtime_dir = env["XDG_RUNTIME_DIR"]
        if os.path.exists(runtime_dir):
            for fname in os.listdir(runtime_dir):
                if fname.startswith("wayland-") and not fname.endswith(".lock"):
                    env["WAYLAND_DISPLAY"] = fname
                    break
            else:
                env["WAYLAND_DISPLAY"] = "wayland-1"
        else:
            env["WAYLAND_DISPLAY"] = "wayland-1"

    if "DISPLAY" not in env:
        env["DISPLAY"] = ":1"

    return env


def set_screen_power(turn_on: bool) -> Tuple[bool, str]:
    """
    Turns screen monitors ON or OFF.
    Supports Niri, Hyprland, Sway, wlopm, and X11 xset.
    """
    env = _get_display_env()
    action_str = "acceso" if turn_on else "spento"

    # 1. Niri (Default compositor)
    if shutil.which("niri"):
        niri_cmd = "power-on-monitors" if turn_on else "power-off-monitors"
        try:
            res = subprocess.run(
                ["niri", "msg", "action", niri_cmd],
                env=env,
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                state_icon = "💡" if turn_on else "💤"
                return True, f"{state_icon} *Schermo del PC {action_str}* (via Niri DPMS)."
        except Exception:
            pass

    # 2. Hyprland
    if shutil.which("hyprctl"):
        dpms_arg = "on" if turn_on else "off"
        try:
            res = subprocess.run(
                ["hyprctl", "dispatch", "dpms", dpms_arg],
                env=env,
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                state_icon = "💡" if turn_on else "💤"
                return True, f"{state_icon} *Schermo del PC {action_str}* (via Hyprland DPMS)."
        except Exception:
            pass

    # 3. Sway
    if shutil.which("swaymsg"):
        dpms_arg = "on" if turn_on else "off"
        try:
            res = subprocess.run(
                ["swaymsg", f"output * dpms {dpms_arg}"],
                env=env,
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                state_icon = "💡" if turn_on else "💤"
                return True, f"{state_icon} *Schermo del PC {action_str}* (via Sway DPMS)."
        except Exception:
            pass

    # 4. wlopm (generic Wayland DPMS)
    if shutil.which("wlopm"):
        flag = "--on" if turn_on else "--off"
        try:
            res = subprocess.run(
                ["wlopm", flag, "*"],
                env=env,
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                state_icon = "💡" if turn_on else "💤"
                return True, f"{state_icon} *Schermo del PC {action_str}* (via wlopm)."
        except Exception:
            pass

    # 5. X11 xset fallback
    if shutil.which("xset"):
        xset_cmd = ["xset", "dpms", "force", "on" if turn_on else "off"]
        try:
            res = subprocess.run(
                xset_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                state_icon = "💡" if turn_on else "💤"
                return True, f"{state_icon} *Schermo del PC {action_str}* (via X11 xset)."
        except Exception:
            pass

    return False, "❌ Impossibile controllare lo schermo: nessun gestore DPMS supportato (niri, hyprctl, swaymsg, wlopm, xset)."


def lock_screen() -> Tuple[bool, str]:
    """Locks the graphical session (Noctalia / Logind)."""
    env = _get_display_env()

    if shutil.which("noctalia"):
        try:
            res = subprocess.run(
                ["noctalia", "msg", "session", "lock"],
                env=env,
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                return True, "🔒 *Schermo bloccato con successo* (Noctalia)."
        except Exception:
            pass

    # Fallback to loginctl lock-session
    try:
        subprocess.run(["loginctl", "lock-session"], timeout=3)
        return True, "🔒 *Schermo bloccato* (loginctl)."
    except Exception:
        pass

    return False, "❌ Impossibile bloccare lo schermo."


def unlock_screen() -> Tuple[bool, str]:
    """Unlocks the graphical session and powers on monitors."""
    # 1. Turn on monitors first
    set_screen_power(True)

    # 2. Find active user session
    session_id = os.environ.get("XDG_SESSION_ID")
    if not session_id:
        try:
            out = subprocess.check_output(["loginctl", "list-sessions", "--no-legend"], text=True)
            for line in out.strip().split("\n"):
                parts = line.split()
                if len(parts) >= 3 and parts[2] == os.environ.get("USER", "francesco"):
                    session_id = parts[0]
                    break
        except Exception:
            session_id = "2"

    if session_id:
        try:
            res = subprocess.run(
                ["loginctl", "unlock-session", session_id],
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                return True, "🔓 *Schermo sbloccato con successo!* (Noctalia / Logind)"
        except Exception:
            pass

    return False, "❌ Impossibile sbloccare la sessione."
