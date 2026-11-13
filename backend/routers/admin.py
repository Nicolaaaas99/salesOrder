"""
Admin — Users & Roles management (Phase 3.3 of user-rights).

ADMIN-only endpoints for CRUD on `_tb_SOAppUsers`. Every row the admin
UI shows is one Evolution agent (active in `_rtblAgents`); the LEFT
JOIN overlays whether that agent is registered with the app and what
role they hold.

Self-lockout guard: an admin can't demote themselves below ADMIN or
deactivate themselves. Prevents a single-admin install from becoming
unmanageable. Other admins can still demote / deactivate you if there
are multiple admins in the system.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

import schemas
from auth import get_current_user, get_current_user_role, presence, require_min_role
from database import get_db
from services.dc_warehouse import dc_warehouses


router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    # SUPERVISOR+ to get in at all (agents 403 before touching the DB).
    # SUPERVISORs get a limited view (2026-11-12): registered non-admin
    # users only, and the only change they may make is an AGENT's
    # warehouse. Adding users, roles and activation stay ADMIN-only.
    dependencies=[Depends(require_min_role("SUPERVISOR"))],
)


def _is_admin(role: str) -> bool:
    return (role or "").upper() == "ADMIN"


def _admin_usernames(db: Session) -> set:
    rows = db.execute(
        text(
            """
            SELECT g.cAgentName
            FROM [SOAssistant].dbo._tb_SOAppUsers u
            JOIN [BA_00_LPD_CON].dbo._rtblAgents g ON g.idAgents = u.iAgentID
            WHERE u.cRole = 'ADMIN'
            """
        )
    ).mappings().all()
    return {(r["cAgentName"] or "").strip().lower() for r in rows}


_VALID_ROLES = {"AGENT", "SUPERVISOR", "ADMIN"}


# Shared SELECT for the GET and the POST/PATCH return value — kept in
# one place so adding a column later is a one-line change.
_ADMIN_USER_SELECT = """
    g.idAgents          AS agent_id,
    g.cAgentName        AS agent_name,
    ISNULL(g.cDescription, '') AS agent_description,
    u.idAppUser         AS app_user_id,
    u.cRole             AS role,
    u.bActive           AS is_active,
    u.dtCreatedAt       AS registered_at,
    u.cCreatedBy        AS registered_by,
    w.Code              AS default_warehouse_code
"""

# The agent's default warehouse (_rtblAgents.iDefWhseId -> WhseMst.Code).
_WHSE_JOIN = "LEFT JOIN [BA_00_LPD_CON].dbo.WhseMst w ON w.WhseLink = g.iDefWhseId"


def _row_to_user(r) -> schemas.AdminUserRow:
    return schemas.AdminUserRow(
        agent_id=int(r["agent_id"]),
        agent_name=(r["agent_name"] or "").strip(),
        agent_description=(r["agent_description"] or "").strip(),
        app_user_id=int(r["app_user_id"]) if r["app_user_id"] is not None else None,
        role=(r["role"] or "").strip().upper() or None if r["role"] else None,
        is_active=bool(r["is_active"]) if r["is_active"] is not None else None,
        registered_at=r["registered_at"],
        registered_by=(r["registered_by"] or "").strip() if r["registered_by"] else None,
        default_warehouse_code=(r["default_warehouse_code"] or "").strip() or None,
    )


def _lookup_agent_id(db: Session, username: str) -> Optional[int]:
    """Current session's idAgents — used by the self-lockout guard."""
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


def _fetch_user_row(db: Session, *, app_user_id: int) -> Optional[schemas.AdminUserRow]:
    row = db.execute(
        text(
            f"""
            SELECT {_ADMIN_USER_SELECT}
            FROM [SOAssistant].dbo._tb_SOAppUsers u
            JOIN [BA_00_LPD_CON].dbo._rtblAgents g ON g.idAgents = u.iAgentID
            {_WHSE_JOIN}
            WHERE u.idAppUser = :id
            """
        ),
        {"id": app_user_id},
    ).mappings().first()
    return _row_to_user(row) if row else None


