"""Nightly forecast run.

Opens a _tb_SOForecastRun row per enabled model, iterates every active
leaf through the engine, writes the resulting forecast / classification
/ delivery-pattern rows, and closes the run with a status.

Transaction strategy:
  - One commit per leaf (bounded blast radius; if a single leaf blows
    up we lose that leaf's output, not the entire catalogue's).
  - Run metadata written in a separate commit so partial failures
    still produce a visible row in _tb_SOForecastRun with status
    PARTIAL or FAILED.
  - Delivery pattern writes grouped per customer, deduped across leaves.

Not scheduled yet -- Phase 6 adds APScheduler. For now expose a plain
`run_nightly_forecast()` function that any entry point can call (CLI,
cron, Celery, FastAPI startup, pytest).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import config
from . import weeks
from .classifier import Classification
from .delivery_pattern import DeliveryPattern
from .engine import ForecastEngine, LeafForecast, LeafResult


logger = logging.getLogger(__name__)


# Default forecast horizon -- 4 weeks ahead starting from next Monday.
_DEFAULT_HORIZON_WEEKS = 4


@dataclass
class RunStats:
    """Summary of a nightly run -- surfaced to logs + the UI."""
    run_id: Optional[int] = None
    status: str = "RUNNING"
    leaves_discovered: int = 0
    leaves_forecast: int = 0
    leaves_skipped_no_context: int = 0
    leaves_all_declined: int = 0
    forecasts_written: int = 0
    classifications_written: int = 0
    delivery_patterns_written: int = 0
    errors: list[str] = field(default_factory=list)


def run_nightly_forecast(
    db: Session,
    as_of: Optional[date] = None,
    horizon_weeks: int = _DEFAULT_HORIZON_WEEKS,
    created_by: str = "scheduler",
    *,
    limit_leaves: Optional[int] = None,
) -> RunStats:
    """Run the engine over every active leaf and persist the results.

    Args:
        db: a SQLAlchemy Session. The runner commits transactionally
            per leaf; caller should NOT be inside its own transaction.
        as_of: anchor date (default: today). Classification / fallback
               aggregates use the trailing year ending here.
        horizon_weeks: how many weeks ahead to forecast. Default 4.
        created_by: audit trail -- goes into _tb_SOForecastRun.cCreatedBy.
        limit_leaves: optional cap on the number of leaves processed
                      (useful for smoke tests / integration verification).

    Returns:
        RunStats with counts and the resulting run_id (per model).
        For Phase 2b with only BASE_MEDIAN_6W enabled we get one run_id;
        when more models flip on, this becomes a list of (model, run)
        pairs.
    """
    as_of = as_of or date.today()
    anchor = weeks.week_start(as_of)
    forecast_weeks = [
        anchor + timedelta(days=7 * (i + 1))
        for i in range(horizon_weeks)
    ]

    stats = RunStats()

    engine = ForecastEngine(db, as_of)
    enabled_models = engine._models
    if not enabled_models:
        logger.warning("No enabled models in _tb_SOForecastModel -- nothing to run.")
        stats.status = "FAILED"
        stats.errors.append("No enabled models")
        return stats

    # Phase 2b: one run id covers all enabled models. When Phase 2c adds
    # multiple models that produce DIFFERENT predictions per leaf, we
    # may want one run per model so the audit is cleaner. For now a
    # shared run is fine -- all rows carry idForecastModel directly.
    first_model_id = next(iter(enabled_models.values()))
    stats.run_id = _create_run(
        db, first_model_id, forecast_weeks[0], forecast_weeks[-1], created_by,
    )
    logger.info("Opened forecast run %s", stats.run_id)

    try:
        leaves = engine.find_active_leaves()
        stats.leaves_discovered = len(leaves)
        logger.info("Discovered %s active leaves", stats.leaves_discovered)

        if limit_leaves is not None:
            leaves = leaves[:limit_leaves]

        # Group by customer so we only compute the delivery pattern once.
        customers_seen: set[str] = set()

        for customer, sku in leaves:
            try:
                result = engine.classify_and_forecast(customer, sku, forecast_weeks)
                _persist_leaf_result(
                    db,
                    stats.run_id,
                    result,
                    stats,
                )

                if customer not in customers_seen:
                    customers_seen.add(customer)
                    pattern = engine.learn_delivery_pattern(customer)
                    if pattern.weeks_sampled > 0:
                        _persist_delivery_pattern(db, customer, pattern)
                        stats.delivery_patterns_written += 1
                db.commit()
            except Exception as exc:  # pragma: no cover -- logged + continues
                db.rollback()
                msg = f"leaf ({customer}, {sku}): {exc}"
                stats.errors.append(msg)
                logger.exception(msg)

        stats.status = "PARTIAL" if stats.errors else "SUCCESS"
        _close_run(db, stats.run_id, stats)
        db.commit()
    except Exception as exc:  # pragma: no cover
        db.rollback()
        stats.status = "FAILED"
        stats.errors.append(str(exc))
        logger.exception("Forecast run failed")
        _close_run(db, stats.run_id, stats)
        db.commit()

    logger.info(
        "Forecast run %s %s: wrote %s forecasts for %s leaves "
        "(%s skipped no-context, %s all models declined)",
        stats.run_id, stats.status,
        stats.forecasts_written, stats.leaves_forecast,
        stats.leaves_skipped_no_context, stats.leaves_all_declined,
    )
    return stats


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _create_run(
    db: Session,
    model_id: int,
    horizon_from: date,
    horizon_to: date,
    created_by: str,
) -> int:
    row = db.execute(
        text("""
            INSERT INTO [SOAssistant].dbo._tb_SOForecastRun
                (idForecastModel, cStatus, dHorizonFrom, dHorizonTo, cCreatedBy)
            OUTPUT inserted.idForecastRun
            VALUES (:model_id, 'RUNNING', :h_from, :h_to, :by)
        """),
        {
            "model_id": model_id,
            "h_from": horizon_from,
            "h_to": horizon_to,
            "by": created_by,
        },
    ).mappings().first()
    db.commit()
    return int(row["idForecastRun"])


def _close_run(db: Session, run_id: int, stats: RunStats) -> None:
    notes = "; ".join(stats.errors[:5]) if stats.errors else None
    db.execute(
        text("""
            UPDATE [SOAssistant].dbo._tb_SOForecastRun
            SET cStatus = :status,
                dtRunCompletedAt = sysdatetime(),
                iPredictionsWritten = :n,
                cNotes = :notes
            WHERE idForecastRun = :id
        """),
        {
            "status": stats.status,
            "n": stats.forecasts_written,
            "notes": notes,
            "id": run_id,
        },
    )


def _persist_leaf_result(
    db: Session,
    run_id: int,
    result: LeafResult,
    stats: RunStats,
) -> None:
    """Write classification + forecast rows for one leaf. Caller commits."""
    if result.skipped_reason == "NO_CONTEXT":
        stats.leaves_skipped_no_context += 1
        return

    _upsert_classification(db, result.classification, result.customer_account,
                           result.stock_code)
    stats.classifications_written += 1

    if result.skipped_reason == "ALL_MODELS_DECLINED":
        stats.leaves_all_declined += 1
        return

    # Keep the "exactly ONE active forecast per (leaf, week)" invariant
    # across runs: a new run's active row supersedes any earlier run's.
    # Same transaction as the insert, so a failed leaf leaves the old
    # active rows untouched.
    active_weeks = sorted({fc.week_start for fc in result.forecasts if fc.is_active})
    for wk in active_weeks:
        db.execute(
            text("""
                UPDATE [SOAssistant].dbo._tb_SOForecast
                   SET bIsActive = 0
                 WHERE cCustomerAccount = :customer
                   AND cStockCode       = :stock
                   AND dWeekStart       = :week
                   AND idForecastRun   <> :run_id
                   AND bIsActive = 1
            """),
            {"customer": result.customer_account, "stock": result.stock_code,
             "week": wk, "run_id": run_id},
        )

    for fc in result.forecasts:
        _insert_forecast(db, run_id, fc)
        stats.forecasts_written += 1

    stats.leaves_forecast += 1


def _upsert_classification(
    db: Session,
    classification: Classification,
    customer_account: str,
    stock_code: str,
) -> None:
    """UPSERT into _tb_SODemandClassification keyed on (customer, SKU).

    MERGE is the idiomatic SQL Server upsert. We don't need the full
    OUTPUT clause -- just insert-or-update.
    """
    db.execute(
        text("""
            MERGE [SOAssistant].dbo._tb_SODemandClassification AS tgt
            USING (VALUES (:customer, :stock)) AS src (cCustomerAccount, cStockCode)
                ON tgt.cCustomerAccount = src.cCustomerAccount
               AND tgt.cStockCode       = src.cStockCode
            WHEN NOT MATCHED BY TARGET THEN
                INSERT (cCustomerAccount, cStockCode, fCVSquared, fADI,
                        cPattern, iNonZeroWeeks, iTotalWeeksObserved,
                        iMinRequired, cRecommendedModel, cFallbackReason)
                VALUES (:customer, :stock, :cv2, :adi,
                        :pattern, :nzw, :tw,
                        :min_req, :rec_model, :fb_reason)
            WHEN MATCHED THEN
                UPDATE SET fCVSquared = :cv2,
                           fADI = :adi,
                           cPattern = :pattern,
                           iNonZeroWeeks = :nzw,
                           iTotalWeeksObserved = :tw,
                           iMinRequired = :min_req,
                           cRecommendedModel = :rec_model,
                           cFallbackReason = :fb_reason,
                           dtClassifiedAt = sysdatetime();
        """),
        {
            "customer": customer_account,
            "stock": stock_code,
            "cv2": classification.cv_squared,
            "adi": classification.adi,
            "pattern": classification.pattern,
            "nzw": classification.non_zero_weeks,
            "tw": classification.total_weeks_observed,
            "min_req": classification.min_required,
            "rec_model": classification.recommended_model,
            "fb_reason": classification.fallback_reason,
        },
    )


def _insert_forecast(db: Session, run_id: int, fc: LeafForecast) -> None:
    """INSERT a single _tb_SOForecast row. The UNIQUE constraint on
    (run, customer, SKU, week) means re-running the same run id for
    the same leaf will fail -- caller must either create a new run
    (preferred) or purge old rows first.
    """
    # Context dims come from the LeafResult.context, but LeafForecast
    # doesn't carry them to keep the dataclass small. Pull them from
    # the engine's cache... actually no -- the runner handles writing
    # so the simpler path is to re-query. For Phase 2b we query once
    # per leaf which is fine; Phase 2c can pass context through.
    row = db.execute(
        text("""
            SELECT ulARRoute       AS route,
                   ulARDistCentre  AS dist_centre,
                   ulARTelesalesRep AS telesales,
                   REP.Code        AS sales_rep
            FROM [BA_00_LPD_CON].dbo.Client CL
            LEFT JOIN [BA_00_LPD_CON].dbo.SalesRep REP ON REP.idSalesRep = CL.RepID
            WHERE CL.Account = :customer
        """),
        {"customer": fc.customer_account},
    ).mappings().first()

    cat_row = db.execute(
        text("""
            SELECT ulIIMainCategory AS category
            FROM [BA_00_LPD_CON].dbo.StkItem
            WHERE Code = :stock
        """),
        {"stock": fc.stock_code},
    ).mappings().first()

    db.execute(
        text("""
            INSERT INTO [SOAssistant].dbo._tb_SOForecast
                (idForecastRun, idForecastModel,
                 cCustomerAccount, cStockCode, dWeekStart,
                 cRoute, cDistCentre, cCategory, cTelesales, cSalesRep,
                 fPredictedQty, fPILower, fPIUpper,
                 cDemandPattern, cConfidenceGrade,
                 bIsFallback, cFallbackReason, fFallbackSource,
                 bIsActive)
            VALUES (:run_id, :model_id,
                    :customer, :stock, :week_start,
                    :route, :dc, :category, :tel, :rep,
                    :qty, :pi_lower, :pi_upper,
                    :pattern, :grade,
                    :fb, :fb_reason, :fb_src,
                    :active)
        """),
        {
            "run_id": run_id,
            "model_id": fc.model_id,
            "customer": fc.customer_account,
            "stock": fc.stock_code,
            "week_start": fc.week_start,
            "route": (row["route"] if row else None),
            "dc": (row["dist_centre"] if row else None),
            "category": (cat_row["category"] if cat_row else None),
            "tel": (row["telesales"] if row else None),
            "rep": (row["sales_rep"] if row else None),
            "qty": fc.predicted_qty,
            "pi_lower": fc.pi_lower,
            "pi_upper": fc.pi_upper,
            "pattern": fc.pattern,
            "grade": fc.confidence_grade,
            "fb": 1 if fc.is_fallback else 0,
            "fb_reason": fc.fallback_reason,
            "fb_src": fc.fallback_source,
            "active": 1 if fc.is_active else 0,
        },
    )


def _persist_delivery_pattern(
    db: Session,
    customer_account: str,
    pattern: DeliveryPattern,
) -> None:
    """UPSERT into _tb_SODeliveryPattern keyed on customer_account."""
    db.execute(
        text("""
            MERGE [SOAssistant].dbo._tb_SODeliveryPattern AS tgt
            USING (VALUES (:customer)) AS src (cCustomerAccount)
                ON tgt.cCustomerAccount = src.cCustomerAccount
            WHEN NOT MATCHED BY TARGET THEN
                INSERT (cCustomerAccount, fMonShare, fTueShare, fWedShare,
                        fThuShare, fFriShare, fSatShare, fSunShare,
                        iWeeksSampled, cConfidence)
                VALUES (:customer, :mon, :tue, :wed, :thu, :fri, :sat, :sun,
                        :weeks, :conf)
            WHEN MATCHED THEN
                UPDATE SET fMonShare = :mon,
                           fTueShare = :tue,
                           fWedShare = :wed,
                           fThuShare = :thu,
                           fFriShare = :fri,
                           fSatShare = :sat,
                           fSunShare = :sun,
                           iWeeksSampled = :weeks,
                           cConfidence = :conf,
                           dtComputedAt = sysdatetime();
        """),
        {
            "customer": customer_account,
            "mon": pattern.mon_share, "tue": pattern.tue_share,
            "wed": pattern.wed_share, "thu": pattern.thu_share,
            "fri": pattern.fri_share, "sat": pattern.sat_share,
            "sun": pattern.sun_share,
            "weeks": pattern.weeks_sampled,
            "conf": pattern.confidence,
        },
    )
