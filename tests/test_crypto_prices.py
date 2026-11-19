import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from crypto_benchmark.prices import capture_prices, close_rows, request_dates


def candle(day="2025-01-01", **overrides):
    return {"source_timestamp": f"{day}T00:00:00+00:00", "Open": 100.0, "High": 110.0,
            "Low": 90.0, "Close": 105.0, "Volume": 1000.0, **overrides}


class PriceCaptureTests(unittest.TestCase):
    def test_closes_use_following_midnight(self):
        self.assertEqual(close_rows("BTC-USD", [candle()], [date(2025, 1, 1)]), [
            {"timestamp": "2025-01-02T00:00:00+00:00", "ticker": "BTC-USD", "close": 105.0}
        ])

    def test_gaps_duplicates_wrong_timezones_and_bad_prices_fail(self):
        cases = [[], [candle(), candle()], [candle("2025-01-02")],
                 [candle(Close=float("nan"))], [candle(High=0)], [candle(Volume=-1)],
                 [candle(Close=None)], [candle(source_timestamp="2025-01-01T00:00:00")],
                 [candle(source_timestamp="2025-01-01T00:00:00+02:00")]]
        for raw in cases:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                close_rows("BTC-USD", raw, [date(2025, 1, 1)])

    def test_invalid_requests_fail_before_output(self):
        cases = [("BTC-USD,BTC-USD", "2025-01-01", "2025-01-01"),
                 ("BTCUSDT", "2025-01-01", "2025-01-01"),
                 ("BTC-USD", "20250101", "2025-01-01"),
                 ("BTC-USD", "2025-01-02", "2025-01-01"),
                 ("BTC-USD", "2025-01-01", datetime.now(UTC).date().isoformat())]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(ValueError):
                request_dates(*args)

    def fake_yahoo(self, raw):
        calls = []

        class Ticker:
            def __init__(self, ticker):
                self.ticker = ticker

            def history(self, **kwargs):
                calls.append({"ticker": self.ticker, **kwargs})
                return SimpleNamespace(iterrows=lambda: [
                    (datetime.fromisoformat(row["source_timestamp"]), row) for row in raw
                ])

        return SimpleNamespace(__version__="fixture", Ticker=Ticker,
                               set_tz_cache_location=lambda path: None), calls

    def test_saved_csv_and_provenance_match_request(self):
        yahoo, calls = self.fake_yahoo([candle()])
        with tempfile.TemporaryDirectory() as directory, patch(
            "crypto_benchmark.prices._yahoo", return_value=yahoo
        ):
            output = Path(directory) / "data"
            result = capture_prices("BTC-USD,ETH-USD", "2025-01-01", "2025-01-01", output)
            self.assertEqual((result["status"], result["price_rows"]), ("completed", 2))
            with (output / "prices.csv").open(newline="") as handle:
                self.assertEqual(list(csv.DictReader(handle)), [
                    {"timestamp": "2025-01-02T00:00:00+00:00", "ticker": "BTC-USD", "close": "105.0"},
                    {"timestamp": "2025-01-02T00:00:00+00:00", "ticker": "ETH-USD", "close": "105.0"},
                ])
            saved = json.loads((output / "provenance.json").read_text())
            self.assertEqual(saved["assets"]["BTC-USD"]["raw"][0]["Close"], 105.0)
            self.assertEqual(saved["csv_sha256"], hashlib.sha256((output / "prices.csv").read_bytes()).hexdigest())
            self.assertEqual(calls[0], {"ticker": "BTC-USD", "start": "2025-01-01",
                                       "end": "2025-01-02", "interval": "1d",
                                       "auto_adjust": False, "actions": False, "timeout": 30})
            with self.assertRaises(FileExistsError):
                capture_prices("BTC-USD", "2025-01-01", "2025-01-01", output)
            self.assertEqual(json.loads((output / "provenance.json").read_text())["price_rows"], 2)

    def test_failed_capture_keeps_evidence_without_csv(self):
        yahoo, _ = self.fake_yahoo([candle(Close=float("nan"))])
        with tempfile.TemporaryDirectory() as directory, patch(
            "crypto_benchmark.prices._yahoo", return_value=yahoo
        ):
            output = Path(directory) / "data"
            with self.assertRaises(RuntimeError):
                capture_prices("BTC-USD", "2025-01-01", "2025-01-01", output)
            saved = json.loads((output / "provenance.json").read_text())
            self.assertEqual(saved["status"], "failed")
            self.assertIn("invalid Close", saved["error"])
            self.assertIsNone(saved["assets"]["BTC-USD"]["raw"][0]["Close"])
            self.assertFalse((output / "prices.csv").exists())

    def test_cli_rejects_incomplete_day_without_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "data"
            result = subprocess.run(
                [sys.executable, "-m", "crypto_benchmark.prices", "--tickers", "BTC-USD",
                 "--start", "2025-01-01", "--end", datetime.now(UTC).date().isoformat(),
                 "--output", str(output)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("only completed UTC dates before today", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
