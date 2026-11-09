"""Scenario cascade + target materialisation (Phase 3).

A scenario is a scoped lever over a period (_tb_SOScenario). Approving it
materialises leaf targets into _tb_SOScenarioTarget -- one row per
(customer, SKU, week), shared by every scenario that touches it.

Lifecycle (CK_SOScenario_Status):
    DRAFT --preview--> PREVIEW --approve--> ACTIVE --cancel--> CANCELLED
    DRAFT/PREVIEW --edit--> DRAFT          DRAFT/PREVIEW --cancel--> CANCELLED

Scope (cScopeJson) -- AND across dimensions, OR within one:
    {"distCentre": ["WC"], "route": [...], "customer": [...],
     "category": ["Milk"], "sku": [...], "salesRep": [...],
     "telesales": [...],
     "excludeLeaves": [{"customer": "C1", "sku": "S1"}]}
An empty scope means every forecast leaf. excludeLeaves records leaves a
manager excluded while reviewing flags; they never receive this lever.

Levers (cLeverType / fLeverValue), applied per week:
    PERCENT         target = forecast x (1 + value/100)    (value 5 = +5%)
    ABSOLUTE_DELTA  add `value` to the scope's weekly total, split across
                    leaves pro rata to their forecast share
    FIXED_QTY       set the scope's weekly total to `value`, split pro rata
A week whose in-scope forecast total is 0 cannot be split -- it is skipped
and reported as a warning.

Several ACTIVE scenarios (revised 2026-11-06, see resolver.py): every
scenario is a target on a node. A target inside another one is pinned and
the outer target's total still holds -- the rest of the outer node is
rebalanced by forecast share. Overrides and locked rows are pinned too.
Targets inside a node that add up to more than the node's own target are a
conflict, which blocks approval. All weeks are solved by resolver.resolve_week
and written by ScenarioService._rebuild.

Target rows:
  fForecastQty   snapshot of the active forecast at materialisation.
  fTargetQty     the EFFECTIVE target (override if present, else the
                 stacked scenario target). Every downstream view reads this.
  fOverrideQty   the manager's manual value (NULL = no override).
  cSource        OVERRIDE | SCENARIO | FORECAST.
  bIsFlagged / cFlagReasonsJson  reasonableness flags on the stacked
                 scenario target.
  Locked rows (bIsLocked=1) are never touched by (re)materialisation.
  Weeks before the as-of week are never touched.

Approval rule: every flagged leaf (customer x SKU) must be individually
reviewed -- ACCEPT (keep the target) or EXCLUDE (drop the leaf from this
scenario). Reviews are written to _tb_SOForecastAudit.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from . import config, weeks
from .reasonableness import ReasonablenessFlag, evaluate_flags
from .resolver import MEASURE_UNITS, MEASURES, Conflict, GoalSpec, WeekResolution, resolve_week

_WK_ST = weeks.week_start_sql("ST.TxDate")

# --- constants ---------------------------------------------------------------

STATUS_DRAFT = "DRAFT"
STATUS_PREVIEW = "PREVIEW"
STATUS_ACTIVE = "ACTIVE"
STATUS_COMPLETED = "COMPLETED"
STATUS_CANCELLED = "CANCELLED"

LEVER_PERCENT = "PERCENT"
LEVER_ABSOLUTE_DELTA = "ABSOLUTE_DELTA"
LEVER_FIXED_QTY = "FIXED_QTY"
LEVER_TYPES = (LEVER_PERCENT, LEVER_ABSOLUTE_DELTA, LEVER_FIXED_QTY)

SOURCE_FORECAST = "FORECAST"
SOURCE_SCENARIO = "SCENARIO"
SOURCE_OVERRIDE = "OVERRIDE"

REVIEW_ACCEPT = "ACCEPT"
REVIEW_EXCLUDE = "EXCLUDE"

# SQL Server allows 2100 parameters per statement; stay well under.
_IN_CHUNK = 900
_MAX_SKU_FILTER = 400   # above this, history is loaded per customer and filtered in Python
_MAX_PERIOD_WEEKS = 53
_MERGE_BATCH = 100   # rows per MERGE ... USING (VALUES ...) -- 12 params each

from .scenarios_scope import (  # noqa: E402,F401  (re-exported)
    _SCOPE_DIMS, ForecastLeaf, LeafKey, LeafWeekKey, ScenarioError, ScenarioScope, _norm,
)


# --- pure cascade ------------------------------------------------------------

@dataclass(frozen=True)
class Effect:
    """One scenario's effect on one leaf-week."""
    scenario_id: int
    kind: str      # 'MULT' | 'ADD' | 'SET'
    amount: float


def compute_lever_effects(
    scenario_id: int,
    lever_type: str,
    lever_value: float,
    leaves: list[ForecastLeaf],
) -> tuple[dict[LeafWeekKey, Effect], list[str]]:
    """Turn a scenario lever into per-leaf-week effects. Pure.

    `leaves` must already be scoped (and exclusions removed).
    Returns (effects, warnings).
    """
    effects: dict[LeafWeekKey, Effect] = {}
    warnings: list[str] = []

    if lever_type == LEVER_PERCENT:
        factor = 1.0 + lever_value / 100.0
        for lf in leaves:
            effects[lf.key] = Effect(scenario_id, "MULT", factor)
        return effects, warnings

    if lever_type not in (LEVER_ABSOLUTE_DELTA, LEVER_FIXED_QTY):
        raise ScenarioError(f"Unknown lever type {lever_type!r}")

    kind = "ADD" if lever_type == LEVER_ABSOLUTE_DELTA else "SET"
    by_week: dict[date, list[ForecastLeaf]] = defaultdict(list)
    for lf in leaves:
        by_week[lf.week].append(lf)

    for wk in sorted(by_week):
        week_leaves = by_week[wk]
        total = sum(max(lf.forecast_qty, 0.0) for lf in week_leaves)
        if total <= 0:
            warnings.append(
                f"Week {wk.isoformat()}: in-scope forecast total is 0, so "
                f"{lever_type} {lever_value:g} could not be split across leaves"
            )
            continue
        for lf in week_leaves:
            share = max(lf.forecast_qty, 0.0) / total
            effects[lf.key] = Effect(scenario_id, kind, lever_value * share)
    return effects, warnings


def apply_effects(base_qty: float, effects: Iterable[Effect]) -> float:
    """Stack effects (in idScenario order) on a base forecast. Pure.

    Never negative; rounded to the DECIMAL(18,4) storage precision.
    """
    qty = base_qty
    for e in sorted(effects, key=lambda x: x.scenario_id):
        if e.kind == "MULT":
            qty *= e.amount
        elif e.kind == "ADD":
            qty += e.amount
        elif e.kind == "SET":
            qty = e.amount
        else:
            raise ValueError(f"Unknown effect kind {e.kind!r}")
        qty = max(qty, 0.0)
    return round(qty, 4)


