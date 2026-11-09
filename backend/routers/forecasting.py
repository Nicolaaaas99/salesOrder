"""Forecasting & scenario API (Phase 3).

All endpoints are SUPERVISOR+ (management tooling). Reps get target-aware
suggestions through /history in Phase 4, not through this router.

  GET    /forecasting/models                        model registry + leaderboard
  GET    /forecasting/forecasts                     leaf forecasts (filters, paging)
  GET    /forecasting/scenarios                     list (optional ?status=)
  GET    /forecasting/scenarios/{id}                one scenario
  POST   /forecasting/scenarios                     create (DRAFT)
  PATCH  /forecasting/scenarios/{id}                edit DRAFT/PREVIEW (-> DRAFT)
  POST   /forecasting/scenarios/{id}/preview        compute cascade + flags, no target writes
  POST   /forecasting/scenarios/{id}/approve        review flags + materialise targets (-> ACTIVE)
  POST   /forecasting/scenarios/{id}/cancel         cancel (un-materialises if ACTIVE)
  GET    /forecasting/targets                       materialised targets, optional roll-up
  PATCH  /forecasting/targets/{id}/override         set / clear a manager override
"""

import json
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

import schemas
from auth import get_current_user, require_min_role
from database import get_db
from services.forecasting import scenarios as sc_mod
from services.forecasting.scenarios import ScenarioError, ScenarioScope
from services.forecasting import weeks as weeks_mod
from services.forecasting import workbench as wb

