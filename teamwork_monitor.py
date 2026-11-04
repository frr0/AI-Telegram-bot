import glob
import hashlib
import os
import re
from typing import Dict, Any, List, Optional, Tuple


class TeamworkMonitor:
    """
    Monitors active multi-agent Teamwork projects (e.g. ~/Progetti/News).
    Tracks orchestrator progress, completed surveys, milestones, and generates notifications.
    """

    def __init__(self, news_path: str = "/home/francesco/Progetti/News"):
        self.news_path = os.path.abspath(os.path.expanduser(news_path))
        self.teamwork_dir = os.path.join(self.news_path, ".agents", "teamwork")
        self.progress_file = os.path.join(self.teamwork_dir, "orchestrator", "progress.md")
        self.project_file = os.path.join(self.teamwork_dir, "PROJECT.md")
        self._last_state_hash: Optional[str] = None
        self._known_handoffs = set()
        self._last_progress_snippet: str = ""

    def is_active(self) -> bool:
        return os.path.exists(self.progress_file)

    def get_status(self) -> Dict[str, Any]:
        """Parses current progress and subagents from teamwork directory."""
        if not os.path.exists(self.teamwork_dir):
            return {
                "is_active": False,
                "current_status_lines": [],
                "handoff_count": 0,
                "handoff_names": [],
                "active_subagents": [],
                "iteration": "0 / 32"
            }

        handoffs = glob.glob(os.path.join(self.teamwork_dir, "*", "handoff.md"))
        handoff_names = [os.path.basename(os.path.dirname(h)) for h in handoffs]

        status_lines = []
        active_subagents = []
        iteration = "N/D"

        if os.path.exists(self.progress_file):
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    content = f.read()

                # Extract status items
                in_status = False
                in_subagents = False
                for line in content.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("## Current Status"):
                        in_status = True
                        in_subagents = False
                        continue
                    elif stripped.startswith("## Iteration Status"):
                        in_status = False
                        continue
                    elif stripped.startswith("## Active Subagents"):
                        in_status = False
                        in_subagents = True
                        continue
                    elif stripped.startswith("## "):
                        in_status = False
                        in_subagents = False

                    if in_status and stripped.startswith(("- [", "  -")):
                        status_lines.append(stripped)
                    elif in_subagents and stripped.startswith("- "):
                        active_subagents.append(stripped)

                    if "Current iteration:" in stripped:
                        m = re.search(r'Current iteration:\s*(\d+\s*/\s*\d+)', stripped)
                        if m:
                            iteration = m.group(1)
            except Exception:
                pass

        has_project_file = os.path.exists(self.project_file)

        return {
            "is_active": True,
            "current_status_lines": status_lines,
            "handoff_count": len(handoffs),
            "handoff_names": handoff_names,
            "active_subagents": active_subagents,
            "iteration": iteration,
            "has_project_file": has_project_file
        }

    def format_news_card(self) -> str:
        """Builds a rich Telegram Markdown card for the News Teamwork project."""
        st = self.get_status()
        if not st["is_active"]:
            return "📰 *Progetti/News:* Nessuna sessione Teamwork attiva al momento."

        lines = [
            "📰 *Monitor Teamwork: `~/Progetti/News`*",
            "",
            "🎯 *Obiettivo:* Creazione app di notizie cross-platform (Web + Android con Capacitor) con 5 categorie (*Mondo, Italia, Sport, Finanza, Tech*) e aggregazione feed/API gratuite.",
            "",
            f"🔄 *Iterazione:* `{st['iteration']}` • *Survey Handoff:* `{st['handoff_count']} consegnati`",
            "",
            "📋 *Stato di Avanzamento Attuale:*"
        ]

        if st["current_status_lines"]:
            for sl in st["current_status_lines"][:10]:
                lines.append(sl)
        else:
            lines.append("• _Inizializzazione completata, indagini in corso..._")

        if st["handoff_names"]:
            lines.append("\n📦 *Report Handoff Ricevuti:*")
            for hn in st["handoff_names"]:
                lines.append(f"  • ✅ `{hn}`")

        if st["active_subagents"]:
            lines.append("\n🤖 *Subagenti Attivi:*")
            for sa in st["active_subagents"]:
                lines.append(f"  • {sa}")

        lines.append("\n💡 _Questo monitor invia automaticamente una notifica push appena gli agenti completano un nuovo milestone o producono modifiche._")
        return "\n".join(lines)

    def check_for_updates(self) -> Tuple[bool, str]:
        """
        Detects if there has been a state transition or updated handoff.
        Returns (has_update, update_description).
        """
        if not os.path.exists(self.teamwork_dir):
            return False, ""

        # Collect state fingerprint
        fingerprint_parts = []
        if os.path.exists(self.progress_file):
            try:
                mtime = os.path.getmtime(self.progress_file)
                size = os.path.getsize(self.progress_file)
                fingerprint_parts.append(f"prog:{mtime}:{size}")
            except Exception:
                pass

        if os.path.exists(self.project_file):
            try:
                mtime = os.path.getmtime(self.project_file)
                size = os.path.getsize(self.project_file)
                fingerprint_parts.append(f"proj:{mtime}:{size}")
            except Exception:
                pass

        handoffs = glob.glob(os.path.join(self.teamwork_dir, "*", "handoff.md"))
        for h in sorted(handoffs):
            try:
                mtime = os.path.getmtime(h)
                fingerprint_parts.append(f"{os.path.basename(os.path.dirname(h))}:{mtime}")
            except Exception:
                pass

        current_hash = hashlib.md5("".join(fingerprint_parts).encode("utf-8")).hexdigest()

        if self._last_state_hash is None:
            # Baseline initialization
            self._last_state_hash = current_hash
            self._known_handoffs = set(handoffs)
            return False, ""

        if current_hash != self._last_state_hash:
            self._last_state_hash = current_hash

            # Check if project has reached full completion
            is_final_complete = False
            desc = ""
            if os.path.exists(self.progress_file):
                try:
                    with open(self.progress_file, "r", encoding="utf-8") as f:
                        content = f.read()
                    if "- [x] Final project deliverables ready" in content or "all deliverables ready" in content.lower():
                        is_final_complete = True
                        desc = "🎉 *Progetto News Completato!* Tutti i milestone e i deliverables finali sono pronti."
                except Exception:
                    pass

            if is_final_complete:
                return True, desc

            # Intermediate progress (scaffolding, builds, subagent handoffs): do not alert
            return False, ""

        return False, ""