@dataclass
class ExistingTarget:
    id: int
    override_qty: Optional[float]
    is_locked: bool
    scenario_ids: list[int]
    forecast_qty: float
    forecast_id: Optional[int]
    route: Optional[str] = None
    dist_centre: Optional[str] = None
    category: Optional[str] = None
    target_qty: float = 0.0                 # effective fTargetQty


@dataclass
class FlagInputs:
    history: dict[LeafKey, dict[date, float]] = field(default_factory=dict)
    sku_first_sale: dict[str, Optional[date]] = field(default_factory=dict)
    pack_size: dict[str, int] = field(default_factory=dict)
    history_start: Optional[date] = None    # earliest invoice on record


@dataclass
class CascadedTarget:
    leaf: ForecastLeaf
    target_qty: float                 # stacked scenario target (pre-override)
    scenario_ids: list[int]
    flags: list[ReasonablenessFlag]
    existing_id: Optional[int] = None
    override_qty: Optional[float] = None
    before_qty: Optional[float] = None   # target before this scenario (preview)
    in_scope: bool = True                # False = outside the scenario, rebalanced by it

    @property
    def effective_target_qty(self) -> float:
        return self.override_qty if self.override_qty is not None else self.target_qty

    @property
    def is_flagged(self) -> bool:
        return bool(self.flags)

    @property
    def flag_codes(self) -> list[str]:
        return [f.code for f in self.flags]


@dataclass
class FlaggedLeaf:
    customer: str
    sku: str
    flag_codes: list[str]
    weeks: list[date]
    messages: list[str]


@dataclass
class CascadeResult:
    scenario_id: int
    as_of_week: date
    weeks: list[date]
    targets: list[CascadedTarget] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    locked_skipped: int = 0
    conflicts: list[Conflict] = field(default_factory=list)
    measure: str = MEASURE_UNITS

    @property
    def _scoped(self) -> list[CascadedTarget]:
        return [t for t in self.targets if t.in_scope]

    @property
    def total_forecast_qty(self) -> float:
        return round(sum(t.leaf.forecast_qty for t in self._scoped), 4)

    @property
    def total_target_qty(self) -> float:
        return round(sum(t.target_qty for t in self._scoped), 4)

    @property
    def total_effective_target_qty(self) -> float:
        return round(sum(t.effective_target_qty for t in self._scoped), 4)

    @property
    def total_before_qty(self) -> float:
        return round(sum(t.before_qty if t.before_qty is not None else t.leaf.forecast_qty
                         for t in self._scoped), 4)

    @property
    def rebalanced(self) -> list[CascadedTarget]:
        """Customer-products outside the scenario whose target it moves
        (they share what's left of an outer target)."""
        return [t for t in self.targets if not t.in_scope]

    @property
    def rebalanced_delta_qty(self) -> float:
        return round(sum(t.effective_target_qty - (t.before_qty or 0.0) for t in self.rebalanced), 4)

    @property
    def leaf_count(self) -> int:
        return len({(t.leaf.customer, t.leaf.sku) for t in self._scoped})

    @property
    def flagged_leaves(self) -> list[FlaggedLeaf]:
        grouped: dict[LeafKey, FlaggedLeaf] = {}
        for t in self.targets:
            if not t.flags:
                continue
            k = (t.leaf.customer, t.leaf.sku)
            fl = grouped.setdefault(k, FlaggedLeaf(k[0], k[1], [], [], []))
            for f in t.flags:
                if f.code not in fl.flag_codes:
                    fl.flag_codes.append(f.code)
                fl.messages.append(f"{t.leaf.week.isoformat()}: {f.message}")
            fl.weeks.append(t.leaf.week)
        return [grouped[k] for k in sorted(grouped)]

    @property
    def flagged_leaf_keys(self) -> set[LeafKey]:
        return {(t.leaf.customer, t.leaf.sku) for t in self.targets if t.flags}


def build_cascade(
    *,
    scenario_id: int,
    lever_type: str,
    lever_value: float,
    leaves: list[ForecastLeaf],
    other_effects: dict[LeafWeekKey, list[Effect]],
    existing: dict[LeafWeekKey, ExistingTarget],
    flag_inputs: FlagInputs,
    as_of_week: date,
    weeks_in_period: list[date],
) -> CascadeResult:
    """Compute the stacked, flagged targets for one scenario. Pure.

    `leaves`        this scenario's in-scope active forecasts (exclusions
                    already removed), weeks >= as_of_week only.
    `other_effects` effects of the OTHER active scenarios on those keys.
    `existing`      current _tb_SOScenarioTarget rows for those keys.
    """
    result = CascadeResult(scenario_id=scenario_id, as_of_week=as_of_week,
                           weeks=[w for w in weeks_in_period if w >= as_of_week])

    past = [w for w in weeks_in_period if w < as_of_week]
    if past:
        result.warnings.append(
            f"{len(past)} week(s) before {as_of_week.isoformat()} are in the past "
            f"and were skipped"
        )
    weeks_with_forecast = {lf.week for lf in leaves}
    missing = [w for w in result.weeks if w not in weeks_with_forecast]
    if missing:
        result.warnings.append(
            "No active forecasts in scope for week(s): "
            + ", ".join(w.isoformat() for w in missing)
            + " -- run the forecast for these weeks first"
        )

    effects, lever_warnings = compute_lever_effects(scenario_id, lever_type, lever_value, leaves)
    result.warnings.extend(lever_warnings)

    for lf in sorted(leaves, key=lambda x: (x.week, x.customer, x.sku)):
        own = effects.get(lf.key)
        if own is None:
            continue   # e.g. week with a zero scope total
        ex = existing.get(lf.key)
        if ex is not None and ex.is_locked:
            result.locked_skipped += 1
            continue
        stack = [e for e in other_effects.get(lf.key, []) if e.scenario_id != scenario_id]
        stack.append(own)
        target = apply_effects(lf.forecast_qty, stack)
        flags = evaluate_flags(
            target_qty=target,
            forecast_qty=lf.forecast_qty,
            history=flag_inputs.history.get((lf.customer, lf.sku), {}),
            forecast_week=lf.week,
            as_of_week=as_of_week,
            pack_size=flag_inputs.pack_size.get(lf.sku, 1),
            sku_first_sale=flag_inputs.sku_first_sale.get(lf.sku),
            history_start=flag_inputs.history_start,
        )
        result.targets.append(CascadedTarget(
            leaf=lf,
            target_qty=target,
            scenario_ids=sorted({e.scenario_id for e in stack}),
            flags=flags,
            existing_id=ex.id if ex else None,
            override_qty=ex.override_qty if ex else None,
        ))

    if result.locked_skipped:
        result.warnings.append(
            f"{result.locked_skipped} leaf-week(s) are locked (week started) and were left unchanged"
        )
    return result


