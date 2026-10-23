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

    token = issue_token(username)
    return schemas.LoginResponse(
        token=token,
        username=username,
        default_warehouse_code=_default_warehouse_for(db, username),
        force_ext_order_num=_force_ext_order_num(db),
    )


@router.post("/logout")
def logout(token: str = Depends(get_current_token)):
    revoke_token(token)
    return {"ok": True}


@router.get("/me", response_model=schemas.MeResponse)
def me(username: str = Depends(get_current_user), db: Session = Depends(get_db)):
    return schemas.MeResponse(
        username=username,
        default_warehouse_code=_default_warehouse_for(db, username),
        force_ext_order_num=_force_ext_order_num(db),
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
