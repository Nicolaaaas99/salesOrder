"""
Customer contact log — one row per contact attempt a telesales agent makes.

Lives in [SOAssistant].dbo._tb_SOCustomerContactLog. The table is append-only;
the dropdown dot colour is computed from the LATEST row per (date, route,
customer). Writes are best-effort — a failed insert never blocks the user.

Outcomes:
    PLACED      — written by the order-save path, not the modal
    NO_ORDER      — customer did not want / need to order today
    WRITTEN_ORDER — customer sent a written order (email / paper); it gets
                    keyed in manually rather than through the assistant
    CALLBACK      — asked to be called back (optional dFollowUpDate)
    NO_ANSWER     — call went to voicemail / no answer
    REFUSED       — refused, on stop, closed

"Opened by mistake" is not a stored outcome — the modal lets the agent
escape without writing a row, so the dropdown dot stays red.

cSourceApp records which screen wrote the row: ORDER_ASSISTANT or
SALES_ORDER_ENTRY (legacy rows before 2026-11-06: 'SO_ASSISTANT').
"""
import logging
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CONTACT_LOG_TABLE = "[SOAssistant].dbo._tb_SOCustomerContactLog"

ALLOWED_OUTCOMES = {
    "PLACED", "NO_ORDER", "WRITTEN_ORDER", "CALLBACK", "NO_ANSWER", "REFUSED",
}


def _resolve_agent_id(db: Session, username: str) -> Optional[int]:
    row = db.execute(
        text(
            "SELECT idAgents FROM dbo._rtblAgents "
            "WHERE cAgentName = :name AND bAgentActive = 1"
        ),
        {"name": (username or "").strip()},
    ).mappings().first()
    return int(row["idAgents"]) if row and row["idAgents"] is not None else None


def _resolve_customer(db: Session, account: str) -> Optional[dict]:
    """Look up DCLink + canonical Account for the customer. We denormalize both
    onto the row so queries don't need to join Client to get a readable code.

    No DB prefix on dbo.Client — the backend connects directly to the Evolution
    company DB (COMPANY_DATABASE env var), so unqualified names resolve there."""
    row = db.execute(
        text(
            "SELECT DCLink, Account FROM dbo.Client "
            "WHERE Account = :account"
        ),
        {"account": (account or "").strip()},
    ).mappings().first()
    return dict(row) if row else None


class ContactLogError(Exception):
    """Raised when a contact-log INSERT fails for a reason the caller needs to
    see (permissions, constraint violations, connectivity). Distinct from the
    silent "customer not found" case which returns None and lets the caller
    decide how to handle it."""


def write_contact_log(
    db: Session,
    *,
    customer_account: str,
    route_code: Optional[str],
    outcome: str,
    username: str,
    source_app: str,
    app_version: Optional[str],
    note: Optional[str] = None,
    follow_up_date: Optional[date] = None,
    order_no: Optional[str] = None,
    inv_num_auto_index: Optional[int] = None,
    call_date: Optional[date] = None,
    delivery_date: Optional[date] = None,
    swallow_db_errors: bool = False,
) -> Optional[int]:
    """Insert one contact-log row. Returns the new idContactLog on success,
    or None when the customer couldn't be resolved / outcome is invalid.

    DB-level failures (permissions, constraint violations) raise
    ContactLogError so the user-facing path can surface the real reason.
    Internal callers that must stay best-effort (e.g. the PLACED write
    inside the order-save path) can pass `swallow_db_errors=True`.

    `call_date` defaults to today (actual day the row was written).
    `delivery_date` is the session's planned delivery date — may differ
    from call_date when the rep is prepping tomorrow in today's session.
    The Deliveries report filters strictly by delivery_date so a NO_ORDER
    logged on the 27th for a 28th-delivery call list shows up when the
    report is filtered to the 28th. Pass None to leave it blank for
    callers that genuinely don't have a target delivery date."""
    outcome = (outcome or "").upper().strip()
    if outcome not in ALLOWED_OUTCOMES:
        logger.warning("contact-log: rejecting unknown outcome %r", outcome)
        return None

    customer = _resolve_customer(db, customer_account)
    if not customer:
        logger.warning("contact-log: customer %r not found", customer_account)
        return None

    agent_id = _resolve_agent_id(db, username)
    call_date = call_date or date.today()

    try:
        row = db.execute(
            text(f"""
                INSERT INTO {CONTACT_LOG_TABLE}
                    (dCallDate, dDeliveryDate, cRouteCode,
                     iCustomerDCLink, cCustomerAccount,
                     cOutcome, cNote, dFollowUpDate,
                     cOrderNum, iInvNumAutoIndex,
                     iAgentID, cUsername, cSourceApp, cAppVersion)
                OUTPUT INSERTED.idContactLog
                VALUES
                    (:call_date, :delivery_date, :route,
                     :dclink, :account,
                     :outcome, :note, :follow_up,
                     :order_no, :auto_index,
                     :agent_id, :username, :source_app, :app_version)
            """),
            {
                "call_date": call_date,
                "delivery_date": delivery_date,
                "route": (route_code or "").strip() or None,
                "dclink": int(customer["DCLink"]),
                "account": customer["Account"],
                "outcome": outcome,
                "note": (note or "").strip()[:200] or None,
                "follow_up": follow_up_date,
                "order_no": order_no,
                "auto_index": inv_num_auto_index,
                "agent_id": agent_id,
                "username": username,
                "source_app": (source_app or "UNKNOWN")[:32],
                "app_version": (app_version or "")[:32] or None,
            },
        ).scalar()
        db.commit()
        logger.info(
            "contact-log: %s %s/call=%s/delivery=%s by %s (route=%s)",
            outcome, customer_account, call_date.isoformat(),
            delivery_date.isoformat() if delivery_date else "-",
            username, route_code,
        )
        return int(row) if row is not None else None
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        logger.warning(
            "contact-log insert failed for %s/%s: %s", outcome, customer_account, e,
        )
        if swallow_db_errors:
            return None
        raise ContactLogError(str(e)) from e


