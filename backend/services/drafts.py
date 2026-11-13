"""Draft orders: an order saved half-way, to finish later.

Saved from the Order Assistant or Sales Order Entry and private to the agent
who saved it: one draft per agent per customer. The draft itself (header +
lines) is the screen's own state, stored as JSON; the backend only keeps the
summary columns needed to list it. Prices are fetched again when a draft is
reopened, so nothing here is charged as saved.

Drafts don't pile up:
  - placing the order deletes its draft (draft_id on SalesOrderCreate);
  - from the day after it was saved the agent is asked to finish or delete
    it (``stale``);
  - 3 days after it was saved it is deleted (``purge_expired``, run when
    drafts are listed: on demand, an index range delete on a tiny table).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

DRAFT_TABLE = "[SOAssistant].dbo._tb_SODraftOrder"
# Deleted this many days after it was saved (saved on the 11th: gone on the 14th).
EXPIRY_DAYS = 3
# A draft's JSON is a screen's header + lines: far below this.
MAX_DRAFT_JSON = 1_000_000

DraftSource = Literal["ORDER_ASSISTANT", "SALES_ORDER_ENTRY"]


class DraftSummary(BaseModel):
    id: int
    source: DraftSource
    customer_account: str
    customer_name: str = ""
    line_count: int = 0
    total_incl: Optional[float] = None
    created_at: datetime
    updated_at: datetime
    # The day it is deleted automatically.
    expires_on: date
    # Saved before today: the agent must finish or delete it.
    stale: bool = False


class DraftDetail(DraftSummary):
    payload: Dict[str, Any]


class DraftSave(BaseModel):
    # The draft being re-saved (opened from the list), if any.
    id: Optional[int] = None
    # Replace another draft of this agent for the same customer.
    replace: bool = False
    source: DraftSource
    customer_account: str = Field(min_length=1, max_length=50)
    customer_name: str = ""
    line_count: int = Field(0, ge=0)
    total_incl: Optional[float] = None
    payload: Dict[str, Any]


class DraftConflict(Exception):
    """The agent already has a draft for this customer."""

    def __init__(self, existing: DraftSummary):
        super().__init__("draft exists")
        self.existing = existing


_COLS = (
    "idDraft, cSource, cCustomerAccount, cCustomerName, iLines, fTotalIncl, "
    "dtCreatedAt, dtUpdatedAt"
)


def _summary(r, today: date) -> DraftSummary:
    created: datetime = r["dtCreatedAt"]
    return DraftSummary(
        id=int(r["idDraft"]),
        source=r["cSource"],
        customer_account=(r["cCustomerAccount"] or "").strip(),
        customer_name=r["cCustomerName"] or "",
        line_count=int(r["iLines"] or 0),
        total_incl=float(r["fTotalIncl"]) if r["fTotalIncl"] is not None else None,
        created_at=created,
        updated_at=r["dtUpdatedAt"],
        expires_on=created.date() + timedelta(days=EXPIRY_DAYS),
        stale=created.date() < today,
    )


def _today(db: Session) -> date:
    """The SQL Server's date: the same clock that stamps dtCreatedAt."""
    return db.execute(text("SELECT CAST(SYSDATETIME() AS date)")).scalar()


def purge_expired(db: Session) -> int:
    """Delete every agent's drafts saved EXPIRY_DAYS or more days ago
    (saved on the 11th: deleted from the 14th). Caller commits."""
    res = db.execute(
        text(
            f"""
            DELETE FROM {DRAFT_TABLE}
            WHERE dtCreatedAt < DATEADD(day, :keep,
                      CAST(CAST(SYSDATETIME() AS date) AS datetime2))
            """
        ),
        {"keep": -(EXPIRY_DAYS - 1)},
    )
    return res.rowcount or 0


def list_drafts(db: Session, username: str) -> List[DraftSummary]:
    purged = purge_expired(db)
    if purged:
        logger.info("drafts: purged %s expired draft(s)", purged)
    rows = db.execute(
        text(
            f"""
            SELECT {_COLS} FROM {DRAFT_TABLE}
            WHERE cUsername = :u
            ORDER BY dtCreatedAt
            """
        ),
        {"u": username},
    ).mappings().all()
    db.commit()
    today = _today(db)
    return [_summary(r, today) for r in rows]


