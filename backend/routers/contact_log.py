"""
Customer contact log — endpoints the Order Assistant uses to record what
happened on each customer visit (no-order, written order, callback,
no-answer, refused)
and to colour the dropdown dots on the customer picker.

PLACED outcomes are written server-side by the order-save path and should
NOT come through POST /contact-log — the modal only ever picks one of the
non-placed outcomes.
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
    fetch_latest_in_delivery_range,
    write_contact_log,
)

logger = logging.getLogger(__name__)

# PLACED only comes from the order-save path. The modal + POST /contact-log
# are limited to the outcomes a telesales agent can actually log by hand.
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
            delivery_date=payload.delivery_date,
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
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    """Latest outcome row per customer. The two forms key off DIFFERENT
    columns on purpose:

      • `date=X` (single day) → filters by dCallDate — what the Order
        Assistant dropdown dots use ("did I handle this customer today?").
      • `from=&to=` (range)   → filters by dDeliveryDate — what the
        Deliveries report Reason column uses ("what's the latest known
        state for this customer's call list on these delivery days?").

    If both forms are given, the range wins. Default: today's dCallDate."""
    if from_date is not None and to_date is not None:
        rows = fetch_latest_in_delivery_range(db, from_date=from_date, to_date=to_date)
    else:
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
