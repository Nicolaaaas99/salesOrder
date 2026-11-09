"""
Order Intake dashboard — ordered / invoiced qty, litres and excl amount per
hour of capture, split by where the order was created (SO Assistant app vs
Evolution) and by who captured it.

Source of truth is [SOAssistant].dbo._fn_SOOrderIntake(@from, @to): one row
per order (originals only — split-order copies are excluded there), keyed on
InvNum_dCreatedDate. See create_views.sql for the counting rules and
order_intake_checks.sql for the verification queries.

Cost (PERFORMANCE.md): runs on screen open / Refresh / filter change only —
no timers. Range capped at MAX_RANGE_DAYS. The summary is the same answer
for every viewer, so it's cached for CACHE_TTL_SECONDS per range.
"""
import logging
from datetime import date, timedelta
from io import BytesIO
from typing import List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

import schemas
from auth import get_current_user, require_min_role
from database import get_db
from services import short_cache

logger = logging.getLogger(__name__)

# SUPERVISOR + ADMIN only — agents don't see the Order Intake dashboard
# (the hub card is hidden for them too; this is the server-side gate).
router = APIRouter(
    prefix="/analytics/order-intake",
    tags=["analytics"],
    dependencies=[Depends(get_current_user), Depends(require_min_role("SUPERVISOR"))],
)

MAX_RANGE_DAYS = 31
CACHE_TTL_SECONDS = 60
CACHE_NS = "order_intake"

_FN = "[SOAssistant].dbo._fn_SOOrderIntake(:from_date, :to_date)"

_METRICS_SQL = """
    COUNT(*)                AS orders,
    SUM(OrderedQty)         AS ordered_qty,
    SUM(OrderedLitres)      AS ordered_litres,
    SUM(OrderedExclAmnt)    AS ordered_excl,
    SUM(InvoicedQty)        AS invoiced_qty,
    SUM(InvoicedLitres)     AS invoiced_litres,
    SUM(InvoicedExclAmnt)   AS invoiced_excl
"""

SUMMARY_SQL = f"""
    SELECT CreatedDate, CreatedHour, OrderSource, AppScreen, UserName, CorpGroup,
           {_METRICS_SQL}
    FROM {_FN}
    GROUP BY CreatedDate, CreatedHour, OrderSource, AppScreen, UserName, CorpGroup
    ORDER BY CreatedDate, CreatedHour
"""

ORDERS_SQL = f"""
    SELECT OrderNum, CreatedAt, CreatedHour, OrderSource, AppScreen, UserName,
           CorpGroup, CustomerAccount, CustomerName, DocState,
           OrderedQty, OrderedLitres, OrderedExclAmnt,
           InvoicedQty, InvoicedLitres, InvoicedExclAmnt
    FROM {_FN}
    WHERE (:hour IS NULL OR CreatedHour = :hour)
      AND (:source IS NULL OR OrderSource = :source)
      AND (:user_name IS NULL OR UserName = :user_name)
      AND (:corp_group IS NULL OR CorpGroup = :corp_group)
    ORDER BY CreatedAt
"""


def resolve_range(from_date: Optional[date], to_date: Optional[date]) -> Tuple[date, date]:
    """Default to today; swap a reversed range; cap the span."""
    frm = from_date or to_date or date.today()
    to = to_date or frm
    if frm > to:
        frm, to = to, frm
    if (to - frm).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(400, f"Pick a range of at most {MAX_RANGE_DAYS} days.")
    return frm, to


def _f(v) -> float:
    return float(v or 0)


def _metrics(r) -> dict:
    return dict(
        orders=int(r["orders"] or 0),
        ordered_qty=_f(r["ordered_qty"]),
        ordered_litres=_f(r["ordered_litres"]),
        ordered_excl=_f(r["ordered_excl"]),
        invoiced_qty=_f(r["invoiced_qty"]),
        invoiced_litres=_f(r["invoiced_litres"]),
        invoiced_excl=_f(r["invoiced_excl"]),
    )


def _fetch_summary(db: Session, frm: date, to: date) -> List[schemas.OrderIntakeRow]:
    rows = db.execute(
        text(SUMMARY_SQL), {"from_date": frm, "to_date": to}
    ).mappings().all()
    return [
        schemas.OrderIntakeRow(
            date=r["CreatedDate"],
            hour=int(r["CreatedHour"]),
            source=r["OrderSource"],
            app_screen=r["AppScreen"],
            user=(r["UserName"] or "Evolution").strip(),
            corp_group=r["CorpGroup"],
            **_metrics(r),
        )
        for r in rows
    ]