# How far a PLACED row's logged delivery date may sit from its order's real
# one and still be found (the order was moved in Evolution after capture).
PLACED_DATE_SLACK_DAYS = 14


def delivery_outcomes_sql(per_day: bool) -> str:
    """Latest contact-log outcome per customer (per_day: per customer AND
    delivery date) for deliveries in [from_date, to_date].

    A PLACED row follows its ORDER, not the date logged when it was placed:
    its delivery date is the order's current InvNum.DeliveryDate (moved in
    Evolution, or captured for another day than the call list's -- seen
    2026-11-13: logged 13 Nov, delivered + invoiced 12 Nov, so 13 Nov showed
    "Placed" with no order). A PLACED row whose order was cancelled no longer
    counts. Other outcomes keep their logged date. The logged-date window is
    widened by PLACED_DATE_SLACK_DAYS so moved orders are still found; the
    InvNum join is a key lookup (AutoIndex) for PLACED rows only."""
    partition = "cCustomerAccount, dEffectiveDelivery" if per_day else "cCustomerAccount"
    return f"""
        SELECT cCustomerAccount   AS account,
               iCustomerDCLink    AS dclink,
               cOutcome           AS outcome,
               cNote              AS note,
               dFollowUpDate      AS follow_up_date,
               cOrderNum          AS order_no,
               iAgentID           AS agent_id,
               cUsername          AS username,
               dtCreatedAt        AS created_at,
               dEffectiveDelivery AS delivery_date
        FROM (
            SELECT e.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY {partition}
                       ORDER BY dtCreatedAt DESC, idContactLog DESC
                   ) AS rn
            FROM (
                SELECT L.*,
                       COALESCE(CAST(I.DeliveryDate AS date), L.dDeliveryDate) AS dEffectiveDelivery
                FROM {CONTACT_LOG_TABLE} L
                LEFT JOIN dbo.InvNum I
                       ON L.cOutcome = 'PLACED'
                      AND I.AutoIndex = L.iInvNumAutoIndex
                WHERE L.dDeliveryDate BETWEEN DATEADD(DAY, -{PLACED_DATE_SLACK_DAYS}, :from_date)
                                          AND DATEADD(DAY, {PLACED_DATE_SLACK_DAYS}, :to_date)
                  AND ISNULL(I.DocState, 0) <> 7
            ) e
            WHERE e.dEffectiveDelivery BETWEEN :from_date AND :to_date
        ) t
        WHERE t.rn = 1
    """


def fetch_latest_in_delivery_range(
    db: Session,
    *,
    from_date: date,
    to_date: date,
) -> list[dict]:
    """Latest outcome row per customer whose dDeliveryDate falls inside
    [from_date, to_date] inclusive. Used by the Deliveries report Reason
    column — the whole point of filtering on dDeliveryDate (not dCallDate)
    is so a NO_ORDER logged on the 27th for a 28th call list shows up when
    the report is scoped to the 28th. Rows with NULL dDeliveryDate (pre-
    migration data) are intentionally excluded; the user can backfill via
    the template in create_views.sql. PLACED rows follow their order's
    current delivery date (see delivery_outcomes_sql)."""
    rows = db.execute(
        text(delivery_outcomes_sql(per_day=False)),
        {"from_date": from_date, "to_date": to_date},
    ).mappings().all()
    return [dict(r) for r in rows]


def fetch_latest_for_date(
    db: Session,
    *,
    call_date: Optional[date] = None,
) -> list[dict]:
    """Return the latest outcome row per customer for a given date, across
    ALL routes. A customer belongs to one route (Client.ulARRoute is 1:1),
    so there's no value in filtering by route here — the frontend uses this
    to colour every dropdown dot regardless of which route (if any) is
    currently selected. cRouteCode stays on the row as audit metadata."""
    call_date = call_date or date.today()
    rows = db.execute(
        text(f"""
            SELECT cCustomerAccount AS account,
                   iCustomerDCLink  AS dclink,
                   cOutcome         AS outcome,
                   cNote            AS note,
                   dFollowUpDate    AS follow_up_date,
                   cOrderNum        AS order_no,
                   iAgentID         AS agent_id,
                   cUsername        AS username,
                   dtCreatedAt      AS created_at
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY cCustomerAccount
                           ORDER BY dtCreatedAt DESC, idContactLog DESC
                       ) AS rn
                FROM {CONTACT_LOG_TABLE}
                WHERE dCallDate = :call_date
            ) t
            WHERE t.rn = 1
        """),
        {"call_date": call_date},
    ).mappings().all()
    return [dict(r) for r in rows]
