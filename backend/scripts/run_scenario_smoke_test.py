"""Phase 3 smoke test -- run a scenario through the real service against the live DB.

Usage:
    cd c:\\Dev\\salesOrder\\backend
    .\\.venv\\Scripts\\python.exe scripts\\run_scenario_smoke_test.py --dc WC --category Milk
    .\\.venv\\Scripts\\python.exe scripts\\run_scenario_smoke_test.py --route "RT 101" --lever 5 --approve

Prerequisite: forecasts exist for the coming weeks (run
run_forecast_smoke_test.py first) and the SOAssistant DB is at
compatibility level >= 130 (OPENJSON).

What it does:
    1. Creates a scenario named 'SMOKE: ...' (DRAFT)
    2. Previews it (DRAFT -> PREVIEW): loads forecasts in scope, 104 weeks
       of PostST history, SKU launch dates and pack sizes, runs the flags
    3. Prints totals, warnings and a sample of targets + flagged leaves
    4. With --approve: ACCEPTs every flagged leaf and approves, which
       MERGEs rows into _tb_SOScenarioTarget, then reads them back
    5. Cancels the scenario (unless --keep). Cancelling an approved
       scenario recomputes/deletes the targets it created, which also
       exercises the cancel path.

What it writes:
    - 1 _tb_SOScenario row (left as CANCELLED, or ACTIVE with --keep)
    - _tb_SOForecastAudit rows for CREATE / APPROVE / CANCEL
    - With --approve: _tb_SOScenarioTarget rows (removed again on cancel
      unless another ACTIVE scenario or an override still covers them)
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402

from database import SessionLocal  # noqa: E402
from services.forecasting import weeks  # noqa: E402
from services.forecasting.scenarios import ScenarioError, ScenarioScope, ScenarioService  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dc", action="append", default=[], help="Distribution centre (repeatable)")
    p.add_argument("--route", action="append", default=[])
    p.add_argument("--category", action="append", default=[])
    p.add_argument("--sku", action="append", default=[])
    p.add_argument("--customer", action="append", default=[])
    p.add_argument("--lever-type", default="PERCENT", choices=["PERCENT", "ABSOLUTE_DELTA", "FIXED_QTY"])
    p.add_argument("--lever", type=float, default=5.0, help="Lever value (PERCENT: 5 = +5%%)")
    p.add_argument("--weeks", type=int, default=4, help="Period length in weeks, starting next Monday")
    p.add_argument("--as-of", default=None, help="YYYY-MM-DD (default today)")
    p.add_argument("--approve", action="store_true", help="Also approve (writes targets)")
    p.add_argument("--keep", action="store_true", help="Do not cancel at the end")
    p.add_argument("--user", default="smoke_test", help="Username recorded in the audit log")
    args = p.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    start = weeks.week_start(as_of) + timedelta(weeks=1)
    end = start + timedelta(weeks=args.weeks - 1)
    scope = ScenarioScope(
        dist_centre=tuple(args.dc), route=tuple(args.route), category=tuple(args.category),
        sku=tuple(args.sku), customer=tuple(args.customer),
    )
    if not scope.dim_filters():
        print("Refusing to run an unscoped (company-wide) smoke test -- pass at least one of "
              "--dc / --route / --category / --sku / --customer.")
        return 2

    db = SessionLocal()
    svc = ScenarioService(db, args.user)
    scenario_id = None
    try:
        s = svc.create(
            name=f"SMOKE: {args.lever_type} {args.lever:g} {scope.to_json()}"[:100],
            description="Created by scripts/run_scenario_smoke_test.py",
            scope=scope, lever_type=args.lever_type, lever_value=args.lever,
            period_from=start, period_to=end,
        )
        scenario_id = s.id
        print(f"Created scenario {s.id} ({s.status}) period {start} .. {end}, scope {scope.to_json()}")

        t0 = time.perf_counter()
        s, r = svc.preview(s.id, as_of)
        print(f"\nPREVIEW ({time.perf_counter() - t0:.1f}s) -> status {s.status}")
        print(f"  weeks               {', '.join(w.isoformat() for w in r.weeks)}")
        print(f"  leaves / leaf-weeks {r.leaf_count} / {len(r.targets)}")
        print(f"  forecast total      {r.total_forecast_qty:,.2f}")
        print(f"  target total        {r.total_target_qty:,.2f}  (effective {r.total_effective_target_qty:,.2f})")
        print(f"  flagged leaves      {len(r.flagged_leaves)}   locked skipped {r.locked_skipped}")
        for w in r.warnings:
            print(f"  WARNING: {w}")
        print("\n  sample targets:")
        for t in r.targets[:10]:
            print(f"    {t.leaf.customer:<14} {t.leaf.sku:<16} {t.leaf.week}  "
                  f"fc {t.leaf.forecast_qty:>9.2f} -> tgt {t.target_qty:>9.2f}  {','.join(t.flag_codes)}")
        if r.flagged_leaves:
            print("\n  sample flagged leaves:")
            for fl in r.flagged_leaves[:10]:
                print(f"    {fl.customer} x {fl.sku}: {', '.join(fl.flag_codes)}")
                print(f"      {fl.messages[0]}")

        if args.approve:
            reviews = {(fl.customer, fl.sku): ("ACCEPT", "smoke test") for fl in r.flagged_leaves}
            t0 = time.perf_counter()
            s, r, written = svc.approve(s.id, reviews, as_of)
            print(f"\nAPPROVE ({time.perf_counter() - t0:.1f}s) -> status {s.status}, {written} target rows merged")
            n = db.execute(text("""
                SELECT COUNT(*) FROM [SOAssistant].dbo._tb_SOScenarioTarget st
                 WHERE EXISTS (SELECT 1 FROM OPENJSON(st.cScenariosAppliedJson) j
                                WHERE TRY_CAST(j.value AS INT) = :id)
            """), {"id": s.id}).scalar()
            print(f"  rows now referencing scenario {s.id}: {n}")

        if not args.keep:
            s, updated, deleted = svc.cancel(scenario_id, "smoke test cleanup", as_of)
            print(f"\nCANCEL -> {s.status}; targets recomputed {updated}, deleted {deleted}")
        else:
            print(f"\n--keep: scenario {scenario_id} left as {s.status}")
        return 0
    except ScenarioError as e:
        print(f"\nScenario rule violation ({e.status_code}): {e.detail}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
