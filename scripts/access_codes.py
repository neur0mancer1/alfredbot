"""Issue one-use Alfred activation codes for paid or promotional access."""

from __future__ import annotations

import argparse

from alfred.access import AccessStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("issue", "grandfather"))
    parser.add_argument("--kind", choices=("paid", "promo"), default="promo")
    parser.add_argument("--note", default="")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--root", default=None)
    parser.add_argument("--output")
    args = parser.parse_args()
    store = AccessStore(args.root) if args.root else AccessStore()
    if args.action == "grandfather":
        print(f"Grandfathered {store.grandfather_existing_households()} household(s).")
        return
    codes = [store.issue(kind=args.kind, note=args.note) for _ in range(args.count)]
    rendered = "\n".join(codes) + "\n"
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(rendered)
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
