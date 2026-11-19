# Crypto benchmark design

The goal is to measure TradingAgents on the top 100 crypto assets before building wallet execution. The user chose ChatGPT plan access and no paid API calls.

## Scope of this first implementation

Capture a dated top-100 market-cap universe, estimate decision volume at several cadences, run bounded daily crypto analyses through the upstream graph, and replay saved decisions against timestamped prices. A synthetic example verifies accounting. It is not evidence of trading performance.

The upstream revision is `9fa1196fffbcfd9de5146d2dd2869df84ab99838`. `TradingAgentsGraph.propagate` runs analysts, investment debate, the trader, and risk review. It returns a state and a five-tier rating. State JSON includes the ticker, analysis date, rating, reports, and run settings. `run_backtest` scores independent decisions and does not carry cash or holdings. Its default comparison is SPY for crypto. Its date-based inputs cannot establish intraday information cutoffs.

## Design alternatives

| Shape | Consequence | Decision |
| --- | --- | --- |
| Add accounting and crypto scheduling to upstream `run_backtest` | Changes the meaning of an existing decision-quality API and mixes inference with fills | Rejected |
| Add a separate `crypto_benchmark` package | Keeps the graph reusable and makes portfolio accounting independently testable | Selected |

## Contracts

- Universe snapshots preserve CoinGecko IDs, ranks, symbols, market caps, source, retrieval time, exact UTF-8 response text, and its hash. They describe the present membership only. Symbols alone are not an instrument identifier. Mapping to a price vendor needs explicit validation.
- Planning is offline by default. The CLI reports 1h, 4h, and 24h analysis counts. Only daily graph runs are implemented. No scheduler or repeated model use starts implicitly.
- Every model run pins `chatgpt` for both tiers and rejects paid-provider fallback. Each invocation has a maximum number of decisions. The first pilot is deliberately bounded before scaling to 100 assets.
- Replay accepts UTC timestamped prices and ratings. A decision executes at the first price strictly after it became available. Daily upstream states become available at the following UTC midnight. Price timestamps denote actual candle closes. The graph's model knowledge cannot be made historically point-in-time by this conversion.
- Each asset has an independent, equal-funded spot sleeve. Cash and units persist between decisions. The replay policy maps Buy to 100%, Overweight to 75%, Underweight to 25%, and Sell to 0% of sleeve equity. Hold leaves units unchanged. REVIEW makes no trade and is counted separately. This mapping belongs to the evaluator, not TradingAgents.
- Fees and slippage apply to both agent and buy-and-hold turnover. No shorting, leverage, wallet calls, interest, or implicit end liquidation. Missing data and unresolved decisions remain visible.
- Artifacts include decisions, fills, equity, settings, and summary metrics. A synthetic smoke run is labeled synthetic everywhere it is reported.

## Accepted changes after review

The main agent accepted the independent reviewer's midnight-boundary correction. An end-of-day timestamp one microsecond before midnight could fill against the just-completed candle. Availability is now exactly the following midnight, with a regression through the runner CSV and replay.

The user asked to inspect ardhanurfan/trading-bot-agent. The main agent accepted independent implementations of immutable crypto-context collection and a daily price downloader. Existing upstream news/social tools remain in use. New context is not injected into historical runs. See REFERENCE_REVIEW.md.

## Benchmark protocol to complete after the first pilot

Use archived historical universe membership or label a fixed-current-universe study with survivorship bias. Freeze training and tuning periods separately from held-out periods. Compare cash, buy-and-hold, a simple trend rule, a single LLM, and the full agent team. Repeat stochastic model runs. Report return after costs, drawdown, turnover, exposure, failure coverage, inference time, and plan usage. Measure 1h, 4h, and 24h cadence only after timestamp-safe data adapters exist. Historical text availability and model pretraining contamination remain limitations. Forward paper decisions recorded before outcomes are the final test before any execution work.

## Throughput checkpoint

- Blocking first steps. Inspect upstream output, pin scope and replay timing, verify the personal GitHub account.
- Independent workstreams. Replay accounting and universe planning own disjoint modules. The main agent owns the graph runner and documentation.
- Shared mutable state. Workers write separate files. Runs use separate output directories and memory logs.
- Smallest safe decomposition. One owner handles accounting and its tests. A second handles universe planning and its tests. Integration and direct CLI verification stay with the main agent.
