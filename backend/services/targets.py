"""
Litre targets — measurement helper + period/window utilities.

The core function `litres_for` takes a scope filter (any subset of
agent / telesales / sales_rep / route / customer / category / stock)
and a date range, and returns the total litres sold inside that scope
+ range.

Measurement source: `[SOAssistant].dbo._vw_SOCustomerInvoiceHistory` —
the same view the Analytics Customer Sales History screen uses. The
view unions the two legitimate "what was sold" sources:

  • `PostST` invoiced rows (`OInv` / `Inv` / `Crn`) → shipped history.
    Credit notes are signed negative so they subtract.
  • Open sales-order lines (`InvNum` DocType=4 DocState=1) with
    remaining qty = `fQuantity - fQtyProcessed`.

Both halves expose `Litre = ulIISize × signed Quantity`, so targets
don't do their own litre arithmetic.

Using the view keeps target totals in exact agreement with CSH — if
you can see litres on the pivot, those same litres count toward the
target with no divergence from "placed-not-yet-invoiced" edge cases.

Agent attribution uses `_tb_SOOrderAssistantAudit` matched via
`cOrderNum = Order_No`: for an agent-scoped target we only count view
rows whose `Order_No` corresponds to a CREATE audit row for that
agent. `iAgentID` is preferred over `cUsername` so casing drift
("BrendaS" vs "Brendas") never causes a miss.

Caveat: the view caps PostST history at 12 weeks (`TxDate >= GETDATE()
- 84 days`). Daily / Weekly / Monthly periods always fit inside that;
a `Custom` window spanning more than 12 weeks of invoiced history will
under-count. Lift the cap on the view if that becomes an issue.
"""
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


def sunday_of_current_week(d: Optional[date] = None) -> date:
    """Sunday that opens the given day's week (defaults to today).
    Matches the Analytics 12-week pivot columns and the PerformanceStrip
    "this week" bucket."""
    d = d or date.today()
    # isoweekday: Mon=1..Sun=7. Modulo 7 maps Sunday→0.
    return d - timedelta(days=d.isoweekday() % 7)


def resolve_window(period: str, eff_from: date, eff_to: Optional[date]) -> tuple[date, date]:
    """Resolve a target's `cPeriod` + effective dates into the
    `[from, to]` DATE range to measure litres over.

    DAILY / WEEKLY / MONTHLY ignore the effective dates for the window
    (they're on/off validity gates only — the parent layer should have
    filtered the target out if today falls outside the effective range).
    CUSTOM uses the effective dates as the window directly.
    """
    today = date.today()
    period = period.upper()
    if period == "DAILY":
        return today, today
    if period == "WEEKLY":
        return sunday_of_current_week(today), today
    if period == "MONTHLY":
        return today.replace(day=1), today
    if period == "CUSTOM":
        if eff_to is None:
            raise ValueError("CUSTOM period requires a dEffectiveTo bound")
        return eff_from, eff_to
    raise ValueError(f"Unknown period: {period!r}")


def is_active(eff_from: date, eff_to: Optional[date], on_date: Optional[date] = None) -> bool:
    """Is this target in effect on `on_date` (defaults to today)?
    Inclusive on both bounds; a NULL dEffectiveTo means open-ended."""
    on_date = on_date or date.today()
    if on_date < eff_from:
        return False
    if eff_to is not None and on_date > eff_to:
        return False
    return True


def litres_for(
    db: Session,
    *,
    from_date: date,
    to_date: date,
    agent_id: Optional[int] = None,
    username: Optional[str] = None,
    telesale: Optional[str] = None,
    sales_rep: Optional[str] = None,
    route: Optional[str] = None,
    customer_account: Optional[str] = None,
    category: Optional[str] = None,
    stock_code: Optional[str] = None,
) -> float:
    """Sum of litres for the given scope + date range, measured against
    `_vw_SOCustomerInvoiceHistory`. All scope dims AND-combine; any
    left None is "no filter on that dim".

    Agent attribution: when `agent_id` or `username` is set, we only
    count view rows whose `Order_No` has a matching CREATE audit row
    for that agent. `iAgentID` is tried first; falls back to a
    case-insensitive `cUsername` match for legacy audit rows without
    an id. The audit EXISTS subquery has no date filter — an order
    placed in September but invoiced in October still counts toward
    October's measurement for the agent who placed it.
    """
    agent_filter_needed = agent_id is not None or (username or "").strip()
    bind = {
        "from_date": from_date,
        "to_date": to_date,
        "agent_id": agent_id,
        "username": (username or "").strip() or None,
        "telesale": (telesale or "").strip() or None,
        "sales_rep": (sales_rep or "").strip() or None,
        "route": (route or "").strip() or None,
        "customer_account": (customer_account or "").strip() or None,
        "category": (category or "").strip() or None,
        "stock_code": (stock_code or "").strip() or None,
    }

    # Agent-dim filter is an EXISTS on the audit table so compound
    # targets (e.g. "agent + category") stay a single query. The OR
    # inside the EXISTS lets a single parameter pair cover both the
    # id-based path and the legacy username fallback.
    agent_exists_clause = """
        AND EXISTS (
            SELECT 1
            FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit a
            WHERE a.cOrderNum = h.Order_No
              AND a.cAction = 'CREATE'
              AND (
                  (:agent_id IS NOT NULL AND a.iAgentID = :agent_id)
                  OR (:agent_id IS NULL AND :username IS NOT NULL
                      AND LOWER(a.cUsername) = LOWER(:username))
              )
        )
    """ if agent_filter_needed else ""

    sql = text(
        f"""
        SELECT ISNULL(SUM(h.Litre), 0) AS litres
        FROM [SOAssistant].dbo._vw_SOCustomerInvoiceHistory h
        WHERE h.TxDate BETWEEN :from_date AND :to_date
          {agent_exists_clause}
          AND (:telesale IS NULL OR h.TelesalesRep = :telesale)
          AND (:sales_rep IS NULL OR h.SalesRep = :sales_rep)
          AND (:route IS NULL OR LTRIM(RTRIM(h.Route)) = :route)
          AND (:customer_account IS NULL OR h.CustomerAccount = :customer_account)
          AND (:category IS NULL OR ISNULL(h.Category, '') = :category)
          AND (:stock_code IS NULL OR h.StockCode = :stock_code)
        """
    )
    row = db.execute(sql, bind).mappings().first()
    return float(row["litres"] or 0) if row else 0.0
