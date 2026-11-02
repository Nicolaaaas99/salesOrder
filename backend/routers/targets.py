"""
Litre targets CRUD + listing — powers the Analytics → Performance screen
and the Order Assistant's agent progress bar.

Role gating (Phase 3.2 of user-rights work):
  • CRUD endpoints (POST / PATCH / DELETE)    SUPERVISOR + ADMIN only
  • GET /targets, /targets/mine                all roles, BUT
    AGENTs only see:
      - their own placed-by targets (iAgentID / cUsername = me)
      - non-identity targets (route / customer / category / stock,
        including compounds that don't name a specific person)
    AGENTs do NOT see:
      - any telesales target  (cTelesales set, regardless of value)
      - any sales rep target  (cSalesRep  set, regardless of value)
      - other agents' placed-by targets
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

import schemas
from auth import get_current_user, get_current_user_role, require_min_role
from database import get_db
from services.targets import is_active, litres_for, resolve_window

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/targets",
    tags=["targets"],
    dependencies=[Depends(get_current_user)],
)


# Shared SELECT column list — kept here so adding a dim column stays a
# one-line change across every endpoint.
TARGET_SELECT_COLS = """
    idTarget, iAgentID, cUsername, cTelesales, cSalesRep,
    cRoute, cCustomerAccount, cCategory, cStockCode,
    cPeriod, fTargetLitres, dEffectiveFrom, dEffectiveTo,
    cCreatedBy, dtCreatedAt
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate(payload: schemas.TargetBase) -> None:
    """Run the same checks the DB CHECKs enforce, with friendlier errors
    than a 500 from SQL Server. Applies to create + update bodies."""
    period = (payload.period or "").upper()
    if period not in schemas.TARGET_PERIODS:
        raise HTTPException(
            400,
            f"period must be one of {sorted(schemas.TARGET_PERIODS)}",
        )
    if payload.target_litres is None or payload.target_litres <= 0:
        raise HTTPException(400, "target_litres must be greater than 0")
    scope_set = (
        payload.agent_id is not None
        or any(
            [
                (payload.username or "").strip(),
                (payload.telesale or "").strip(),
                (payload.sales_rep or "").strip(),
                (payload.route or "").strip(),
                (payload.customer_account or "").strip(),
                (payload.category or "").strip(),
                (payload.stock_code or "").strip(),
            ]
        )
    )
    if not scope_set:
        raise HTTPException(
            400,
            "At least one scope dim must be set: agent_id / username / "
            "telesale / sales_rep / route / customer_account / category / "
            "stock_code.",
        )
    if period == "CUSTOM" and payload.effective_to is None:
        raise HTTPException(
            400, "CUSTOM period requires effective_to (the window end)."
        )
    if (
        payload.effective_to is not None
        and payload.effective_from > payload.effective_to
    ):
        raise HTTPException(
            400, "effective_from must be on or before effective_to."
        )


def _row_to_target(r) -> schemas.Target:
    def _s(v):
        return (v or "").strip() if isinstance(v, str) else v
    agent_id_val = r["iAgentID"] if "iAgentID" in r.keys() else None
    return schemas.Target(
        id=int(r["idTarget"]),
        agent_id=int(agent_id_val) if agent_id_val is not None else None,
        username=_s(r["cUsername"]),
        telesale=_s(r["cTelesales"]),
        sales_rep=_s(r["cSalesRep"]),
        route=_s(r["cRoute"]),
        customer_account=_s(r["cCustomerAccount"]),
        category=_s(r["cCategory"]),
        stock_code=_s(r["cStockCode"]),
        period=(r["cPeriod"] or "").upper(),
        target_litres=float(r["fTargetLitres"] or 0),
        effective_from=r["dEffectiveFrom"],
        effective_to=r["dEffectiveTo"],
        created_by=(r["cCreatedBy"] or "").strip(),
        created_at=r["dtCreatedAt"],
    )


