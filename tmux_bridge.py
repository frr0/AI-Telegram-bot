import hashlib
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
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

    def discover_sessions(self, only_agy: bool = False, include_aibot: bool = False) -> List[Dict[str, Any]]:
        """
        Scans all tmux panes across all sessions.
        If only_agy is True, only returns panes running agy.
        If include_aibot is False, excludes AIbot pane.
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
            session_name = target.split(":")[0] if ":" in target else target
            try:
                pane_pid = int(pid_str)
            except ValueError:
                continue

            if not include_aibot and win_name == "AIbot":
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

            if cwd == "unknown":
                try:
                    cwd = os.readlink(f"/proc/{pane_pid}/cwd")
                except Exception:
                    pass

            if only_agy and not is_agy:
                continue

            parsed = self.inspect_pane(target) if is_agy else {}
            sessions.append({
                "target": target,
                "session_name": session_name,
                "name": session_name,
                "window_name": win_name,
                "command": cmd,
                "cwd": cwd,
                "pid": found_pid,
                "is_agy": is_agy,
                "subagents_count": parsed.get("subagents_count", 0),
                "model": parsed.get("model", ""),
                "requires_intervention": parsed.get("requires_intervention", False),
                "is_completed": parsed.get("is_completed", False),
                "snippet": parsed.get("snippet", "")
            })

        return sessions

    def discover_agy_sessions(self) -> List[Dict[str, Any]]:
        """Scans all tmux panes and returns only those running agy."""
        return self.discover_sessions(only_agy=True, include_aibot=False)

    def discover_all_sessions(self, include_aibot: bool = True) -> List[Dict[str, Any]]:
        """Scans all tmux panes across all sessions (both agy and generic)."""
        return self.discover_sessions(only_agy=False, include_aibot=include_aibot)

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

        # If choices are present, verify that they are preceded by a genuine question and not an informational list of future steps
        preceding_text = " ".join(above_prompt).lower()
        is_informational_list = any(inf in preceding_text for inf in [
            "ecco", "dettagli", "modifiche", "novità", "passi", "step", "fasi",
            "task", "elenco", "changelog", "riepilogo", "prossimi", "azioni da",
            "cose da fare", "attività previste"
        ])

        has_actual_question = has_question_tag or ("?" in " ".join(above_prompt))
        if is_informational_list and not has_question_tag and not ("?" in preceding_text):
            choices = []
            requires_intervention = False
        else:
            requires_intervention = (bool(choices) and has_actual_question) or (has_question_tag and idx_prompt != -1)

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

    def send_key(self, target: str, key: str) -> bool:
        """Sends a single key or shortcut (e.g. 'C-c', 'Enter', 'Escape') to the specified tmux pane."""
        if not self.is_tmux_available():
            return False
        try:
            res = subprocess.run(
                ["tmux", "send-keys", "-t", target, key],
                capture_output=True,
                text=True,
                timeout=3
            )
            return res.returncode == 0
        except Exception:
            return False

    def kill_session(self, target: str) -> bool:
        """Terminates a tmux session."""
        if not self.is_tmux_available():
            return False
        if not target:
            return False
        target_str = str(target)
        session_name = target_str.split(":")[0] if ":" in target_str else target_str
        try:
            res = subprocess.run(
                ["tmux", "kill-session", "-t", session_name],
                capture_output=True,
                text=True,
                timeout=3
            )
            return res.returncode == 0
        except Exception:
            return False

    def create_session(
        self,
        session_name: Optional[str] = None,
        cwd: Optional[str] = None,
        window_name: str = "agy",
        command: Optional[str] = None
    ) -> Optional[str]:
        """
        Creates a new tmux session running an agy instance in the background.
        Returns the session name if created successfully, or None on failure.
        """
        if not self.is_tmux_available():
            return None

        if not session_name:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            session_name = f"chat_{timestamp}"

        # Clean session name: allow only alphanumeric, underscores, hyphens
        clean_name = re.sub(r'[^a-zA-Z0-9_\-]', '_', session_name).strip("-_")
        if not clean_name:
            clean_name = f"chat_{int(time.time())}"

        if not command:
            agy_cmd = shutil.which("agy") or os.path.expanduser("~/.local/bin/agy") or "agy"
            command = f"{agy_cmd}; exec ${{SHELL:-bash}}"

        cmd = ["tmux", "new-session", "-d", "-s", clean_name, "-n", window_name]
        if cwd and os.path.isdir(cwd):
            cmd.extend(["-c", cwd])
        cmd.append(command)

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                return clean_name
            # If session name already exists, append unique timestamp suffix
            if "duplicate session" in res.stderr.lower():
                clean_name = f"{clean_name}_{int(time.time())}"
                cmd[cmd.index("-s") + 1] = clean_name
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if res.returncode == 0:
                    return clean_name
        except Exception:
            pass

        return None

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
            target = s.get("target")
            if not target:
                continue
            target_str = str(target)
            win_name = str(s.get("window_name") or s.get("name") or target_str)
            cwd = str(s.get("cwd") or "unknown")
            parsed = self.inspect_pane(target_str)

            raw_sub = parsed.get("subagents_count")
            try:
                subagents_count = int(raw_sub) if raw_sub is not None else 0
            except (ValueError, TypeError):
                subagents_count = 0
            requires_intervention = bool(parsed.get("requires_intervention", False))
            is_completed = bool(parsed.get("is_completed", False))
            choices = parsed.get("choices") or []
            snippet = str(parsed.get("snippet") or "")

            # Track busy state
            if subagents_count > 0:
                self._was_busy[target_str] = True
                self._notified_completions.discard(target_str)

            # Case 1: Requires user intervention
            if requires_intervention:
                q_hash = hashlib.md5((snippet + "".join(str(c) for c in choices)).encode("utf-8")).hexdigest()
                if self._notified_questions.get(target_str) != q_hash:
                    self._notified_questions[target_str] = q_hash
                    events.append({
                        "target": target_str,
                        "window_name": win_name,
                        "cwd": cwd,
                        "event_type": "question",
                        "requires_intervention": True,
                        "subagents_count": subagents_count,
                        "choices": choices,
                        "snippet": snippet
                    })
                continue

            # Case 2: All agents have finished the entire work
            agents_finished = bool(self._was_busy.get(target_str, False) and subagents_count == 0)
            if is_completed or agents_finished:
                if target_str not in self._notified_completions:
                    self._notified_completions.add(target_str)
                    self._was_busy[target_str] = False
                    events.append({
                        "target": target_str,
                        "window_name": win_name,
                        "cwd": cwd,
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

    def format_sessions_card(self, only_agy: bool = True) -> str:
        """Builds an interactive Telegram Markdown card of tmux sessions."""
        sessions = self.discover_sessions(only_agy=only_agy, include_aibot=not only_agy)
        if not sessions:
            if only_agy:
                return (
                    "🖥️ *Sessioni Antigravity su tmux*\n\n"
                    "⚪ _Nessuna sessione `agy` attiva rilevata in tmux al momento._\n"
                    "_(Puoi avviare Agy in qualsiasi finestra tmux sul PC e verrà rilevata automaticamente!)_"
                )
            else:
                return (
                    "🖥️ *Sessioni Tmux*\n\n"
                    "⚪ _Nessuna sessione tmux attiva rilevata al momento._"
                )

        header_title = "Sessioni Antigravity su tmux" if only_agy else "Tutte le Sessioni Tmux"
        lines = [
            f"🖥️ *{header_title} ({len(sessions)} attive)*\n"
        ]

        for idx, s in enumerate(sessions, 1):
            if s.get("is_agy"):
                raw_sub = s.get("subagents_count")
                try:
                    subagents_count = int(raw_sub) if raw_sub is not None else 0
                except (ValueError, TypeError):
                    subagents_count = 0
                requires_intervention = bool(s.get("requires_intervention", False))
                window_name = str(s.get("window_name") or s.get("name") or "session")
                target = str(s.get("target") or "")
                cwd = str(s.get("cwd") or "unknown")
                snippet = str(s.get("snippet") or "")

                sub_tag = f" • 🤖 `{subagents_count} subagenti`" if subagents_count > 0 else ""
                status_tag = "⏳ Richiede tuo intervento" if requires_intervention else "⚙️ Al lavoro in background"
                lines.append(
                    f"*{idx}. 🪐 Finestra `{window_name}`* (`{target}`)\n"
                    f"   • *Cartella:* `{cwd}`\n"
                    f"   • *Stato:* {status_tag}{sub_tag}\n"
                )
                if snippet:
                    lines.append(f"   • _Output recente:_ {snippet}\n")
            else:
                window_name = str(s.get("window_name") or s.get("name") or "session")
                target = str(s.get("target") or "")
                cwd = str(s.get("cwd") or "unknown")
                cmd_name = str(s.get("command") or "bash")
                pid = str(s.get("pid") if s.get("pid") is not None else "N/A")
                lines.append(
                    f"*{idx}. 💻 Finestra `{window_name}`* (`{target}`)\n"
                    f"   • *Cartella:* `{cwd}`\n"
                    f"   • *Comando:* `{cmd_name}` (PID: `{pid}`)\n"
                )

        if only_agy:
            lines.append("💡 *Notifiche:* Riceverai messaggi solo quando ti viene chiesta una scelta o tutti gli agenti hanno terminato.")
        else:
            lines.append("💡 *Usa i pulsanti o `/send <finestra> <testo>` per interagire con qualunque sessione.*")

        return "\n".join(lines)
