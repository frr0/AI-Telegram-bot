import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger("history_manager")

HISTORY_FILE = Path(__file__).parent / ".chat_history.json"

class HistoryManager:
    def __init__(self, filepath: Optional[Path] = None):
        self.filepath = filepath or HISTORY_FILE
        self.active_session_id: str = datetime.now().strftime("session_%Y%m%d_%H%M%S")
        self.sessions: Dict[str, List[Dict[str, Any]]] = {}
        self.load()

    def load(self):
        if not self.filepath.exists():
            self.sessions[self.active_session_id] = []
            return
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.active_session_id = data.get("active_session_id", self.active_session_id)
                self.sessions = data.get("sessions", {})
                if self.active_session_id not in self.sessions:
                    self.sessions[self.active_session_id] = []
        except Exception as e:
            logger.error(f"Error loading chat history from {self.filepath}: {e}")
            if self.active_session_id not in self.sessions:
                self.sessions[self.active_session_id] = []

    def save(self):
        try:
            data = {
                "active_session_id": self.active_session_id,
                "sessions": self.sessions
            }
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving chat history to {self.filepath}: {e}")

    def add_turn(self, prompt: str, response: str, model: str = ""):
        if self.active_session_id not in self.sessions:
            self.sessions[self.active_session_id] = []

        turn_index = len(self.sessions[self.active_session_id]) + 1
        turn_data = {
            "turn": turn_index,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "prompt": prompt,
            "response": response,
            "model": model
        }
        self.sessions[self.active_session_id].append(turn_data)
        self.save()

    def new_session(self) -> str:
        """Starts a new session and returns the new session ID."""
        self.active_session_id = datetime.now().strftime("session_%Y%m%d_%H%M%S")
        self.sessions[self.active_session_id] = []
        self.save()
        return self.active_session_id

    def get_current_turns(self) -> List[Dict[str, Any]]:
        return self.sessions.get(self.active_session_id, [])

    def get_last_turn(self) -> Optional[Dict[str, Any]]:
        turns = self.get_current_turns()
        return turns[-1] if turns else None

    def format_history_summary(self, max_turns: int = 6) -> str:
        turns = self.get_current_turns()
        if not turns:
            return "📭 *Nessun messaggio nella sessione corrente.*\nPuoi inviare un prompt per iniziare!"

        total = len(turns)
        shown_turns = turns[-max_turns:]

        lines = [
            f"📜 *Cronologia Chat Corrente* ({total} turn{'i' if total > 1 else 'o'} total{'i' if total > 1 else 'e'})",
            ""
        ]

        if total > max_turns:
            lines.append(f"_...visualizzati gli ultimi {max_turns} di {total} turni..._\n")

        for t in shown_turns:
            t_num = t.get("turn", 1)
            t_time = t.get("timestamp", "")
            prompt = t.get("prompt", "")
            resp = t.get("response", "")

            # Truncate prompt if long
            p_snippet = prompt if len(prompt) <= 120 else prompt[:117] + "..."
            p_snippet = p_snippet.replace("\n", " ")

            # Truncate response if long
            r_snippet = resp if len(resp) <= 140 else resp[:137] + "..."
            r_snippet = r_snippet.replace("\n", " ")

            lines.append(f"*{t_num}.* 🕒 `{t_time}`")
            lines.append(f"👤 *Tu:* {p_snippet}")
            lines.append(f"🤖 *AI:* {r_snippet}\n")

        return "\n".join(lines).strip()

    def clear_active_session(self):
        if self.active_session_id in self.sessions:
            self.sessions[self.active_session_id] = []
            self.save()
