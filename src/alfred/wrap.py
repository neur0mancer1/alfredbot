"""Wrapped — weekly/monthly household summary + funny per-member awards.

Pure functions over the saved shop JSONs (no bot/Telegram deps). Awards are
rule-based over what the household actually buys; the household's learned splits
(its Mubit/local memory) are surfaced as a "signature" insight, so the wrap is a
visible payoff of the operational memory.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from .config import DATA_DIR
from .money import allocate_pence, format_money as fm

_SKIP = {"household.json", "memory.json"}

# keyword buckets -> (emoji, award title). First match wins per item.
_AWARDS = [
    (("chicken",), "🍗", "Store Manager at Los Pollos Hermanos"),
    (("coffee", "espresso", "nespresso"), "☕", "Caffeine Baron"),
    (("wine", "beer", "lager", "gin", "vodka", "cider", "prosecco"), "🍷", "Resident Sommelier"),
    (("chocolate", "haribo", "sweets", "biscuit", "cookie", "crisps", "doritos"), "🍫", "Chief Snack Officer"),
    (("salmon", "steak", "mince", "beef", "pork", "protein"), "🥩", "Protein Maxxer"),
    (("milk", "yoghurt", "cheese", "butter"), "🥛", "Dairy Devotee"),
    (("avocado", "banana", "tomato", "lettuce", "pepper", "spinach", "berries"), "🥑", "Greengrocer of the Year"),
]


def _load_receipts(household_id: str, root: str = DATA_DIR) -> list[dict]:
    folder = Path(root) / household_id
    out: list[dict] = []
    if folder.exists():
        for p in folder.glob("*.json"):
            if p.name in _SKIP:
                continue
            try:
                r = json.loads(p.read_text())
                if r.get("total_paid_pence"):       # skip £0 / failed parses
                    out.append(r)
            except (json.JSONDecodeError, OSError):
                pass
    return out


def _ts(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None


def _in_period(rec: dict, period: str) -> bool:
    if period == "all":
        return True
    days = 7 if period == "week" else 30
    when = _ts(rec.get("settled_at")) or _ts(rec.get("ordered_at"))
    if not when:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= datetime.now(timezone.utc) - timedelta(days=days)


def _member_shares(rec: dict) -> dict[str, int]:
    shares = {m["id"]: 0 for m in rec.get("members", [])}
    for it in rec.get("items", []):
        a = it.get("assigned_to")
        if not a:
            continue
        targets = [mid for mid in shares if mid in a]
        if not targets:
            continue
        for mid, part in zip(targets, allocate_pence(it["charged_pence"], [1] * len(targets))):
            shares[mid] += part
    return shares


def _short_item(name: str, limit: int = 38) -> str:
    name = name.removeprefix("Tesco ").strip()
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def _awards(receipts: list[dict], name_of: dict[str, str]) -> list[str]:
    tally: dict[tuple[str, str], int] = {}
    products: dict[tuple[str, str], list[str]] = {}
    for r in receipts:
        for it in r.get("items", []):
            nm = it["name"].lower()
            for keys, _emoji, title in _AWARDS:
                if any(k in nm for k in keys):
                    for mid in (it.get("assigned_to") or []):
                        tally[(mid, title)] = tally.get((mid, title), 0) + 1
                        names = products.setdefault((mid, title), [])
                        if it["name"] not in names:
                            names.append(it["name"])
                    break
    best: dict[str, tuple[str, int]] = {}
    for (mid, title), c in tally.items():
        if title not in best or c > best[title][1]:
            best[title] = (mid, c)
    lines = []
    for keys, emoji, title in _AWARDS:
        if title in best:
            mid, _count = best[title]
            ordered = ", ".join(_short_item(x) for x in products[(mid, title)][:2])
            lines.append(f"{emoji} {name_of.get(mid, mid)} — {title}: {ordered}")
    return lines[:4]


def _signature_splits(household_id: str, name_of: dict[str, str], root: str = DATA_DIR) -> list[str]:
    """Surface the household's learned standing splits (its memory)."""
    p = Path(root) / household_id / "memory.json"
    if not p.exists():
        return []
    try:
        mem = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    out = []
    for item, counts in list(mem.items())[:3]:
        if not counts:
            continue
        best = max(counts, key=counts.get)
        who = " & ".join(name_of.get(x, x) for x in best.split("|")) if best else "?"
        out.append(f"• {item} → {who}")
    return out