def expand_group_decisions(
    flagged: list[FlaggedLeaf],
    reviews: dict[LeafKey, tuple[str, Optional[str]]],
    group_decisions: dict[str, str],
) -> dict[LeafKey, tuple[str, Optional[str]]]:
    """Fill in a review for every flagged customer-product the manager
    didn't decide individually, from the decision on its warning group(s).
    Pure. An individual decision always wins. With several warnings: any
    EXCLUDE group excludes it; it's accepted only when every one of its
    groups is ACCEPT; otherwise it stays unreviewed."""
    out = dict(reviews)
    decided = {(_norm(c), _norm(s)) for c, s in reviews}
    for fl in flagged:
        if (_norm(fl.customer), _norm(fl.sku)) in decided:
            continue
        ds = [group_decisions.get(code) for code in fl.flag_codes]
        if REVIEW_EXCLUDE in ds:
            out[(fl.customer, fl.sku)] = (REVIEW_EXCLUDE, "group decision")
        elif ds and all(d == REVIEW_ACCEPT for d in ds):
            out[(fl.customer, fl.sku)] = (REVIEW_ACCEPT, "group decision")
    return out


def validate_reviews(
    flagged: set[LeafKey],
    reviews: dict[LeafKey, str],
) -> list[LeafKey]:
    """Return the flagged leaves that have no review decision. Pure."""
    reviewed = {(_norm(c), _norm(s)) for c, s in reviews}
    return sorted(k for k in flagged if (_norm(k[0]), _norm(k[1])) not in reviewed)


# --- DB loaders ----------------------------------------------------------------

def _chunks(seq: list, size: int = _IN_CHUNK) -> Iterable[list]:
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _as_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    return v


def load_active_forecasts(
    db: Session,
    scope: ScenarioScope,
    week_list: list[date],
) -> list[ForecastLeaf]:
    """Active forecasts for `week_list` matching `scope` (exclusions removed).

    One row per leaf-week: if several runs left bIsActive=1 rows (older
    data written before the runner deactivated superseded rows), the
    latest run wins. Scope is applied AFTER picking the latest row so a
    re-routed customer is judged by its current attribution.
    """
    if not week_list:
        return []
    filters = scope.dim_filters()
    where = []
    params: dict = {"weeks": list(week_list)}
    binds = [bindparam("weeks", expanding=True)]
    for i, (col, vals) in enumerate(filters.items()):
        name = f"d{i}"
        where.append(f"r.{col} IN :{name}")
        params[name] = list(vals)
        binds.append(bindparam(name, expanding=True))

    sql = f"""
        WITH ranked AS (
            SELECT f.idForecast, f.cCustomerAccount, f.cStockCode, f.dWeekStart,
                   f.cRoute, f.cDistCentre, f.cCategory, f.cTelesales, f.cSalesRep,
                   f.fPredictedQty,
                   ROW_NUMBER() OVER (
                       PARTITION BY f.cCustomerAccount, f.cStockCode, f.dWeekStart
                       ORDER BY f.idForecastRun DESC, f.idForecast DESC) AS rn
              FROM [SOAssistant].dbo._tb_SOForecast f
             WHERE f.bIsActive = 1
               AND f.dWeekStart IN :weeks
        )
        SELECT r.* FROM ranked r
         WHERE r.rn = 1 {''.join(' AND ' + w for w in where)}
    """
    rows = db.execute(text(sql).bindparams(*binds), params).mappings().all()
    out = []
    for r in rows:
        if scope.is_excluded(r["cCustomerAccount"], r["cStockCode"]):
            continue
        out.append(ForecastLeaf(
            customer=r["cCustomerAccount"],
            sku=r["cStockCode"],
            week=_as_date(r["dWeekStart"]),
            forecast_id=int(r["idForecast"]),
            forecast_qty=float(r["fPredictedQty"]),
            route=r["cRoute"],
            dist_centre=r["cDistCentre"],
            category=r["cCategory"],
            telesales=r["cTelesales"],
            sales_rep=r["cSalesRep"],
        ))
    return out


def load_flag_inputs(db: Session, leaves: list[ForecastLeaf], as_of_week: date) -> FlagInputs:
    """Bulk-load weekly true demand, SKU launch dates and pack sizes.

    History reads PostST directly (same invoice/credit-note sign logic
    as _vw_SOCustomerInvoiceHistory) because that view only spans ~12
    weeks, and the checks need 52 weeks + same-week-last-year.
    """
    inputs = FlagInputs()
    if not leaves:
        return inputs
    wanted = {(lf.customer, lf.sku) for lf in leaves}
    customers = sorted({c for c, _ in wanted})
    skus = sorted({s for _, s in wanted})
    window_start = as_of_week - timedelta(weeks=config.TARGET_FLAG_HISTORY_LOOKBACK_WEEKS)
    filter_skus = len(skus) <= _MAX_SKU_FILTER

    history: dict[LeafKey, dict[date, float]] = defaultdict(dict)

    def add(c: str, s: str, wk, qty) -> None:
        k = (c, s)
        if k not in wanted or qty is None:
            return
        wk = weeks.week_start(_as_date(wk))
        history[k][wk] = history[k].get(wk, 0.0) + float(qty)

    for chunk in _chunks(customers):
        sku_clause = "AND STK.Code IN :skus" if filter_skus else ""
        binds = [bindparam("custs", expanding=True)]
        params: dict = {"custs": chunk, "from": window_start, "to": as_of_week}
        if filter_skus:
            binds.append(bindparam("skus", expanding=True))
            params["skus"] = skus
        rows = db.execute(text(f"""
            SELECT CL.Account AS cust, STK.Code AS sku,
                   {_WK_ST} AS wk,
                   SUM(CASE WHEN ST.Credit > 0 THEN ST.Quantity ELSE -ST.Quantity END) AS qty
              FROM [BA_00_LPD_CON].dbo.PostST ST
              JOIN [BA_00_LPD_CON].dbo.Client  CL  ON CL.DCLink     = ST.DrCrAccount
              JOIN [BA_00_LPD_CON].dbo.StkItem STK ON STK.StockLink = ST.AccountLink
             WHERE ST.Id IN ('Crn', 'OInv', 'Inv')
               AND ST.TxDate >= :from AND ST.TxDate < :to
               AND CL.Account IN :custs {sku_clause}
             GROUP BY CL.Account, STK.Code,
                      {_WK_ST}
        """).bindparams(*binds), params).mappings().all()
        for r in rows:
            add(r["cust"], r["sku"], r["wk"], r["qty"])

        # Correction 3: add short-delivered qty back -> true demand.
        rows = db.execute(text("""
            SELECT cCustomerAccount AS cust, cStockCode AS sku,
                   dDeliveryDate AS d, fShortfallQty AS qty
              FROM [SOAssistant].dbo._tb_SOShortDelivery
             WHERE cCustomerAccount IN :custs
               AND dDeliveryDate >= :from AND dDeliveryDate < :to
        """).bindparams(bindparam("custs", expanding=True)),
            {"custs": chunk, "from": window_start, "to": as_of_week}).mappings().all()
        for r in rows:
            add(r["cust"], r["sku"], r["d"], r["qty"])

    # Credit notes can push a week negative; demand can't be < 0.
    inputs.history = {k: {wk: max(q, 0.0) for wk, q in v.items()} for k, v in history.items()}

    for chunk in _chunks(skus):
        rows = db.execute(text("""
            SELECT STK.Code AS sku, MIN(ST.TxDate) AS first_sale
              FROM [BA_00_LPD_CON].dbo.PostST ST
              JOIN [BA_00_LPD_CON].dbo.StkItem STK ON STK.StockLink = ST.AccountLink
             WHERE ST.Id IN ('OInv', 'Inv')
               AND STK.Code IN :skus
             GROUP BY STK.Code
        """).bindparams(bindparam("skus", expanding=True)), {"skus": chunk}).mappings().all()
        for r in rows:
            inputs.sku_first_sale[r["sku"]] = _as_date(r["first_sale"])

        rows = db.execute(text("""
            SELECT Code AS sku, ISNULL(ulIIPerCrate, 1) AS pack
              FROM [BA_00_LPD_CON].dbo.StkItem
             WHERE Code IN :skus
        """).bindparams(bindparam("skus", expanding=True)), {"skus": chunk}).mappings().all()
        for r in rows:
            inputs.pack_size[r["sku"]] = int(r["pack"] or 1)

    for s in skus:
        inputs.sku_first_sale.setdefault(s, None)

    inputs.history_start = _as_date(db.execute(text("""
        SELECT MIN(TxDate) FROM [BA_00_LPD_CON].dbo.PostST WHERE Id IN ('OInv', 'Inv')
    """)).scalar())
    return inputs


