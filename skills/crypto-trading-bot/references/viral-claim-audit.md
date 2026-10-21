# Viral Claim Audit: "$68 to $750,000 with a Claude Code trading bot"

Every number below comes from `scripts/calculate.py`. Reproduce with:

```python
from calculate import audit_return_claim, implied_period_return, round_trip_cost
```

## The claim

- Starting capital: $68
- First night profit: $6,732 (capital becomes $6,800)
- Total profit "so far": $750,000
- Mechanism: scans 50+ markets, syncs Binance BTC data every second, executes
  on "price errors" and "arbitrage windows", runs 24/7

## The arithmetic

| Figure | Value |
|---|---|
| First-night multiple | 100x in one session (+9,900%) |
| Total multiple | 11,029x |
| Implied daily return if it took 30 days | +36.4% per day |
| Implied daily return if it took 90 days | +10.9% per day |
| Trades assumed (30 days x 50/day) | 1,500 |
| Net return required per trade | +0.62% |
| One round trip of costs (0.10% fee x2 + slippage) | 0.24% |
| Gross capture required per trade | 0.86% with zero losing trades |

## Why it cannot be arbitrage

Cross-venue BTC/USDT gaps between Binance and Kraken are usually under
0.05%. The breakeven gap for a $1,000 trade with a $5 transfer cost is
0.74% (`breakeven_price_gap`). The claimed strategy would need gaps 15 to
20 times larger than the market shows, on every trade, forever.

With $68 of capital the exchange minimum notional ($10 on Binance spot)
allows at most six open positions, and one BTC withdrawal fee consumes a
large share of the account. "Scanning 50 markets" with $68 is a dashboard,
not a portfolio.

## Engagement-bait signature

- Follow + like + repost + comment a keyword "so I can DM you"
- "Free, 24 hours only"
- An AI-generated image presented as the bot's dashboard
- An account with tens of thousands of posts and recycled stories
- No code, no trade log, no exchange statement

The funnel usually ends in a paid course, a referral link, a "signals"
group, or a request for exchange API keys. Never share API keys or deposit
to a wallet to "unlock" a bot.

## What to say to the user

1. The story is arithmetically impossible on liquid markets.
2. The post is a lead-generation funnel, not a strategy.
3. A real bot is buildable in an afternoon; the hard part is risk control,
   and this skill ships one with its limits stated.