def _age(iso: str | None) -> str:
    when = _ts(iso)
    if not when:
        return "recently"
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - when).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


def _duration(hours: float) -> str:
    if hours < 1:
        minutes = round(hours * 60)
        return f"{minutes}m" if minutes else "<1m"
    return f"{hours:.0f}h"


def unpaid(household_id: str, root: str = DATA_DIR) -> list[dict]:
    """Settled shops still awaiting payment, oldest first."""
    recs = [r for r in _load_receipts(household_id, root)
            if r.get("status") == "settled" and r.get("settlement")]
    recs.sort(key=lambda r: r.get("settled_at") or "")
    return recs


def nudge(household_id: str, root: str = DATA_DIR) -> tuple[str, list[tuple[str, str]]]:
    """(text, [(button_label, receipt_id)]) for an on-demand 'who still owes' reminder.

    The receipt_id maps straight onto the bot's existing ``paid:<id>`` confirm flow.
    """
    recs = unpaid(household_id, root)
    if not recs:
        return "🎩 All square, sir — nothing outstanding. 🎉", []
    name_of: dict[str, str] = {}
    for r in recs:
        for m in r.get("members", []):
            name_of[m["id"]] = m["name"]
    lines = ["🎩 A gentle nudge, sir — these shops are settled but not yet paid:"]
    buttons: list[tuple[str, str]] = []
    for r in recs:
        age = _age(r.get("settled_at"))
        for t in r.get("settlement", []):
            lines.append(
                f"• {name_of.get(t['from'], t['from'])} → {name_of.get(t['to'], t['to'])}: "
                f"{fm(t['pence'])}  ({r.get('retailer', 'Tesco')}, {age})")
        buttons.append(
            (f"🎩 Confirm {r.get('retailer', 'Tesco')} {fm(r['total_paid_pence'])} received", r["id"]))
    return "\n".join(lines), buttons


def render(household_id: str, period: str = "month") -> str:
    receipts = [r for r in _load_receipts(household_id) if _in_period(r, period)]
    name_of: dict[str, str] = {}
    for r in receipts:
        for m in r.get("members", []):
            name_of[m["id"]] = m["name"]

    label = {"week": "This week", "month": "This month", "all": "All time"}.get(period, period)
    if not receipts:
        return f"🎩 {label}: no shops yet, sir. Forward a receipt and I'll start the tally."

    total = sum(r["total_paid_pence"] for r in receipts)
    per_member: dict[str, int] = {}
    paid_lags = []
    for r in receipts:
        for mid, pence in _member_shares(r).items():
            per_member[mid] = per_member.get(mid, 0) + pence
        settled, paid = _ts(r.get("settled_at")), _ts(r.get("paid_at"))
        if settled and paid:
            paid_lags.append(max(0, (paid - settled).total_seconds() / 3600))

    biggest = max(receipts, key=lambda r: r["total_paid_pence"])
    top_spender = max(per_member, key=per_member.get) if per_member else None

    lines = [f"🎩 *Alfred Wrapped — {label}*",
             f"🧾 {len(receipts)} shop{'s' if len(receipts) != 1 else ''} · {fm(total)} total",
             f"🏆 Biggest shop: {fm(biggest['total_paid_pence'])} ({biggest.get('retailer','Tesco')})"]
    if top_spender:
        lines.append(f"💸 Top spender: {name_of.get(top_spender, top_spender)} ({fm(per_member[top_spender])})")
    if paid_lags:
        avg = sum(paid_lags) / len(paid_lags)
        lines.append(f"⏱️ Avg time to confirm payment: {_duration(avg)}")

    awards = _awards(receipts, name_of)
    if awards:
        lines.append("\n*Awards 🏅*")
        lines += awards

    sig = _signature_splits(household_id, name_of)
    if sig:
        lines.append("\n*Your signature splits* (remembered)")
        lines += sig

    return "\n".join(lines)
