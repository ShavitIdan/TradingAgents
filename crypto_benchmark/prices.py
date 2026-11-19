"""Download complete daily crypto prices with explicit UTC candle-close timestamps."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path


def request_dates(tickers: str, start: str, end: str) -> tuple[list[str], list[date]]:
    names = [name.strip() for name in tickers.split(",")]
    if any(not re.fullmatch(r"[A-Z0-9]{1,20}-USD", name) for name in names):
        raise ValueError("use explicit crypto vendor tickers such as BTC-USD,ETH-USD")
    if len(set(names)) != len(names):
        raise ValueError("duplicate tickers are not allowed")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first.isoformat() != start or last.isoformat() != end:
        raise ValueError("dates must use YYYY-MM-DD")
    if last < first or last >= datetime.now(UTC).date():
        raise ValueError("require start <= end and only completed UTC dates before today")
    return names, [first + timedelta(days=n) for n in range((last - first).days + 1)]


def close_rows(ticker: str, raw: list[dict], days: list[date]) -> list[dict]:
    by_day = {}
    for row in raw:
        timestamp = datetime.fromisoformat(row["source_timestamp"])
        if timestamp.utcoffset() != timedelta(0) or timestamp.time() != datetime.min.time():
            raise ValueError(f"{ticker}: expected daily candle opens at UTC midnight")
        day = timestamp.date()
        if day in by_day:
            raise ValueError(f"{ticker}: duplicate daily candle for {day}")
        for key in ("Open", "High", "Low", "Close", "Volume"):
            value = row.get(key)
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or value < 0 or (key != "Volume" and value == 0)):
                raise ValueError(f"{ticker}: invalid {key} for {day}")
        by_day[day] = row
    if set(by_day) != set(days):
        raise ValueError(f"{ticker}: expected every requested day exactly once; no gaps or extra candles")
    return [
        {"timestamp": datetime.combine(day + timedelta(days=1), datetime.min.time(), UTC).isoformat(),
         "ticker": ticker, "close": by_day[day]["Close"]}
        for day in days
    ]


def _yahoo():
    import yfinance

    return yfinance


def capture_prices(tickers: str, start: str, end: str, output: Path) -> dict:
    names, days = request_dates(tickers, start, end)
    output.mkdir(parents=True, exist_ok=False)
    request = {"start": start, "end": (days[-1] + timedelta(days=1)).isoformat(),
               "interval": "1d", "auto_adjust": False, "actions": False, "timeout": 30}
    manifest = {
        "schema_version": 1, "status": "running", "source": "Yahoo Finance via yfinance",
        "tickers": names, "requested_days_inclusive": [start, end], "request": request,
        "timestamp_policy": "Yahoo UTC daily open date plus one day is the candle close",
        "warning": "Aggregated Yahoo prices approximate exchange execution. No fill guarantee.",
        "raw_format": "yfinance OHLCV rows, not the original HTTP response; nonfinite values are null",
        "assets": {},
    }
    provenance = output / "provenance.json"

    def persist():
        provenance.write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")

    persist()
    try:
        yf = _yahoo()
        manifest["yfinance_version"] = yf.__version__
        yf.set_tz_cache_location(str(output / "cache"))
        rows = []
        for ticker in names:
            frame = yf.Ticker(ticker).history(**request)
            raw = []
            for index, values in frame.iterrows():
                row = {"source_timestamp": index.isoformat()}
                for key in ("Open", "High", "Low", "Close", "Volume"):
                    value = float(values[key])
                    row[key] = value if math.isfinite(value) else None
                raw.append(row)
            manifest["assets"][ticker] = {"fetched_at": datetime.now(UTC).isoformat(), "raw": raw}
            persist()
            rows.extend(close_rows(ticker, raw, days))
        csv_text = io.StringIO(newline="")
        writer = csv.DictWriter(csv_text, fieldnames=["timestamp", "ticker", "close"])
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: (row["timestamp"], row["ticker"])))
        content = csv_text.getvalue().encode("utf-8")
        manifest.update(status="completed", price_rows=len(rows),
                        csv_sha256=hashlib.sha256(content).hexdigest())
        with (output / "prices.csv").open("xb") as handle:
            handle.write(content)
        persist()
    except Exception as exc:
        manifest.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        persist()
        raise RuntimeError(f"price capture failed; see {provenance}") from exc
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = capture_prices(args.tickers, args.start, args.end, args.output)
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Prices failed: {exc}\n")
    print(f"Saved {result['price_rows']} daily closes to {args.output / 'prices.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