@router.get("", response_model=schemas.OrderIntakeSummary)
def order_intake_summary(
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    """Rows grouped by (date, hour, source, app screen, user, corp group).
    A few hundred rows at most — the frontend pivots and filters them."""
    frm, to = resolve_range(from_date, to_date)
    rows = short_cache.get_or_compute(
        CACHE_NS, (frm, to), CACHE_TTL_SECONDS,
        lambda: _fetch_summary(db, frm, to),
    )
    return schemas.OrderIntakeSummary(from_date=frm, to_date=to, rows=rows)


def _fetch_orders(
    db: Session, frm: date, to: date,
    hour: Optional[int], source: Optional[str],
    user_name: Optional[str], corp_group: Optional[str],
):
    return db.execute(
        text(ORDERS_SQL),
        {
            "from_date": frm, "to_date": to, "hour": hour,
            "source": source or None, "user_name": user_name or None,
            "corp_group": corp_group or None,
        },
    ).mappings().all()


@router.get("/orders", response_model=List[schemas.OrderIntakeOrder])
def order_intake_orders(
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    hour: Optional[int] = Query(None, ge=0, le=23),
    source: Optional[str] = Query(None),
    user: Optional[str] = Query(None),
    corp_group: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """The individual orders behind one chart bar / table cell."""
    frm, to = resolve_range(from_date, to_date)
    rows = _fetch_orders(db, frm, to, hour, source, user, corp_group)
    return [
        schemas.OrderIntakeOrder(
            order_no=r["OrderNum"] or "",
            created_at=r["CreatedAt"],
            hour=int(r["CreatedHour"]),
            source=r["OrderSource"],
            app_screen=r["AppScreen"],
            user=(r["UserName"] or "Evolution").strip(),
            corp_group=r["CorpGroup"],
            customer_account=r["CustomerAccount"] or "",
            customer_name=(r["CustomerName"] or "").strip(),
            doc_state=int(r["DocState"] or 0),
            ordered_qty=_f(r["OrderedQty"]),
            ordered_litres=_f(r["OrderedLitres"]),
            ordered_excl=_f(r["OrderedExclAmnt"]),
            invoiced_qty=_f(r["InvoicedQty"]),
            invoiced_litres=_f(r["InvoicedLitres"]),
            invoiced_excl=_f(r["InvoicedExclAmnt"]),
        )
        for r in rows
    ]


EXPORT_HEADERS = [
    "CreatedAt", "Hour", "OrderNum", "OrderSource", "AppScreen", "UserName",
    "CorpGroup", "CustomerAccount", "CustomerName",
    "OrderedQty", "OrderedLitres", "OrderedExclAmnt",
    "InvoicedQty", "InvoicedLitres", "InvoicedExclAmnt",
]


@router.get("/export")
def order_intake_export(
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    """Order-level Excel export for the range (one row per order)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    frm, to = resolve_range(from_date, to_date)
    rows = _fetch_orders(db, frm, to, None, None, None, None)

    wb = Workbook()
    ws = wb.active
    label = frm.isoformat() if frm == to else f"{frm.isoformat()} to {to.isoformat()}"
    ws.title = f"Intake {label}"[:31]
    ws.append(EXPORT_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append([
            r["CreatedAt"], int(r["CreatedHour"]), r["OrderNum"], r["OrderSource"],
            r["AppScreen"] or "", (r["UserName"] or "").strip(), r["CorpGroup"],
            r["CustomerAccount"], (r["CustomerName"] or "").strip(),
            _f(r["OrderedQty"]), _f(r["OrderedLitres"]), _f(r["OrderedExclAmnt"]),
            _f(r["InvoicedQty"]), _f(r["InvoicedLitres"]), _f(r["InvoicedExclAmnt"]),
        ])
    for i, width in enumerate((19, 6, 16, 13, 18, 16, 14, 16, 40, 11, 13, 15, 11, 13, 15)):
        ws.column_dimensions[chr(ord("A") + i)].width = width
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = (
        f"order-intake-{frm.isoformat()}.xlsx" if frm == to
        else f"order-intake-{frm.isoformat()}_to_{to.isoformat()}.xlsx"
    )
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
