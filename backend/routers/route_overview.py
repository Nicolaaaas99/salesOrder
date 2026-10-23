"""
Analytics — hub of sub-reports (Customer History, Deliveries, Contact Log,
Agent Performance). Each report has its own endpoint under
/api/route-overview/*; the frontend splits them into separate screens.

Customer History returns one weekly-aggregated roll-up across ALL routes
in one shot. The frontend builds a four-level pivot:
    Route → Customer → Category → Stock
and aggregates up from the leaf rows.
"""
import logging
from datetime import date, timedelta
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

import schemas
from auth import get_current_user
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/route-overview",
    tags=["route-overview"],
    dependencies=[Depends(get_current_user)],
)


# Reusable Sunday-of-week expression. @@DATEFIRST-invariant: 1900-01-07 is
# a known Sunday, so the mod-7 of DATEDIFF(day, '19000107', TxDate) gives
# days-since-the-Sunday-of-the-current-week.
_WEEK_START_SQL = (
    "DATEADD(day, "
    "-((DATEDIFF(day, '19000107', TxDate) % 7 + 7) % 7), "
    "CAST(TxDate AS date))"
)


@router.get("/customer-history", response_model=schemas.CustomerHistoryResponse)
def customer_history(
    weeks: int = Query(12, ge=1, le=26),
    db: Session = Depends(get_db),
):
    """Returns the full dataset for the Customer History pivot. One
    weekly-aggregated row per (route, customer, category, stock, week) for
    invoiced sales only; plus the complete route + customer master so
    zero-sale rows still appear in the tree."""
    today = date.today()
    window_start = today - timedelta(weeks=weeks)

    routes = db.execute(
        text("""
            SELECT LTRIM(RTRIM(Route)) AS route
            FROM [SOAssistant].dbo._vw_SORoute
            ORDER BY route
        """)
    ).mappings().all()

    customer_rows = db.execute(
        text("""
            SELECT LTRIM(RTRIM(Route))      AS route,
                   AccountCode               AS account,
                   CustomerName              AS name
            FROM [SOAssistant].dbo._vw_SOCustomers
            WHERE AccountCode IS NOT NULL
              AND Route IS NOT NULL
              AND LTRIM(RTRIM(Route)) <> ''
            ORDER BY Route, CustomerName
        """)
    ).mappings().all()

    sales_sql = f"""
        SELECT
            LTRIM(RTRIM(Route))         AS route,
            CustomerAccount             AS account,
            ISNULL(Category, 'Uncategorized') AS category,
            StockCode                   AS stock_code,
            MAX(StockCodeDesc)          AS stock_desc,
            {_WEEK_START_SQL}           AS week_start,
            SUM(Litre)                  AS week_litres,
            SUM(InclAmount)             AS week_amount_incl,
            MAX(CAST(TxDate AS date))   AS last_sale_date
        FROM [SOAssistant].dbo._vw_SOCustomerInvoiceHistory
        WHERE OnOrder = 0
          AND TxDate >= :window_start
        GROUP BY
            LTRIM(RTRIM(Route)),
            CustomerAccount,
            ISNULL(Category, 'Uncategorized'),
            StockCode,
            {_WEEK_START_SQL}
    """
    sales_rows = db.execute(
        text(sales_sql), {"window_start": window_start}
    ).mappings().all()

    return schemas.CustomerHistoryResponse(
        routes=[(r["route"] or "") for r in routes if r["route"]],
        customers=[
            schemas.CustomerHistoryCustomer(
                route=(r["route"] or "").strip(),
                account=(r["account"] or "").strip(),
                name=(r["name"] or "").strip(),
            )
            for r in customer_rows
            if r["account"]
        ],
        sales=[
            schemas.CustomerHistorySale(
                route=(r["route"] or "").strip(),
                account=(r["account"] or "").strip(),
                category=(r["category"] or "Uncategorized").strip() or "Uncategorized",
                stock_code=(r["stock_code"] or "").strip(),
                stock_desc=(r["stock_desc"] or "").strip(),
                week_start=r["week_start"],
                week_litres=float(r["week_litres"] or 0),
                week_amount_incl=float(r["week_amount_incl"] or 0),
                last_sale_date=r["last_sale_date"],
            )
            for r in sales_rows
            if r["account"] and r["stock_code"]
        ],
    )
