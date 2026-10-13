from datetime import date, timedelta
from statistics import median
from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
import schemas
from services.pricing import resolve_customer_prices

router = APIRouter(
    prefix="/history",
    tags=["history"],
    dependencies=[Depends(get_current_user)],
)


def _week_start_sunday(d: date) -> date:
    """Return the Sunday on or before d."""
    days_since_sunday = (d.weekday() + 1) % 7
    return d - timedelta(days=days_since_sunday)


@router.get("/routes", response_model=List[schemas.HistoryRoute])
def list_routes(db: Session = Depends(get_db)):
    sql = """
        SELECT DISTINCT LTRIM(RTRIM(Route)) AS route
        FROM dbo._vw_SORoute
        WHERE Route IS NOT NULL AND LTRIM(RTRIM(Route)) <> ''
        ORDER BY route
    """
    rows = db.execute(text(sql)).mappings().all()
    return [schemas.HistoryRoute(id=r["route"], display=r["route"]) for r in rows]


@router.get("/customers", response_model=List[schemas.HistoryCustomer])
def list_customers(route: str = Query(...), db: Session = Depends(get_db)):
    sql = """
        SELECT AccountCode, CustomerName
        FROM dbo._vw_SOCustomers
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


@router.get("/orders", response_model=List[schemas.HistoricalOrder])
def list_orders(customer_id: str = Query(...), db: Session = Depends(get_db)):
    sql = """
        SELECT
            Route, CustomerAccName, CustomerAccount, Category,
            StockCode, StockCodeDesc,
            TxDate, Order_No, Quantity, ExclAmount, VAT, InclAmount, OnOrder
        FROM dbo._vw_SOCustomerInvoiceHistory
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
    weeks: int = Query(6, ge=2, le=26),
    min_weeks: int = Query(3, ge=1),
    db: Session = Depends(get_db),
):
    """
    Suggested reorder basket for a customer.

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
    8. Unit price = resolved from _vw_SOCustomerPrice (customer/group deal
       beats price list). Historical median prices are NOT used — we want the
       current live price the customer would actually be charged.
    """
    today = date.today()
    current_week_start = _week_start_sunday(today)
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
        FROM dbo._vw_SOCustomerInvoiceHistory
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

        wk = _week_start_sunday(tx_date)
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
            )
        )

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
                FROM dbo._vw_SOInventory
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
            base_median = s.suggested_quantity
            already_covered = s.on_order_qty + s.current_week_invoiced_qty
            adjusted = max(0.0, base_median - already_covered)
            crates = int(adjusted / pack + 0.5)
            s.suggested_quantity = float(crates * pack)   # may be 0 → filtered

        # Fill unit_price from the customer/deal price resolver. Suggestions
        # with no priced entry stay at 0 and the rep must enter a price.
        # Both excl + incl are returned so the frontend's Tax Mode toggle can
        # display the exact Price List value without re-deriving.
        price_map = resolve_customer_prices(db, customer_id, codes)
        for s in suggestions:
            p = price_map.get(s.stock_code)
            if p:
                s.unit_price = p.excl_price
                s.unit_price_incl = p.incl_price
                s.price_source = p.source
            else:
                s.price_source = "None"

    # Drop any suggestion that got snapped to 0 crates by the on-order
    # adjustment — rep has nothing meaningful to add, and the HistoryGrid
    # already shows the pending qty for that stock.
    suggestions = [s for s in suggestions if s.suggested_quantity > 0]

    suggestions.sort(
        key=lambda s: (-s.non_zero_weeks, -(s.suggested_quantity * s.unit_price))
    )
    return suggestions
