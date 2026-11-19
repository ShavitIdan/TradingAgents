import csv
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from crypto_benchmark.replay import Decision, Price, simulate
from crypto_benchmark.run import build_run_plan, execute


class RunnerTests(unittest.TestCase):
    def plan(self, output, **kwargs):
        values = {"tickers": "BTC-USD,ETH-USD", "start": "2025-01-01", "end": "2025-01-01",
                      "max_decisions": 2, "quick_model": "quick-test", "deep_model": "deep-test",
                      "output": output}
        return build_run_plan(**(values | kwargs))

    def test_real_cli_dry_run_needs_no_upstream_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "untouched"
            result = subprocess.run(
                [sys.executable, "-m", "crypto_benchmark.run", "--tickers", "BTC-USD,ETH-USD",
                 "--start", "2025-01-01", "--end", "2025-01-01", "--quick-model", "q",
                 "--deep-model", "d", "--output", str(output)], capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["decision_count"], 2)
            self.assertEqual(json.loads(result.stdout)["status"], "dry_run")
            self.assertFalse(output.exists())

    def test_invalid_plans_cannot_start(self):
        cases = [{"max_decisions": 1}, {"max_decisions": 0}, {"tickers": "BTC-USD,BTC-USD"},
                 {"tickers": "../BTC-USD"}, {"start": "20250101"},
                 {"end": "2024-12-31"}, {"analysts": "fundamentals"},
                 {"end": datetime.now(UTC).date().isoformat()}]
        for overrides in cases:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.plan(Path("unused"), **overrides)

    def test_execute_isolates_cells_and_records_success(self):
        configs, calls = [], []

        class Stats:
            def get_stats(self):
                return {"llm_calls": 4, "tool_calls": 2, "tokens_in": 0, "tokens_out": 0}

        class Graph:
            def __init__(self, analysts, config, callbacks):
                configs.append(config)

            def propagate(self, ticker, day, asset_type):
                calls.append((ticker, day, asset_type))
                return {}, "Buy" if ticker == "BTC-USD" else "Hold"

        dirty = {"llm_provider": "openai", "quick_think_provider": "anthropic",
                     "deep_think_provider": "openai", "quick_think_backend_url": "https://paid.example",
                     "deep_think_backend_url": "https://paid.example", "max_tokens": 100,
                     "tool_vendors": {"get_stock_data": "alpha_vantage"},
                     "api_key": "must-never-be-recorded"}
        with tempfile.TemporaryDirectory() as directory, patch(
            "crypto_benchmark.run._runtime", return_value=(Graph, Stats, dirty, lambda: "secret")
        ):
            output = Path(directory) / "run"
            result = execute(self.plan(output))
            self.assertEqual((result["status"], result["completed"]), ("completed", 2))
            self.assertEqual(calls, [("BTC-USD", "2025-01-01", "crypto"),
                                     ("ETH-USD", "2025-01-01", "crypto")])
            for config in configs:
                for key in ("llm_provider", "quick_think_provider", "deep_think_provider"):
                    self.assertEqual(config[key], "chatgpt")
                for key in ("backend_url", "quick_think_backend_url", "deep_think_backend_url"):
                    self.assertEqual(config[key], "https://api.openai.com/v1")
            self.assertNotEqual(configs[0]["memory_log_path"], configs[1]["memory_log_path"])
            self.assertTrue(configs[0]["memory_log_path"].startswith(str(output.resolve())))
            with (output / "decisions.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0], {"timestamp": "2025-01-02T00:00:00+00:00",
                                       "ticker": "BTC-USD", "rating": "Buy"})
            decision = Decision(datetime.fromisoformat(rows[0]["timestamp"]),
                                rows[0]["ticker"], rows[0]["rating"])
            replay = simulate([
                Price(datetime(2025, 1, 2, tzinfo=UTC), "BTC-USD", 100),
                Price(datetime(2025, 1, 3, tzinfo=UTC), "BTC-USD", 200),
            ], [decision], initial_cash=1000, fee_bps=0, slippage_bps=0)
            self.assertEqual(replay["agent"]["final_equity"], 1000)
            self.assertEqual(replay["buy_hold"]["final_equity"], 2000)
            self.assertEqual(replay["agent"]["fills"][0]["timestamp"], "2025-01-03T00:00:00+00:00")
            logs = [json.loads(line) for line in (output / "decisions.jsonl").read_text().splitlines()]
            self.assertEqual(logs[1]["rating"], "Hold")
            self.assertEqual(logs[0]["usage"]["llm_calls"], 4)
            self.assertIsNone(logs[0]["usage"]["tokens_in"])
            self.assertEqual(logs[0]["settings"]["deep_think_llm"], "deep-test")
            self.assertEqual(logs[0]["settings"]["max_tool_rounds"], 3)
            self.assertEqual(logs[0]["settings"]["max_tokens"], None)
            self.assertNotIn("must-never-be-recorded", (output / "decisions.jsonl").read_text())
            self.assertEqual(result["provenance"]["upstream_revision"],
                             "9fa1196fffbcfd9de5146d2dd2869df84ab99838")
            with self.assertRaises(FileExistsError):
                execute(self.plan(output))

    def test_auth_failure_creates_no_output_or_graph(self):
        graph = Mock(side_effect=AssertionError("graph must not start"))
        authenticate = Mock(side_effect=RuntimeError("sign in first"))
        with tempfile.TemporaryDirectory() as directory, patch(
            "crypto_benchmark.run._runtime", return_value=(graph, Mock(), {}, authenticate)
        ):
            output = Path(directory) / "run"
            with self.assertRaisesRegex(RuntimeError, "sign in first"):
                execute(self.plan(output))
            self.assertFalse(output.exists())
            graph.assert_not_called()

    def test_existing_output_is_preserved_before_auth(self):
        with tempfile.TemporaryDirectory() as directory, patch(
            "crypto_benchmark.run._runtime", side_effect=AssertionError("must not load runtime")
        ):
            output = Path(directory)
            sentinel = output / "run.json"
            sentinel.write_text("existing experiment")
            with self.assertRaisesRegex(FileExistsError, "refusing to reuse"):
                execute(self.plan(output))
            self.assertEqual(sentinel.read_text(), "existing experiment")

    def test_failure_stops_and_preserves_audit(self):
        class Stats:
            def get_stats(self):
                return {"llm_calls": 1, "tool_calls": 0, "tokens_in": 9, "tokens_out": 3}

        class Graph:
            def __init__(self, *args, **kwargs):
                pass

            def propagate(self, *args, **kwargs):
                raise RuntimeError("plan limit reached")

        with tempfile.TemporaryDirectory() as directory, patch(
            "crypto_benchmark.run._runtime", return_value=(Graph, Stats, {}, lambda: "secret")
        ):
            output = Path(directory) / "run"
            with self.assertRaisesRegex(RuntimeError, "pilot stopped"):
                execute(self.plan(output))
            manifest = json.loads((output / "run.json").read_text())
            self.assertEqual((manifest["completed"], manifest["failures"]), (0, 1))
            logs = (output / "decisions.jsonl").read_text().splitlines()
            self.assertEqual(len(logs), 1)
            self.assertEqual(json.loads(logs[0])["error"], "RuntimeError: plan limit reached")
            self.assertEqual((output / "decisions.csv").read_text().strip(), "timestamp,ticker,rating")


if __name__ == "__main__":
    unittest.main()
