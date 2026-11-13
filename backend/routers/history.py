import logging
from datetime import date, timedelta
from io import BytesIO
from statistics import median
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from services.forecasting import weeks as fc_weeks
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
import schemas
from services.pricing import resolve_customer_prices
from services import short_cache
from services.contact_log import delivery_outcomes_sql
from services.forecasting import suggestions as sugg

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/history",
    tags=["history"],
    dependencies=[Depends(get_current_user)],
)


def _week_start(d: date) -> date:
    """Monday that opens d's ISO week (see services/forecasting/weeks.py)."""
    return fc_weeks.week_start(d)


@router.get("/routes", response_model=List[schemas.HistoryRoute])
def list_routes(db: Session = Depends(get_db)):
    sql = """
        SELECT DISTINCT LTRIM(RTRIM(Route)) AS route
        FROM [SOAssistant].dbo._vw_SORoute
        WHERE Route IS NOT NULL AND LTRIM(RTRIM(Route)) <> ''
        ORDER BY route
    """
    rows = db.execute(text(sql)).mappings().all()
    return [schemas.HistoryRoute(id=r["route"], display=r["route"]) for r in rows]


@router.get("/customers", response_model=List[schemas.HistoryCustomer])
def list_customers(route: str = Query(...), db: Session = Depends(get_db)):
    sql = """
        SELECT AccountCode, CustomerName
        FROM [SOAssistant].dbo._vw_SOCustomers
        WHERE LTRIM(RTRIM(Route)) = :route
          AND AccountCode IS NOT NULL AND LTRIM(RTRIM(AccountCode)) <> ''
          AND CustomerName IS NOT NULL
        ORDER BY CustomerName
    """
    rows = db.execute(text(sql), {"route": route.strip()}).mappings().all()
    return [
        schemas.HistoryCustomer(
            id=(r["AccountCode"] or "").strip(),
            display=(r["CustomerName"] or "").strip(),
        )
        for r in rows
    ]


def _date_column(by: str) -> str:
    """Whitelist mapping so endpoints can swap OrderDate / DeliveryDate safely
    (dict lookup, not user string interpolation, so there's no SQL injection
    risk even though we're building the WHERE column name in Python)."""
    return {"order": "OrderDate", "delivery": "DeliveryDate"}[by]


def _resolve_range(
    on_date: Optional[date],
    from_date: Optional[date],
    to_date: Optional[date],
) -> tuple[date, date]:
    """Collapse the three date query params into a (from, to) tuple.

    Precedence: explicit range (from+to) wins; else single-day (on_date);
    else default both to today. Swaps if the caller gave them in reverse."""
    if from_date is not None and to_date is not None:
        return (from_date, to_date) if from_date <= to_date else (to_date, from_date)
    single = on_date or date.today()
    return single, single


# Contact-log table went live on this date; rows for earlier dates must
# render blank in the Reason column (not "Not contacted"), since the
# feature simply didn't exist. Keep in sync with the frontend constant in
# DeliveriesScreen.tsx.
_CONTACT_LOG_START_DATE = date(2026, 10, 26)


# Orders + ordered litres per (customer, date) for the Deliveries report and
# its export. One InvNum row per OrderNum: Evolution's split-order copies
# (DocState 3) carry the same full lines as the archived original, so the
# original is preferred and the copy only counts when the original isn't in
# the range. FORCESEEK on the lines: without it SQL Server scans every
# invoice line ever (~75k reads on dev) instead of ~60 seeks per order —
# measured 2026-11-11: one delivery day ~100-180 ms, a week ~350 ms.
_DELIVERY_TOTALS_SQL = """
    WITH picked AS (
        SELECT NUM.AutoIndex, NUM.OrderNum, NUM.AccountID,
               CAST(NUM.{date_col} AS date) AS tx_date,
               ROW_NUMBER() OVER (
                   PARTITION BY NUM.OrderNum
                   ORDER BY CASE WHEN NUM.OrigDocID IS NULL OR NUM.OrigDocID = 0
                                  OR NUM.OrigDocID = NUM.AutoIndex THEN 0 ELSE 1 END,
                            NUM.AutoIndex) AS rn
        FROM dbo.InvNum NUM
        WHERE NUM.DocType = 4
          AND NUM.DocState IN (1, 3, 4)
          AND NUM.{date_col} >= :from_date
          AND NUM.{date_col} <  :to_exclusive
    )
    SELECT CL.Account                       AS account,
           CL.Name                          AS customer_name,
           CL.ulARRoute                     AS route,
           P.tx_date                        AS tx_date,
           COUNT(DISTINCT P.OrderNum)       AS order_count,
           SUM(STK.ulIISize * LIN.fQuantity) AS litres
    FROM picked P
    JOIN dbo.Client CL ON CL.DCLink = P.AccountID
    LEFT JOIN dbo._btblInvoiceLines LIN WITH (FORCESEEK) ON LIN.iInvoiceID = P.AutoIndex
    LEFT JOIN dbo.StkItem STK ON STK.StockLink = LIN.iStockCodeID
    WHERE P.rn = 1
    GROUP BY CL.Account, CL.Name, CL.ulARRoute, P.tx_date
"""


