"""Target-aware order suggestions + suggestion/decision persistence (Phase 4).

Suggested qty for one delivery (decided 2026-11-03: "remaining week on
this order"):

    weekly_basis = active target for the leaf-week   (source TARGET)
                 | weekly base median (legacy algo)  (source BASE_MODEL)
    suggested    = max(0, weekly_basis - already_covered_this_week),
                   snapped to the nearest whole pack

where already_covered = open sales-order qty + qty invoiced this week.
Later deliveries in the same week therefore pick up any shortfall.

Every suggestion shown in the Order Assistant is written to
_tb_SOOrderSuggestion (correction 1). When the order is saved, each
presented row gets the rep's decision:

    ACCEPTED    ordered qty == suggested qty
    MODIFIED    ordered qty != suggested qty (> 0)
    REJECTED    not ordered
    MANUAL_ADD  extra row for a stock ordered that wasn't suggested

Persistence is best-effort: a failure here is logged and never blocks a
rep from seeing suggestions or placing an order.
"""

from __future__ import annotations

import json
import math
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from . import weeks

log = logging.getLogger(__name__)

SOURCE_TARGET = "TARGET"
SOURCE_BASE_MODEL = "BASE_MODEL"
SOURCE_MANUAL = "MANUAL"

DECISION_ACCEPTED = "ACCEPTED"
DECISION_MODIFIED = "MODIFIED"
DECISION_REJECTED = "REJECTED"
DECISION_MANUAL_ADD = "MANUAL_ADD"

_QTY_EPS = 1e-6
_INSERT_BATCH = 150     # rows per INSERT -- 12 params each, < 2100


def _code(v: Optional[str]) -> str:
    return (v or "").strip().upper()


# --- pure helpers ------------------------------------------------------------

def remaining_week_qty(weekly_basis: float, covered: float, pack_size: int,
                       round_up: bool = False) -> float:
    """max(0, weekly_basis - covered) snapped to whole packs.

    round_up=False: nearest pack, half-up -- exactly the legacy suggestion.
    round_up=True:  next full pack, so the ordered qty always meets the
                    remaining target (decided 2026-11-03 for TARGET rows:
                    target 12, pack 10 -> 20, not 10).
    """
    pack = max(1, int(pack_size or 1))
    adjusted = max(0.0, weekly_basis - covered)
    if round_up:
        # Tolerance so 24.0000001 (float noise) doesn't become an extra pack.
        packs = math.ceil(adjusted / pack - 1e-6)
    else:
        packs = int(adjusted / pack + 0.5)
    return float(max(packs, 0) * pack)


@dataclass(frozen=True)
class PresentedSuggestion:
    id: int
    stock_code: str
    suggested_qty: float


@dataclass(frozen=True)
class Decision:
    id: int
    accepted_qty: float
    decision: str


def classify_decisions(
    presented: list[PresentedSuggestion],
    ordered_qty: dict[str, float],
) -> tuple[list[Decision], dict[str, float]]:
    """Match an order's lines against the suggestions that were shown.

    `ordered_qty` is {stock_code: total qty on the order} (any case).
    Returns (decisions for presented rows, {stock_code: qty} manual adds).
    If the same stock was presented more than once, only the latest
    (highest id) presentation is decided; older ones stay undecided.
    """
    ordered = {}
    for code, qty in ordered_qty.items():
        ordered[_code(code)] = ordered.get(_code(code), 0.0) + float(qty or 0)

    latest: dict[str, PresentedSuggestion] = {}
    for p in presented:
        k = _code(p.stock_code)
        if k not in latest or p.id > latest[k].id:
            latest[k] = p

    decisions = []
    for k, p in sorted(latest.items()):
        qty = ordered.get(k, 0.0)
        if qty <= _QTY_EPS:
            decisions.append(Decision(p.id, 0.0, DECISION_REJECTED))
        elif abs(qty - p.suggested_qty) <= _QTY_EPS:
            decisions.append(Decision(p.id, qty, DECISION_ACCEPTED))
        else:
            decisions.append(Decision(p.id, qty, DECISION_MODIFIED))

    manual = {k: q for k, q in ordered.items() if k not in latest and q > _QTY_EPS}
    return decisions, manual


# --- targets + forecasts -----------------------------------------------------

@dataclass
class WeekTarget:
    target_id: int
    stock_code: str
    week_start: date
    target_qty: float
    forecast_qty: float
    forecast_id: Optional[int]
    source: str                    # SCENARIO | OVERRIDE
    scenario_ids: list[int]
    scenario_names: list[str]
    category: Optional[str]


