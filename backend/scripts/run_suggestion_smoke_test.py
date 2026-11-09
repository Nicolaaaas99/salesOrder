"""Phase 4 smoke test -- target-aware suggestions + decision capture, live DB.

Usage:
    cd c:\\Dev\\salesOrder\\backend
    .\\.venv\\Scripts\\python.exe scripts\\run_suggestion_smoke_test.py
    .\\.venv\\Scripts\\python.exe scripts\\run_suggestion_smoke_test.py --customer SPA227 --sku 323 --lever 20

What it does (NO Evolution sales order is created):
    1. Picks a customer x SKU with an active forecast for the delivery week
       (or uses --customer / --sku)
    2. Creates + previews + approves a +lever% scenario scoped to just that
       leaf for that week (flags auto-ACCEPTed)
    3. Calls GET /api/history/suggestions?record=true through the real app
       and checks the leaf comes back as source=TARGET with a suggestion_id
    4. Calls GET /api/history/week-targets for the progress strip
    5. Records decisions against a fake order 'SMOKE-hhmmss': the targeted
       stock ordered exactly as suggested (ACCEPTED), one other suggestion
       at a different qty (MODIFIED), the rest not ordered (REJECTED), plus
       one unsuggested stock (MANUAL_ADD)
    6. Cleans up (unless --keep): deletes the smoke suggestion rows and
       cancels the scenario (which removes its targets)
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import auth  # noqa: E402
import main  # noqa: E402
from database import SessionLocal  # noqa: E402
from services.forecasting import weeks  # noqa: E402
from services.forecasting.scenarios import ScenarioError, ScenarioScope, ScenarioService  # noqa: E402
from services.forecasting.suggestions import record_order_decisions  # noqa: E402

USER = "smoke_test"


def next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def main_() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--customer")
    p.add_argument("--sku")
    p.add_argument("--as-of", help="Delivery date YYYY-MM-DD (default: next weekday)")
    p.add_argument("--lever", type=float, default=20.0, help="PERCENT lever (default +20)")
    p.add_argument("--keep", action="store_true", help="Keep scenario + suggestion rows")
    args = p.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else next_weekday(date.today())
    week = weeks.week_start(as_of)
    started = datetime.now() - timedelta(seconds=1)
    db = SessionLocal()
    svc = ScenarioService(db, USER)
    scenario_id = None
    ok = True
    try:
        if args.customer and args.sku:
            cust, sku = args.customer, args.sku
        else:
            row = db.execute(text("""
                SELECT TOP 1 cCustomerAccount, cStockCode FROM [SOAssistant].dbo._tb_SOForecast
                 WHERE bIsActive = 1 AND dWeekStart = :w AND fPredictedQty > 0
                 ORDER BY idForecastRun DESC, fPredictedQty DESC
            """), {"w": week}).mappings().first()
            if not row:
                print(f"No active forecast for week {week}. Run run_forecast_smoke_test.py first.")
                return 2
            cust, sku = row["cCustomerAccount"], row["cStockCode"]
        print(f"Leaf {cust} x {sku}, delivery {as_of} (week {week}), lever +{args.lever:g}%")

        s = svc.create(name=f"SMOKE P4: {cust} x {sku} +{args.lever:g}%", description="Phase 4 smoke test",
                       scope=ScenarioScope(customer=(cust,), sku=(sku,)), lever_type="PERCENT",
                       lever_value=args.lever, period_from=week, period_to=week)
        scenario_id = s.id
        _, r = svc.preview(s.id)
        if not r.targets:
            print("Scenario matched no forecast leaf-weeks:", r.warnings)
            return 1
        reviews = {(f.customer, f.sku): ("ACCEPT", "smoke test") for f in r.flagged_leaves}
        _, r, written = svc.approve(s.id, reviews)
        t = r.targets[0]
        print(f"Approved scenario {s.id}: forecast {t.leaf.forecast_qty:g} -> target {t.target_qty:g} "
              f"({written} row)")

        main.app.dependency_overrides[auth.get_current_user] = lambda: USER
        client = TestClient(main.app)
        resp = client.get("/api/history/suggestions",
                          params={"customer_id": cust, "as_of": as_of.isoformat(), "record": "true"})
        resp.raise_for_status()
        sugs = resp.json()
        mine = [x for x in sugs if x["stock_code"].strip().upper() == sku.strip().upper()]
        print(f"\nSuggestions: {len(sugs)} rows, {sum(1 for x in sugs if x['source'] == 'TARGET')} TARGET")
        if mine:
            m = mine[0]
            print(f"  {sku}: source={m['source']} target={m['target_qty']} forecast={m['forecast_qty']} "
                  f"covered={m['on_order_qty'] + m['current_week_invoiced_qty']:g} "
                  f"suggested={m['suggested_quantity']:g} id={m['suggestion_id']}")
            ok &= m["source"] == "TARGET" and m["suggestion_id"] is not None
        else:
            print(f"  {sku}: not suggested (target already covered this week?)")
        ok &= all(x["suggestion_id"] is not None for x in sugs)

        wt = client.get("/api/history/week-targets",
                        params={"customer_id": cust, "as_of": as_of.isoformat()}).json()
        print("Week targets:", [(w["stock_code"], w["target_qty"], w["covered_qty"]) for w in wt])
        ok &= any(w["stock_code"].strip().upper() == sku.strip().upper() for w in wt)

        order_no = f"SMOKE-{datetime.now():%H%M%S}"
        lines = []
        if mine:
            lines.append((mine[0]["stock_code"], mine[0]["suggested_quantity"]))
        other = next((x for x in sugs if x not in mine), None)
        if other:
            lines.append((other["stock_code"], other["suggested_quantity"] + other["pack_size"]))
        lines.append(("SMOKE-MANUAL", 1.0))
        counts = record_order_decisions(
            db, suggestion_ids=[x["suggestion_id"] for x in sugs if x["suggestion_id"]],
            customer=cust, username=USER, order_no=order_no, delivery_date=as_of, lines=lines)
        print(f"\nDecisions recorded for {order_no}: {counts}")
        rows = db.execute(text("""
            SELECT cDecision, COUNT(*) n FROM [SOAssistant].dbo._tb_SOOrderSuggestion
             WHERE cUsername = :u AND dtPresentedAt >= :t GROUP BY cDecision
        """), {"u": USER, "t": started}).all()
        print("  in DB:", {d or "UNDECIDED": n for d, n in rows})
        ok &= counts is not None and counts.get("MANUAL_ADD") == 1
        if mine:
            ok &= counts.get("ACCEPTED", 0) >= 1
    except ScenarioError as e:
        print(f"Scenario rule violation ({e.status_code}): {e.detail}")
        ok = False
    finally:
        main.app.dependency_overrides.clear()
        if not args.keep:
            n = db.execute(text("""
                DELETE FROM [SOAssistant].dbo._tb_SOOrderSuggestion
                 WHERE cUsername = :u AND dtPresentedAt >= :t
            """), {"u": USER, "t": started}).rowcount
            db.commit()
            if scenario_id:
                svc.cancel(scenario_id, "smoke test cleanup")
            print(f"\nCleanup: deleted {n} suggestion rows, cancelled scenario {scenario_id}")
        db.close()

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main_())
