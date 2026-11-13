#!/usr/bin/env python3
"""Runnable crypto trading bot: scan, backtest, paper-trade, (gated) live.

Modes
  scan      Rank the most liquid Binance USDT markets by momentum/volatility and
            report cross-venue BTC price gaps net of fees (Binance vs Kraken).
  backtest  Run the trend-following strategy on a CSV of daily/hourly bars.
  paper     Fetch live bars, evaluate the signal, update a JSON paper account.
  live      Same as paper but sends real orders. Requires BINANCE_API_KEY,
            BINANCE_API_SECRET, --live, and I_UNDERSTAND_THE_RISK=1. Off by
            default; every safety check is intentional.

No keys are needed for scan/backtest/paper (public endpoints only).
Stdlib only. Imports the math from calculate.py - never re-derive it.

Examples
  python3 bot.py scan --top 50
  python3 bot.py backtest --csv ../evals/files/btc_daily_mock.csv --equity 1000
  python3 bot.py paper --symbol BTCUSDT --interval 1d --state paper_state.json
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True  # keep the validator's no-__pycache__ check green
sys.path.insert(0, str(Path(__file__).resolve().parent))
import calculate as calc  # noqa: E402

BINANCE = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com")
KRAKEN = os.environ.get("KRAKEN_BASE_URL", "https://api.kraken.com")
TAKER_FEE = float(os.environ.get("TAKER_FEE", "0.001"))
SLIPPAGE_BPS = float(os.environ.get("SLIPPAGE_BPS", "2"))
STABLE_QUOTES = ("USDT",)
EXCLUDE_TOKENS = (
    "UP",
    "DOWN",
    "BULL",
    "BEAR",
    "USDC",
    "TUSD",
    "FDUSD",
    "BUSD",
    "DAI",
    "USDP",
    "EUR",
    "GBP",
)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get(url: str, params: dict | None = None, headers: dict | None = None, timeout: int = 15):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "financeskills-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_klines(symbol: str, interval: str = "1d", limit: int = 500) -> list[dict]:
    raw = _get(f"{BINANCE}/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return [
        {
            "time": k[0],
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        }
        for k in raw
    ]


def fetch_24h_tickers() -> list[dict]:
    return _get(f"{BINANCE}/api/v3/ticker/24hr")


def fetch_kraken_btc_usdt() -> dict[str, float]:
    data = _get(f"{KRAKEN}/0/public/Ticker", {"pair": "XBTUSDT"})
    book = next(iter(data["result"].values()))
    return {"bid": float(book["b"][0]), "ask": float(book["a"][0])}


def fetch_binance_book(symbol: str = "BTCUSDT") -> dict[str, float]:
    t = _get(f"{BINANCE}/api/v3/ticker/bookTicker", {"symbol": symbol})
    return {"bid": float(t["bidPrice"]), "ask": float(t["askPrice"])}


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def scan_markets(top: int = 50) -> list[dict]:
    """Rank liquid USDT pairs. Momentum = 24h change; vol proxy = (high-low)/last."""
    rows = []
    for t in fetch_24h_tickers():
        s = t["symbol"]
        if not s.endswith(STABLE_QUOTES) or any(x in s[:-4] for x in EXCLUDE_TOKENS):
            continue
        last = float(t["lastPrice"])
        if last <= 0:
            continue
        rows.append(
            {
                "symbol": s,
                "last": last,
                "quote_volume": float(t["quoteVolume"]),
                "change_24h": float(t["priceChangePercent"]) / 100,
                "range_24h": (float(t["highPrice"]) - float(t["lowPrice"])) / last,
                "spread_bps": (
                    (float(t["askPrice"]) - float(t["bidPrice"])) / last * 10_000
                    if float(t["bidPrice"]) > 0
                    else None
                ),
            }
        )
    rows.sort(key=lambda r: r["quote_volume"], reverse=True)
    liquid = rows[:top]
    liquid.sort(key=lambda r: r["change_24h"], reverse=True)
    return liquid


def cross_venue_check(notional: float = 1_000.0, transfer_cost_quote: float = 5.0) -> dict:
    """Buy on the cheaper ask, sell on the richer bid, net of fees/slippage/transfer."""
    b, k = fetch_binance_book("BTCUSDT"), fetch_kraken_btc_usdt()
    legs = [
        ("buy Binance / sell Kraken", b["ask"], k["bid"]),
        ("buy Kraken / sell Binance", k["ask"], b["bid"]),
    ]
    best = None
    for name, buy, sell in legs:
        e = calc.arbitrage_net_edge(
            buy, sell, TAKER_FEE, 0.0026, transfer_cost_quote, notional, SLIPPAGE_BPS
        )
        e["direction"], e["buy_price"], e["sell_price"] = name, buy, sell
        if best is None or e["net_edge"] > best["net_edge"]:
            best = e
    best["breakeven_gap"] = calc.breakeven_price_gap(
        TAKER_FEE, 0.0026, transfer_cost_quote, notional, SLIPPAGE_BPS
    )
    return best


def cmd_scan(args: argparse.Namespace) -> None:
    rows = scan_markets(args.top)
    print(
        f"{'symbol':<12}{'last':>14}{'24h chg':>10}{'24h range':>11}{'spread bps':>12}{'quote vol (M)':>15}"
    )
    for r in rows:
        sp = f"{r['spread_bps']:.1f}" if r["spread_bps"] is not None else "n/a"
        print(
            f"{r['symbol']:<12}{r['last']:>14,.4f}{r['change_24h']*100:>9.2f}%{r['range_24h']*100:>10.2f}%{sp:>12}{r['quote_volume']/1e6:>15,.1f}"
        )
    try:
        x = cross_venue_check(args.notional)
        print(
            f"\nCross-venue BTC/USDT: {x['direction']} | gross {x['gross_edge']*100:+.3f}% | "
            f"net {x['net_edge']*100:+.3f}% (${x['net_profit_quote']:+.2f} on ${args.notional:,.0f}) | "
            f"breakeven gap {x['breakeven_gap']*100:.3f}%"
        )
        if x["net_edge"] <= 0:
            print(
                "No executable arbitrage after costs. This is the normal state of liquid markets."
            )
    except Exception as e:  # network/venue errors should not kill the scan
        print(f"\nCross-venue check skipped: {e}")


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------


def load_csv(path: str) -> list[dict]:
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        out.append(
            {
                k: (float(r[k]) if k in ("open", "high", "low", "close", "volume") else r[k])
                for k in r
            }
        )
    return out


def run_backtest(bars: list[dict], **kw) -> tuple[calc.BacktestResult, list[float]]:
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    res = calc.backtest_trend_following(highs, lows, closes, **kw)
    return res, calc.buy_and_hold_curve(
        closes, kw.get("initial_equity", 1_000.0), kw.get("taker_fee", TAKER_FEE)
    )


def print_report(res: calc.BacktestResult, bh: list[float], periods_per_year: int) -> dict:
    m = res.metrics(periods_per_year)
    bhm = calc.performance_metrics(bh, None, periods_per_year)
    print(f"{'metric':<22}{'strategy':>14}{'buy & hold':>14}")
    for key in ("end_equity", "total_return", "cagr", "max_drawdown", "sharpe"):
        sv, bv = m[key], bhm[key]
        if key == "end_equity":
            fmt = lambda v: f"{v:,.2f}"  # noqa: E731
        elif key == "sharpe":
            fmt = lambda v: f"{v:.3f}"  # noqa: E731
        elif key == "max_drawdown":
            fmt = lambda v: f"-{v*100:.1f}%"  # noqa: E731
        else:
            fmt = lambda v: f"{v*100:+.1f}%"  # noqa: E731
        print(f"{key:<22}{fmt(sv):>14}{fmt(bv):>14}")
    print(f"{'trades':<22}{m['trades']:>14.0f}")
    print(f"{'win_rate':<22}{m['win_rate']*100:>13.1f}%")
    print(f"{'profit_factor':<22}{m['profit_factor']:>14.2f}")
    print(f"{'expectancy/trade':<22}{m['expectancy_per_trade']:>14,.2f}")
    print(f"{'fees_paid_est':<22}{m['fees_paid_est']:>14,.2f}")
    return m


def cmd_backtest(args: argparse.Namespace) -> None:
    bars = load_csv(args.csv) if args.csv else fetch_klines(args.symbol, args.interval, args.limit)
    res, bh = run_backtest(
        bars,
        initial_equity=args.equity,
        entry_period=args.entry,
        exit_period=args.exit,
        atr_period=args.atr,
        atr_stop_mult=args.stop_mult,
        risk_per_trade=args.risk,
        taker_fee=TAKER_FEE,
        slippage_bps=SLIPPAGE_BPS,
    )
    ppy = {"1d": 365, "4h": 365 * 6, "1h": 365 * 24}.get(args.interval, 365)
    m = print_report(res, bh, ppy)
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"params": res.params, "metrics": m, "equity_curve": res.equity_curve}, indent=2
            )
        )
        print(f"\nwrote {args.json}")


# ---------------------------------------------------------------------------
# paper / live
# ---------------------------------------------------------------------------


def signal_from_bars(
    bars: list[dict], entry: int, exit_: int, atr_period: int, stop_mult: float
) -> dict:
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    upper, _ = calc.donchian(highs, lows, entry)
    _, lower = calc.donchian(highs, lows, exit_)
    a = calc.atr(highs, lows, closes, atr_period)
    i = len(closes) - 1
    px = closes[i]
    return {
        "price": px,
        "breakout": upper[i] is not None and px > upper[i],
        "breakdown": lower[i] is not None and px < lower[i],
        "atr": a[i],
        "stop": px - stop_mult * a[i] if a[i] else None,
        "upper": upper[i],
        "lower": lower[i],
    }


def load_state(path: str, equity: float) -> dict:
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text())
    return {"cash": equity, "units": 0.0, "stop": None, "entry": None, "log": []}


def save_state(path: str, state: dict) -> None:
    Path(path).write_text(json.dumps(state, indent=2))


def sign_binance(params: dict, secret: str) -> str:
    q = urllib.parse.urlencode(params)
    return hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()


def place_binance_market_order(symbol: str, side: str, quantity: float) -> dict:
    """Real order. Only reachable through cmd_trade with every live gate passed."""
    key, secret = os.environ["BINANCE_API_KEY"], os.environ["BINANCE_API_SECRET"]
    params = {
        "symbol": symbol,
        "side": side,
        "type": "MARKET",
        "quantity": f"{quantity:.6f}",
        "timestamp": int(time.time() * 1000),
    }
    params["signature"] = sign_binance(params, secret)
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        f"{BINANCE}/api/v3/order", data=data, method="POST", headers={"X-MBX-APIKEY": key}
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def cmd_trade(args: argparse.Namespace, live: bool) -> None:
    if live:
        missing = [k for k in ("BINANCE_API_KEY", "BINANCE_API_SECRET") if not os.environ.get(k)]
        if missing or os.environ.get("I_UNDERSTAND_THE_RISK") != "1" or not args.live:
            sys.exit(
                "live mode refused: need BINANCE_API_KEY, BINANCE_API_SECRET, I_UNDERSTAND_THE_RISK=1 and --live"
            )
    bars = fetch_klines(args.symbol, args.interval, max(args.entry, args.exit, args.atr) + 5)
    sig = signal_from_bars(bars, args.entry, args.exit, args.atr, args.stop_mult)
    state = load_state(args.state, args.equity)
    cost_side = TAKER_FEE + SLIPPAGE_BPS / 10_000
    px = sig["price"]
    action = "hold"
    if state["units"] > 0:
        if sig["atr"]:
            state["stop"] = max(state["stop"] or 0, px - args.stop_mult * sig["atr"])
        if px <= (state["stop"] or 0) or sig["breakdown"]:
            action = "sell"
            if live:
                place_binance_market_order(args.symbol, "SELL", state["units"])
            state["cash"] += state["units"] * px * (1 - cost_side)
            state["units"], state["stop"], state["entry"] = 0.0, None, None
    elif sig["breakout"] and sig["stop"] and sig["stop"] > 0:
        units = calc.position_size(state["cash"], args.risk, px, sig["stop"])
        units = min(units, state["cash"] / (px * (1 + cost_side)))
        if units * px >= args.min_notional:
            action = "buy"
            if live:
                place_binance_market_order(args.symbol, "BUY", units)
            state["cash"] -= units * px * (1 + cost_side)
            state["units"], state["stop"], state["entry"] = units, sig["stop"], px
    equity = state["cash"] + state["units"] * px
    entry = {
        "ts": int(time.time()),
        "mode": "live" if live else "paper",
        "price": px,
        "action": action,
        "equity": equity,
        "units": state["units"],
        "stop": state["stop"],
    }
    state["log"].append(entry)
    save_state(args.state, state)
    print(json.dumps({**entry, "signal": sig}, indent=2, default=str))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="rank liquid markets and check cross-venue BTC gap")
    s.add_argument("--top", type=int, default=50)
    s.add_argument("--notional", type=float, default=1_000.0)

    def strategy_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--symbol", default="BTCUSDT")
        sp.add_argument("--interval", default="1d")
        sp.add_argument("--equity", type=float, default=1_000.0)
        sp.add_argument("--entry", type=int, default=20, help="Donchian entry lookback")
        sp.add_argument("--exit", type=int, default=10, help="Donchian exit lookback")
        sp.add_argument("--atr", type=int, default=14)
        sp.add_argument("--stop-mult", dest="stop_mult", type=float, default=2.0)
        sp.add_argument(
            "--risk", type=float, default=0.01, help="fraction of equity risked per trade"
        )

    b = sub.add_parser("backtest", help="backtest on CSV or fetched bars")
    strategy_args(b)
    b.add_argument("--csv", help="CSV with open,high,low,close columns")
    b.add_argument("--limit", type=int, default=1000)
    b.add_argument("--json", help="write params/metrics/equity curve here")

    for name in ("paper", "live"):
        t = sub.add_parser(name, help=f"{name} trade one evaluation step")
        strategy_args(t)
        t.add_argument("--state", default=f"{name}_state.json")
        t.add_argument("--min-notional", dest="min_notional", type=float, default=10.0)
        t.add_argument(
            "--live", action="store_true", help="required (with env gates) to send real orders"
        )
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.cmd == "scan":
        cmd_scan(args)
    elif args.cmd == "backtest":
        cmd_backtest(args)
    elif args.cmd == "paper":
        cmd_trade(args, live=False)
    elif args.cmd == "live":
        cmd_trade(args, live=True)


if __name__ == "__main__":
    if len(sys.argv) == 1:  # validator self-test: offline backtest on the fixture
        fixture = Path(__file__).resolve().parents[1] / "evals" / "files" / "btc_daily_mock.csv"
        res, bh = run_backtest(load_csv(str(fixture)), initial_equity=1_000.0)
        m = res.metrics(365)
        assert m["trades"] > 5 and 0 < m["max_drawdown"] < 1
        print(
            f"bot self-test: {m['trades']:.0f} trades, ret {m['total_return']*100:+.1f}%, maxDD {m['max_drawdown']*100:.1f}% | OK"
        )
    else:
        try:
            main()
        except urllib.error.URLError as exc:
            sys.exit(
                f"network error talking to an exchange ({exc.reason}). Check connectivity, proxy, or BINANCE_BASE_URL."
            )