def _parse_ids(raw: Optional[str]) -> list[int]:
    if not raw:
        return []
    try:
        return sorted({int(x) for x in json.loads(raw)})
    except (ValueError, TypeError):
        return []


def _ids_json(ids: Iterable[int]) -> str:
    return json.dumps(sorted(set(ids)), separators=(",", ":"))


def load_existing_targets(
    db: Session,
    keys: Iterable[LeafWeekKey],
) -> dict[LeafWeekKey, ExistingTarget]:
    keys = set(keys)
    if not keys:
        return {}
    week_list = sorted({k[2] for k in keys})
    customers = sorted({k[0] for k in keys})
    out: dict[LeafWeekKey, ExistingTarget] = {}
    for chunk in _chunks(customers):
        rows = db.execute(text(f"""
            SELECT {_TARGET_COLS}
              FROM [SOAssistant].dbo._tb_SOScenarioTarget
             WHERE cCustomerAccount IN :custs AND dWeekStart IN :weeks
        """).bindparams(bindparam("custs", expanding=True), bindparam("weeks", expanding=True)),
            {"custs": chunk, "weeks": week_list}).mappings().all()
        for r in rows:
            k = (r["cCustomerAccount"], r["cStockCode"], _as_date(r["dWeekStart"]))
            if k in keys:
                out[k] = _existing_from_row(r)
    return out


def load_targets_for_weeks(db: Session, week_list: Iterable[date]) -> dict[LeafWeekKey, ExistingTarget]:
    """Every _tb_SOScenarioTarget row in `week_list`."""
    week_list = sorted(set(week_list))
    if not week_list:
        return {}
    rows = db.execute(text(f"""
        SELECT {_TARGET_COLS}
          FROM [SOAssistant].dbo._tb_SOScenarioTarget
         WHERE dWeekStart IN :weeks
    """).bindparams(bindparam("weeks", expanding=True)), {"weeks": week_list}).mappings().all()
    return {(r["cCustomerAccount"], r["cStockCode"], _as_date(r["dWeekStart"])): _existing_from_row(r)
            for r in rows}


_TARGET_COLS = """idScenarioTarget, cCustomerAccount, cStockCode, dWeekStart,
                   fOverrideQty, bIsLocked, cScenariosAppliedJson, fTargetQty,
                   fForecastQty, idForecastSnapshot, cRoute, cDistCentre, cCategory"""


def _existing_from_row(r) -> ExistingTarget:
    return ExistingTarget(
        id=int(r["idScenarioTarget"]),
        override_qty=float(r["fOverrideQty"]) if r["fOverrideQty"] is not None else None,
        is_locked=bool(r["bIsLocked"]),
        scenario_ids=_parse_ids(r["cScenariosAppliedJson"]),
        forecast_qty=float(r["fForecastQty"]),
        forecast_id=r["idForecastSnapshot"],
        route=r["cRoute"], dist_centre=r["cDistCentre"], category=r["cCategory"],
        target_qty=float(r["fTargetQty"]) if r["fTargetQty"] is not None else 0.0,
    )


# --- scenario record ---------------------------------------------------------

@dataclass
class Scenario:
    id: int
    name: str
    description: Optional[str]
    scope: ScenarioScope
    lever_type: str
    lever_value: float
    period_from: date
    period_to: date
    status: str
    created_by: str
    created_at: datetime
    approved_by: Optional[str]
    approved_at: Optional[datetime]
    cancel_reason: Optional[str]
    cancelled_at: Optional[datetime]
    measure: str = MEASURE_UNITS
    anchor_level: Optional[str] = None

    @property
    def period_weeks(self) -> list[date]:
        return weeks.weeks_between(self.period_from, self.period_to)

    def state(self) -> dict:
        """JSON-able snapshot for the audit log."""
        return {
            "name": self.name, "description": self.description,
            "scope": self.scope.to_dict(), "leverType": self.lever_type,
            "leverValue": self.lever_value,
            "periodFrom": self.period_from.isoformat(),
            "periodTo": self.period_to.isoformat(), "status": self.status,
            "measure": self.measure, "anchorLevel": self.anchor_level,
        }

    @classmethod
    def from_row(cls, r) -> "Scenario":
        return cls(
            id=int(r["idScenario"]), name=r["cName"], description=r["cDescription"],
            scope=ScenarioScope.from_json(r["cScopeJson"]),
            lever_type=r["cLeverType"], lever_value=float(r["fLeverValue"]),
            period_from=_as_date(r["dPeriodFrom"]), period_to=_as_date(r["dPeriodTo"]),
            status=r["cStatus"], created_by=r["cCreatedBy"], created_at=r["dtCreatedAt"],
            approved_by=r["cApprovedBy"], approved_at=r["dtApprovedAt"],
            cancel_reason=r["cCancelReason"], cancelled_at=r["dtCancelledAt"],
            measure=(r.get("cMeasure") or MEASURE_UNITS), anchor_level=r.get("cAnchorLevel"),
        )


def validate_definition(lever_type: str, lever_value: float, period_from: date, period_to: date,
                        measure: str = MEASURE_UNITS) -> None:
    if measure not in MEASURES:
        raise ScenarioError(f"measure must be one of {', '.join(MEASURES)}")
    if lever_type not in LEVER_TYPES:
        raise ScenarioError(f"lever_type must be one of {', '.join(LEVER_TYPES)}")
    if lever_type == LEVER_PERCENT and lever_value <= -100:
        raise ScenarioError("A PERCENT lever must be greater than -100")
    if lever_type == LEVER_FIXED_QTY and lever_value < 0:
        raise ScenarioError("A FIXED_QTY lever cannot be negative")
    if period_to < period_from:
        raise ScenarioError("period_to must be on or after period_from")
    if len(weeks.weeks_between(period_from, period_to)) > _MAX_PERIOD_WEEKS:
        raise ScenarioError(f"A scenario period can span at most {_MAX_PERIOD_WEEKS} weeks")


