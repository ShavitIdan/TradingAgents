"""Archive public crypto context without using current feeds as historical evidence.

Input ideas were evaluated in https://github.com/ardhanurfan/trading-bot-agent.
This collector is an independent implementation and does not invoke its code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .universe import _tls_context

SOURCES = {
    "fear_greed": ("https://api.alternative.me/fng/?limit=1&format=json", "Alternative.me"),
    "global_market": ("https://api.coingecko.com/api/v3/global", "CoinGecko"),
    "trending": ("https://api.coingecko.com/api/v3/search/trending", "CoinGecko"),
}


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("cutoff and archive timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _number(value, name: str, maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"invalid {name}")
    number = float(value)
    if not math.isfinite(number) or number < 0 or (maximum is not None and number > maximum):
        raise ValueError(f"invalid {name}")
    return number


def _text(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected nonempty source text")
    return value


def _published(value) -> str:
    seconds = _number(value, "source timestamp")
    if seconds == 0:
        raise ValueError("source timestamp must be positive")
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _normalize(name: str, payload: dict) -> tuple[dict, str | None]:
    if name == "fear_greed":
        row = payload["data"][0]
        value = _number(row["value"], "Fear & Greed value", 100)
        if not value.is_integer():
            raise ValueError("Fear & Greed value must be an integer")
        return {"value": int(value), "classification": _text(row["value_classification"]),
                "scope": "Bitcoin market sentiment", "attribution": "Alternative.me",
                "attribution_url": "https://alternative.me/crypto/fear-and-greed-index/"}, \
            _published(row["timestamp"])
    if name == "global_market":
        row = payload["data"]
        return {"btc_dominance_percent": _number(row["market_cap_percentage"]["btc"],
                                               "BTC dominance", 100),
                "market_cap_usd": _number(row["total_market_cap"]["usd"], "market cap"),
                "volume_24h_usd": _number(row["total_volume"]["usd"], "volume")}, \
            _published(row["updated_at"])
    rows = payload["coins"]
    if not isinstance(rows, list):
        raise ValueError("trending coins must be a list")
    coins = [{key: _text(row["item"][key]) for key in ("id", "symbol", "name")} for row in rows]
    if len({coin["id"] for coin in coins}) != len(coins):
        raise ValueError("duplicate trending coin ID")
    return {"coins": coins, "meaning": "CoinGecko search interest, not news articles"}, None


def _fetch(url: str) -> bytes:
    headers = {"Accept": "application/json", "User-Agent": "TradingAgents-crypto-benchmark/1"}
    if url.startswith("https://api.coingecko.com/") and (key := os.getenv("COINGECKO_DEMO_API_KEY")):
        headers["x-cg-demo-api-key"] = key
    with urlopen(Request(url, headers=headers), timeout=30, context=_tls_context()) as response:
        return response.read()


def capture_context(output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite context: {output}")
    sources = {}
    for name, (url, attribution) in SOURCES.items():
        record = {"url": url, "attribution": attribution, "status": "unavailable",
                  "data": None, "source_published_at": None, "raw_response_text": None,
                  "raw_sha256": None, "error": None}
        try:
            raw = _fetch(url)
            record["raw_sha256"] = hashlib.sha256(raw).hexdigest()
            record["raw_response_text"] = raw.decode("utf-8")
            payload = json.loads(raw)
            json.dumps(payload, allow_nan=False)
            data, published = _normalize(name, payload)
            if published and datetime.fromisoformat(published) > datetime.now(UTC):
                raise ValueError("source publication is in the future")
            record.update(status="ok", data=data, source_published_at=published)
        except HTTPError as exc:
            record["error"] = f"HTTP {exc.code}"
        except URLError as exc:
            record["error"] = f"Transport error: {type(exc.reason).__name__}"
        except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
            record["error"] = f"Invalid or unavailable source: {type(exc).__name__}"
        record["fetched_at"] = record["available_at"] = datetime.now(UTC).isoformat()
        sources[name] = record
    count = sum(record["status"] == "ok" for record in sources.values())
    snapshot = {
        "schema_version": 1, "kind": "forward_crypto_context",
        "captured_at": datetime.now(UTC).isoformat(), "sources": sources,
        "status": "complete" if count == len(SOURCES) else "partial" if count else "unavailable",
        "available_sources": count, "unavailable_sources": len(SOURCES) - count,
        "warnings": ["Availability begins at fetch completion, not the source's historical timestamp.",
                     "This archive is not injected into the historical agent graph.",
                     "Fear & Greed describes Bitcoin. Trending coins are search interest, not news."],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return snapshot


def context_as_of(snapshot: dict, cutoff: datetime) -> dict:
    cutoff = _aware(cutoff)
    if _aware(datetime.fromisoformat(snapshot["captured_at"])) > cutoff:
        raise ValueError("context snapshot was captured after the decision cutoff")
    sources = {}
    for name, record in snapshot["sources"].items():
        available = _aware(datetime.fromisoformat(record["available_at"]))
        fetched = _aware(datetime.fromisoformat(record["fetched_at"]))
        published = record["source_published_at"]
        eligible = max(available, fetched) <= cutoff and (
            published is None or _aware(datetime.fromisoformat(published)) <= cutoff
        )
        sources[name] = (record if eligible else
                         {"status": "unavailable", "data": None, "error": "after_decision_cutoff"})
    return {"as_of": cutoff.isoformat(), "sources": sources}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        snapshot = capture_context(args.output)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Context capture failed: {exc}\n")
    print(f"Saved {snapshot['status']} context to {args.output}. "
          f"Available sources: {snapshot['available_sources']}; "
          f"unavailable sources: {snapshot['unavailable_sources']}.")
    print("Fear & Greed attribution: Alternative.me. This index describes Bitcoin market sentiment.")
    return 0 if snapshot["available_sources"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
