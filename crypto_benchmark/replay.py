"""Replay timestamped crypto decisions against a self-financing spot ledger."""

from __future__ import annotations

import argparse
import csv
import json
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

TARGETS = {"Buy": 1.0, "Overweight": 0.75, "Underweight": 0.25, "Sell": 0.0}
RATINGS = {*TARGETS, "Hold", "REVIEW"}


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware datetimes")
    return value.astimezone(UTC)


def _number(value: float, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise ValueError(f"{label} must be a finite number")
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"{label} must be finite and {'positive' if positive else 'nonnegative'}")
    return float(value)


@dataclass(frozen=True)
class Price:
    timestamp: datetime
    ticker: str
    close: float


@dataclass(frozen=True)
class Decision:
    timestamp: datetime
    ticker: str
    rating: str


@dataclass
class _Position:
    cash: float
    units: float = 0.0

    def trade(self, target: float, price: Price, fee: float, slip: float) -> dict | None:
        mark = price.close
        equity = self.cash + self.units * mark
        difference = target * equity - self.units * mark
        if abs(difference) <= max(equity, 1.0) * 1e-12:
            return None
        buy = difference > 0
        execution = mark * (1 + slip if buy else 1 - slip)
        loss = execution * (1 + fee) - mark if buy else mark - execution * (1 - fee)
        quantity = abs(difference) / (mark + target * loss if buy else mark - target * loss)
        quantity = min(quantity, self.cash / (execution * (1 + fee)) if buy else self.units)
        notional = quantity * execution
        commission = notional * fee
        if buy:
            self.cash -= notional + commission
            self.units += quantity
        else:
            self.cash += notional - commission
            self.units -= quantity
        self.cash = max(0.0, self.cash)
        self.units = max(0.0, self.units)
        return {
            "timestamp": _utc(price.timestamp).isoformat(), "ticker": price.ticker,
            "side": "buy" if buy else "sell", "units": quantity,
            "mark_price": mark, "fill_price": execution, "notional": notional,
            "fee": commission, "slippage_cost": quantity * abs(execution - mark),
            "cash_after": self.cash, "units_after": self.units, "target_weight": target,
        }


def _run(grid: dict[str, dict[datetime, Price]], times: list[datetime],
         orders: dict[tuple[str, datetime], float], initial: float,
         fee: float, slip: float) -> dict:
    book = {ticker: _Position(initial / len(grid)) for ticker in grid}
    fills, curve = [], []
    peak, drawdown = initial, 0.0
    for timestamp in times:
        for ticker, position in book.items():
            target = orders.get((ticker, timestamp))
            if target is not None:
                fill = position.trade(target, grid[ticker][timestamp], fee, slip)
                if fill:
                    fills.append(fill)
        cash = sum(p.cash for p in book.values())
        invested = sum(p.units * grid[ticker][timestamp].close for ticker, p in book.items())
        equity = cash + invested
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("prices or costs produced nonfinite or nonpositive portfolio equity")
        peak = max(peak, equity)
        drawdown = max(drawdown, 1 - equity / peak)
        curve.append({"timestamp": timestamp.isoformat(), "equity": equity,
                      "cash": cash, "exposure": invested / equity})
    return {
        "initial_cash": initial, "final_equity": curve[-1]["equity"],
        "total_return": curve[-1]["equity"] / initial - 1,
        "max_drawdown": drawdown, "trades": len(fills),
        "fees": sum(fill["fee"] for fill in fills),
        "slippage_cost": sum(fill["slippage_cost"] for fill in fills),
        "turnover_over_initial_cash": sum(fill["notional"] for fill in fills) / initial,
        "equity": curve, "fills": fills,
        "ending_positions": {ticker: {"cash": p.cash, "units": p.units}
                             for ticker, p in book.items()},
    }


