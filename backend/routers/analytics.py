"""
Analytics — hub of sub-reports.

Customer History is served as a **lazy-loaded, re-orderable pivot**: the
frontend picks a hierarchy (an ordered list of dimensions chosen from
route / salesrep / telesales / customer / category / stock) and then asks
for one level at a time, drilling down. Each level's query is scoped by
the parent dimensions so SQL Server only hits the slice it needs.

Direct to PostST + Client + StkItem (not through
_vw_SOCustomerInvoiceHistory) because the view's UNION with the
open-orders half makes it harder for the optimizer to prune for this
aggregation-heavy call path. The view stays as the source of truth for
the Order Assistant's history grid.

Each row also carries customer-coverage stats so the UI can rank buckets
by % of customers that have gone stale (no sales in the last 14 days).
For root-level route/salesrep/telesales buckets we augment counts from
the Client master so a route with no sales at all still surfaces at the
top of the "problem routes" list.
"""
import logging
from datetime import date, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from services.forecasting import weeks as fc_weeks
from sqlalchemy import text
from sqlalchemy.orm import Session

import schemas
from auth import get_current_user
from database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/analytics",
    tags=["analytics"],
    dependencies=[Depends(get_current_user)],
)


# ---------------------------------------------------------------------------
# Shared SQL fragments
# ---------------------------------------------------------------------------

# Week-start (Monday) expression, @@DATEFIRST-invariant. See weeks.py.
_WEEK_START = fc_weeks.week_start_sql("ST.TxDate")

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

# A customer is "stale" if their most-recent sale in the window is older
# than 14 days. The threshold is intentionally a plain constant — tweak
# here if the rule changes.
_STALE_DAYS = 14


# ---------------------------------------------------------------------------
# Dimension registry
# ---------------------------------------------------------------------------
# Each dimension defines:
#   • key_expr    — the SQL expression grouped on and compared in WHERE
#                   when it appears as a parent. Must evaluate to a string
#                   key (we CAST ints like RepID to varchar) so values
#                   shuttle through URL params untouched.
#   • label_expr  — SELECT for the row label. If None, the key doubles as
#                   the label (works whenever the key is already in GROUP
#                   BY). SalesRep is the only one that needs MAX() because
#                   REP.Name is on the joined table.
#   • sublabel_expr — SELECT for the row sublabel (shown as a small line
#                   under the label). None → no sublabel.
#   • client_side — True if this dim's key is derivable from Client alone
#                   (no StkItem join). Master-augmentation only applies
#                   when the current dim AND every parent dim is
#                   client-side — otherwise we'd need to filter the
#                   Client master by a transaction-derived column, which
#                   isn't possible.
#   • needs_salesrep_join — True if the SQL requires LEFT JOIN SalesRep.

_DIM_KEY = {
    "route":     "NULLIF(LTRIM(RTRIM(CL.ulARRoute)), '')",
    "salesrep":  "CAST(CL.RepID AS varchar(16))",
    "telesales": "ISNULL(NULLIF(LTRIM(RTRIM(CL.ulARTelesalesRep)), ''), 'None')",
    "customer":  "CL.Account",
    "category":  "ISNULL(STK.ulIIMainCategory, 'Uncategorized')",
    "stock":     "STK.Code",
}

_DIM_LABEL = {
    "route":     None,
    # Salesrep displays `SalesRep.Code` — the short handle like "Vincent",
    # "M M", "Kwena". `SalesRep.Name` is sometimes blank or carries a
    # different full-name string that doesn't match how the business
    # refers to the rep, so we don't use it. If Code is empty the row
    # shows an empty label on purpose (not a fallback to the numeric
    # RepID — see _label_from_sql below).
    "salesrep":  "MAX(REP.Code)",
    "telesales": None,
    "customer":  None,
    "category":  None,
    "stock":     None,
}

_DIM_SUBLABEL = {
    "route":     None,
    "salesrep":  None,
    "telesales": None,
    "customer":  "MAX(CL.Name)",
    "category":  None,
    "stock":     "MAX(STK.Description_1)",
}

# Dims whose key is a column on Client (so Client is a sufficient master
# source for augmentation). Category/Stock live on StkItem and have no
# meaningful master customer list, so they never augment.
_DIM_CLIENT_SIDE = {"route", "salesrep", "telesales", "customer"}

