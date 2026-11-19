"""Offline planning and explicit top-100 universe capture."""

import argparse
import json
from pathlib import Path

from .plan import build_plan, render_plan
from .universe import capture_universe


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="estimate analysis volume without model calls")
    plan.add_argument("--assets", type=int, default=100)
    plan.add_argument("--days", type=int, default=30)
    plan.add_argument("--json", action="store_true")
    universe = commands.add_parser("universe", help="capture a dated market-cap top 100")
    universe.add_argument("--output", type=Path, required=True)
    universe.add_argument("--input", type=Path, help="import raw CoinGecko markets JSON offline")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            result = build_plan(args.assets, args.days)
            print(json.dumps(result, indent=2) if args.json else render_plan(result))
        else:
            result = capture_universe(args.output, args.input)
            flagged = sum(bool(coin["review_flags"]) for coin in result["coins"])
            print(f"Saved {result['count']} assets to {args.output}. {flagged} have review flags.")
            print("No assets excluded. Vendor identities and eligibility still require review.")
            print("This snapshot does not establish historical top-100 membership.")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"error: {exc}\n")


if __name__ == "__main__":
    main()