router = APIRouter(
    prefix="/forecasting",
    tags=["forecasting"],
    dependencies=[Depends(require_min_role("SUPERVISOR"))],
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _raise(e: ScenarioError):
    raise HTTPException(status_code=e.status_code, detail=e.detail)


def _scope_in(m: schemas.ScenarioScopeModel) -> ScenarioScope:
    return ScenarioScope.from_dict(m.model_dump(), strict=True)


def _scope_out(s: ScenarioScope) -> schemas.ScenarioScopeModel:
    return schemas.ScenarioScopeModel(**s.to_dict())


def _scenario_out(s: sc_mod.Scenario) -> schemas.ScenarioOut:
    return schemas.ScenarioOut(
        id=s.id, name=s.name, description=s.description, scope=_scope_out(s.scope),
        lever_type=s.lever_type, lever_value=s.lever_value,
        period_from=s.period_from, period_to=s.period_to, status=s.status,
        created_by=s.created_by, created_at=s.created_at,
        approved_by=s.approved_by, approved_at=s.approved_at,
        cancel_reason=s.cancel_reason, cancelled_at=s.cancelled_at,
        measure=s.measure, anchor_level=s.anchor_level,
    )


def _cascade_out(
    scenario: sc_mod.Scenario,
    r: sc_mod.CascadeResult,
    offset: int,
    limit: int,
    flagged_only: bool,
    rows_written: Optional[int] = None,
) -> schemas.ScenarioCascadeResponse:
    rows = [t for t in r.targets if t.flags] if flagged_only else r.targets
    page = rows[offset:offset + limit]
    return schemas.ScenarioCascadeResponse(
        scenario=_scenario_out(scenario),
        as_of_week=r.as_of_week,
        weeks=r.weeks,
        leaf_count=r.leaf_count,
        leaf_week_count=len(r.targets),
        total_forecast_qty=r.total_forecast_qty,
        total_target_qty=r.total_target_qty,
        total_effective_target_qty=r.total_effective_target_qty,
        total_before_qty=r.total_before_qty,
        rebalanced_count=len(r.rebalanced),
        rebalanced_delta_qty=r.rebalanced_delta_qty,
        conflicts=[schemas.TargetConflictOut(scenario_id=c.scenario_id, week=c.week, goal=c.goal,
                                             inner_total=c.inner_total, measure=c.measure,
                                             message=c.message) for c in r.conflicts],
        locked_skipped=r.locked_skipped,
        warnings=r.warnings,
        flagged_leaves=[
            schemas.FlaggedLeafOut(customer_account=f.customer, stock_code=f.sku,
                                   flag_codes=f.flag_codes, weeks=f.weeks, messages=f.messages)
            for f in r.flagged_leaves
        ],
        targets=[
            schemas.CascadedTargetOut(
                customer_account=t.leaf.customer, stock_code=t.leaf.sku,
                week_start=t.leaf.week, route=t.leaf.route, dist_centre=t.leaf.dist_centre,
                category=t.leaf.category, telesales=t.leaf.telesales,
                sales_rep=t.leaf.sales_rep, forecast_qty=t.leaf.forecast_qty,
                target_qty=t.target_qty, override_qty=t.override_qty,
                effective_target_qty=t.effective_target_qty, scenario_ids=t.scenario_ids,
                before_qty=t.before_qty, in_scope=t.in_scope,
                flags=[schemas.ReasonablenessFlagOut(code=f.code, message=f.message,
                                                     metric_value=f.metric_value,
                                                     threshold_value=f.threshold_value)
                       for f in t.flags],
            )
            for t in page
        ],
        targets_offset=offset,
        rows_written=rows_written,
    )


def _as_float(v) -> Optional[float]:
    return float(v) if v is not None else None


# ---------------------------------------------------------------------------
# models + forecasts
# ---------------------------------------------------------------------------

@router.get("/models", response_model=List[schemas.ForecastModelRow])
def list_models(db: Session = Depends(get_db)):
    """Every registered model (enabled or not) with its scored accuracy,
    from _vw_SOModelLeaderboard. Unscored models have null metrics."""
    rows = db.execute(text("""
        SELECT m.cCode, m.cName, m.cFamily, m.bEnabled,
               lb.iLeafsScored, lb.fWeightedMAPE, lb.fMAE, lb.fBias,
               lb.dFirstScoredWeek, lb.dLastScoredWeek
          FROM [SOAssistant].dbo._tb_SOForecastModel m
          LEFT JOIN [SOAssistant].dbo._vw_SOModelLeaderboard lb
            ON lb.idForecastModel = m.idForecastModel
         ORDER BY m.bEnabled DESC, lb.fWeightedMAPE, m.cCode
    """)).mappings().all()
    return [
        schemas.ForecastModelRow(
            model_code=r["cCode"], model_name=r["cName"], family=r["cFamily"],
            is_enabled=bool(r["bEnabled"]), leaves_scored=int(r["iLeafsScored"] or 0),
            weighted_mape=_as_float(r["fWeightedMAPE"]), mae=_as_float(r["fMAE"]),
            bias=_as_float(r["fBias"]), first_scored_week=r["dFirstScoredWeek"],
            last_scored_week=r["dLastScoredWeek"],
        )
        for r in rows
    ]


@router.get("/forecasts", response_model=schemas.ForecastListResponse)
def list_forecasts(
    customer_account: Optional[str] = Query(None),
    stock_code: Optional[str] = Query(None),
    route: Optional[str] = Query(None),
    dist_centre: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    telesales: Optional[str] = Query(None),
    sales_rep: Optional[str] = Query(None),
    week_from: Optional[date] = Query(None),
    week_to: Optional[date] = Query(None),
    model_code: Optional[str] = Query(None),
    active_only: bool = Query(True, description="Only the forecast that feeds targets (latest run)"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Leaf forecasts. With active_only (default) each leaf-week appears
    once -- the active row from the latest run. With active_only=false,
    every model's prediction from every run is returned (shadow rows)."""
    where, params = [], {}
    for col, val, name in (
        ("f.cCustomerAccount", customer_account, "cust"), ("f.cStockCode", stock_code, "sku"),
        ("f.cRoute", route, "route"), ("f.cDistCentre", dist_centre, "dc"),
        ("f.cCategory", category, "cat"), ("f.cTelesales", telesales, "tel"),
        ("f.cSalesRep", sales_rep, "rep"), ("m.cCode", model_code, "model"),
    ):
        if val:
            where.append(f"{col} = :{name}")
            params[name] = val
    if week_from:
        where.append("f.dWeekStart >= :wf")
        params["wf"] = week_from
    if week_to:
        where.append("f.dWeekStart <= :wt")
        params["wt"] = week_to
    if active_only:
        where.append("f.bIsActive = 1")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rank_filter = "WHERE x.rn = 1" if active_only else ""

    base = f"""
        WITH x AS (
            SELECT f.idForecast, f.idForecastRun, f.cCustomerAccount, f.cStockCode,
                   f.dWeekStart, f.cRoute, f.cDistCentre, f.cCategory, f.cTelesales,
                   f.cSalesRep, f.fPredictedQty, m.cCode AS cModelCode, f.bIsActive,
                   f.bIsFallback, f.cFallbackReason, f.cDemandPattern, f.cConfidenceGrade,
                   ROW_NUMBER() OVER (
                       PARTITION BY f.cCustomerAccount, f.cStockCode, f.dWeekStart
                       ORDER BY f.idForecastRun DESC, f.idForecast DESC) AS rn
              FROM [SOAssistant].dbo._tb_SOForecast f
              JOIN [SOAssistant].dbo._tb_SOForecastModel m
                ON m.idForecastModel = f.idForecastModel
             {where_sql}
        )
    """
    total = db.execute(text(base + f"SELECT COUNT(*) AS n FROM x {rank_filter}"),
                       params).scalar() or 0
    rows = db.execute(text(base + f"""
        SELECT * FROM x {rank_filter}
         ORDER BY x.dWeekStart, x.cCustomerAccount, x.cStockCode, x.idForecastRun DESC
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """), {**params, "offset": offset, "limit": limit}).mappings().all()

    return schemas.ForecastListResponse(
        total=int(total),
        forecasts=[
            schemas.ForecastRow(
                forecast_id=int(r["idForecast"]), run_id=int(r["idForecastRun"]),
                customer_account=r["cCustomerAccount"], stock_code=r["cStockCode"],
                week_start=r["dWeekStart"], route=r["cRoute"], dist_centre=r["cDistCentre"],
                category=r["cCategory"], telesales=r["cTelesales"], sales_rep=r["cSalesRep"],
                forecast_qty=float(r["fPredictedQty"]), model_code=r["cModelCode"],
                is_active=bool(r["bIsActive"]), is_fallback=bool(r["bIsFallback"]),
                fallback_reason=r["cFallbackReason"], demand_pattern=r["cDemandPattern"],
                confidence_grade=r["cConfidenceGrade"],
            )
            for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------

@router.get("/scenarios", response_model=List[schemas.ScenarioOut])
def list_scenarios(
    status: Optional[str] = Query(None, pattern="^(DRAFT|PREVIEW|ACTIVE|COMPLETED|CANCELLED)$"),
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    svc = sc_mod.ScenarioService(db, username)
    return [_scenario_out(s) for s in svc.list_scenarios(status)]


@router.get("/scenarios/{scenario_id}", response_model=schemas.ScenarioOut)
def get_scenario(scenario_id: int, db: Session = Depends(get_db),
                 username: str = Depends(get_current_user)):
    try:
        return _scenario_out(sc_mod.ScenarioService(db, username).get(scenario_id))
    except ScenarioError as e:
        _raise(e)


@router.post("/scenarios", response_model=schemas.ScenarioOut, status_code=201)
def create_scenario(payload: schemas.ScenarioCreate, db: Session = Depends(get_db),
                    username: str = Depends(get_current_user)):
    try:
        s = sc_mod.ScenarioService(db, username).create(
            name=payload.name.strip(), description=payload.description,
            scope=_scope_in(payload.scope), lever_type=payload.lever_type,
            lever_value=payload.lever_value, period_from=payload.period_from,
            period_to=payload.period_to, measure=payload.measure, anchor_level=payload.anchor_level,
        )
        return _scenario_out(s)
    except ScenarioError as e:
        _raise(e)


@router.patch("/scenarios/{scenario_id}", response_model=schemas.ScenarioOut)
def update_scenario(scenario_id: int, payload: schemas.ScenarioUpdate,
                    db: Session = Depends(get_db), username: str = Depends(get_current_user)):
    changes = payload.model_dump(exclude_unset=True)
    if "scope" in changes:
        changes["scope"] = _scope_in(payload.scope) if payload.scope is not None else ScenarioScope()
    if "name" in changes and changes["name"] is not None:
        changes["name"] = changes["name"].strip()
    for k in ("name", "lever_type", "lever_value", "period_from", "period_to"):
        if k in changes and changes[k] is None:
            raise HTTPException(400, f"{k} cannot be null")
    try:
        return _scenario_out(sc_mod.ScenarioService(db, username).update(scenario_id, changes))
    except ScenarioError as e:
        _raise(e)


@router.post("/scenarios/{scenario_id}/preview", response_model=schemas.ScenarioCascadeResponse)
def preview_scenario(
    scenario_id: int,
    as_of: Optional[date] = Query(None, description="Defaults to today; weeks before as_of's week are skipped"),
    flagged_only: bool = Query(False),
    limit: int = Query(1000, ge=0, le=20000),
    offset: int = Query(0, ge=0),
    rollup_level: Optional[str] = Query(None, pattern="^(dist_centre|route|customer|category|sku)$"),
    rollup_week: Optional[date] = Query(None, description="Week for the rollup (default: first week)"),
    measure: str = Query("units", pattern="^(units|litres)$"),
    dist_centre: Optional[str] = Query(None),
    route: Optional[str] = Query(None),
    customer: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    """Compute the cascade + reasonableness flags without writing targets.
    Totals and `flagged_leaves` always cover the whole scenario; `targets`
    is one page of leaf-weeks. With `rollup_level` (+ the drill path the
    Targets screen is showing) it also returns the change per row of that
    view for `rollup_week` -- the screen's "Proposed" column."""
    try:
        s, result = sc_mod.ScenarioService(db, username).preview(scenario_id, as_of)
    except ScenarioError as e:
        _raise(e)
    out = _cascade_out(s, result, offset, limit, flagged_only)
    if rollup_level:
        path = {k: v for k, v in (("dist_centre", dist_centre), ("route", route),
                                  ("customer", customer), ("category", category)) if v is not None}
        wk = weeks_mod.week_start(rollup_week) if rollup_week else (result.weeks[0] if result.weeks else None)
        factors = None
        if measure == "litres":
            factors = wb.load_litre_factors(db, sorted({t.leaf.sku for t in result.targets}))
        out.rollup_level = rollup_level
        out.rollup_deltas, out.rollup_total_delta = rollup_deltas(result.targets, rollup_level, path, wk, factors)
        _, out.rollup_scope_delta = rollup_deltas([t for t in result.targets if t.in_scope],
                                                  rollup_level, {}, wk, factors)
        _, out.rollup_outside_delta = rollup_deltas([t for t in result.targets if not t.in_scope],
                                                    rollup_level, {}, wk, factors)
    return out


def rollup_deltas(targets, level: str, path: dict, week: Optional[date],
                  factors: Optional[dict] = None) -> tuple[dict, float]:
    """Sum (after - before) per `level` value for one week, within the drill
    path. Pure. Keys are upper-cased/trimmed like the workbench rows."""
    norm = lambda v: (v or "").strip().upper()
    deltas: dict[str, float] = {}
    total = 0.0
    for t in targets:
        lf = t.leaf
        if week is not None and lf.week != week:
            continue
        if any(norm(getattr(lf, k)) != norm(v) for k, v in path.items()):
            continue
        before = t.before_qty if t.before_qty is not None else lf.forecast_qty
        d = (t.effective_target_qty - before) * ((factors or {}).get(lf.sku, 1.0) if factors is not None else 1.0)
        if abs(d) < 1e-9:
            continue
        key = norm(getattr(lf, level))
        deltas[key] = round(deltas.get(key, 0.0) + d, 4)
        total += d
    return deltas, round(total, 4)


@router.post("/scenarios/{scenario_id}/approve", response_model=schemas.ScenarioCascadeResponse)
def approve_scenario(
    scenario_id: int,
    payload: schemas.ScenarioApproveRequest,
    as_of: Optional[date] = Query(None),
    limit: int = Query(1000, ge=0, le=20000),
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    """Materialise targets. Every flagged customer x SKU from the preview
    must appear in `reviews` with ACCEPT or EXCLUDE, otherwise 400 with the
    unreviewed list. The cascade is recomputed at approval time."""
    reviews: dict = {}
    for r in payload.reviews:
        key = (r.customer_account.strip(), r.stock_code.strip())
        if key in reviews:
            raise HTTPException(400, f"Duplicate review for {key[0]} x {key[1]}")
        reviews[key] = (r.decision, r.note)
    try:
        s, result, written = sc_mod.ScenarioService(db, username).approve(
            scenario_id, reviews, as_of, group_decisions=dict(payload.group_decisions))
    except ScenarioError as e:
        _raise(e)
    return _cascade_out(s, result, 0, limit, False, rows_written=written)


@router.post("/scenarios/{scenario_id}/cancel", response_model=schemas.ScenarioCancelResponse)
def cancel_scenario(scenario_id: int, payload: schemas.ScenarioCancelRequest,
                    as_of: Optional[date] = Query(None),
                    db: Session = Depends(get_db), username: str = Depends(get_current_user)):
    """Cancel. If the scenario was ACTIVE, its unlocked current/future
    targets are recomputed without it."""
    try:
        s, updated, deleted = sc_mod.ScenarioService(db, username).cancel(
            scenario_id, payload.reason.strip(), as_of)
    except ScenarioError as e:
        _raise(e)
    return schemas.ScenarioCancelResponse(scenario=_scenario_out(s),
                                          targets_recomputed=updated, targets_deleted=deleted)


# ---------------------------------------------------------------------------
# targets
# ---------------------------------------------------------------------------

_ROLLUP_COLUMNS = {
    "route": "st.cRoute",
    "dist_centre": "st.cDistCentre",
    "category": "st.cCategory",
    "customer": "st.cCustomerAccount",
    "sku": "st.cStockCode",
    "week": None,   # company total per week
}


@router.get("/targets", response_model=schemas.ScenarioTargetListResponse)
def list_targets(
    scenario_id: Optional[int] = Query(None, description="Only targets this scenario contributed to"),
    customer_account: Optional[str] = Query(None),
    stock_code: Optional[str] = Query(None),
    route: Optional[str] = Query(None),
    dist_centre: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    week_from: Optional[date] = Query(None),
    week_to: Optional[date] = Query(None),
    flagged_only: bool = Query(False),
    rollup: Optional[str] = Query(None, pattern="^(route|dist_centre|category|customer|sku|week)$"),
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    where, params = [], {}
    for col, val, name in (
        ("st.cCustomerAccount", customer_account, "cust"), ("st.cStockCode", stock_code, "sku"),
        ("st.cRoute", route, "route"), ("st.cDistCentre", dist_centre, "dc"),
        ("st.cCategory", category, "cat"),
    ):
        if val:
            where.append(f"{col} = :{name}")
            params[name] = val
    if week_from:
        where.append("st.dWeekStart >= :wf")
        params["wf"] = week_from
    if week_to:
        where.append("st.dWeekStart <= :wt")
        params["wt"] = week_to
    if flagged_only:
        where.append("st.bIsFlagged = 1")
    if scenario_id is not None:
        where.append("""EXISTS (SELECT 1 FROM OPENJSON(st.cScenariosAppliedJson) j
                                 WHERE TRY_CAST(j.value AS INT) = :sid)""")
        params["sid"] = scenario_id
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    src = "FROM [SOAssistant].dbo._tb_SOScenarioTarget st " + where_sql

    if rollup:
        col = _ROLLUP_COLUMNS[rollup]
        dim = col if col else "CAST(NULL AS VARCHAR(100))"
        group = f"{col}, st.dWeekStart" if col else "st.dWeekStart"
        grouped = f"""
            SELECT {dim} AS dim_value, st.dWeekStart,
                   SUM(st.fForecastQty) AS fq, SUM(st.fTargetQty) AS tq,
                   COUNT(*) AS n, SUM(CAST(st.bIsFlagged AS INT)) AS nf
              {src}
             GROUP BY {group}
        """
        total = db.execute(text(f"SELECT COUNT(*) FROM ({grouped}) g"), params).scalar() or 0
        rows = db.execute(text(f"""
            {grouped}
            ORDER BY st.dWeekStart, dim_value
            OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
        """), {**params, "offset": offset, "limit": limit}).mappings().all()
        return schemas.ScenarioTargetListResponse(
            rollup=rollup, total=int(total),
            rollup_rows=[schemas.TargetRollupRow(
                dim_value=r["dim_value"], week_start=r["dWeekStart"],
                forecast_qty=float(r["fq"] or 0), target_qty=float(r["tq"] or 0),
                leaf_count=int(r["n"]), flagged_count=int(r["nf"] or 0)) for r in rows],
        )

    total = db.execute(text(f"SELECT COUNT(*) {src}"), params).scalar() or 0
    rows = db.execute(text(f"""
        SELECT st.* {src}
         ORDER BY st.dWeekStart, st.cCustomerAccount, st.cStockCode
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY
    """), {**params, "offset": offset, "limit": limit}).mappings().all()
    return schemas.ScenarioTargetListResponse(
        rollup=None, total=int(total),
        targets=[_target_row(r) for r in rows],
    )


def _target_row(r) -> schemas.ScenarioTargetRow:
    try:
        flags = json.loads(r["cFlagReasonsJson"]) if r["cFlagReasonsJson"] else []
    except ValueError:
        flags = []
    return schemas.ScenarioTargetRow(
        id=int(r["idScenarioTarget"]), customer_account=r["cCustomerAccount"],
        stock_code=r["cStockCode"], week_start=r["dWeekStart"], route=r["cRoute"],
        dist_centre=r["cDistCentre"], category=r["cCategory"],
        forecast_qty=float(r["fForecastQty"]), target_qty=float(r["fTargetQty"]),
        override_qty=_as_float(r["fOverrideQty"]), override_reason=r["cOverrideReason"],
        override_by=r["cOverrideBy"], source=r["cSource"],
        scenario_ids=sc_mod._parse_ids(r["cScenariosAppliedJson"]),
        is_flagged=bool(r["bIsFlagged"]), flag_codes=flags, is_locked=bool(r["bIsLocked"]),
    )


@router.patch("/targets/{target_id}/override", response_model=schemas.ScenarioTargetRow)
def override_target(
    target_id: int,
    payload: schemas.TargetOverrideRequest,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    """Set a manager override (override_qty >= 0) or clear it (null).
    Locked targets need break_lock=true. Clearing an override can delete
    the row if no active scenario still covers it (404 afterwards)."""
    try:
        row = sc_mod.ScenarioService(db, username).override_target(
            target_id, payload.override_qty, payload.reason.strip(), payload.break_lock)
    except ScenarioError as e:
        _raise(e)
    if row.get("deleted"):
        raise HTTPException(410, "Override cleared. No active scenario covers this customer-product "
                                 "this week, so it now follows the forecast.")
    return _target_row(row)


# ---------------------------------------------------------------------------
# Phase 5: Target Workbench, scope pickers, Opportunity Inbox
# ---------------------------------------------------------------------------

def _wb_row(r: wb.WorkbenchRow, label: Optional[str] = None) -> schemas.WorkbenchRowOut:
    return schemas.WorkbenchRowOut(
        key=r.key, label=label if label is not None else r.label,
        forecast=round(r.forecast, 4), target=round(r.target, 4),
        delivered=round(r.delivered, 4), on_order=round(r.on_order, 4),
        committed=round(r.committed, 4), leaf_count=r.leaf_count,
        targeted_count=r.targeted_count, flagged_count=r.flagged_count,
        override_count=r.override_count, target_id=r.target_id, override=r.override,
        source=r.source, is_locked=r.is_locked,
    )


@router.get("/workbench", response_model=schemas.WorkbenchResponse)
def workbench(
    week: date = Query(..., description="Any date in the week; normalised to its Monday"),
    level: str = Query("dist_centre", pattern="^(dist_centre|route|customer|category|sku)$"),
    dist_centre: Optional[str] = Query(None),
    route: Optional[str] = Query(None),
    customer: Optional[str] = Query(None),
    category: Optional[str] = Query(None, description="Drill path, or a filter at any level"),
    measure: str = Query("units", pattern="^(units|litres)$"),
    db: Session = Depends(get_db),
):
    """Plan vs actual for one week at one hierarchy level. Pass the drill
    path (dist_centre / route / customer / category) for the levels above
    `level`; `category` may also be passed at any level as a filter."""
    week_start = weeks_mod.week_start(week)
    filters = {k: v for k, v in (("dist_centre", dist_centre), ("route", route),
                                 ("customer", customer), ("category", category)) if v is not None}
    leaves = wb.load_leaves(db, week_start)
    factors = None
    if measure == "litres":
        factors = wb.load_litre_factors(db, sorted({lf.sku for lf in leaves}))
    total, rows = wb.rollup(leaves, level, filters, factors)
    labels = wb.load_labels(db, level, [r.key for r in rows])
    return schemas.WorkbenchResponse(
        week_start=week_start, level=level, measure=measure, filters=filters,
        total=_wb_row(total),
        rows=[_wb_row(r, labels.get(r.key.strip().upper(), r.key) if labels else None)
              for r in rows],
    )


_SCOPE_OPTION_COLUMNS = {
    "distCentre": "cDistCentre", "route": "cRoute", "customer": "cCustomerAccount",
    "category": "cCategory", "sku": "cStockCode", "salesRep": "cSalesRep",
    "telesales": "cTelesales",
}


@router.get("/scope-options", response_model=List[schemas.ScopeOption])
def scope_options(
    dim: str = Query(..., pattern="^(distCentre|route|customer|category|sku|salesRep|telesales)$"),
    q: Optional[str] = Query(None, max_length=50),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Values for a scenario scope picker, taken from the active forecasts
    themselves -- scope filters match these exact values, so every option
    is guaranteed to select something."""
    col = _SCOPE_OPTION_COLUMNS[dim]
    label_join, label_col = "", "NULL"
    if dim == "customer":
        label_join = "LEFT JOIN [BA_00_LPD_CON].dbo.Client L ON L.Account = v.val"
        label_col = "L.Name"
    elif dim == "sku":
        label_join = "LEFT JOIN [BA_00_LPD_CON].dbo.StkItem L ON L.Code = v.val"
        label_col = "L.Description_1"
    where = ""
    params: dict = {"lim": limit}
    if q:
        params["q"] = f"%{q.strip()}%"
        where = f"WHERE v.val LIKE :q OR {label_col} LIKE :q" if label_join else "WHERE v.val LIKE :q"
    rows = db.execute(text(f"""
        SELECT TOP (:lim) v.val, {label_col} AS label
          FROM (SELECT DISTINCT {col} AS val FROM [SOAssistant].dbo._tb_SOForecast
                 WHERE bIsActive = 1 AND {col} IS NOT NULL AND {col} <> '') v
          {label_join}
          {where}
         ORDER BY v.val
    """), params).mappings().all()
    return [schemas.ScopeOption(value=r["val"], label=(r["label"] or "").strip()) for r in rows]


def _task_out(r) -> schemas.ForecastTaskOut:
    return schemas.ForecastTaskOut(
        id=int(r["idTask"]), task_type=r["cTaskType"], priority=r["cPriority"],
        assigned_to=r["cAssignedToUsername"], customer_account=r["cCustomerAccount"],
        stock_code=r["cStockCode"], category=r["cCategory"], route=r["cRoute"],
        dist_centre=r["cDistCentre"], scenario_id=r["idScenario"], title=r["cTitle"],
        description=r["cDescription"], due_date=r["dDueDate"], status=r["cStatus"],
        resolution_notes=r["cResolutionNotes"], resolved_by=r["cResolvedBy"],
        created_at=r["dtCreatedAt"], resolved_at=r["dtResolvedAt"],
    )


@router.get("/tasks", response_model=List[schemas.ForecastTaskOut])
def list_tasks(
    status: Optional[str] = Query("open", pattern="^(open|closed|all)$",
                                  description="open = OPEN/IN_PROGRESS/SNOOZED"),
    mine: bool = Query(False, description="Only tasks assigned to me"),
    task_type: Optional[str] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    where, params = [], {"lim": limit}
    if status == "open":
        where.append("cStatus IN ('OPEN', 'IN_PROGRESS', 'SNOOZED')")
    elif status == "closed":
        where.append("cStatus IN ('RESOLVED', 'DISMISSED')")
    if mine:
        where.append("cAssignedToUsername = :u")
        params["u"] = username
    if task_type:
        where.append("cTaskType = :t")
        params["t"] = task_type
    rows = db.execute(text(f"""
        SELECT TOP (:lim) * FROM [SOAssistant].dbo._tb_SOTask
         {('WHERE ' + ' AND '.join(where)) if where else ''}
         ORDER BY CASE cPriority WHEN 'HIGH' THEN 0 WHEN 'MED' THEN 1 ELSE 2 END,
                  dDueDate, idTask
    """), params).mappings().all()
    return [_task_out(r) for r in rows]


@router.patch("/tasks/{task_id}", response_model=schemas.ForecastTaskOut)
def update_task(
    task_id: int,
    payload: schemas.ForecastTaskUpdate,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    closing = payload.status in ("RESOLVED", "DISMISSED")
    n = db.execute(text("""
        UPDATE [SOAssistant].dbo._tb_SOTask
           SET cStatus = :s,
               cResolutionNotes = COALESCE(:notes, cResolutionNotes),
               cResolvedBy  = CASE WHEN :closing = 1 THEN :u ELSE NULL END,
               dtResolvedAt = CASE WHEN :closing = 1 THEN sysdatetime() ELSE NULL END
         WHERE idTask = :id
    """), {"s": payload.status, "notes": payload.resolution_notes, "closing": 1 if closing else 0,
           "u": username, "id": task_id}).rowcount
    if not n:
        db.rollback()
        raise HTTPException(404, f"Task {task_id} not found")
    db.commit()
    row = db.execute(text("SELECT * FROM [SOAssistant].dbo._tb_SOTask WHERE idTask = :id"),
                     {"id": task_id}).mappings().first()
    return _task_out(row)
