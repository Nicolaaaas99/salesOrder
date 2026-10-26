"""
Analytics — hub of sub-reports.

Customer History is served as a **lazy-loaded pivot**: the frontend asks
for one level at a time (routes → customers → categories → stocks). Each
level's query is tightly scoped by the parent dimensions so SQL Server
hits only the slice it needs. Direct to PostST + Client + StkItem (not
through _vw_SOCustomerInvoiceHistory) because the view's UNION with the
open-orders half makes it harder for the optimizer to prune for this
aggregation-heavy call path. The view stays as the source of truth for
the Order Assistant's history grid.
"""
import logging
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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


# ---------------------------------------------------------------------------
# Shared SQL fragments
# ---------------------------------------------------------------------------

# Sunday-of-week expression. @@DATEFIRST-invariant: 1900-01-07 is a known
# Sunday, so (DATEDIFF-days mod 7) gives days-since-the-current-Sunday.
_WEEK_START = (
    "DATEADD(day, "
    "-((DATEDIFF(day, '19000107', ST.TxDate) % 7 + 7) % 7), "
    "CAST(ST.TxDate AS date))"
)

# PostST signs returns as Debit > 0 / Credit = 0; sales as Credit > 0 /
# Debit = 0. Mirror that onto the quantity so credit notes subtract.
_SIGNED_QTY = "CASE WHEN ST.Credit > 0 THEN ST.Quantity ELSE -ST.Quantity END"
_SIGNED_LITRES = f"STK.ulIISize * ({_SIGNED_QTY})"
_SIGNED_INCL = (
    "CASE WHEN ST.Credit > 0 "
    "THEN ST.Credit + ST.Tax_Amount "
    "ELSE -ST.Debit - ST.Tax_Amount END"
)

# Fixed invoice-side filter. Mirrors the WHERE in _vw_SOCustomerInvoiceHistory
# but applied here so we don't go through the view's UNION.
_INVOICE_WHERE = (
    "ST.Id IN ('Crn', 'OInv', 'Inv') "
    "AND ST.TxDate >= :window_start "
    "AND STK.Code <> '999000'"
)


# ---------------------------------------------------------------------------
# Pivot helpers
# ---------------------------------------------------------------------------

def _collect_weekly(agg_rows, key_fields: tuple[str, ...]) -> dict:
    """Fold the per-week aggregate rows into one bucket per group. Each
    group key is a tuple of values at `key_fields`. The returned dict is
    `{ group_key_tuple: {weekly: {date_iso: {amount, litres}},
                          total_amount, total_litres, last_sale} }`."""
    out: dict = {}
    for r in agg_rows:
        key = tuple(r[f] for f in key_fields)
        bucket = out.setdefault(
            key,
            {"weekly": {}, "total_amount": 0.0, "total_litres": 0.0, "last_sale": None},
        )
        week = r["week_start"]
        if week is None:
            continue
        week_iso = week.isoformat() if hasattr(week, "isoformat") else str(week)
        amount = float(r["amount"] or 0)
        litres = float(r["litres"] or 0)
        bucket["weekly"][week_iso] = {"amount": amount, "litres": litres}
        bucket["total_amount"] += amount
        bucket["total_litres"] += litres
        last_sale = r["last_sale"]
        if last_sale and (bucket["last_sale"] is None or last_sale > bucket["last_sale"]):
            bucket["last_sale"] = last_sale
    return out


def _row_from_bucket(
    key: str,
    label: str,
    sublabel: Optional[str],
    has_children: bool,
    bucket: dict,
) -> schemas.PivotRow:
    return schemas.PivotRow(
        key=key,
        label=label,
        sublabel=sublabel,
        has_children=has_children,
        weekly={
            k: schemas.PivotWeekCell(amount=v["amount"], litres=v["litres"])
            for k, v in bucket["weekly"].items()
        },
        total_amount=bucket["total_amount"],
        total_litres=bucket["total_litres"],
        last_sale_date=bucket["last_sale"],
    )


_EMPTY_BUCKET = {"weekly": {}, "total_amount": 0.0, "total_litres": 0.0, "last_sale": None}


# ---------------------------------------------------------------------------
# Level fetchers
# ---------------------------------------------------------------------------

