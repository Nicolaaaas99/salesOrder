"""
Customer contact log — endpoints the Order Assistant uses to record what
happened on each customer visit (no-order, callback, no-answer, refused)
and to colour the dropdown dots on the customer picker.

PLACED outcomes are written server-side by the order-save path and should
NOT come through POST /contact-log — the modal only ever picks one of the
four non-placed outcomes.
"""
import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

import schemas
from auth import get_current_user
from database import get_db
from services.contact_log import (
    ALLOWED_OUTCOMES,
    ContactLogError,
    fetch_latest_for_date,
    write_contact_log,
)

logger = logging.getLogger(__name__)

# PLACED only comes from the order-save path. The modal + POST /contact-log
# are limited to the four outcomes a telesales agent can actually log by hand.
MODAL_OUTCOMES = {o for o in ALLOWED_OUTCOMES if o != "PLACED"}


def _app_version(
    x_app_version: Optional[str] = Header(None, alias="X-App-Version"),
) -> Optional[str]:
    return (x_app_version or "").strip()[:32] or None


router = APIRouter(
    prefix="/contact-log",
    tags=["contact-log"],
    dependencies=[Depends(get_current_user)],
)


@router.post("", response_model=dict, status_code=201)
def log_contact_outcome(
    payload: schemas.ContactLogCreate,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
    app_version: Optional[str] = Depends(_app_version),
):
    outcome = (payload.outcome or "").upper().strip()
    if outcome not in MODAL_OUTCOMES:
        raise HTTPException(400, f"outcome must be one of {sorted(MODAL_OUTCOMES)}")

    customer_account = (payload.customer_account or "").strip()
    if not customer_account:
        raise HTTPException(400, "customer_account is required")

    try:
        new_id = write_contact_log(
            db,
            customer_account=customer_account,
            route_code=payload.route_code,
            outcome=outcome,
            note=payload.note,
            follow_up_date=payload.follow_up_date,
            username=username,
            source_app=payload.source_app,
            app_version=app_version,
        )
    except ContactLogError as e:
        # DB-level failure (permissions, constraint, connection). Surface the
        # real message so the modal can tell the agent what to tell the admin.
        raise HTTPException(500, f"Could not log outcome: {e}")

    if new_id is None:
        # Customer account couldn't be resolved to a DCLink.
        raise HTTPException(400, f"Customer '{customer_account}' not found in Client master")

    return {"id": new_id, "outcome": outcome}


@router.get("/by-date", response_model=List[schemas.ContactLogEntry])
def list_latest_for_date(
    on_date: Optional[date] = Query(None, alias="date"),
    db: Session = Depends(get_db),
):
    """Return the latest outcome row for each customer on the given date,
    across all routes (default: today). Used by the customer dropdown to
    colour its dots. The counter at the top of the picker further filters
    this on the client against the current route's customer list."""
    rows = fetch_latest_for_date(db, call_date=on_date)
    return [
        schemas.ContactLogEntry(
            account=(r["account"] or "").strip(),
            outcome=(r["outcome"] or "").strip(),
            note=r.get("note") or None,
            follow_up_date=r.get("follow_up_date"),
            order_no=r.get("order_no") or None,
            username=r.get("username") or "",
        )
        for r in rows
    ]
