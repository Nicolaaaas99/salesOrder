"""Draft orders (see services/drafts.py). Every route is scoped to the
signed-in agent: nobody sees or touches another agent's drafts."""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from services.dc_warehouse import check_customer_dc
from services import drafts as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/drafts", tags=["drafts"])

_TABLE_HINT = (
    "Drafts are unavailable: run create_views.sql (table _tb_SODraftOrder) "
    "and its grants block."
)


@router.get("", response_model=List[svc.DraftSummary])
def list_drafts(db: Session = Depends(get_db), username: str = Depends(get_current_user)):
    try:
        return svc.list_drafts(db, username)
    except Exception:  # noqa: BLE001
        logger.warning("drafts: list failed", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=503, detail=_TABLE_HINT)


@router.get("/{draft_id}", response_model=svc.DraftDetail)
def get_draft(draft_id: int, db: Session = Depends(get_db), username: str = Depends(get_current_user)):
    draft = svc.get_draft(db, draft_id, username)
    if draft is None:
        raise HTTPException(
            status_code=404,
            detail="This draft no longer exists (finished, deleted or expired).",
        )
    return draft


@router.post("", response_model=svc.DraftSummary)
def save_draft(
    req: svc.DraftSave,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    # Same rule as placing the order: no draft while the lines come from a
    # different DC than the customer's card (services/dc_warehouse.py).
    lines = req.payload.get("lines") if isinstance(req.payload, dict) else None
    check_customer_dc(
        db, req.customer_account,
        (l.get("warehouse_code") for l in (lines or []) if isinstance(l, dict)),
        action="saving the draft",
    )
    try:
        return svc.save_draft(db, req, username)
    except svc.DraftConflict as c:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"You already have a draft for {c.existing.customer_account}.",
                "existing": c.existing.model_dump(mode="json"),
            },
        )
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:  # noqa: BLE001
        logger.warning("drafts: save failed", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=503, detail=_TABLE_HINT)


@router.delete("/{draft_id}")
def delete_draft(draft_id: int, db: Session = Depends(get_db), username: str = Depends(get_current_user)):
    deleted = svc.delete_draft(db, draft_id, username)
    db.commit()
    return {"deleted": deleted}
