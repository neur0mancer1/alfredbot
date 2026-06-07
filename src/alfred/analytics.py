"""Privacy-conscious, append-only product usage events."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR


class Analytics:
    def __init__(self, root: str = DATA_DIR):
        self.path = Path(root) / "analytics.jsonl"

    @staticmethod
    def anonymise(kind: str, value: int | str) -> str:
        raw = f"alfred:{kind}:{value}".encode()
        return hashlib.sha256(raw).hexdigest()[:12]

    def track(
        self,
        event: str,
        *,
        chat_id: int | str | None = None,
        user_id: int | str | None = None,
        properties: dict | None = None,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "household": self.anonymise("chat", chat_id) if chat_id is not None else None,
            "user": self.anonymise("user", user_id) if user_id is not None else None,
            "properties": properties or {},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    def events(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
