import asyncio
import fcntl
import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Optional, Dict, List, Any

from config import Config
from telegram_client import TelegramClient
from agy_engine import AgyEngine, KNOWN_MODELS, normalize_model_name
from terminal_ui import TerminalUI
from question_parser import QuestionParser, QuestionChoice
from history_manager import HistoryManager
from power_monitor import get_power_report
from screen_manager import set_screen_power, lock_screen, unlock_screen
from media_manager import MediaManager
from hardware_monitor import HardwareMonitor
from teamwork_monitor import TeamworkMonitor
from tmux_bridge import TmuxBridge
from notification_manager import (
    NotificationManager,
    build_completion_header,
    format_duration
)
from agy_manager import (
    get_usage_report,
    get_credits_report,
    get_mcp_report,
    get_skills_report,
    get_agents_report,
    get_effort_report,
    get_workflow_help
)

# Setup basic logging
_handlers: List[logging.Handler] = [logging.StreamHandler()]
try:
    _handlers.append(logging.FileHandler("aibot.log", encoding="utf-8"))
except Exception:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_handlers
)
logger = logging.getLogger("aibot")

class BotApp:
    def __init__(self):
        self.config = Config()
        self.ui = TerminalUI()
        self.client: Optional[TelegramClient] = None
        self.engine: Optional[AgyEngine] = None
        self.history = HistoryManager()
        self.notifier = NotificationManager()
        self.media = MediaManager()
        self.hw = HardwareMonitor()
        self.news_monitor = TeamworkMonitor()
        self.tmux = TmuxBridge()
        self.active_tmux_target: Optional[str] = None
        self.active_tmux_name: Optional[str] = None
        self.startup_time = time.time()
        self.lock_file = None

        self.last_chat_id: Optional[int] = None
        self.pending_choices: Dict[int, str] = {} # choice_index -> choice_value
        self.is_processing = False
        self._shutdown_event = asyncio.Event()
        self.teamwork_watcher_task: Optional[asyncio.Task] = None
        self.current_reply_menu: str = "main"

    def _load_telegram_offset(self) -> int:
        """Loads the last processed Telegram update_id offset from disk."""
        try:
            if os.path.exists(".telegram_offset"):
                with open(".telegram_offset", "r") as f:
                    content = f.read().strip()
                    if content.isdigit():
                        return int(content)
        except Exception:
            pass
        return 0

    def _save_telegram_offset(self, offset: int):
        """Persists the Telegram update_id offset to avoid redelivering processed messages."""
        try:
            with open(".telegram_offset", "w") as f:
                f.write(str(offset))
        except Exception:
            pass

    def get_reply_keyboard(self, menu: Optional[str] = None) -> Dict[str, Any]:
        """Persistent bottom keyboard for Telegram on mobile/desktop with sub-menus."""
        if not self.client:
            return {}
        if getattr(self, "active_tmux_target", None):
            return self.client.build_reply_keyboard([
                ["👁️ Schermo", "🔄 Aggiorna"],
                ["🛑 Ctrl+C", "⏎ Invio"],
                ["1", "2", "3", "y", "n"],
                ["🔙 Sgancia (Torna al Bot)"]
            ], resize_keyboard=True, is_persistent=True)

        if menu:
            self.current_reply_menu = menu
        active_menu = getattr(self, "current_reply_menu", "main")

        if active_menu == "media":
            return self.client.build_reply_keyboard([
                ["⏮️ Prec", "⏯️ Play/Pausa", "⏭️ Succ"],
                ["🔉 Vol -", "🔊 Vol +", "🔇 Muto"],
                ["🔄 Aggiorna Media", "🔙 Menu Principale"]
            ], resize_keyboard=True, is_persistent=True)

        elif active_menu == "screen":
            return self.client.build_reply_keyboard([
                ["💡 Accendi Schermo", "💤 Spegni Schermo"],
                ["🔒 Blocca PC", "🔓 Sblocca PC"],
                ["🔙 Menu Principale"]
            ], resize_keyboard=True, is_persistent=True)

        elif active_menu in ["system", "hardware"]:
            return self.client.build_reply_keyboard([
                ["📊 Hardware", "⚡ Consumi"],
                ["📜 Cronologia", "📊 Stato"],
                ["🔄 Aggiorna Metriche", "🔙 Menu Principale"]
            ], resize_keyboard=True, is_persistent=True)

        elif active_menu == "agy":
            return self.client.build_reply_keyboard([
                ["📊 Quota Usage", "💳 Crediti G1"],
                ["🔌 MCP Servers", "⚡ Skills", "🤖 Agenti"],
                ["🧠 Modello AI", "🔙 Menu Principale"]
            ], resize_keyboard=True, is_persistent=True)

        elif active_menu == "tmux":
            return self.client.build_reply_keyboard([
                ["🖥️ Sessioni Agy", "💻 Tutte le Sessioni"],
                ["📰 Teamwork News", "🔙 Menu Principale"]
            ], resize_keyboard=True, is_persistent=True)

        # Default main menu
        self.current_reply_menu = "main"
        return self.client.build_reply_keyboard([
            ["🛑 Stop", "➕ Nuova Chat"],
            ["🎵 Media", "🖥️ Schermo"],
            ["📊 Sistema", "🎛️ Opzioni Agy"],
            ["🖥️ Tmux Agy", "📋 Menu"]
        ], resize_keyboard=True, is_persistent=True)

    def get_inline_menu(self) -> Dict[str, Any]:
        """Interactive inline menu attached to messages."""
        return self.client.build_inline_keyboard([
            [
                {"text": "🛑 Stop", "callback_data": "menu:stop"},
                {"text": "➕ Nuova Chat", "callback_data": "menu:new"}
            ],
            [
                {"text": "📜 Cronologia", "callback_data": "menu:history"},
                {"text": "📊 Stato", "callback_data": "menu:status"}
            ],
            [
                {"text": "🎵 Media & Volume", "callback_data": "menu:media"},
                {"text": "📊 Monitor Hardware", "callback_data": "menu:hardware"}
            ],
            [
                {"text": "🖥️ Sessioni Agy su tmux", "callback_data": "menu:tmux"},
                {"text": "📰 Teamwork News", "callback_data": "menu:news"}
            ],
            [
                {"text": "⚡ Consumi", "callback_data": "menu:power"},
                {"text": "🧠 Seleziona Modello", "callback_data": "menu:models"}
            ],
            [
                {"text": "💤 Spegni Schermo", "callback_data": "menu:screen_off"},
                {"text": "💡 Accendi Schermo", "callback_data": "menu:screen_on"}
            ],
            [
                {"text": "🔔 Impostazioni Notifiche", "callback_data": "menu:notifications"}
            ],
            [
                {"text": "🎛️ Tutte le Opzioni da Terminale Agy", "callback_data": "agy:menu"}
            ]
        ])

    def get_notification_keyboard(self) -> Dict[str, Any]:
        """Interactive inline keyboard for desktop and audio notifications."""
        sound_status = "🔊 Suono: ON" if self.notifier.sound_alerts else "🔇 Suono: OFF"
        desktop_status = "🖥️ Desktop: ON" if self.notifier.desktop_notifications else "⚪ Desktop: OFF"
        return self.client.build_inline_keyboard([
            [
                {"text": desktop_status, "callback_data": "notify:toggle_desktop"},
                {"text": sound_status, "callback_data": "notify:toggle_sound"}
            ],
            [
                {"text": "🔙 Torna al Menu", "callback_data": "menu:main"}
            ]
        ])

    def get_media_keyboard(self) -> Dict[str, Any]:
        """Interactive inline keyboard for media player and volume controls."""
        return self.client.build_inline_keyboard([
            [
                {"text": "⏮️ Prec", "callback_data": "media:prev"},
                {"text": "⏯️ Play/Pausa", "callback_data": "media:playpause"},
                {"text": "⏭️ Succ", "callback_data": "media:next"}
            ],
            [
                {"text": "🔉 Vol -5%", "callback_data": "media:voldn"},
                {"text": "🔇 Muto", "callback_data": "media:mute"},
                {"text": "🔊 Vol +5%", "callback_data": "media:volup"}
            ],
            [
                {"text": "🔄 Aggiorna", "callback_data": "media:refresh"},
                {"text": "🔙 Torna al Menu", "callback_data": "menu:main"}
            ]
        ])

    def get_hardware_keyboard(self) -> Dict[str, Any]:
        """Interactive inline keyboard for hardware monitor."""
        return self.client.build_inline_keyboard([
            [
                {"text": "🔄 Aggiorna Metriche", "callback_data": "hw:refresh"},
                {"text": "🔙 Torna al Menu", "callback_data": "menu:main"}
            ]
        ])

    def get_agy_options_keyboard(self) -> Dict[str, Any]:
        """Interactive inline keyboard with all Agy terminal options and workflows."""
        return self.client.build_inline_keyboard([
            [
                {"text": "📊 Quota (Usage)", "callback_data": "agy:usage"},
                {"text": "💳 Crediti G1", "callback_data": "agy:credits"}
            ],
            [
                {"text": "🔌 Server MCP", "callback_data": "agy:mcp"},
                {"text": "⚡ Skills", "callback_data": "agy:skills"}
            ],
            [
                {"text": "👥 Teamwork", "callback_data": "agy:teamwork"},
                {"text": "🎯 Goal Mode", "callback_data": "agy:goal"}
            ],
            [
                {"text": "📝 Plan Mode", "callback_data": "agy:plan"},
                {"text": "🚀 Boost Mode", "callback_data": "agy:boost"}
            ],
            [
                {"text": "🌐 Browser Web", "callback_data": "agy:browser"},
                {"text": "🗜️ Compact", "callback_data": "agy:compact"}
            ],
            [
                {"text": "🧑‍🏫 Grill Me", "callback_data": "agy:grill_me"},
                {"text": "💡 Learn", "callback_data": "agy:learn"}
            ],
            [
                {"text": "⚙️ Reasoning Effort", "callback_data": "agy:effort"},
                {"text": "🤖 Agenti", "callback_data": "agy:agents"}
            ],
            [
                {"text": "🔙 Torna al Menu", "callback_data": "menu:main"}
            ]
        ])

    def get_model_keyboard(self) -> Dict[str, Any]:
        """Interactive inline keyboard for selecting available AI models."""
        popular_models = [
            "Gemini 3.8 Flash (Medium)",
            "Gemini 4 Argon (Medium)",
            "Claude Sonnet 5.5 (Medium)",
            "Claude Opus 5.5 (Medium)",
            "GPT-OSS 120B (Medium)"
        ]
        rows = []
        for m in popular_models:
            is_active = "✅ " if m.lower() == self.engine.model.lower() else ""
            rows.append([{"text": f"{is_active}{m}", "callback_data": f"setmodel:{m}"}])
        rows.append([{"text": "🔙 Torna al Menu", "callback_data": "menu:main"}])
        return self.client.build_inline_keyboard(rows)

    async def initialize(self) -> bool:
        if not self.config.bot_token or "YOUR_TELEGRAM_BOT_TOKEN" in self.config.bot_token:
            self.ui.log_error("No Telegram Bot Token configured!")
            print("\nPlease open 'config.json' and paste your Telegram Bot Token:")
            print("  { \"bot_token\": \"1234567890:ABCdef...\" }")
            print("Or paste it directly here right now:")
            sys.stdout.write("Bot Token: ")
            sys.stdout.flush()
            token_input = sys.stdin.readline().strip()
            if token_input:
                self.config.bot_token = token_input
                self.config.save()
                self.ui.log_success("Saved bot token to config.json!")
            else:
                self.ui.log_error("No token provided. Exiting.")
                return False

        self.client = TelegramClient(self.config.bot_token)
        me = None
        for attempt in range(1, 6):
            me = await self.client.get_me()
            if me:
                break
            if attempt < 5:
                self.ui.log_warn(f"Failed to connect to Telegram (attempt {attempt}/5). Retrying in 2s...")
                await asyncio.sleep(2)

        if not me:
            self.ui.log_error("Failed to connect to Telegram. Please check your BOT TOKEN and internet connection.")
            if self.client:
                await self.client.close()
            return False

        bot_username = me.get("username", "UnknownBot")

        # Register official bot commands in Telegram (only if changed or not registered yet)
        bot_commands = [
            {"command": "menu", "description": "📋 Mostra il menu principale e pulsanti"},
            {"command": "stop", "description": "🛑 Ferma l'attività in corso"},
            {"command": "new", "description": "➕ Nuova chat / resetta sessione"},
            {"command": "history", "description": "📜 Mostra la cronologia della chat"},
            {"command": "status", "description": "📊 Visualizza lo stato del bot"},
            {"command": "power", "description": "⚡ Visualizza i consumi energetici e costi (PC & RPi0W)"},
            {"command": "screen", "description": "🖥️ Accendi o spegni lo schermo del PC (on / off)"},
            {"command": "media", "description": "🎵 Controlli multimediali e volume PC"},
            {"command": "hardware", "description": "📊 Monitor risorse hardware in tempo reale (CPU, RAM, Disco, Temp)"},
            {"command": "tmux", "description": "🖥️ Visualizza e interagisci con le sessioni Agy in tmux"},
            {"command": "send", "description": "✍️ Invia un input a una sessione tmux (/send <finestra> <testo>)"},
            {"command": "news", "description": "📰 Monitor del team di agenti in ~/Progetti/News"},
            {"command": "notify", "description": "🔔 Gestisci notifiche push e avvisi sonori/desktop"},
            {"command": "agy", "description": "🎛️ Opzioni e strumenti da terminale Agy (usage, mcp, teamwork, ecc)"},
            {"command": "usage", "description": "📊 Quote di utilizzo e limiti modelli Agy"},
            {"command": "credits", "description": "💳 Crediti G1 rimanenti"},
            {"command": "mcp", "description": "🔌 Server MCP configurati e attivi"},
            {"command": "skills", "description": "⚡ Skill disponibili in Antigravity"},
            {"command": "model", "description": "🧠 Visualizza o cambia il modello AI"}
        ]
        cmd_hash = hashlib.md5(json.dumps(bot_commands, sort_keys=True).encode()).hexdigest()
        prev_hash = ""
        try:
            if os.path.exists(".commands_hash"):
                with open(".commands_hash", "r") as f:
                    prev_hash = f.read().strip()
        except Exception:
            pass

        if cmd_hash != prev_hash:
            res = await self.client.set_my_commands(bot_commands)
            if res:
                try:
                    with open(".commands_hash", "w") as f:
                        f.write(cmd_hash)
                except Exception:
                    pass

        self.engine = AgyEngine(
            agy_path=self.config.agy_path,
            workspace_path=self.config.workspace_path,
            default_model=self.config.default_model,
            dangerously_skip_permissions=self.config.dangerously_skip_permissions
        )

        if self.history.get_current_turns():
            self.engine.conversation_id = "continue"
            last_turn = self.history.get_last_turn()
            if last_turn and last_turn.get("response"):
                q = QuestionParser.extract_question(last_turn["response"])
                if q:
                    for ch in q[1]:
                        self.pending_choices[ch.index] = ch.value

        self.ui.print_banner(
            bot_username=bot_username,
            model=self.config.default_model,
            workspace=self.config.workspace_path
        )

        if not self.config.allowed_user_ids:
            self.ui.log_warn("Security Notice: No Telegram user IDs paired yet.")
            self.ui.log_info(f"Open Telegram on your phone, find @{bot_username}, and send /start to pair.")
        else:
            self.ui.log_info(f"Authorized Telegram User IDs: {self.config.allowed_user_ids}")

        return True

    # --- Actions ---

    async def cmd_stop(self, chat_id: Optional[int] = None):
        """Stops the active agy turn."""
        target_chat = chat_id or self.last_chat_id
        if self.engine and self.engine.stop_current_task():
            self.ui.log_success("Stopped agent task by request.")
            if target_chat and self.client:
                await self.client.send_message(
                    target_chat,
                    "🛑 *Attività interrotta con successo.*",
                    reply_markup=self.get_reply_keyboard(),
                    parse_mode="Markdown"
                )
        else:
            self.ui.log_info("Stop requested, but no active task running.")
            if target_chat and self.client:
                await self.client.send_message(
                    target_chat,
                    "ℹ️ *Nessuna attività in corso da interrompere.*",
                    reply_markup=self.get_reply_keyboard(),
                    parse_mode="Markdown"
                )

    async def cmd_new(self, chat_id: Optional[int] = None):
        """Resets the conversation, creates a new tmux session, and starts a fresh session."""
        target_chat = chat_id or self.last_chat_id
        if self.engine:
            self.engine.reset_conversation()
        self.history.new_session()
        self.pending_choices.clear()

        # Create a new session on tmux for this chat
        tmux_session = None
        if self.tmux and self.tmux.is_tmux_available():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sname = f"chat_{ts}"
            tmux_session = await asyncio.to_thread(
                self.tmux.create_session,
                session_name=sname,
                cwd=self.config.workspace_path,
                window_name="agy"
            )

        log_extra = f" (tmux: {tmux_session})" if tmux_session else ""
        self.ui.log_success(f"Conversation reset. Started a fresh session{log_extra}.")
        if target_chat and self.client:
            msg = "🔄 *Nuova sessione di chat avviata!*\nLa conversazione precedente è stata archiviata."
            if tmux_session:
                msg += (
                    f"\n\n🖥️ *Nuova sessione tmux creata:* `{tmux_session}`\n"
                    f"• *Finestra:* `agy` (in `{self.config.workspace_path}`)\n"
                    f"• *Collegamento da PC:* `tmux a -t {tmux_session}`\n"
                    f"• *Da Telegram:* usa `/tmux` per visualizzare lo schermo e interagire."
                )
            else:
                msg += "\nPuoi iniziare con una nuova richiesta."

            await self.client.send_message(
                target_chat,
                msg,
                reply_markup=self.get_reply_keyboard(),
                parse_mode="Markdown"
            )

    async def cmd_history(self, chat_id: Optional[int] = None):
        """Displays chat history of the active session."""
        target_chat = chat_id or self.last_chat_id
        if not target_chat or not self.client:
            print(self.history.format_history_summary())
            return

        summary = self.history.format_history_summary()
        inline_rows = []
        if self.history.get_last_turn():
            inline_rows.append([{"text": "📄 Leggi ultima risposta completa", "callback_data": "history:last"}])
        inline_rows.append([
            {"text": "➕ Nuova Chat", "callback_data": "menu:new"},
            {"text": "🧹 Svuota", "callback_data": "history:clear"}
        ])

        reply_markup = self.client.build_inline_keyboard(inline_rows)
        await self.client.send_message(
            target_chat,
            summary,
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    async def cmd_status(self, chat_id: Optional[int] = None):
        """Displays current bot status."""
        target_chat = chat_id or self.last_chat_id
        status_text = "⏳ *In elaborazione...*" if self.is_processing else "🟢 *Pronto (In attesa)*"
        turns_count = len(self.history.get_current_turns())
        msg = (
            f"📊 *Stato Antigravity Bridge*\n\n"
            f"• *Stato:* {status_text}\n"
            f"• *Modello:* `{self.engine.model}`\n"
            f"• *Workspace:* `{self.config.workspace_path}`\n"
            f"• *Messaggi sessione:* `{turns_count}`\n"
            f"• *ID Sessione:* `{self.history.active_session_id}`"
        )
        if target_chat and self.client:
            await self.client.send_message(
                target_chat,
                msg,
                reply_markup=self.get_reply_keyboard(),
                parse_mode="Markdown"
            )
        else:
            self.ui.log_info(msg.replace("*", "").replace("`", ""))

    async def cmd_power(self, chat_id: Optional[int] = None):
        """Displays power consumption statistics and estimated costs."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(get_power_report)
        if target_chat and self.client:
            await self.client.send_message(
                target_chat,
                report,
                reply_markup=self.get_reply_keyboard(),
                parse_mode="Markdown"
            )
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_screen(self, arg: str = "", chat_id: Optional[int] = None):
        """Controls PC monitor power state (DPMS on / off)."""
        target_chat = chat_id or self.last_chat_id
        arg_clean = arg.strip().lower()

        if arg_clean in ["off", "0", "spento", "spegni"]:
            success, msg = await asyncio.to_thread(set_screen_power, False)
            if target_chat and self.client:
                await self.client.send_message(
                    target_chat,
                    msg,
                    reply_markup=self.get_reply_keyboard("screen"),
                    parse_mode="Markdown"
                )
            else:
                self.ui.log_info(msg.replace("*", "").replace("`", ""))
        elif arg_clean in ["on", "1", "acceso", "accendi"]:
            success, msg = await asyncio.to_thread(set_screen_power, True)
            if target_chat and self.client:
                await self.client.send_message(
                    target_chat,
                    msg,
                    reply_markup=self.get_reply_keyboard("screen"),
                    parse_mode="Markdown"
                )
            else:
                self.ui.log_info(msg.replace("*", "").replace("`", ""))
        elif arg_clean in ["unlock", "sblocca"]:
            success, msg = await asyncio.to_thread(unlock_screen)
            if target_chat and self.client:
                await self.client.send_message(
                    target_chat,
                    msg,
                    reply_markup=self.get_reply_keyboard("screen"),
                    parse_mode="Markdown"
                )
            else:
                self.ui.log_info(msg.replace("*", "").replace("`", ""))
        elif arg_clean in ["lock", "blocca"]:
            success, msg = await asyncio.to_thread(lock_screen)
            if target_chat and self.client:
                await self.client.send_message(
                    target_chat,
                    msg,
                    reply_markup=self.get_reply_keyboard("screen"),
                    parse_mode="Markdown"
                )
            else:
                self.ui.log_info(msg.replace("*", "").replace("`", ""))
        else:
            if target_chat and self.client:
                await self.client.send_message(
                    target_chat,
                    "🖥️ *Controllo Schermo PC*\nUsa i pulsanti in basso per controllare il display o bloccare la sessione:",
                    reply_markup=self.get_reply_keyboard("screen"),
                    parse_mode="Markdown"
                )
            else:
                self.ui.log_info("Use: /screen on|off|lock|unlock")

    async def cmd_models(self, chat_id: Optional[int] = None):
        """Displays model switcher."""
        target_chat = chat_id or self.last_chat_id
        if target_chat and self.client:
            await self.client.send_message(
                target_chat,
                f"🧠 *Selezione Modello AI*\nModello attualmente attivo: `{self.engine.model}`\n\nScegli un modello da applicare:",
                reply_markup=self.get_model_keyboard(),
                parse_mode="Markdown"
            )

    async def cmd_menu(self, chat_id: Optional[int] = None):
        """Displays the main interactive menu."""
        target_chat = chat_id or self.last_chat_id
        if target_chat and self.client:
            text = (
                "📋 *Pannello di Controllo Antigravity*\n\n"
                "Usa i pulsanti interattivi qui sotto o la tastiera fissata in basso:"
            )
            await self.client.send_message(
                target_chat,
                text,
                reply_markup=self.get_inline_menu(),
                parse_mode="Markdown"
            )

    async def cmd_notify(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Displays and configures notification settings."""
        target_chat = chat_id or self.last_chat_id
        text = self.notifier.get_status_report()
        kb = self.get_notification_keyboard() if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=text, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, text, reply_markup=kb, parse_mode="Markdown")
        else:
            self.ui.log_info(text.replace("*", ""))

    async def cmd_media(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Displays media player & volume controller."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(self.media.get_media_report)
        if target_chat and self.client:
            if message_id:
                kb = self.get_media_keyboard()
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, text=report, reply_markup=self.get_reply_keyboard("media"), parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_hardware(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Displays real-time hardware status."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(self.hw.get_hardware_report)
        if target_chat and self.client:
            if message_id:
                kb = self.get_hardware_keyboard()
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, text=report, reply_markup=self.get_reply_keyboard("system"), parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_news(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Displays live status of the ~/Progetti/News teamwork multi-agent session."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(self.news_monitor.format_news_card)
        kb = self.client.build_inline_keyboard([
            [{"text": "🔄 Aggiorna Stato News", "callback_data": "news:refresh"}],
            [{"text": "🔙 Torna al Menu", "callback_data": "menu:main"}]
        ]) if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, text=report, reply_markup=kb, parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    def _build_tmux_screen_keyboard(self, target: str) -> Dict[str, Any]:
        """Builds an interactive control keyboard for a tmux session screen."""
        if not self.client:
            return {}
        target_str = str(target or "")
        is_attached = bool(target_str and getattr(self, "active_tmux_target", None) == target_str)
        attach_btn = (
            {"text": "🔓 Sgancia", "callback_data": "tmux:detach"}
            if is_attached
            else {"text": "🎯 Aggancia e Scrivi", "callback_data": f"tmux:attach:{target}"}
        )
        return self.client.build_inline_keyboard([
            [
                {"text": "🔄 Aggiorna Schermo", "callback_data": f"tmux:screen:{target}"},
                attach_btn
            ],
            [
                {"text": "🛑 Ctrl+C", "callback_data": f"tmux:key:{target}:C-c"},
                {"text": "⏎ Invio", "callback_data": f"tmux:key:{target}:Enter"}
            ],
            [
                {"text": "1", "callback_data": f"tmux:send:{target}:1"},
                {"text": "2", "callback_data": f"tmux:send:{target}:2"},
                {"text": "3", "callback_data": f"tmux:send:{target}:3"},
                {"text": "y", "callback_data": f"tmux:send:{target}:y"},
                {"text": "n", "callback_data": f"tmux:send:{target}:n"}
            ],
            [
                {"text": "🗑️ Chiudi Sessione", "callback_data": f"tmux:kill_ask:{target}"},
                {"text": "🔙 Lista Sessioni", "callback_data": "menu:tmux"}
            ],
            [
                {"text": "🏠 Menu Principale", "callback_data": "menu:main"}
            ]
        ])

    async def cmd_tmux(self, chat_id: Optional[int] = None, message_id: Optional[int] = None, show_all: bool = False):
        """Displays running sessions in tmux with action buttons."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(self.tmux.format_sessions_card, only_agy=not show_all)
        sessions = await asyncio.to_thread(self.tmux.discover_sessions, only_agy=not show_all, include_aibot=show_all)

        keyboard_rows = []
        for s in sessions:
            target = str(s.get("target") or "")
            w_name = str(s.get("window_name") or s.get("name") or target or "tmux")
            icon = "🪐" if s.get("is_agy") else "💻"
            is_active = bool(target and getattr(self, "active_tmux_target", None) == target)
            attach_btn_text = f"🔓 Connesso (Sgancia)" if is_active else f"🎯 Aggancia {w_name}"
            attach_cb = "tmux:detach" if is_active else f"tmux:attach:{target}"
            keyboard_rows.append([
                {"text": f"👁️ Schermo: {icon} {w_name}", "callback_data": f"tmux:screen:{target}"},
                {"text": attach_btn_text, "callback_data": attach_cb}
            ])

        filter_btn = (
            {"text": "🔍 Mostra: Tutte le Sessioni", "callback_data": "tmux:view:all"}
            if not show_all
            else {"text": "🔍 Mostra: Solo Agy", "callback_data": "tmux:view:agy"}
        )
        refresh_cb = "tmux:refresh:all" if show_all else "tmux:refresh:agy"

        keyboard_rows.append([
            filter_btn,
            {"text": "➕ Nuova Sessione Tmux", "callback_data": "tmux:new_session"}
        ])
        keyboard_rows.append([
            {"text": "🔄 Aggiorna", "callback_data": refresh_cb},
            {"text": "🔙 Menu", "callback_data": "menu:main"}
        ])
        kb = self.client.build_inline_keyboard(keyboard_rows) if self.client else None

        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, text=report, reply_markup=kb, parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_tmux_screen(self, target: str, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Captures recent lines of a tmux pane and sends/edits it as formatted code block with interactive controls."""
        target_chat = chat_id or self.last_chat_id
        target_str = str(target or "")
        screen = await asyncio.to_thread(self.tmux.capture_session_screen, target_str, 25)
        safe_screen = screen.replace("```", "'''")[:3500]
        is_attached = bool(target_str and getattr(self, "active_tmux_target", None) == target_str)
        focus_label = "🎯 *Agganciata:* ogni messaggio in chat andrà qui!" if is_attached else "⚪ *Non agganciata:* usa '🎯 Aggancia' per scrivere da chat"
        text = f"🖥️ *Live Screen tmux (`{target}`):*\n{focus_label}\n```text\n{safe_screen}\n```"
        kb = self._build_tmux_screen_keyboard(target) if self.client else None
        if target_chat and self.client:
            if message_id:
                try:
                    await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=text, reply_markup=kb, parse_mode="Markdown")
                    return
                except Exception:
                    pass
            await self.client.send_message(target_chat, text, reply_markup=kb, parse_mode="Markdown")
        else:
            self.ui.log_info(screen)

    async def cmd_send(self, args: str, chat_id: Optional[int] = None):
        """Sends input to a specific tmux pane or window."""
        target_chat = chat_id or self.last_chat_id
        parts = args.strip().split(maxsplit=1)
        if len(parts) < 2:
            msg = "ℹ️ *Uso comando:* `/send <nome_finestra_o_target> <testo_da_inviare>`\nEsempio: `/send News 1` oppure `/send 0:3.1 /status`"
            if target_chat and self.client:
                await self.client.send_message(target_chat, msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
            return

        dest, input_text = parts[0], parts[1]
        sessions = await asyncio.to_thread(self.tmux.discover_all_sessions, include_aibot=True)
        target = dest
        for s in sessions:
            s_target = str(s.get("target") or "")
            s_wname = str(s.get("window_name") or "")
            s_sname = str(s.get("session_name") or s.get("name") or (s_target.split(":")[0] if ":" in s_target else s_target))
            candidates = [str(c).lower() for c in [s_target, s_wname, s_sname, s.get("name")] if c is not None and str(c).strip()]
            if dest.lower() in candidates or (s_target and dest == s_target):
                target = s_target
                break

        success = await asyncio.to_thread(self.tmux.send_input, target, input_text)
        if success:
            res_msg = f"✅ Inviato input a tmux (`{target}`):\n`{input_text}`"
        else:
            res_msg = f"❌ Impossibile inviare input al target tmux `{target}`."

        if target_chat and self.client:
            await self.client.send_message(target_chat, res_msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
        else:
            self.ui.log_info(res_msg.replace("`", ""))

    async def cmd_agy_options(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Displays the full Agy terminal options and workflows panel."""
        target_chat = chat_id or self.last_chat_id
        text = (
            "🎛️ *Opzioni e Strumenti da Terminale Antigravity (Agy)*\n\n"
            "Usa i pulsanti in basso per visualizzare statistiche, crediti, server MCP e configurazioni:"
        )
        if target_chat and self.client:
            if message_id:
                kb = self.get_agy_options_keyboard()
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=text, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, text, reply_markup=self.get_reply_keyboard("agy"), parse_mode="Markdown")
        else:
            self.ui.log_info("Agy options available via Telegram or /help")

    async def cmd_usage(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Fetches and displays model quota from agy."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(get_usage_report)
        kb = self.client.build_inline_keyboard([
            [{"text": "🔄 Aggiorna Quota", "callback_data": "agy:usage"}],
            [{"text": "🔙 Tutte le Opzioni Agy", "callback_data": "agy:menu"}]
        ]) if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, report, reply_markup=self.get_reply_keyboard("agy"), parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_credits(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Fetches and displays G1 credits."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(get_credits_report)
        kb = self.client.build_inline_keyboard([
            [{"text": "🔄 Aggiorna", "callback_data": "agy:credits"}],
            [{"text": "🔙 Tutte le Opzioni Agy", "callback_data": "agy:menu"}]
        ]) if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, report, reply_markup=self.get_reply_keyboard("agy"), parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_mcp(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Fetches and displays MCP servers."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(get_mcp_report)
        kb = self.client.build_inline_keyboard([
            [{"text": "🔄 Aggiorna Server MCP", "callback_data": "agy:mcp"}],
            [{"text": "🔙 Tutte le Opzioni Agy", "callback_data": "agy:menu"}]
        ]) if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, report, reply_markup=self.get_reply_keyboard("agy"), parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_skills(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Fetches and displays available skills."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(get_skills_report)
        kb = self.client.build_inline_keyboard([
            [{"text": "🔄 Aggiorna Skills", "callback_data": "agy:skills"}],
            [{"text": "🔙 Tutte le Opzioni Agy", "callback_data": "agy:menu"}]
        ]) if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, report, reply_markup=self.get_reply_keyboard("agy"), parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_agents(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Fetches and displays available custom agents."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(get_agents_report)
        kb = self.client.build_inline_keyboard([
            [{"text": "🔄 Aggiorna", "callback_data": "agy:agents"}],
            [{"text": "🔙 Tutte le Opzioni Agy", "callback_data": "agy:menu"}]
        ]) if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, report, reply_markup=self.get_reply_keyboard("agy"), parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_effort(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Fetches and displays reasoning effort."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(get_effort_report)
        kb = self.client.build_inline_keyboard([
            [{"text": "🔙 Tutte le Opzioni Agy", "callback_data": "agy:menu"}]
        ]) if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, report, reply_markup=kb, parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_workflow(self, mode: str, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Displays explanation and actions for slash command workflows."""
        target_chat = chat_id or self.last_chat_id
        report = get_workflow_help(mode)
        buttons = []
        if mode == "compact":
            buttons.append([{"text": "🗜️ Esegui Compact Ora", "callback_data": "agy:run:compact"}])
        buttons.append([{"text": "🔙 Tutte le Opzioni Agy", "callback_data": "agy:menu"}])
        kb = self.client.build_inline_keyboard(buttons) if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, report, reply_markup=kb, parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    # --- Prompt handling ---

    async def handle_prompt(self, prompt: str, source: str = "telegram", chat_id: Optional[int] = None):
        """Dispatches a prompt turn to the agy engine and streams/sends results."""
        if self.is_processing:
            msg = "⏳ Un comando è già in esecuzione! Premi *Stop* per annullarlo prima."
            if source == "telegram" and chat_id:
                await self.client.send_message(chat_id, msg, reply_markup=self.get_reply_keyboard(), parse_mode="Markdown")
            else:
                self.ui.log_warn(msg)
            return

        self.is_processing = True
        target_chat = chat_id or self.last_chat_id

        # Resolve fallback choice:X if received directly
        if prompt.startswith("choice:"):
            try:
                c_idx = int(prompt.split(":", 1)[1])
                val = self.pending_choices.get(c_idx)
                if not val:
                    last_turn = self.history.get_last_turn()
                    if last_turn and last_turn.get("response"):
                        q = QuestionParser.extract_question(last_turn["response"])
                        if q:
                            for ch in q[1]:
                                if ch.index == c_idx:
                                    val = ch.value
                                    break
                if val:
                    prompt = val
            except Exception:
                pass

        # Clear any pending choices
        self.pending_choices.clear()

        # Typing indicator loop
        typing_task = None
        if target_chat and self.client:
            async def keep_typing():
                while self.is_processing:
                    await self.client.send_chat_action(target_chat, "typing")
                    await asyncio.sleep(4)
            typing_task = asyncio.create_task(keep_typing())

        start_time = time.time()
        try:
            self.ui.log_info(f"Dispatching task to Antigravity ({self.engine.model})...")
            exit_code, output = await self.engine.execute_turn(prompt)
            elapsed = time.time() - start_time

            if typing_task:
                typing_task.cancel()

            if not output:
                output = "Turn completed with no output." if exit_code == 0 else f"Process exited with error code {exit_code}."

            self.ui.log_agent_complete(output)

            # Record turn in chat history
            self.history.add_turn(prompt, output, self.engine.model)

            # Check if output contains a multiple-choice question
            question_data = QuestionParser.extract_question(output)
            is_question = bool(question_data)
            success = (exit_code == 0)

            # Push notification header
            push_header = build_completion_header(
                success=success,
                elapsed=elapsed,
                is_question=is_question,
                model=self.engine.model
            )

            # Only trigger loud alerts if user intervention is required
            if is_question:
                title = "🔔 Antigravity: Domanda in attesa!"
                first_line = output.split("\n", 1)[0]
                summary_preview = (first_line[:90] + "...") if len(first_line) > 90 else first_line
                self.notifier.trigger_local_alert(
                    title=title,
                    body=f"⏱️ {format_duration(elapsed)} • {summary_preview}",
                    is_question=True,
                    urgency="critical"
                )

            if question_data:
                question_text, choices = question_data
                self.ui.log_question(question_text, choices)

                # Store pending choices
                for choice in choices:
                    self.pending_choices[choice.index] = choice.value

                if target_chat and self.client:
                    # Build Telegram inline keyboard buttons for multiple choices
                    keyboard_rows = []
                    for choice in choices:
                        keyboard_rows.append([{
                            "text": choice.label,
                            "callback_data": f"choice:{choice.index}"
                        }])
                    reply_markup = self.client.build_inline_keyboard(keyboard_rows)

                    # Send push notification header + question with buttons (audible alert)
                    await self.client.send_message(
                        target_chat,
                        f"{push_header}\n\n{output}\n\n❓ *Question:* {question_text}",
                        reply_markup=reply_markup,
                        parse_mode="Markdown",
                        disable_notification=False
                    )
            else:
                if target_chat and self.client:
                    # Regular response without user intervention: silent delivery, no notification spam
                    await self.client.send_message(
                        target_chat,
                        output,
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown",
                        disable_notification=True
                    )

        except asyncio.CancelledError:
            self.ui.log_warn("Task cancelled.")
            elapsed = time.time() - start_time
            if target_chat and self.client:
                header = build_completion_header(success=False, elapsed=elapsed, model=self.engine.model)
                await self.client.send_message(
                    target_chat,
                    f"{header}\n\n🛑 Attività annullata.",
                    reply_markup=self.get_reply_keyboard(),
                    parse_mode="Markdown",
                    disable_notification=False
                )
        except Exception as e:
            self.ui.log_error(f"Error handling prompt: {e}")
            elapsed = time.time() - start_time
            if target_chat and self.client:
                header = build_completion_header(success=False, elapsed=elapsed, model=self.engine.model)
                await self.client.send_message(
                    target_chat,
                    f"{header}\n\n❌ Errore: {e}",
                    reply_markup=self.get_reply_keyboard(),
                    parse_mode="Markdown",
                    disable_notification=False
                )
        finally:
            self.is_processing = False
            if typing_task and not typing_task.done():
                typing_task.cancel()

    async def handle_terminal_input(self, text: str):
        """Processes input typed directly into the PC terminal."""
        text = text.strip()
        if not text:
            return

        self.ui.log_local_in(text)

        # Handle local slash commands
        if text.startswith("/"):
            parts = text.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1] if len(parts) > 1 else ""

            if cmd in ["/exit", "/quit"]:
                self.ui.log_info("Exiting...")
                self._shutdown_event.set()
                return
            elif cmd in ["/stop", "/cancel"]:
                await self.cmd_stop()
                return
            elif cmd in ["/new", "/reset"]:
                await self.cmd_new()
                return
            elif cmd in ["/history", "/cronologia"]:
                await self.cmd_history()
                return
            elif cmd in ["/status", "/stato"]:
                await self.cmd_status()
                return
            elif cmd in ["/power", "/consumi"]:
                await self.cmd_power()
                return
            elif cmd in ["/screen", "/schermo"]:
                await self.cmd_screen(arg)
                return
            elif cmd in ["/notify", "/notifiche"]:
                await self.cmd_notify()
                return
            elif cmd in ["/usage", "/quota"]:
                await self.cmd_usage()
                return
            elif cmd in ["/credits", "/crediti"]:
                await self.cmd_credits()
                return
            elif cmd == "/mcp":
                await self.cmd_mcp()
                return
            elif cmd == "/skills":
                await self.cmd_skills()
                return
            elif cmd == "/agents":
                await self.cmd_agents()
                return
            elif cmd == "/effort":
                await self.cmd_effort()
                return
            elif cmd in ["/model", "/modello"]:
                if arg:
                    norm = normalize_model_name(arg)
                    self.engine.model = norm
                    self.config.default_model = norm
                    self.config.save()
                    self.ui.log_success(f"Model switched to: {norm}")
                else:
                    self.ui.log_info(f"Current model: {self.engine.model}")
                return
            elif cmd == "/help":
                print("Available commands:")
                print("  /status      - Mostra lo stato del bot")
                print("  /usage       - Mostra le quote di utilizzo e limiti modelli Agy")
                print("  /credits     - Mostra i crediti G1 rimanenti")
                print("  /mcp         - Elenca i server MCP attivi")
                print("  /skills      - Elenca le skill Antigravity disponibili")
                print("  /effort      - Mostra il livello di reasoning effort")
                print("  /power       - Mostra consumi energetici e costi stimati (PC & RPi0W)")
                print("  /screen <on|off> - Accendi o spegni lo schermo del PC")
                print("  /notify      - Gestisci notifiche push e avvisi sonori/desktop")
                print("  /new         - Resetta e avvia una nuova sessione")
                print("  /history     - Mostra la cronologia della sessione")
                print("  /stop        - Ferma l'attività in corso")
                print("  /model <m>   - Cambia modello AI")
                print("  /exit        - Chiudi l'applicazione")
                return

        # Check if user typed a choice number (1, 2, 3...) when a question is pending
        if self.pending_choices:
            try:
                choice_idx = int(text)
                if choice_idx in self.pending_choices:
                    selected_val = self.pending_choices[choice_idx]
                    self.ui.log_success(f"Selected option #{choice_idx}: {selected_val}")
                    self.pending_choices.clear()
                    if self.last_chat_id and self.client:
                        await self.client.send_message(
                            self.last_chat_id,
                            f"💻 *Selected from PC Terminal:* {selected_val}",
                            reply_markup=self.get_reply_keyboard(),
                            parse_mode="Markdown"
                        )
                    await self.handle_prompt(selected_val, source="local")
                    return
            except ValueError:
                pass

        # Broadcast notice to Telegram if user initiated prompt from terminal
        if self.last_chat_id and self.client:
            await self.client.send_message(
                self.last_chat_id,
                f"💻 *[From PC Terminal]:* {text}",
                reply_markup=self.get_reply_keyboard(),
                parse_mode="Markdown"
            )

        await self.handle_prompt(text, source="local")

    async def poll_telegram(self):
        """Continuously polls Telegram for incoming messages and button clicks."""
        offset = self._load_telegram_offset()
        self.ui.log_info(f"Telegram polling started (offset: {offset}).")

        while not self._shutdown_event.is_set():
            try:
                updates = await self.client.get_updates(offset=offset, timeout=20)
                for update in updates:
                    offset = update["update_id"] + 1
                    self._save_telegram_offset(offset)
                    await self.process_telegram_update(update)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.ui.log_error(f"Telegram polling error: {e}")
                await asyncio.sleep(3)

    async def process_telegram_update(self, update: Dict):
        # 1. Handle Callback Queries (Button Clicks)
        if "callback_query" in update:
            cq = update["callback_query"]
            cq_id = cq["id"]
            user = cq.get("from", {})
            user_id = user.get("id")
            user_name = user.get("first_name", "User")
            data = cq.get("data", "")
            msg = cq.get("message", {})
            chat_id = msg.get("chat", {}).get("id")

            if not self.config.is_user_allowed(user_id):
                await self.client.answer_callback_query(cq_id, "Unauthorized user.", show_alert=True)
                return

            self.last_chat_id = chat_id
            await self.client.answer_callback_query(cq_id)

            # A. Multiple choice response
            if data.startswith("choice:"):
                try:
                    choice_idx = int(data.split(":", 1)[1])
                    selected_val = self.pending_choices.get(choice_idx)
                    if not selected_val:
                        last_turn = self.history.get_last_turn()
                        if last_turn and last_turn.get("response"):
                            q = QuestionParser.extract_question(last_turn["response"])
                            if q:
                                for ch in q[1]:
                                    if ch.index == choice_idx:
                                        selected_val = ch.value
                                        break
                    if not selected_val:
                        selected_val = data
                except ValueError:
                    selected_val = data

                self.pending_choices.clear()
                self.ui.log_telegram_in(user_name, f"[Clicked Option: {selected_val}]")

                # Edit message to remove buttons and show confirmation
                if msg.get("message_id"):
                    original_text = msg.get("text", "")
                    await self.client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg["message_id"],
                        text=f"{original_text}\n\n✅ *Selected:* {selected_val}",
                        reply_markup=None,
                        parse_mode="Markdown"
                    )

                # Send selected option to agy as next turn
                await self.handle_prompt(selected_val, source="telegram", chat_id=chat_id)
                return

            # B. Menu actions
            if data == "menu:stop":
                await self.cmd_stop(chat_id)
                return
            elif data == "menu:new":
                await self.cmd_new(chat_id)
                return
            elif data == "menu:history":
                await self.cmd_history(chat_id)
                return
            elif data == "menu:status":
                await self.cmd_status(chat_id)
                return
            elif data == "menu:power":
                await self.cmd_power(chat_id)
                return
            elif data == "menu:screen_off":
                success, screen_msg = await asyncio.to_thread(set_screen_power, False)
                if msg.get("message_id"):
                    await self.client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg["message_id"],
                        text=f"{screen_msg}\n\nUsa i pulsanti per riaccenderlo:",
                        reply_markup=self.client.build_inline_keyboard([
                            [{"text": "💡 Accendi Schermo", "callback_data": "menu:screen_on"}],
                            [{"text": "🔙 Torna al Menu", "callback_data": "menu:main"}]
                        ]),
                        parse_mode="Markdown"
                    )
                else:
                    await self.client.send_message(
                        chat_id,
                        screen_msg,
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown"
                    )
                return
            elif data == "menu:screen_on":
                success, screen_msg = await asyncio.to_thread(set_screen_power, True)
                if msg.get("message_id"):
                    await self.client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg["message_id"],
                        text=f"{screen_msg}\n\nUsa i pulsanti per spegnerlo:",
                        reply_markup=self.client.build_inline_keyboard([
                            [{"text": "💤 Spegni Schermo", "callback_data": "menu:screen_off"}],
                            [{"text": "🔙 Torna al Menu", "callback_data": "menu:main"}]
                        ]),
                        parse_mode="Markdown"
                    )
                else:
                    await self.client.send_message(
                        chat_id,
                        screen_msg,
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown"
                    )
                return
            elif data == "menu:screen_unlock":
                success, screen_msg = await asyncio.to_thread(unlock_screen)
                if msg.get("message_id"):
                    await self.client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg["message_id"],
                        text=screen_msg,
                        reply_markup=self.client.build_inline_keyboard([
                            [{"text": "🔒 Blocca Schermo", "callback_data": "menu:screen_lock"}],
                            [{"text": "🔙 Torna al Menu", "callback_data": "menu:main"}]
                        ]),
                        parse_mode="Markdown"
                    )
                else:
                    await self.client.send_message(
                        chat_id,
                        screen_msg,
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown"
                    )
                return
            elif data == "menu:screen_lock":
                success, screen_msg = await asyncio.to_thread(lock_screen)
                if msg.get("message_id"):
                    await self.client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg["message_id"],
                        text=screen_msg,
                        reply_markup=self.client.build_inline_keyboard([
                            [{"text": "🔓 Sblocca Schermo", "callback_data": "menu:screen_unlock"}],
                            [{"text": "🔙 Torna al Menu", "callback_data": "menu:main"}]
                        ]),
                        parse_mode="Markdown"
                    )
                else:
                    await self.client.send_message(
                        chat_id,
                        screen_msg,
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown"
                    )
                return
            elif data == "menu:models":
                await self.cmd_models(chat_id)
                return
            elif data == "menu:notifications":
                await self.cmd_notify(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "notify:toggle_desktop":
                self.notifier.toggle_desktop()
                await self.cmd_notify(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "notify:toggle_sound":
                self.notifier.toggle_sound()
                await self.cmd_notify(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "menu:media":
                await self.cmd_media(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "menu:hardware":
                await self.cmd_hardware(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "media:playpause":
                await asyncio.to_thread(self.media.play_pause)
                await self.cmd_media(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "media:next":
                await asyncio.to_thread(self.media.next_track)
                await self.cmd_media(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "media:prev":
                await asyncio.to_thread(self.media.prev_track)
                await self.cmd_media(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "media:volup":
                await asyncio.to_thread(self.media.volume_up, 5)
                await self.cmd_media(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "media:voldn":
                await asyncio.to_thread(self.media.volume_down, 5)
                await self.cmd_media(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "media:mute":
                await asyncio.to_thread(self.media.toggle_mute)
                await self.cmd_media(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "media:refresh":
                await self.cmd_media(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "hw:refresh":
                await self.cmd_hardware(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "menu:news":
                await self.cmd_news(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "news:refresh":
                await self.cmd_news(chat_id, message_id=msg.get("message_id"))
                return
            elif data in ["menu:tmux", "tmux:refresh", "tmux:refresh:agy", "tmux:view:agy"]:
                self.current_reply_menu = "tmux"
                await self.cmd_tmux(chat_id, message_id=msg.get("message_id"), show_all=False)
                return
            elif data in ["tmux:view:all", "tmux:refresh:all"]:
                self.current_reply_menu = "tmux"
                await self.cmd_tmux(chat_id, message_id=msg.get("message_id"), show_all=True)
                return
            elif data == "tmux:new_session":
                sname = await asyncio.to_thread(
                    self.tmux.create_session,
                    cwd=self.config.workspace_path,
                    window_name="agy"
                )
                if sname:
                    await self.client.answer_callback_query(cq_id, f"✅ Creata sessione tmux: {sname}", show_alert=True)
                else:
                    await self.client.answer_callback_query(cq_id, "❌ Impossibile creare sessione tmux", show_alert=True)
                await self.cmd_tmux(chat_id, message_id=msg.get("message_id"))
                return
            elif data.startswith("tmux:screen:"):
                t_target = data.split(":", 2)[2]
                await self.cmd_tmux_screen(t_target, chat_id, message_id=msg.get("message_id"))
                return
            elif data.startswith("tmux:attach:") or data.startswith("tmux:prompt:"):
                parts = data.split(":", 2)
                t_target = parts[2] if len(parts) >= 3 else ""
                sessions = await asyncio.to_thread(self.tmux.discover_all_sessions, include_aibot=True)
                w_name = t_target or "session"
                target_session = t_target.split(":")[0] if ":" in t_target else t_target

                # Two-pass resolution: prioritize exact target match first, then fall back to session name matching
                matched = None
                if t_target:
                    # Pass 1: exact target match
                    for s in sessions:
                        if str(s.get("target") or "") == t_target:
                            matched = s
                            break
                    # Pass 2: fallback to session name match
                    if not matched:
                        for s in sessions:
                            s_target = str(s.get("target") or "")
                            s_sname = str(s.get("session_name") or s.get("name") or (s_target.split(":")[0] if ":" in s_target else s_target))
                            if s_sname == target_session:
                                matched = s
                                break

                if matched:
                    t_target = str(matched.get("target") or t_target)
                    w_name = str(matched.get("window_name") or matched.get("name") or t_target or "session")

                self.active_tmux_target = t_target if t_target else None
                self.active_tmux_name = w_name if t_target else None
                await self.client.answer_callback_query(cq_id, f"🎯 Agganciato a {w_name}!", show_alert=False)
                await self.client.send_message(
                    chat_id,
                    f"🎯 *Sessione tmux agganciata:* `{w_name}` (`{t_target}`)\n\n"
                    f"• Ora puoi digitare qualsiasi messaggio in chat per inviarlo direttamente a questo terminale.\n"
                    f"• Usa i pulsanti per comandi rapidi (Ctrl+C, Invio, Schermo).\n"
                    f"• Premi *🔙 Sgancia (Torna al Bot)* o scrivi `/detach` per tornare alla modalità bot.",
                    reply_markup=self.get_reply_keyboard(),
                    parse_mode="Markdown"
                )
                await self.cmd_tmux_screen(t_target, chat_id=chat_id)
                return
            elif data == "tmux:detach":
                t_name = self.active_tmux_name or self.active_tmux_target or "sessione"
                self.active_tmux_target = None
                self.active_tmux_name = None
                self.current_reply_menu = "main"
                await self.client.answer_callback_query(cq_id, "🔓 Sessione sganciata", show_alert=False)
                await self.client.send_message(
                    chat_id,
                    f"🔓 *Sganciato da:* `{t_name}`\nSei tornato alla chat principale del bot.",
                    reply_markup=self.get_reply_keyboard("main"),
                    parse_mode="Markdown"
                )
                return
            elif data.startswith("tmux:key:"):
                payload = data[len("tmux:key:"):]
                if ":" in payload:
                    t_target, t_key = payload.rsplit(":", 1)
                    if t_target and t_key:
                        await asyncio.to_thread(self.tmux.send_key, t_target, t_key)
                        await self.client.answer_callback_query(cq_id, f"Inviato: {t_key}")
                        await asyncio.sleep(0.5)
                        await self.cmd_tmux_screen(t_target, chat_id, message_id=msg.get("message_id"))
                return
            elif data.startswith("tmux:send:"):
                payload = data[len("tmux:send:"):]
                if ":" in payload:
                    t_target, t_val = payload.rsplit(":", 1)
                    if t_target and t_val:
                        await asyncio.to_thread(self.tmux.send_input, t_target, t_val)
                        await self.client.answer_callback_query(cq_id, f"Inviato: {t_val}")
                        await asyncio.sleep(0.5)
                        await self.cmd_tmux_screen(t_target, chat_id, message_id=msg.get("message_id"))
                return
            elif data.startswith("tmux:kill_ask:"):
                t_target = data.split(":", 2)[2]
                confirm_kb = self.client.build_inline_keyboard([
                    [
                        {"text": "⚠️ Sì, Termina Sessione", "callback_data": f"tmux:kill_confirm:{t_target}"},
                        {"text": "❌ Annulla", "callback_data": f"tmux:screen:{t_target}"}
                    ]
                ])
                if msg.get("message_id"):
                    await self.client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg["message_id"],
                        text=f"⚠️ *Sei sicuro di voler chiudere la sessione tmux (`{t_target}`)?*\nI processi in esecuzione in questa sessione verranno interrotti.",
                        reply_markup=confirm_kb,
                        parse_mode="Markdown"
                    )
                return
            elif data.startswith("tmux:kill_confirm:"):
                t_target = data.split(":", 2)[2]
                success = await asyncio.to_thread(self.tmux.kill_session, t_target)
                if self.active_tmux_target == t_target:
                    self.active_tmux_target = None
                    self.active_tmux_name = None
                if success:
                    await self.client.answer_callback_query(cq_id, f"✅ Sessione {t_target} chiusa", show_alert=True)
                else:
                    await self.client.answer_callback_query(cq_id, "❌ Impossibile chiudere la sessione", show_alert=True)
                await self.cmd_tmux(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "menu:main":
                self.current_reply_menu = "main"
                if msg.get("message_id"):
                    await self.client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg["message_id"],
                        text="📋 *Pannello di Controllo Antigravity*\n\nUsa i pulsanti interattivi qui sotto:",
                        reply_markup=self.get_inline_menu(),
                        parse_mode="Markdown"
                    )
                else:
                    await self.cmd_menu(chat_id)
                return

            # E. Agy Terminal Options & Workflows
            if data == "agy:menu":
                await self.cmd_agy_options(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "agy:usage":
                await self.cmd_usage(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "agy:credits":
                await self.cmd_credits(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "agy:mcp":
                await self.cmd_mcp(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "agy:skills":
                await self.cmd_skills(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "agy:agents":
                await self.cmd_agents(chat_id, message_id=msg.get("message_id"))
                return
            elif data == "agy:effort":
                await self.cmd_effort(chat_id, message_id=msg.get("message_id"))
                return
            elif data.startswith("agy:run:"):
                run_action = data.split(":", 2)[2]
                if run_action == "compact":
                    await self.handle_prompt("/compact", source="telegram", chat_id=chat_id)
                else:
                    await self.handle_prompt(f"/{run_action}", source="telegram", chat_id=chat_id)
                return
            elif data.startswith("agy:"):
                workflow_mode = data.split(":", 1)[1]
                await self.cmd_workflow(workflow_mode, chat_id, message_id=msg.get("message_id"))
                return

            # C. Model selection
            if data.startswith("setmodel:"):
                model_name = data.split(":", 1)[1]
                norm = normalize_model_name(model_name)
                self.engine.model = norm
                self.config.default_model = norm
                self.config.save()
                self.ui.log_success(f"Model switched to {norm} via Telegram")
                if msg.get("message_id"):
                    await self.client.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg["message_id"],
                        text=f"✅ *Modello aggiornato a:* `{norm}`",
                        reply_markup=None,
                        parse_mode="Markdown"
                    )
                return

            # D. History actions
            if data == "history:last":
                last_turn = self.history.get_last_turn()
                if last_turn:
                    resp = last_turn.get("response", "Nessuna risposta disponibile.")
                    await self.client.send_message(
                        chat_id,
                        f"📄 *Ultima risposta completa:*\n\n{resp}",
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown"
                    )
                else:
                    await self.client.send_message(
                        chat_id,
                        "Nessun turno disponibile.",
                        reply_markup=self.get_reply_keyboard()
                    )
                return
            elif data == "history:clear":
                self.history.clear_active_session()
                await self.client.send_message(
                    chat_id,
                    "🧹 *Cronologia della sessione attiva svuotata.*",
                    reply_markup=self.get_reply_keyboard(),
                    parse_mode="Markdown"
                )
                return

            return

        # 2. Handle Text Messages
        if "message" in update:
            msg = update["message"]
            user = msg.get("from", {})
            user_id = user.get("id")
            user_name = user.get("first_name", "User")
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "").strip()

            if not text:
                return

            # Check authorization
            if not self.config.is_user_allowed(user_id):
                self.ui.log_warn(f"Blocked unauthorized Telegram message from {user_name} (ID: {user_id})")
                await self.client.send_message(
                    chat_id,
                    "⛔ *Unauthorized.* This bot is paired to another user or machine."
                )
                return

            self.last_chat_id = chat_id
            self.ui.log_telegram_in(user_name, text)

            text_lower = text.lower().strip()

            # Handle Active Tmux Session routing
            if getattr(self, "active_tmux_target", None):
                target = self.active_tmux_target
                target_name = self.active_tmux_name or target

                escape_commands = [
                    "/detach", "detach", "sgancia", "🔙 sgancia (torna al bot)", "/unfocus", "esci",
                    "/menu", "menu", "🔙 menu principale", "🔙 menu", "🔙 indietro", "indietro", "/start", "start"
                ]
                if text_lower in escape_commands:
                    self.active_tmux_target = None
                    self.active_tmux_name = None
                    self.current_reply_menu = "main"
                    await self.client.send_message(
                        chat_id,
                        f"🔓 *Sganciato da:* `{target_name}`\nSei tornato alla chat principale con il bot.",
                        reply_markup=self.get_reply_keyboard("main"),
                        parse_mode="Markdown"
                    )
                    if text_lower in ["/menu", "menu", "🔙 menu principale", "🔙 menu", "🔙 indietro", "indietro"]:
                        await self.cmd_menu(chat_id)
                    return

                if text in ["👁️ Schermo", "🔄 Aggiorna", "/screen"]:
                    await self.cmd_tmux_screen(target, chat_id=chat_id)
                    return

                if text in ["🛑 Ctrl+C", "ctrl+c", "/stop"]:
                    await asyncio.to_thread(self.tmux.send_key, target, "C-c")
                    await asyncio.sleep(0.5)
                    await self.cmd_tmux_screen(target, chat_id=chat_id)
                    return

                if text in ["⏎ Invio", "invio", "enter"]:
                    await asyncio.to_thread(self.tmux.send_key, target, "Enter")
                    await asyncio.sleep(0.5)
                    await self.cmd_tmux_screen(target, chat_id=chat_id)
                    return

                # Forward regular message or quick choice to the tmux session
                self.ui.log_telegram_in(user_name, f"[Forwarding to tmux {target_name}]: {text}")
                await asyncio.to_thread(self.tmux.send_input, target, text)
                await asyncio.sleep(0.7)
                await self.cmd_tmux_screen(target, chat_id=chat_id)
                return

            if text_lower == "/attach" or text_lower == "/focus":
                await self.client.send_message(
                    chat_id,
                    "ℹ️ *Uso comando:* `/attach <nome_sessione>`\nUsa `/tmux` per visualizzare le sessioni attive.",
                    reply_markup=self.get_reply_keyboard(),
                    parse_mode="Markdown"
                )
                return

            if text_lower.startswith("/attach ") or text_lower.startswith("/focus "):
                parts = text.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    await self.client.send_message(
                        chat_id,
                        "ℹ️ *Uso comando:* `/attach <nome_sessione>`\nUsa `/tmux` per visualizzare le sessioni attive.",
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown"
                    )
                    return
                target_arg = parts[1].strip()
                sessions = await asyncio.to_thread(self.tmux.discover_all_sessions, include_aibot=True)
                found = None
                for s in sessions:
                    s_target = str(s.get("target") or "")
                    s_wname = str(s.get("window_name") or "")
                    s_sname = str(s.get("session_name") or s.get("name") or (s_target.split(":")[0] if ":" in s_target else s_target))
                    candidate_names = [str(c) for c in [s_target, s_wname, s_sname, s.get("name")] if c is not None and str(c).strip()]
                    if target_arg in candidate_names or target_arg.lower() in [c.lower() for c in candidate_names]:
                        found = s
                        break
                if found:
                    found_target = str(found.get("target") or found.get("session_name") or target_arg)
                    found_name = str(found.get("window_name") or found.get("name") or found_target)
                    self.active_tmux_target = found_target
                    self.active_tmux_name = found_name
                    await self.client.send_message(
                        chat_id,
                        f"🎯 *Sessione tmux agganciata:* `{found_name}` (`{found_target}`)\n"
                        f"I messaggi digitati in questa chat verranno inoltrati direttamente al terminale.\n"
                        f"Premi *🔙 Sgancia (Torna al Bot)* o scrivi `/detach` per disconnetterti.",
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown"
                    )
                    await self.cmd_tmux_screen(found_target, chat_id=chat_id)
                else:
                    await self.client.send_message(
                        chat_id,
                        f"❌ *Sessione `{target_arg}` non trovata.* Usa `/tmux` per visualizzare le sessioni disponibili.",
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown"
                    )
                return

            if text_lower in ["/detach", "detach", "sgancia", "🔙 sgancia (torna al bot)", "sgancia (torna al bot)"]:
                if getattr(self, "active_tmux_target", None):
                    t_name = self.active_tmux_name or self.active_tmux_target
                    self.active_tmux_target = None
                    self.active_tmux_name = None
                    self.current_reply_menu = "main"
                    await self.client.send_message(chat_id, f"🔓 *Sganciato da:* `{t_name}`\nSei tornato alla chat principale del bot.", reply_markup=self.get_reply_keyboard("main"), parse_mode="Markdown")
                else:
                    self.current_reply_menu = "main"
                    await self.client.send_message(chat_id, "ℹ️ *Tastiera ripristinata al Menu Principale.*", reply_markup=self.get_reply_keyboard("main"), parse_mode="Markdown")
                return

            # Prevent tmux terminal shortcuts from leaking into agy prompts if session is not active
            orphaned_tmux_buttons = ["👁️ Schermo", "🔄 Aggiorna", "🛑 Ctrl+C", "⏎ Invio"]
            if not getattr(self, "active_tmux_target", None):
                if text in orphaned_tmux_buttons or (getattr(self, "current_reply_menu", "main") == "tmux" and text in ["1", "2", "3", "y", "n"]):
                    self.current_reply_menu = "main"
                    await self.client.send_message(
                        chat_id,
                        "ℹ️ *Nessuna sessione tmux attiva.* Tastiera ripristinata al Menu Principale.",
                        reply_markup=self.get_reply_keyboard("main"),
                        parse_mode="Markdown"
                    )
                    return

            # Commands & Button Presses
            if text_lower in ["/start", "start"]:
                welcome = (
                    f"👋 *Ciao {user_name}! Antigravity Bridge è connesso.*\n\n"
                    f"• *Modello:* `{self.engine.model}`\n"
                    f"• *Workspace:* `{self.config.workspace_path}`\n\n"
                    "Puoi inviare prompt, istruzioni o richieste di codice in qualsiasi momento.\n"
                    "Usa i comodi pulsanti qui sotto per gestire il flusso di lavoro!"
                )
                await self.client.send_message(
                    chat_id,
                    welcome,
                    reply_markup=self.get_reply_keyboard("main"),
                    parse_mode="Markdown"
                )
                return

            if text_lower in ["/restart", "restart", "riavvia", "/riavvia"]:
                msg_date = msg.get("date", 0)
                # If message was sent before bot process startup, ignore to prevent stale restart loops
                if msg_date and msg_date < (self.startup_time - 2):
                    self.ui.log_warn(
                        f"Ignoring stale restart command from Telegram (msg date: {msg_date}, startup: {int(self.startup_time)})"
                    )
                    return

                await self.client.send_message(
                    chat_id,
                    "🔄 *Riavvio in corso...*\nIl bot si sta riavviando con il codice aggiornato.",
                    reply_markup=self.get_reply_keyboard("main"),
                    parse_mode="Markdown"
                )
                self.ui.log_info("Restart requested via Telegram")

                # CRITICAL: Acknowledge the restart update so Telegram doesn't redeliver it in an infinite loop!
                update_id = update.get("update_id")
                if update_id is not None:
                    self._save_telegram_offset(update_id + 1)
                    try:
                        await self.client.get_updates(offset=update_id + 1, timeout=0)
                    except Exception as e:
                        logger.warning(f"Failed to acknowledge offset before restart: {e}")

                if getattr(self, "lock_file", None):
                    try:
                        fcntl.flock(self.lock_file, fcntl.LOCK_UN)
                        self.lock_file.close()
                    except Exception:
                        pass

                if self.client:
                    await self.client.close()
                self.ui.stop()
                await asyncio.sleep(0.5)
                os.execv(sys.executable, [sys.executable] + sys.argv)
                return

            # Back / Main Menu button
            if text_lower in ["🔙 menu principale", "🔙 menu", "🔙 indietro", "🔙 torna al menu", "menu principale", "indietro"]:
                self.current_reply_menu = "main"
                await self.client.send_message(
                    chat_id,
                    "🏠 *Menu Principale*\nTastiera ripristinata ai comandi primari.",
                    reply_markup=self.get_reply_keyboard("main"),
                    parse_mode="Markdown"
                )
                return

            if text_lower in ["🛑 stop", "stop", "/stop", "/cancel"]:
                await self.cmd_stop(chat_id)
                return

            if text_lower in ["➕ nuova chat", "nuova chat", "new chat", "new", "/new", "/reset"]:
                await self.cmd_new(chat_id)
                return

            if text_lower in ["📜 cronologia", "cronologia", "history", "/history", "/cronologia"]:
                await self.cmd_history(chat_id)
                return

            if text_lower in ["📊 stato", "stato", "status", "/status"]:
                await self.cmd_status(chat_id)
                return

            # System sub-menu
            if text_lower in ["📊 sistema", "sistema", "/sistema", "/system"]:
                self.current_reply_menu = "system"
                await self.cmd_hardware(chat_id)
                return

            if text_lower in ["⚡ consumi", "consumi", "power", "/power", "/consumi"]:
                self.current_reply_menu = "system"
                await self.cmd_power(chat_id)
                return

            if text_lower in ["📊 hardware", "hardware", "/hardware", "/hw", "/top"]:
                self.current_reply_menu = "system"
                await self.cmd_hardware(chat_id)
                return

            if text_lower in ["🔄 aggiorna metriche", "aggiorna metriche"]:
                await self.cmd_hardware(chat_id)
                return

            # Media sub-menu
            if text_lower in ["🎵 media", "media", "/media", "/music", "musica", "/player", "/volume", "/vol"]:
                self.current_reply_menu = "media"
                await self.cmd_media(chat_id)
                return

            if text_lower in ["⏮️ prec", "prec", "precedente"]:
                await asyncio.to_thread(self.media.prev_track)
                await self.cmd_media(chat_id)
                return

            if text_lower in ["⏯️ play/pausa", "play/pausa", "play", "pause", "pausa"]:
                await asyncio.to_thread(self.media.play_pause)
                await self.cmd_media(chat_id)
                return

            if text_lower in ["⏭️ succ", "succ", "successivo", "next"]:
                await asyncio.to_thread(self.media.next_track)
                await self.cmd_media(chat_id)
                return

            if text_lower in ["🔉 vol -", "🔉 vol -5%", "vol -", "volume -", "vol-"]:
                await asyncio.to_thread(self.media.volume_down, 5)
                await self.cmd_media(chat_id)
                return

            if text_lower in ["🔊 vol +", "🔊 vol +5%", "vol +", "volume +", "vol+"]:
                await asyncio.to_thread(self.media.volume_up, 5)
                await self.cmd_media(chat_id)
                return

            if text_lower in ["🔇 muto", "muto", "mute"]:
                await asyncio.to_thread(self.media.toggle_mute)
                await self.cmd_media(chat_id)
                return

            if text_lower in ["🔄 aggiorna media", "aggiorna media"]:
                await self.cmd_media(chat_id)
                return

            # Screen sub-menu
            if text_lower in ["💡 accendi schermo", "accendi schermo", "accendi"]:
                await self.cmd_screen("on", chat_id)
                return

            if text_lower in ["💤 spegni schermo", "spegni schermo", "spegni"]:
                await self.cmd_screen("off", chat_id)
                return

            if text_lower in ["🔒 blocca pc", "blocca pc"]:
                await self.cmd_screen("lock", chat_id)
                return

            if text_lower in ["🔓 sblocca pc", "sblocca pc"]:
                await self.cmd_screen("unlock", chat_id)
                return

            if text_lower.startswith(("/screen", "/schermo", "screen", "schermo", "🖥️ schermo", "🖥️ screen")) or text_lower in ["🖥️ schermo", "schermo", "screen"]:
                self.current_reply_menu = "screen"
                parts = text.split(maxsplit=1)
                arg_val = parts[1] if len(parts) > 1 and not parts[0].startswith("🖥️") else ""
                await self.cmd_screen(arg_val, chat_id)
                return

            if text_lower in ["/unlock", "/sblocca", "sblocca", "unlock"]:
                await self.cmd_screen("unlock", chat_id)
                return

            if text_lower in ["/lock", "/blocca", "blocca", "lock"]:
                await self.cmd_screen("lock", chat_id)
                return

            # Tmux sub-menu
            if text_lower in ["🖥️ sessioni agy", "sessioni agy"]:
                self.current_reply_menu = "tmux"
                await self.cmd_tmux(chat_id, show_all=False)
                return

            if text_lower in ["💻 tutte le sessioni", "/tmux all", "tmux all", "/tmux_all", "tutte le sessioni"]:
                self.current_reply_menu = "tmux"
                await self.cmd_tmux(chat_id, show_all=True)
                return

            if text_lower in ["📰 teamwork news", "📰 news", "news", "/news", "/progetti/news"]:
                self.current_reply_menu = "tmux"
                await self.cmd_news(chat_id)
                return

            if text_lower in ["🖥️ tmux agy", "tmux", "/tmux", "tmux agy", "/sessions"]:
                self.current_reply_menu = "tmux"
                await self.client.send_message(
                    chat_id,
                    "🖥️ *Navigatore Tmux Agy*\nTastiera inferiore sincronizzata con i comandi Tmux:",
                    reply_markup=self.get_reply_keyboard("tmux"),
                    parse_mode="Markdown"
                )
                await self.cmd_tmux(chat_id, show_all=False)
                return

            if text_lower.startswith("/send "):
                arg_val = text[6:].strip()
                await self.cmd_send(arg_val, chat_id)
                return

            # Agy options sub-menu
            if text_lower in ["🎛️ opzioni agy", "opzioni agy", "opzioni", "/agy", "/opzioni", "/tools", "agy"]:
                self.current_reply_menu = "agy"
                await self.cmd_agy_options(chat_id)
                return

            if text_lower in ["📊 quota usage", "/usage", "/quota", "usage", "quota"]:
                self.current_reply_menu = "agy"
                await self.cmd_usage(chat_id)
                return

            if text_lower in ["💳 crediti g1", "/credits", "credits", "crediti", "/crediti"]:
                self.current_reply_menu = "agy"
                await self.cmd_credits(chat_id)
                return

            if text_lower in ["🔌 mcp servers", "🔌 mcp", "/mcp", "mcp"]:
                self.current_reply_menu = "agy"
                await self.cmd_mcp(chat_id)
                return

            if text_lower in ["⚡ skills", "/skills", "skills"]:
                self.current_reply_menu = "agy"
                await self.cmd_skills(chat_id)
                return

            if text_lower in ["🤖 agenti", "/agents", "agents", "agenti", "/agenti"]:
                self.current_reply_menu = "agy"
                await self.cmd_agents(chat_id)
                return

            if text_lower in ["🧠 modello ai", "🧠 modello", "modello", "model", "/model"]:
                self.current_reply_menu = "agy"
                if text_lower.startswith("/model") and len(text.split(maxsplit=1)) > 1:
                    new_model = text.split(maxsplit=1)[1].strip()
                    norm = normalize_model_name(new_model)
                    self.engine.model = norm
                    self.config.default_model = norm
                    self.config.save()
                    await self.client.send_message(
                        chat_id,
                        f"✅ Modello aggiornato a: `{norm}`",
                        reply_markup=self.get_reply_keyboard("agy"),
                        parse_mode="Markdown"
                    )
                    self.ui.log_success(f"Model switched to {norm} via Telegram")
                else:
                    await self.cmd_models(chat_id)
                return

            if text_lower in ["📋 menu", "menu", "/menu"]:
                await self.cmd_menu(chat_id)
                return

            if text_lower in ["/notify", "/notifiche", "notifiche", "🔔 notifiche", "notifica"]:
                await self.cmd_notify(chat_id)
                return

            if text_lower in ["/effort", "effort"]:
                await self.cmd_effort(chat_id)
                return

            if text_lower in ["/teamwork", "teamwork"]:
                await self.cmd_workflow("teamwork", chat_id)
                return

            if text_lower in ["/goal", "goal"]:
                await self.cmd_workflow("goal", chat_id)
                return

            if text_lower in ["/plan", "plan"]:
                await self.cmd_workflow("plan", chat_id)
                return

            if text_lower in ["/boost", "boost"]:
                await self.cmd_workflow("boost", chat_id)
                return

            if text_lower in ["/browser", "browser"]:
                await self.cmd_workflow("browser", chat_id)
                return

            if text_lower in ["/compact", "compact"]:
                await self.handle_prompt("/compact", source="telegram", chat_id=chat_id)
                return

            if text_lower in ["/grill-me", "/grill_me", "grill-me", "grill me"]:
                await self.cmd_workflow("grill_me", chat_id)
                return

            if text_lower in ["/learn", "learn"]:
                await self.cmd_workflow("learn", chat_id)
                return

            if text_lower in ["/help", "help", "aiuto"]:
                help_msg = (
                    "ℹ️ *Guida Comandi Antigravity*\n\n"
                    "• *🛑 Stop:* Interrompe l'elaborazione dell'agente se in corso\n"
                    "• *➕ Nuova Chat:* Avvia una nuova sessione pulita\n"
                    "• *📜 Cronologia:* Mostra i messaggi e turni della sessione corrente\n"
                    "• *📊 Stato:* Informazioni sul modello attivo e cartella di lavoro\n"
                    "• *⚡ Consumi:* Visualizza consumi in Watt/h, tempo di attività e costi stimati\n"
                    "• *🖥️ Schermo:* `/screen on` o `/screen off` per accendere o spegnere il display del PC\n"
                    "• *🎵 Media:* `/media` controlli multimediali (Play/Pausa, Skip, Volume PC, Muto)\n"
                    "• *📊 Hardware:* `/hardware` o `/top` per monitorare CPU, RAM, Disco, Temp e Batteria in tempo reale\n"
                    "• *🖥️ Tmux Agy:* `/tmux` per vedere tutte le sessioni Agy attive in tmux, subagenti e live screen\n"
                    "• *✍️ Invia Input:* `/send <finestra> <testo>` per rispondere direttamente a una sessione Agy in tmux\n"
                    "• *📰 News:* `/news` per monitorare il progetto multi-agente in ~/Progetti/News\n"
                    "• *🔔 Notifiche:* `/notify` per gestire pop-up desktop Linux, avvisi sonori e push Telegram\n"
                    "• *🎛️ Opzioni Agy:* Menu interattivo con tutte le opzioni da terminale (Usage, Credits, MCP, Skills)\n"
                    "• *📊 /usage:* Mostra le quote residue settimanali e su 5 ore di tutti i modelli\n"
                    "• *💳 /credits:* Crediti G1 rimanenti\n"
                    "• *🔌 /mcp:* Elenco dei server MCP attivi e configurati\n"
                    "• *⚡ /skills:* Elenco skill disponibili\n"
                    "• *🧠 Modello:* Menu interattivo per cambiare modello AI\n"
                    "• *📋 Menu:* Visualizza il pannello comandi inline"
                )
                await self.client.send_message(
                    chat_id,
                    help_msg,
                    reply_markup=self.get_reply_keyboard(),
                    parse_mode="Markdown"
                )
                return

            # Check if user typed a choice number (1, 2, 3...) when a question is pending
            if self.pending_choices:
                try:
                    choice_idx = int(text)
                    if choice_idx in self.pending_choices:
                        selected_val = self.pending_choices[choice_idx]
                        self.pending_choices.clear()
                        await self.client.send_message(
                            chat_id,
                            f"✅ *Selected:* {selected_val}",
                            reply_markup=self.get_reply_keyboard(),
                            parse_mode="Markdown"
                        )
                        await self.handle_prompt(selected_val, source="telegram", chat_id=chat_id)
                        return
                except ValueError:
                    pass

            # Regular prompt
            await self.handle_prompt(text, source="telegram", chat_id=chat_id)

    async def _teamwork_watch_loop(self):
        """Background watcher that pushes notifications ONLY when user intervention is needed or all agents finish."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.sleep(10)
                target_chat = self.last_chat_id or (self.config.allowed_user_ids[0] if self.config.allowed_user_ids else None)
                if not target_chat or not self.client:
                    continue

                # Poll tmux agy sessions (strictly filtered: only question choices or full completion)
                events = await asyncio.to_thread(self.tmux.check_for_session_events)
                for ev in events:
                    w_name = str(ev.get("window_name") or ev.get("target") or "session")
                    t_target = str(ev.get("target") or "")
                    sub_info = f" • 🤖 `{ev.get('subagents_count') or 0} subagenti`" if ev.get("subagents_count") else ""

                    if ev["event_type"] == "question":
                        q_msg = (
                            f"🔔 *Antigravity attende una risposta in tmux!*\n\n"
                            f"• *Finestra:* `{w_name}` (`{t_target}`){sub_info}\n"
                            f"• *Cartella:* `{ev['cwd']}`\n\n"
                            f"```text\n{ev['snippet']}\n```"
                        )
                        # Build buttons for choices if available
                        kb_rows = []
                        if ev.get("choices"):
                            for ch in ev["choices"][:4]:
                                idx_str = ch.split(".", 1)[0].strip() if "." in ch else ch[:10]
                                kb_rows.append([{"text": f"👉 {ch[:35]}", "callback_data": f"tmux:send:{t_target}:{idx_str}"}])
                        kb_rows.append([
                            {"text": f"👁️ Vedi Schermo {w_name}", "callback_data": f"tmux:screen:{t_target}"},
                            {"text": "🖥️ Tutte le Sessioni", "callback_data": "menu:tmux"}
                        ])
                        reply_kb = self.client.build_inline_keyboard(kb_rows)
                        await self.client.send_message(
                            target_chat,
                            q_msg,
                            reply_markup=reply_kb,
                            parse_mode="Markdown",
                            disable_notification=False
                        )
                        self.notifier.trigger_local_alert(
                            title=f"🔔 Domanda Antigravity ({w_name})",
                            body=ev["snippet"][:90],
                            is_question=True
                        )
                    elif ev["event_type"] == "completed":
                        comp_msg = (
                            f"🎉 *Richiesta Completata con Successo in tmux!*\n\n"
                            f"• *Finestra:* `{w_name}` (`{t_target}`)\n"
                            f"• *Cartella:* `{ev['cwd']}`\n\n"
                            f"```text\n{ev['snippet']}\n```"
                        )
                        kb_rows = [
                            [{"text": f"👁️ Schermo Finale: {w_name}", "callback_data": f"tmux:screen:{t_target}"}],
                            [{"text": "🖥️ Tutte le Sessioni", "callback_data": "menu:tmux"}]
                        ]
                        reply_kb = self.client.build_inline_keyboard(kb_rows)
                        await self.client.send_message(
                            target_chat,
                            comp_msg,
                            reply_markup=reply_kb,
                            parse_mode="Markdown",
                            disable_notification=False
                        )
                        self.notifier.trigger_local_alert(
                            title=f"✅ Task Completato ({w_name})",
                            body=ev["snippet"][:90],
                            is_question=False
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in background monitor: {e}")
                await asyncio.sleep(15)

    async def run(self):
        if not await self.initialize():
            return

        self.ui.set_input_callback(self.handle_terminal_input)

        # Start terminal listener, Telegram poller, and teamwork watcher
        terminal_task = asyncio.create_task(self.ui.start_input_listener())
        polling_task = asyncio.create_task(self.poll_telegram())
        teamwork_task = asyncio.create_task(self._teamwork_watch_loop())

        # Wait until shutdown event is triggered
        try:
            await self._shutdown_event.wait()
        finally:
            self.ui.log_info("Shutting down bot...")
            terminal_task.cancel()
            polling_task.cancel()
            teamwork_task.cancel()
            if self.client:
                await self.client.close()
            self.ui.stop()

def main():
    lock_file = open("/tmp/aibot.lock", "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, IOError):
        print("\n\033[31m[ERROR] Un'altra istanza del bot è già attiva!\033[0m")
        print("Termina la vecchia istanza con:")
        print("  \033[1mpkill -f 'python.*main.py'\033[0m\n")
        sys.exit(1)

    app = BotApp()
    app.lock_file = lock_file

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal():
        app._shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(app.run())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.exception(f"Unhandled fatal error in bot run: {e}")
        app.ui.log_error(f"Fatal error: {e}")
    finally:
        loop.close()
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()
