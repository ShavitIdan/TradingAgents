import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from crypto_benchmark.replay import Decision, Price, simulate

START = datetime(2026, 1, 1, tzinfo=UTC)


def prices(values, ticker="BTC-USD"):
    return [Price(START + timedelta(days=i), ticker, value) for i, value in enumerate(values)]


def decision(day, rating, ticker="BTC-USD"):
    return Decision(START + timedelta(days=day), ticker, rating)


class CryptoReplayTest(unittest.TestCase):
    def test_decision_cannot_fill_at_its_own_price(self):
        report = simulate(prices([100, 200, 300]), [decision(0, "Buy")],
                          initial_cash=1000, fee_bps=0, slippage_bps=0)
        self.assertEqual(report["agent"]["final_equity"], 1500)
        self.assertEqual(report["agent"]["fills"][0]["fill_price"], 200)
        self.assertEqual(report["buy_hold"]["final_equity"], 3000)
        self.assertEqual(report["cash"]["final_equity"], 1000)

    def test_buy_and_hold_parity_and_entry_costs(self):
        report = simulate(prices([100, 100]), [decision(-1, "Buy")],
                          initial_cash=1020.1, fee_bps=100, slippage_bps=100)
        self.assertEqual(report["agent"], report["buy_hold"])
        self.assertAlmostEqual(report["agent"]["final_equity"], 1000)
        self.assertAlmostEqual(report["agent"]["fees"], 10.1)
        self.assertAlmostEqual(report["agent"]["slippage_cost"], 10)
        self.assertAlmostEqual(report["agent"]["ending_positions"]["BTC-USD"]["cash"], 0)

    def test_sell_realizes_cash_with_costs_on_both_legs(self):
        report = simulate(prices([100, 100]), [decision(-1, "Buy"), decision(0, "Sell")],
                          initial_cash=1020.1, fee_bps=100, slippage_bps=100)
        self.assertAlmostEqual(report["agent"]["final_equity"], 980.1)
        self.assertAlmostEqual(report["agent"]["fees"], 20)
        self.assertAlmostEqual(report["agent"]["slippage_cost"], 20)
        self.assertEqual(report["agent"]["ending_positions"]["BTC-USD"]["units"], 0)
        self.assertEqual(report["agent"]["trades"], 2)

    def test_hold_and_review_preserve_units_while_sell_exits(self):
        report = simulate(prices([100, 200, 300, 200, 500]),
                          [decision(-1, "Buy"), decision(0, "Hold"),
                           decision(1, "REVIEW"), decision(2, "Sell")],
                          initial_cash=1000, fee_bps=0, slippage_bps=0)
        self.assertEqual(report["agent"]["final_equity"], 2000)
        self.assertEqual(report["agent"]["trades"], 2)
        self.assertEqual(report["decision_counts"],
                         {"total": 4, "processed": 3, "review": 1, "unfilled": 0})
        self.assertAlmostEqual(report["agent"]["max_drawdown"], 1 / 3)

    def test_post_cost_target_weight(self):
        report = simulate(prices([100, 200]),
                          [decision(-1, "Overweight"), decision(0, "Underweight")],
                          initial_cash=1000, fee_bps=100, slippage_bps=100)
        self.assertAlmostEqual(report["agent"]["equity"][0]["exposure"], 0.75)
        self.assertAlmostEqual(report["agent"]["equity"][1]["exposure"], 0.25)
        self.assertAlmostEqual(report["agent"]["fills"][0]["units"], 7.388616604684382)

    def test_independent_sleeves_do_not_reuse_capital(self):
        report = simulate(prices([100, 200]) + prices([100, 50], "ETH-USD"),
                          [decision(-1, "Buy"), decision(-1, "Buy", "ETH-USD")],
                          initial_cash=1000, fee_bps=0, slippage_bps=0)
        self.assertEqual(report["agent"]["final_equity"], 1250)
        self.assertEqual(report["agent"]["ending_positions"]["BTC-USD"]["units"], 5)
        self.assertEqual(report["agent"]["ending_positions"]["ETH-USD"]["units"], 5)

    def test_unfilled_decision_remains_visible(self):
        report = simulate(prices([100]), [decision(0, "Buy"), decision(1, "REVIEW")])
        self.assertEqual(report["agent"]["final_equity"], 10000)
        self.assertEqual(report["decision_counts"],
                         {"total": 2, "processed": 0, "review": 1, "unfilled": 2})
        self.assertEqual(report["decisions"][0]["status"], "unfilled")

    def test_invalid_or_ambiguous_inputs_fail(self):
        cases = [
            ([], [], {}, "at least one"),
            (prices([0]), [], {}, "positive"),
            (prices([float("nan")]), [], {}, "finite"),
            (prices([100]) * 2, [], {}, "duplicate price"),
            (prices([100, 200]) + prices([100], "ETH-USD"), [], {}, "identical"),
            (prices([100]), [decision(-1, "BUY")], {}, "unknown rating"),
            (prices([100]), [decision(-1, "Buy", "MISSING")], {}, "no prices"),
            (prices([100]), [decision(-1, "Buy")] * 2, {}, "duplicate decision"),
            (prices([100]), [decision(-2, "Buy"), decision(-1, "Sell")], {}, "one price bar"),
            ([Price(datetime(2026, 1, 1), "BTC-USD", 100)], [], {}, "timezone-aware"),
            (prices([100]), [], {"fee_bps": 10000}, "below 10000"),
            (prices([100]), [], {"initial_cash": -1}, "positive"),
        ]
        for bars, decisions, options, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                simulate(bars, decisions, **options)

    def test_cli_reads_fixtures_and_labels_synthetic_output(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            run = subprocess.run([
                sys.executable, "-m", "crypto_benchmark.replay",
                "--prices", str(root / "examples/crypto/synthetic_prices.csv"),
                "--decisions", str(root / "examples/crypto/synthetic_decisions.csv"),
                "--output", str(output), "--synthetic", "--fee-bps", "0", "--slippage-bps", "0",
            ], cwd=root, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(output.read_text())
            self.assertEqual(report["agent"]["final_equity"], 12500)
            self.assertEqual(report["buy_hold"]["final_equity"], 17500)
            self.assertTrue(report["synthetic"])
            self.assertIn("Synthetic accounting check", run.stdout)


if __name__ == "__main__":
    unittest.main()