def _delivery_totals(db: Session, frm: date, to: date, date_col: str):
    return db.execute(
        text(_DELIVERY_TOTALS_SQL.format(date_col=date_col)),
        {"from_date": frm, "to_exclusive": to + timedelta(days=1)},
    ).mappings().all()


def _routed_customers(db: Session):
    """Every active (not on hold) customer that has a route."""
    return db.execute(
        text("""
            SELECT ulARRoute AS route, Account AS account, Name AS customer_name
            FROM dbo.Client
            WHERE ISNULL(On_Hold, 0) = 0
              AND ulARRoute IS NOT NULL
              AND LTRIM(RTRIM(ulARRoute)) <> ''
        """)
    ).mappings().all()


def _route_label(route) -> str:
    return (route or "").strip() or "No route"


@router.get("/deliveries", response_model=List[schemas.DeliveryCustomer])
def list_deliveries(
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    db: Session = Depends(get_db),
):
    """Deliveries report: every active, routed customer with their order
    count and ordered litres for orders DELIVERED in [from, to], plus any
    customer with such an order who isn't in that list (on hold / no route,
    shown under 'No route'). Runs on screen open, date change and
    (throttled) window focus — cached 20 s per range in the orders_today
    namespace, which every order write already invalidates."""
    frm, to = _resolve_range(None, from_date, to_date)
    return short_cache.get_or_compute(
        ORDERS_TODAY_CACHE, ("deliveries", frm, to), 20,
        lambda: _query_deliveries(db, frm, to),
    )


def _query_deliveries(db: Session, frm: date, to: date) -> List[schemas.DeliveryCustomer]:
    out: dict[str, schemas.DeliveryCustomer] = {}
    for c in _routed_customers(db):
        acc = (c["account"] or "").strip()
        if not acc:
            continue
        out[acc] = schemas.DeliveryCustomer(
            route=_route_label(c["route"]), account=acc,
            customer_name=(c["customer_name"] or "").strip(),
        )
    for r in _delivery_totals(db, frm, to, "DeliveryDate"):
        acc = (r["account"] or "").strip()
        row = out.get(acc)
        if row is None:
            row = out[acc] = schemas.DeliveryCustomer(
                route=_route_label(r["route"]), account=acc,
                customer_name=(r["customer_name"] or "").strip(),
            )
        row.order_count += int(r["order_count"] or 0)
        row.litres += float(r["litres"] or 0)
    return list(out.values())