# How each dim's key maps onto _vw_SOCustomerInvoiceHistory. Used by the
# /transactions endpoint — the view already normalizes some of these
# (TelesalesRep is ISNULL'd to 'None'), but the frontend sends values
# produced by the _DIM_KEY expressions above, so we re-apply the same
# normalization on comparison to keep the two sides in lockstep.
_DIM_VIEW_WHERE = {
    "route":     "NULLIF(LTRIM(RTRIM(Route)), '')",
    "salesrep":  "CAST(SalesRepId AS varchar(16))",
    "telesales": "TelesalesRep",   # already ISNULL'd by the view
    "customer":  "CustomerAccount",
    "category":  "ISNULL(Category, 'Uncategorized')",
    "stock":     "StockCode",
}

# Dims that produce a meaningful "customers stale %" number — only these
# get the stale-% primary sort. Category/Stock buckets group transactions
# by item (not by who bought them), so a stale-% there is noise.
_DIM_HAS_STALE = {"route", "salesrep", "telesales", "customer"}

_DIMENSIONS = list(_DIM_KEY.keys())


def _master_applies(path: List[str], depth: int) -> bool:
    """Master augmentation requires every dim in the chain up to and
    including the current one to be client-side. Otherwise the master
    query can't be filtered to the current parent context."""
    return all(path[i] in _DIM_CLIENT_SIDE for i in range(depth + 1))


def _week_aligned_window_start(weeks: int) -> date:
    """Monday of the oldest week displayed in the grid. The `>=
    window_start` filter must anchor on a week start, otherwise mid-week
    dates from the week BEFORE the oldest displayed column leak into
    Total / last_sale while having no visible column. Matches the
    frontend `buildWeekColumnsDesc` helper."""
    return fc_weeks.week_start(date.today()) - timedelta(weeks=weeks - 1)


# ---------------------------------------------------------------------------
# SQL builders
# ---------------------------------------------------------------------------

def _needs_salesrep_join(path: List[str], depth: int) -> bool:
    """SalesRep is joined only if 'salesrep' appears somewhere in the
    scoped prefix of the path (either as the current level or any
    parent)."""
    return "salesrep" in path[: depth + 1]


def _parent_where(path: List[str], depth: int) -> str:
    """AND-prefixed WHERE clauses matching each parent dim to its :p{i}
    bind value. Empty string at depth 0."""
    return " ".join(
        f"AND {_DIM_KEY[path[i]]} = :p{i}" for i in range(depth)
    )


def _weekly_sql(path: List[str], depth: int) -> str:
    """Per-week aggregate: one row per (dim-key, week-start) with
    amount / litres / last-sale totals and the row label."""
    dim = path[depth]
    key = _DIM_KEY[dim]
    label = _DIM_LABEL[dim] or key
    sublabel = _DIM_SUBLABEL[dim] or "CAST(NULL AS varchar(255))"
    salesrep_join = (
        "LEFT JOIN dbo.SalesRep REP ON REP.idSalesRep = CL.RepID"
        if _needs_salesrep_join(path, depth) else ""
    )
    return f"""
        SELECT
            {key}                          AS k,
            {label}                        AS label,
            {sublabel}                     AS sublabel,
            {_WEEK_START}                  AS week_start,
            SUM({_SIGNED_LITRES})          AS litres,
            SUM({_SIGNED_INCL})            AS amount,
            MAX(CAST(ST.TxDate AS date))   AS last_sale
        FROM dbo.PostST ST
        JOIN dbo.Client  CL  ON CL.DCLink     = ST.DrCrAccount
        JOIN dbo.StkItem STK ON STK.StockLink = ST.AccountLink
        {salesrep_join}
        WHERE {_INVOICE_WHERE}
          AND {key} IS NOT NULL
          {_parent_where(path, depth)}
        GROUP BY {key}, {_WEEK_START}
    """


def _stale_sql(path: List[str], depth: int) -> str:
    """Per-customer last-sale within the window, rolled up to the current
    dim. Returns one row per key with total customer count and the subset
    that counts as stale (last_sale older than _STALE_DAYS). Zero-sale
    customers are added back in later from the Client master, for the
    root-level customer-partitioning dims only."""
    dim = path[depth]
    key = _DIM_KEY[dim]
    salesrep_join = (
        "LEFT JOIN dbo.SalesRep REP ON REP.idSalesRep = CL.RepID"
        if _needs_salesrep_join(path, depth) else ""
    )
    return f"""
        WITH cust_max AS (
            SELECT
                {key}                        AS k,
                CL.Account                   AS acc,
                MAX(CAST(ST.TxDate AS date)) AS last_sale
            FROM dbo.PostST ST
            JOIN dbo.Client  CL  ON CL.DCLink     = ST.DrCrAccount
            JOIN dbo.StkItem STK ON STK.StockLink = ST.AccountLink
            {salesrep_join}
            WHERE {_INVOICE_WHERE}
              AND {key} IS NOT NULL
              {_parent_where(path, depth)}
            GROUP BY {key}, CL.Account
        )
        SELECT
            k,
            COUNT(*) AS total_customers,
            SUM(CASE WHEN last_sale < :stale_cutoff THEN 1 ELSE 0 END)
                     AS stale_customers
        FROM cust_max
        GROUP BY k
    """


