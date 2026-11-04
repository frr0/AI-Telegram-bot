import hashlib
import os
import re
import shutil
import subprocess
from typing import Dict, Any, List, Optional, Tuple


class TmuxBridge:
    """
    Bidirectional bridge between Telegram and Antigravity (agy) sessions running inside tmux.
    Discovers running agy instances across all tmux windows, monitors their subagents and output,
    and enables sending commands or choices directly from Telegram.
    """

    def __init__(self):
        self._last_content_hashes: Dict[str, str] = {}
        self._last_waiting_state: Dict[str, bool] = {}

    def is_tmux_available(self) -> bool:
        return shutil.which("tmux") is not None

    def discover_agy_sessions(self) -> List[Dict[str, Any]]:
        """
        Scans all tmux panes across all sessions and returns those running an agy instance
        (excluding AIbot itself).
        """
        if not self.is_tmux_available():
            return []

        try:
            res = subprocess.run(
                ["tmux", "list-panes", "-a", "-F", "#{session_name}:#{window_index}.#{pane_index}|#{pane_pid}|#{window_name}|#{pane_current_command}"],
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode != 0:
                return []
        except Exception:
            return []

        sessions = []
        for line in res.stdout.strip().splitlines():
            if not line or "|" not in line:
                continue
            parts = line.split("|")
            if len(parts) < 4:
                continue
            target, pid_str, win_name, cmd = parts[0], parts[1], parts[2], parts[3]
            try:
                pane_pid = int(pid_str)
            except ValueError:
                continue

            # Gather child PIDs
            pids_to_check = [pane_pid]
            try:
                pg = subprocess.run(["pgrep", "-P", str(pane_pid)], capture_output=True, text=True, timeout=2)
                for cpid in pg.stdout.split():
                    try:
                        pids_to_check.append(int(cpid))
                    except ValueError:
                        pass
            except Exception:
                pass

            is_agy = False
            cwd = "unknown"
            found_pid = pane_pid

            for p in pids_to_check:
                try:
                    with open(f"/proc/{p}/cmdline", "rb") as f:
                        cmdline = f.read().decode("utf-8", errors="ignore")
                        if "agy" in cmdline and "main.py" not in cmdline and "test_" not in cmdline:
                            is_agy = True
                            found_pid = p
                            try:
                                cwd = os.readlink(f"/proc/{p}/cwd")
                            except Exception:
                                pass
                            break
                except Exception:
                    pass

            # Exclude AIbot pane to prevent self-referencing loops
            if is_agy and win_name != "AIbot":
                parsed = self.inspect_pane(target)
                sessions.append({
                    "target": target,
                    "window_name": win_name,
                    "cwd": cwd,
                    "pid": found_pid,
                    "subagents_count": parsed.get("subagents_count", 0),
                    "model": parsed.get("model", ""),
                    "is_waiting_input": parsed.get("is_waiting_input", False),
                    "snippet": parsed.get("snippet", "")
                })

        return sessions

    def inspect_pane(self, target: str) -> Dict[str, Any]:
        """Captures recent lines of a tmux pane and extracts agent activity & status."""
        try:
            res = subprocess.run(
                ["tmux", "capture-pane", "-pt", target, "-S", "-40"],
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                return self.parse_pane_content(res.stdout)
        except Exception:
            pass
        return {
            "subagents_count": 0,
            "model": "",
            "is_waiting_input": False,
            "snippet": "",
            "main_text": ""
        }

    def parse_pane_content(self, text: str) -> Dict[str, Any]:
        """Parses raw terminal text from an agy session."""
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        subagents_count = 0
        m_sub = re.search(r'(\d+)\s+subagent\(s\)', text)
        if m_sub:
            subagents_count = int(m_sub.group(1))

        model = ""
        m_model = re.search(r'([A-Za-z0-9\.\s]+)\s*·\s*(?:low|medium|high)\s*·', text)
        if m_model:
            model = m_model.group(1).strip()

        # Check if waiting for input (prompt line `>` or question choices)
        is_waiting = False
        if any(l == ">" or l.startswith("> ") for l in lines[-6:]):
            is_waiting = True

        # Extract snippet or main message
        content_lines = []
        for l in lines:
            if not l.startswith("─") and not l.startswith("● Agent") and not "subagent(s)" in l and l != ">":
                content_lines.append(l)

        main_text = "\n".join(content_lines[-12:]) if content_lines else ""
        snippet = (main_text[:140] + "...") if len(main_text) > 140 else main_text

        # Detect multiple-choice numbers
        choices = []
        for l in content_lines[-15:]:
            m_ch = re.match(r'^[1-9]\.\s+(.*)', l)
            if m_ch:
                choices.append(l)

        return {
            "subagents_count": subagents_count,
            "model": model,
            "is_waiting_input": is_waiting,
            "snippet": snippet,
            "main_text": main_text,
            "choices": choices
        }

    def capture_session_screen(self, target: str, lines: int = 30) -> str:
        """Returns clean formatted terminal lines from target pane."""
        try:
            res = subprocess.run(
                ["tmux", "capture-pane", "-pt", target, "-S", f"-{lines}"],
                capture_output=True,
                text=True,
                timeout=3
            )
            if res.returncode == 0:
                raw = res.stdout.strip()
                # Clean trailing empty lines
                return raw
        except Exception as e:
            return f"Errore cattura schermo: {e}"
        return "Nessun output disponibile."

    def send_input(self, target: str, text: str) -> bool:
        """Sends text input and Enter key to the specified tmux pane."""
        if not self.is_tmux_available():
            return False
        try:
            res = subprocess.run(
                ["tmux", "send-keys", "-t", target, text, "Enter"],
                capture_output=True,
                text=True,
                timeout=3
            )
            return res.returncode == 0
        except Exception:
            return False

    COMPLETION_PATTERNS = [
        r"run has finished",
        r"run has completed",
        r"task completato",
        r"attività completat",
        r"all tasks complete",
        r"all deliverables ready",
        r"final project deliverables ready",
        r"progetto completato",
        r"richiesta completat",
        r"execution finished",
        r"execution complete"
    ]

    def check_for_session_events(self) -> List[Dict[str, Any]]:
        """
        Polls active agy sessions and returns notifications ONLY if:
        1. Agy asks to select an option / make a choice (event_type: 'question')
        2. The request is completely finished (event_type: 'completed')
        Filters out all intermediate logs, commands, and partial progress.
        """
        events = []
        sessions = self.discover_agy_sessions()

        for s in sessions:
            target = s["target"]
            win_name = s["window_name"]
            parsed = self.inspect_pane(target)

            main_text = parsed.get("main_text", "")
            is_waiting = parsed.get("is_waiting_input", False)
            choices = parsed.get("choices", [])
            content_hash = hashlib.md5(main_text.encode("utf-8")).hexdigest()

            prev_hash = self._last_content_hashes.get(target)
            prev_waiting = self._last_waiting_state.get(target, False)

            self._last_content_hashes[target] = content_hash
            self._last_waiting_state[target] = is_waiting

            # First run: establish baseline without spam
            if prev_hash is None:
                continue

            # If no change in content or state, skip
            if content_hash == prev_hash and not (is_waiting and not prev_waiting):
                continue

            lower_text = main_text.lower()
            is_completed = any(re.search(pat, lower_text) for pat in self.COMPLETION_PATTERNS)
            is_question = bool(choices) or (is_waiting and any(q_mark in main_text for q_mark in ["?", "Quale", "Scegli", "Come desideri", "[QUESTION:"]))

            if is_question:
                event_type = "question"
            elif is_completed:
                event_type = "completed"
            else:
                # Filter out regular intermediate progress updates!
                continue

            events.append({
                "target": target,
                "window_name": win_name,
                "cwd": s["cwd"],
                "event_type": event_type,
                "is_waiting_input": is_waiting,
                "subagents_count": parsed.get("subagents_count", 0),
                "main_text": main_text,
                "choices": choices,
                "snippet": parsed.get("snippet", "")
            })

        return events

    def format_sessions_card(self) -> str:
        """Builds an interactive Telegram Markdown card of all active agy sessions."""
        sessions = self.discover_agy_sessions()
        if not sessions:
            return (
                "🖥️ *Sessioni Antigravity su tmux*\n\n"
                "⚪ _Nessuna sessione `agy` attiva rilevata in tmux al momento._\n"
                "_(Puoi avviare Agy in qualsiasi finestra tmux sul PC e verrà rilevata automaticamente!)_"
            )

        lines = [
            f"🖥️ *Sessioni Antigravity su tmux ({len(sessions)} attive)*\n"
        ]

        for idx, s in enumerate(sessions, 1):
            sub_tag = f" • 🤖 `{s['subagents_count']} subagenti`" if s['subagents_count'] > 0 else ""
            status_tag = "⏳ In attesa input" if s["is_waiting_input"] else "⚙️ In elaborazione"
            lines.append(
                f"*{idx}. Finestra `{s['window_name']}`* (`{s['target']}`)\n"
                f"   • *Cartella:* `{s['cwd']}`\n"
                f"   • *Stato:* {status_tag}{sub_tag}\n"
            )
            if s["snippet"]:
                lines.append(f"   • _Output recente:_ {s['snippet']}\n")

        lines.append("💡 *Come interagire da Telegram:*")
        lines.append("• Usa i pulsanti inline per vedere lo schermo live o rispondere.")
        lines.append("• Oppure invia: `/send <nome_finestra> <testo/numero>` per inviare input!")

        return "\n".join(lines)
