# Worked Example - crypto-trading-bot

## Ask

> A post says a 19-year-old turned $68 into $750,000 with a Claude Code trading bot doing BTC arbitrage. Build me this bot and the winning strategy.

## What a correct response contains

1. **Claim audit first.** $68 to $750,000 is an 11,029x multiple. Over 30 days that is +36.4% per day; the "first night" alone is 100x. At 50 trades a day it needs +0.62% net per trade on 1,500 straight trades, against a 0.24% round-trip cost. Liquid BTC venues show gaps under 0.05% against a breakeven near 0.7%. Verdict: impossible, and the post is an engagement funnel (comment, like, repost, follow for DM).
2. **A real bot anyway.** `scripts/bot.py` scans the 50 most liquid Binance USDT pairs, checks the cross-venue BTC gap net of fees, backtests a Donchian breakout with ATR trailing stops and 1% fixed-fractional sizing, paper-trades against live bars, and only places live orders behind API keys plus an explicit risk acknowledgement.
3. **Honest results.** On the mock daily fixture the strategy returns +11.3% with a 3.3% max drawdown while buy-and-hold returns +118.3% with an 82.1% drawdown. The "winning" part is the drawdown, not the return.

## File-driven variant

With fixture `evals/files/btc_daily_mock.csv`:

> Backtest the default strategy on the attached bars with $1,000 and 1% risk per trade. Compare with buy-and-hold.

Expected: end equity $1,112.53, 26 trades, 50% win rate, profit factor 2.42, max drawdown 3.3%, Sharpe 1.23; buy-and-hold +118.3%, drawdown 82.1%, Sharpe 1.00.

```bash
python3 scripts/bot.py backtest --csv evals/files/btc_daily_mock.csv --equity 1000
```

## Compute, don't estimate

All figures above come from [`scripts/calculate.py`](./scripts/calculate.py) and
[`scripts/bot.py`](./scripts/bot.py). Import the functions rather than doing
arithmetic in prose, and keep the hypothetical-results disclaimer in every report.