def _master_sql(path: List[str], depth: int) -> str:
    """Per-bucket customer master. Returns one row per key with the
    customer count from Client (On_Hold=0), filtered by parent dim
    values. Only valid when the chain up to `depth` is entirely
    client-side (see _master_applies). Zero-sale customers live here but
    not in the weekly/stale queries, so we fold them in as all-stale
    rows."""
    dim = path[depth]
    assert _master_applies(path, depth), (
        f"_master_sql requires a client-side chain; got {path} at depth {depth}"
    )
    key = _DIM_KEY[dim]
    label = _DIM_LABEL[dim] or key
    sublabel = _DIM_SUBLABEL[dim] or "CAST(NULL AS varchar(255))"
    salesrep_join = (
        "LEFT JOIN dbo.SalesRep REP ON REP.idSalesRep = CL.RepID"
        if "salesrep" in path[: depth + 1] else ""
    )
    return f"""
        SELECT
            {key}                       AS k,
            {label}                     AS label,
            {sublabel}                  AS sublabel,
            COUNT(DISTINCT CL.Account)  AS master_total
        FROM dbo.Client CL
        {salesrep_join}
        WHERE ISNULL(CL.On_Hold, 0) = 0
          AND {key} IS NOT NULL
          {_parent_where(path, depth)}
        GROUP BY {key}
    """


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------

def _empty_bucket() -> dict:
    return {
        "label": None,
        "sublabel": None,
        "weekly": {},
        "total_amount": 0.0,
        "total_litres": 0.0,
        "last_sale": None,
    }


def _resolve_label(dim: str, sql_label, key: str) -> str:
    """What text to display for a row. For most dims we fall back to the
    group key when the SQL label expression returned NULL / empty, so a
    row never renders as a blank cell. For salesrep an empty Code is an
    intentional empty display (per the user — showing the raw numeric
    RepID instead would be misleading noise)."""
    if sql_label is not None and sql_label != "":
        return sql_label
    if dim == "salesrep":
        return ""
    return key


