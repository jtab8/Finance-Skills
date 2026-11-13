# Exchange Costs: What a Retail Bot Pays

Defaults used by `scripts/bot.py` (override with env vars `TAKER_FEE`,
`SLIPPAGE_BPS`). Verify current fees on the exchange's own fee page before
relying on these; tiers change.

## Trading fees (spot, base tier, taker)

| Venue | Taker | Maker | Notes |
|---|---|---|---|
| Binance | 0.10% | 0.10% | 25% discount paying fees in BNB |
| Kraken | 0.26% | 0.16% | Tiers drop with 30-day volume |
| Coinbase Advanced | 0.60% | 0.40% | Much higher at retail tiers |

A round trip on Binance costs 0.20% in fees before spread and slippage.

## Hidden costs

- **Spread**: liquid BTC/USDT pairs quote 0.5 to 2 bps; small alts 10 to 50 bps.
- **Slippage**: market orders walk the book; assume 2 bps on majors for
  retail size and far more for alts or during volatility spikes.
- **Withdrawal / network fees**: a BTC on-chain withdrawal can cost $1 to $20
  depending on congestion; ERC-20 tokens more. This alone kills small arbs.
- **Transfer latency**: on-chain confirmations take minutes; the gap you saw
  is gone before the coins arrive.
- **Funding**: perpetual futures charge funding every 8h; not modelled here.

## Why arbitrage gaps close

Market makers quote both venues simultaneously with inventory on each side,
so they never transfer coins to capture a gap. Their cost is near zero and
their latency is sub-millisecond. A retail REST client polling once a second
sees the gap only after it has been filled.

## Minimum order sizes

| Venue | Spot minimum notional |
|---|---|
| Binance | about $10 per order (varies by pair) |
| Kraken | varies by asset, typically $5 to $10 equivalent |

With $68 of capital, position sizing at 1% risk per trade produces orders
below these minimums on most setups, which is why `bot.py` enforces
`--min-notional` and skips trades that cannot be placed.
