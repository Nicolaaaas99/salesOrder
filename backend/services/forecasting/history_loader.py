"""Load per-leaf weekly history from the data layer.

Two shapes:
  - load_weekly_history()  -> dict[week-start, true_demand_qty] for the model+classifier
  - load_deliveries()      -> list[(delivery_date, qty)] for the pattern learner

Both apply the short-delivery uplift from _tb_SOShortDelivery so the
engine always trains on ESTIMATED TRUE DEMAND, never on clipped sales.
This is correction 3 of the design: fDeliveredQty on the view reflects
what Evolution invoiced (90 L on a stockout day); fEstimatedTrueDemand
on the fact row adds the shortfall back (150 L -- what the customer
actually wanted).

The pure data transformation (combine_true_demand) is split out so it
can be unit tested without a DB.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import config
from . import weeks


# ---------------------------------------------------------------------------
# Pure transformation (unit-testable)
# ---------------------------------------------------------------------------

# Invoice-side lines with NO date cap. Same columns, joins, Id filter and
# credit-note sign convention as the invoice half of
# _vw_SOCustomerInvoiceHistory -- but that view only keeps the last 12
# weeks of PostST (it serves the rep screens), which silently capped
# every 26/52-week window in the engine. Use as a derived table:
#     FROM """ + INVOICE_LINES_SQL + """ il WHERE il.TxDate >= ...
# Always filter on TxDate; OnOrder is 0 on every row (kept so queries
# written against the view work unchanged).
INVOICE_LINES_SQL = """(
    SELECT CL.Account                AS CustomerAccount,
           STK.Code                  AS StockCode,
           STK.ulIIMainCategory      AS Category,
           ST.TxDate,
           CASE WHEN ST.Credit > 0 THEN ST.Quantity ELSE -ST.Quantity END AS Quantity,
           0                         AS OnOrder
      FROM [BA_00_LPD_CON].dbo.PostST  ST
      JOIN [BA_00_LPD_CON].dbo.Client  CL  ON CL.DCLink     = ST.DrCrAccount
      JOIN [BA_00_LPD_CON].dbo.StkItem STK ON STK.StockLink = ST.AccountLink
     WHERE ST.Id IN ('Crn', 'OInv', 'Inv')
       AND STK.Code <> '999000'
)"""


def load_history_start(db: Session) -> Optional[date]:
    """Earliest invoice on record (PostST). Weeks before it are unknown,
    not zero demand -- see classifier.observation_window."""
    v = db.execute(text(
        "SELECT MIN(TxDate) FROM [BA_00_LPD_CON].dbo.PostST WHERE Id IN ('OInv', 'Inv')"
    )).scalar()
    if v is None:
        return None
    return v.date() if hasattr(v, "date") else v


def combine_true_demand(
    delivered_by_week: dict[date, float],
    shortfall_by_week: dict[date, float],
) -> dict[date, float]:
    """Produce the estimated-true-demand per week.

        true_demand(week) = delivered(week) + shortfall(week)

    Both inputs are keyed by week-start. Weeks present in either
    input appear in the output. Weeks absent from both are absent.

    Example (Scenario B from the Phase 1 seed):
        delivered: {2026-10-25: 90}
        shortfall: {2026-10-25: 60}
        -> {2026-10-25: 150}
    """
    all_weeks = set(delivered_by_week) | set(shortfall_by_week)
    return {
        wk: delivered_by_week.get(wk, 0.0) + shortfall_by_week.get(wk, 0.0)
        for wk in all_weeks
    }


# ---------------------------------------------------------------------------
# DB loaders
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LeafContext:
    """Dimension snapshot for a (customer, SKU) leaf. Pulled fresh at
    forecast-run time and denormalised onto fact rows so later queries
    don't need to cross-DB-join to Evolution for every selection."""
    customer_account: str
    stock_code: str
    route: Optional[str]
    dist_centre: Optional[str]
    category: Optional[str]
    telesales: Optional[str]
    sales_rep: Optional[str]


