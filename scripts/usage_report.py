"""Generate a judge-facing report from Alfred's append-only analytics."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from alfred.analytics import Analytics


def build_report(root: str) -> dict:
    events = Analytics(root).events()
    counts = Counter(e["event"] for e in events)
    households_by_event: dict[str, set[str]] = defaultdict(set)
    users_by_event: dict[str, set[str]] = defaultdict(set)
    settlements_by_household = Counter()

    for event in events:
        name = event["event"]
        if event.get("household"):
            households_by_event[name].add(event["household"])
        if event.get("user"):
            users_by_event[name].add(event["user"])
        if name == "settlement_completed":
            settlements_by_household[event["household"]] += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Append-only anonymised production events; no names, emails, or raw Telegram IDs.",
        "headline": {
            "interested_households": len(households_by_event["access_page_viewed"]),
            "activated_households": len(households_by_event["household_activated"]),
            "joined_users": len(users_by_event["member_joined"]),
            "households_started_receipt_flow": len(households_by_event["receipt_started"]),
            "households_completed_settlement": len(households_by_event["settlement_completed"]),
            "completed_settlements": counts["settlement_completed"],
            "repeat_households": sum(n >= 2 for n in settlements_by_household.values()),
            "confirmed_payments": counts["payment_confirmed"],
        },
        "event_counts": dict(sorted(counts.items())),
        "settlements_per_anonymised_household": dict(sorted(settlements_by_household.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data/store")
    parser.add_argument("--output")
    args = parser.parse_args()
    rendered = json.dumps(build_report(args.root), indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