def _fetch_level(
    db: Session,
    window_start: date,
    path: List[str],
    depth: int,
    parent_values: List[str],
) -> List[schemas.PivotRow]:
    dim = path[depth]
    bind = {"window_start": window_start}
    for i, v in enumerate(parent_values):
        bind[f"p{i}"] = v

    weekly_rows = db.execute(text(_weekly_sql(path, depth)), bind).mappings().all()

    buckets: dict[str, dict] = {}
    sold_keys: set[str] = set()  # keys that produced at least one weekly row
    for r in weekly_rows:
        k = r["k"]
        if k is None:
            continue
        k = str(k).strip()
        if not k:
            continue
        sold_keys.add(k)
        b = buckets.setdefault(k, _empty_bucket())
        if b["label"] is None:
            b["label"] = _resolve_label(dim, r["label"], k)
            b["sublabel"] = r["sublabel"]
        week = r["week_start"]
        if week is not None:
            week_iso = week.isoformat() if hasattr(week, "isoformat") else str(week)
            amount = float(r["amount"] or 0)
            litres = float(r["litres"] or 0)
            b["weekly"][week_iso] = {"amount": amount, "litres": litres}
            b["total_amount"] += amount
            b["total_litres"] += litres
        last_sale = r["last_sale"]
        if last_sale and (b["last_sale"] is None or last_sale > b["last_sale"]):
            b["last_sale"] = last_sale

    stale_bind = dict(bind)
    stale_bind["stale_cutoff"] = date.today() - timedelta(days=_STALE_DAYS)
    stale_rows = db.execute(text(_stale_sql(path, depth)), stale_bind).mappings().all()
    stale_by_key: dict[str, tuple[int, int]] = {}
    for r in stale_rows:
        k = r["k"]
        if k is None:
            continue
        k = str(k).strip()
        if not k:
            continue
        stale_by_key[k] = (int(r["total_customers"] or 0), int(r["stale_customers"] or 0))

    # Master augmentation: whenever the dim chain is entirely client-side
    # we can pull a bucket's customer master from Client and fold in
    # never-purchased customers as stale. This runs at any depth — e.g.
    # "customers under this route who have never bought in the window"
    # becomes visible when drilling in.
    master_by_key: dict[str, tuple[str, Optional[str], int]] = {}
    if _master_applies(path, depth):
        master_rows = db.execute(text(_master_sql(path, depth)), bind).mappings().all()
        for r in master_rows:
            k = r["k"]
            if k is None:
                continue
            k = str(k).strip()
            if not k:
                continue
            master_by_key[k] = (
                _resolve_label(dim, r["label"], k),
                r["sublabel"],
                int(r["master_total"] or 0),
            )

    # Final row set is the union of all three key sources.
    all_keys = set(buckets.keys()) | set(stale_by_key.keys()) | set(master_by_key.keys())
    can_have_children = depth < len(path) - 1
    # Will master augmentation also populate the IMMEDIATE child level?
    # True only when both this dim and the child dim are client-side — in
    # which case drilling into a bucket with zero sales is still useful
    # (it surfaces the master customer list for maintenance / rerouting).
    # For non-client-side child dims (category / stock) a zero-sale bucket
    # is a dead end so we keep the chevron hidden.
    next_level_has_master = (
        can_have_children
        and dim in _DIM_CLIENT_SIDE
        and path[depth + 1] in _DIM_CLIENT_SIDE
    )

    rows: list[schemas.PivotRow] = []
    for k in all_keys:
        bucket = buckets.get(k, _empty_bucket())
        total_cust, stale_cust = stale_by_key.get(k, (0, 0))
        label = bucket["label"]
        sublabel = bucket["sublabel"]

        if k in master_by_key:
            master_label, master_sublabel, master_total = master_by_key[k]
            # Customers in the master but not in the transaction set are
            # "never bought in window" → count as stale.
            missing = max(0, master_total - total_cust)
            total_cust = max(total_cust, master_total)
            stale_cust += missing
            if label is None:
                label = master_label
            if sublabel is None:
                sublabel = master_sublabel

        if label is None:
            label = _resolve_label(dim, None, k)

        # A row has drillable children when either (a) it produced sales
        # at this level, or (b) the next level is master-populated — in
        # which case the drill shows a zero-sale master customer list,
        # which is exactly what the user needs for maintenance (moving
        # inactive routes/reps/telesales).
        has_children = can_have_children and (k in sold_keys or next_level_has_master)

        rows.append(schemas.PivotRow(
            key=k,
            label=label,
            sublabel=sublabel,
            has_children=has_children,
            weekly={
                wk: schemas.PivotWeekCell(amount=v["amount"], litres=v["litres"])
                for wk, v in bucket["weekly"].items()
            },
            total_amount=bucket["total_amount"],
            total_litres=bucket["total_litres"],
            last_sale_date=bucket["last_sale"],
            stale_count=stale_cust,
            total_count=total_cust,
        ))

    # Sort: customer-partitioning dims rank by % stale descending — that's
    # the whole point of this screen (problem routes / silent reps bubble
    # to the top). Category/Stock stay sorted by amount desc because
    # "most stale stock" is confusing noise rather than useful signal.
    if dim in _DIM_HAS_STALE:
        def sort_key(r: schemas.PivotRow):
            pct = (r.stale_count / r.total_count) if r.total_count > 0 else -1.0
            return (-pct, -r.total_amount)
        rows.sort(key=sort_key)
    else:
        rows.sort(key=lambda r: -r.total_amount)

    return rows


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get("/customer-history", response_model=schemas.PivotLevelResponse)
def customer_history(
    path: str = Query(
        "route,customer,category,stock",
        description=(
            "Comma-separated dimension codes in drill-down order. Codes: "
            "route | salesrep | telesales | customer | category | stock."
        ),
    ),
    depth: int = Query(0, ge=0, description="0-indexed level to fetch (0 = root)."),
    v0: Optional[str] = Query(None),
    v1: Optional[str] = Query(None),
    v2: Optional[str] = Query(None),
    v3: Optional[str] = Query(None),
    v4: Optional[str] = Query(None),
    weeks: int = Query(12, ge=1, le=26),
    db: Session = Depends(get_db),
):
    dims = [d.strip() for d in path.split(",") if d.strip()]
    if not dims:
        raise HTTPException(400, "path must contain at least one dimension")
    unknown = [d for d in dims if d not in _DIMENSIONS]
    if unknown:
        raise HTTPException(400, f"unknown dimension(s): {','.join(unknown)}")
    if len(dims) != len(set(dims)):
        raise HTTPException(400, "path may not repeat a dimension")
    if depth >= len(dims):
        raise HTTPException(400, "depth out of range for the given path")

    # Collect parent values v0..v<depth-1>. All must be provided; missing
    # ones at that range are a client bug.
    raw_values = [v0, v1, v2, v3, v4][:depth]
    if any(v is None or v == "" for v in raw_values):
        raise HTTPException(400, f"must provide v0..v{depth - 1} for depth={depth}")
    parent_values = [v.strip() for v in raw_values]  # type: ignore[union-attr]

    window_start = _week_aligned_window_start(weeks)
    rows = _fetch_level(db, window_start, dims, depth, parent_values)
    return schemas.PivotLevelResponse(level=dims[depth], rows=rows)