def load_weekly_history(
    db: Session,
    customer_account: str,
    stock_code: str,
    as_of: date,
    lookback_weeks: int = config.CLASSIFICATION_LOOKBACK_WEEKS,
) -> dict[date, float]:
    """Return {week-start: estimated-true-demand} for the leaf.

    Looks at the `lookback_weeks` weeks strictly BEFORE as_of's week-aligned)
    week. Delivered qty comes from INVOICE_LINES_SQL (PostST, uncapped)
    (invoiced lines only -- OnOrder=0). Short-delivery shortfalls from
    _tb_SOShortDelivery are added back to recover true demand.

    Weeks with no activity are absent from the result (not stored as
    zero). The model / classifier handle missing weeks as implicit zeros.
    """
    anchor = weeks.week_start(as_of)
    window_start = anchor - timedelta(days=7 * lookback_weeks)

    # Delivered qty from the invoice-side union. Pending orders (OnOrder=1)
    # are deliberately excluded -- a prediction's job is "what WILL happen",
    # and pending orders aren't delivered yet.
    delivered_rows = db.execute(
        text("""
            SELECT TxDate, Quantity
            FROM """ + INVOICE_LINES_SQL + """ il
            WHERE CustomerAccount = :customer
              AND StockCode       = :stock
              AND OnOrder = 0
              AND TxDate >= :window_start
              AND TxDate <  :anchor
        """),
        {
            "customer": customer_account,
            "stock": stock_code,
            "window_start": window_start,
            "anchor": anchor,
        },
    ).mappings().all()

    delivered_by_week: dict[date, float] = {}
    for r in delivered_rows:
        d = r["TxDate"]
        if d is None:
            continue
        if hasattr(d, "date"):
            d = d.date()
        wk = weeks.week_start(d)
        delivered_by_week[wk] = delivered_by_week.get(wk, 0.0) + float(r["Quantity"] or 0)

    # Shortfall uplift from the dedicated table.
    shortfall_rows = db.execute(
        text("""
            SELECT dDeliveryDate, fShortfallQty
            FROM [SOAssistant].dbo._tb_SOShortDelivery
            WHERE cCustomerAccount = :customer
              AND cStockCode       = :stock
              AND dDeliveryDate >= :window_start
              AND dDeliveryDate <  :anchor
        """),
        {
            "customer": customer_account,
            "stock": stock_code,
            "window_start": window_start,
            "anchor": anchor,
        },
    ).mappings().all()

    shortfall_by_week: dict[date, float] = {}
    for r in shortfall_rows:
        d = r["dDeliveryDate"]
        if d is None:
            continue
        if hasattr(d, "date"):
            d = d.date()
        wk = weeks.week_start(d)
        shortfall_by_week[wk] = shortfall_by_week.get(wk, 0.0) + float(r["fShortfallQty"] or 0)

    return combine_true_demand(delivered_by_week, shortfall_by_week)


def load_deliveries(
    db: Session,
    customer_account: str,
    as_of: date,
    lookback_weeks: int = config.DELIVERY_PATTERN_LOOKBACK_WEEKS,
) -> list[tuple[date, float]]:
    """Return (delivery_date, qty) pairs for the pattern learner.

    All SKUs together -- the pattern is a customer-level fact, not a
    per-SKU one. Returns the invoice-side TxDate as a proxy for delivery
    date (close enough for the day-of-week share: posting typically
    happens same-day or next-day after delivery).
    """
    anchor = weeks.week_start(as_of)
    window_start = anchor - timedelta(days=7 * lookback_weeks)

    rows = db.execute(
        text("""
            SELECT TxDate, Quantity
            FROM """ + INVOICE_LINES_SQL + """ il
            WHERE CustomerAccount = :customer
              AND OnOrder = 0
              AND TxDate >= :window_start
              AND TxDate <  :anchor
        """),
        {
            "customer": customer_account,
            "window_start": window_start,
            "anchor": anchor,
        },
    ).mappings().all()

    out: list[tuple[date, float]] = []
    for r in rows:
        d = r["TxDate"]
        if d is None:
            continue
        if hasattr(d, "date"):
            d = d.date()
        qty = float(r["Quantity"] or 0)
        if qty > 0:
            out.append((d, qty))
    return out


def load_leaf_context(
    db: Session,
    customer_account: str,
    stock_code: str,
) -> Optional[LeafContext]:
    """Pull dimension values for the leaf -- the snapshot that gets
    denormalised onto the forecast / target / actual fact rows.

    Returns None if either the customer or the SKU can't be resolved.
    """
    row = db.execute(
        text("""
            SELECT
                CL.Account         AS customer_account,
                CL.ulARRoute       AS route,
                CL.ulARDistCentre  AS dist_centre,
                CL.ulARTelesalesRep AS telesales,
                REP.Code           AS sales_rep,
                STK.Code           AS stock_code,
                STK.ulIIMainCategory AS category
            FROM [BA_00_LPD_CON].dbo.Client CL
            LEFT JOIN [BA_00_LPD_CON].dbo.SalesRep REP ON REP.idSalesRep = CL.RepID
            CROSS JOIN [BA_00_LPD_CON].dbo.StkItem STK
            WHERE CL.Account = :customer
              AND STK.Code   = :stock
        """),
        {"customer": customer_account, "stock": stock_code},
    ).mappings().first()

    if row is None:
        return None

    return LeafContext(
        customer_account=row["customer_account"],
        stock_code=row["stock_code"],
        route=(row["route"] or None),
        dist_centre=(row["dist_centre"] or None),
        category=(row["category"] or None),
        telesales=(row["telesales"] or None),
        sales_rep=(row["sales_rep"] or None),
    )
