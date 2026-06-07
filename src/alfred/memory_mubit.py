"""Mubit-backed memory — the +10 wedge on real operational memory.

Drop-in for ``LocalMemory`` (same ``suggest`` / ``remember`` interface). Mubit's
recall is *semantic* (it returns a synthesised answer + evidence), so to keep our
suggestions deterministic we embed a machine-readable tag in each remembered
lesson and parse it back out of the recall evidence. Keyed per household via
``user_id`` + a global lesson scope — Mubit's documented cross-session pattern.

Validated live once the API key is set; until then ``get_memory`` keeps us on
LocalMemory, so a missing/duff key never breaks the bot.
"""

from __future__ import annotations

import os

from .memory import normalize_name


class MubitMemory:
    def __init__(self) -> None:
        import mubit  # lazy: only needed when actually selected

        # transport="http": the gRPC path rejects the SDK's lowercase enum defaults;
        # HTTP accepts them and returns evidence content verbatim (tags survive).
        self.client = mubit.Client(
            endpoint=os.environ.get("MUBIT_ENDPOINT", "https://api.mubit.ai"),
            transport="http",
        )
        self.client.set_api_key(os.environ["MUBIT_API_KEY"])
        self.agent_id = "alfred"
        self._cache: dict[str, dict[str, set[str] | None]] = {}

    def remember(self, household_id: str, item_name: str, assignment: set[str]) -> None:
        norm = normalize_name(item_name)
        akey = "|".join(sorted(assignment))
        self.client.remember(
            session_id=f"alfred:{household_id}",
            agent_id=self.agent_id,
            user_id=household_id,
            content=(
                f"In household {household_id}, grocery item '{norm}' is usually claimed by "
                f"{akey.replace('|', ' and ')}. ALFRED_ITEM={norm} ALFRED_ASSIGN={akey}"
            ),
            intent="lesson",
            lesson_scope="global",
        )
        self._cache.pop(household_id, None)  # invalidate after a write

    def _household_map(self, household_id: str) -> dict[str, set[str] | None]:
        """One recall per household (cached), parsed back into item -> assignment."""
        if household_id in self._cache:
            return self._cache[household_id]
        out: dict[str, set[str] | None] = {}
        try:
            ans = self.client.recall(
                session_id=f"alfred:{household_id}:q",
                agent_id=self.agent_id,
                user_id=household_id,
                query="every grocery item and who usually claims it",
                entry_types=["lesson"],
                limit=100,
            )
            evidence = ans.get("evidence") if isinstance(ans, dict) else []
        except Exception:  # noqa: BLE001  (recall failure -> no suggestions, not a crash)
            evidence = []

        for ev in evidence or []:
            content = ev.get("content", "")
            if "ALFRED_ITEM=" in content and "ALFRED_ASSIGN=" in content:
                item = content.split("ALFRED_ITEM=", 1)[1].split()[0]
                seg = content.split("ALFRED_ASSIGN=", 1)[1].split()[0]
                out[item] = set(seg.split("|")) if seg else None
        self._cache[household_id] = out
        return out

    def suggest(self, household_id: str, item_name: str) -> set[str] | None:
        return self._household_map(household_id).get(normalize_name(item_name))
