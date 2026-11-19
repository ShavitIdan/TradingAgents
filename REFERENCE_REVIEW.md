# Useful ideas from trading-bot-agent

Reviewed [ardhanurfan/trading-bot-agent](https://github.com/ardhanurfan/trading-bot-agent) at commit `e478951d308c5a7e606ff25e92c0c2c7e2e3c08e`. The repository includes an Apache-2.0 license. No implementation code was copied into this fork.

Keep the newer TauricResearch base. Its existing sentiment analyst uses Reddit, StockTwits, and news with date-window filtering. The reference repository's social analyst exposes only its news tool.

| Idea | Finding | Decision |
| --- | --- | --- |
| Broad market scanner | Bulk exchange data filters candidates before expensive agent analysis | Evaluate this as a separate selection policy after the full-universe baseline |
| Fear & Greed, BTC dominance, trending | Useful distinct crypto-context inputs | Add immutable collection with provenance and retrieval-time cutoffs |
| News/social integration | Trending labels become synthetic headlines, and live news ignores its historical date arguments | Keep existing upstream news/social tools |
| Historical exchange prices | Binance kline pagination exists, but the primary adapter uses daily bars | Reference for later intraday work, not a completed solution |
| Autonomous executor | The analysis graph can validate and enqueue real orders | Keep execution outside the benchmark |

The reference scanner supplies plausible neutral defaults after some failed requests. Our context archive records unavailable data instead. It also retains CoinGecko IDs rather than resolving ambiguous symbols to the first search result.

The new context collection is not connected to historical decisions. That separation prevents current sentiment from silently improving retrospective results. A future experiment should compare market only, market plus existing news/social, and market plus news/social plus archived crypto context.

Relevant reference files are `tradingagents/integrations/ticker_scanner.py`, `tradingagents/dataflows/binance_spot_data.py`, `tradingagents/agents/utils/crypto_on_chain_tools.py`, and `tradingagents/agents/analysts/social_media_analyst.py`.
