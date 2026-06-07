"""Storage — one JSON file per shop. Simple, debuggable, zero dependencies.

A settled shop is a self-contained blob (items + claims + the result), so it maps
naturally onto a single file. We only write once the shop is settled, so two people
claiming at the same time can't clobber each other mid-flow. The domain models
don't know or care they're being saved as JSON — storage is a thin edge layer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR
from .models import Receipt
from .settlement import Transaction


def receipt_to_dict(receipt: Receipt, settlement: list[Transaction]) -> dict:
    return {
        "id": receipt.id,
        "order_ref": receipt.order_ref,
        "household_id": receipt.household_id,
        "retailer": receipt.retailer,
        "ordered_at": receipt.created_at,
        "payer_id": receipt.payer_id,
        "total_paid_pence": receipt.stated_total_pence,
        "members": [{"id": m.id, "name": m.name} for m in receipt.members],
        "items": [
            {
                "name": i.name,
                "quantity": i.quantity,
                "charged_pence": i.total_pence,
                "is_overhead": i.is_overhead,
                "assigned_to": sorted(i.assigned_to) if i.assigned_to else None,
            }
            for i in receipt.items
        ],
        "settlement": [
            {"from": t.from_id, "to": t.to_id, "pence": t.pence} for t in settlement
        ],
    }


def save_receipt(
    receipt: Receipt, settlement: list[Transaction], *,
    status: str = "settled", payer_tg_id: int | None = None, root: str = DATA_DIR,
) -> Path:
    folder = Path(root) / receipt.household_id
    folder.mkdir(parents=True, exist_ok=True)
    rec = receipt_to_dict(receipt, settlement)
    rec["status"] = status                              # "settled" -> "paid"
    rec["payer_tg_id"] = payer_tg_id                    # only they may confirm receipt
    rec["settled_at"] = datetime.now(timezone.utc).isoformat()
    rec["paid_at"] = None
    path = folder / f"{receipt.id}.json"
    path.write_text(json.dumps(rec, indent=2))
    return path


def save_household(household_id: str, members, chat_id, payer_id: str | None = None,
                   pay: dict | None = None, emails: dict | None = None,
                   telegram_ids: dict | None = None,
                   root: str = DATA_DIR) -> Path:
    """Persist a household (members, who paid, payment handles, emails) across restarts."""
    folder = Path(root) / household_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "household.json"
    path.write_text(json.dumps({
        "chat_id": chat_id,
        "payer_id": payer_id,
        "members": [{"id": m.id, "name": m.name} for m in members],
        "pay": pay or {},
        "emails": emails or {},
        "telegram_ids": telegram_ids or {},
    }, indent=2))
    return path


def find_household_by_email(addr: str, root: str = DATA_DIR) -> tuple[str, str] | None:
    """Map a sender address -> (household_id, member_id) by scanning saved households."""
    base = Path(root)
    if not base.exists():
        return None
    for hh_file in base.glob("*/household.json"):
        try:
            data = json.loads(hh_file.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for member_id, email_addr in (data.get("emails") or {}).items():
            if email_addr.lower() == addr.lower():
                return hh_file.parent.name, member_id
    return None


def load_household(household_id: str, root: str = DATA_DIR) -> dict | None:
    path = Path(root) / household_id / "household.json"
    return json.loads(path.read_text()) if path.exists() else None


def settled_order_refs(household_id: str, root: str = DATA_DIR) -> set[str]:
    """Order numbers this household has already settled (for duplicate detection)."""
    folder = Path(root) / household_id
    refs: set[str] = set()
    if folder.exists():
        for p in folder.glob("*.json"):
            if p.name in ("household.json", "memory.json"):
                continue
            try:
                ref = json.loads(p.read_text()).get("order_ref", "")
                if ref:
                    refs.add(ref)
            except (json.JSONDecodeError, OSError):
                pass
    return refs


def load_receipt(household_id: str, receipt_id: str, root: str = DATA_DIR) -> dict | None:
    path = Path(root) / household_id / f"{receipt_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def mark_receipt_paid(household_id: str, receipt_id: str, root: str = DATA_DIR) -> dict | None:
    """Payer confirmed the money landed -> flip status to 'paid' with a timestamp."""
    path = Path(root) / household_id / f"{receipt_id}.json"
    if not path.exists():
        return None
    rec = json.loads(path.read_text())
    rec["status"] = "paid"
    rec["paid_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(rec, indent=2))
    return rec


def mark_nudge_sent(
    household_id: str, receipt_id: str, stage_hours: int, root: str = DATA_DIR,
) -> dict | None:
    """Persist the latest automatic nudge stage so restarts do not resend it."""
    path = Path(root) / household_id / f"{receipt_id}.json"
    if not path.exists():
        return None
    rec = json.loads(path.read_text())
    rec["nudge_stage_hours"] = stage_hours
    rec["last_nudged_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(rec, indent=2))
    return rec
