import asyncio
import fcntl
import logging
import signal
import sys
from typing import Optional, Dict, List, Any

from config import Config
from telegram_client import TelegramClient
from agy_engine import AgyEngine, KNOWN_MODELS, normalize_model_name
from terminal_ui import TerminalUI
from question_parser import QuestionParser, QuestionChoice
from history_manager import HistoryManager

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

        self.last_chat_id: Optional[int] = None
        self.pending_choices: Dict[int, str] = {} # choice_index -> choice_value
        self.is_processing = False
        self._shutdown_event = asyncio.Event()

    def get_reply_keyboard(self) -> Dict[str, Any]:
        """Persistent bottom keyboard for Telegram on mobile/desktop."""
        return self.client.build_reply_keyboard([
            ["🛑 Stop", "➕ Nuova Chat"],
            ["📜 Cronologia", "📊 Stato"],
            ["🧠 Modello", "📋 Menu"]
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
                {"text": "🧠 Seleziona Modello", "callback_data": "menu:models"}
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
            {"command": "model", "description": "🧠 Visualizza o cambia il modello AI"}
        ]
        await self.client.set_my_commands(bot_commands)

        self.engine = AgyEngine(
            agy_path=self.config.agy_path,
            workspace_path=self.config.workspace_path,
            default_model=self.config.default_model,
            dangerously_skip_permissions=self.config.dangerously_skip_permissions
        )

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

        try:
            self.ui.log_info(f"Dispatching task to Antigravity ({self.engine.model})...")
            exit_code, output = await self.engine.execute_turn(prompt)

            if typing_task:
                typing_task.cancel()

            if not output:
                output = "Turn completed with no output." if exit_code == 0 else f"Process exited with error code {exit_code}."

            self.ui.log_agent_complete(output)

            # Record turn in chat history
            self.history.add_turn(prompt, output, self.engine.model)

            # Check if output contains a multiple-choice question
            question_data = QuestionParser.extract_question(output)

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

                    # Send main output + question with buttons
                    await self.client.send_message(
                        target_chat,
                        f"{output}\n\n❓ *Question:* {question_text}",
                        reply_markup=reply_markup,
                        parse_mode="Markdown"
                    )
            else:
                if target_chat and self.client:
                    await self.client.send_message(
                        target_chat,
                        output,
                        reply_markup=self.get_reply_keyboard(),
                        parse_mode="Markdown"
                    )

        except asyncio.CancelledError:
            self.ui.log_warn("Task cancelled.")
            if target_chat and self.client:
                await self.client.send_message(
                    target_chat,
                    "🛑 Attività annullata.",
                    reply_markup=self.get_reply_keyboard()
                )
        except Exception as e:
            self.ui.log_error(f"Error handling prompt: {e}")
            if target_chat and self.client:
                await self.client.send_message(
                    target_chat,
                    f"❌ Errore: {e}",
                    reply_markup=self.get_reply_keyboard()
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
                    selected_val = self.pending_choices.get(choice_idx, data)
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
            elif data == "menu:models":
                await self.cmd_models(chat_id)
                return
            elif data == "menu:main":
                await self.cmd_menu(chat_id)
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

            if text_lower in ["/help", "help", "aiuto"]:
                help_msg = (
                    "ℹ️ *Guida Comandi Antigravity*\n\n"
                    "• *🛑 Stop:* Interrompe l'elaborazione dell'agente se in corso\n"
                    "• *➕ Nuova Chat:* Avvia una nuova sessione pulita\n"
                    "• *📜 Cronologia:* Mostra i messaggi e turni della sessione corrente\n"
                    "• *📊 Stato:* Informazioni sul modello attivo e cartella di lavoro\n"
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

    async def run(self):
        if not await self.initialize():
            return

        self.ui.set_input_callback(self.handle_terminal_input)

        # Start terminal listener and Telegram poller
        terminal_task = asyncio.create_task(self.ui.start_input_listener())
        polling_task = asyncio.create_task(self.poll_telegram())

        # Wait until shutdown event is triggered
        try:
            await self._shutdown_event.wait()
        finally:
            self.ui.log_info("Shutting down bot...")
            terminal_task.cancel()
            polling_task.cancel()
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
