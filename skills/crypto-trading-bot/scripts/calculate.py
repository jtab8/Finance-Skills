"""Crypto trading bot math: fees, arbitrage edge, sizing, backtest, metrics.

Every function is deterministic and stdlib-only so an agent can import it
instead of doing mental math. Run the file directly to self-test.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 1. Costs and arbitrage edge
# ---------------------------------------------------------------------------


def round_trip_cost(
    taker_fee: float = 0.001, slippage_bps: float = 2.0, spread_bps: float = 1.0
) -> float:
    """Fraction of notional lost to open AND close one position.

    taker_fee is per side (0.001 = 0.10%). Slippage and half-spread are paid on
    each side too, so everything is doubled.
    """
    per_side = taker_fee + slippage_bps / 10_000 + (spread_bps / 2) / 10_000
    return 2 * per_side


def arbitrage_net_edge(
    buy_price: float,
    sell_price: float,
    buy_fee: float = 0.001,
    sell_fee: float = 0.001,
    transfer_cost_quote: float = 0.0,
    notional_quote: float = 1_000.0,
    slippage_bps: float = 2.0,
) -> dict[str, float]:
    """Net edge of buying on venue A and selling on venue B, after all costs.

    Returns gross and net edge as fractions of notional plus the absolute
    profit in quote currency. transfer_cost_quote covers withdrawal / network
    fees in quote terms (e.g. the USDT value of a BTC withdrawal fee).
    """
    if buy_price <= 0 or sell_price <= 0 or notional_quote <= 0:
        raise ValueError("prices and notional must be positive")
    gross = sell_price / buy_price - 1
    slip = 2 * slippage_bps / 10_000
    fees = buy_fee + sell_fee
    net = gross - fees - slip - transfer_cost_quote / notional_quote
    return {
        "gross_edge": gross,
        "fees": fees,
        "slippage": slip,
        "transfer": transfer_cost_quote / notional_quote,
        "net_edge": net,
        "net_profit_quote": net * notional_quote,
    }


def breakeven_price_gap(
    buy_fee: float = 0.001,
    sell_fee: float = 0.001,
    transfer_cost_quote: float = 0.0,
    notional_quote: float = 1_000.0,
    slippage_bps: float = 2.0,
) -> float:
    """Minimum gross price gap (fraction) for a cross-venue arb to break even."""
    return buy_fee + sell_fee + 2 * slippage_bps / 10_000 + transfer_cost_quote / notional_quote


# ---------------------------------------------------------------------------
# 2. Claim auditing: what return does a story imply?
# ---------------------------------------------------------------------------


def implied_period_return(start_capital: float, end_capital: float, periods: int) -> float:
    """Constant per-period return that compounds start into end over N periods."""
    if start_capital <= 0 or end_capital <= 0 or periods <= 0:
        raise ValueError("capital and periods must be positive")
    return (end_capital / start_capital) ** (1 / periods) - 1


def audit_return_claim(
    start_capital: float,
    end_capital: float,
    days: int,
    trades_per_day: int = 50,
    round_trip_cost_frac: float = 0.0024,
) -> dict[str, float]:
    """Translate a viral profit claim into the per-trade edge it requires.

    Compares the required per-trade return against the cost of one round trip
    so the reader can see whether the story is even arithmetically possible.
    """
    total_mult = end_capital / start_capital
    daily = implied_period_return(start_capital, end_capital, days)
    n_trades = days * trades_per_day
    per_trade = implied_period_return(start_capital, end_capital, n_trades)
    return {
        "total_multiple": total_mult,
        "implied_daily_return": daily,
        "trades_assumed": float(n_trades),
        "implied_net_return_per_trade": per_trade,
        "round_trip_cost": round_trip_cost_frac,
        "required_gross_per_trade": per_trade + round_trip_cost_frac,
        "cost_as_share_of_required_gross": round_trip_cost_frac
        / (per_trade + round_trip_cost_frac),
    }


# ---------------------------------------------------------------------------
# 3. Position sizing and survival
# ---------------------------------------------------------------------------


def expectancy(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Expected profit per trade, in the same units as avg_win/avg_loss (>0)."""
    return win_rate * avg_win - (1 - win_rate) * avg_loss


def kelly_fraction(win_rate: float, payoff_ratio: float) -> float:
    """Full Kelly for a binary bet: f* = p - q / b. Negative means no edge."""
    if payoff_ratio <= 0:
        raise ValueError("payoff_ratio must be positive")
    return win_rate - (1 - win_rate) / payoff_ratio


