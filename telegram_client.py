import asyncio
import logging
import aiohttp
from typing import Dict, Any, Optional, List, Union

logger = logging.getLogger("telegram_client")

class TelegramClient:
    def __init__(self, token: str):
        self.token = token
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._session: Optional[aiohttp.ClientSession] = None

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=45, connect=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        url = f"{self.base_url}/{method}"
        session = await self.get_session()
        try:
            async with session.post(url, json=params or {}) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.warning(f"Telegram API {method} error: {data.get('description')}")
                return data
        except Exception as e:
            logger.error(f"Telegram network error on {method}: {e}")
            return None

    async def get_me(self) -> Optional[Dict[str, Any]]:
        res = await self._request("getMe")
        if res and res.get("ok"):
            return res.get("result")
        return None

    async def get_updates(self, offset: Optional[int] = None, timeout: int = 30) -> List[Dict[str, Any]]:
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        res = await self._request("getUpdates", params)
        if res and res.get("ok"):
            return res.get("result", [])
        return []

    async def send_chat_action(self, chat_id: Union[int, str], action: str = "typing"):
        await self._request("sendChatAction", {"chat_id": chat_id, "action": action})

    async def send_message(
        self,
        chat_id: Union[int, str],
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: Optional[str] = "Markdown"
    ) -> Optional[Dict[str, Any]]:
        if not text:
            return None

        # Handle message length limit (4096 chars in Telegram)
        max_len = 4000
        if len(text) > max_len:
            chunks = [text[i:i + max_len] for i in range(0, len(text), max_len)]
            last_res = None
            for idx, chunk in enumerate(chunks):
                # Only attach reply_markup to the final chunk
                markup = reply_markup if idx == len(chunks) - 1 else None
                last_res = await self._send_chunk(chat_id, chunk, markup, parse_mode)
            return last_res
        else:
            return await self._send_chunk(chat_id, text, reply_markup, parse_mode)

    async def _send_chunk(
        self,
        chat_id: Union[int, str],
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: Optional[str] = "Markdown"
    ) -> Optional[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
        }
        if reply_markup:
            params["reply_markup"] = reply_markup
        if parse_mode:
            params["parse_mode"] = parse_mode

        res = await self._request("sendMessage", params)
        # If Markdown fails due to special character parsing, fall back to plain text
        if not res or not res.get("ok"):
            if parse_mode:
                params.pop("parse_mode", None)
                res = await self._request("sendMessage", params)

        if res and res.get("ok"):
            return res.get("result")
        return None

    async def edit_message_text(
        self,
        chat_id: Union[int, str],
        message_id: int,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        parse_mode: Optional[str] = "Markdown"
    ) -> Optional[Dict[str, Any]]:
        params: Dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4000],
        }
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        if parse_mode:
            params["parse_mode"] = parse_mode

        res = await self._request("editMessageText", params)
        if not res or not res.get("ok"):
            if parse_mode:
                params.pop("parse_mode", None)
                res = await self._request("editMessageText", params)
        return res

    async def answer_callback_query(
        self,
        callback_query_id: str,
        text: Optional[str] = None,
        show_alert: bool = False
    ):
        params: Dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            params["text"] = text
        if show_alert:
            params["show_alert"] = show_alert
        await self._request("answerCallbackQuery", params)

    @staticmethod
    def build_inline_keyboard(buttons: List[List[Dict[str, str]]]) -> Dict[str, Any]:
        """
        Takes a list of rows, each row containing {"text": "...", "callback_data": "..."}
        """
        return {"inline_keyboard": buttons}

    @staticmethod
    def build_reply_keyboard(
        buttons: List[List[Union[str, Dict[str, Any]]]],
        resize_keyboard: bool = True,
        is_persistent: bool = True
    ) -> Dict[str, Any]:
        """
        Takes a list of rows, each row containing list of string labels or button dicts.
        """
        keyboard = []
        for row in buttons:
            row_btns = []
            for btn in row:
                if isinstance(btn, str):
                    row_btns.append({"text": btn})
                elif isinstance(btn, dict):
                    row_btns.append(btn)
            keyboard.append(row_btns)
        return {
            "keyboard": keyboard,
            "resize_keyboard": resize_keyboard,
            "is_persistent": is_persistent
        }

    async def set_my_commands(self, commands: List[Dict[str, str]]) -> bool:
        """
        Sets the list of the bot's commands in Telegram.
        Each command dict has: {"command": "menu", "description": "Apri il menu"}
        """
        res = await self._request("setMyCommands", {"commands": commands})
        return bool(res and res.get("ok"))
