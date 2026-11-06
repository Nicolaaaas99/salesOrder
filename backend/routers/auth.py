from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

import schemas
from auth import (
    authenticate_against_evolution,
    get_current_token,
    get_current_user,
    issue_token,
    resolve_role,
    revoke_token,
)
from connection import get_sdk
from database import get_db


router = APIRouter(prefix="/auth", tags=["auth"])


def _default_warehouse_for(db: Session, agent_username: str) -> Optional[str]:
    """Look up the telesales agent's default warehouse code via the agent view.
    Returns None if the agent isn't in the view (e.g. supervisor without a DC)
    — pricing then falls through to its generic "lowest across warehouses"
    behaviour so the rep can still work."""
    name = (agent_username or "").strip()
    if not name:
        return None
    row = db.execute(
        text("SELECT WhseCode FROM [SOAssistant].dbo._vw_SOAgentDefaultWhse WHERE cAgentName = :name"),
        {"name": name},
    ).mappings().first()
    code = (row and row["WhseCode"]) or None
    return code.strip() if isinstance(code, str) else code


def _force_ext_order_num(db: Session) -> bool:
    """Read Evolution's OrdersDf.bForceExtOrderNum via the config view. Falls
    to False if the view is missing or the row doesn't exist (fail-open so
    orders can still be placed if the config isn't set up)."""
    try:
        row = db.execute(
            text("SELECT ForceExtOrderNum FROM [SOAssistant].dbo._vw_SOSalesOrderConfig")
        ).mappings().first()
        return bool(row and row["ForceExtOrderNum"])
    except Exception:
        return False