def _fetch_routes(db: Session, window_start: date) -> List[schemas.PivotRow]:
    master = db.execute(
        text("""
            SELECT LTRIM(RTRIM(Route)) AS route
            FROM [SOAssistant].dbo._vw_SORoute
            ORDER BY route
        """)
    ).mappings().all()

    agg_rows = db.execute(
        text(f"""
            SELECT
                LTRIM(RTRIM(CL.ulARRoute)) AS route,
                {_WEEK_START}              AS week_start,
                SUM({_SIGNED_LITRES})      AS litres,
                SUM({_SIGNED_INCL})        AS amount,
                MAX(CAST(ST.TxDate AS date)) AS last_sale
            FROM dbo.PostST ST
            JOIN dbo.Client CL  ON CL.DCLink  = ST.DrCrAccount
            JOIN dbo.StkItem STK ON STK.StockLink = ST.AccountLink
            WHERE {_INVOICE_WHERE}
              AND LTRIM(RTRIM(CL.ulARRoute)) <> ''
            GROUP BY LTRIM(RTRIM(CL.ulARRoute)), {_WEEK_START}
        """),
        {"window_start": window_start},
    ).mappings().all()

    buckets = _collect_weekly(agg_rows, ("route",))

    out: list[schemas.PivotRow] = []
    seen: set[str] = set()
    for r in master:
        route = (r["route"] or "").strip()
        if not route:
            continue
        bucket = buckets.get((route,), _EMPTY_BUCKET)
        out.append(_row_from_bucket(
            key=route, label=route, sublabel=None,
            has_children=True,  # routes always have customers to show
            bucket=bucket,
        ))
        seen.add(route)
    # Any route that showed up in sales but not in the master (edge case
    # with recent route changes) — include so the data isn't lost.
    for (route,), bucket in buckets.items():
        if route in seen or not route:
            continue
        out.append(_row_from_bucket(
            key=route, label=route, sublabel=None, has_children=True, bucket=bucket,
        ))
    out.sort(key=lambda r: -r.total_amount)
    return out


def _fetch_customers(db: Session, window_start: date, route: str) -> List[schemas.PivotRow]:
    master = db.execute(
        text("""
            SELECT AccountCode AS account, CustomerName AS name
            FROM [SOAssistant].dbo._vw_SOCustomers
            WHERE LTRIM(RTRIM(Route)) = :route
              AND AccountCode IS NOT NULL
        """),
        {"route": route},
    ).mappings().all()

    agg_rows = db.execute(
        text(f"""
            SELECT
                CL.Account                 AS account,
                {_WEEK_START}              AS week_start,
                SUM({_SIGNED_LITRES})      AS litres,
                SUM({_SIGNED_INCL})        AS amount,
                MAX(CAST(ST.TxDate AS date)) AS last_sale
            FROM dbo.PostST ST
            JOIN dbo.Client CL  ON CL.DCLink  = ST.DrCrAccount
            JOIN dbo.StkItem STK ON STK.StockLink = ST.AccountLink
            WHERE {_INVOICE_WHERE}
              AND LTRIM(RTRIM(CL.ulARRoute)) = :route
            GROUP BY CL.Account, {_WEEK_START}
        """),
        {"window_start": window_start, "route": route},
    ).mappings().all()

    buckets = _collect_weekly(agg_rows, ("account",))

    out: list[schemas.PivotRow] = []
    seen: set[str] = set()
    for r in master:
        account = (r["account"] or "").strip()
        if not account:
            continue
        name = (r["name"] or "").strip()
        bucket = buckets.get((account,), _EMPTY_BUCKET)
        out.append(_row_from_bucket(
            key=account,
            label=account,
            sublabel=name,
            # Only drill in if there's data; a zero-sale customer has no
            # categories to show.
            has_children=bucket["total_amount"] != 0 or bucket["total_litres"] != 0,
            bucket=bucket,
        ))
        seen.add(account)
    for (account,), bucket in buckets.items():
        if account in seen or not account:
            continue
        out.append(_row_from_bucket(
            key=account, label=account, sublabel=None,
            has_children=True, bucket=bucket,
        ))
    # Stale customers first (null last_sale, then oldest). Big floating-up
    # signal — same ordering we used in the previous eager version.
    def sort_key(row: schemas.PivotRow):
        d = row.last_sale_date
        # None sorts before any date via a sentinel tuple
        return (0, "") if d is None else (1, d.isoformat())
    out.sort(key=sort_key)
    return out


