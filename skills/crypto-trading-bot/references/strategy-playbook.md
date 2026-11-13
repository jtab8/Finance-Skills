# Strategy Playbook: Long-Only Trend Following with Hard Risk Limits

This is the system `scripts/bot.py` runs. It is deliberately simple. Every
parameter below exists to control a specific failure mode, not to fit history.

## Rules

| Element | Rule | Default |
|---|---|---|
| Universe | Liquid USDT spot pairs (top N by 24h quote volume) | BTCUSDT |
| Interval | Daily bars (4h acceptable; below 1h costs dominate) | 1d |
| Entry | Close above the highest high of the prior `entry` bars | 20 |
| Exit A | Close below the lowest low of the prior `exit` bars | 10 |
| Exit B | Low touches the trailing stop at `close - k * ATR(14)` | k = 2.0 |
| Sizing | Units = equity x risk_per_trade / (entry - stop) | 1% |
| Cap | Notional never exceeds 100% of equity (no leverage) | 1.0 |
| Costs | Taker fee + slippage per side | 0.10% + 2 bps |

Direction is long-only on spot. Shorting on margin or perpetual futures adds
funding costs and liquidation risk that this playbook does not model.

## Why each rule exists

- **Breakout entry**: you are paid for being in the market during the few
  weeks a year when crypto trends hard. The entry does not predict; it reacts.
- **Channel exit**: gets you out when the trend has clearly failed, without
  needing a forecast.
- **ATR trailing stop**: converts an open profit into a realized one and caps
  the loss on a false breakout. ATR adapts the stop to current volatility so
  the same rule works at $20K and $100K BTC.
- **Fixed-fractional sizing**: the most important line of code. A 1% risk
  per trade means twenty consecutive losers cost about 18% of equity. At 10%
  per trade the same streak costs 88%.
- **No leverage cap**: removes liquidation as a failure mode entirely.

## What the backtest will show

On the mock daily fixture (730 bars, two bull and two bear regimes):

| Metric | Strategy | Buy and hold |
|---|---|---|
| Total return | +11.3% | +118.3% |
| Max drawdown | 3.3% | 82.1% |
| Sharpe | 1.23 | 1.00 |
| Trades | 26 | 1 |
| Win rate | 50% | n/a |
| Profit factor | 2.42 | n/a |

Read that honestly: the bot gave up most of the upside to avoid an 82%
drawdown. That is what a 1% risk budget buys. Raising `--risk` scales both
return and drawdown roughly linearly until position caps bind.

## Tuning without fooling yourself

1. Change one parameter at a time and record the whole metrics table.
2. Split data: tune on the first 70% of bars, evaluate on the last 30%.
3. Prefer parameters in the middle of a plateau over the single best value.
4. If a tweak improves return but raises max drawdown or lowers profit
   factor, you have moved risk, not created edge.
5. Fewer than 30 closed trades proves nothing either way.

## Operating cadence

- Run `paper` once per bar close (cron at 00:05 UTC for daily bars).
- Review the JSON state file weekly: equity, open stop, action log.
- Kill switch: if equity falls below 90% of its peak, stop and review.
- Promote to `live` only after at least 60 paper days with no logic errors.
