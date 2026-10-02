"""IFRS 15 / ASC 606: relative-SSP allocation, over-time recognition, financing component,
and a period-end revenue schedule built from a contracts file.

Self-test:  python3 scripts/calculate.py
Schedule:   python3 scripts/calculate.py contracts.csv --as-of 2026-03-31 [--period-start 2026-01-01] [--json]

Contracts file columns (one row per performance obligation):
  contract, performance_obligation, ssp, timing (point_in_time | over_time)   required
  stated_price or transaction_price                                         one required
  start, months             over-time obligations (straight-line over the term)
  satisfied_on              point-in-time obligations
  billed_to_date            optional; summed per contract for the contract asset / liability
A missing or non-numeric input is reported OPEN and never estimated. Exit code 2 when any
item is OPEN, 1 when a tie check is BLOCKED, 0 when clean.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import json
import sys
from datetime import date, timedelta

TIE_TOLERANCE = 0.005


def allocate_transaction_price(total_price: float, ssps: dict[str, float]) -> dict[str, float]:
    """Allocate to performance obligations pro-rata to standalone selling prices."""
    total_ssp = sum(ssps.values())
    return {po: total_price * ssp / total_ssp for po, ssp in ssps.items()}


def over_time_revenue(allocated: float, months_elapsed: float, total_months: float) -> float:
    return allocated * min(months_elapsed / total_months, 1.0)


def significant_financing_split(deferred_price: float, rate: float, years: float) -> dict:
    """Split a deferred receipt into cash selling price (revenue) and interest."""
    cash_price = deferred_price / (1 + rate) ** years
    return {"revenue": cash_price, "interest_income": deferred_price - cash_price}


def contract_liability(billed: float, recognized: float) -> float:
    return max(billed - recognized, 0.0)


def contract_position(billed: float, recognized: float) -> dict:
    """Net contract position per contract: billed ahead of revenue is a contract liability,
    revenue ahead of billing is a contract asset (or receivable if the right is unconditional)."""
    net = billed - recognized
    return {"contract_liability": max(net, 0.0), "contract_asset": max(-net, 0.0)}


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def months_elapsed(start: str, as_of: str) -> float:
    """Calendar months of service delivered from start (ISO date) to the end of as_of (ISO date).
    Whole months plus the fraction of the current month by days: 1 Jan to 31 Mar = 3.0."""
    s, end = date.fromisoformat(start), date.fromisoformat(as_of) + timedelta(days=1)
    if end <= s:
        return 0.0
    n = 0
    while _add_months(s, n + 1) <= end:
        n += 1
    lo, hi = _add_months(s, n), _add_months(s, n + 1)
    return n + (end - lo).days / (hi - lo).days


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _iso(v) -> str | None:
    try:
        return date.fromisoformat(str(v).strip()).isoformat()
    except (TypeError, ValueError):
        return None


def _recognized(po: dict, allocated: float, as_of: str) -> float:
    if po["timing"] == "point_in_time":
        return allocated if po["satisfied_on"] <= as_of else 0.0
    return over_time_revenue(allocated, months_elapsed(po["start"], as_of), po["months"])


def revenue_schedule(rows: list[dict], as_of: str, period_start: str = "") -> dict:
    """Period-end schedule per contract: relative-SSP allocation, revenue to date, revenue for
    the period (when period_start is given), billed, contract asset / liability, draft JE.
    Rows use the contracts-file columns. Missing inputs are returned as OPEN, never estimated."""
    if isinstance(rows, str):
        rows = json.loads(rows)
    as_of = date.fromisoformat(as_of).isoformat()
    prior = (date.fromisoformat(period_start) - timedelta(days=1)).isoformat() \
        if period_start else ""
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        grouped.setdefault(str(r.get("contract", "")).strip(), []).append(r)

    out = {"as_of": as_of, "period_start": period_start or None, "contracts": [],
           "open": [], "blocked": []}
    for cid, lines in grouped.items():
        c_open: list[str] = []
        pos: list[dict] = []
        for r in lines:
            name = str(r.get("performance_obligation", "")).strip()
            ref = f"{cid} / {name}"
            ssp = _num(r.get("ssp"))
            if ssp is None or ssp <= 0:
                c_open.append(f"{ref}: SSP '{r.get('ssp')}' is not an established amount; "
                              "provide the SSP (or the documented estimate) before allocating")
            timing = str(r.get("timing", "")).strip().lower()
            po = {"performance_obligation": name, "ssp": ssp, "timing": timing,
                  "stated_price": _num(r.get("stated_price"))}
            if timing == "over_time":
                po["start"], po["months"] = _iso(r.get("start")), _num(r.get("months"))
                if not po["start"] or not po["months"] or po["months"] <= 0:
                    c_open.append(f"{ref}: over-time obligation needs start and months")
            elif timing == "point_in_time":
                po["satisfied_on"] = _iso(r.get("satisfied_on"))
                if not po["satisfied_on"]:
                    c_open.append(f"{ref}: point-in-time obligation needs satisfied_on")
            else:
                c_open.append(f"{ref}: timing '{r.get('timing')}' must be point_in_time or over_time")
            pos.append(po)

        tps = {_num(r.get("transaction_price")) for r in lines
               if str(r.get("transaction_price", "") or "").strip()}
        if len(tps) > 1 or None in tps:
            c_open.append(f"{cid}: transaction_price differs across rows {sorted(map(str, tps))}; "
                          "confirm the contract price")
            tp = None
        elif tps:
            tp = tps.pop()
        elif all(p["stated_price"] is not None for p in pos):
            tp = sum(p["stated_price"] for p in pos)
        else:
            c_open.append(f"{cid}: no transaction_price and stated_price incomplete")
            tp = None

        billed_vals = [str(r.get("billed_to_date", "") or "").strip() for r in lines]
        billed = None
        if any(billed_vals):
            nums = [_num(v) if v else 0.0 for v in billed_vals]
            if None in nums:
                c_open.append(f"{cid}: billed_to_date is not numeric")
            else:
                billed = sum(nums)

        contract = {"contract": cid, "transaction_price": tp, "billed_to_date": billed,
                    "obligations": pos, "open": c_open}
        out["contracts"].append(contract)
        out["open"] += c_open
        if c_open:
            continue

        alloc = allocate_transaction_price(tp, {i: p["ssp"] for i, p in enumerate(pos)})
        for i, p in enumerate(pos):
            p["allocated"] = alloc[i]
            p["recognized_to_date"] = _recognized(p, alloc[i], as_of)
            if prior:
                p["recognized_period"] = p["recognized_to_date"] - _recognized(p, alloc[i], prior)
        diff = sum(alloc.values()) - tp
        if abs(diff) > TIE_TOLERANCE:
            msg = f"{cid}: allocation {sum(alloc.values()):,.2f} != transaction price {tp:,.2f}"
            out["blocked"].append(msg)
        rec = sum(p["recognized_to_date"] for p in pos)
        contract["recognized_to_date"] = rec
        contract["unrecognized"] = tp - rec
        if billed is not None:
            contract.update(contract_position(billed, rec))
        if prior:
            contract["recognized_period"] = sum(p["recognized_period"] for p in pos)
            contract["draft_je"] = _draft_je(cid, pos, period_start, as_of)
    return out


def _draft_je(cid: str, pos: list[dict], period_start: str, as_of: str) -> list[dict]:
    """Revenue entry for the period, rounded to cents at the end; debit balances the credits.
    Billing is assumed booked separately (Dr receivable, Cr contract liability)."""
    cr = [{"account": f"Revenue - {p['performance_obligation']}", "dr": 0.0,
           "cr": round(p["recognized_period"], 2)} for p in pos if round(p["recognized_period"], 2)]
    total = round(sum(line["cr"] for line in cr), 2)
    if not total:
        return []
    dr = {"account": f"Contract liability / contract asset - {cid} (net position)",
          "dr": total, "cr": 0.0}
    return [{"memo": f"DRAFT - not posted. {cid} revenue {period_start} to {as_of}"}, dr] + cr


def _fmt(v: float | None) -> str:
    return "OPEN" if v is None else f"{v:,.2f}"


def _report(s: dict) -> str:
    lines = [f"Revenue schedule as of {s['as_of']}"
             + (f", period from {s['period_start']}" if s["period_start"] else "")]
    for c in s["contracts"]:
        lines += ["", f"Contract {c['contract']}  transaction price {_fmt(c['transaction_price'])}"
                  f"  billed {_fmt(c['billed_to_date'])}"]
        if c["open"]:
            lines += [f"  OPEN  {m}" for m in c["open"]]
            lines.append("  No allocation computed for this contract.")
            continue
        hdr = f"  {'Performance obligation':<38}{'SSP':>14}{'Allocated':>14}{'To date':>14}"
        hdr += f"{'Period':>14}" if s["period_start"] else ""
        lines.append(hdr)
        for p in c["obligations"]:
            row = (f"  {p['performance_obligation'][:37]:<38}{p['ssp']:>14,.2f}"
                   f"{p['allocated']:>14,.2f}{p['recognized_to_date']:>14,.2f}")
            row += f"{p['recognized_period']:>14,.2f}" if s["period_start"] else ""
            lines.append(row)
        tot = (f"  {'Total':<38}{sum(p['ssp'] for p in c['obligations']):>14,.2f}"
               f"{c['transaction_price']:>14,.2f}{c['recognized_to_date']:>14,.2f}")
        tot += f"{c['recognized_period']:>14,.2f}" if s["period_start"] else ""
        lines += [tot, f"  Unrecognized (remaining performance obligation): {_fmt(c['unrecognized'])}"]
        if "contract_liability" in c:
            lines.append(f"  Contract liability {_fmt(c['contract_liability'])}"
                         f"  contract asset {_fmt(c['contract_asset'])}")
        else:
            lines.append("  Contract asset / liability: OPEN (no billed_to_date column)")
        if c.get("draft_je"):
            lines.append(f"  {c['draft_je'][0]['memo']}")
            for j in c["draft_je"][1:]:
                dr, cr = (f"{v:,.2f}" if v else "" for v in (j["dr"], j["cr"]))
                lines.append(f"    {j['account'][:60]:<62}{dr:>14}{cr:>14}")
    for b in s["blocked"]:
        lines.append(f"\nBLOCKED  {b}")
    if s["open"]:
        lines.append(f"\n{len(s['open'])} OPEN item(s); affected contracts have no figures.")
    return "\n".join(lines)


def _cli(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="IFRS 15 / ASC 606 period-end revenue schedule")
    ap.add_argument("contracts", help="CSV, one row per performance obligation")
    ap.add_argument("--as-of", required=True, help="period end, YYYY-MM-DD")
    ap.add_argument("--period-start", default="", help="YYYY-MM-DD; adds period revenue and draft JE")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    with open(a.contracts, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    s = revenue_schedule(rows, a.as_of, a.period_start)
    print(json.dumps(s, indent=2) if a.json else _report(s))
    return 1 if s["blocked"] else 2 if s["open"] else 0


def _selftest() -> None:
    alloc = allocate_transaction_price(
        100_000, {"licence": 75_000, "support": 30_000, "implementation": 20_000})
    assert abs(alloc["licence"] - 60_000) < 1e-9
    assert abs(alloc["support"] - 24_000) < 1e-9
    assert abs(alloc["implementation"] - 16_000) < 1e-9
    assert abs(sum(alloc.values()) - 100_000) < 1e-9
    q1 = alloc["licence"] + over_time_revenue(alloc["support"], 3, 12) \
         + over_time_revenue(alloc["implementation"], 3, 12)
    assert abs(q1 - 70_000) < 1e-9
    fin = significant_financing_split(121_000, 0.10, 2)
    assert abs(fin["revenue"] - 100_000) < 1e-6 and abs(fin["interest_income"] - 21_000) < 1e-6
    assert contract_liability(100_000, q1) == 30_000
    assert months_elapsed("2026-01-01", "2026-03-31") == 3.0
    rows = [
        {"contract": "C-101", "performance_obligation": "licence", "stated_price": "60000",
         "ssp": "75000", "timing": "point_in_time", "satisfied_on": "2026-01-01",
         "billed_to_date": "100000"},
        {"contract": "C-101", "performance_obligation": "support", "stated_price": "30000",
         "ssp": "30000", "timing": "over_time", "start": "2026-01-01", "months": "12"},
        {"contract": "C-101", "performance_obligation": "implementation", "stated_price": "10000",
         "ssp": "20000", "timing": "over_time", "start": "2026-01-01", "months": "12"},
    ]
    s = revenue_schedule(rows, "2026-03-31", "2026-01-01")
    c = s["contracts"][0]
    assert not s["open"] and not s["blocked"]
    assert abs(c["recognized_to_date"] - 70_000) < 1e-9
    assert abs(c["contract_liability"] - 30_000) < 1e-9 and c["contract_asset"] == 0
    assert c["draft_je"][1]["dr"] == 70_000.0
    rows[2]["ssp"] = "NOT ESTABLISHED"
    s = revenue_schedule(rows, "2026-03-31")
    assert len(s["open"]) == 1 and "recognized_to_date" not in s["contracts"][0]
    print(f"allocation {dict((k, round(v)) for k, v in alloc.items())} | Q1 rev {q1:,.0f} | OK")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(_cli(sys.argv[1:]))
    _selftest()