# --- service -------------------------------------------------------------------

class ScenarioService:
    """DB-coupled scenario + target operations. Methods that write commit
    their own transaction (one per business action) and roll back on error."""

    def __init__(self, db: Session, username: str):
        self._db = db
        self._username = username

    # -- audit ------------------------------------------------------------------

    def _audit(self, object_type: str, object_id: int, action: str,
               prev: Optional[dict], new: Optional[dict]) -> None:
        from services.audit import _resolve_agent_id
        self._db.execute(text("""
            INSERT INTO [SOAssistant].dbo._tb_SOForecastAudit
                (cObjectType, iObjectId, cAction, cPrevStateJson, cNewStateJson,
                 cUsername, iAgentID)
            VALUES (:t, :id, :a, :prev, :new, :u, :agent)
        """), {
            "t": object_type, "id": object_id, "a": action,
            "prev": json.dumps(prev, default=str) if prev is not None else None,
            "new": json.dumps(new, default=str) if new is not None else None,
            "u": self._username, "agent": _resolve_agent_id(self._db, self._username),
        })

    def _run(self, fn):
        try:
            out = fn()
            self._db.commit()
            return out
        except Exception:
            self._db.rollback()
            raise

    # -- CRUD -------------------------------------------------------------------

    def get(self, scenario_id: int, lock: bool = False) -> Scenario:
        hint = "WITH (UPDLOCK, ROWLOCK)" if lock else ""
        row = self._db.execute(text(f"""
            SELECT * FROM [SOAssistant].dbo._tb_SOScenario {hint}
             WHERE idScenario = :id
        """), {"id": scenario_id}).mappings().first()
        if not row:
            raise ScenarioError(f"Scenario {scenario_id} not found", 404)
        return Scenario.from_row(row)

    def list_scenarios(self, status: Optional[str] = None) -> list[Scenario]:
        sql = "SELECT * FROM [SOAssistant].dbo._tb_SOScenario"
        params = {}
        if status:
            sql += " WHERE cStatus = :status"
            params["status"] = status
        sql += " ORDER BY dtCreatedAt DESC, idScenario DESC"
        return [Scenario.from_row(r) for r in self._db.execute(text(sql), params).mappings().all()]

    def create(self, *, name: str, description: Optional[str], scope: ScenarioScope,
               lever_type: str, lever_value: float, period_from: date, period_to: date,
               measure: str = MEASURE_UNITS, anchor_level: Optional[str] = None) -> Scenario:
        validate_definition(lever_type, lever_value, period_from, period_to, measure)

        def go():
            row = self._db.execute(text("""
                INSERT INTO [SOAssistant].dbo._tb_SOScenario
                    (cName, cDescription, cScopeJson, cLeverType, fLeverValue,
                     dPeriodFrom, dPeriodTo, cStatus, cCreatedBy, cMeasure, cAnchorLevel)
                OUTPUT inserted.idScenario
                VALUES (:name, :descr, :scope, :lt, :lv, :pf, :pt, 'DRAFT', :by, :m, :anchor)
            """), {"name": name, "descr": description, "scope": scope.to_json(),
                   "lt": lever_type, "lv": lever_value, "pf": period_from,
                   "pt": period_to, "by": self._username, "m": measure,
                   "anchor": anchor_level}).mappings().first()
            sid = int(row["idScenario"])
            sc = self.get(sid)
            self._audit("SCENARIO", sid, "CREATE", None, sc.state())
            return sc
        return self._run(go)

    def update(self, scenario_id: int, changes: dict) -> Scenario:
        """Edit a DRAFT/PREVIEW scenario. Any edit returns it to DRAFT so a
        fresh preview (and flag review) is required before approval."""
        def go():
            sc = self.get(scenario_id, lock=True)
            if sc.status not in (STATUS_DRAFT, STATUS_PREVIEW):
                raise ScenarioError(f"Only DRAFT or PREVIEW scenarios can be edited "
                                    f"(this one is {sc.status})", 409)
            prev = sc.state()
            name = changes.get("name", sc.name)
            descr = changes.get("description", sc.description)
            scope = changes.get("scope", sc.scope)
            lt = changes.get("lever_type", sc.lever_type)
            lv = changes.get("lever_value", sc.lever_value)
            pf = changes.get("period_from", sc.period_from)
            pt = changes.get("period_to", sc.period_to)
            m = changes.get("measure") or sc.measure
            validate_definition(lt, lv, pf, pt, m)
            self._db.execute(text("""
                UPDATE [SOAssistant].dbo._tb_SOScenario
                   SET cName = :name, cDescription = :descr, cScopeJson = :scope,
                       cLeverType = :lt, fLeverValue = :lv,
                       dPeriodFrom = :pf, dPeriodTo = :pt, cMeasure = :m, cStatus = 'DRAFT'
                 WHERE idScenario = :id
            """), {"id": scenario_id, "name": name, "descr": descr, "scope": scope.to_json(),
                   "lt": lt, "lv": lv, "pf": pf, "pt": pt, "m": m})
            updated = self.get(scenario_id)
            self._audit("SCENARIO", scenario_id, "UPDATE", prev, updated.state())
            return updated
        return self._run(go)

    # -- cascade ------------------------------------------------------------------

    def _active_scenarios(self, week_list: list[date], exclude_id: Optional[int] = None) -> list[Scenario]:
        if not week_list:
            return []
        rows = self._db.execute(text("""
            SELECT * FROM [SOAssistant].dbo._tb_SOScenario
             WHERE cStatus = 'ACTIVE'
               AND dPeriodFrom <= :last AND dPeriodTo >= :first
        """), {"first": min(week_list), "last": max(week_list) + timedelta(days=6)}).mappings().all()
        return [Scenario.from_row(r) for r in rows if int(r["idScenario"]) != exclude_id]

    # -- resolution -------------------------------------------------------------

    @staticmethod
    def _goal(sc: Scenario) -> GoalSpec:
        return GoalSpec(sc.id, sc.scope, sc.lever_type, sc.lever_value, sc.measure, sc.name)

    def _resolve(
        self,
        week_list: list[date],
        scenarios: list[Scenario],
        existing: dict[LeafWeekKey, ExistingTarget],
        unlock_keys: frozenset = frozenset(),
    ) -> dict[date, tuple[WeekResolution, dict[LeafWeekKey, ForecastLeaf]]]:
        """Solve every week in `week_list` with `scenarios` active. Overrides
        and locked rows in `existing` are pinned (except `unlock_keys`,
        whose lock is being broken -- they're re-solved like any other)."""
        wanted = set(week_list)
        leaves: dict[date, dict[LeafWeekKey, ForecastLeaf]] = {w: {} for w in wanted}
        goals: dict[date, list[GoalSpec]] = {w: [] for w in wanted}
        for sc in scenarios:
            sc_weeks = [w for w in sc.period_weeks if w in wanted]
            if not sc_weeks:
                continue
            for lf in load_active_forecasts(self._db, sc.scope, sc_weeks):
                leaves[lf.week][lf.key] = lf
            for w in sc_weeks:
                goals[w].append(self._goal(sc))

        pinned: dict[date, dict[LeafWeekKey, float]] = {w: {} for w in wanted}
        for k, ex in existing.items():
            w = k[2]
            if w not in wanted:
                continue
            if ex.is_locked and k not in unlock_keys:
                qty = ex.target_qty
            elif ex.override_qty is not None:
                qty = ex.override_qty
            else:
                continue
            pinned[w][k] = qty
            if k not in leaves[w]:
                leaves[w][k] = ForecastLeaf(k[0], k[1], w, ex.forecast_id, ex.forecast_qty,
                                            route=ex.route, dist_centre=ex.dist_centre,
                                            category=ex.category)

        factors: dict[str, float] = {}
        if any(g.measure != MEASURE_UNITS for gs in goals.values() for g in gs):
            from .workbench import load_litre_factors
            factors = load_litre_factors(self._db, sorted({k[1] for lw in leaves.values() for k in lw}))

        out = {}
        for w in sorted(wanted):
            lw = leaves[w]
            out[w] = (resolve_week(w, list(lw.values()), goals[w], pinned[w], factors), lw)
        return out

    def _cascade(self, sc: Scenario, as_of: Optional[date]) -> CascadeResult:
        """What approving `sc` would do: every week of its period is solved
        without it and with it. The result lists its customer-products plus
        any outside it whose target it moves (rebalanced siblings)."""
        as_of_week = weeks.week_start(as_of or date.today())
        period = sc.period_weeks
        live = [w for w in period if w >= as_of_week]
        result = CascadeResult(scenario_id=sc.id, as_of_week=as_of_week, weeks=live, measure=sc.measure)
        past = [w for w in period if w < as_of_week]
        if past:
            result.warnings.append(f"{len(past)} week(s) before {as_of_week.isoformat()} are in the past "
                                   f"and were skipped")
        if not live:
            return result

        existing = load_targets_for_weeks(self._db, live)
        others = self._active_scenarios(live, exclude_id=sc.id)
        before = self._resolve(live, others, existing)
        after = self._resolve(live, others + [sc], existing)

        missing = [w for w in live if not any(sc.scope.matches(lf) for lf in after[w][1].values())]
        if missing:
            result.warnings.append("No active forecasts in scope for week(s): "
                                   + ", ".join(w.isoformat() for w in missing)
                                   + " -- run the forecast for these weeks first")

        rows: list[tuple[ForecastLeaf, float, list[int], float, bool]] = []
        for w in live:
            res_b, _ = before[w]
            res_a, lw = after[w]
            for c in res_a.conflicts:
                if not any(c.scenario_id == b.scenario_id for b in res_b.conflicts):
                    result.conflicts.append(c)
            for msg in res_a.warnings:
                if msg not in result.warnings:
                    result.warnings.append(msg)
            for key, rl in res_a.values.items():
                lf = lw[key]
                ex = existing.get(key)
                in_scope = sc.scope.matches(lf)
                if ex is not None and ex.is_locked:
                    result.locked_skipped += 1 if in_scope else 0
                    continue
                prev = res_b.values.get(key)
                before_qty = prev.qty if prev is not None else round(max(lf.forecast_qty, 0.0), 4)
                if in_scope or abs(rl.qty - before_qty) > 1e-4:
                    rows.append((lf, rl.qty, rl.scenario_ids, before_qty, in_scope))

        flag_inputs = load_flag_inputs(self._db, [r[0] for r in rows], as_of_week)
        for lf, qty, ids, before_qty, in_scope in sorted(rows, key=lambda r: (r[0].week, r[0].customer, r[0].sku)):
            ex = existing.get(lf.key)
            result.targets.append(CascadedTarget(
                leaf=lf, target_qty=qty, scenario_ids=ids,
                flags=self._flags(lf, qty, flag_inputs, as_of_week),
                existing_id=ex.id if ex else None,
                override_qty=ex.override_qty if ex else None,
                before_qty=before_qty, in_scope=in_scope,
            ))
        if result.locked_skipped:
            result.warnings.append(f"{result.locked_skipped} customer-product week(s) are locked "
                                   f"(week started) and keep their target")
        return result

    @staticmethod
    def _flags(lf: ForecastLeaf, qty: float, fi: FlagInputs, as_of_week: date) -> list[ReasonablenessFlag]:
        return evaluate_flags(
            target_qty=qty, forecast_qty=lf.forecast_qty,
            history=fi.history.get((lf.customer, lf.sku), {}), forecast_week=lf.week,
            as_of_week=as_of_week, pack_size=fi.pack_size.get(lf.sku, 1),
            sku_first_sale=fi.sku_first_sale.get(lf.sku), history_start=fi.history_start,
        )

    def _rebuild(self, week_list: Iterable[date], as_of_week: date,
                 unlock_keys: frozenset = frozenset(),
                 only_keys: Optional[set] = None) -> tuple[int, int]:
        """Re-solve `week_list` from the ACTIVE scenarios and write the result:
        upsert changed rows, delete rows nothing needs any more. Weeks before
        `as_of_week` are left alone unless `only_keys` is given (used to clear
        an override in a past week without touching its neighbours). Locked
        rows are never written, except `unlock_keys`.
        Returns (rows_written, rows_deleted)."""
        wl = sorted(set(week_list))
        if only_keys is None:
            wl = [w for w in wl if w >= as_of_week]
        if not wl:
            return 0, 0
        existing = load_targets_for_weeks(self._db, wl)
        resolved = self._resolve(wl, self._active_scenarios(wl), existing, unlock_keys)

        desired: set[LeafWeekKey] = set()
        changed: list[tuple[ForecastLeaf, float, list[int]]] = []
        for w, (res, lw) in resolved.items():
            for key, rl in res.values.items():
                if only_keys is not None and key not in only_keys:
                    continue
                ex = existing.get(key)
                unlocking = key in unlock_keys and ex is not None
                if ex is not None and ex.is_locked and not unlocking:
                    desired.add(key)
                    continue
                if not (rl.scenario_ids or (ex is not None and ex.override_qty is not None) or unlocking):
                    continue
                desired.add(key)
                lf = lw[key]
                if (ex is None or abs(ex.target_qty - rl.qty) > 1e-4
                        or sorted(ex.scenario_ids) != rl.scenario_ids
                        or abs(ex.forecast_qty - lf.forecast_qty) > 1e-4):
                    changed.append((lf, rl.qty, rl.scenario_ids))

        fi = load_flag_inputs(self._db, [c[0] for c in changed], as_of_week)
        normal, unlocked = [], []
        for lf, qty, ids in changed:
            ex = existing.get(lf.key)
            t = CascadedTarget(lf, qty, ids, self._flags(lf, qty, fi, as_of_week),
                               ex.id if ex else None, ex.override_qty if ex else None)
            (unlocked if lf.key in unlock_keys else normal).append(t)
        written = self._write_targets(normal)
        if unlocked:
            written += self._write_targets(unlocked, allow_locked=True)

        stale = [ex.id for k, ex in existing.items()
                 if k not in desired and not ex.is_locked
                 and (only_keys is None or k in only_keys)]
        for chunk in _chunks(stale):
            self._db.execute(text("""
                DELETE FROM [SOAssistant].dbo._tb_SOScenarioTarget
                 WHERE idScenarioTarget IN :ids AND bIsLocked = 0
            """).bindparams(bindparam("ids", expanding=True)), {"ids": chunk})
        return written, len(stale)

    def preview(self, scenario_id: int, as_of: Optional[date] = None) -> tuple[Scenario, CascadeResult]:
        """Compute the cascade without writing targets. DRAFT -> PREVIEW;
        an ACTIVE scenario can be previewed read-only."""
        def go():
            sc = self.get(scenario_id, lock=True)
            if sc.status in (STATUS_CANCELLED, STATUS_COMPLETED):
                raise ScenarioError(f"Scenario is {sc.status}", 409)
            result = self._cascade(sc, as_of)
            if sc.status == STATUS_DRAFT:
                self._db.execute(text("""
                    UPDATE [SOAssistant].dbo._tb_SOScenario SET cStatus = 'PREVIEW'
                     WHERE idScenario = :id
                """), {"id": scenario_id})
                sc.status = STATUS_PREVIEW
            return sc, result
        return self._run(go)

    def approve(self, scenario_id: int, reviews: dict[LeafKey, tuple[str, Optional[str]]],
                as_of: Optional[date] = None,
                group_decisions: Optional[dict[str, str]] = None) -> tuple[Scenario, CascadeResult, int]:
        """Materialise targets. `reviews` = {(customer, sku): (ACCEPT|EXCLUDE, note)}.

        Every flagged leaf must have a review -- individually or through
        `group_decisions` ({flag code: ACCEPT|EXCLUDE}). EXCLUDE removes the
        leaf from this scenario (persisted in scope.excludeLeaves). Returns
        (scenario, cascade, rows_written).
        """
        def go():
            sc = self.get(scenario_id, lock=True)
            if sc.status != STATUS_PREVIEW:
                raise ScenarioError(
                    f"Only a PREVIEW scenario can be approved (this one is {sc.status}). "
                    f"Run a preview and review its flags first.", 409)

            result = self._cascade(sc, as_of)
            if result.conflicts:
                raise ScenarioError(
                    f"{len(result.conflicts)} conflict(s) block approval", 409,
                    {"message": "This target can't be met: " + "; ".join(c.message for c in result.conflicts[:5]),
                     "conflicts": [{"scenario_id": c.scenario_id, "week": c.week.isoformat(),
                                    "goal": c.goal, "inner_total": c.inner_total,
                                    "measure": c.measure, "message": c.message}
                                   for c in result.conflicts[:50]]})
            groups = group_decisions or {}
            revs = expand_group_decisions(result.flagged_leaves, dict(reviews), groups)
            missing = validate_reviews(result.flagged_leaf_keys, dict(revs))
            if missing:
                raise ScenarioError(
                    f"{len(missing)} flagged customer-product(s) have not been reviewed", 400,
                    {"message": f"{len(missing)} flagged customer-product(s) have not been reviewed. "
                                f"Each one needs an Accept or Exclude decision.",
                     "unreviewed": [{"customer_account": c, "stock_code": s} for c, s in missing[:200]]})

            excluded = [k for k, (d, _) in revs.items() if d == REVIEW_EXCLUDE]
            if excluded:
                sc.scope = sc.scope.with_exclusions(excluded)
                self._db.execute(text("""
                    UPDATE [SOAssistant].dbo._tb_SOScenario SET cScopeJson = :scope
                     WHERE idScenario = :id
                """), {"id": sc.id, "scope": sc.scope.to_json()})
                result = self._cascade(sc, as_of)
                # Warnings that only appeared because of the exclusions are
                # covered by the group decisions too.
                revs = expand_group_decisions(result.flagged_leaves, revs, groups)
                accepted = {(_norm(c), _norm(s)) for (c, s), (d, _) in revs.items()
                            if d == REVIEW_ACCEPT}
                newly = sorted(k for k in result.flagged_leaf_keys
                               if (_norm(k[0]), _norm(k[1])) not in accepted)
                if newly:
                    raise ScenarioError(
                        "Excluding customer-products changed the split", 409,
                        {"message": f"Excluding those customer-products moved their volume onto others, "
                                    f"which raised new flags on {len(newly)} customer-product(s). "
                                    f"Preview again and review the new flags.",
                         "unreviewed": [{"customer_account": c, "stock_code": s} for c, s in newly[:200]]})

            self._db.execute(text("""
                UPDATE [SOAssistant].dbo._tb_SOScenario
                   SET cStatus = 'ACTIVE', cApprovedBy = :by, dtApprovedAt = sysdatetime()
                 WHERE idScenario = :id
            """), {"id": sc.id, "by": self._username})
            written, _ = self._rebuild(sc.period_weeks, result.as_of_week)
            flagged = {(_norm(fl.customer), _norm(fl.sku)): fl.flag_codes
                       for fl in result.flagged_leaves}
            self._audit("SCENARIO", sc.id, "APPROVE", {"status": STATUS_PREVIEW}, {
                "status": STATUS_ACTIVE,
                "rowsWritten": written,
                "totalForecastQty": result.total_forecast_qty,
                "totalTargetQty": result.total_target_qty,
                "flagReviews": [
                    {"customer": c, "sku": s, "decision": d, "note": note,
                     "flags": flagged.get((_norm(c), _norm(s)), [])}
                    for (c, s), (d, note) in sorted(revs.items())
                ],
            })
            sc.status = STATUS_ACTIVE
            sc.approved_by = self._username
            return sc, result, written
        return self._run(go)

    def cancel(self, scenario_id: int, reason: str, as_of: Optional[date] = None) -> tuple[Scenario, int, int]:
        """Cancel a scenario. If it was ACTIVE, every unlocked, current-or-
        future target it contributed to is recomputed without it (rows left
        with no scenario and no override are deleted).
        Returns (scenario, rows_updated, rows_deleted)."""
        def go():
            sc = self.get(scenario_id, lock=True)
            if sc.status in (STATUS_CANCELLED, STATUS_COMPLETED):
                raise ScenarioError(f"Scenario is already {sc.status}", 409)
            prev_status = sc.status
            updated = deleted = 0
            self._db.execute(text("""
                UPDATE [SOAssistant].dbo._tb_SOScenario
                   SET cStatus = 'CANCELLED', cCancelReason = :reason,
                       dtCancelledAt = sysdatetime()
                 WHERE idScenario = :id
            """), {"id": sc.id, "reason": reason})
            if prev_status == STATUS_ACTIVE:
                updated, deleted = self._rebuild(sc.period_weeks, weeks.week_start(as_of or date.today()))
            self._audit("SCENARIO", sc.id, "CANCEL", {"status": prev_status},
                        {"status": STATUS_CANCELLED, "reason": reason,
                         "targetsRecomputed": updated, "targetsDeleted": deleted})
            sc.status = STATUS_CANCELLED
            sc.cancel_reason = reason
            return sc, updated, deleted
        return self._run(go)

    def _write_targets(self, targets: list[CascadedTarget], allow_locked: bool = False) -> int:
        """Upsert target rows in batches (MERGE ... USING VALUES). Locked
        rows are never modified; existing overrides are preserved."""
        cols = ("cust", "sku", "wk", "route", "dc", "cat", "fid", "fq",
                "applied", "tq", "flagged", "flags")
        lock_guard = "" if allow_locked else "AND tgt.bIsLocked = 0"
        written = 0
        for batch in _chunks(targets, _MERGE_BATCH):
            values_sql = []
            params: dict = {}
            for i, t in enumerate(batch):
                values_sql.append("(" + ", ".join(f":{c}{i}" for c in cols) + ")")
                lf = t.leaf
                params.update({
                    f"cust{i}": lf.customer, f"sku{i}": lf.sku, f"wk{i}": lf.week,
                    f"route{i}": lf.route, f"dc{i}": lf.dist_centre, f"cat{i}": lf.category,
                    f"fid{i}": lf.forecast_id, f"fq{i}": round(lf.forecast_qty, 4),
                    f"applied{i}": _ids_json(t.scenario_ids), f"tq{i}": t.target_qty,
                    f"flagged{i}": 1 if t.flags else 0,
                    f"flags{i}": json.dumps(t.flag_codes) if t.flags else None,
                })
            self._db.execute(text(f"""
                MERGE [SOAssistant].dbo._tb_SOScenarioTarget WITH (HOLDLOCK) AS tgt
                USING (VALUES {', '.join(values_sql)})
                      AS src (cust, sku, wk, route, dc, cat, fid, fq, applied, tq, flagged, flags)
                   ON tgt.cCustomerAccount = src.cust
                  AND tgt.cStockCode       = src.sku
                  AND tgt.dWeekStart       = src.wk
                WHEN MATCHED {lock_guard} THEN UPDATE SET
                     cRoute = src.route, cDistCentre = src.dc, cCategory = src.cat,
                     idForecastSnapshot = src.fid, fForecastQty = src.fq,
                     cScenariosAppliedJson = src.applied,
                     fTargetQty = COALESCE(tgt.fOverrideQty, src.tq),
                     cSource = CASE WHEN tgt.fOverrideQty IS NOT NULL THEN 'OVERRIDE'
                                    WHEN src.applied = '[]' THEN 'FORECAST'
                                    ELSE 'SCENARIO' END,
                     bIsFlagged = src.flagged, cFlagReasonsJson = src.flags,
                     dtUpdatedAt = sysdatetime()
                WHEN NOT MATCHED THEN INSERT
                     (cCustomerAccount, cStockCode, dWeekStart, cRoute, cDistCentre, cCategory,
                      idForecastSnapshot, fForecastQty, cScenariosAppliedJson, fTargetQty,
                      cSource, bIsFlagged, cFlagReasonsJson)
                     VALUES (src.cust, src.sku, src.wk, src.route, src.dc, src.cat,
                             src.fid, src.fq, src.applied, src.tq,
                             CASE WHEN src.applied = '[]' THEN 'FORECAST' ELSE 'SCENARIO' END,
                             src.flagged, src.flags);
            """), params)
            written += len(batch)
        return written

    # -- targets ------------------------------------------------------------------

    def get_target(self, target_id: int, lock: bool = False) -> dict:
        hint = "WITH (UPDLOCK, ROWLOCK)" if lock else ""
        row = self._db.execute(text(f"""
            SELECT * FROM [SOAssistant].dbo._tb_SOScenarioTarget {hint}
             WHERE idScenarioTarget = :id
        """), {"id": target_id}).mappings().first()
        if not row:
            raise ScenarioError(f"Target {target_id} not found", 404)
        return dict(row)

    def override_target(self, target_id: int, override_qty: Optional[float], reason: str,
                        break_lock: bool = False, as_of: Optional[date] = None) -> dict:
        """Set (or clear, with override_qty=None) a manager override.

        Locked targets (week started) need break_lock=True -- goalposts
        only move deliberately. An override is pinned: the rest of every
        target it sits in rebalances around it (for the current and future
        weeks). Clearing it goes back to the scenario-driven value.
        """
        def go():
            row = self.get_target(target_id, lock=True)
            if row["bIsLocked"] and not break_lock:
                raise ScenarioError(
                    "This target is locked because its week has started. "
                    "Resend with break_lock=true to override it anyway.", 409)
            prev = {k: row[k] for k in ("fTargetQty", "fOverrideQty", "cSource",
                                         "cOverrideReason", "cOverrideBy")}
            key = (row["cCustomerAccount"], row["cStockCode"], _as_date(row["dWeekStart"]))
            as_of_week = weeks.week_start(as_of or date.today())
            # A past week: only this row is re-solved, its neighbours stay put.
            only = {key} if key[2] < as_of_week else None
            if override_qty is not None:
                self._db.execute(text("""
                    UPDATE [SOAssistant].dbo._tb_SOScenarioTarget
                       SET fOverrideQty = :q, fTargetQty = :q, cSource = 'OVERRIDE',
                           cOverrideReason = :reason, cOverrideBy = :by,
                           dtOverrideAt = sysdatetime(), dtUpdatedAt = sysdatetime()
                     WHERE idScenarioTarget = :id
                """), {"id": target_id, "q": round(override_qty, 4), "reason": reason,
                       "by": self._username})
                # The rest of every target this row sits in rebalances around it.
                self._rebuild([key[2]], as_of_week, only_keys=only)
            else:
                if row["fOverrideQty"] is None:
                    raise ScenarioError("This target has no override to clear", 409)
                self._db.execute(text("""
                    UPDATE [SOAssistant].dbo._tb_SOScenarioTarget
                       SET fOverrideQty = NULL, cOverrideReason = :reason, cOverrideBy = :by,
                           dtOverrideAt = sysdatetime(), dtUpdatedAt = sysdatetime()
                     WHERE idScenarioTarget = :id
                """), {"id": target_id, "reason": reason, "by": self._username})
                # Back to the scenario-driven value (break_lock already
                # authorised touching a locked row); the neighbours rebalance.
                self._rebuild([key[2]], as_of_week,
                              unlock_keys=frozenset([key]) if row["bIsLocked"] else frozenset(),
                              only_keys=only)
            after = self._db.execute(text("""
                SELECT * FROM [SOAssistant].dbo._tb_SOScenarioTarget WHERE idScenarioTarget = :id
            """), {"id": target_id}).mappings().first()
            new_state = ({k: after[k] for k in prev} if after else {"deleted": True})
            new_state["reason"] = reason
            new_state["breakLock"] = bool(break_lock and row["bIsLocked"])
            self._audit("OVERRIDE", target_id, "SET" if override_qty is not None else "CLEAR",
                        prev, new_state)
            return dict(after) if after else {**dict(row), "deleted": True}
        return self._run(go)
