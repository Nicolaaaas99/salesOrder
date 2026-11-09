"""Live-DB smoke test for targets on any node (2026-11 rethink, Phase C).

    1. Scenario A: <DC> weekly total = forecast x 1.10 (FIXED_QTY) -> approve
    2. Scenario B: the DC's biggest customer = 2 x its forecast, inside A -> preview shows
       the rest of the DC rebalancing -> approve
    3. Checks from _tb_SOScenarioTarget: the DC still totals A's target, the
       customer totals B's target
    4. Override one of the customer's products -> the customer total still holds
       (its other products rebalance); clear it -> back to the scenario value
    5. Cancel B then A -> every target row is gone

Everything is created as `smoke_test` and cleaned up. Usage:
    .venv\\Scripts\\python.exe scripts\\run_target_nesting_smoke_test.py --dc Makhado --week 2026-11-16
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from database import SessionLocal  # noqa: E402
from services.forecasting import weeks  # noqa: E402
from services.forecasting.scenarios import ScenarioScope, ScenarioService  # noqa: E402

ok_all = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}  {detail}")


def target_total(db, wk: date, where: str, params: dict) -> float:
    return float(db.execute(text(f"""
        SELECT ISNULL(SUM(fTargetQty), 0) FROM [SOAssistant].dbo._tb_SOScenarioTarget
         WHERE dWeekStart = :wk AND {where}"""), {"wk": wk, **params}).scalar())


def forecast_total(db, wk: date, where: str, params: dict) -> float:
    return float(db.execute(text(f"""
        SELECT ISNULL(SUM(fPredictedQty), 0) FROM [SOAssistant].dbo._tb_SOForecast
         WHERE bIsActive = 1 AND dWeekStart = :wk AND {where}"""), {"wk": wk, **params}).scalar())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dc", default="Makhado")
    ap.add_argument("--week", default=None, help="Any date in the week (default: next week)")
    args = ap.parse_args()
    wk = weeks.week_start(date.fromisoformat(args.week)) if args.week else         weeks.week_start(date.today()) + timedelta(weeks=1)
    db = SessionLocal()
    svc = ScenarioService(db, "smoke_test")
    print(f"\nTarget nesting smoke test -- DC {args.dc}, week {wk}\n")

    dc_fc = forecast_total(db, wk, "cDistCentre = :dc", {"dc": args.dc})
    cust = db.execute(text("""
        SELECT TOP 1 cCustomerAccount FROM [SOAssistant].dbo._tb_SOForecast
         WHERE bIsActive = 1 AND dWeekStart = :wk AND cDistCentre = :dc
         GROUP BY cCustomerAccount ORDER BY SUM(fPredictedQty) DESC"""), {"wk": wk, "dc": args.dc}).scalar()
    cust_fc = forecast_total(db, wk, "cCustomerAccount = :c", {"c": cust})
    print(f"  DC forecast {dc_fc:,.1f}   biggest customer {cust} forecast {cust_fc:,.1f}\n")
    if dc_fc <= 0:
        print("No forecast for that DC/week -- run the forecast first.")
        return 1

    dc_goal = round(dc_fc * 1.10, 2)
    cust_goal = round(cust_fc * 2.0, 2)
    created = []
    try:
        t0 = time.time()
        a = svc.create(name=f"SMOKE C: {args.dc} = {dc_goal}", description=None,
                       scope=ScenarioScope(dist_centre=(args.dc,)), lever_type="FIXED_QTY",
                       lever_value=dc_goal, period_from=wk, period_to=wk, anchor_level="dist_centre")
        created.append(a.id)
        _, ra = svc.preview(a.id)
        reviews = {(f.customer, f.sku): ("ACCEPT", "smoke") for f in ra.flagged_leaves}
        _, ra, written = svc.approve(a.id, reviews)
        print(f"  A approved: {written} rows, {len(reviews)} flags accepted ({time.time() - t0:.1f}s)")
        check("DC total = A's target", abs(target_total(db, wk, "cDistCentre = :dc", {"dc": args.dc}) - dc_goal) < 0.05,
              f"{target_total(db, wk, 'cDistCentre = :dc', {'dc': args.dc}):,.2f} vs {dc_goal:,.2f}")

        t0 = time.time()
        b = svc.create(name=f"SMOKE C: {cust} = {cust_goal}", description=None,
                       scope=ScenarioScope(customer=(cust,)), lever_type="FIXED_QTY",
                       lever_value=cust_goal, period_from=wk, period_to=wk, anchor_level="customer")
        created.append(b.id)
        _, rb = svc.preview(b.id)
        print(f"  B preview: in scope {rb.total_before_qty:,.1f} -> {rb.total_effective_target_qty:,.1f}; "
              f"{len(rb.rebalanced)} other customer-products rebalanced by {rb.rebalanced_delta_qty:,.1f}; "
              f"conflicts {len(rb.conflicts)}")
        check("preview rebalances the rest of the DC by the opposite amount",
              abs((rb.total_effective_target_qty - rb.total_before_qty) + rb.rebalanced_delta_qty) < 0.05)
        reviews = {(f.customer, f.sku): ("ACCEPT", "smoke") for f in rb.flagged_leaves}
        _, rb, written = svc.approve(b.id, reviews)
        print(f"  B approved: {written} rows written ({time.time() - t0:.1f}s)")
        dc_now = target_total(db, wk, "cDistCentre = :dc", {"dc": args.dc})
        cust_now = target_total(db, wk, "cCustomerAccount = :c", {"c": cust})
        check("DC still totals A's target", abs(dc_now - dc_goal) < 0.05, f"{dc_now:,.2f} vs {dc_goal:,.2f}")
        check("customer totals B's target", abs(cust_now - cust_goal) < 0.05, f"{cust_now:,.2f} vs {cust_goal:,.2f}")

        row = db.execute(text("""
            SELECT TOP 1 idScenarioTarget, fTargetQty FROM [SOAssistant].dbo._tb_SOScenarioTarget
             WHERE dWeekStart = :wk AND cCustomerAccount = :c ORDER BY fTargetQty DESC"""),
            {"wk": wk, "c": cust}).mappings().first()
        new_qty = round(float(row["fTargetQty"]) + 10, 4)
        svc.override_target(int(row["idScenarioTarget"]), new_qty, "smoke override")
        cust_now = target_total(db, wk, "cCustomerAccount = :c", {"c": cust})
        dc_now = target_total(db, wk, "cDistCentre = :dc", {"dc": args.dc})
        check("override: customer total still holds", abs(cust_now - cust_goal) < 0.05, f"{cust_now:,.2f}")
        check("override: DC total still holds", abs(dc_now - dc_goal) < 0.05, f"{dc_now:,.2f}")
        after = svc.override_target(int(row["idScenarioTarget"]), None, "smoke clear")
        check("clear: back to the scenario value", abs(float(after["fTargetQty"]) - float(row["fTargetQty"])) < 0.01,
              f"{float(after['fTargetQty']):,.4f} vs {float(row['fTargetQty']):,.4f}")
    finally:
        for sid in reversed(created):
            try:
                st = svc.get(sid).status
                if st not in ("CANCELLED", "COMPLETED"):
                    svc.cancel(sid, "smoke test cleanup")
            except Exception as e:  # noqa: BLE001
                print("  cleanup error", sid, e)
        left = target_total(db, wk, "1 = 1", {})
        check("cleanup: no target rows left for the week", left == 0, f"sum left {left}")

    print(f"\nRESULT: {'PASS' if ok_all else 'FAIL'}\n")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
