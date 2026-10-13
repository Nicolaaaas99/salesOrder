from fastapi import APIRouter, Depends, HTTPException, status

import schemas
from auth import (
    authenticate_against_evolution,
    get_current_token,
    get_current_user,
    issue_token,
    revoke_token,
)
from connection import get_sdk


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.LoginResponse)
def login(payload: schemas.LoginRequest):
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
    return schemas.LoginResponse(token=token, username=username)


@router.post("/logout")
def logout(token: str = Depends(get_current_token)):
    revoke_token(token)
    return {"ok": True}


@router.get("/me", response_model=schemas.MeResponse)
def me(username: str = Depends(get_current_user)):
    return schemas.MeResponse(username=username)
