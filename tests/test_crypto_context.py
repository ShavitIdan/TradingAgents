import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from crypto_benchmark.context import SOURCES, capture_context, context_as_of, main


def responses():
    return [
        json.dumps({"data": [{"value": "25", "value_classification": "Extreme Fear",
                              "timestamp": "1735689600"}]}).encode(),
        json.dumps({"data": {"market_cap_percentage": {"btc": 60},
                             "total_market_cap": {"usd": 3000000000000},
                             "total_volume": {"usd": 100000000000},
                             "updated_at": 1735689600}}).encode(),
        json.dumps({"coins": [{"item": {"id": "bitcoin", "name": "Bitcoin",
                                         "symbol": "BTC"}}]}).encode(),
    ]


class CryptoContextTests(unittest.TestCase):
    def test_capture_preserves_raw_sources_and_normalized_context(self):
        raw = responses()
        with tempfile.TemporaryDirectory() as directory, \
                patch("crypto_benchmark.context._fetch", side_effect=raw):
            output = Path(directory) / "context.json"
            result = capture_context(output)
            saved = json.loads(output.read_text())
            self.assertEqual(saved, result)
            self.assertEqual(saved["available_sources"], 3)
            self.assertEqual(saved["sources"]["fear_greed"]["data"]["value"], 25)
            self.assertEqual(saved["sources"]["global_market"]["data"]["btc_dominance_percent"], 60)
            self.assertEqual(saved["sources"]["trending"]["data"]["coins"][0]["id"], "bitcoin")
            for name, response in zip(SOURCES, raw, strict=True):
                self.assertEqual(saved["sources"][name]["raw_response_text"].encode(), response)
                self.assertEqual(saved["sources"][name]["raw_sha256"], hashlib.sha256(response).hexdigest())
            before = output.read_bytes()
            with self.assertRaises(FileExistsError):
                capture_context(output)
            self.assertEqual(output.read_bytes(), before)

    def test_failed_feed_stays_missing_without_neutral_value(self):
        raw = responses()
        with tempfile.TemporaryDirectory() as directory, \
                patch("crypto_benchmark.context._fetch", side_effect=[URLError("secret"), *raw[1:]]):
            result = capture_context(Path(directory) / "context.json")
            self.assertEqual(result["status"], "partial")
            self.assertEqual(result["available_sources"], 2)
            self.assertIsNone(result["sources"]["fear_greed"]["data"])
            self.assertEqual(result["sources"]["fear_greed"]["error"], "Transport error: str")

    def test_malformed_and_nonfinite_payloads_are_archived_as_unavailable(self):
        for invalid in (b'{"data": NaN}', b'{"data": 1e999}', b'{}', b'{broken'):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory, \
                    patch("crypto_benchmark.context._fetch", side_effect=[invalid, *responses()[1:]]):
                result = capture_context(Path(directory) / "context.json")
                failed = result["sources"]["fear_greed"]
                self.assertEqual(failed["status"], "unavailable")
                self.assertIsNone(failed["data"])
                self.assertEqual(failed["raw_response_text"].encode(), invalid)

    def test_as_of_rejects_later_archive_and_filters_later_source(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("crypto_benchmark.context._fetch", side_effect=responses()):
            snapshot = capture_context(Path(directory) / "context.json")
        captured = datetime.fromisoformat(snapshot["captured_at"])
        with self.assertRaisesRegex(ValueError, "after the decision cutoff"):
            context_as_of(snapshot, captured - timedelta(seconds=1))
        visible = context_as_of(snapshot, captured)
        self.assertEqual(visible["sources"]["fear_greed"]["data"]["value"], 25)
        for field in ("source_published_at", "fetched_at", "available_at"):
            later = copy.deepcopy(snapshot)
            later["sources"]["fear_greed"][field] = (captured + timedelta(seconds=1)).isoformat()
            hidden = context_as_of(later, captured)
            self.assertEqual(hidden["sources"]["fear_greed"],
                             {"status": "unavailable", "data": None, "error": "after_decision_cutoff"})
        with self.assertRaises(ValueError):
            context_as_of(snapshot, datetime(2025, 1, 1))

    def test_cli_all_failures_returns_nonzero_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch("crypto_benchmark.context._fetch", side_effect=TimeoutError), \
                redirect_stdout(io.StringIO()) as printed:
            output = Path(directory) / "failed.json"
            code = main(["--output", str(output)])
            self.assertEqual(code, 1)
            saved = json.loads(output.read_text())
            self.assertEqual(saved["unavailable_sources"], 3)
            self.assertIn("Available sources: 0", printed.getvalue())

    def test_future_source_publication_is_not_usable(self):
        future = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
        raw = responses()
        payload = json.loads(raw[0])
        payload["data"][0]["timestamp"] = str(future)
        raw[0] = json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as directory, \
                patch("crypto_benchmark.context._fetch", side_effect=raw):
            result = capture_context(Path(directory) / "future.json")
            self.assertEqual(result["available_sources"], 2)
            self.assertIsNone(result["sources"]["fear_greed"]["data"])


if __name__ == "__main__":
    unittest.main()
