import asyncio
import logging
import os
import signal
from typing import AsyncGenerator, Optional, Tuple, Callable

logger = logging.getLogger("agy_engine")

KNOWN_MODELS = [
    "Gemini 4 Argon (Medium)",
    "Gemini 4 Argon (High)",
    "Gemini 4 Argon (Low)",
    "Gemini 3.8 Flash (High)",
    "Gemini 3.8 Flash (Medium)",
    "Gemini 3.8 Flash (Low)",
    "Claude Opus 5.5 (Low)",
    "Claude Opus 5.5 (Medium)",
    "Claude Opus 5.5 (High)",
    "Claude Sonnet 5.5 (Low)",
    "Claude Sonnet 5.5 (Medium)",
    "Claude Sonnet 5.5 (High)",
    "GPT-OSS 120B (Medium)",
]

def normalize_model_name(model_name: Optional[str]) -> str:
    if not model_name:
        return "Gemini 3.8 Flash (Medium)"
    # Exact case-insensitive match
    for km in KNOWN_MODELS:
        if model_name.strip().lower() == km.lower():
            return km
    # Match without effort, defaulting to (Medium)
    cleaned = model_name.strip().lower().replace("-", " ")
    for km in KNOWN_MODELS:
        base = km.split("(")[0].strip().lower()
        if cleaned == base and "(medium)" in km.lower():
            return km
    return model_name.strip()

class AgyEngine:
    def __init__(
        self,
        agy_path: str,
        workspace_path: str,
        default_model: str = "Gemini 3.8 Flash (Medium)",
        dangerously_skip_permissions: bool = True
    ):
        self.agy_path = agy_path
        self.workspace_path = workspace_path
        self.model = normalize_model_name(default_model)
        self.dangerously_skip_permissions = dangerously_skip_permissions
        self.current_process: Optional[asyncio.subprocess.Process] = None
        self.conversation_id: Optional[str] = None
        self.is_running = False

    def reset_conversation(self):
        """Starts a fresh conversation on the next prompt."""
        self.conversation_id = None

    def stop_current_task(self) -> bool:
        """Kills the currently running agy turn if active."""
        if self.current_process and self.is_running:
            try:
                self.current_process.send_signal(signal.SIGINT)
                logger.info("Sent SIGINT to agy process")
                return True
            except ProcessLookupError:
                pass
            except Exception as e:
                logger.error(f"Error stopping agy process: {e}")
        return False

    async def execute_turn(
        self,
        prompt: str,
        on_chunk: Optional[Callable[[str], None]] = None
    ) -> Tuple[int, str]:
        """
        Executes a single prompt turn using `agy -p`.
        Streams stdout/stderr and returns (exit_code, full_output).
        """
        cmd = [self.agy_path, "-p", prompt]

        if self.conversation_id:
            if self.conversation_id == "continue":
                cmd.append("-c")
            else:
                cmd.extend(["--conversation", self.conversation_id])
        else:
            # First turn: start fresh
            pass

        norm_model = normalize_model_name(self.model)
        if norm_model:
            cmd.extend(["--model", norm_model])

        if self.dangerously_skip_permissions:
            cmd.append("--dangerously-skip-permissions")

        logger.info(f"Starting agy: {' '.join(cmd)} in {self.workspace_path}")

        env = os.environ.copy()
        # Set unbuffered python or environment flags if helpful
        env["PYTHONUNBUFFERED"] = "1"

        try:
            self.is_running = True
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.workspace_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )
            self.current_process = process

            full_output_chunks = []

            async def read_stream(stream, is_stderr=False):
                while True:
                    line = await stream.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace")
                    full_output_chunks.append(decoded)
                    if on_chunk:
                        on_chunk(decoded)

            # Read stdout and stderr concurrently
            await asyncio.gather(
                read_stream(process.stdout, is_stderr=False),
                read_stream(process.stderr, is_stderr=True)
            )

            await process.wait()
            exit_code = process.returncode if process.returncode is not None else 0
            full_text = "".join(full_output_chunks).strip()

            # Attempt to discover conversation ID from logs or transcripts if available
            # In agy, after turn 1, subsequent turns can continue using --continue
            if not self.conversation_id:
                # Mark that conversation has started so next turn can pass --continue
                self.conversation_id = "continue"

            return exit_code, full_text

        except asyncio.CancelledError:
            self.stop_current_task()
            raise
        except Exception as e:
            logger.error(f"Error running agy: {e}")
            return 1, f"Execution failed: {e}"
        finally:
            self.is_running = False
            self.current_process = None
