"""
Order Intake dashboard — ordered / invoiced / lost qty, litres and excl amount per
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
    SUM(InvoicedExclAmnt)   AS invoiced_excl,
    SUM(LostQty)            AS lost_qty,
    SUM(LostLitres)         AS lost_litres,
    SUM(LostExclAmnt)       AS lost_excl,
    -- Pending: ordered on orders still unprocessed (DocState 1) in Evolution.
    SUM(CASE WHEN DocState = 1 THEN OrderedQty      ELSE 0 END) AS pending_qty,
    SUM(CASE WHEN DocState = 1 THEN OrderedLitres   ELSE 0 END) AS pending_litres,
    SUM(CASE WHEN DocState = 1 THEN OrderedExclAmnt ELSE 0 END) AS pending_excl
"""

SUMMARY_SQL = f"""
    SELECT CreatedDate, CreatedHour, OrderSource, AppScreen, UserName, CorpGroup,
           DistCentre, Route,
           {_METRICS_SQL}
    FROM {_FN}
    GROUP BY CreatedDate, CreatedHour, OrderSource, AppScreen, UserName, CorpGroup,
             DistCentre, Route
    ORDER BY CreatedDate, CreatedHour
"""

ORDERS_SQL = f"""
    SELECT OrderNum, CreatedAt, CreatedHour, OrderSource, AppScreen, UserName,
           CorpGroup, DistCentre, Route, CustomerAccount, CustomerName, DocState,
           OrderedQty, OrderedLitres, OrderedExclAmnt,
           InvoicedQty, InvoicedLitres, InvoicedExclAmnt,
           LostQty, LostLitres, LostExclAmnt
    FROM {_FN}
    WHERE (:hour IS NULL OR CreatedHour = :hour)
      AND (:source IS NULL OR OrderSource = :source)
      AND (:user_name IS NULL OR UserName = :user_name)
      AND (:corp_group IS NULL OR CorpGroup = :corp_group)
      AND (:dist_centre IS NULL OR DistCentre = :dist_centre)
      AND (:route IS NULL OR Route = :route)
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
        lost_qty=_f(r["lost_qty"]),
        lost_litres=_f(r["lost_litres"]),
        lost_excl=_f(r["lost_excl"]),
        pending_qty=_f(r["pending_qty"]),
        pending_litres=_f(r["pending_litres"]),
        pending_excl=_f(r["pending_excl"]),
    )


def _pending(r) -> dict:
    """An order is pending while it is unprocessed (DocState 1): all of it."""
    is_open = int(r["DocState"] or 0) == 1
    return dict(
        pending_qty=_f(r["OrderedQty"]) if is_open else 0.0,
        pending_litres=_f(r["OrderedLitres"]) if is_open else 0.0,
        pending_excl=_f(r["OrderedExclAmnt"]) if is_open else 0.0,
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
            user=(r["UserName"] or "SDK").strip(),
            corp_group=r["CorpGroup"],
            dist_centre=r["DistCentre"],
            route=r["Route"],
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
    """Rows grouped by (date, hour, source, app screen, user, corp group, DC,
    route).
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
    dist_centre: Optional[str] = None, route: Optional[str] = None,
):
    return db.execute(
        text(ORDERS_SQL),
        {
            "from_date": frm, "to_date": to, "hour": hour,
            "source": source or None, "user_name": user_name or None,
            "corp_group": corp_group or None,
            "dist_centre": dist_centre or None, "route": route or None,
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
    dist_centre: Optional[str] = Query(None),
    route: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """The individual orders behind one chart bar / table cell."""
    frm, to = resolve_range(from_date, to_date)
    rows = _fetch_orders(db, frm, to, hour, source, user, corp_group, dist_centre, route)
    return [
        schemas.OrderIntakeOrder(
            order_no=r["OrderNum"] or "",
            created_at=r["CreatedAt"],
            hour=int(r["CreatedHour"]),
            source=r["OrderSource"],
            app_screen=r["AppScreen"],
            user=(r["UserName"] or "SDK").strip(),
            corp_group=r["CorpGroup"],
            dist_centre=r["DistCentre"],
            route=r["Route"],
            customer_account=r["CustomerAccount"] or "",
            customer_name=(r["CustomerName"] or "").strip(),
            doc_state=int(r["DocState"] or 0),
            ordered_qty=_f(r["OrderedQty"]),
            ordered_litres=_f(r["OrderedLitres"]),
            ordered_excl=_f(r["OrderedExclAmnt"]),
            invoiced_qty=_f(r["InvoicedQty"]),
            invoiced_litres=_f(r["InvoicedLitres"]),
            invoiced_excl=_f(r["InvoicedExclAmnt"]),
            lost_qty=_f(r["LostQty"]),
            lost_litres=_f(r["LostLitres"]),
            lost_excl=_f(r["LostExclAmnt"]),
            **_pending(r),
        )
        for r in rows
    ]


EXPORT_HEADERS = [
    "CreatedAt", "Hour", "OrderNum", "OrderSource", "AppScreen", "Agent",
    "DistCentre", "Route", "CorpGroup", "CustomerAccount", "CustomerName",
    "OrderedQty", "OrderedLitres", "OrderedExclAmnt",
    "InvoicedQty", "InvoicedLitres", "InvoicedExclAmnt",
    "LostQty", "LostLitres", "LostExclAmnt",
    "UnprocessedQty", "UnprocessedLitres", "UnprocessedExclAmnt", "Status",
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
        pend = _pending(r)
        ws.append([
            r["CreatedAt"], int(r["CreatedHour"]), r["OrderNum"], r["OrderSource"],
            r["AppScreen"] or "", (r["UserName"] or "SDK").strip(),
            r["DistCentre"], r["Route"], r["CorpGroup"],
            r["CustomerAccount"], (r["CustomerName"] or "").strip(),
            _f(r["OrderedQty"]), _f(r["OrderedLitres"]), _f(r["OrderedExclAmnt"]),
            _f(r["InvoicedQty"]), _f(r["InvoicedLitres"]), _f(r["InvoicedExclAmnt"]),
            _f(r["LostQty"]), _f(r["LostLitres"]), _f(r["LostExclAmnt"]),
            pend["pending_qty"], pend["pending_litres"], pend["pending_excl"],
            "Unprocessed" if int(r["DocState"] or 0) == 1 else "Processed",
        ])
    for i, width in enumerate((19, 6, 16, 13, 18, 16, 14, 14, 14, 16, 40, 11, 13, 15, 11, 13, 15, 11, 13, 15, 11, 13, 15, 11)):
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