def load_week_targets(db: Session, customer: str, week_start: date) -> dict[str, WeekTarget]:
    """Materialised targets for one customer-week, keyed by stock code
    (upper-cased). FORECAST-sourced rows carry no management ask and are
    ignored."""
    rows = db.execute(text("""
        SELECT idScenarioTarget, cStockCode, dWeekStart, fTargetQty, fForecastQty,
               idForecastSnapshot, cSource, cScenariosAppliedJson, cCategory
          FROM [SOAssistant].dbo._tb_SOScenarioTarget
         WHERE cCustomerAccount = :c AND dWeekStart = :w AND cSource <> 'FORECAST'
    """), {"c": customer, "w": week_start}).mappings().all()
    if not rows:
        return {}

    def ids(raw):
        try:
            return sorted({int(x) for x in json.loads(raw)}) if raw else []
        except (ValueError, TypeError):
            return []

    all_ids = sorted({i for r in rows for i in ids(r["cScenariosAppliedJson"])})
    names: dict[int, str] = {}
    if all_ids:
        for r in db.execute(text("""
            SELECT idScenario, cName FROM [SOAssistant].dbo._tb_SOScenario
             WHERE idScenario IN :ids
        """).bindparams(bindparam("ids", expanding=True)), {"ids": all_ids}).mappings():
            names[int(r["idScenario"])] = r["cName"]

    out = {}
    for r in rows:
        sids = ids(r["cScenariosAppliedJson"])
        wk = r["dWeekStart"]
        out[_code(r["cStockCode"])] = WeekTarget(
            target_id=int(r["idScenarioTarget"]),
            stock_code=r["cStockCode"],
            week_start=wk.date() if isinstance(wk, datetime) else wk,
            target_qty=float(r["fTargetQty"]),
            forecast_qty=float(r["fForecastQty"]),
            forecast_id=r["idForecastSnapshot"],
            source=r["cSource"],
            scenario_ids=sids,
            scenario_names=[names[i] for i in sids if i in names],
            category=r["cCategory"],
        )
    return out


def load_week_forecasts(db: Session, customer: str, week_start: date) -> dict[str, tuple[int, float]]:
    """Latest-run active forecast per stock for one customer-week:
    {STOCK: (idForecast, qty)}."""
    rows = db.execute(text("""
        SELECT cStockCode, idForecast, fPredictedQty FROM (
            SELECT cStockCode, idForecast, fPredictedQty,
                   ROW_NUMBER() OVER (PARTITION BY cStockCode
                                      ORDER BY idForecastRun DESC, idForecast DESC) AS rn
              FROM [SOAssistant].dbo._tb_SOForecast
             WHERE cCustomerAccount = :c AND dWeekStart = :w AND bIsActive = 1) x
         WHERE rn = 1
    """), {"c": customer, "w": week_start}).mappings().all()
    return {_code(r["cStockCode"]): (int(r["idForecast"]), float(r["fPredictedQty"])) for r in rows}


# --- persistence -------------------------------------------------------------

@dataclass(frozen=True)
class PresentationRow:
    stock_code: str
    suggested_qty: float
    source: str
    target_id: Optional[int] = None
    forecast_id: Optional[int] = None
    target_basis: Optional[float] = None
    forecast_basis: Optional[float] = None


def resolve_agent_id(db: Session, username: str) -> Optional[int]:
    row = db.execute(text(
        "SELECT idAgents FROM dbo._rtblAgents WHERE cAgentName = :n AND bAgentActive = 1"
    ), {"n": (username or "").strip()}).mappings().first()
    return int(row["idAgents"]) if row and row["idAgents"] is not None else None


def _insert_rows(db: Session, customer: str, delivery_date: date, username: str,
                 agent_id: Optional[int], rows: list[dict]) -> dict[str, int]:
    """Multi-row INSERT with OUTPUT; returns {STOCK: idOrderSuggestion}."""
    cols = ("sku", "sq", "src", "tid", "fid", "tb", "fb", "aq", "dec", "ord")
    ids: dict[str, int] = {}
    for i0 in range(0, len(rows), _INSERT_BATCH):
        batch = rows[i0:i0 + _INSERT_BATCH]
        params = {"c": customer, "d": delivery_date, "w": weeks.week_start(delivery_date),
                  "u": username, "a": agent_id, "ts": datetime.now()}
        values = []
        for i, r in enumerate(batch):
            values.append("(:c, " + ", ".join(f":{c}{i}" for c in cols[:7])
                          + ", :d, :w, :u, :a, :ts, "
                          + ", ".join(f":{c}{i}" for c in cols[7:]) + ")")
            for c in cols:
                params[f"{c}{i}"] = r.get(c)
        result = db.execute(text(f"""
            INSERT INTO [SOAssistant].dbo._tb_SOOrderSuggestion
                (cCustomerAccount, cStockCode, fSuggestedQty, cSuggestionSource,
                 idScenarioTarget, idForecast, fTargetBasis, fForecastBasis,
                 dDeliveryDate, dWeekStart, cUsername, iAgentID, dtPresentedAt,
                 fAcceptedQty, cDecision, cOrderNum)
            OUTPUT inserted.idOrderSuggestion, inserted.cStockCode
            VALUES {', '.join(values)}
        """), params).mappings().all()
        for r in result:
            ids[_code(r["cStockCode"])] = int(r["idOrderSuggestion"])
    return ids