def fractional_kelly(win_rate: float, payoff_ratio: float, fraction: float = 0.25) -> float:
    """Kelly scaled down (quarter-Kelly default) and floored at zero."""
    return max(0.0, kelly_fraction(win_rate, payoff_ratio) * fraction)


def position_size(
    equity: float, risk_per_trade: float, entry_price: float, stop_price: float
) -> float:
    """Units to buy so a stop-out loses exactly risk_per_trade of equity."""
    per_unit_risk = abs(entry_price - stop_price)
    if per_unit_risk <= 0:
        raise ValueError("stop must differ from entry")
    return equity * risk_per_trade / per_unit_risk


def risk_of_ruin(
    win_rate: float, payoff_ratio: float, risk_fraction: float, ruin_level: float = 0.5
) -> float:
    """Probability of drawing down to ruin_level of equity (fixed-fraction bets).

    Uses the classic gambler's-ruin approximation with units = ln(ruin)/ln(1-f).
    Returns a probability in [0, 1].
    """
    if not 0 < risk_fraction < 1:
        raise ValueError("risk_fraction must be in (0, 1)")
    edge = win_rate * payoff_ratio - (1 - win_rate)
    if edge <= 0:
        return 1.0
    units = math.log(ruin_level) / math.log(1 - risk_fraction)
    ratio = (1 - win_rate) / (win_rate * payoff_ratio)
    return min(1.0, ratio**units)


# ---------------------------------------------------------------------------
# 4. Indicators
# ---------------------------------------------------------------------------


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= period:
            running -= values[i - period]
        if i >= period - 1:
            out[i] = running / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> list[float | None]:
    """Wilder's Average True Range."""
    n = len(closes)
    out: list[float | None] = [None] * n
    trs: list[float] = []
    for i in range(n):
        if i == 0:
            trs.append(highs[0] - lows[0])
        else:
            trs.append(
                max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            )
    if n < period:
        return out
    prev = sum(trs[:period]) / period
    out[period - 1] = prev
    for i in range(period, n):
        prev = (prev * (period - 1) + trs[i]) / period
        out[i] = prev
    return out


def donchian(
    highs: list[float], lows: list[float], period: int
) -> tuple[list[float | None], list[float | None]]:
    """Highest high / lowest low over the PRIOR `period` bars (excludes current)."""
    n = len(highs)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for i in range(period, n):
        upper[i] = max(highs[i - period : i])
        lower[i] = min(lows[i - period : i])
    return upper, lower


def realized_volatility(
    closes: list[float], window: int = 20, periods_per_year: int = 365
) -> list[float | None]:
    """Annualized rolling log-return volatility."""
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    out: list[float | None] = [None] * len(closes)
    for i in range(window, len(rets) + 1):
        out[i] = statistics.pstdev(rets[i - window : i]) * math.sqrt(periods_per_year)
    return out


# ---------------------------------------------------------------------------
# 5. Backtest engine (long-only trend following with ATR stops)
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    entry_index: int
    entry_price: float
    units: float
    stop_price: float
    exit_index: int | None = None
    exit_price: float | None = None
    pnl: float | None = None
    reason: str = ""


@dataclass
class BacktestResult:
    equity_curve: list[float]
    trades: list[Trade] = field(default_factory=list)
    params: dict = field(default_factory=dict)

    def metrics(self, periods_per_year: int = 365) -> dict[str, float]:
        return performance_metrics(self.equity_curve, self.trades, periods_per_year)


