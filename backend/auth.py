"""
Session + agent authentication built around the Evolution SDK.

The SDK already knows how to validate operator credentials via
`Pastel.Evolution.Agent.Authenticate(username, password)` — we just need to
front it with a tiny FastAPI session layer so the browser client can hold a
token and we can look up the username on every request. The username is then
written into `DatabaseContext.CurrentAgent` before any `.Save()` so Evolution's
audit columns record who placed the order.

Sessions are stored in a process-local dict. That means a backend restart
invalidates everyone — fine for a small internal telesales app; the frontend
just redirects back to the login screen on the next 401.
"""
import secrets
import threading
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from database import get_db


_sessions: dict[str, str] = {}       # token -> username
_sessions_lock = threading.Lock()


def issue_token(username: str) -> str:
    """Mint a cryptographically-random token and bind it to the username."""
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = username
    return token


def revoke_token(token: str) -> None:
    """Idempotent logout."""
    with _sessions_lock:
        _sessions.pop(token, None)


def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def get_current_user(authorization: Optional[str] = Header(None)) -> str:
    """
    FastAPI dependency — resolves the Authorization header to a username.
    Raises 401 if the token is missing / revoked / invalid.
    """
    token = _extract_bearer(authorization)
    if token is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing or malformed Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    with _sessions_lock:
        username = _sessions.get(token)
    if not username:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Session expired or invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


def get_current_token(authorization: Optional[str] = Header(None)) -> str:
    """Used by the logout endpoint — returns the raw token so we can revoke it."""
    token = _extract_bearer(authorization)
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")
    return token


_ROLE_RANK = {"AGENT": 0, "SUPERVISOR": 1, "ADMIN": 2}


def resolve_role(db: Session, username: str) -> Optional[str]:
    """Look up the active role for a login username. None when no
    matching `_tb_SOAppUsers` row exists (or the row is inactive) —
    callers treat None as "not authorised"."""
    row = db.execute(
        text(
            """
            SELECT u.cRole
            FROM [SOAssistant].dbo._tb_SOAppUsers u
            JOIN [BA_00_LPD_CON].dbo._rtblAgents g
                   ON g.idAgents = u.iAgentID
            WHERE LOWER(g.cAgentName) = LOWER(:username)
              AND u.bActive = 1
            """
        ),
        {"username": (username or "").strip()},
    ).mappings().first()
    if not row:
        return None
    return (row["cRole"] or "").strip().upper() or None


def get_current_user_role(
    username: str = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> str:
    """FastAPI dependency — resolves the current session's role on
    every request. A deactivated user's next request 401s immediately
    (no need to invalidate their token manually)."""
    role = resolve_role(db, username)
    if role is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Not authorised — ask your admin to add you to the Sales Order Assistant.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return role


def require_min_role(min_role: str):
    """Factory producing a FastAPI dep that 403s when the caller's
    role is below `min_role`. Call like:
        router = APIRouter(dependencies=[Depends(require_min_role('SUPERVISOR'))])
    …or inline on individual endpoints that need to escalate above
    the router's default."""
    min_rank = _ROLE_RANK[min_role.upper()]

    def _dep(role: str = Depends(get_current_user_role)) -> str:
        if _ROLE_RANK.get(role, -1) < min_rank:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Requires {min_role} or higher.",
            )
        return role

    return _dep


# ---------------- Evolution SDK integration ----------------------------------

def authenticate_against_evolution(SDK, username: str, password: str) -> bool:
    """
    Delegate the credential check to the Evolution SDK. Mirrors the C# usage:
        bool success = Agent.Authenticate("username", "password");
    Returns False on bad credentials, raises on infrastructure errors so the
    caller can surface a useful message to the UI.
    """
    try:
        result = SDK.Agent.Authenticate(username, password)
    except Exception as e:
        # Reraise so the endpoint can map it to a 500 with the SDK message;
        # we don't want to silently swallow DLL / connection problems.
        raise RuntimeError(f"Evolution Agent.Authenticate failed: {e}") from e
    return bool(result)


def apply_agent_to_context(SDK, username: str) -> None:
    """
    Stamp the current thread's DatabaseContext.CurrentAgent so Evolution's
    audit columns record the operator who performed the action.

    Call this on the request-handling thread BEFORE any `.Save()`. The SDK's
    DatabaseContext is thread-local, same as the connection, so this needs
    re-applying per request (worker threads are pooled).
    """
    try:
        from Pastel.Evolution import DatabaseContext
        DatabaseContext.CurrentAgent = SDK.Agent(username)
        print(f"[auth] CurrentAgent = {username!r}")
    except Exception as e:
        # Don't fail the save over this — just log loudly so the audit miss
        # is visible in the backend log.
        print(f"[auth] WARNING: could not set CurrentAgent={username!r}: {e}")