def _fetch_user_row_by_agent(db: Session, *, agent_id: int) -> Optional[schemas.AdminUserRow]:
    row = db.execute(
        text(
            f"""
            SELECT {_ADMIN_USER_SELECT}
            FROM [BA_00_LPD_CON].dbo._rtblAgents g
            LEFT JOIN [SOAssistant].dbo._tb_SOAppUsers u ON u.iAgentID = g.idAgents
            {_WHSE_JOIN}
            WHERE g.idAgents = :agent_id
            """
        ),
        {"agent_id": agent_id},
    ).mappings().first()
    return _row_to_user(row) if row else None


@router.get("/users", response_model=List[schemas.AdminUserRow])
def list_users(db: Session = Depends(get_db), role: str = Depends(get_current_user_role)):
    """Every active Evolution agent, with registration status overlaid.
    Unregistered agents appear with `app_user_id = null` so the UI can
    offer an "Add as …" button inline."""
    rows = db.execute(
        text(
            f"""
            SELECT {_ADMIN_USER_SELECT}
            FROM [BA_00_LPD_CON].dbo._rtblAgents g
            LEFT JOIN [SOAssistant].dbo._tb_SOAppUsers u ON u.iAgentID = g.idAgents
            {_WHSE_JOIN}
            WHERE g.bAgentActive = 1
            ORDER BY g.cAgentName
            """
        )
    ).mappings().all()
    users = [_row_to_user(r) for r in rows]
    if not _is_admin(role):
        # Supervisors: registered users only, and never the admins.
        users = [u for u in users if u.app_user_id is not None and u.role != "ADMIN"]
    return users


@router.get("/presence", response_model=List[schemas.UserPresence])
def list_presence(db: Session = Depends(get_db), role: str = Depends(get_current_user_role)):
    """Who is signed in right now (memory only, see auth.presence). Polled
    every 60 s while the Users & Roles screen is open. Supervisors don't
    see admins."""
    people = presence()
    if not _is_admin(role) and people:
        hidden = _admin_usernames(db)
        people = {k: v for k, v in people.items() if k not in hidden}
    return [schemas.UserPresence(**p) for p in people.values()]


@router.post(
    "/users",
    response_model=schemas.AdminUserRow,
    status_code=201,
    dependencies=[Depends(require_min_role("ADMIN"))],
)
def create_user(
    payload: schemas.AdminUserCreate,
    db: Session = Depends(get_db),
    me_username: str = Depends(get_current_user),
):
    """Register an Evolution agent as an app user with the given role.
    Rejects if the agent is already registered (use PATCH to change
    role / toggle active instead)."""
    role = (payload.role or "").strip().upper()
    if role not in _VALID_ROLES:
        raise HTTPException(400, f"role must be one of {sorted(_VALID_ROLES)}")

    # Confirm the agent exists + is active.
    agent = db.execute(
        text(
            """
            SELECT idAgents FROM [BA_00_LPD_CON].dbo._rtblAgents
            WHERE idAgents = :id AND bAgentActive = 1
            """
        ),
        {"id": payload.agent_id},
    ).mappings().first()
    if not agent:
        raise HTTPException(400, f"Agent id {payload.agent_id} not found or inactive")

    # Reject double-registration — PATCH is the way to change state.
    existing = db.execute(
        text("SELECT idAppUser FROM [SOAssistant].dbo._tb_SOAppUsers WHERE iAgentID = :id"),
        {"id": payload.agent_id},
    ).first()
    if existing:
        raise HTTPException(409, "That agent is already registered. Use PATCH to update.")

    db.execute(
        text(
            """
            INSERT INTO [SOAssistant].dbo._tb_SOAppUsers
                (iAgentID, cRole, bActive, cCreatedBy)
            VALUES
                (:agent_id, :role, 1, :created_by)
            """
        ),
        {"agent_id": payload.agent_id, "role": role, "created_by": me_username[:50]},
    )
    db.commit()
    row = _fetch_user_row_by_agent(db, agent_id=payload.agent_id)
    if row is None:
        # Can't happen in practice (we just inserted) — defensive.
        raise HTTPException(500, "Failed to re-read the created user row")
    return row


