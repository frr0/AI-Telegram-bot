import hashlib
import os
import re
import shutil
import subprocess
from typing import Dict, Any, List, Optional, Tuple, Set


class TmuxBridge:
    """
    Bidirectional bridge between Telegram and Antigravity (agy) sessions running inside tmux.
    Discovers running agy instances across all tmux windows and delivers notifications ONLY IF:
    1. Agy requires user intervention (multiple choice / question asked).
    2. All agents for the request have completely finished their work.
    """

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

    def __init__(self):
        self._notified_questions: Dict[str, str] = {}
        self._notified_completions: Set[str] = set()
        self._was_busy: Dict[str, bool] = {}

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
                    "requires_intervention": parsed.get("requires_intervention", False),
                    "is_completed": parsed.get("is_completed", False),
                    "snippet": parsed.get("snippet", "")
                })

        return sessions

    def inspect_pane(self, target: str) -> Dict[str, Any]:
        """Captures recent lines of a tmux pane and extracts agent activity & status."""
        try:
            res = subprocess.run(
                ["tmux", "capture-pane", "-pt", target, "-S", "-35"],
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
            "requires_intervention": False,
            "is_completed": False,
            "snippet": "",
            "main_text": "",
            "choices": []
        }

    def parse_pane_content(self, text: str) -> Dict[str, Any]:
        """Parses raw terminal text from an agy session."""
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # 1. Count subagents
        subagents_count = 0
        m_sub = re.search(r'(\d+)\s+subagent\(s\)', text)
        if m_sub:
            subagents_count = int(m_sub.group(1))

        # 2. Model
        model = ""
        m_model = re.search(r'([A-Za-z0-9\.\s]+)\s*·\s*(?:low|medium|high)\s*·', text)
        if m_model:
            model = m_model.group(1).strip()

        # 3. Locate prompt line `>`
        idx_prompt = -1
        for i in range(len(lines) - 1, -1, -1):
            if lines[i] == ">" or lines[i].startswith("> "):
                idx_prompt = i
                break

        above_prompt = lines[max(0, idx_prompt - 10):idx_prompt] if idx_prompt != -1 else lines[-10:]

        # 4. Check for active numbered choices immediately above prompt
        choices = []
        has_question_tag = False
        for l in above_prompt:
            m_ch = re.match(r'^(?:[1-9]\.|\([a-z]\))\s+(.*)', l)
            if m_ch:
                choices.append(l)
            lower_line = l.lower()
            if any(k in lower_line for k in ["[question:", "come desideri", "quale opzione", "[y/n]", "(y/n)", "(yes/no)"]) or l.startswith("? "):
                has_question_tag = True

        # Provide yes/no choices if confirmation prompt detected
        if not choices and any(yn in " ".join(above_prompt).lower() for yn in ["[y/n]", "(y/n)", "(yes/no)"]):
            choices = ["1. Yes (y)", "2. No (n)"]

        # Requires intervention ONLY if explicit choices or question is waiting above prompt
        requires_intervention = bool(choices) or (has_question_tag and idx_prompt != -1)

        # 5. Check if complete
        is_completed = False
        if subagents_count == 0:
            above_text = " ".join(above_prompt).lower()
            if any(re.search(pat, above_text) for pat in self.COMPLETION_PATTERNS):
                is_completed = True

        # Extract main text snippet
        content_lines = []
        for l in lines:
            if not l.startswith("─") and not l.startswith("● Agent") and not "subagent(s)" in l and l != ">":
                content_lines.append(l)

        main_text = "\n".join(content_lines[-10:]) if content_lines else ""
        snippet = (main_text[:140] + "...") if len(main_text) > 140 else main_text

        return {
            "subagents_count": subagents_count,
            "model": model,
            "requires_intervention": requires_intervention,
            "is_completed": is_completed,
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
                return res.stdout.strip()
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

    def check_for_session_events(self) -> List[Dict[str, Any]]:
        """
        Polls active agy sessions and returns notifications ONLY IF:
        1. Agy asks to select an option / make a choice (event_type: 'question')
        2. All agents have finished the entire work (event_type: 'completed')
        Filters out all intermediate logs, commands, and partial progress.
        """
        events = []
        sessions = self.discover_agy_sessions()

        for s in sessions:
            target = s["target"]
            win_name = s["window_name"]
            parsed = self.inspect_pane(target)

            subagents_count = parsed.get("subagents_count", 0)
            requires_intervention = parsed.get("requires_intervention", False)
            is_completed = parsed.get("is_completed", False)
            choices = parsed.get("choices", [])
            snippet = parsed.get("snippet", "")

            # Track busy state
            if subagents_count > 0:
                self._was_busy[target] = True
                self._notified_completions.discard(target)

            # Case 1: Requires user intervention
            if requires_intervention:
                q_hash = hashlib.md5((snippet + "".join(choices)).encode("utf-8")).hexdigest()
                if self._notified_questions.get(target) != q_hash:
                    self._notified_questions[target] = q_hash
                    events.append({
                        "target": target,
                        "window_name": win_name,
                        "cwd": s["cwd"],
                        "event_type": "question",
                        "requires_intervention": True,
                        "subagents_count": subagents_count,
                        "choices": choices,
                        "snippet": snippet
                    })
                continue

            # Case 2: All agents have finished the entire work
            agents_finished = bool(self._was_busy.get(target, False) and subagents_count == 0)
            if is_completed or agents_finished:
                if target not in self._notified_completions:
                    self._notified_completions.add(target)
                    self._was_busy[target] = False
                    events.append({
                        "target": target,
                        "window_name": win_name,
                        "cwd": s["cwd"],
                        "event_type": "completed",
                        "requires_intervention": False,
                        "subagents_count": 0,
                        "choices": [],
                        "snippet": snippet
                    })
                continue

            # In all other cases (running, intermediate progress, ordinary output): ZERO NOTIFICATIONS
            continue

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
            status_tag = "⏳ Richiede tuo intervento" if s["requires_intervention"] else "⚙️ Al lavoro in background"
            lines.append(
                f"*{idx}. Finestra `{s['window_name']}`* (`{s['target']}`)\n"
                f"   • *Cartella:* `{s['cwd']}`\n"
                f"   • *Stato:* {status_tag}{sub_tag}\n"
            )
            if s["snippet"]:
                lines.append(f"   • _Output recente:_ {s['snippet']}\n")

        lines.append("💡 *Notifiche:* Riceverai messaggi solo quando ti viene chiesta una scelta o tutti gli agenti hanno terminato.")

        return "\n".join(lines)