def backtest_trend_following(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    *,
    initial_equity: float = 1_000.0,
    entry_period: int = 20,
    exit_period: int = 10,
    atr_period: int = 14,
    atr_stop_mult: float = 2.0,
    risk_per_trade: float = 0.01,
    taker_fee: float = 0.001,
    slippage_bps: float = 2.0,
    max_position_frac: float = 1.0,
) -> BacktestResult:
    """Donchian breakout entry, Donchian/ATR-stop exit, fixed-fractional sizing.

    Rules (evaluated on bar close, filled at next bar open ~ close here):
      - Enter long when close > highest high of prior `entry_period` bars.
      - Exit when close < lowest low of prior `exit_period` bars OR
        low <= trailing ATR stop.
      - Size so a stop-out loses `risk_per_trade` of equity; cap notional at
        `max_position_frac` of equity (no leverage by default).
      - Fees + slippage charged per side.
    """
    n = len(closes)
    if not (len(highs) == len(lows) == n):
        raise ValueError("highs, lows, closes must be the same length")
    upper, _ = donchian(highs, lows, entry_period)
    _, lower = donchian(highs, lows, exit_period)
    atr_vals = atr(highs, lows, closes, atr_period)
    cost_side = taker_fee + slippage_bps / 10_000

    equity = initial_equity
    cash = initial_equity
    curve: list[float] = []
    trades: list[Trade] = []
    open_trade: Trade | None = None

    for i in range(n):
        price = closes[i]
        if open_trade is not None:
            # trailing stop ratchets up with price
            if atr_vals[i] is not None:
                open_trade.stop_price = max(
                    open_trade.stop_price, price - atr_stop_mult * atr_vals[i]
                )
            exit_reason = ""
            exit_px = price
            if lows[i] <= open_trade.stop_price:
                exit_reason, exit_px = "stop", min(open_trade.stop_price, highs[i])
            elif lower[i] is not None and price < lower[i]:
                exit_reason = "channel_exit"
            if exit_reason:
                proceeds = open_trade.units * exit_px * (1 - cost_side)
                cash += proceeds
                open_trade.exit_index, open_trade.exit_price, open_trade.reason = (
                    i,
                    exit_px,
                    exit_reason,
                )
                open_trade.pnl = proceeds - open_trade.units * open_trade.entry_price * (
                    1 + cost_side
                )
                trades.append(open_trade)
                open_trade = None
        if (
            open_trade is None
            and upper[i] is not None
            and atr_vals[i] is not None
            and price > upper[i]
        ):
            stop = price - atr_stop_mult * atr_vals[i]
            if stop > 0:
                units = position_size(cash, risk_per_trade, price, stop)
                max_units = cash * max_position_frac / (price * (1 + cost_side))
                units = min(units, max_units)
                cost = units * price * (1 + cost_side)
                if units > 0 and cost <= cash:
                    cash -= cost
                    open_trade = Trade(i, price, units, stop)
        equity = cash + (open_trade.units * price if open_trade else 0.0)
        curve.append(equity)

    if open_trade is not None:  # mark open position to market, count as open
        open_trade.exit_index, open_trade.exit_price, open_trade.reason = n - 1, closes[-1], "open"
        open_trade.pnl = open_trade.units * closes[-1] * (
            1 - cost_side
        ) - open_trade.units * open_trade.entry_price * (1 + cost_side)
        trades.append(open_trade)

    return BacktestResult(
        curve,
        trades,
        {
            "entry_period": entry_period,
            "exit_period": exit_period,
            "atr_period": atr_period,
            "atr_stop_mult": atr_stop_mult,
            "risk_per_trade": risk_per_trade,
            "taker_fee": taker_fee,
            "slippage_bps": slippage_bps,
        },
    )


def buy_and_hold_curve(
    closes: list[float], initial_equity: float = 1_000.0, taker_fee: float = 0.001
) -> list[float]:
    units = initial_equity * (1 - taker_fee) / closes[0]
    return [units * c for c in closes]


# ---------------------------------------------------------------------------
# 6. Performance metrics
# ---------------------------------------------------------------------------


def max_drawdown(equity: list[float]) -> float:
    """Largest peak-to-trough decline as a positive fraction."""
    peak, worst = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        worst = max(worst, (peak - v) / peak if peak > 0 else 0.0)
    return worst


def cagr(equity: list[float], periods_per_year: int = 365) -> float:
    years = (len(equity) - 1) / periods_per_year
    if years <= 0 or equity[0] <= 0:
        return 0.0
    return (equity[-1] / equity[0]) ** (1 / years) - 1


def sharpe_ratio(equity: list[float], periods_per_year: int = 365, rf: float = 0.0) -> float:
    rets = [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity))]
    if len(rets) < 2:
        return 0.0
    sd = statistics.pstdev(rets)
    if sd == 0:
        return 0.0
    return (statistics.mean(rets) - rf / periods_per_year) / sd * math.sqrt(periods_per_year)