def _fetch_categories(
    db: Session, window_start: date, route: str, account: str,
) -> List[schemas.PivotRow]:
    agg_rows = db.execute(
        text(f"""
            SELECT
                ISNULL(STK.ulIIMainCategory, 'Uncategorized') AS category,
                {_WEEK_START}              AS week_start,
                SUM({_SIGNED_LITRES})      AS litres,
                SUM({_SIGNED_INCL})        AS amount,
                MAX(CAST(ST.TxDate AS date)) AS last_sale
            FROM dbo.PostST ST
            JOIN dbo.Client CL  ON CL.DCLink  = ST.DrCrAccount
            JOIN dbo.StkItem STK ON STK.StockLink = ST.AccountLink
            WHERE {_INVOICE_WHERE}
              AND LTRIM(RTRIM(CL.ulARRoute)) = :route
              AND CL.Account = :account
            GROUP BY ISNULL(STK.ulIIMainCategory, 'Uncategorized'), {_WEEK_START}
        """),
        {"window_start": window_start, "route": route, "account": account},
    ).mappings().all()

    buckets = _collect_weekly(agg_rows, ("category",))
    out = [
        _row_from_bucket(
            key=cat, label=cat, sublabel=None,
            has_children=True,  # any category has at least one stock
            bucket=bucket,
        )
        for (cat,), bucket in buckets.items()
        if cat
    ]
    out.sort(key=lambda r: -r.total_amount)
    return out


def _fetch_stocks(
    db: Session, window_start: date, route: str, account: str, category: str,
) -> List[schemas.PivotRow]:
    # Category stored on StkItem as ulIIMainCategory. Match the same ISNULL
    # fallback that _fetch_categories used so 'Uncategorized' selects the
    # NULL-category rows.
    if category == "Uncategorized":
        cat_filter = "ISNULL(STK.ulIIMainCategory, 'Uncategorized') = 'Uncategorized'"
    else:
        cat_filter = "STK.ulIIMainCategory = :category"

    agg_rows = db.execute(
        text(f"""
            SELECT
                STK.Code                   AS stock_code,
                MAX(STK.Description_1)     AS stock_desc,
                {_WEEK_START}              AS week_start,
                SUM({_SIGNED_LITRES})      AS litres,
                SUM({_SIGNED_INCL})        AS amount,
                MAX(CAST(ST.TxDate AS date)) AS last_sale
            FROM dbo.PostST ST
            JOIN dbo.Client CL  ON CL.DCLink  = ST.DrCrAccount
            JOIN dbo.StkItem STK ON STK.StockLink = ST.AccountLink
            WHERE {_INVOICE_WHERE}
              AND LTRIM(RTRIM(CL.ulARRoute)) = :route
              AND CL.Account = :account
              AND {cat_filter}
            GROUP BY STK.Code, {_WEEK_START}
        """),
        {
            "window_start": window_start,
            "route": route,
            "account": account,
            **({"category": category} if category != "Uncategorized" else {}),
        },
    ).mappings().all()

    # Collect by stock_code; also carry one stock_desc per code from the
    # MAX we selected above (there's only one description per stock, so
    # it's effectively a label passthrough — but SQL requires it in the
    # aggregate layer).
    desc_by_code: dict[str, str] = {}
    for r in agg_rows:
        code = (r["stock_code"] or "").strip()
        if code and code not in desc_by_code:
            desc_by_code[code] = (r["stock_desc"] or "").strip()
    buckets = _collect_weekly(agg_rows, ("stock_code",))

    out = [
        _row_from_bucket(
            key=code,
            label=code,
            sublabel=desc_by_code.get(code) or None,
            has_children=False,
            bucket=bucket,
        )
        for (code,), bucket in buckets.items()
        if code
    ]
    out.sort(key=lambda r: -r.total_amount)
    return out


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/customer-history", response_model=schemas.PivotLevelResponse)
def customer_history(
    level: str = Query(..., pattern="^(routes|customers|categories|stocks)$"),
    route: Optional[str] = Query(None),
    account: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    weeks: int = Query(12, ge=1, le=26),
    db: Session = Depends(get_db),
):
    window_start = date.today() - timedelta(weeks=weeks)

    if level == "routes":
        rows = _fetch_routes(db, window_start)
    elif level == "customers":
        if not route:
            raise HTTPException(400, "route is required for level=customers")
        rows = _fetch_customers(db, window_start, route.strip())
    elif level == "categories":
        if not route or not account:
            raise HTTPException(400, "route and account are required for level=categories")
        rows = _fetch_categories(db, window_start, route.strip(), account.strip())
    else:  # stocks
        if not route or not account or not category:
            raise HTTPException(
                400,
                "route, account and category are required for level=stocks",
            )
        rows = _fetch_stocks(
            db, window_start, route.strip(), account.strip(), category.strip(),
        )

    return schemas.PivotLevelResponse(level=level, rows=rows)
