"""Phase 2b smoke test -- run the real forecast engine against the live DB.

Usage:
    cd c:\\Dev\\salesOrder\\backend
    .\\.venv\\Scripts\\python.exe scripts\\run_forecast_smoke_test.py

What it does:
    1. Opens a SessionLocal using the configured credentials
    2. Instantiates the ForecastEngine (loads enabled models + fallback aggregates)
    3. Discovers active leaves (customers x SKUs with recent activity)
    4. Processes a small cap of them (default 20) through the engine
    5. Prints a summary + sample forecast rows

What it writes to the DB:
    - 1 row in _tb_SOForecastRun (status = SUCCESS / PARTIAL)
    - Up to (leaves * horizon_weeks) rows in _tb_SOForecast
    - 1 row per leaf in _tb_SODemandClassification (UPSERT)
    - 1 row per unique customer in _tb_SODeliveryPattern (UPSERT)

Safe to re-run: all upserts; forecast rows are append-only per run id.
If you want to purge rows from this run, use the run_id printed at the end:
    DELETE FROM _tb_SOForecast WHERE idForecastRun = <that id>;
    DELETE FROM _tb_SOForecastRun WHERE idForecastRun = <that id>;
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make `backend/` importable when running as a script from any working dir.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND_ROOT))

from datetime import date  # noqa: E402

from database import SessionLocal  # noqa: E402
from services.forecasting.runner import run_nightly_forecast  # noqa: E402
from sqlalchemy import text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=20,
        help="Max leaves to process (default: 20). Use --limit 0 for full run.",
    )
    parser.add_argument(
        "--horizon", type=int, default=4,
        help="How many weeks ahead to forecast (default: 4).",
    )
    parser.add_argument(
        "--as-of", type=str, default=None,
        help="Anchor date YYYY-MM-DD (default: today). Mostly for repro testing.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable DEBUG-level logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    limit = args.limit if args.limit > 0 else None

    print()
    print("=" * 72)
    print("  Phase 2b forecast smoke test")
    print("=" * 72)
    print(f"  as_of         : {as_of or date.today()}")
    print(f"  horizon weeks : {args.horizon}")
    print(f"  leaf cap      : {limit if limit is not None else 'uncapped'}")
    print("=" * 72)
    print()

    db = SessionLocal()
    try:
        stats = run_nightly_forecast(
            db,
            as_of=as_of,
            horizon_weeks=args.horizon,
            created_by="smoke_test",
            limit_leaves=limit,
        )

        print()
        print("-" * 72)
        print("  Run summary")
        print("-" * 72)
        print(f"  run_id                      : {stats.run_id}")
        print(f"  status                      : {stats.status}")
        print(f"  leaves discovered           : {stats.leaves_discovered}")
        print(f"  leaves forecast             : {stats.leaves_forecast}")
        print(f"  leaves skipped (no context) : {stats.leaves_skipped_no_context}")
        print(f"  leaves all models declined  : {stats.leaves_all_declined}")
        print(f"  forecasts written           : {stats.forecasts_written}")
        print(f"  classifications written     : {stats.classifications_written}")
        print(f"  delivery patterns written   : {stats.delivery_patterns_written}")
        if stats.errors:
            print(f"  errors ({len(stats.errors)}):")
            for e in stats.errors[:5]:
                print(f"    - {e}")
            if len(stats.errors) > 5:
                print(f"    ... (+{len(stats.errors) - 5} more)")
        print("-" * 72)
        print()

        # Sample what we just wrote.
        if stats.run_id is not None and stats.forecasts_written > 0:
            print("  First 10 forecast rows (ordered by week, customer):")
            print()
            rows = db.execute(
                text("""
                    SELECT TOP 10
                        f.dWeekStart, f.cCustomerAccount, f.cStockCode,
                        f.cDistCentre, f.cCategory,
                        m.cCode AS model_code,
                        f.fPredictedQty, f.cDemandPattern, f.cConfidenceGrade,
                        f.bIsFallback, f.cFallbackReason, f.bIsActive
                    FROM [SOAssistant].dbo._tb_SOForecast f
                    JOIN [SOAssistant].dbo._tb_SOForecastModel m
                        ON m.idForecastModel = f.idForecastModel
                    WHERE f.idForecastRun = :run_id
                    ORDER BY f.dWeekStart, f.cCustomerAccount, f.cStockCode
                """),
                {"run_id": stats.run_id},
            ).mappings().all()

            # Header
            header = (
                f"  {'Week':10} {'Customer':16} {'Stock':16} "
                f"{'DC':6} {'Cat':14} {'Model':20} {'Qty':>8} "
                f"{'Pattern':13} {'Grade':5} {'Fallback':8} {'Active':6}"
            )
            print(header)
            print("  " + "-" * (len(header) - 2))
            for r in rows:
                print(
                    f"  {str(r['dWeekStart']):10} "
                    f"{(r['cCustomerAccount'] or '')[:16]:16} "
                    f"{(r['cStockCode'] or '')[:16]:16} "
                    f"{(r['cDistCentre'] or '')[:6]:6} "
                    f"{(r['cCategory'] or '')[:14]:14} "
                    f"{(r['model_code'] or '')[:20]:20} "
                    f"{float(r['fPredictedQty']):>8.1f} "
                    f"{(r['cDemandPattern'] or '')[:13]:13} "
                    f"{(r['cConfidenceGrade'] or '')[:5]:5} "
                    f"{('yes' if r['bIsFallback'] else 'no'):8} "
                    f"{('yes' if r['bIsActive'] else 'no'):6}"
                )

            print()
            print(f"  Full inspection:")
            print(f"    SELECT * FROM [SOAssistant].dbo._tb_SOForecast "
                  f"WHERE idForecastRun = {stats.run_id};")

        print()
        return 0 if stats.status in ("SUCCESS", "PARTIAL") else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
