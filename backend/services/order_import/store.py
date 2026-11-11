"""Order Import storage: the uploaded PO PDFs and orders submitted for review.

Tables (create_views.sql):
  _tb_SOImportDocument  one row per distinct PDF (deduplicated by SHA-256)
  _tb_SOImportReview    an import an agent handed to a supervisor because a
                        line's PO price is too far from ours

Every query runs on a button click (upload / submit / open / approve /
reject) and is keyed by id, or lists one status page — nothing on a timer.
"""
from __future__ import annotations

import hashlib
import json
from typing import List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.order_import.models import (
    ImportReviewDetail,
    ImportReviewSummary,
    ImportValidation,
)
from services.order_import.parsers import ParsedOrder

DOC_TABLE = "[SOAssistant].dbo._tb_SOImportDocument"
REVIEW_TABLE = "[SOAssistant].dbo._tb_SOImportReview"


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def save_document(
    db: Session, data: bytes, file_name: str, username: str, po_number: str = ""
) -> int:
    """Store the PDF (once — the same file uploaded again returns its id)."""
    sha = hashlib.sha256(data).hexdigest()
    row = db.execute(
        text(f"SELECT idDocument FROM {DOC_TABLE} WHERE cSha256 = :sha"),
        {"sha": sha},
    ).first()
    if row:
        return int(row[0])
    row = db.execute(
        text(
            f"""
            INSERT INTO {DOC_TABLE}
                (cSha256, cFileName, iSizeBytes, bData, cUploadedBy, cPONumber)
            OUTPUT INSERTED.idDocument
            VALUES (:sha, :name, :size, :data, :user, :po)
            """
        ),
        {
            "sha": sha,
            "name": (file_name or "order.pdf")[:260],
            "size": len(data),
            "data": data,
            "user": (username or "")[:50],
            "po": (po_number or "")[:50] or None,
        },
    ).first()
    db.commit()
    return int(row[0])


def get_document(db: Session, doc_id: int) -> Optional[dict]:
    row = db.execute(
        text(f"SELECT cFileName, bData FROM {DOC_TABLE} WHERE idDocument = :id"),
        {"id": doc_id},
    ).mappings().first()
    return dict(row) if row else None


def document_for_order(db: Session, order_num: str) -> Optional[int]:
    """The imported PDF an order was created from, or None.

    The document row carries the last order created from it; a review that
    was approved carries its order too (covers a PDF re-used for a later
    order). Keyed lookups (IX_SOImportDocument_Order / IX_SOImportReview_Order),
    run once when an order is opened. Never fails the caller: a missing
    table (not deployed yet) just means "no document".
    """
    if not order_num:
        return None
    try:
        row = db.execute(
            text(
                f"""
                SELECT TOP 1 id FROM (
                    SELECT idDocument AS id, 1 AS pri FROM {DOC_TABLE}
                    WHERE cOrderNum = :ord
                    UNION ALL
                    SELECT iDocumentID, 2 FROM {REVIEW_TABLE}
                    WHERE cOrderNum = :ord AND iDocumentID IS NOT NULL
                ) x
                ORDER BY pri, id DESC
                """
            ),
            {"ord": order_num[:50]},
        ).first()
    except Exception:  # noqa: BLE001
        db.rollback()
        return None
    return int(row[0]) if row else None


def link_document(
    db: Session, doc_id: Optional[int], account: str, order_num: Optional[str] = None
) -> None:
    """Record the customer (and, once created, our order) on the document."""
    if not doc_id:
        return
    db.execute(
        text(
            f"""
            UPDATE {DOC_TABLE}
            SET cCustomerAccount = :acc,
                cOrderNum = COALESCE(:ord, cOrderNum)
            WHERE idDocument = :id
            """
        ),
        {"id": doc_id, "acc": account[:20], "ord": (order_num or "")[:50] or None},
    )
    db.commit()


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------

_SUMMARY_COLS = """
    idReview, cStatus, cCustomerAccount, cCustomerName, cPONumber, dDeliveryDate,
    fPOTotalIncl, iReviewLines, iDocumentID, cSubmittedBy, dtSubmittedAt,
    cSubmitNote, cReviewedBy, dtReviewedAt, cReviewNote, cOrderNum
"""


