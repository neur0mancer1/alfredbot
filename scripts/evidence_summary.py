"""Generate an anonymised, dated snapshot of Alfred's persisted usage."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def summarise(root: Path) -> dict:
    households = []
    receipts = []
    learned_items = 0

    for folder in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("demo")):
        household = load_json(folder / "household.json")
        if household:
            households.append(household)
        learned_items += len(load_json(folder / "memory.json"))
        for path in folder.glob("*.json"):
            if path.name in {"household.json", "memory.json"}:
                continue
            receipt = load_json(path)
            if receipt.get("status") in {"settled", "paid"}:
                receipts.append(receipt)

    statuses = Counter(r.get("status") for r in receipts)
    unique_refs = {r["order_ref"] for r in receipts if r.get("order_ref")}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Non-demo local persisted records; may include development/test activity.",
        "households": len(households),
        "configured_members": sum(len(h.get("members", [])) for h in households),
        "completed_settlements": len(receipts),
        "paid_confirmations": statuses["paid"],
        "distinct_order_references": len(unique_refs),
        "learned_item_preferences": learned_items,
        "status_counts": dict(statuses),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data/store")
    parser.add_argument("--output")
    args = parser.parse_args()
    result = summarise(Path(args.root))
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