def performance_metrics(
    equity: list[float], trades: list[Trade] | None = None, periods_per_year: int = 365
) -> dict[str, float]:
    m: dict[str, float] = {
        "start_equity": equity[0],
        "end_equity": equity[-1],
        "total_return": equity[-1] / equity[0] - 1,
        "cagr": cagr(equity, periods_per_year),
        "max_drawdown": max_drawdown(equity),
        "sharpe": sharpe_ratio(equity, periods_per_year),
    }
    if trades:
        closed = [t for t in trades if t.pnl is not None]
        wins = [t.pnl for t in closed if t.pnl > 0]
        losses = [-t.pnl for t in closed if t.pnl <= 0]
        m["trades"] = float(len(closed))
        m["win_rate"] = len(wins) / len(closed) if closed else 0.0
        m["avg_win"] = statistics.mean(wins) if wins else 0.0
        m["avg_loss"] = statistics.mean(losses) if losses else 0.0
        m["profit_factor"] = (
            (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else float("inf")
        )
        m["expectancy_per_trade"] = (sum(wins) - sum(losses)) / len(closed) if closed else 0.0
        m["fees_paid_est"] = sum(
            t.units * (t.entry_price + (t.exit_price or t.entry_price)) * 0.001 for t in closed
        )
    return m


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # costs
    assert abs(round_trip_cost(0.001, 2.0, 1.0) - 0.0025) < 1e-12
    arb = arbitrage_net_edge(
        100_000, 100_150, 0.001, 0.001, transfer_cost_quote=5, notional_quote=1_000
    )
    assert abs(arb["gross_edge"] - 0.0015) < 1e-12
    assert arb["net_edge"] < 0, "0.15% gap must lose money after 0.2% fees + slippage + transfer"
    assert abs(breakeven_price_gap(0.001, 0.001, 5, 1_000, 2.0) - 0.0074) < 1e-12

    # claim audit: $68 -> $750,000 over 30 days, 50 trades/day
    claim = audit_return_claim(68, 750_000, 30, 50, 0.0024)
    assert abs(claim["total_multiple"] - 11029.41) < 0.01
    assert abs(claim["implied_daily_return"] - 0.3638) < 0.001  # ~36% per day
    assert 0.0061 < claim["implied_net_return_per_trade"] < 0.0063
    # first night: $68 -> $6,800 (= $68 + $6,732 profit) in one session
    assert abs(implied_period_return(68, 6_800, 1) - 99.0) < 1e-9

    # sizing / survival
    assert abs(expectancy(0.4, 2.0, 1.0) - 0.2) < 1e-12
    assert abs(kelly_fraction(0.55, 1.0) - 0.10) < 1e-12
    assert abs(fractional_kelly(0.55, 1.0, 0.25) - 0.025) < 1e-12
    assert fractional_kelly(0.40, 1.0) == 0.0
    assert abs(position_size(10_000, 0.01, 100, 95) - 20.0) < 1e-9
    assert risk_of_ruin(0.45, 1.0, 0.02) == 1.0  # negative edge -> certain ruin
    ror = risk_of_ruin(0.55, 1.0, 0.02)
    assert 0 < ror < 0.01, ror

    # indicators
    s = sma([1, 2, 3, 4, 5], 3)
    assert s[:2] == [None, None] and s[2:] == [2.0, 3.0, 4.0]
    e = ema([1, 2, 3, 4, 5], 3)
    assert e[2] == 2.0 and abs(e[4] - 4.0) < 1e-9
    up, lo = donchian([1, 2, 3, 4, 5], [0, 1, 2, 3, 4], 2)
    assert up[2] == 2 and lo[2] == 0 and up[4] == 4

    # backtest on a synthetic trending series with pullbacks
    import random

    rng = random.Random(7)
    px = [100.0]
    for i in range(400):
        drift = 0.004 if (i // 100) % 2 == 0 else -0.002
        px.append(px[-1] * math.exp(drift + rng.gauss(0, 0.02)))
    highs = [p * (1 + abs(rng.gauss(0, 0.005))) for p in px]
    lows = [p * (1 - abs(rng.gauss(0, 0.005))) for p in px]
    res = backtest_trend_following(highs, lows, px, initial_equity=1_000)
    m = res.metrics()
    assert len(res.equity_curve) == len(px)
    assert m["trades"] > 3
    assert 0 <= m["max_drawdown"] < 1
    assert all(t.units > 0 for t in res.trades)
    # position can never exceed equity (no leverage)
    for t in res.trades:
        assert t.units * t.entry_price <= 1.0001 * res.equity_curve[t.entry_index] + 1e-6
    bh = buy_and_hold_curve(px, 1_000)
    assert abs(max_drawdown([100, 80, 120, 60]) - 0.5) < 1e-12
    assert abs(cagr([100, 200], 1) - 1.0) < 1e-12

    print(
        f"round-trip 0.25% | arb net {arb['net_edge']*100:+.3f}% | claim needs "
        f"{claim['implied_daily_return']*100:.0f}%/day | ruin {ror:.4f} | "
        f"backtest {m['trades']:.0f} trades, ret {m['total_return']*100:+.1f}%, "
        f"maxDD {m['max_drawdown']*100:.1f}% vs B&H {bh[-1]/bh[0]-1:+.1%} | OK"
    )