@router.get("/export-orders-per-day")
def export_orders_per_day(
    on_date: Optional[date] = Query(None, alias="date"),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    by: str = Query("order", pattern="^(order|delivery)$"),
    db: Session = Depends(get_db),
):
    """Download an .xlsx of every active, routed customer × date in the
    range. One row per customer per date (so a 3-day range × 500 customers
    = 1500 rows). Columns: Date · Route · Account · Customer · NoOfOrders
    · Litres · Reason. Zero-order rows kept so the Reason column tells the full
    coverage story. Pass `from=&to=` or just `date=`."""
    # Deferred import so the module still loads if openpyxl isn't installed
    # (e.g. a stale venv) — only this endpoint would 500 until pip install.
    from openpyxl import Workbook
    from openpyxl.styles import Font

    frm, to = _resolve_range(on_date, from_date, to_date)
    date_col = _date_column(by)

    # 1. Customer master — one row per active, routed customer.
    customers = list(_routed_customers(db))

    # 2. Per-(account, date) order counts + ordered litres across the range
    # (one InvNum row per OrderNum — see _DELIVERY_TOTALS_SQL).
    order_rows = _delivery_totals(db, frm, to, date_col)
    counts_by_key: dict[tuple[str, date], tuple[int, float]] = {
        ((r["account"] or "").strip(), r["tx_date"]): (int(r["order_count"] or 0), float(r["litres"] or 0))
        for r in order_rows
    }
    # Customers with an order who aren't in the master list (on hold / no
    # route) still belong in the export.
    known = {(c["account"] or "").strip() for c in customers}
    for r in order_rows:
        acc = (r["account"] or "").strip()
        if acc not in known:
            known.add(acc)
            customers.append({"route": "", "account": acc, "customer_name": r["customer_name"]})

    # 3. Latest contact-log outcome per (customer, delivery-date) in the
    # range. Keyed on dDeliveryDate (not dCallDate) so a NO_ORDER logged
    # on the 27th for a 28th-delivery call list lands in the 28 Oct row
    # of the export where the user expects to see it. Partitioning by
    # (customer, delivery-date) — not just customer — so each daily row
    # carries that day's reason, not a bleed-through from a later day.
    # Rows with NULL dDeliveryDate (pre-migration data) are excluded.
    # PLACED rows follow their order's current delivery date, and a
    # cancelled order's PLACED no longer counts (delivery_outcomes_sql).
    outcome_rows = db.execute(
        text(delivery_outcomes_sql(per_day=True)),
        {"from_date": frm, "to_date": to},
    ).mappings().all()
    outcomes_by_key: dict[tuple[str, date], str] = {
        ((r["account"] or "").strip(), r["delivery_date"]): (r["outcome"] or "").strip()
        for r in outcome_rows
    }

    # 4. Build the dense (customer × date) grid. Reason values are
    # uppercase to match the native DB outcome strings (NO_ORDER / CALLBACK
    # / NO_ANSWER / REFUSED) — makes the column grep/filter-friendly in
    # Excel. The on-screen chip uses title-case for readability; the
    # export favours consistency.
    def _reason(count: int, outcome: Optional[str], d: date) -> str:
        if count > 0 or outcome == "PLACED":
            return "PLACED"
        if outcome:
            return outcome
        if d >= _CONTACT_LOG_START_DATE:
            return "NOT_CONTACTED"
        return ""  # pre-feature — don't label

    all_dates: list[date] = []
    cursor = frm
    while cursor <= to:
        all_dates.append(cursor)
        cursor += timedelta(days=1)

    wb = Workbook()
    ws = wb.active
    date_label = "DeliveryDate" if by == "delivery" else "OrderDate"
    range_label = frm.isoformat() if frm == to else f"{frm.isoformat()} to {to.isoformat()}"
    ws.title = f"Orders {range_label}"[:31]  # Excel caps sheet names at 31 chars

    headers = [date_label, "Route", "CustomerAccount", "CustomerName", "NoOfOrders", "Litres", "Reason"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    # Sort customers once; emit day-by-day so the sheet reads as date
    # blocks top-to-bottom. Within each date, Route then Customer name.
    customers_sorted = sorted(
        customers,
        key=lambda r: (_route_label(r["route"]), (r["customer_name"] or "").strip()),
    )

    for d in all_dates:
        for c in customers_sorted:
            route = _route_label(c["route"])
            account = (c["account"] or "").strip()
            name = (c["customer_name"] or "").strip()
            count, litres = counts_by_key.get((account, d), (0, 0.0))
            outcome = outcomes_by_key.get((account, d))
            ws.append([
                d.isoformat(),
                route,
                account,
                name,
                count,
                round(litres, 2),
                _reason(count, outcome, d),
            ])

    # Reasonable default column widths (approximate — openpyxl uses char units).
    for col_letter, width in zip(("A", "B", "C", "D", "E", "F", "G"), (12, 20, 18, 48, 12, 12, 16)):
        ws.column_dimensions[col_letter].width = width
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = (
        f"orders-per-day-{frm.isoformat()}.xlsx"
        if frm == to
        else f"orders-per-day-{frm.isoformat()}_to_{to.isoformat()}.xlsx"
    )
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/orders-today", response_model=List[schemas.CustomerOrderCount])
def list_orders_today(
    route: Optional[str] = Query(None),
    on_date: Optional[date] = Query(None, alias="date"),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to"),
    by: str = Query("order", pattern="^(order|delivery)$"),
    db: Session = Depends(get_db),
):
    """Order counts per customer across a date range (inclusive). Pass
    `from=&to=` for a range or `date=` for a single day; if neither is
    passed, defaults to today.
    `by=order` counts by OrderDate — Order Assistant pill (always single-day).
    `by=delivery` counts by DeliveryDate — Deliveries report (range-capable).
    `route` optional: omit to count across all customers (used by the
    Order Assistant's "placed today" check-mark before a route is picked)."""
    frm, to = _resolve_range(on_date, from_date, to_date)
    date_col = _date_column(by)
    route_key = (route or "").strip()
    # Shared 20 s cache: every rep asks this same question on every window
    # focus. Order create/update/cancel/reschedule invalidate it (see
    # sales_orders.py) so a just-placed order still shows immediately.
    return short_cache.get_or_compute(
        ORDERS_TODAY_CACHE,
        (frm, to, date_col, route_key),
        20,
        lambda: _query_orders_today(db, frm, to, date_col, route_key),
    )


ORDERS_TODAY_CACHE = "orders_today"


def _query_orders_today(db: Session, frm: date, to: date, date_col: str, route: str):
    # Plain range on the raw column (no CAST) so an index on the date
    # column can be used; `< to + 1 day` keeps the inclusive-end semantics.
    where = [
        "NUM.DocType = 4",
        "NUM.DocState IN (1, 3, 4)",
        f"NUM.{date_col} >= :from_date",
        f"NUM.{date_col} < :to_exclusive",
    ]
    params: dict = {"from_date": frm, "to_exclusive": to + timedelta(days=1)}
    if route:
        where.append("LTRIM(RTRIM(CL.ulARRoute)) = :route")
        params["route"] = route

    # COUNT DISTINCT OrderNum (not COUNT rows): Evolution splits a
    # partially-processed order into multiple InvNum rows sharing the same
    # OrderNum, so plain COUNT would double-count a split order. The user
    # sees one logical order, not the fulfilment slices.
    sql = f"""
        SELECT CL.Account AS account, COUNT(DISTINCT NUM.OrderNum) AS order_count
        FROM dbo.InvNum NUM
        JOIN dbo.Client CL ON CL.DCLink = NUM.AccountID
        WHERE {' AND '.join(where)}
        GROUP BY CL.Account
    """
    rows = db.execute(text(sql), params).mappings().all()
    return [
        schemas.CustomerOrderCount(
            account=(r["account"] or "").strip(),
            order_count=int(r["order_count"] or 0),
        )
        for r in rows
    ]


@router.get("/deal-items", response_model=List[schemas.CustomerDealItem])
def list_customer_deal_items(
    customer_id: str = Query(...),
    as_of: date = Query(...),
    warehouse_code: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Stock items with an active Customer or Group Deal for the customer
    at the rep's warehouse, as of the rep's session date. Lets the Order
    Assistant surface dealed products the customer has never bought so the
    rep can still offer them during the call. Category comes from
    `StkItem.ulIIMainCategory` — the same column `_vw_SOCustomerInvoiceHistory`
    uses, so a dealed item lands in the same bucket it would land in if it
    had history."""
    where = [
        "pr.Account = :account",
        "pr.PriceSource IN ('Customer Deal', 'Group Deal')",
        "STK.ItemActive = 1",
    ]
    params: dict = {"account": customer_id.strip(), "as_of": as_of}
    if warehouse_code and warehouse_code.strip():
        where.append("pr.WhseCode = :whse")
        params["whse"] = warehouse_code.strip()

    sql = f"""
        SELECT DISTINCT
            pr.StockCode               AS stock_code,
            STK.Description_1          AS description,
            STK.ulIIMainCategory       AS category,
            pr.PriceSource             AS source,
            pr.ExclPrice               AS unit_price,
            pr.InclPrice               AS unit_price_incl
        FROM [SOAssistant].dbo._fn_SOCustomerPriceAsOf(:as_of) pr
        JOIN dbo.StkItem STK ON STK.StockLink = pr.iStockID
        WHERE {' AND '.join(where)}
        ORDER BY pr.StockCode
    """
    rows = db.execute(text(sql), params).mappings().all()
    return [
        schemas.CustomerDealItem(
            stock_code=(r["stock_code"] or "").strip(),
            description=(r["description"] or "").strip(),
            category=(r["category"] or "Uncategorized").strip() or "Uncategorized",
            source=(r["source"] or "Price List"),
            unit_price=float(r["unit_price"] or 0),
            unit_price_incl=float(r["unit_price_incl"] or 0),
        )
        for r in rows
        if r["stock_code"]
    ]


def _parse_size(raw) -> Optional[float]:
    """StkItem.ulIISize is a free-text UDF ('0.5', '0.350', occasionally
    blank or junk). Return a float, or None when it isn't a clean number."""
    if raw is None:
        return None
    try:
        return float(str(raw).strip().replace(",", "."))
    except ValueError:
        return None


@router.get("/orders", response_model=List[schemas.HistoricalOrder])
def list_orders(customer_id: str = Query(...), db: Session = Depends(get_db)):
    sql = """
        SELECT
            Route, CustomerAccName, CustomerAccount, Category,
            StockCode, StockCodeDesc, Size,
            TxDate, Order_No, Quantity, ExclAmount, VAT, InclAmount, OnOrder
        FROM [SOAssistant].dbo._vw_SOCustomerInvoiceHistory
        WHERE CustomerAccount = :account
          AND TxDate >= DATEADD(month, -3, CAST(GETDATE() AS DATE))
    """
    rows = db.execute(text(sql), {"account": customer_id}).mappings().all()

    orders: dict[str, dict] = {}

    for row in rows:
        order_num = row["Order_No"]
        if not order_num:
            continue
        order_num = str(order_num).strip()

        if order_num not in orders:
            tx_date = row["TxDate"]
            orders[order_num] = {
                "orderNum": order_num,
                "orderDate": tx_date.isoformat() if tx_date else None,
                "route": (row["Route"] or "").strip(),
                "customerName": (row["CustomerAccName"] or "").strip(),
                "_items": {},
                "totalQuantity": 0.0,
                "totalExclAmount": 0.0,
                "totalVat": 0.0,
                "totalInclAmount": 0.0,
            }

        # StockCode is its own column now — only split StockCodeDesc to peel
        # the "<code>-" prefix off and get the description.
        stock_code = (row["StockCode"] or "").strip()
        desc_full = (row["StockCodeDesc"] or "").strip()
        if stock_code and desc_full.startswith(stock_code + "-"):
            description = desc_full[len(stock_code) + 1:].strip()
        else:
            description = desc_full
        product_key = f"{stock_code}-{description}"

        quantity = float(row["Quantity"] or 0)
        excl = float(row["ExclAmount"] or 0)
        vat = float(row["VAT"] or 0)
        incl = float(row["InclAmount"] or 0)

        items = orders[order_num]["_items"]
        if product_key not in items:
            items[product_key] = {
                "stockCode": stock_code,
                "description": description,
                "category": (row["Category"] or "Uncategorized").strip() or "Uncategorized",
                "size": _parse_size(row["Size"]),
                "quantity": 0.0,
                "exclAmount": 0.0,
                "vat": 0.0,
                "inclAmount": 0.0,
                "onOrder": False,
            }
        line = items[product_key]
        line["quantity"] += quantity
        line["exclAmount"] += excl
        line["vat"] += vat
        line["inclAmount"] += incl
        if bool(row["OnOrder"]):
            line["onOrder"] = True

        orders[order_num]["totalQuantity"] += quantity
        orders[order_num]["totalExclAmount"] += excl
        orders[order_num]["totalVat"] += vat
        orders[order_num]["totalInclAmount"] += incl

    result: List[schemas.HistoricalOrder] = []
    for order in orders.values():
        items_list = list(order.pop("_items").values())
        order["items"] = items_list
        order["itemCount"] = len(items_list)
        result.append(schemas.HistoricalOrder(**order))

    result.sort(key=lambda o: o.orderDate or "", reverse=True)
    return result


@router.get("/suggestions", response_model=List[schemas.OrderSuggestion])
def order_suggestions(
    customer_id: str = Query(...),
    as_of: date = Query(...),
    weeks: int = Query(6, ge=2, le=26),
    min_weeks: int = Query(3, ge=1),
    warehouse_code: Optional[str] = Query(None),
    record: bool = Query(False, description="Persist this presentation to _tb_SOOrderSuggestion "
                                            "(Order Assistant only) and return suggestion_id per row"),
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    """
    Suggested reorder basket for a customer.

    Phase 4 -- target-aware: when a materialised management target exists
    for the delivery week (week of `as_of`), it replaces the weekly median
    as the basis, and targeted SKUs are suggested even without enough
    history. Step 6 below applies to both. See
    services/forecasting/suggestions.py.

    Algorithm:
    1. Historical window = last `weeks` COMPLETED calendar weeks (Sun-Sat).
       The current (in-progress) week is handled separately: whatever's
       already been invoiced or ordered for it counts as "already covered"
       and is subtracted from the suggestion. If we included the current
       week in the median set, the suggestion would jump back up the moment
       a pending SO got invoiced — same qty, different source, double
       counting.
    2. Group historical invoice history by (stock_code, week_start).
    3. Keep SKUs whose non-zero-week count in the historical window >= `min_weeks`.
    4. base_median = median of non-zero weekly quantities (rounded half-up).
    5. already_covered = on_order_qty + current_week_invoiced_qty.
    6. adjusted = max(0, base_median − already_covered), snapped to pack size.
    7. Dropped if snapped == 0.
    8. Unit price = resolved from _fn_SOCustomerPriceAsOf(:as_of) (customer/
       group deal beats price list, deals filtered to the rep's session date).
       Historical median prices are NOT used — we want the live price the
       customer would actually be charged on the order being drafted.
    """
    today = date.today()
    current_week_start = _week_start(today)
    # Fetch `weeks` completed weeks + the current (partial) week.
    window_start = current_week_start - timedelta(weeks=weeks)
    # Upper bound = end of CURRENT week (Sat), not just today. An invoiced
    # posting often inherits the SO's OrderDate as its TxDate, and reps set
    # OrderDate to the next business day — so a Monday invoicing for a
    # Tuesday-dated SO lands with TxDate=Tue. Using `today` as the upper
    # bound would drop it from the window and the suggestion wouldn't
    # subtract the already-covered qty.
    current_week_end = current_week_start + timedelta(days=6)

    # Pull both invoiced (OnOrder=0) and pending (OnOrder=1) rows. Invoiced
    # rows drive the historical median — those are window-bounded through
    # end-of-current-week (see above). Pending rows become a per-stock
    # `on_order_qty` that we subtract so we don't suggest something the
    # customer already has coming — those are NOT date-filtered: a pending
    # SO is going to be delivered no matter when its OrderDate falls.
    sql = """
        SELECT StockCode, StockCodeDesc, Category, TxDate, Quantity, ExclAmount, OnOrder
        FROM [SOAssistant].dbo._vw_SOCustomerInvoiceHistory
        WHERE CustomerAccount = :account
          AND (
                (OnOrder = 0 AND TxDate >= :window_start AND TxDate <= :week_end)
             OR OnOrder = 1
          )
    """
    rows = db.execute(
        text(sql),
        {"account": customer_id, "window_start": window_start, "week_end": current_week_end},
    ).mappings().all()

    # Bucket:
    #   { product_key: { stock_code, description, category,
    #                    weekly_invoiced: { week_start: {qty, excl} },  # historical weeks ONLY
    #                    on_order_qty: float,                           # pending (OnOrder=1), any date
    #                    current_week_invoiced: float } }               # invoiced w/ TxDate in current week
    buckets: dict[str, dict] = {}
    for row in rows:
        tx_date = row["TxDate"]
        if not tx_date:
            continue
        if hasattr(tx_date, "date"):
            tx_date = tx_date.date()

        stock_code = (row["StockCode"] or "").strip()
        if not stock_code:
            continue
        desc_full = (row["StockCodeDesc"] or "").strip()
        if desc_full.startswith(stock_code + "-"):
            description = desc_full[len(stock_code) + 1:].strip()
        else:
            description = desc_full

        # View already filters STK.Code <> '999000', but a few crate SKUs
        # with other codes slip through — belt-and-braces.
        if "crate" in description.lower():
            continue

        product_key = f"{stock_code}|{description}"
        bucket = buckets.setdefault(product_key, {
            "stock_code": stock_code,
            "description": description,
            "category": (row["Category"] or "Uncategorized").strip() or "Uncategorized",
            "weekly_invoiced": {},
            "on_order_qty": 0.0,
            "current_week_invoiced": 0.0,
        })

        qty = float(row["Quantity"] or 0)
        if bool(row["OnOrder"]):
            # Pending — adds to coverage.
            bucket["on_order_qty"] += qty
            continue

        wk = _week_start(tx_date)
        if wk >= current_week_start:
            # Invoiced in the current (in-progress) week → coverage, NOT median.
            bucket["current_week_invoiced"] += qty
        else:
            # Historical invoiced week → feeds the median.
            excl = float(row["ExclAmount"] or 0)
            wk_bucket = bucket["weekly_invoiced"].setdefault(wk, {"qty": 0.0, "excl": 0.0})
            wk_bucket["qty"] += qty
            wk_bucket["excl"] += excl

    suggestions: List[schemas.OrderSuggestion] = []
    for bucket in buckets.values():
        non_zero = [w for w in bucket["weekly_invoiced"].values() if w["qty"] > 0]
        if len(non_zero) < min_weeks:
            continue

        qtys = [w["qty"] for w in non_zero]
        raw_qty = median(qtys)
        # Round half-up to nearest whole unit. Pack-size snapping + subtracting
        # the pending qty happens in the enrichment pass below, once we know
        # the item's pack_size (ulIIPerCrate).
        base_median = float(int(raw_qty + 0.5))

        suggestions.append(
            schemas.OrderSuggestion(
                stock_code=bucket["stock_code"],
                description=bucket["description"],
                category=bucket["category"],
                suggested_quantity=base_median,          # replaced during enrichment
                unit_price=0.0,                          # filled by the resolver below
                non_zero_weeks=len(non_zero),
                total_weeks=weeks,
                on_order_qty=bucket["on_order_qty"],
                current_week_invoiced_qty=bucket["current_week_invoiced"],
                base_weekly_qty=base_median,
            )
        )

    # Phase 4: management targets + active forecast for the delivery week.
    # Any failure here (e.g. forecasting tables not deployed) degrades to
    # the legacy suggestion rather than breaking the rep's screen.
    delivery_week = _week_start(as_of)
    try:
        targets_by_code = sugg.load_week_targets(db, customer_id, delivery_week)
        week_forecasts = sugg.load_week_forecasts(db, customer_id, delivery_week)
    except Exception:
        db.rollback()
        log.exception("Target lookup failed for %s; using base suggestions", customer_id)
        targets_by_code, week_forecasts = {}, {}

    targeted_done: set[str] = set()
    for s in suggestions:
        key = s.stock_code.strip().upper()
        s.week_start = delivery_week
        fc = week_forecasts.get(key)
        s.forecast_qty = fc[1] if fc else None
        t = targets_by_code.get(key)
        if t and key not in targeted_done:
            targeted_done.add(key)
            s.source = sugg.SOURCE_TARGET
            s.suggested_quantity = t.target_qty          # weekly basis; coverage subtracted below
            s.target_qty = t.target_qty
            s.target_source = t.source
            s.scenario_names = t.scenario_names
            if s.forecast_qty is None:
                s.forecast_qty = t.forecast_qty

    # Targeted SKUs without enough history for a base suggestion.
    buckets_by_code: dict[str, dict] = {}
    for b in buckets.values():
        buckets_by_code.setdefault(b["stock_code"].strip().upper(), b)
    for key, t in targets_by_code.items():
        if key in targeted_done:
            continue
        b = buckets_by_code.get(key)
        suggestions.append(schemas.OrderSuggestion(
            stock_code=b["stock_code"] if b else t.stock_code,
            description=b["description"] if b else "",          # filled from inventory below
            category=(b["category"] if b else (t.category or "Uncategorized")),
            suggested_quantity=t.target_qty,
            unit_price=0.0,
            non_zero_weeks=len([w for w in b["weekly_invoiced"].values() if w["qty"] > 0]) if b else 0,
            total_weeks=weeks,
            on_order_qty=b["on_order_qty"] if b else 0.0,
            current_week_invoiced_qty=b["current_week_invoiced"] if b else 0.0,
            source=sugg.SOURCE_TARGET,
            forecast_qty=(week_forecasts[key][1] if key in week_forecasts else t.forecast_qty),
            target_qty=t.target_qty,
            target_source=t.source,
            scenario_names=t.scenario_names,
            week_start=delivery_week,
        ))

    # Enrich with inventory info (warehouse list, tax type, uom, availability) in one round-trip
    if suggestions:
        codes = list({s.stock_code for s in suggestions})
        inv_rows = db.execute(
            text(
                """
                SELECT
                    StockCode,
                    MAX(StockDesc)                                              AS description,
                    MAX(CAST(IsUomItem AS int))                                 AS is_uom_item,
                    MAX(CAST(IsWhseItem AS int))                                AS is_whse_item,
                    MAX(Uom)                                                    AS uom,
                    MAX(DefaultTaxTypeId)                                       AS default_tax_type_id,
                    MAX(PackSize)                                               AS pack_size,
                    SUM(QtyOnHand)                                              AS available,
                    STRING_AGG(WhseCode, ',') WITHIN GROUP (ORDER BY WhseCode)  AS warehouse_codes
                FROM [SOAssistant].dbo._vw_SOInventory
                WHERE StockCode IN :codes
                GROUP BY StockCode
                """
            ).bindparams(bindparam("codes", expanding=True)),
            {"codes": codes},
        ).mappings().all()

        inv_by_code = {r["StockCode"]: r for r in inv_rows}
        for s in suggestions:
            inv = inv_by_code.get(s.stock_code)
            if not inv:
                continue
            s.item_description = inv["description"] or s.description
            if not s.description:
                s.description = s.item_description
            s.is_uom_item = bool(inv["is_uom_item"])
            s.is_whse_item = bool(inv["is_whse_item"])
            s.uom = inv["uom"] or ""
            s.default_tax_type_id = (
                int(inv["default_tax_type_id"]) if inv["default_tax_type_id"] else None
            )
            s.available = float(inv["available"] or 0)
            s.warehouse_codes = [
                c for c in (inv["warehouse_codes"] or "").split(",") if c
            ]
            pack = max(1, int(inv["pack_size"] or 1))
            s.pack_size = pack
            # Subtract "already covered this week" (pending SO qty + anything
            # invoiced earlier this week) from the historical median, THEN snap
            # to pack size. Dropping both sources means the suggestion stays
            # the same whether the SO is still pending or just invoiced.
            # (weekly basis = target for TARGET rows, median otherwise).
            # Targets round UP to the next full pack so the ask is met;
            # base suggestions keep the legacy nearest-pack rounding.
            s.suggested_quantity = sugg.remaining_week_qty(   # may be 0 → filtered
                s.suggested_quantity,
                s.on_order_qty + s.current_week_invoiced_qty,
                pack,
                round_up=(s.source == sugg.SOURCE_TARGET),
            )

        # Fill unit_price from the customer/deal price resolver. Suggestions
        # with no priced entry stay at 0 and the rep must enter a price.
        # Both excl + incl are returned so the frontend's Tax Mode toggle can
        # display the exact Price List value without re-deriving.
        price_map = resolve_customer_prices(
            db, customer_id, codes, as_of=as_of, warehouse_code=warehouse_code
        )
        for s in suggestions:
            p = price_map.get(s.stock_code)
            if p:
                s.unit_price = p.excl_price
                s.unit_price_incl = p.incl_price
                s.price_list_broken = p.price_list_broken
                s.price_source = p.source
            else:
                s.price_source = "None"

    # Drop any suggestion that got snapped to 0 crates by the on-order
    # adjustment — rep has nothing meaningful to add, and the HistoryGrid
    # already shows the pending qty for that stock.
    suggestions = [s for s in suggestions if s.suggested_quantity > 0]

    for s in suggestions:
        if not s.description:
            s.description = s.stock_code

    suggestions.sort(
        key=lambda s: (s.source != sugg.SOURCE_TARGET, -s.non_zero_weeks,
                       -(s.suggested_quantity * s.unit_price))
    )

    if record and suggestions:
        rows = []
        for s in suggestions:
            key = s.stock_code.strip().upper()
            rows.append(sugg.PresentationRow(
                stock_code=s.stock_code,
                suggested_qty=s.suggested_quantity,
                source=s.source,
                target_id=(targets_by_code[key].target_id
                           if s.source == sugg.SOURCE_TARGET and key in targets_by_code else None),
                forecast_id=week_forecasts[key][0] if key in week_forecasts else None,
                target_basis=s.target_qty,
                forecast_basis=s.forecast_qty,
            ))
        ids = sugg.record_presentation(
            db, customer=customer_id, delivery_date=as_of, username=username, rows=rows,
        )
        for s in suggestions:
            s.suggestion_id = ids.get(s.stock_code.strip().upper())
    return suggestions


@router.get("/week-targets", response_model=List[schemas.WeekTarget])
def week_targets(
    customer_id: str = Query(...),
    as_of: date = Query(..., description="Delivery / session date; its week (Mon-Sun) is used"),
    db: Session = Depends(get_db),
):
    """Management targets for the customer's delivery week with what's
    already covered (invoiced in that week + open sales orders), for the
    Order Assistant progress strip. Empty when no target is active."""
    week = _week_start(as_of)
    try:
        targets = sugg.load_week_targets(db, customer_id, week)
    except Exception:
        db.rollback()
        log.exception("Week-target lookup failed for %s", customer_id)
        return []
    if not targets:
        return []

    codes = sorted({t.stock_code for t in targets.values()})
    covered: dict[str, float] = {}
    for r in db.execute(text("""
        SELECT StockCode, SUM(Quantity) AS qty
          FROM [SOAssistant].dbo._vw_SOCustomerInvoiceHistory
         WHERE CustomerAccount = :c AND StockCode IN :codes
           AND (OnOrder = 1 OR (OnOrder = 0 AND TxDate >= :ws AND TxDate < :we))
         GROUP BY StockCode
    """).bindparams(bindparam("codes", expanding=True)),
            {"c": customer_id, "codes": codes, "ws": week,
             "we": week + timedelta(days=7)}).mappings():
        covered[(r["StockCode"] or "").strip().upper()] = float(r["qty"] or 0)

    meta: dict[str, dict] = {}
    for r in db.execute(text("""
        SELECT Code, Description_1, ulIIMainCategory, ISNULL(ulIIPerCrate, 1) AS pack
          FROM dbo.StkItem WHERE Code IN :codes
    """).bindparams(bindparam("codes", expanding=True)), {"codes": codes}).mappings():
        meta[(r["Code"] or "").strip().upper()] = dict(r)

    out = []
    for key, t in sorted(targets.items()):
        m = meta.get(key, {})
        out.append(schemas.WeekTarget(
            stock_code=t.stock_code,
            description=(m.get("Description_1") or "").strip(),
            category=(m.get("ulIIMainCategory") or t.category or ""),
            week_start=week,
            target_qty=t.target_qty,
            forecast_qty=t.forecast_qty,
            covered_qty=max(0.0, covered.get(key, 0.0)),
            pack_size=max(1, int(m.get("pack") or 1)),
            source=t.source,
            scenario_names=t.scenario_names,
        ))
    return out
