"""Paid-access gate: one activation code unlocks one Telegram chat."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR


class AccessStore:
    def __init__(self, root: str = DATA_DIR):
        self.root = Path(root)
        self.path = self.root / "access_codes.json"

    def _load(self) -> dict:
        if not self.path.exists():
            return {"codes": {}, "chats": {}}
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"codes": {}, "chats": {}}
        data.setdefault("codes", {})
        data.setdefault("chats", {})
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def is_activated(self, chat_id: int | str) -> bool:
        return str(chat_id) in self._load()["chats"]

    def issue(self, *, kind: str = "promo", note: str = "") -> str:
        data = self._load()
        while True:
            code = f"ALFRED-{secrets.token_hex(3).upper()}"
            if code not in data["codes"]:
                break
        data["codes"][code] = {
            "kind": kind,
            "note": note,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "redeemed_at": None,
            "chat_id": None,
        }
        self._save(data)
        return code

    def activate(self, code: str, chat_id: int | str) -> tuple[bool, str]:
        code = code.strip().upper()
        chat_key = str(chat_id)
        data = self._load()
        if chat_key in data["chats"]:
            return True, "This household is already activated."
        entry = data["codes"].get(code)
        if not entry:
            return False, "That activation code is invalid."
        if entry.get("chat_id") is not None:
            return False, "That activation code has already been used."
        now = datetime.now(timezone.utc).isoformat()
        entry["chat_id"] = chat_key
        entry["redeemed_at"] = now
        data["chats"][chat_key] = {"code": code, "activated_at": now, "kind": entry["kind"]}
        self._save(data)
        return True, "Household activated."

    def grandfather_existing_households(self) -> int:
        data = self._load()
        added = 0
        if self.root.exists():
            for path in self.root.glob("*/household.json"):
                chat_key = path.parent.name
                if chat_key not in data["chats"]:
                    data["chats"][chat_key] = {
                        "code": None,
                        "activated_at": datetime.now(timezone.utc).isoformat(),
                        "kind": "grandfathered",
                    }
                    added += 1
        self._save(data)
        return added
