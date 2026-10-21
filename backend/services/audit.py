"""
Audit log for every sales-order action the app places into Evolution.

Lets management tell at a glance whether an order was placed via this app
versus manually in Evolution. We don't rely on an Evolution UDF (which a
user could set themselves) — instead we write to our own table that the
operator Evolution logins don't have INSERT permission on.

Writes are best-effort: a failed audit insert MUST NOT break the user's
save. The row is purely observational.
"""
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

AUDIT_TABLE = "_tb_SOOrderAssistantAudit"

_has_doc_version: Optional[bool] = None


def _detect_doc_version(db: Session) -> bool:
    """Cache whether `InvNum.DocVersion` exists on this Evolution build."""
    global _has_doc_version
    if _has_doc_version is None:
        row = db.execute(
            text(
                "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = 'dbo' "
                "AND TABLE_NAME = 'InvNum' "
                "AND COLUMN_NAME = 'DocVersion'"
            )
        ).first()
        _has_doc_version = row is not None
        logger.info("audit: InvNum.DocVersion %s", "present" if _has_doc_version else "absent")
    return _has_doc_version


def _fetch_invnum_meta(db: Session, order_no: str) -> Optional[dict]:
    """One lookup for the fields we denormalize into the audit row."""
    doc_col = (
        ", inv.DocVersion AS doc_version"
        if _detect_doc_version(db)
        else ", NULL AS doc_version"
    )
    row = db.execute(
        text(f"""
            SELECT inv.AutoIndex AS auto_index,
                   inv.OrdTotIncl AS total_incl,
                   c.Account      AS customer_account
                   {doc_col}
            FROM InvNum inv
            LEFT JOIN Client c ON c.DCLink = inv.AccountID
            WHERE inv.OrderNum = :order_no AND inv.DocType = 4
        """),
        {"order_no": order_no},
    ).mappings().first()
    return dict(row) if row else None


def _resolve_agent_id(db: Session, username: str) -> Optional[int]:
    row = db.execute(
        text(
            "SELECT idAgents FROM dbo._rtblAgents "
            "WHERE cAgentName = :name AND bAgentActive = 1"
        ),
        {"name": (username or "").strip()},
    ).mappings().first()
    return int(row["idAgents"]) if row and row["idAgents"] is not None else None


def write_order_audit(
    db: Session,
    order_no: str,
    action: str,
    username: str,
    app_version: Optional[str],
    customer_account: Optional[str] = None,
) -> None:
    """Insert one audit row for a sales-order action. Swallows errors so a
    failed audit never blocks the user's save. The _tb_SOOrderAssistantAudit
    table must exist beforehand — see the SQL script shared with the admin."""
    try:
        meta = _fetch_invnum_meta(db, order_no) or {}
        agent_id = _resolve_agent_id(db, username)
        customer = customer_account or meta.get("customer_account")

        db.execute(
            text(f"""
                INSERT INTO dbo.{AUDIT_TABLE}
                    (iInvNumAutoIndex, cOrderNum, cAction, iDocVersion,
                     iAgentID, cUsername, cCustomerAccount,
                     fOrderTotalIncl, cAppVersion)
                VALUES
                    (:auto_index, :order_no, :action, :doc_version,
                     :agent_id, :username, :customer,
                     :total_incl, :app_version)
            """),
            {
                "auto_index": meta.get("auto_index"),
                "order_no": order_no,
                "action": action,
                "doc_version": meta.get("doc_version"),
                "agent_id": agent_id,
                "username": username,
                "customer": customer,
                "total_incl": meta.get("total_incl"),
                "app_version": app_version,
            },
        )
        db.commit()
        logger.info(
            "audit: %s %s by %s (agent=%s, ver=%s)",
            action, order_no, username, agent_id, app_version,
        )
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("audit insert failed for %s/%s: %s", action, order_no, e)