def _enrich_with_actual(db: Session, t: schemas.Target) -> schemas.TargetWithActual:
    """Attach the current measurement window + actual litres + progress
    % to a Target. Called for every row returned by GET /targets."""
    window_from, window_to = resolve_window(t.period, t.effective_from, t.effective_to)
    actual = litres_for(
        db,
        from_date=window_from,
        to_date=window_to,
        agent_id=t.agent_id,
        username=t.username,
        telesale=t.telesale,
        sales_rep=t.sales_rep,
        route=t.route,
        customer_account=t.customer_account,
        category=t.category,
        stock_code=t.stock_code,
    )
    progress = (actual / t.target_litres) if t.target_litres > 0 else 0.0
    return schemas.TargetWithActual(
        **t.model_dump(),
        actual_litres=actual,
        progress_pct=progress,
        window_from=window_from,
        window_to=window_to,
    )


def _visible_to_agent(
    t: schemas.Target,
    *,
    me_username: str,
    me_agent_id: Optional[int],
) -> bool:
    """AGENT visibility rule — see pure non-identity targets + your
    own placed-by targets. No telesales / sales rep targets at all
    (regardless of who they're about). Supervisors / admins bypass
    this check entirely and see everything."""
    # No telesales / sales rep targets, period.
    if t.telesale:
        return False
    if t.sales_rep:
        return False
    # Target names a placed-by person → show only when it's me.
    agent_dim_set = t.agent_id is not None or bool(t.username)
    if agent_dim_set:
        if me_agent_id is not None and t.agent_id == me_agent_id:
            return True
        if t.username and me_username and t.username.lower() == me_username.lower():
            return True
        return False
    # No identity dim at all — route / customer / category / stock
    # (or compound combinations of those). Visible to everyone.
    return True


def _scope_kind(t: schemas.Target) -> str:
    """Classify a target's scope shape for the Analytics filter chips.

    Scope dims live in three semantic GROUPS:
      • placed_by     — agent (iAgentID / cUsername)
      • customer_side — customer_account, sales_rep, telesale, route
      • sold_what     — stock_code, category

    Classification rules:
      • Compound = the target touches MORE THAN ONE group (e.g. Agent +
        Category, Customer + Stock).
      • Within one group, the MOST SPECIFIC dim wins the name:
          customer > sales_rep > telesales > route
          stock > category
        So a target where the user picked Customer and the form
        auto-filled route / telesales / sales_rep is a "customer"
        target — not compound, not route — because customer pins the
        others. Same for Stock → category.
      • agent + iAgentID count as one agent dim (not two)."""
    agent_dim         = t.agent_id is not None or bool(t.username)
    customer_side_dim = any([
        bool(t.customer_account),
        bool(t.sales_rep),
        bool(t.telesale),
        bool(t.route),
    ])
    sold_what_dim     = bool(t.stock_code) or bool(t.category)

    groups_touched = sum([agent_dim, customer_side_dim, sold_what_dim])
    if groups_touched > 1:
        return "compound"

    if agent_dim:
        return "agent"
    if customer_side_dim:
        # Pick the most specific dim actually set — customer pins the
        # others, so an auto-filled Customer target reads as "customer".
        if t.customer_account:
            return "customer"
        if t.sales_rep:
            return "sales_rep"
        if t.telesale:
            return "telesales"
        if t.route:
            return "route"
    if sold_what_dim:
        if t.stock_code:
            return "stock"
        if t.category:
            return "category"
    return "unknown"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=schemas.TargetListResponse)
def list_targets(
    scope: Optional[str] = Query(
        None,
        pattern="^(agent|telesales|sales_rep|route|customer|category|stock|compound|all)$",
        description=(
            "Filter by scope type. 'agent' = cUsername only (no other dim), "
            "'telesales' = cTelesales only, 'sales_rep' = cSalesRep only, "
            "'compound' = two or more dims set, 'all' (default) returns "
            "everything."
        ),
    ),
    username: Optional[str] = Query(
        None, description="If set, limit to targets whose cUsername matches."
    ),
    active_only: bool = Query(
        True,
        description=(
            "When true (default) hides targets whose effective range does "
            "not include today. Set false to see historical / future ones."
        ),
    ),
    db: Session = Depends(get_db),
    me_username: str = Depends(get_current_user),
    me_role: str = Depends(get_current_user_role),
):
    rows = db.execute(
        text(
            f"""
            SELECT {TARGET_SELECT_COLS}
            FROM [SOAssistant].dbo._tb_SOTargets
            ORDER BY dtCreatedAt DESC
            """
        )
    ).mappings().all()

    targets = [_row_to_target(r) for r in rows]
    if username:
        u = username.strip()
        targets = [t for t in targets if (t.username or "") == u]
    if active_only:
        targets = [t for t in targets if is_active(t.effective_from, t.effective_to)]
    if scope and scope != "all":
        targets = [t for t in targets if _scope_kind(t) == scope]

    # Role-scoped visibility — SUPERVISOR / ADMIN see everything; AGENT
    # only sees own placed-by + non-identity targets. See
    # `_visible_to_agent` for the exact rule.
    if me_role == "AGENT":
        me_agent_id = _resolve_agent_id(db, me_username)
        targets = [
            t for t in targets
            if _visible_to_agent(t, me_username=me_username, me_agent_id=me_agent_id)
        ]

    enriched = [_enrich_with_actual(db, t) for t in targets]
    return schemas.TargetListResponse(targets=enriched)


