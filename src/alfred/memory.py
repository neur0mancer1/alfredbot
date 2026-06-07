"""Learning layer — remembers a household's standing splits so next week auto-drafts.

Hidden behind ``MemoryStore`` so the rest of the app never knows or cares which
implementation it's talking to. We run on ``LocalMemory`` (a JSON-backed prior)
now, and swap in a Mubit-backed store later for the +10 — no other code changes.

The key is a *normalised* item name ("Tesco British Chicken Thighs 1Kg" ->
"british chicken thighs") so the same product matches itself week to week.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Protocol

from .config import DATA_DIR

# strip size/qty tokens like "1kg", "500g", "1 litre", "2x80g", "6x2l", "3 pack"
_SIZE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:kg|g|l|ml|cl|litre|ltr|x\d+|pack|each|pk|pcs)\b", re.I
)
_BRANDS = ("tesco", "finest", "heinz", "mlekovita")  # light touch; enough to match


def normalize_name(name: str) -> str:
    s = name.lower()
    s = _SIZE.sub(" ", s)
    for b in _BRANDS:
        s = s.replace(b, " ")
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


class MemoryStore(Protocol):
    def suggest(self, household_id: str, item_name: str) -> set[str] | None: ...
    def remember(self, household_id: str, item_name: str, assignment: set[str]) -> None: ...


class LocalMemory:
    """JSON-backed prior: normalised item -> {"rob|sam": count, ...}. Most-seen wins."""

    def __init__(self, root: str = DATA_DIR):
        self.root = Path(root)

    def _path(self, household_id: str) -> Path:
        return self.root / household_id / "memory.json"

    def _load(self, household_id: str) -> dict:
        p = self._path(household_id)
        return json.loads(p.read_text()) if p.exists() else {}

    def suggest(self, household_id: str, item_name: str) -> set[str] | None:
        counts = self._load(household_id).get(normalize_name(item_name))
        if not counts:
            return None
        best = max(counts, key=counts.get)  # the assignment seen most often
        return set(best.split("|")) if best else None

    def remember(self, household_id: str, item_name: str, assignment: set[str]) -> None:
        data = self._load(household_id)
        bucket = data.setdefault(normalize_name(item_name), {})
        key = "|".join(sorted(assignment))
        bucket[key] = bucket.get(key, 0) + 1
        p = self._path(household_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, indent=2))


class LayeredMemory:
    """Mubit as the operational memory (primary, cross-session, judge-verifiable)
    with LocalMemory as an instant write-through cache that covers Mubit's
    async-index lag. Writes go to both; reads prefer Mubit, fall back to local."""

    def __init__(self, primary: MemoryStore, fallback: MemoryStore):
        self.primary = primary
        self.fallback = fallback

    def remember(self, household_id: str, item_name: str, assignment: set[str]) -> None:
        try:
            self.primary.remember(household_id, item_name, assignment)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("alfred").warning("Mubit remember failed: %s", exc)
        self.fallback.remember(household_id, item_name, assignment)

    def suggest(self, household_id: str, item_name: str) -> set[str] | None:
        try:
            s = self.primary.suggest(household_id, item_name)
            if s:
                return s
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("alfred").warning("Mubit suggest failed: %s", exc)
        return self.fallback.suggest(household_id, item_name)


def get_memory() -> MemoryStore:
    """Mubit (+ local write-through) when MUBIT_API_KEY is set, else local JSON."""
    if os.environ.get("MUBIT_API_KEY"):
        try:
            from .memory_mubit import MubitMemory
            return LayeredMemory(MubitMemory(), LocalMemory())
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("alfred").warning(
                "Mubit unavailable (%s) — falling back to LocalMemory", exc
            )
    return LocalMemory()
