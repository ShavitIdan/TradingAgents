import hashlib
import json
import ssl
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

from crypto_benchmark.plan import build_plan
from crypto_benchmark.universe import _tls_context, build_snapshot, capture_universe


def market_rows():
    return [{"id": f"coin-{rank}", "symbol": f"c{rank}", "name": f"Coin {rank}",
             "market_cap_rank": rank, "market_cap": 1000000 / rank}
            for rank in range(1, 101)]


class CryptoPlanningTests(unittest.TestCase):
    def test_plan_cli_counts_decisions_and_marks_intraday_unsupported(self):
        completed = subprocess.run(
            [sys.executable, "-m", "crypto_benchmark", "plan", "--json"],
            capture_output=True, text=True, check=True,
        )
        plan = json.loads(completed.stdout)
        self.assertEqual(
            [(row["decisions_per_day"], row["decisions_total"], row["graph_supported"])
             for row in plan["cadences"]],
            [(2400, 72000, False), (600, 18000, False), (100, 3000, True)],
        )
        self.assertEqual(plan["provider"], "chatgpt")

    def test_nondefault_plan_and_bad_counts(self):
        plan = build_plan(2, 7)
        self.assertEqual(plan["cadences"][0]["decisions_total"], 336)
        for assets, days in [(0, 30), (101, 30), (100, 0), (True, 30)]:
            with self.subTest(assets=assets, days=days), self.assertRaises(ValueError):
                build_plan(assets, days)

    def test_snapshot_keeps_ids_and_flags_without_removing_assets(self):
        rows = market_rows()
        rows[0].update(id="usd-coin", symbol="usdc", name="USD Coin")
        rows[1].update(id="wrapped-bitcoin", symbol="btc", name="Wrapped Bitcoin")
        rows[2].update(id="bitcoin", symbol="btc", name="Bitcoin")
        raw = json.dumps(list(reversed(rows))).encode()
        result = build_snapshot(raw, source="fixture")
        self.assertEqual(result["count"], 100)
        self.assertEqual(result["coins"][0]["id"], "usd-coin")
        self.assertEqual(result["coins"][0]["review_flags"], ["stablecoin_candidate"])
        self.assertEqual(result["coins"][1]["review_flags"],
                         ["wrapped_or_staked_candidate", "duplicate_symbol"])
        self.assertEqual(result["coins"][2]["review_flags"], ["duplicate_symbol"])
        self.assertEqual(result["raw_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["raw_response_text"].encode("utf-8"), raw)
        self.assertEqual(result["universe_kind"], "imported_membership_unverified")
        self.assertIsNone(result["fetched_at"])

    def test_bad_universe_never_claims_complete_membership(self):
        mutations = [lambda rows: rows.pop(),
                     lambda rows: rows[1].update(id="coin-1"),
                     lambda rows: rows[1].update(market_cap_rank=1),
                     lambda rows: rows[1].update(market_cap_rank=None),
                     lambda rows: rows[1].update(market_cap=float("nan")),
                     lambda rows: rows[1].update(symbol="")]
        for mutate in mutations:
            rows = market_rows()
            mutate(rows)
            with self.subTest(rows=rows[1]), self.assertRaises(ValueError):
                build_snapshot(json.dumps(rows).encode(), source="fixture")

    def test_offline_cli_writes_exclusive_auditable_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            raw, output = Path(folder) / "raw.json", Path(folder) / "snapshot.json"
            raw.write_text(json.dumps(market_rows()), encoding="utf-8")
            command = [sys.executable, "-m", "crypto_benchmark", "universe",
                       "--input", str(raw), "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertIn("Saved 100 assets", result.stdout)
            saved = output.read_bytes()
            self.assertEqual(json.loads(saved)["coins"][99]["id"], "coin-100")
            archived_raw = json.loads(saved)["raw_response_text"].encode("utf-8")
            self.assertEqual(archived_raw, raw.read_bytes())
            reconstructed = build_snapshot(archived_raw, source="archived response")
            self.assertEqual(reconstructed["coins"], json.loads(saved)["coins"])
            refused = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(refused.returncode, 2)
            self.assertIn("refusing to overwrite", refused.stderr)
            self.assertEqual(output.read_bytes(), saved)

    def test_invalid_import_does_not_write_file(self):
        with tempfile.TemporaryDirectory() as folder:
            raw, output = Path(folder) / "raw.json", Path(folder) / "snapshot.json"
            raw.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                capture_universe(output, raw)
            self.assertFalse(output.exists())

    def test_tls_uses_explicit_ca_then_certifi_then_system_trust(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        cases = [({"SSL_CERT_FILE": "/custom/ca.pem"}, SimpleNamespace(where=lambda: "/certifi/ca.pem"),
                  {"cafile": "/custom/ca.pem"}),
                 ({}, SimpleNamespace(where=lambda: "/certifi/ca.pem"), {"cafile": "/certifi/ca.pem"}),
                 ({}, None, {})]
        for environment, certifi_module, expected in cases:
            with self.subTest(environment=environment, expected=expected), \
                    patch.dict("os.environ", environment, clear=True), \
                    patch.dict(sys.modules, {"certifi": certifi_module}), \
                    patch("ssl.create_default_context", return_value=context) as create:
                result = _tls_context()
                self.assertEqual(result.verify_mode, ssl.CERT_REQUIRED)
                self.assertTrue(result.check_hostname)
                self.assertEqual(create.call_args.kwargs, expected)

    def test_download_preserves_verified_context_and_original_response(self):
        raw = json.dumps(market_rows(), indent=1).encode() + b"\n"
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with tempfile.TemporaryDirectory() as folder, \
                patch("crypto_benchmark.universe._tls_context", return_value=context), \
                patch("crypto_benchmark.universe.urlopen") as request:
            request.return_value.__enter__.return_value.read.return_value = raw
            output = Path(folder) / "snapshot.json"
            result = capture_universe(output)
            self.assertEqual(result["universe_kind"], "current_market_cap")
            self.assertIsNotNone(result["fetched_at"])
            self.assertEqual(result["raw_response_text"].encode("utf-8"), raw)
            supplied_context = request.call_args.kwargs["context"]
            self.assertEqual(supplied_context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(supplied_context.check_hostname)
            self.assertEqual(json.loads(output.read_text())["coins"][0]["id"], "coin-1")

    def test_certificate_error_keeps_diagnostic_and_writes_nothing(self):
        error = URLError(ssl.SSLCertVerificationError("untrusted certificate"))
        with tempfile.TemporaryDirectory() as folder, \
                patch("crypto_benchmark.universe.urlopen", side_effect=error):
            output = Path(folder) / "snapshot.json"
            with self.assertRaisesRegex(ValueError, "SSLCertVerificationError.*SSL_CERT_FILE"):
                capture_universe(output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