def record_presentation(
    db: Session,
    *,
    customer: str,
    delivery_date: date,
    username: str,
    rows: list[PresentationRow],
) -> dict[str, int]:
    """Persist one presentation (everything shown for one customer load).
    Returns {STOCK: idOrderSuggestion}; {} on failure (logged)."""
    if not rows:
        return {}
    try:
        ids = _insert_rows(db, customer, delivery_date, username, resolve_agent_id(db, username), [
            {"sku": r.stock_code, "sq": round(r.suggested_qty, 4), "src": r.source,
             "tid": r.target_id, "fid": r.forecast_id,
             "tb": round(r.target_basis, 4) if r.target_basis is not None else None,
             "fb": round(r.forecast_basis, 4) if r.forecast_basis is not None else None,
             "aq": None, "dec": None, "ord": None}
            for r in rows
        ])
        db.commit()
        return ids
    except Exception:
        db.rollback()
        log.exception("Failed to record suggestion presentation for %s", customer)
        return {}


def record_order_decisions(
    db: Session,
    *,
    suggestion_ids: Iterable[int],
    customer: str,
    username: str,
    order_no: str,
    delivery_date: Optional[date],
    lines: Iterable[tuple[str, float]],
) -> Optional[dict[str, int]]:
    """Stamp the rep's decision on presented suggestions after an order is
    placed from the Order Assistant. Only undecided rows belonging to this
    customer + user are touched (ids from the client are not trusted
    beyond that). Returns counts per decision, or None on failure (logged).
    """
    try:
        ordered: dict[str, float] = {}
        original: dict[str, str] = {}      # UPPER -> code as written on the order
        for code, qty in lines:
            original.setdefault(_code(code), (code or "").strip())
            ordered[_code(code)] = ordered.get(_code(code), 0.0) + float(qty or 0)

        id_list = sorted({int(i) for i in suggestion_ids})
        presented: list[PresentedSuggestion] = []
        first_delivery: Optional[date] = None
        for i0 in range(0, len(id_list), 900):
            for r in db.execute(text("""
                SELECT idOrderSuggestion, cStockCode, fSuggestedQty, dDeliveryDate
                  FROM [SOAssistant].dbo._tb_SOOrderSuggestion
                 WHERE idOrderSuggestion IN :ids
                   AND cCustomerAccount = :c AND cUsername = :u AND cDecision IS NULL
            """).bindparams(bindparam("ids", expanding=True)),
                    {"ids": id_list[i0:i0 + 900], "c": customer, "u": username}).mappings():
                presented.append(PresentedSuggestion(int(r["idOrderSuggestion"]), r["cStockCode"],
                                                     float(r["fSuggestedQty"])))
                first_delivery = first_delivery or r["dDeliveryDate"]

        decisions, manual = classify_decisions(presented, ordered)
        agent_id = resolve_agent_id(db, username)
        for d in decisions:
            db.execute(text("""
                UPDATE [SOAssistant].dbo._tb_SOOrderSuggestion
                   SET fAcceptedQty = :q, cDecision = :d, cOrderNum = :o,
                       dtDecidedAt = sysdatetime(), iAgentID = COALESCE(iAgentID, :a)
                 WHERE idOrderSuggestion = :id AND cDecision IS NULL
            """), {"q": d.accepted_qty, "d": d.decision, "o": order_no, "a": agent_id, "id": d.id})

        if manual:
            dd = delivery_date or first_delivery or date.today()
            if isinstance(dd, datetime):
                dd = dd.date()
            _insert_rows(db, customer, dd, username, agent_id, [
                {"sku": original.get(code, code), "sq": 0.0, "src": SOURCE_MANUAL, "tid": None, "fid": None,
                 "tb": None, "fb": None, "aq": qty, "dec": DECISION_MANUAL_ADD, "ord": order_no}
                for code, qty in sorted(manual.items())
            ])
            db.execute(text("""
                UPDATE [SOAssistant].dbo._tb_SOOrderSuggestion SET dtDecidedAt = sysdatetime()
                 WHERE cOrderNum = :o AND cDecision = 'MANUAL_ADD' AND dtDecidedAt IS NULL
            """), {"o": order_no})
        db.commit()
        counts: dict[str, int] = {}
        for d in decisions:
            counts[d.decision] = counts.get(d.decision, 0) + 1
        if manual:
            counts[DECISION_MANUAL_ADD] = len(manual)
        return counts
    except Exception:
        db.rollback()
        log.exception("Failed to record suggestion decisions for order %s", order_no)
        return None
