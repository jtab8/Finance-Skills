"""Unit tests for scripts/calculate.py. Run: python3 -m unittest discover -s evals -p 'test_*.py'"""
from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "calculate.py"
spec = importlib.util.spec_from_file_location("revrec_calc", SCRIPT)
calc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calc)


def load(name: str) -> list[dict]:
    with open(HERE / "files" / name, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


class MonthsElapsed(unittest.TestCase):
    def test_whole_quarter(self) -> None:
        self.assertEqual(calc.months_elapsed("2026-01-01", "2026-03-31"), 3.0)

    def test_mid_month_start(self) -> None:
        self.assertAlmostEqual(calc.months_elapsed("2026-02-15", "2026-03-31"), 1 + 17 / 31)

    def test_before_start(self) -> None:
        self.assertEqual(calc.months_elapsed("2026-04-01", "2026-03-31"), 0.0)

    def test_month_end_start_clamps(self) -> None:
        self.assertEqual(calc.months_elapsed("2026-01-31", "2026-02-27"), 1.0)


class Schedule(unittest.TestCase):
    def setUp(self) -> None:
        self.s = calc.revenue_schedule(load("contracts_schedule.csv"), "2026-03-31", "2026-01-01")
        self.c = {c["contract"]: c for c in self.s["contracts"]}

    def test_clean(self) -> None:
        self.assertEqual(self.s["open"], [])
        self.assertEqual(self.s["blocked"], [])

    def test_allocation_ties_to_price(self) -> None:
        for c in self.c.values():
            self.assertAlmostEqual(sum(p["allocated"] for p in c["obligations"]),
                                   c["transaction_price"], places=9)

    def test_c101_quarter(self) -> None:
        c = self.c["C-101"]
        self.assertAlmostEqual(c["recognized_to_date"], 70_000)
        self.assertAlmostEqual(c["contract_liability"], 30_000)
        self.assertEqual(c["contract_asset"], 0)

    def test_c102_partial_month_and_future_delivery(self) -> None:
        c = self.c["C-102"]
        sub, onboarding = c["obligations"]
        self.assertAlmostEqual(sub["allocated"], 54_000 * 48_000 / 56_000)
        self.assertAlmostEqual(sub["recognized_to_date"], sub["allocated"] * (1 + 17 / 31) / 24)
        self.assertEqual(onboarding["recognized_to_date"], 0.0)
        self.assertAlmostEqual(c["contract_liability"], 12_000 - sub["recognized_to_date"])

    def test_period_excludes_prior_revenue(self) -> None:
        s = calc.revenue_schedule(load("contracts_schedule.csv"), "2026-06-30", "2026-04-01")
        c101 = {c["contract"]: c for c in s["contracts"]}["C-101"]
        self.assertAlmostEqual(c101["recognized_to_date"], 60_000 + 40_000 * 6 / 12)
        self.assertAlmostEqual(c101["recognized_period"], 40_000 * 3 / 12)

    def test_je_balances_and_is_draft(self) -> None:
        for c in self.c.values():
            je = c["draft_je"]
            self.assertTrue(je[0]["memo"].startswith("DRAFT - not posted"))
            self.assertAlmostEqual(sum(j["dr"] for j in je[1:]), sum(j["cr"] for j in je[1:]))

    def test_contract_asset_when_revenue_ahead_of_billing(self) -> None:
        rows = load("contracts_schedule.csv")
        rows[0]["billed_to_date"] = "50000"
        c = calc.revenue_schedule(rows, "2026-03-31")["contracts"][0]
        self.assertAlmostEqual(c["contract_asset"], 20_000)
        self.assertEqual(c["contract_liability"], 0)


class OpenItems(unittest.TestCase):
    def test_missing_ssp_is_open_not_estimated(self) -> None:
        s = calc.revenue_schedule(load("contract_missing_ssp.csv"), "2026-03-31")
        self.assertTrue(any("Custom training: SSP" in m for m in s["open"]))
        self.assertNotIn("recognized_to_date", s["contracts"][0])

    def test_conflicting_transaction_price(self) -> None:
        rows = load("contracts_schedule.csv")[:3]
        for r, tp in zip(rows, ("100000", "100000", "95000")):
            r["transaction_price"] = tp
        s = calc.revenue_schedule(rows, "2026-03-31")
        self.assertTrue(any("transaction_price differs" in m for m in s["open"]))

    def test_no_billing_column_leaves_position_open(self) -> None:
        rows = [{k: v for k, v in r.items() if k != "billed_to_date"}
                for r in load("contracts_schedule.csv")]
        c = calc.revenue_schedule(rows, "2026-03-31")["contracts"][0]
        self.assertIsNone(c["billed_to_date"])
        self.assertNotIn("contract_liability", c)


class Cli(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

    def test_exit_codes(self) -> None:
        ok = self.run_cli(str(HERE / "files" / "contracts_schedule.csv"), "--as-of", "2026-03-31")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = self.run_cli(str(HERE / "files" / "contract_missing_ssp.csv"), "--as-of", "2026-03-31")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("OPEN", bad.stdout)

    def test_selftest_without_args(self) -> None:
        r = self.run_cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
