import asyncio
import sys
import datetime
from typing import Optional, Callable

# ANSI color codes
RESET = "\033[0m"
BOLD = "\033[1m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"

class TerminalUI:
    def __init__(self):
        self._running = False
        self._input_callback: Optional[Callable[[str], None]] = None

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now().strftime("%H:%M:%S")

    def print_banner(self, bot_username: str, model: str, workspace: str):
        print("\n" + "=" * 60)
        print(f"{BOLD}{CYAN}🤖 Antigravity Telegram & Terminal Bridge{RESET}")
        print("=" * 60)
        print(f" • Bot Username:  {BOLD}{GREEN}@{bot_username}{RESET}")
        print(f" • Active Model:  {BOLD}{YELLOW}{model}{RESET}")
        print(f" • Workspace:     {BOLD}{WHITE}{workspace}{RESET}")
        print(f" • Status:        {BOLD}{GREEN}ONLINE & LISTENING{RESET}")
        print("=" * 60)
        print(f"{WHITE}Type prompts here in the terminal OR send them via Telegram.{RESET}")
        print(f"{WHITE}Commands: /status, /new, /stop, /model <name>, /exit{RESET}")
        print("-" * 60 + "\n")

    def log_info(self, message: str):
        print(f"{BLUE}[{self._now()} INFO]{RESET} {message}")

    def log_success(self, message: str):
        print(f"{GREEN}[{self._now()} SUCCESS]{RESET} {message}")

    def log_warn(self, message: str):
        print(f"{YELLOW}[{self._now()} WARNING]{RESET} {message}")

    def log_error(self, message: str):
        print(f"{RED}[{self._now()} ERROR]{RESET} {message}")

    def log_telegram_in(self, user_name: str, message: str):
        print(f"\n{GREEN}[{self._now()} TELEGRAM from {user_name}]{RESET} {message}")

    def log_local_in(self, message: str):
        print(f"\n{CYAN}[{self._now()} LOCAL TERMINAL]{RESET} {message}")

    def log_agent_chunk(self, chunk: str):
        # Print stream chunk with yellow tint
        sys.stdout.write(f"{YELLOW}{chunk}{RESET}")
        sys.stdout.flush()

    def log_agent_complete(self, output: str):
        print(f"\n{BOLD}{YELLOW}[{self._now()} AGY RESPONSE]{RESET}\n{output}\n")

    def log_question(self, question: str, options: list):
        print(f"\n{BOLD}{MAGENTA}[{self._now()} QUESTION FOR YOU]{RESET} {question}")
        for opt in options:
            label = opt.label if hasattr(opt, 'label') else str(opt)
            print(f"   {BOLD}{MAGENTA}🔘 {label}{RESET}")
        print(f"{MAGENTA}(Select via Telegram button or type option number here){RESET}\n")

    def set_input_callback(self, callback: Callable[[str], None]):
        self._input_callback = callback

    async def start_input_listener(self):
        """Listens asynchronously to terminal stdin without blocking the event loop."""
        self._running = True
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while self._running:
            try:
                line = await reader.readline()
                if not line:
                    break
                text = line.decode().strip()
                if text and self._input_callback:
                    await self._input_callback(text)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log_error(f"Error in stdin reader: {e}")
                break

    def stop(self):
        self._running = False
