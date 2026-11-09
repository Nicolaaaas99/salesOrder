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

AUDIT_TABLE = "[SOAssistant].dbo._tb_SOOrderAssistantAudit"

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


_has_source_app: Optional[bool] = None


def _detect_source_app(db: Session) -> bool:
    """Cache whether the audit table has cSourceApp yet. Lets the backend
    deploy ahead of the create_views.sql migration without every audit
    INSERT failing (and with it the litres on the performance strip).
    Only a positive result is cached, so once the migration runs the
    column is picked up without a restart."""
    global _has_source_app
    if not _has_source_app:
        row = db.execute(
            text(
                "SELECT 1 FROM [SOAssistant].sys.columns "
                "WHERE object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOOrderAssistantAudit') "
                "AND name = 'cSourceApp'"
            )
        ).first()
        _has_source_app = row is not None
        if not _has_source_app:
            logger.warning("audit: cSourceApp column missing — run create_views.sql")
    return _has_source_app


def _fetch_invnum_meta(db: Session, order_no: str) -> Optional[dict]:
    """One lookup for the fields we denormalize into the audit row."""
    doc_col = (
        ", inv.DocVersion AS doc_version"
        if _detect_doc_version(db)
        else ", NULL AS doc_version"
    )
    # TOP 1 ... ORDER BY AutoIndex DESC: an OrderNum isn't unique in InvNum
    # (split-order copies share it, and a reissued number can collide with
    # an older order — seen on a dev copy where the audit row pointed at the
    # wrong order). The row just saved is the newest one.
    row = db.execute(
        text(f"""
            SELECT TOP 1 inv.AutoIndex AS auto_index,
                   inv.OrdTotIncl AS total_incl,
                   c.Account      AS customer_account
                   {doc_col}
            FROM InvNum inv
            LEFT JOIN Client c ON c.DCLink = inv.AccountID
            WHERE inv.OrderNum = :order_no AND inv.DocType = 4
            ORDER BY inv.AutoIndex DESC
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
    source_app: Optional[str] = None,
) -> None:
    """Insert one audit row for a sales-order action. Swallows errors so a
    failed audit never blocks the user's save. The _tb_SOOrderAssistantAudit
    table must exist beforehand — see the SQL script shared with the admin.

    `source_app` is the screen that performed the action (ORDER_ASSISTANT /
    SALES_ORDER_ENTRY). Left NULL for RESCHEDULE / CANCEL, which come from
    the shared order-summary modal rather than an order-entry screen."""
    try:
        meta = _fetch_invnum_meta(db, order_no) or {}
        agent_id = _resolve_agent_id(db, username)
        customer = customer_account or meta.get("customer_account")
        with_source = _detect_source_app(db)
        src_col = ", cSourceApp" if with_source else ""
        src_val = ", :source_app" if with_source else ""

        db.execute(
            text(f"""
                INSERT INTO {AUDIT_TABLE}
                    (iInvNumAutoIndex, cOrderNum, cAction, iDocVersion,
                     iAgentID, cUsername, cCustomerAccount,
                     fOrderTotalIncl, cAppVersion{src_col})
                VALUES
                    (:auto_index, :order_no, :action, :doc_version,
                     :agent_id, :username, :customer,
                     :total_incl, :app_version{src_val})
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
                "source_app": (source_app or "")[:32] or None,
            },
        )
        db.commit()
        logger.info(
            "audit: %s %s by %s (agent=%s, ver=%s, source=%s)",
            action, order_no, username, agent_id, app_version, source_app,
        )
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning("audit insert failed for %s/%s: %s", action, order_no, e)