@router.patch("/users/{app_user_id}", response_model=schemas.AdminUserRow)
def update_user(
    app_user_id: int,
    payload: schemas.AdminUserUpdate,
    db: Session = Depends(get_db),
    me_username: str = Depends(get_current_user),
    me_role: str = Depends(get_current_user_role),
):
    """Change role and / or active flag on an existing app-user row.
    Self-lockout guard: can't demote yourself from ADMIN or deactivate
    yourself. Other admins can still do that to you — multi-admin
    deployments are safe."""
    role_new = (payload.role or "").strip().upper() if payload.role else None
    if role_new is not None and role_new not in _VALID_ROLES:
        raise HTTPException(400, f"role must be one of {sorted(_VALID_ROLES)}")

    existing = _fetch_user_row(db, app_user_id=app_user_id)
    if existing is None:
        raise HTTPException(404, f"App user id {app_user_id} not found")

    # Supervisors may only change an AGENT's warehouse.
    if not _is_admin(me_role):
        if payload.role is not None or payload.is_active is not None:
            raise HTTPException(403, "Only an admin can change roles or activate / deactivate users.")
        if existing.role != "AGENT":
            raise HTTPException(403, "Supervisors can only change an agent's warehouse.")

    me_agent_id = _lookup_agent_id(db, me_username)
    is_self = me_agent_id is not None and existing.agent_id == me_agent_id
    if is_self:
        if role_new is not None and role_new != "ADMIN":
            raise HTTPException(
                400,
                "You can't demote yourself from ADMIN. Ask another admin to do it.",
            )
        if payload.is_active is False:
            raise HTTPException(
                400,
                "You can't deactivate yourself. Ask another admin to do it.",
            )

    # Build a dynamic UPDATE SET so unchanged fields stay as-is.
    set_parts = []
    params: dict = {"id": app_user_id}
    if role_new is not None:
        set_parts.append("cRole = :role")
        params["role"] = role_new
    if payload.is_active is not None:
        set_parts.append("bActive = :active")
        params["active"] = 1 if payload.is_active else 0

    # Default warehouse: lives on the Evolution agent record, not on
    # _tb_SOAppUsers. The only way an AGENT's warehouse changes.
    if payload.default_warehouse_code is not None:
        code = payload.default_warehouse_code.strip().upper()
        links = dc_warehouses(db)
        if code not in links:
            raise HTTPException(400, f"Warehouse must be one of {', '.join(sorted(links))}.")
        db.execute(
            text("UPDATE [BA_00_LPD_CON].dbo._rtblAgents SET iDefWhseId = :whse WHERE idAgents = :agent"),
            {"whse": links[code], "agent": existing.agent_id},
        )
        db.commit()
        if not set_parts:
            return _fetch_user_row(db, app_user_id=app_user_id) or existing

    if not set_parts:
        # Nothing to do — return the row as-is rather than running an
        # empty UPDATE (SQL Server rejects SET with no assignments).
        return existing

    db.execute(
        text(
            f"UPDATE [SOAssistant].dbo._tb_SOAppUsers SET {', '.join(set_parts)} WHERE idAppUser = :id"
        ),
        params,
    )
    db.commit()
    row = _fetch_user_row(db, app_user_id=app_user_id)
    if row is None:
        # Row vanished between the UPDATE and the re-read — very rare,
        # treat as 404.
        raise HTTPException(404, f"App user id {app_user_id} not found")
    return row
