import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

import schemas
from auth import get_current_user
from database import get_db
from routers.lookups import CUSTOMER_CREDIT_SQL, _build_customer_detail
from services.sales_order_service import create_sales_order, update_sales_order

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/sales-orders",
    tags=["sales-orders"],
    dependencies=[Depends(get_current_user)],
)


DOC_STATE_LABELS = {
    0: "Unknown",
    1: "Unprocessed",
    2: "Quote",
    3: "Partial Processed",
    4: "Archived",
    5: "Template",
    6: "Contract Template",
    7: "Cancelled",
}


def _to_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return v.date()
    except AttributeError:
        return None


def _fmt_money(v: float) -> str:
    return f"{v:,.2f}"


def _compute_order_incl_total(payload: schemas.SalesOrderCreate, db: Session) -> float:
    unique_tax_ids = {int(l.tax_type_id) for l in payload.lines if l.tax_type_id is not None}
    rates: dict[int, float] = {}
    if unique_tax_ids:
        id_list = ",".join(str(i) for i in unique_tax_ids)
        rows = db.execute(
            text(f"SELECT idTaxRate AS id, TaxRate AS rate FROM TaxRate WHERE idTaxRate IN ({id_list})")
        ).mappings().all()
        rates = {int(r["id"]): float(r["rate"] or 0) for r in rows}

    inclusive_mode = (payload.tax_mode or "Exclusive").strip().lower() == "inclusive"
    total_excl = 0.0
    total_tax = 0.0

    for line in payload.lines:
        rate = rates.get(line.tax_type_id, 0.0) if line.tax_type_id is not None else 0.0
        gross = line.quantity * line.unit_price
        disc_factor = 1 - (line.discount_percent or 0) / 100
        if inclusive_mode:
            incl = gross * disc_factor
            excl = incl / (1 + rate / 100) if rate else incl
            tax = incl - excl
        else:
            excl = gross * disc_factor
            tax = excl * (rate / 100)
        total_excl += excl
        total_tax += tax

    doc_disc_factor = 1 - (payload.discount_percent or 0) / 100
    return (total_excl + total_tax) * doc_disc_factor


