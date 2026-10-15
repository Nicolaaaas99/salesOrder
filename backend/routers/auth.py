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
        text("SELECT WhseCode FROM dbo._vw_SOAgentDefaultWhse WHERE cAgentName = :name"),
        {"name": name},
    ).mappings().first()
    code = (row and row["WhseCode"]) or None
    return code.strip() if isinstance(code, str) else code


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
    default_whse = _default_warehouse_for(db, username)
    return schemas.LoginResponse(
        token=token,
        username=username,
        default_warehouse_code=default_whse,
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
    )