def get_draft(db: Session, draft_id: int, username: str) -> Optional[DraftDetail]:
    r = db.execute(
        text(f"SELECT {_COLS}, cDraftJson FROM {DRAFT_TABLE} WHERE idDraft = :id AND cUsername = :u"),
        {"id": draft_id, "u": username},
    ).mappings().first()
    if not r:
        return None
    return DraftDetail(**_summary(r, _today(db)).model_dump(), payload=json.loads(r["cDraftJson"]))


def _find_for_customer(db: Session, username: str, account: str) -> Optional[DraftSummary]:
    r = db.execute(
        text(f"SELECT {_COLS} FROM {DRAFT_TABLE} WHERE cUsername = :u AND cCustomerAccount = :a"),
        {"u": username, "a": account},
    ).mappings().first()
    return _summary(r, _today(db)) if r else None


def save_draft(db: Session, req: DraftSave, username: str) -> DraftSummary:
    """Insert or re-save. Re-saving keeps dtCreatedAt, so the expiry clock
    isn't reset by saving again. Raises DraftConflict when the agent has
    another draft for the customer and ``replace`` isn't set. Commits."""
    body = json.dumps(req.payload, separators=(",", ":"), default=str)
    if len(body) > MAX_DRAFT_JSON:
        raise ValueError("The draft is too large to save.")
    account = req.customer_account.strip()
    params = {
        "u": username[:50],
        "src": req.source,
        "acc": account,
        "name": (req.customer_name or "")[:100],
        "lines": req.line_count,
        "total": req.total_incl,
        "json": body,
    }

    other = _find_for_customer(db, username, account)
    if other and other.id != req.id:
        if not req.replace:
            raise DraftConflict(other)
        db.execute(text(f"DELETE FROM {DRAFT_TABLE} WHERE idDraft = :id"), {"id": other.id})

    draft_id: Optional[int] = None
    if req.id is not None:
        res = db.execute(
            text(
                f"""
                UPDATE {DRAFT_TABLE}
                SET cSource = :src, cCustomerAccount = :acc, cCustomerName = :name,
                    iLines = :lines, fTotalIncl = :total, cDraftJson = :json,
                    dtUpdatedAt = SYSDATETIME()
                WHERE idDraft = :id AND cUsername = :u
                """
            ),
            {**params, "id": req.id},
        )
        if res.rowcount:
            draft_id = req.id
    if draft_id is None:
        # New, or the one being re-saved was deleted meanwhile (expired).
        row = db.execute(
            text(
                f"""
                INSERT INTO {DRAFT_TABLE}
                    (cUsername, cSource, cCustomerAccount, cCustomerName, iLines,
                     fTotalIncl, cDraftJson)
                OUTPUT INSERTED.idDraft
                VALUES (:u, :src, :acc, :name, :lines, :total, :json)
                """
            ),
            params,
        ).first()
        draft_id = int(row[0])
    db.commit()
    saved = get_draft(db, draft_id, username)
    assert saved is not None
    return DraftSummary(**saved.model_dump(exclude={"payload"}))


def delete_draft(db: Session, draft_id: int, username: str) -> bool:
    """Caller commits."""
    res = db.execute(
        text(f"DELETE FROM {DRAFT_TABLE} WHERE idDraft = :id AND cUsername = :u"),
        {"id": draft_id, "u": username},
    )
    return bool(res.rowcount)


def delete_after_order(db: Session, draft_id: Optional[int], username: str) -> None:
    """The order was placed: its draft is done. Best-effort, never fails
    the order."""
    if draft_id is None:
        return
    try:
        delete_draft(db, draft_id, username)
        db.commit()
    except Exception:  # noqa: BLE001
        logger.warning("drafts: could not delete draft %s after its order", draft_id, exc_info=True)
        db.rollback()