# ---------------------------------------------------------------------------
# Drill-into-cell: show the individual invoices / credit notes that make
# up a bucket's number. Scoped by the same dim chain the row was built
# from, optionally narrowed to one week.
# ---------------------------------------------------------------------------

@router.get("/transactions", response_model=schemas.TransactionListResponse)
def transactions(
    path: str = Query(..., description="Comma-separated dim codes (same shape as /customer-history)."),
    levels: int = Query(
        ...,
        ge=1,
        description="How many dim filters to apply (= clicked row's depth + 1).",
    ),
    v0: Optional[str] = Query(None),
    v1: Optional[str] = Query(None),
    v2: Optional[str] = Query(None),
    v3: Optional[str] = Query(None),
    v4: Optional[str] = Query(None),
    v5: Optional[str] = Query(None),
    week_start: Optional[date] = Query(
        None,
        description="Monday of the week to show. Omit to span the full 12-week window.",
    ),
    weeks: int = Query(12, ge=1, le=26),
    db: Session = Depends(get_db),
):
    dims = [d.strip() for d in path.split(",") if d.strip()]
    if not dims:
        raise HTTPException(400, "path must contain at least one dimension")
    unknown = [d for d in dims if d not in _DIMENSIONS]
    if unknown:
        raise HTTPException(400, f"unknown dimension(s): {','.join(unknown)}")
    if levels > len(dims):
        raise HTTPException(400, "levels exceeds the length of path")

    raw_values = [v0, v1, v2, v3, v4, v5][:levels]
    if any(v is None or v == "" for v in raw_values):
        raise HTTPException(400, f"must provide v0..v{levels - 1} for levels={levels}")
    values = [v.strip() for v in raw_values]  # type: ignore[union-attr]

    if week_start is not None:
        tx_from = week_start
        tx_to = week_start + timedelta(days=6)
    else:
        tx_from = _week_aligned_window_start(weeks)
        tx_to = date.today()

    # Open sales orders (Id='SO') are excluded so the modal totals match
    # the grid, which aggregates PostST invoice-side rows only.
    where_parts = ["Id <> 'SO'", "TxDate >= :tx_from", "TxDate <= :tx_to"]
    bind: dict = {"tx_from": tx_from, "tx_to": tx_to}
    for i, (dim, val) in enumerate(zip(dims[:levels], values)):
        where_parts.append(f"{_DIM_VIEW_WHERE[dim]} = :p{i}")
        bind[f"p{i}"] = val
    where = " AND ".join(where_parts)

    rows = db.execute(
        text(f"""
            SELECT
                TxDate,
                Order_No,
                Id,
                CustomerAccount,
                CustomerAccName,
                Category,
                StockCode,
                StockCodeDesc,
                Quantity,
                Litre,
                ExclAmount,
                VAT,
                InclAmount
            FROM [SOAssistant].dbo._vw_SOCustomerInvoiceHistory
            WHERE {where}
            ORDER BY TxDate DESC, Order_No, StockCode
        """),
        bind,
    ).mappings().all()

    def _strip_prefix(name: str, account: str) -> str:
        # CustomerAccName is stored as "ACC-Name". Trim the prefix for display.
        if name and name.startswith(account + "-"):
            return name[len(account) + 1:]
        return name or ""

    return schemas.TransactionListResponse(
        transactions=[
            schemas.Transaction(
                tx_date=r["TxDate"],
                order_no=(r["Order_No"] or "").strip(),
                id=(r["Id"] or "").strip(),
                customer_account=(r["CustomerAccount"] or "").strip(),
                customer_name=_strip_prefix(r["CustomerAccName"] or "", r["CustomerAccount"] or ""),
                category=r["Category"],
                stock_code=(r["StockCode"] or "").strip(),
                stock_desc=(r["StockCodeDesc"] or "").strip(),
                quantity=float(r["Quantity"] or 0),
                litre=float(r["Litre"] or 0),
                excl_amount=float(r["ExclAmount"] or 0),
                vat=float(r["VAT"] or 0),
                incl_amount=float(r["InclAmount"] or 0),
            )
            for r in rows
        ]
    )
