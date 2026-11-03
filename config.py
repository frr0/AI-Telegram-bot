import json
import os
from pathlib import Path
from typing import List, Optional

CONFIG_FILE = Path(__file__).parent / "config.json"
EXAMPLE_FILE = Path(__file__).parent / "config.example.json"

class Config:
    def __init__(self):
        self.bot_token: str = ""
        self.allowed_user_ids: List[int] = []
        self.workspace_path: str = str(Path(__file__).parent.resolve())
        self.agy_path: str = os.path.expanduser("~/.local/bin/agy")
        self.default_model: str = "Gemini 3.8 Flash"
        self.dangerously_skip_permissions: bool = True
        self.load()

    def load(self):
        if not CONFIG_FILE.exists():
            if EXAMPLE_FILE.exists():
                with open(EXAMPLE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                data = {
                    "bot_token": "",
                    "allowed_user_ids": [],
                    "workspace_path": self.workspace_path,
                    "agy_path": self.agy_path,
                    "default_model": self.default_model,
                    "dangerously_skip_permissions": True
                }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.bot_token = data.get("bot_token", "").strip()
        self.allowed_user_ids = [int(uid) for uid in data.get("allowed_user_ids", [])]
        self.workspace_path = data.get("workspace_path", self.workspace_path)
        self.agy_path = data.get("agy_path", self.agy_path)
        self.default_model = data.get("default_model", "Gemini 3.8 Flash")
        self.dangerously_skip_permissions = data.get("dangerously_skip_permissions", True)

    def save(self):
        data = {
            "bot_token": self.bot_token,
            "allowed_user_ids": self.allowed_user_ids,
            "workspace_path": self.workspace_path,
            "agy_path": self.agy_path,
            "default_model": self.default_model,
            "dangerously_skip_permissions": self.dangerously_skip_permissions
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add_allowed_user(self, user_id: int):
        if user_id not in self.allowed_user_ids:
            self.allowed_user_ids.append(user_id)
            self.save()

    def is_user_allowed(self, user_id: int) -> bool:
        # If no user IDs are configured yet, allow the first user and register them
        if not self.allowed_user_ids:
            self.add_allowed_user(user_id)
            return True
        return user_id in self.allowed_user_ids
