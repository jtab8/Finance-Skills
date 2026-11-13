"""Unit tests for skills/crypto-trading-bot/scripts/calculate.py.

Covers the claim-audit math (required daily / per-trade return for a target
multiple), round-trip trading costs, and cross-venue arbitrage edge and
break-even gap, including edge cases (zero / negative inputs, fees larger than
the price gap).

Stdlib ``unittest`` only, so it runs with no installs:

    python3 -m unittest discover -s skills/crypto-trading-bot/evals -p "test_*.py" -v

It is also collected by pytest if that is available.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True  # keep __pycache__ out of skills/ (validator rejects it)
SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import calculate as calc  # noqa: E402

TOL = 1e-12


class TestImpliedPeriodReturn(unittest.TestCase):
    """Constant per-period return needed to turn start into end."""

    def test_doubling_in_one_period_is_100_percent(self) -> None:
        self.assertAlmostEqual(calc.implied_period_return(100, 200, 1), 1.0, delta=TOL)

    def test_matches_closed_form(self) -> None:
        # 10x over 30 days: (10)^(1/30) - 1
        expected = 10 ** (1 / 30) - 1
        self.assertAlmostEqual(calc.implied_period_return(1_000, 10_000, 30), expected, delta=TOL)

    def test_compounds_back_to_target(self) -> None:
        r = calc.implied_period_return(68, 750_000, 1_500)
        self.assertAlmostEqual(68 * (1 + r) ** 1_500, 750_000, delta=1e-6)

    def test_no_change_is_zero(self) -> None:
        self.assertEqual(calc.implied_period_return(500, 500, 12), 0.0)

    def test_loss_gives_negative_return(self) -> None:
        r = calc.implied_period_return(100, 50, 2)
        self.assertAlmostEqual(r, math.sqrt(0.5) - 1, delta=TOL)
        self.assertLess(r, 0)

    def test_rejects_zero_and_negative_inputs(self) -> None:
        bad = [
            (0, 100, 10),
            (-1, 100, 10),
            (100, 0, 10),
            (100, -5, 10),
            (100, 200, 0),
            (100, 200, -3),
        ]
        for args in bad:
            with self.subTest(args=args), self.assertRaises(ValueError):
                calc.implied_period_return(*args)


class TestAuditReturnClaim(unittest.TestCase):
    """Required daily and per-trade return for a target multiple."""

    def setUp(self) -> None:
        # $68 -> $750,000 in 30 days at 50 trades/day, 0.24% round trip
        self.claim = calc.audit_return_claim(68, 750_000, 30, 50, 0.0024)

    def test_total_multiple(self) -> None:
        self.assertAlmostEqual(self.claim["total_multiple"], 750_000 / 68, delta=1e-9)

    def test_required_daily_return(self) -> None:
        expected = (750_000 / 68) ** (1 / 30) - 1
        self.assertAlmostEqual(self.claim["implied_daily_return"], expected, delta=TOL)
        self.assertAlmostEqual(self.claim["implied_daily_return"], 0.3638, delta=1e-3)

    def test_required_per_trade_return(self) -> None:
        expected = (750_000 / 68) ** (1 / 1_500) - 1
        self.assertEqual(self.claim["trades_assumed"], 1_500.0)
        self.assertAlmostEqual(self.claim["implied_net_return_per_trade"], expected, delta=TOL)

    def test_daily_and_per_trade_are_consistent(self) -> None:
        per_trade = self.claim["implied_net_return_per_trade"]
        daily = self.claim["implied_daily_return"]
        self.assertAlmostEqual((1 + per_trade) ** 50 - 1, daily, delta=1e-9)

    def test_gross_requirement_adds_round_trip_cost(self) -> None:
        c = self.claim
        self.assertAlmostEqual(
            c["required_gross_per_trade"],
            c["implied_net_return_per_trade"] + 0.0024,
            delta=TOL,
        )
        self.assertAlmostEqual(
            c["cost_as_share_of_required_gross"],
            0.0024 / c["required_gross_per_trade"],
            delta=TOL,
        )
        self.assertTrue(0 < c["cost_as_share_of_required_gross"] < 1)

    def test_one_trade_per_day_makes_per_trade_equal_daily(self) -> None:
        c = calc.audit_return_claim(100, 1_000, 10, trades_per_day=1, round_trip_cost_frac=0.002)
        self.assertAlmostEqual(
            c["implied_net_return_per_trade"], c["implied_daily_return"], delta=TOL
        )

    def test_zero_cost(self) -> None:
        c = calc.audit_return_claim(100, 200, 10, 5, 0.0)
        self.assertEqual(c["round_trip_cost"], 0.0)
        self.assertEqual(c["cost_as_share_of_required_gross"], 0.0)
        self.assertAlmostEqual(
            c["required_gross_per_trade"], c["implied_net_return_per_trade"], delta=TOL
        )

    def test_losing_claim_has_negative_requirements(self) -> None:
        c = calc.audit_return_claim(100, 50, 10, 10, 0.0024)
        self.assertLess(c["implied_daily_return"], 0)
        self.assertLess(c["implied_net_return_per_trade"], 0)

    def test_rejects_zero_or_negative_days_and_trades(self) -> None:
        for kwargs in (
            {"days": 0},
            {"days": -5},
            {"days": 10, "trades_per_day": 0},
            {"days": 10, "trades_per_day": -1},
        ):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                calc.audit_return_claim(100, 200, **kwargs)

    def test_rejects_non_positive_end_capital(self) -> None:
        for end in (0, -100):
            with self.subTest(end=end), self.assertRaises(ValueError):
                calc.audit_return_claim(100, end, 10)

    def test_zero_start_capital_raises(self) -> None:
        # Current behaviour: the multiple is computed before validation, so this
        # surfaces as ZeroDivisionError rather than ValueError. Either is a
        # refusal; the test pins that it never returns a number.
        with self.assertRaises((ValueError, ZeroDivisionError)):
            calc.audit_return_claim(0, 100, 10)

    def test_flat_claim_with_zero_cost_raises(self) -> None:
        # end == start and cost == 0 makes the required gross 0, so the cost
        # share is 0/0. Pinned so a future fix is a deliberate change.
        with self.assertRaises(ZeroDivisionError):
            calc.audit_return_claim(100, 100, 10, 5, 0.0)


class TestRoundTripCost(unittest.TestCase):
    def test_defaults(self) -> None:
        # 2 * (0.10% + 2bp + 0.5bp) = 0.25%
        self.assertAlmostEqual(calc.round_trip_cost(), 0.0025, delta=TOL)

    def test_formula(self) -> None:
        got = calc.round_trip_cost(taker_fee=0.0004, slippage_bps=5.0, spread_bps=3.0)
        expected = 2 * (0.0004 + 5.0 / 10_000 + 1.5 / 10_000)
        self.assertAlmostEqual(got, expected, delta=TOL)

    def test_fee_only(self) -> None:
        self.assertAlmostEqual(calc.round_trip_cost(0.001, 0.0, 0.0), 0.002, delta=TOL)

    def test_all_zero_is_free(self) -> None:
        self.assertEqual(calc.round_trip_cost(0.0, 0.0, 0.0), 0.0)

    def test_monotonic_in_each_input(self) -> None:
        base = calc.round_trip_cost(0.001, 2.0, 1.0)
        self.assertGreater(calc.round_trip_cost(0.002, 2.0, 1.0), base)
        self.assertGreater(calc.round_trip_cost(0.001, 3.0, 1.0), base)
        self.assertGreater(calc.round_trip_cost(0.001, 2.0, 2.0), base)

    def test_negative_fee_is_not_validated(self) -> None:
        # Maker rebates can be negative, so the function passes them through.
        self.assertAlmostEqual(calc.round_trip_cost(-0.0001, 0.0, 0.0), -0.0002, delta=TOL)


class TestArbitrageNetEdge(unittest.TestCase):
    def test_reference_case_loses_money(self) -> None:
        # 0.15% gap vs 0.2% fees + 0.04% slippage + 0.5% transfer
        arb = calc.arbitrage_net_edge(100_000, 100_150, 0.001, 0.001, 5, 1_000, 2.0)
        self.assertAlmostEqual(arb["gross_edge"], 0.0015, delta=TOL)
        self.assertAlmostEqual(arb["fees"], 0.002, delta=TOL)
        self.assertAlmostEqual(arb["slippage"], 0.0004, delta=TOL)
        self.assertAlmostEqual(arb["transfer"], 0.005, delta=TOL)
        self.assertAlmostEqual(arb["net_edge"], 0.0015 - 0.002 - 0.0004 - 0.005, delta=TOL)
        self.assertAlmostEqual(arb["net_profit_quote"], arb["net_edge"] * 1_000, delta=1e-9)
        self.assertLess(arb["net_profit_quote"], 0)

    def test_profitable_when_gap_exceeds_costs(self) -> None:
        arb = calc.arbitrage_net_edge(100, 101, 0.001, 0.001, 0.0, 10_000, 2.0)
        self.assertAlmostEqual(arb["net_edge"], 0.01 - 0.002 - 0.0004, delta=TOL)
        self.assertGreater(arb["net_profit_quote"], 0)

    def test_fees_exceeding_edge_make_net_negative(self) -> None:
        # 0.1% gap, 0.5% fee per side
        arb = calc.arbitrage_net_edge(100, 100.1, 0.005, 0.005, 0.0, 1_000, 0.0)
        self.assertGreater(arb["gross_edge"], 0)
        self.assertGreater(arb["fees"], arb["gross_edge"])
        self.assertLess(arb["net_edge"], 0)
        self.assertLess(arb["net_profit_quote"], 0)

    def test_zero_costs_net_equals_gross(self) -> None:
        arb = calc.arbitrage_net_edge(200, 202, 0.0, 0.0, 0.0, 1_000, 0.0)
        self.assertAlmostEqual(arb["net_edge"], arb["gross_edge"], delta=TOL)
        self.assertAlmostEqual(arb["net_profit_quote"], 10.0, delta=1e-9)

    def test_inverted_prices_give_negative_gross(self) -> None:
        arb = calc.arbitrage_net_edge(101, 100, 0.0, 0.0, 0.0, 1_000, 0.0)
        self.assertLess(arb["gross_edge"], 0)
        self.assertLess(arb["net_edge"], 0)

    def test_equal_prices_lose_exactly_the_costs(self) -> None:
        arb = calc.arbitrage_net_edge(100, 100, 0.001, 0.001, 2.0, 1_000, 2.0)
        self.assertEqual(arb["gross_edge"], 0.0)
        self.assertAlmostEqual(arb["net_edge"], -(0.002 + 0.0004 + 0.002), delta=TOL)

    def test_transfer_cost_shrinks_with_notional(self) -> None:
        small = calc.arbitrage_net_edge(100, 101, transfer_cost_quote=10, notional_quote=1_000)
        large = calc.arbitrage_net_edge(100, 101, transfer_cost_quote=10, notional_quote=100_000)
        self.assertAlmostEqual(small["transfer"], 0.01, delta=TOL)
        self.assertAlmostEqual(large["transfer"], 0.0001, delta=TOL)
        self.assertGreater(large["net_edge"], small["net_edge"])

    def test_rejects_zero_and_negative_inputs(self) -> None:
        bad = [
            {"buy_price": 0, "sell_price": 100},
            {"buy_price": -1, "sell_price": 100},
            {"buy_price": 100, "sell_price": 0},
            {"buy_price": 100, "sell_price": -1},
            {"buy_price": 100, "sell_price": 101, "notional_quote": 0},
            {"buy_price": 100, "sell_price": 101, "notional_quote": -500},
        ]
        for kwargs in bad:
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                calc.arbitrage_net_edge(**kwargs)


class TestBreakevenPriceGap(unittest.TestCase):
    def test_reference_case(self) -> None:
        self.assertAlmostEqual(
            calc.breakeven_price_gap(0.001, 0.001, 5, 1_000, 2.0), 0.0074, delta=TOL
        )

    def test_defaults(self) -> None:
        self.assertAlmostEqual(calc.breakeven_price_gap(), 0.0024, delta=TOL)

    def test_all_zero_costs(self) -> None:
        self.assertEqual(calc.breakeven_price_gap(0.0, 0.0, 0.0, 1_000, 0.0), 0.0)

    def test_gap_at_breakeven_nets_to_zero(self) -> None:
        params = dict(
            buy_fee=0.00075,
            sell_fee=0.001,
            transfer_cost_quote=3.0,
            notional_quote=2_500,
            slippage_bps=1.5,
        )
        gap = calc.breakeven_price_gap(**params)
        buy = 50_000.0
        arb = calc.arbitrage_net_edge(buy, buy * (1 + gap), **params)
        self.assertAlmostEqual(arb["net_edge"], 0.0, delta=1e-12)

    def test_gap_above_breakeven_is_profitable_below_is_not(self) -> None:
        gap = calc.breakeven_price_gap()
        above = calc.arbitrage_net_edge(100, 100 * (1 + gap + 0.0001))
        below = calc.arbitrage_net_edge(100, 100 * (1 + gap - 0.0001))
        self.assertGreater(above["net_edge"], 0)
        self.assertLess(below["net_edge"], 0)

    def test_breakeven_matches_round_trip_cost_without_spread_or_transfer(self) -> None:
        self.assertAlmostEqual(
            calc.breakeven_price_gap(0.001, 0.001, 0.0, 1_000, 2.0),
            calc.round_trip_cost(0.001, 2.0, 0.0),
            delta=TOL,
        )

    def test_zero_notional_raises(self) -> None:
        # No input validation here (unlike arbitrage_net_edge); pinned so a
        # future ValueError is a deliberate change.
        with self.assertRaises((ValueError, ZeroDivisionError)):
            calc.breakeven_price_gap(notional_quote=0)


if __name__ == "__main__":
    unittest.main()
