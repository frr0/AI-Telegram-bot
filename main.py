import asyncio
import logging
import signal
import sys
from typing import Optional, Dict, List

from config import Config
from telegram_client import TelegramClient
from agy_engine import AgyEngine
from terminal_ui import TerminalUI
from question_parser import QuestionParser, QuestionChoice

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

        self.last_chat_id: Optional[int] = None
        self.pending_choices: Dict[int, str] = {} # choice_index -> choice_value
        self.is_processing = False
        self._shutdown_event = asyncio.Event()

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

    async def handle_prompt(self, prompt: str, source: str = "telegram", chat_id: Optional[int] = None):
        """Dispatches a prompt turn to the agy engine and streams/sends results."""
        if self.is_processing:
            msg = "⏳ An agent turn is already running! Send /stop to cancel it first."
            if source == "telegram" and chat_id:
                await self.client.send_message(chat_id, msg)
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

            # Check if output contains a multiple-choice question
            question_data = QuestionParser.extract_question(output)

            if question_data:
                question_text, choices = question_data
                self.ui.log_question(question_text, choices)

                # Store pending choices
                for choice in choices:
                    self.pending_choices[choice.index] = choice.value

                if target_chat and self.client:
                    # Build Telegram inline keyboard buttons
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
                    await self.client.send_message(target_chat, output, parse_mode="Markdown")

        except asyncio.CancelledError:
            self.ui.log_warn("Task cancelled.")
            if target_chat and self.client:
                await self.client.send_message(target_chat, "🛑 Task was cancelled.")
        except Exception as e:
            self.ui.log_error(f"Error handling prompt: {e}")
            if target_chat and self.client:
                await self.client.send_message(target_chat, f"❌ Error: {e}")
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
                if self.engine and self.engine.stop_current_task():
                    self.ui.log_success("Stopped agent task.")
                else:
                    self.ui.log_info("No agent task is currently running.")
                return
            elif cmd in ["/new", "/reset"]:
                if self.engine:
                    self.engine.reset_conversation()
                    self.pending_choices.clear()
                    self.ui.log_success("Conversation reset. Started a fresh session.")
                    if self.last_chat_id and self.client:
                        await self.client.send_message(self.last_chat_id, "🔄 *Conversation reset.* Started a fresh session.")
                return
            elif cmd == "/status":
                status = "Busy" if self.is_processing else "Idle"
                self.ui.log_info(f"Status: {status} | Model: {self.engine.model} | Workspace: {self.config.workspace_path}")
                return
            elif cmd == "/model":
                if arg:
                    self.engine.model = arg
                    self.config.default_model = arg
                    self.config.save()
                    self.ui.log_success(f"Model switched to: {arg}")
                else:
                    self.ui.log_info(f"Current model: {self.engine.model}")
                return
            elif cmd == "/help":
                print("Available commands:")
                print("  /status      - Show current bot status")
                print("  /new         - Reset and start fresh conversation")
                print("  /stop        - Stop active turn")
                print("  /model <m>   - Switch model (e.g. gemini-2.5-pro)")
                print("  /exit        - Exit application")
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
                        await self.client.send_message(self.last_chat_id, f"💻 *Selected from PC Terminal:* {selected_val}")
                    await self.handle_prompt(selected_val, source="local")
                    return
            except ValueError:
                pass

        # Broadcast notice to Telegram if user initiated prompt from terminal
        if self.last_chat_id and self.client:
            await self.client.send_message(self.last_chat_id, f"💻 *[From PC Terminal]:* {text}")

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

            # Commands
            if text == "/start":
                welcome = (
                    f"👋 *Hello {user_name}! Antigravity is connected.*\n\n"
                    f"• *Model:* `{self.engine.model}`\n"
                    f"• *Workspace:* `{self.config.workspace_path}`\n\n"
                    "You can now send any prompt, instruction, or coding task from your phone.\n"
                    "If the agent needs your input, it will send interactive buttons right here!\n\n"
                    "Commands:\n"
                    "/status - Show current state\n"
                    "/new - Start fresh conversation\n"
                    "/stop - Stop running agent turn\n"
                    "/model <name> - Switch Gemini model"
                )
                await self.client.send_message(chat_id, welcome, parse_mode="Markdown")
                return

            if text in ["/new", "/reset"]:
                self.engine.reset_conversation()
                self.pending_choices.clear()
                self.ui.log_success("Conversation reset by Telegram user.")
                await self.client.send_message(chat_id, "🔄 *Conversation reset.* Started a fresh session.")
                return

            if text in ["/stop", "/cancel"]:
                if self.engine and self.engine.stop_current_task():
                    await self.client.send_message(chat_id, "🛑 Agent task was stopped.")
                    self.ui.log_success("Stopped agent task by Telegram request.")
                else:
                    await self.client.send_message(chat_id, "ℹ️ No task is currently running.")
                return

            if text == "/status":
                status = "Busy" if self.is_processing else "Idle"
                await self.client.send_message(
                    chat_id,
                    f"📊 *Bot Status:*\n• State: `{status}`\n• Model: `{self.engine.model}`\n• Workspace: `{self.config.workspace_path}`"
                )
                return

            if text.startswith("/model"):
                parts = text.split(maxsplit=1)
                if len(parts) > 1:
                    new_model = parts[1].strip()
                    self.engine.model = new_model
                    self.config.default_model = new_model
                    self.config.save()
                    await self.client.send_message(chat_id, f"✅ Active model updated to: `{new_model}`")
                    self.ui.log_success(f"Model switched to {new_model} via Telegram")
                else:
                    await self.client.send_message(chat_id, f"Current model: `{self.engine.model}`\nUsage: `/model gemini-2.5-pro`")
                return

            # Check if user typed a choice number (1, 2, 3...) when a question is pending
            if self.pending_choices:
                try:
                    choice_idx = int(text)
                    if choice_idx in self.pending_choices:
                        selected_val = self.pending_choices[choice_idx]
                        self.pending_choices.clear()
                        await self.client.send_message(chat_id, f"✅ *Selected:* {selected_val}")
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