def _summary(r) -> dict:
    return dict(
        id=int(r["idReview"]),
        status=r["cStatus"],
        customer_account=r["cCustomerAccount"] or "",
        customer_name=r["cCustomerName"] or "",
        po_number=r["cPONumber"] or "",
        delivery_date=r["dDeliveryDate"],
        po_total_incl=r["fPOTotalIncl"],
        review_lines=int(r["iReviewLines"] or 0),
        document_id=r["iDocumentID"],
        submitted_by=r["cSubmittedBy"] or "",
        submitted_at=r["dtSubmittedAt"],
        submit_note=r["cSubmitNote"] or "",
        reviewed_by=r["cReviewedBy"] or "",
        reviewed_at=r["dtReviewedAt"],
        review_note=r["cReviewNote"] or "",
        order_num=r["cOrderNum"] or "",
    )


def create_review(
    db: Session,
    result: ImportValidation,
    document: ParsedOrder,
    order_date,
    description: Optional[str],
    note: Optional[str],
    username: str,
) -> int:
    row = db.execute(
        text(
            f"""
            INSERT INTO {REVIEW_TABLE}
                (cStatus, cCustomerAccount, cCustomerName, cPONumber, iDocumentID,
                 dOrderDate, dDeliveryDate, cDescription, cDocumentJson,
                 fPOTotalIncl, iReviewLines, cSubmittedBy, cSubmitNote)
            OUTPUT INSERTED.idReview
            VALUES
                ('PENDING', :acc, :name, :po, :doc, :od, :dd, :descr, :json,
                 :total, :lines, :user, :note)
            """
        ),
        {
            "acc": result.customer_account,
            "name": (result.customer_name or "")[:100],
            "po": (document.order_no or "")[:50],
            "doc": document.document_id,
            "od": order_date,
            "dd": result.price_date,
            "descr": (description or "")[:40] or None,
            "json": document.model_dump_json(),
            "total": document.total_incl,
            "lines": sum(1 for l in result.lines if l.needs_review),
            "user": (username or "")[:50],
            "note": (note or "").strip()[:500] or None,
        },
    ).first()
    db.commit()
    return int(row[0])


def list_reviews(
    db: Session, status: Optional[str], submitted_by: Optional[str], limit: int = 200
) -> List[ImportReviewSummary]:
    where, params = [], {"limit": limit}
    if status:
        where.append("cStatus = :status")
        params["status"] = status
    if submitted_by:
        where.append("cSubmittedBy = :user")
        params["user"] = submitted_by
    rows = db.execute(
        text(
            f"""
            SELECT TOP (:limit) {_SUMMARY_COLS}
            FROM {REVIEW_TABLE}
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY CASE cStatus WHEN 'PENDING' THEN 0 ELSE 1 END,
                     dtSubmittedAt DESC
            """
        ),
        params,
    ).mappings().all()
    return [ImportReviewSummary(**_summary(r)) for r in rows]


def count_pending_reviews(db: Session) -> int:
    """Reviews waiting for a supervisor (the badge on Sales Order Entry).
    Runs on a 2-minute timer per supervisor session: an index seek on
    IX_SOImportReview_Status over a tiny table. 0 if the table is missing."""
    try:
        return int(db.execute(
            text(f"SELECT COUNT(*) FROM {REVIEW_TABLE} WHERE cStatus = 'PENDING'")
        ).scalar() or 0)
    except Exception:  # noqa: BLE001 — table not created yet
        db.rollback()
        return 0


def get_review(db: Session, review_id: int) -> Optional[ImportReviewDetail]:
    r = db.execute(
        text(
            f"""
            SELECT {_SUMMARY_COLS}, dOrderDate, cDescription, cDocumentJson
            FROM {REVIEW_TABLE} WHERE idReview = :id
            """
        ),
        {"id": review_id},
    ).mappings().first()
    if not r:
        return None
    return ImportReviewDetail(
        **_summary(r),
        document=ParsedOrder(**json.loads(r["cDocumentJson"])),
        order_date=r["dOrderDate"],
        description=r["cDescription"] or "",
    )


def close_review(
    db: Session,
    review_id: int,
    status: str,
    username: str,
    note: Optional[str] = None,
    order_num: Optional[str] = None,
) -> bool:
    """PENDING -> CREATED / REJECTED. False when it was no longer pending."""
    res = db.execute(
        text(
            f"""
            UPDATE {REVIEW_TABLE}
            SET cStatus = :status, cReviewedBy = :user, dtReviewedAt = SYSDATETIME(),
                cReviewNote = :note, cOrderNum = :ord
            WHERE idReview = :id AND cStatus = 'PENDING'
            """
        ),
        {
            "id": review_id,
            "status": status,
            "user": (username or "")[:50],
            "note": (note or "").strip()[:500] or None,
            "ord": (order_num or "")[:50] or None,
        },
    )
    db.commit()
    return (res.rowcount or 0) > 0