def simulate(prices: list[Price], decisions: list[Decision], initial_cash: float = 10000.0,
             fee_bps: float = 10.0, slippage_bps: float = 5.0) -> dict:
    """Evaluate ratings, buy-and-hold, and cash on the same aligned price grid.

    The universe receives equal independent cash sleeves. Targets describe
    post-cost exposure. Hold and REVIEW keep units unchanged. Buy-and-hold
    enters on the first supplied price. All strategies end marked to market.
    """
    initial = _number(initial_cash, "initial_cash", positive=True)
    fee = _number(fee_bps, "fee_bps") / 10000
    slip = _number(slippage_bps, "slippage_bps") / 10000
    if fee >= 1 or slip >= 1:
        raise ValueError("fee_bps and slippage_bps must be below 10000")
    if not prices:
        raise ValueError("at least one price is required")
    grid: dict[str, dict[datetime, Price]] = {}
    for price in prices:
        timestamp = _utc(price.timestamp)
        if not isinstance(price.ticker, str) or not price.ticker.strip():
            raise ValueError("price ticker must be a nonempty string")
        _number(price.close, "close", positive=True)
        ticker_grid = grid.setdefault(price.ticker, {})
        if timestamp in ticker_grid:
            raise ValueError(f"duplicate price for {price.ticker} at {timestamp}")
        ticker_grid[timestamp] = price
    times = sorted(next(iter(grid.values())))
    if any(sorted(ticker_grid) != times for ticker_grid in grid.values()):
        raise ValueError("all tickers must have identical price timestamps; missing bars are not filled")

    orders, seen, occupied, audit = {}, set(), set(), []
    for decision in sorted(decisions, key=lambda d: (_utc(d.timestamp), d.ticker)):
        timestamp = _utc(decision.timestamp)
        if decision.ticker not in grid:
            raise ValueError(f"no prices for decision ticker {decision.ticker!r}")
        if decision.rating not in RATINGS:
            raise ValueError(f"unknown rating {decision.rating!r}")
        key = (decision.ticker, timestamp)
        if key in seen:
            raise ValueError(f"duplicate decision for {decision.ticker} at {timestamp}")
        seen.add(key)
        index = bisect_right(times, timestamp)
        row = {"timestamp": timestamp.isoformat(), "ticker": decision.ticker,
               "rating": decision.rating, "execution_timestamp": None, "status": "unfilled"}
        if index < len(times):
            execution_time = times[index]
            slot = (decision.ticker, execution_time)
            if slot in occupied:
                raise ValueError("multiple decisions map to one price bar; use finer price data")
            occupied.add(slot)
            row["execution_timestamp"] = execution_time.isoformat()
            row["status"] = "review" if decision.rating == "REVIEW" else "processed"
            if decision.rating in TARGETS:
                orders[slot] = TARGETS[decision.rating]
        audit.append(row)

    return {
        "schema_version": 1, "kind": "offline_spot_replay",
        "settings": {"initial_cash": initial, "fee_bps": fee_bps,
                     "slippage_bps": slippage_bps, "target_weights": TARGETS,
                     "hold_policy": "keep_units", "review_policy": "keep_units_and_flag",
                     "fill_policy": "first_price_strictly_after_decision",
                     "baseline_entry": "first_supplied_price", "end_liquidation": False,
                     "allocation": "equal_independent_cash_sleeves"},
        "tickers": sorted(grid), "price_rows": len(prices),
        "decision_counts": {"total": len(audit),
                            "processed": sum(row["status"] == "processed" for row in audit),
                            "review": sum(row["rating"] == "REVIEW" for row in audit),
                            "unfilled": sum(row["status"] == "unfilled" for row in audit)},
        "decisions": audit,
        "agent": _run(grid, times, orders, initial, fee, slip),
        "buy_hold": _run(grid, times, {(ticker, times[0]): 1.0 for ticker in grid},
                         initial, fee, slip),
        "cash": _run(grid, times, {}, initial, fee, slip),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prices", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--initial-cash", type=float, default=10000)
    parser.add_argument("--fee-bps", type=float, default=10)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--synthetic", action="store_true", help="Label fixture data as synthetic")
    args = parser.parse_args(argv)
    try:
        with args.prices.open(newline="", encoding="utf-8") as handle:
            prices = [Price(datetime.fromisoformat(r["timestamp"]), r["ticker"], float(r["close"]))
                      for r in csv.DictReader(handle)]
        with args.decisions.open(newline="", encoding="utf-8") as handle:
            decisions = [Decision(datetime.fromisoformat(r["timestamp"]), r["ticker"], r["rating"])
                         for r in csv.DictReader(handle)]
        result = simulate(prices, decisions, args.initial_cash, args.fee_bps, args.slippage_bps)
        result["synthetic"] = args.synthetic
        result["inputs"] = {"prices": str(args.prices), "decisions": str(args.decisions)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
            handle.write("\n")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"Replay failed: {exc}\n")
    label = "Synthetic accounting check" if args.synthetic else "Offline replay"
    print(f"{label}: {args.output}")
    for name in ("agent", "buy_hold", "cash"):
        stats = result[name]
        print(f"{name}: return {stats['total_return']:.2%}, drawdown {stats['max_drawdown']:.2%}, "
              f"fills {stats['trades']}")
    print(f"Decisions: {result['decision_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
