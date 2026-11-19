# Run the crypto benchmark pilot

This fork adds a crypto evaluation package around TradingAgents. Start with a bounded daily pilot, inspect its data and resource use, then expand the experiment to the top 100 assets. Wallet execution is a later project.

The first implementation includes a top-100 universe snapshot, experiment sizing, a ChatGPT-only daily runner, public daily price collection, crypto-context archives, and an offline spot portfolio replay. It does not yet provide a validated 100-asset instrument map, hourly agent evaluation, a scheduler, or evidence that the agents make money.

## Install

Use Python 3.11 or newer from this repository.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

## Size the experiment

```bash
.venv/bin/python -m crypto_benchmark plan --assets 100 --days 30
```

One hundred assets require 2,400 decisions per day at an hourly cadence, 600 at four hours, or 100 daily. Each decision involves multiple model calls. These are arithmetic workload counts, not measured throughput or plan-usage estimates.

Only daily analysis is implemented. Intraday work needs timestamp-aware data cutoffs throughout the graph. Faster polling alone does not create a valid hourly benchmark.

## Capture the universe

```bash
.venv/bin/python -m crypto_benchmark universe --output results/crypto/universe.json
```

The snapshot preserves the current top 100 CoinGecko IDs and the original response with its hash. Review flags identify possible stablecoins, wrapped assets, and duplicate symbols. Nothing is automatically excluded. Output files cannot be overwritten.

Set `COINGECKO_DEMO_API_KEY` if the public service requires a free demo key. The downloader honors `SSL_CERT_FILE`, uses certifi when installed, and otherwise uses system certificate trust.

Map CoinGecko IDs to confirmed vendor instruments before running them. Do not convert every symbol to a Yahoo ticker automatically. A current snapshot does not establish which assets ranked in the top 100 historically. Historical studies need archived membership or an explicit survivorship-bias label.

## Connect the ChatGPT plan

```bash
.venv/bin/python -m cli.main login chatgpt
```

Complete the browser consent on this machine. TradingAgents stores the login outside the repository in `~/.tradingagents/chatgpt-auth.json`. Do not copy that file into results or Git.

List the models available to the connected plan.

```bash
.venv/bin/python -c 'from tradingagents.llm_clients.chatgpt_auth import list_models; print(list_models())'
```

Use returned model IDs for `QUICK_MODEL` and `DEEP_MODEL` below. Both tiers are forced to use the ChatGPT provider. No API-key fallback is allowed. Plan eligibility and usage limits still apply.

## Run a small historical pilot

Replace the two model placeholders with IDs from your plan. Run the command without `--execute` to inspect the plan without network access or output writes.

```bash
.venv/bin/python -m crypto_benchmark.run \
  --tickers BTC-USD,ETH-USD \
  --start 2026-11-01 --end 2026-11-01 \
  --quick-model QUICK_MODEL --deep-model DEEP_MODEL \
  --max-decisions 2 --output results/crypto/pilot
```

Add `--execute` to use the connected plan. The default starts with the market analyst, followed by the investment debate, trader, and risk review. Add `--analysts market,news,social` for the news/social experiment. Use a different output directory for every experiment.

Each decision has isolated memory. This prevents learning from outcomes generated elsewhere in the same retrospective experiment. The runner stops on the first failure and records completed work. Its decision limit bounds cells, not model calls or subscription usage.

The run saves `run.json`, `decisions.jsonl`, `decisions.csv`, and the upstream reports. Audit records contain model settings, code provenance, elapsed time, and reported token metadata. Missing usage metadata is not treated as free inference.

Tool calls are recorded as unmeasured because the runner's callbacks attach to model clients, not data tools. A null tool count does not mean the agents skipped data retrieval.

## Fetch prices and replay decisions

```bash
.venv/bin/python -m crypto_benchmark.prices \
  --tickers BTC-USD,ETH-USD \
  --start 2026-11-02 --end 2026-11-18 \
  --output results/crypto/prices

.venv/bin/python -m crypto_benchmark.replay \
  --prices results/crypto/prices/prices.csv \
  --decisions results/crypto/pilot/decisions.csv \
  --fee-bps 10 --slippage-bps 5 \
  --output results/crypto/pilot/replay.json
```

Fees and slippage are assumptions, not measurements of an exchange. Yahoo daily crypto prices are an aggregate source, not evidence that an exchange would fill at that price.

CSV timestamps represent actual candle-close times, never candle-open labels. A Yahoo bar dated November 2 closes at November 3 00:00 UTC. A historical decision for November 1 becomes available at November 2 00:00 UTC and fills only at a strictly later close. The price file must cover the evaluation period only. Do not prepend indicator warm-up history, because buy-and-hold enters at the first supplied price.

The evaluator gives every asset an equal independent cash allocation. Buy targets 100% exposure, Overweight 75%, Underweight 25%, and Sell 0%. Hold leaves the existing units unchanged. REVIEW makes no trade and remains visible in the report. This allocation policy belongs to the evaluator; it is not a position size produced by TradingAgents.

Agent decisions, buy-and-hold, and cash share the same evaluation grid. Reports include returns after costs, drawdown, turnover, fees, fills, holdings, and unfilled decisions. Missing bars and multiple decisions assigned to one execution bar fail explicitly. Positions are marked to market at the end without a forced sale.

## Archive additional crypto context

```bash
.venv/bin/python -m crypto_benchmark.context --output results/crypto/context.json
```

This captures Alternative.me's Bitcoin Fear & Greed index, CoinGecko BTC dominance, and CoinGecko trending asset IDs. Each response retains its source, retrieval time, raw content, and hash. Missing sources remain unavailable rather than becoming neutral scores. Trending is a separate signal, not a news article.

Collection is for future point-in-time experiments. The daily runner does not consume these archives yet. A snapshot fetched today must not be supplied to yesterday's agents. The context reader enforces the retrieval cutoff even when a source supplies an older publication timestamp.

## Verify accounting without model access

```bash
.venv/bin/python -m crypto_benchmark.replay \
  --prices examples/crypto/synthetic_prices.csv \
  --decisions examples/crypto/synthetic_decisions.csv \
  --fee-bps 0 --slippage-bps 0 --synthetic \
  --output results/crypto/synthetic-check.json

.venv/bin/python -m unittest discover -s tests -p 'test_crypto_*.py'
```

The synthetic example ends with agent equity of $12,500, buy-and-hold equity of $17,500, and cash of $10,000. These are fixed accounting expectations, not crypto trading results.

## Complete the performance study

Freeze the asset universe, dates, execution rules, costs, and tuning period before scoring a held-out period. Compare cash, buy-and-hold, a simple trend strategy, a single model, and the full agent team. The latter three comparison configurations still need implementation and execution.

Run market-only and market-plus-news/social variants on identical asset/date cells. Repeat model runs to measure variation. Report failed and unavailable cells alongside returns. Archive inputs before claiming reproducibility. Date filtering does not remove knowledge a model learned during pretraining, and currently retrieved historical text may be incomplete.

Measure hourly, four-hourly, and daily decisions only after the intraday data work. A separate experiment can scan all 100 assets cheaply and analyze a shortlist. Include skipped assets in that policy's evaluation so selection does not disappear from the results.

Finish with forward paper decisions recorded before their outcomes. Decide whether to build wallet execution from that evidence.

See [the design](CRYPTO_DESIGN.md) and [the comparison with trading-bot-agent](REFERENCE_REVIEW.md).