@router.post(
    "",
    response_model=schemas.TargetWithActual,
    status_code=201,
    dependencies=[Depends(require_min_role("SUPERVISOR"))],
)
def create_target(
    payload: schemas.TargetCreate,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    _validate(payload)
    row = db.execute(
        text(
            """
            INSERT INTO [SOAssistant].dbo._tb_SOTargets
                (iAgentID, cUsername, cTelesales, cSalesRep,
                 cRoute, cCustomerAccount, cCategory, cStockCode,
                 cPeriod, fTargetLitres,
                 dEffectiveFrom, dEffectiveTo,
                 cCreatedBy)
            OUTPUT INSERTED.*
            VALUES
                (:agent_id, :username, :telesale, :sales_rep,
                 :route, :customer_account, :category, :stock_code,
                 :period, :target_litres,
                 :effective_from, :effective_to,
                 :created_by)
            """
        ),
        {
            "agent_id": payload.agent_id,
            "username": (payload.username or "").strip() or None,
            "telesale": (payload.telesale or "").strip() or None,
            "sales_rep": (payload.sales_rep or "").strip() or None,
            "route": (payload.route or "").strip() or None,
            "customer_account": (payload.customer_account or "").strip() or None,
            "category": (payload.category or "").strip() or None,
            "stock_code": (payload.stock_code or "").strip() or None,
            "period": payload.period.upper(),
            "target_litres": payload.target_litres,
            "effective_from": payload.effective_from,
            "effective_to": payload.effective_to,
            "created_by": username,
        },
    ).mappings().first()
    db.commit()
    return _enrich_with_actual(db, _row_to_target(row))


@router.patch(
    "/{target_id}",
    response_model=schemas.TargetWithActual,
    dependencies=[Depends(require_min_role("SUPERVISOR"))],
)
def update_target(
    target_id: int,
    payload: schemas.TargetUpdate,
    db: Session = Depends(get_db),
):
    _validate(payload)
    row = db.execute(
        text(
            """
            UPDATE [SOAssistant].dbo._tb_SOTargets
            SET iAgentID         = :agent_id,
                cUsername        = :username,
                cTelesales       = :telesale,
                cSalesRep        = :sales_rep,
                cRoute           = :route,
                cCustomerAccount = :customer_account,
                cCategory        = :category,
                cStockCode       = :stock_code,
                cPeriod          = :period,
                fTargetLitres    = :target_litres,
                dEffectiveFrom   = :effective_from,
                dEffectiveTo     = :effective_to
            OUTPUT INSERTED.*
            WHERE idTarget = :id
            """
        ),
        {
            "id": target_id,
            "agent_id": payload.agent_id,
            "username": (payload.username or "").strip() or None,
            "telesale": (payload.telesale or "").strip() or None,
            "sales_rep": (payload.sales_rep or "").strip() or None,
            "route": (payload.route or "").strip() or None,
            "customer_account": (payload.customer_account or "").strip() or None,
            "category": (payload.category or "").strip() or None,
            "stock_code": (payload.stock_code or "").strip() or None,
            "period": payload.period.upper(),
            "target_litres": payload.target_litres,
            "effective_from": payload.effective_from,
            "effective_to": payload.effective_to,
        },
    ).mappings().first()
    if row is None:
        db.rollback()
        raise HTTPException(404, f"Target {target_id} not found")
    db.commit()
    return _enrich_with_actual(db, _row_to_target(row))


@router.delete(
    "/{target_id}",
    status_code=204,
    dependencies=[Depends(require_min_role("SUPERVISOR"))],
)
def delete_target(target_id: int, db: Session = Depends(get_db)):
    result = db.execute(
        text("DELETE FROM [SOAssistant].dbo._tb_SOTargets WHERE idTarget = :id"),
        {"id": target_id},
    )
    db.commit()
    if result.rowcount == 0:
        raise HTTPException(404, f"Target {target_id} not found")
    return None


# ---------------------------------------------------------------------------
# "My" endpoints — targets scoped to the logged-in user.
# ---------------------------------------------------------------------------

def _resolve_agent_id(db: Session, username: str) -> Optional[int]:
    """Agent id for the current login — same lookup auth uses, but
    kept local to avoid cross-router imports. None when the user isn't
    in _rtblAgents; callers fall back to text-matching cUsername."""
    row = db.execute(
        text(
            """
            SELECT TOP 1 idAgents
            FROM [BA_00_LPD_CON].dbo._rtblAgents
            WHERE LOWER(cAgentName) = LOWER(:name)
              AND bAgentActive = 1
            """
        ),
        {"name": username},
    ).mappings().first()
    return int(row["idAgents"]) if row and row["idAgents"] is not None else None


@router.get("/my-primary", response_model=Optional[schemas.TargetWithActual])
def my_primary_target(
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    """The most recently created *agent-only* target for the logged-in
    user that is active today. Used by the Order Assistant's single
    progress bar so we don't clutter the strip with multiple bars.

    Agent-only = `iAgentID` or `cUsername` matches the logged-in user
    AND every other scope dim is NULL. Preferring iAgentID means we
    don't false-miss on casing drift ("BrendaS" vs "Brendas").
    Compound agent targets (e.g. Yoghurt-on-RouteX-by-me) appear in
    /targets/mine instead."""
    agent_id = _resolve_agent_id(db, username)
    row = db.execute(
        text(
            f"""
            SELECT TOP 1 {TARGET_SELECT_COLS}
            FROM [SOAssistant].dbo._tb_SOTargets
            WHERE (iAgentID = :agent_id
                   OR (iAgentID IS NULL AND LOWER(cUsername) = LOWER(:username)))
              AND cTelesales IS NULL
              AND cSalesRep IS NULL
              AND cRoute IS NULL
              AND cCustomerAccount IS NULL
              AND cCategory IS NULL
              AND cStockCode IS NULL
              AND dEffectiveFrom <= CAST(GETDATE() AS DATE)
              AND (dEffectiveTo IS NULL OR dEffectiveTo >= CAST(GETDATE() AS DATE))
            ORDER BY dtCreatedAt DESC
            """
        ),
        {"agent_id": agent_id, "username": username},
    ).mappings().first()
    if row is None:
        return None
    return _enrich_with_actual(db, _row_to_target(row))


@router.get("/mine", response_model=schemas.TargetListResponse)
def my_targets(
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    role: str = Depends(get_current_user_role),
):
    """Every target active today where the logged-in user is on one of
    the "who" dims. For SUPERVISOR / ADMIN: matched by iAgentID OR any
    of cUsername / cTelesales / cSalesRep. For AGENT: only the
    placed-by match counts — telesales + sales rep targets are hidden
    per the role scope rule, even when they'd otherwise match this
    user's name."""
    agent_id = _resolve_agent_id(db, username)
    rows = db.execute(
        text(
            f"""
            SELECT {TARGET_SELECT_COLS}
            FROM [SOAssistant].dbo._tb_SOTargets
            WHERE (iAgentID = :agent_id
                OR LOWER(cUsername)  = LOWER(:username)
                OR LOWER(cTelesales) = LOWER(:username)
                OR LOWER(cSalesRep)  = LOWER(:username))
              AND dEffectiveFrom <= CAST(GETDATE() AS DATE)
              AND (dEffectiveTo IS NULL OR dEffectiveTo >= CAST(GETDATE() AS DATE))
            ORDER BY dtCreatedAt DESC
            """
        ),
        {"agent_id": agent_id, "username": username},
    ).mappings().all()
    targets = [_row_to_target(r) for r in rows]
    if role == "AGENT":
        targets = [
            t for t in targets
            if _visible_to_agent(t, me_username=username, me_agent_id=agent_id)
        ]
    enriched = [_enrich_with_actual(db, t) for t in targets]
    return schemas.TargetListResponse(targets=enriched)