@router.post("", response_model=schemas.SalesOrderCreateResponse, status_code=201)
def place_order(
    payload: schemas.SalesOrderCreate,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    if not payload.lines:
        raise HTTPException(400, "At least one line is required")

    row = db.execute(text(CUSTOMER_CREDIT_SQL), {"account": payload.customer_account}).mappings().first()
    if not row:
        raise HTTPException(404, f"Customer '{payload.customer_account}' not found")

    customer = _build_customer_detail(row)

    # Only hard-block when the customer is flagged with CheckTerms = 1. For
    # CheckTerms = 0 customers the UI still shows the OVER LIMIT / OVER TERMS
    # badges so the rep sees the warning, but we let the order through.
    if customer.check_terms:
        if customer.over_terms:
            term_desc = f"{customer.terms_days} days" if customer.terms_days > 0 else "current period"
            raise HTTPException(
                400,
                f"{customer.name} is over their payment terms "
                f"({term_desc}) with {_fmt_money(customer.overdue_amount)} in overdue invoices. "
                f"Sales order cannot be created.",
            )

        if customer.credit_limit > 0:
            order_total = _compute_order_incl_total(payload, db)
            breakdown = (
                f"Outstanding invoices: {_fmt_money(customer.total_outstanding)}. "
                f"Existing sales orders: {_fmt_money(customer.total_ordered)}. "
                f"Credit limit: {_fmt_money(customer.credit_limit)}."
            )
            if customer.over_credit_limit:
                raise HTTPException(
                    400,
                    f"{customer.name} is already over their credit limit "
                    f"by {_fmt_money(-customer.available_credit)}. {breakdown} "
                    f"Sales order cannot be created.",
                )
            if order_total > customer.available_credit:
                raise HTTPException(
                    400,
                    f"This order ({_fmt_money(order_total)}) would push {customer.name} "
                    f"over their credit limit. Available credit: {_fmt_money(customer.available_credit)}. "
                    f"{breakdown}",
                )

    try:
        return create_sales_order(payload, agent_username=username)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to create sales order: {e}")


SEARCH_SQL = """
    SELECT TOP (:limit)
        inv.OrderNum      AS order_no,
        inv.ExtOrderNum   AS external_order,
        inv.Description   AS description,
        inv.InvDate       AS invoice_date,
        inv.OrderDate     AS order_date,
        inv.DocState      AS doc_state,
        inv.OrdTotIncl    AS total_incl,
        c.Account         AS customer_account,
        c.Name            AS customer_name
    FROM InvNum inv
    LEFT JOIN Client c ON c.DCLink = inv.AccountID
    WHERE inv.DocType = 4
      AND inv.DocState IN :states
      AND ISNULL(inv.OrderNum, '') <> ''
      AND (:account IS NULL OR c.Account LIKE :account OR c.Name LIKE :account)
      AND (:order_no IS NULL OR inv.OrderNum LIKE :order_no)
      AND (:ext_order IS NULL OR inv.ExtOrderNum LIKE :ext_order)
      AND (:description IS NULL OR inv.Description LIKE :description)
      AND (:date_from IS NULL OR CAST(inv.OrderDate AS DATE) >= :date_from)
      AND (:date_to   IS NULL OR CAST(inv.OrderDate AS DATE) <= :date_to)
    ORDER BY inv.OrderNum DESC
"""


# Default to the three "real" sales-order states — Unprocessed, Partially
# Processed, Archived. Quotes/templates/cancelled are intentionally excluded
# so the Open dialog stays focused on actual orders.
DEFAULT_SEARCH_STATES = (1, 3, 4)


@router.get("", response_model=List[schemas.SalesOrderSearchResult])
def search_sales_orders(
    account: Optional[str] = Query(None),
    order_no: Optional[str] = Query(None),
    ext_order: Optional[str] = Query(None),
    description: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    states: Optional[List[int]] = Query(None),
    limit: int = Query(200, le=500),
    db: Session = Depends(get_db),
):
    def like(v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        return f"%{v}%" if v else None

    chosen_states = tuple(states) if states else DEFAULT_SEARCH_STATES

    stmt = text(SEARCH_SQL).bindparams(bindparam("states", expanding=True))
    rows = db.execute(
        stmt,
        {
            "account": like(account),
            "order_no": like(order_no),
            "ext_order": like(ext_order),
            "description": like(description),
            "date_from": date_from,
            "date_to": date_to,
            "states": chosen_states,
            "limit": limit,
        },
    ).mappings().all()

    return [
        schemas.SalesOrderSearchResult(
            order_no=r["order_no"] or "",
            external_order=r["external_order"] or "",
            description=r["description"] or "",
            invoice_date=_to_date(r["invoice_date"]),
            order_date=_to_date(r["order_date"]),
            customer_account=r["customer_account"] or "",
            customer_name=r["customer_name"] or "",
            doc_state=int(r["doc_state"] or 0),
            doc_state_label=DOC_STATE_LABELS.get(int(r["doc_state"] or 0), "Unknown"),
            total_incl=float(r["total_incl"] or 0),
            is_archived=int(r["doc_state"] or 0) == 4,
        )
        for r in rows
    ]


BY_ORDER_DATE_SQL = """
    SELECT
        inv.OrderNum      AS order_no,
        inv.ExtOrderNum   AS external_order,
        inv.Description   AS description,
        inv.InvDate       AS invoice_date,
        inv.OrderDate     AS order_date,
        inv.DocState      AS doc_state,
        inv.OrdTotIncl    AS total_incl,
        c.Account         AS customer_account,
        c.Name            AS customer_name
    FROM InvNum inv
    LEFT JOIN Client c ON c.DCLink = inv.AccountID
    WHERE inv.DocType = 4
      AND inv.DocState IN (1, 3, 4)
      AND ISNULL(inv.OrderNum, '') <> ''
      AND c.Account = :account
      AND CAST(inv.OrderDate AS DATE) = :on_date
    ORDER BY inv.OrderNum DESC
"""


@router.get("/by-order-date", response_model=List[schemas.SalesOrderSearchResult])
def list_orders_by_order_date(
    account: str = Query(...),
    on_date: date = Query(..., alias="date"),
    db: Session = Depends(get_db),
):
    """Return the sales orders for one customer whose OrderDate matches the
    given calendar day. Mirrors the DocState filter used by the per-day
    counts view (_vw_SOCustomerOrdersPerDay) so the modal list stays
    consistent with the pill count on the Route Overview."""
    rows = db.execute(
        text(BY_ORDER_DATE_SQL),
        {"account": account.strip(), "on_date": on_date},
    ).mappings().all()

    return [
        schemas.SalesOrderSearchResult(
            order_no=r["order_no"] or "",
            external_order=r["external_order"] or "",
            description=r["description"] or "",
            invoice_date=_to_date(r["invoice_date"]),
            order_date=_to_date(r["order_date"]),
            customer_account=r["customer_account"] or "",
            customer_name=r["customer_name"] or "",
            doc_state=int(r["doc_state"] or 0),
            doc_state_label=DOC_STATE_LABELS.get(int(r["doc_state"] or 0), "Unknown"),
            total_incl=float(r["total_incl"] or 0),
            is_archived=int(r["doc_state"] or 0) == 4,
        )
        for r in rows
    ]


DETAIL_HEADER_SQL = """
    SELECT
        inv.AutoIndex     AS auto_index,
        inv.OrderNum      AS order_no,
        inv.Description   AS description,
        inv.ExtOrderNum   AS external_order,
        inv.InvDate       AS invoice_date,
        inv.OrderDate     AS order_date,
        inv.DueDate       AS due_date,
        inv.DeliveryDate  AS delivery_date,
        inv.TaxInclusive  AS tax_inclusive,
        inv.DocState      AS doc_state,
        inv.InvDisc       AS discount_percent,
        inv.ProjectID     AS project_id,
        c.Account         AS customer_account,
        c.Name            AS customer_name,
        p.ProjectCode     AS default_project_code
    FROM InvNum inv
    LEFT JOIN Client c ON c.DCLink = inv.AccountID
    LEFT JOIN Project p ON p.ProjectLink = inv.ProjectID
    WHERE inv.OrderNum = :order_no AND inv.DocType = 4
"""


DETAIL_LINES_SQL = """
    SELECT
        il.iStockCodeID     AS stock_id,
        il.iWarehouseID     AS warehouse_id,
        il.iLineProjectID   AS project_id,
        il.iTaxTypeID       AS tax_type_id,
        il.cDescription     AS description,
        il.fQuantity        AS quantity,
        il.fUnitPriceExcl   AS unit_price_excl,
        il.fUnitPriceIncl   AS unit_price_incl,
        il.fLineDiscount    AS discount_percent,
        si.Code             AS stock_code,
        si.Description_1    AS item_description,
        w.Code              AS warehouse_code,
        p.ProjectCode       AS project_code
    FROM _btblInvoiceLines il
    LEFT JOIN StkItem si ON si.StockLink = il.iStockCodeID
    LEFT JOIN WhseMst w ON w.WhseLink = il.iWarehouseID
    LEFT JOIN Project p ON p.ProjectLink = il.iLineProjectID
    WHERE il.iInvoiceID = :auto_index
    ORDER BY il.idInvoiceLines
"""


def _load_sales_order(order_no: str, db: Session) -> schemas.SalesOrderDetail:
    header = db.execute(text(DETAIL_HEADER_SQL), {"order_no": order_no}).mappings().first()
    if not header:
        raise HTTPException(404, f"Sales order '{order_no}' not found")

    inclusive = bool(header["tax_inclusive"])
    tax_mode = "Inclusive" if inclusive else "Exclusive"
    doc_state = int(header["doc_state"] or 0)

    line_rows = db.execute(
        text(DETAIL_LINES_SQL), {"auto_index": header["auto_index"]}
    ).mappings().all()

    logger.info(
        "load_sales_order: order_no=%s auto_index=%s doc_state=%s line_count=%d",
        order_no,
        header["auto_index"],
        doc_state,
        len(line_rows),
    )

    lines = [
        schemas.SalesOrderLineDetail(
            stock_code=r["stock_code"] or "",
            item_description=r["item_description"] or "",
            description=r["description"] or "",
            warehouse_code=r["warehouse_code"] or "",
            quantity=float(r["quantity"] or 0),
            # Always return both prices so the frontend can display the
            # Excl/Incl toggle without re-deriving. `unit_price` is excl
            # (frontend's invariant); `unit_price_incl` is what the Price
            # List / Evolution stored for incl.
            unit_price=float(r["unit_price_excl"] or 0),
            unit_price_incl=float(r["unit_price_incl"] or 0),
            tax_type_id=int(r["tax_type_id"]) if r["tax_type_id"] else None,
            discount_percent=float(r["discount_percent"] or 0),
            project_code=r["project_code"] or "",
        )
        for r in line_rows
    ]

    # Enrich each line with the warehouses that stock item is set up in.
    # Mirrors the /lookups/inventory aggregation so the warehouse dropdown
    # in the loaded order matches the one shown when picking a fresh item.
    codes = list({l.stock_code for l in lines if l.stock_code})
    if codes:
        inv_rows = db.execute(
            text(
                """
                SELECT
                    StockCode,
                    MAX(CAST(IsWhseItem AS int))                               AS is_whse_item,
                    STRING_AGG(WhseCode, ',') WITHIN GROUP (ORDER BY WhseCode) AS warehouse_codes
                FROM dbo._vw_SOInventory
                WHERE StockCode IN :codes
                GROUP BY StockCode
                """
            ).bindparams(bindparam("codes", expanding=True)),
            {"codes": codes},
        ).mappings().all()
        inv_by_code = {r["StockCode"]: r for r in inv_rows}
        for line in lines:
            inv = inv_by_code.get(line.stock_code)
            if not inv:
                continue
            line.is_whse_item = bool(inv["is_whse_item"])
            line.warehouse_codes = [
                c for c in (inv["warehouse_codes"] or "").split(",") if c
            ]

    return schemas.SalesOrderDetail(
        order_no=header["order_no"] or order_no,
        customer_account=header["customer_account"] or "",
        customer_name=header["customer_name"] or "",
        order_date=_to_date(header["order_date"]),
        due_date=_to_date(header["due_date"]),
        invoice_date=_to_date(header["invoice_date"]),
        delivery_date=_to_date(header["delivery_date"]),
        external_order=header["external_order"] or "",
        default_project_code=header["default_project_code"] or "",
        discount_percent=float(header["discount_percent"] or 0),
        tax_mode=tax_mode,
        description=header["description"] or "",
        doc_state=doc_state,
        doc_state_label=DOC_STATE_LABELS.get(doc_state, "Unknown"),
        is_archived=doc_state == 4,
        lines=lines,
    )


@router.get("/{order_no}", response_model=schemas.SalesOrderDetail)
def get_sales_order(order_no: str, db: Session = Depends(get_db)):
    return _load_sales_order(order_no, db)


@router.put("/{order_no}", response_model=schemas.SalesOrderCreateResponse)
def update_order(
    order_no: str,
    payload: schemas.SalesOrderCreate,
    db: Session = Depends(get_db),
    username: str = Depends(get_current_user),
):
    header = db.execute(
        text(
            "SELECT DocState FROM InvNum WHERE OrderNum = :order_no AND DocType = 4"
        ),
        {"order_no": order_no},
    ).mappings().first()
    if not header:
        raise HTTPException(404, f"Sales order '{order_no}' not found")
    if int(header["DocState"] or 0) != 1:
        raise HTTPException(
            400,
            f"Sales order '{order_no}' has state "
            f"{DOC_STATE_LABELS.get(int(header['DocState'] or 0), 'Unknown')} "
            f"and cannot be edited. Only Unprocessed orders can be updated.",
        )

    if not payload.lines:
        raise HTTPException(400, "At least one line is required")

    try:
        return update_sales_order(order_no, payload, agent_username=username)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Failed to update sales order: {e}")