@router.post("/login", response_model=schemas.LoginResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    """
    Validate the Evolution operator credentials via the SDK and, on success,
    mint a session token the frontend will send on every subsequent request.
    """
    username = (payload.username or "").strip()
    password = payload.password or ""
    if not username or not password:
        raise HTTPException(400, "Username and password are required")

    try:
        SDK = get_sdk()
    except Exception as e:
        # Infra problem — SDK didn't load or DB connection failed. The user
        # can't do anything about this; we want it loud in the UI.
        raise HTTPException(500, f"Evolution SDK unavailable: {e}") from e

    try:
        ok = authenticate_against_evolution(SDK, username, password)
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e

    if not ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Canonicalise the username against the Evolution agent master —
    # whatever the user typed to log in, we always carry forward the
    # exact spelling + casing stored in _rtblAgents. Avoids audit rows
    # ending up as "Brendas" when Evolution has "BrendaS", which used
    # to break string-matched target measurement. Case-insensitive
    # match on the way in; canonical value out.
    agent_row = db.execute(
        text(
            """
            SELECT TOP 1 idAgents, cAgentName
            FROM [BA_00_LPD_CON].dbo._rtblAgents
            WHERE LOWER(cAgentName) = LOWER(:name)
              AND bAgentActive = 1
            """
        ),
        {"name": username},
    ).mappings().first()
    if agent_row:
        username = (agent_row["cAgentName"] or username).strip()
        agent_id = int(agent_row["idAgents"]) if agent_row["idAgents"] is not None else None
    else:
        agent_id = None

    # User-rights gate — Evolution credentials are valid but access to
    # the Sales Order Assistant is a separate whitelist (`_tb_SOAppUsers`).
    # Reject here with a clear message so the frontend can show "ask
    # your admin" rather than a generic "invalid credentials".
    role = resolve_role(db, username)
    if role is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not authorised — ask your admin to add you to the Sales Order Assistant.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = issue_token(username)
    return schemas.LoginResponse(
        token=token,
        username=username,
        agent_id=agent_id,
        role=role,
        default_warehouse_code=_default_warehouse_for(db, username),
        force_ext_order_num=_force_ext_order_num(db),
    )


@router.post("/logout")
def logout(token: str = Depends(get_current_token)):
    revoke_token(token)
    return {"ok": True}


def _agent_id_for(db: Session, username: str) -> Optional[int]:
    """Resolve a login username to its `_rtblAgents.idAgents`. Returns
    None if the login isn't a known active agent — downstream endpoints
    then fall back to text-matching on cUsername for compatibility."""
    name = (username or "").strip()
    if not name:
        return None
    row = db.execute(
        text(
            """
            SELECT TOP 1 idAgents
            FROM [BA_00_LPD_CON].dbo._rtblAgents
            WHERE LOWER(cAgentName) = LOWER(:name)
              AND bAgentActive = 1
            """
        ),
        {"name": name},
    ).mappings().first()
    return int(row["idAgents"]) if row and row["idAgents"] is not None else None


@router.get("/me", response_model=schemas.MeResponse)
def me(username: str = Depends(get_current_user), db: Session = Depends(get_db)):
    # Re-resolve role on every /me so a mid-session deactivation /
    # role change takes effect at the next hydration. Deactivated →
    # 401, matches the login path's wording.
    role = resolve_role(db, username)
    if role is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not authorised — ask your admin to add you to the Sales Order Assistant.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return schemas.MeResponse(
        username=username,
        agent_id=_agent_id_for(db, username),
        role=role,
        default_warehouse_code=_default_warehouse_for(db, username),
        force_ext_order_num=_force_ext_order_num(db),
    )


def _sunday_of_current_week() -> date:
    """Sunday that opens the current week (matches the Analytics 12-week
    pivot columns + the stale-cutoff logic). Python isoweekday: Mon=1 ..
    Sun=7; modulo 7 maps Sunday → 0."""
    today = date.today()
    days_since_sunday = today.isoweekday() % 7
    return today - timedelta(days=days_since_sunday)


@router.get("/me/performance", response_model=schemas.AgentPerformance)
def my_performance(
    username: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Current logged-in agent's output across today / this week /
    this month. The three numbers in each row come from one query with
    CASE WHEN buckets so we touch each source table only once per request.

    Litres: derived from our own audit table (CREATE actions for this
    user) joined to the InvNum lines for the current state. We don't
    key off `InvNum.iINVNUMAgentID` because Evolution overwrites that
    column when the order is processed, so attribution would quietly
    swap to whoever handled the invoice.

    Customers called: distinct `cCustomerAccount` from the contact log
    where cUsername matches the agent. One contact row per touch, but a
    repeat call to the same customer the same window doesn't double-
    count — "unique customers I reached" is the useful signal."""
    today = date.today()
    week_start = _sunday_of_current_week()
    month_start = today.replace(day=1)
    bind = {
        "username": username,
        "today_start": today,
        "week_start": week_start,
        "month_start": month_start,
    }

    # Litres. Chain of PK/FK joins:
    #   audit.iInvNumAutoIndex  →  InvNum.AutoIndex
    #   InvNum.AutoIndex        →  _btblInvoiceLines.iInvoiceID
    #   _btblInvoiceLines.iStockCodeID → StkItem.StockLink
    #
    # Partial-processing wrinkle: when Evolution partially processes an
    # order, the original InvNum row keeps its AutoIndex but a NEW row
    # is created for the remainder, with `OrigDocID` pointing back at
    # the original. The lines on each row carry only that slice's qty,
    # so to get the full ORIGINAL order quantity we need both — which
    # we grab via `OR NUM.OrigDocID = c.iInvNumAutoIndex`.
    #
    # DocState IN (1, 3, 4) matches the rest of the codebase and
    # excludes cancelled orders (DocState=0) so cancelling an order
    # subtracts its litres from the agent's totals.
    # Lower bound for the partial-process branch. A remainder row is
    # always created AFTER its original, and InvNum.AutoIndex is an
    # identity, so every row we could match has AutoIndex >= the
    # agent's earliest order this month. Passing it as a literal param
    # turns the OrigDocID branch into a PK range seek over this month's
    # rows instead of a full InvNum scan (no extra index needed).
    min_row = db.execute(
        text(
            """
            SELECT MIN(iInvNumAutoIndex) AS min_id
            FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit
            WHERE cUsername = :username
              AND cAction = 'CREATE'
              AND dtActionAt >= :month_start
              AND iInvNumAutoIndex IS NOT NULL
            """
        ),
        bind,
    ).mappings().first()
    min_id = min_row["min_id"] if min_row else None

    litres_row = None if min_id is None else db.execute(
        text(
            """
            WITH my_creates AS (
                SELECT iInvNumAutoIndex,
                       CAST(dtActionAt AS DATE) AS action_date
                FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit
                WHERE cUsername = :username
                  AND cAction = 'CREATE'
                  AND dtActionAt >= :month_start
                  AND iInvNumAutoIndex IS NOT NULL
            ),
            -- Two separate branches instead of one `AutoIndex = x OR
            -- OrigDocID = x` join. The OR form defeated the InvNum PK
            -- index and made SQL Server re-scan InvNum per audit row
            -- (observed 2026-11-06: 70-145 s CPU / 17-36M reads per
            -- call, pinning the server). UNION ALL keeps the exact same
            -- row semantics: branch 1 is a PK seek, branch 2 is a PK
            -- range seek from :min_id (see min_row above).
            matched AS (
                SELECT c.action_date, NUM.AutoIndex
                FROM my_creates c
                JOIN dbo.InvNum NUM ON NUM.AutoIndex = c.iInvNumAutoIndex
                WHERE NUM.DocType = 4 AND NUM.DocState IN (1, 3, 4)
                UNION ALL
                SELECT c.action_date, NUM.AutoIndex
                FROM my_creates c
                JOIN dbo.InvNum NUM ON NUM.OrigDocID = c.iInvNumAutoIndex
                WHERE NUM.DocType = 4 AND NUM.DocState IN (1, 3, 4)
                  AND NUM.AutoIndex > :min_id
                  AND NUM.OrigDocID <> NUM.AutoIndex
            ),
            per_date AS (
                SELECT m.action_date,
                       SUM(ISNULL(LIN.fQuantity, 0) * ISNULL(STK.ulIISize, 0)) AS litres
                FROM matched m
                JOIN dbo._btblInvoiceLines LIN ON LIN.iInvoiceID = m.AutoIndex
                JOIN dbo.StkItem STK           ON STK.StockLink  = LIN.iStockCodeID
                GROUP BY m.action_date
            )
            SELECT
                SUM(CASE WHEN action_date >= :today_start THEN litres ELSE 0 END) AS today,
                SUM(CASE WHEN action_date >= :week_start THEN litres ELSE 0 END) AS week,
                SUM(litres) AS month
            FROM per_date
            """
        ),
        {**bind, "min_id": min_id},
    ).mappings().first()

    calls_row = db.execute(
        text(
            """
            SELECT
                COUNT(DISTINCT CASE WHEN dCallDate >= :today_start THEN cCustomerAccount END) AS today,
                COUNT(DISTINCT CASE WHEN dCallDate >= :week_start THEN cCustomerAccount END) AS week,
                COUNT(DISTINCT cCustomerAccount) AS month
            FROM [SOAssistant].dbo._tb_SOCustomerContactLog
            WHERE cUsername = :username
              AND dCallDate >= :month_start
            """
        ),
        bind,
    ).mappings().first()

    def _win(r, key: str) -> float:
        v = r and r[key]
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    return schemas.AgentPerformance(
        litres=schemas.PerformanceWindow(
            today=_win(litres_row, "today"),
            week=_win(litres_row, "week"),
            month=_win(litres_row, "month"),
        ),
        customers_called=schemas.PerformanceWindow(
            today=_win(calls_row, "today"),
            week=_win(calls_row, "week"),
            month=_win(calls_row, "month"),
        ),
    )


@router.post("/default-warehouse", response_model=schemas.SetDefaultWarehouseResponse)
def set_default_warehouse(
    payload: schemas.SetDefaultWarehouseRequest,
    username: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Set the logged-in telesales agent's default warehouse. Resolves the
    warehouse code to its WhseLink via _vw_SOWarehouses, then writes it to
    _rtblAgents.iDefWhseId for the row where cAgentName matches the token's
    username. Called from the DefaultWarehouseModal prompt on first login."""
    code = (payload.code or "").strip()
    if not code:
        raise HTTPException(400, "Warehouse code is required")

    whse = db.execute(
        text("SELECT WhseLink FROM [SOAssistant].dbo._vw_SOWarehouses WHERE Code = :code"),
        {"code": code},
    ).mappings().first()
    if not whse:
        raise HTTPException(404, f"Warehouse '{code}' not found")

    agent = db.execute(
        text("SELECT idAgents FROM dbo._rtblAgents WHERE cAgentName = :name AND bAgentActive = 1"),
        {"name": username},
    ).mappings().first()
    if not agent:
        raise HTTPException(
            404,
            f"No active agent record found for '{username}'. "
            "Ask an administrator to register this operator as a telesales agent.",
        )

    db.execute(
        text("UPDATE dbo._rtblAgents SET iDefWhseId = :whse_id WHERE idAgents = :agent_id"),
        {"whse_id": whse["WhseLink"], "agent_id": agent["idAgents"]},
    )
    db.commit()

    return schemas.SetDefaultWarehouseResponse(default_warehouse_code=code)
