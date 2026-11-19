"""Run a bounded daily historical pilot through the ChatGPT-only agent graph."""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .replay import RATINGS

UPSTREAM_REVISION = "9fa1196fffbcfd9de5146d2dd2869df84ab99838"
_RECORDED_SETTINGS = (
    "llm_provider", "quick_think_provider", "deep_think_provider", "backend_url",
    "quick_think_backend_url", "deep_think_backend_url", "quick_think_llm", "deep_think_llm",
    "temperature", "max_tokens", "llm_max_retries", "max_tool_rounds", "max_debate_rounds",
    "max_risk_discuss_rounds", "max_recur_limit", "checkpoint_enabled", "results_dir",
    "data_cache_dir", "memory_log_path", "memory_log_max_entries", "benchmark_ticker",
    "benchmark_map", "holding_period_days", "data_vendors", "tool_vendors",
    "google_thinking_level", "openai_reasoning_effort", "anthropic_effort", "output_language",
    "news_article_limit", "global_news_article_limit", "global_news_lookback_days",
    "global_news_queries",
)


def _provenance() -> dict:
    result = {"upstream_revision": UPSTREAM_REVISION, "package_version": None,
              "checkout_revision": None, "checkout_dirty": None}
    with suppress(PackageNotFoundError):
        result["package_version"] = version("tradingagents")
    try:
        directory = Path(__file__).resolve().parent.parent
        result["checkout_revision"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=directory, check=True, capture_output=True,
            text=True, timeout=5,
        ).stdout.strip()
        result["checkout_dirty"] = bool(subprocess.run(
            ["git", "status", "--porcelain"], cwd=directory, check=True, capture_output=True,
            text=True, timeout=5,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def build_run_plan(tickers: str, start: str, end: str, max_decisions: int,
                   quick_model: str, deep_model: str, output: Path,
                   analysts: str = "market") -> dict:
    names = [value.strip() for value in tickers.split(",")]
    if not names or any(not re.fullmatch(r"[A-Z0-9]{1,20}-USD", name) for name in names):
        raise ValueError("tickers must be explicit vendor symbols such as BTC-USD,ETH-USD")
    if len(set(names)) != len(names):
        raise ValueError("duplicate tickers are not allowed")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first.isoformat() != start or last.isoformat() != end:
        raise ValueError("dates must use YYYY-MM-DD")
    if last < first:
        raise ValueError("end must not precede start")
    if last >= datetime.now(UTC).date():
        raise ValueError("historical runs require completed UTC dates before today")
    if type(max_decisions) is not int or max_decisions < 1:
        raise ValueError("max-decisions must be a positive integer")
    count = ((last - first).days + 1) * len(names)
    if count > max_decisions:
        raise ValueError(f"{count} decisions exceed max-decisions={max_decisions}")
    selected = analysts.split(",")
    if not selected or len(set(selected)) != len(selected) or any(
        value not in {"market", "news", "social"} for value in selected
    ):
        raise ValueError("analysts must be unique choices from market,news,social")
    if not quick_model.strip() or not deep_model.strip():
        raise ValueError("both model IDs are required")
    return {
        "kind": "historical_daily_pilot", "provider": "chatgpt",
        "paid_api_fallback": False, "tickers": names,
        "dates": [(first + timedelta(days=n)).isoformat()
                  for n in range((last - first).days + 1)],
        "decision_count": count, "max_decisions": max_decisions,
        "quick_model": quick_model, "deep_model": deep_model,
        "analysts": selected, "output": str(output.resolve()),
        "warnings": [
            "Daily historical analysis only. Intraday and forward paper trading are not implemented.",
            "Explicit tickers are assumed to be identity-reviewed vendor instruments.",
            "Historical availability uses the following UTC midnight, not actual generation time.",
            "Replay fills strictly after that midnight, excluding the analysis day's closing price.",
            "Model knowledge and unarchived text can contaminate historical results.",
            "Each cell has isolated memory. This evaluates independent decisions, without learning.",
            "Decision count is not a model-call, token, or ChatGPT plan usage cap.",
            "BTC self-alpha is meaningless. Use offline replay, not upstream alpha, to score this run.",
        ],
    }


def _runtime():
    from cli.stats_handler import StatsCallbackHandler
    from tradingagents.default_config import build_default_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.llm_clients import chatgpt_auth

    return TradingAgentsGraph, StatsCallbackHandler, build_default_config(), chatgpt_auth.access_token


def _cell_config(defaults: dict, plan: dict, directory: Path) -> dict:
    return {
        **defaults,
        "llm_provider": "chatgpt", "quick_think_provider": "chatgpt",
        "deep_think_provider": "chatgpt",
        "backend_url": "https://api.openai.com/v1",
        "quick_think_backend_url": "https://api.openai.com/v1",
        "deep_think_backend_url": "https://api.openai.com/v1",
        "quick_think_llm": plan["quick_model"], "deep_think_llm": plan["deep_model"],
        "temperature": None, "max_tokens": None, "llm_max_retries": 0,
        "max_tool_rounds": 3, "max_debate_rounds": 1, "max_risk_discuss_rounds": 1,
        "max_recur_limit": 100, "checkpoint_enabled": False,
        "results_dir": str(directory / "reports"),
        "data_cache_dir": str(directory / "cache"),
        "memory_log_path": str(directory / "memory.md"),
        "memory_log_max_entries": None, "benchmark_ticker": "BTC-USD",
        "data_vendors": {
            "core_stock_apis": "yfinance", "technical_indicators": "yfinance",
            "fundamental_data": "yfinance", "news_data": "yfinance",
            "macro_data": "fred", "prediction_markets": "polymarket",
        },
        "tool_vendors": {},
    }


def execute(plan: dict) -> dict:
    output = Path(plan["output"])
    if output.exists():
        raise FileExistsError(f"refusing to reuse output directory: {output}")
    graph_type, stats_type, defaults, authenticate = _runtime()
    authenticate()
    output.mkdir(parents=True, exist_ok=False)
    status = {**plan, "status": "running", "completed": 0, "failures": 0,
              "provenance": _provenance()}
    manifest = output / "run.json"

    def persist():
        manifest.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")

    persist()
    with (output / "decisions.jsonl").open("x", encoding="utf-8") as log, \
         (output / "decisions.csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "ticker", "rating"])
        writer.writeheader()
        handle.flush()
        for analysis_date in plan["dates"]:
            for ticker in plan["tickers"]:
                cell = output / "cells" / analysis_date / ticker
                config = _cell_config(defaults, plan, cell)
                stats = stats_type()
                began = time.monotonic()
                row = {
                    "ticker": ticker, "analysis_date": analysis_date,
                    "available_at": (date.fromisoformat(analysis_date) + timedelta(days=1)).isoformat()
                                    + "T00:00:00+00:00",
                    "availability_basis": "assumed_historical_next_utc_midnight",
                    "rating": None, "status": "failed",
                    "settings": {key: config[key] for key in _RECORDED_SETTINGS if key in config},
                }
                error = None
                try:
                    graph = graph_type(plan["analysts"], config=config, callbacks=[stats])
                    _, rating = graph.propagate(ticker, analysis_date, asset_type="crypto")
                    if rating not in RATINGS:
                        raise ValueError(f"graph returned an unknown rating: {rating!r}")
                    row.update(rating=rating, status="completed")
                    writer.writerow({"timestamp": row["available_at"], "ticker": ticker,
                                     "rating": rating})
                    handle.flush()
                    status["completed"] += 1
                except Exception as exc:
                    error = exc
                    row["error"] = f"{type(exc).__name__}: {exc}"
                    status["failures"] += 1
                    status["status"] = "failed"
                row["finished_at"] = datetime.now(UTC).isoformat()
                row["elapsed_seconds"] = time.monotonic() - began
                row["usage"] = stats.get_stats()
                row["token_usage_basis"] = "reported_metadata_only_may_be_incomplete"
                if not row["usage"].get("tokens_in") and not row["usage"].get("tokens_out"):
                    row["usage"]["tokens_in"] = row["usage"]["tokens_out"] = None
                log.write(json.dumps(row, allow_nan=False) + "\n")
                log.flush()
                persist()
                print(f"{analysis_date} {ticker}: {row['status']} {row['rating'] or ''}",
                      file=sys.stderr, flush=True)
                if error is not None:
                    raise RuntimeError(f"pilot stopped after {ticker} failed; see {manifest}") from error
    status["status"] = "completed"
    persist()
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--max-decisions", type=int, default=2)
    parser.add_argument("--quick-model", required=True)
    parser.add_argument("--deep-model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--analysts", default="market")
    parser.add_argument("--execute", action="store_true", help="Use your signed-in ChatGPT plan")
    args = parser.parse_args(argv)
    try:
        plan = build_run_plan(args.tickers, args.start, args.end, args.max_decisions,
                              args.quick_model, args.deep_model, args.output, args.analysts)
        result = execute(plan) if args.execute else {**plan, "status": "dry_run"}
        print(json.dumps(result, indent=2))
    except (ValueError, OSError, ImportError, RuntimeError) as exc:
        parser.exit(2, f"Pilot failed: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
