"""Capture market-cap membership without treating symbols as instrument identities."""

import hashlib
import json
import math
import os
import ssl
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MARKETS_URL = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=market_cap_desc&per_page=100&page=1&sparkline=false"
)
_STABLE_SYMBOLS = frozenset({
    "usdt", "usdc", "dai", "usde", "usds", "fdusd", "tusd", "pyusd", "usd1",
    "frax", "lusd", "susde", "gusd", "usdp", "crvusd", "rlusd", "usdd",
})


def build_snapshot(raw: bytes, *, source: str, fetched_at: str | None = None) -> dict:
    rows = json.loads(raw)
    if not isinstance(rows, list) or len(rows) != 100:
        raise ValueError("expected exactly 100 market rows; refusing an incomplete top-100 snapshot")
    coins, ids, ranks = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each market row must be an object")
        strings = {key: row.get(key) for key in ("id", "symbol", "name")}
        if any(not isinstance(value, str) or not value.strip() for value in strings.values()):
            raise ValueError("every market row needs a nonempty id, symbol, and name")
        rank, cap = row.get("market_cap_rank"), row.get("market_cap")
        if type(rank) is not int or not 1 <= rank <= 100:
            raise ValueError("every market row needs an integer rank from 1 to 100")
        if type(cap) not in (int, float) or not math.isfinite(cap) or cap <= 0:
            raise ValueError("every market row needs a finite positive market cap")
        if strings["id"] in ids or rank in ranks:
            raise ValueError("duplicate coin ID or market-cap rank")
        ids.add(strings["id"])
        ranks.add(rank)
        coins.append({**strings, "rank": rank, "market_cap": cap})
    coins.sort(key=lambda coin: coin["rank"])
    counts = Counter(coin["symbol"].casefold() for coin in coins)
    for coin in coins:
        symbol, name = coin["symbol"].casefold(), coin["name"].casefold()
        flags = []
        if symbol in _STABLE_SYMBOLS or any(word in name for word in ("stablecoin", "usd coin", "tether")):
            flags.append("stablecoin_candidate")
        if any(word in name for word in ("wrapped", "bridged", "staked", "restaked")):
            flags.append("wrapped_or_staked_candidate")
        if counts[symbol] > 1:
            flags.append("duplicate_symbol")
        coin["review_flags"] = flags
    return {
        "schema_version": 1,
        "universe_kind": "current_market_cap" if fetched_at else "imported_membership_unverified",
        "source": source,
        "fetched_at": fetched_at,
        "imported_at": None if fetched_at else datetime.now(UTC).isoformat(),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_response_text": raw.decode("utf-8"),
        "count": len(coins),
        "coins": coins,
        "warnings": [
            "This snapshot does not establish historical top-100 membership.",
            "Review flags use incomplete name and symbol heuristics. They do not determine eligibility.",
            "No assets have been excluded. Review stablecoins and wrapped duplicates before evaluation.",
            "CoinGecko IDs must be mapped to verified vendor instruments. Symbols alone are ambiguous.",
        ],
    }


def _tls_context() -> ssl.SSLContext:
    if certificate_file := os.environ.get("SSL_CERT_FILE"):
        return ssl.create_default_context(cafile=certificate_file)
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def capture_universe(output: Path, input_path: Path | None = None) -> dict:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite snapshot: {output}")
    if input_path is not None:
        raw = input_path.read_bytes()
        source, fetched_at = str(input_path.resolve()), None
    else:
        headers = {"Accept": "application/json", "User-Agent": "TradingAgents-crypto-benchmark/1"}
        if key := os.environ.get("COINGECKO_DEMO_API_KEY"):
            headers["x-cg-demo-api-key"] = key
        try:
            with urlopen(Request(MARKETS_URL, headers=headers), timeout=30,
                         context=_tls_context()) as response:
                raw = response.read()
        except HTTPError as exc:
            raise ValueError(f"CoinGecko returned HTTP {exc.code}; no snapshot was written") from None
        except URLError as exc:
            reason = type(exc.reason).__name__
            hint = (" Check SSL_CERT_FILE or install certifi."
                    if isinstance(exc.reason, ssl.SSLCertVerificationError) else "")
            raise ValueError(
                f"could not reach CoinGecko ({reason}); no snapshot was written.{hint}"
            ) from None
        source, fetched_at = MARKETS_URL, datetime.now(UTC).isoformat()
    snapshot = build_snapshot(raw, source=source, fetched_at=fetched_at)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(snapshot, handle, indent=2, allow_nan=False)
        handle.write("\n")
    return snapshot
