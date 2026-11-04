import asyncio
import fcntl
import logging
import signal
import sys
import time
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
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler("aibot.log", encoding="utf-8")]
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

        self.last_chat_id: Optional[int] = None
        self.pending_choices: Dict[int, str] = {} # choice_index -> choice_value
        self.is_processing = False
        self._shutdown_event = asyncio.Event()
        self.teamwork_watcher_task: Optional[asyncio.Task] = None

    def get_reply_keyboard(self) -> Dict[str, Any]:
        """Persistent bottom keyboard for Telegram on mobile/desktop."""
        return self.client.build_reply_keyboard([
            ["🛑 Stop", "➕ Nuova Chat"],
            ["📜 Cronologia", "📊 Hardware"],
            ["⚡ Consumi", "🖥️ Schermo"],
            ["🎵 Media", "🖥️ Tmux Agy"],
            ["🎛️ Opzioni Agy", "📋 Menu"]
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
        me = await self.client.get_me()
        if not me:
            self.ui.log_error("Failed to connect to Telegram. Please check your BOT TOKEN and internet connection.")
            return False

        bot_username = me.get("username", "UnknownBot")

        # Register official bot commands in Telegram
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
        await self.client.set_my_commands(bot_commands)

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
        """Resets the conversation and starts a new session."""
        target_chat = chat_id or self.last_chat_id
        if self.engine:
            self.engine.reset_conversation()
        self.history.new_session()
        self.pending_choices.clear()
        self.ui.log_success("Conversation reset. Started a fresh session.")
        if target_chat and self.client:
            await self.client.send_message(
                target_chat,
                "🔄 *Nuova sessione di chat avviata!*\nLa conversazione precedente è stata archiviata. Puoi iniziare con una nuova richiesta.",
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
                    reply_markup=self.get_reply_keyboard(),
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
                    reply_markup=self.get_reply_keyboard(),
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
                    reply_markup=self.get_reply_keyboard(),
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
                    reply_markup=self.get_reply_keyboard(),
                    parse_mode="Markdown"
                )
            else:
                self.ui.log_info(msg.replace("*", "").replace("`", ""))
        else:
            kb = self.client.build_inline_keyboard([
                [
                    {"text": "💤 Spegni Schermo", "callback_data": "menu:screen_off"},
                    {"text": "💡 Accendi Schermo", "callback_data": "menu:screen_on"}
                ],
                [
                    {"text": "🔓 Sblocca Schermo", "callback_data": "menu:screen_unlock"},
                    {"text": "🔒 Blocca Schermo", "callback_data": "menu:screen_lock"}
                ]
            ])
            if target_chat and self.client:
                await self.client.send_message(
                    target_chat,
                    "🖥️ *Controllo Schermo PC*\nScegli un'azione per il display del PC:",
                    reply_markup=kb,
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
        kb = self.get_media_keyboard() if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, text=report, reply_markup=kb, parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_hardware(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Displays real-time hardware status."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(self.hw.get_hardware_report)
        kb = self.get_hardware_keyboard() if self.client else None
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, text=report, reply_markup=kb, parse_mode="Markdown")
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

    async def cmd_tmux(self, chat_id: Optional[int] = None, message_id: Optional[int] = None):
        """Displays all running agy sessions in tmux with action buttons."""
        target_chat = chat_id or self.last_chat_id
        report = await asyncio.to_thread(self.tmux.format_sessions_card)
        sessions = await asyncio.to_thread(self.tmux.discover_agy_sessions)

        keyboard_rows = []
        for s in sessions:
            w_name = s["window_name"]
            target = s["target"]
            keyboard_rows.append([
                {"text": f"👁️ Schermo: {w_name}", "callback_data": f"tmux:screen:{target}"},
                {"text": f"✍️ Invia Input: {w_name}", "callback_data": f"tmux:prompt:{target}"}
            ])
        keyboard_rows.append([
            {"text": "🔄 Aggiorna Sessioni", "callback_data": "tmux:refresh"},
            {"text": "🔙 Torna al Menu", "callback_data": "menu:main"}
        ])
        kb = self.client.build_inline_keyboard(keyboard_rows) if self.client else None

        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=report, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, text=report, reply_markup=kb, parse_mode="Markdown")
        else:
            self.ui.log_info(report.replace("*", "").replace("`", ""))

    async def cmd_tmux_screen(self, target: str, chat_id: Optional[int] = None):
        """Captures recent lines of a tmux pane and sends it as formatted code block."""
        target_chat = chat_id or self.last_chat_id
        screen = await asyncio.to_thread(self.tmux.capture_session_screen, target, 25)
        text = f"🖥️ *Live Screen tmux (`{target}`):*\n```text\n{screen}\n```"
        kb = self.client.build_inline_keyboard([
            [{"text": "🔄 Aggiorna Schermo", "callback_data": f"tmux:screen:{target}"}],
            [{"text": "🔙 Tutte le Sessioni", "callback_data": "menu:tmux"}]
        ]) if self.client else None
        if target_chat and self.client:
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
        sessions = await asyncio.to_thread(self.tmux.discover_agy_sessions)
        target = dest
        for s in sessions:
            if s["window_name"].lower() == dest.lower() or s["target"] == dest:
                target = s["target"]
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
            "Seleziona un'opzione per visualizzare statistiche in tempo reale o scoprire come usare i workflow avanzati:"
        )
        kb = self.get_agy_options_keyboard()
        if target_chat and self.client:
            if message_id:
                await self.client.edit_message_text(chat_id=target_chat, message_id=message_id, text=text, reply_markup=kb, parse_mode="Markdown")
            else:
                await self.client.send_message(target_chat, text, reply_markup=kb, parse_mode="Markdown")
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
                await self.client.send_message(target_chat, report, reply_markup=kb, parse_mode="Markdown")
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
                await self.client.send_message(target_chat, report, reply_markup=kb, parse_mode="Markdown")
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
                await self.client.send_message(target_chat, report, reply_markup=kb, parse_mode="Markdown")
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
                await self.client.send_message(target_chat, report, reply_markup=kb, parse_mode="Markdown")
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
                await self.client.send_message(target_chat, report, reply_markup=kb, parse_mode="Markdown")
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

            # Trigger local desktop notification and chime
            title = "🔔 Antigravity: Domanda in attesa!" if is_question else (
                "✅ Antigravity: Task Completato" if success else "❌ Antigravity: Task Fallito"
            )
            first_line = output.split("\n", 1)[0]
            summary_preview = (first_line[:90] + "...") if len(first_line) > 90 else first_line
            self.notifier.trigger_local_alert(
                title=title,
                body=f"⏱️ {format_duration(elapsed)} • {summary_preview}",
                is_question=is_question,
                urgency="critical" if is_question else "normal"
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

                    # Send push notification header + question with buttons
                    await self.client.send_message(
                        target_chat,
                        f"{push_header}\n\n{output}\n\n❓ *Question:* {question_text}",
                        reply_markup=reply_markup,
                        parse_mode="Markdown",
                        disable_notification=False
                    )
            else:
                if target_chat and self.client:
                    await self.client.send_message(
                        target_chat,
                        f"{push_header}\n\n{output}",
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown",
                        disable_notification=False
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
        offset = 0
        self.ui.log_info("Telegram polling started.")

        while not self._shutdown_event.is_set():
            try:
                updates = await self.client.get_updates(offset=offset, timeout=20)
                for update in updates:
                    offset = update["update_id"] + 1
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
            elif data == "menu:tmux" or data == "tmux:refresh":
                await self.cmd_tmux(chat_id, message_id=msg.get("message_id"))
                return
            elif data.startswith("tmux:screen:"):
                t_target = data.split(":", 2)[2]
                await self.cmd_tmux_screen(t_target, chat_id)
                return
            elif data.startswith("tmux:send:"):
                parts = data.split(":", 3)
                if len(parts) >= 4:
                    t_target = parts[2]
                    t_val = parts[3]
                    success = await asyncio.to_thread(self.tmux.send_input, t_target, t_val)
                    if msg.get("message_id"):
                        await self.client.edit_message_text(
                            chat_id=chat_id,
                            message_id=msg["message_id"],
                            text=f"✅ Risposta `{t_val}` inviata a tmux (`{t_target}`)",
                            reply_markup=None,
                            parse_mode="Markdown"
                        )
                return
            elif data == "menu:main":
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
                    reply_markup=self.get_reply_keyboard(),
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

            if text_lower in ["⚡ consumi", "consumi", "power", "/power", "/consumi"]:
                await self.cmd_power(chat_id)
                return

            if text_lower in ["🎵 media", "media", "/media", "/music", "musica", "/player", "/volume", "/vol"]:
                await self.cmd_media(chat_id)
                return

            if text_lower in ["📊 hardware", "hardware", "/hardware", "/hw", "/top"]:
                await self.cmd_hardware(chat_id)
                return

            if text_lower in ["📰 news", "news", "/news", "/progetti/news"]:
                await self.cmd_news(chat_id)
                return

            if text_lower in ["🖥️ tmux agy", "tmux", "/tmux", "tmux agy", "/sessions"]:
                await self.cmd_tmux(chat_id)
                return

            if text_lower.startswith("/send "):
                arg_val = text[6:].strip()
                await self.cmd_send(arg_val, chat_id)
                return

            if text_lower.startswith(("/screen", "/schermo", "screen", "schermo", "🖥️ schermo", "🖥️ screen")) or text_lower in ["🖥️ schermo", "schermo", "screen"]:
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

            if text_lower in ["🧠 modello", "modello", "model", "/model"]:
                if text_lower.startswith("/model") and len(text.split(maxsplit=1)) > 1:
                    new_model = text.split(maxsplit=1)[1].strip()
                    norm = normalize_model_name(new_model)
                    self.engine.model = norm
                    self.config.default_model = norm
                    self.config.save()
                    await self.client.send_message(
                        chat_id,
                        f"✅ Modello aggiornato a: `{norm}`",
                        reply_markup=self.get_reply_keyboard(),
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

            if text_lower in ["🎛️ opzioni agy", "opzioni agy", "opzioni", "/agy", "/opzioni", "/tools", "agy"]:
                await self.cmd_agy_options(chat_id)
                return

            if text_lower in ["/usage", "/quota", "usage", "quota"]:
                await self.cmd_usage(chat_id)
                return

            if text_lower in ["/credits", "credits", "crediti", "/crediti"]:
                await self.cmd_credits(chat_id)
                return

            if text_lower in ["/mcp", "mcp"]:
                await self.cmd_mcp(chat_id)
                return

            if text_lower in ["/skills", "skills"]:
                await self.cmd_skills(chat_id)
                return

            if text_lower in ["/agents", "agents", "agenti", "/agenti"]:
                await self.cmd_agents(chat_id)
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
                    w_name = ev["window_name"]
                    t_target = ev["target"]
                    sub_info = f" • 🤖 `{ev['subagents_count']} subagenti`" if ev.get("subagents_count") else ""

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
    finally:
        loop.close()

if __name__ == "__main__":
    main()
