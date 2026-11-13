# Trading Bot Strategy Report

**Prepared for:** [user]  **Date:** [date]  **Mode:** backtest / paper / live

## 1. Claim Audit (if applicable)
| Figure | Value |
|---|---|
| Headline claim | [start] to [end] in [period] |
| Implied daily return | [x]% |
| Implied net return per trade | [x]% on [n] trades |
| One round trip of costs | [x]% |
| Verdict | [Impossible / Implausible / Plausible] |

Engagement-bait signals observed: [list or "none"]

## 2. Cost Model
- Venue and fee tier: [Binance spot, 0.10% taker]
- Slippage and spread assumptions: [2 bps, 1 bp]
- Transfer costs (arbitrage only): [$x per withdrawal]
- Breakeven arbitrage gap: [x]%

## 3. Strategy Specification
- Universe and interval: [BTCUSDT, 1d]
- Entry / exit rules: [Donchian 20 / 10, 2.0 x ATR(14) trailing stop]
- Sizing: [1% of equity risked per trade, no leverage]

## 4. Backtest Results
| Metric | Strategy | Buy and hold |
|---|---|---|
| Total return | | |
| CAGR | | |
| Max drawdown | | |
| Sharpe | | |
| Trades / win rate / profit factor | | n/a |
| Fees paid | | |

Data window: [dates]. Out-of-sample portion: [dates]. Caveats: [overfitting,
survivorship, single asset].

## 5. Risk Controls and Go-Live Checklist
- [ ] Risk per trade <= 1% and risk of ruin computed
- [ ] 60+ paper-trading days with no logic errors
- [ ] API keys in environment only, withdrawal permission disabled
- [ ] Kill switch at 90% of peak equity
- [ ] Monitoring cadence defined

## 6. Confidence and Disclaimer
Confidence in backtest fidelity: [low / medium / high] because [reason].
Hypothetical results, not investment advice. The user bears all risk.
