"""Fallback source resolver.

When a (customer, SKU) leaf has too little history for the engine to
trust an individual-fit forecast (bIsFallback=1 territory), we fall
back to a group-level anchor rather than fail.

Decision #5 locked rule:

    peer_sku_avg = average weekly delivered qty for the SKU across all
                   customers that stock it (last 52 weeks).

    customer_magnitude = this customer's trailing-52w total category qty
                         / median trailing-52w total category qty across
                         all customers in that category.

    fallback_value = peer_sku_avg * customer_magnitude

Why magnitude at the CATEGORY level and not the SKU level: the whole
point of a fallback is "this customer has no track record on this SKU",
so SKU-level magnitude is undefined. The customer's general activity
in the category (e.g. "they buy some Milk, just not this particular
SKU yet") is the best proxy for how much of a new SKU they'd take.

Edge cases:
  - SKU is brand new to EVERYONE -> peer_sku_avg is None -> resolver
    returns None, engine declines to forecast (no silent zeros).
  - Customer has no category activity -> magnitude falls back to 1.0
    so the fallback becomes the raw peer avg.
  - All intermediate aggregates are computed ONCE per run via the
    FallbackResolver init, then every (customer, SKU) resolution is
    a dict lookup -- O(1) per leaf.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from .classifier import observation_window
from .history_loader import INVOICE_LINES_SQL

from . import config
from . import weeks

_WK = weeks.week_start_sql("TxDate")


# How far back the fallback resolver looks when computing peer averages
# and customer magnitudes. 52 weeks = one year of history.
_LOOKBACK_WEEKS = 52


@dataclass(frozen=True)
class FallbackSource:
    """Resolved fallback value with provenance for audit / debugging.

    description is human-readable and goes into the forecast row's
    cFallbackReason or a free-text diagnostics field during inspection.
    """
    value: float
    peer_sku_avg: Optional[float]
    customer_magnitude: Optional[float]
    description: str
    # 'OWN_RATE' = the leaf's own average weekly demand (any own sales);
    # 'PEER'     = peer SKU average x customer magnitude (no own sales).
    basis: str = "PEER"


def own_rate_fallback(
    history: dict[date, float],
    as_of: date,
    lookback_weeks: int = config.CLASSIFICATION_LOOKBACK_WEEKS,
    history_start: Optional[date] = None,
) -> Optional[FallbackSource]:
    """Fallback for a leaf WITH some sales of its own (decided 2026-11-03):
    its average weekly demand over the observed window, empty weeks
    included (total / observed weeks) -- the rate an intermittent buyer
    actually runs at. Returns None when the leaf has no sales in the
    window, so the caller falls through to the peer rule.

    Replaces peer x magnitude for these leaves: on live data that rule
    came out a median 7x the customer's own typical order (max 87x).
    """
    window = observation_window(as_of, lookback_weeks, history_start)
    if not window:
        return None
    total = sum(max(history.get(wk, 0.0), 0.0) for wk in window)
    if total <= 0:
        return None
    value = total / len(window)
    return FallbackSource(
        value=round(value, 4),
        peer_sku_avg=None,
        customer_magnitude=None,
        description=f"own_rate={total:.1f} over {len(window)} wks = {value:.1f}/wk",
        basis="OWN_RATE",
    )


class FallbackResolver:
    """Pre-computes peer-group and customer-magnitude aggregates once
    per run, then answers resolve() queries in constant time.

    Usage:
        resolver = FallbackResolver(db, as_of=date.today())
        source = resolver.resolve('TEST_C_TUCKSHOP_STE', 'TEST_CHZ_CHE_1KG')
        if source is None:
            # SKU is brand new everywhere -- engine declines to forecast
            ...
        else:
            # Use source.value as the forecast's fPredictedQty
            ...
    """

    def __init__(
        self,
        db: Session,
        as_of: date,
        lookback_weeks: int = _LOOKBACK_WEEKS,
    ) -> None:
        self._as_of = weeks.week_start(as_of)
        self._lookback_weeks = lookback_weeks

        self._peer_sku_avg: dict[str, float] = self._load_peer_sku_avg(db)
        self._sku_category: dict[str, str] = self._load_sku_categories(db)
        self._customer_category_qty: dict[tuple[str, str], float] = \
            self._load_customer_category_qty(db)
        self._category_median_qty: dict[str, float] = \
            self._compute_category_medians()

    # ---------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------

    def resolve(
        self,
        customer_account: str,
        stock_code: str,
    ) -> Optional[FallbackSource]:
        """Return a FallbackSource or None if the SKU has no peer data."""
        peer_avg = self._peer_sku_avg.get(stock_code)
        if peer_avg is None or peer_avg <= 0:
            # SKU is brand new to the whole catalogue -- no peer signal.
            return None

        category = self._sku_category.get(stock_code)
        magnitude = self._magnitude_for(customer_account, category)

        value = peer_avg * magnitude

        return FallbackSource(
            value=round(value, 4),
            peer_sku_avg=round(peer_avg, 4),
            customer_magnitude=round(magnitude, 4),
            description=(
                f"peer_sku_avg={peer_avg:.1f} L x "
                f"customer_magnitude={magnitude:.3f} = {value:.1f} L"
            ),
        )

    # ---------------------------------------------------------------
    # Pure magnitude math (unit testable without DB)
    # ---------------------------------------------------------------

    def _magnitude_for(self, customer: str, category: Optional[str]) -> float:
        """Compute customer_qty / peer_median_qty for the category.

        Falls back to 1.0 when the customer or category data is missing
        -- the engine then uses the raw peer average unmodified.
        """
        if category is None:
            return 1.0

        peer_median = self._category_median_qty.get(category)
        if peer_median is None or peer_median <= 0:
            return 1.0

        customer_qty = self._customer_category_qty.get((customer, category))
        if customer_qty is None or customer_qty <= 0:
            # Customer has NO history in this category. Can't scale by
            # customer magnitude -- use the raw peer average and let the
            # downstream pack-size snap floor it.
            return 1.0

        return customer_qty / peer_median

    # ---------------------------------------------------------------
    # Aggregate loaders (one SQL hit per method, called once at init)
    # ---------------------------------------------------------------

    def _load_peer_sku_avg(self, db: Session) -> dict[str, float]:
        """{stock_code: trailing-52w average weekly delivered qty across all
        customers that stock it}.

        Weekly = average over all (customer, week) pairs where
        this SKU was delivered. Zero-weeks do NOT contribute (standard
        average-over-active-weeks convention, matches Croston's intuition).

        Week math: weeks.week_start_sql (Monday weeks, independent of
        SET DATEFIRST).
        """
        window_start = self._as_of - timedelta(days=7 * self._lookback_weeks)

        sql = """
            SELECT StockCode, AVG(weekly_qty) AS avg_weekly_qty
            FROM (
                SELECT StockCode,
                       CustomerAccount,
                       """ + _WK + """ AS week_start,
                       SUM(Quantity) AS weekly_qty
                FROM """ + INVOICE_LINES_SQL + """ il
                WHERE OnOrder = 0
                  AND TxDate >= :window_start
                  AND TxDate <  :anchor
                  AND Quantity > 0
                GROUP BY StockCode, CustomerAccount,
                         """ + _WK + """
            ) weekly
            GROUP BY StockCode
        """
        rows = db.execute(
            text(sql),
            {"window_start": window_start, "anchor": self._as_of},
        ).mappings().all()

        out: dict[str, float] = {}
        for r in rows:
            code = r["StockCode"]
            avg = r["avg_weekly_qty"]
            if code and avg is not None:
                out[code] = float(avg)
        return out

    def _load_sku_categories(self, db: Session) -> dict[str, str]:
        """{stock_code: ulIIMainCategory}. One query over StkItem."""
        sql = """
            SELECT Code AS stock_code, ulIIMainCategory AS category
            FROM [BA_00_LPD_CON].dbo.StkItem
            WHERE ItemActive = 1
              AND ulIIMainCategory IS NOT NULL
        """
        rows = db.execute(text(sql)).mappings().all()
        return {r["stock_code"]: r["category"] for r in rows if r["stock_code"]}

    def _load_customer_category_qty(
        self,
        db: Session,
    ) -> dict[tuple[str, str], float]:
        """{(customer_account, category): trailing-52w total delivered qty}.
        Fuels the magnitude numerator for every customer.
        """
        window_start = self._as_of - timedelta(days=7 * self._lookback_weeks)

        sql = """
            SELECT CustomerAccount, Category, SUM(Quantity) AS total_qty
            FROM """ + INVOICE_LINES_SQL + """ il
            WHERE OnOrder = 0
              AND TxDate >= :window_start
              AND TxDate <  :anchor
              AND Quantity > 0
              AND Category IS NOT NULL
            GROUP BY CustomerAccount, Category
        """
        rows = db.execute(
            text(sql),
            {"window_start": window_start, "anchor": self._as_of},
        ).mappings().all()

        out: dict[tuple[str, str], float] = {}
        for r in rows:
            cust = r["CustomerAccount"]
            cat = r["Category"]
            qty = r["total_qty"]
            if cust and cat and qty is not None:
                out[(cust, cat)] = float(qty)
        return out

    def _compute_category_medians(self) -> dict[str, float]:
        """{category: median per-customer total qty for that category}.
        Derived in Python from _customer_category_qty -- no extra DB hit.
        """
        buckets: dict[str, list[float]] = {}
        for (cust, cat), qty in self._customer_category_qty.items():
            buckets.setdefault(cat, []).append(qty)
        return {cat: median(qtys) for cat, qtys in buckets.items() if qtys}


# ---------------------------------------------------------------------------
# Pure helper -- exposed for direct unit testing without a Session.
# ---------------------------------------------------------------------------

def magnitude_from_aggregates(
    customer: str,
    category: Optional[str],
    customer_category_qty: dict[tuple[str, str], float],
    category_median_qty: dict[str, float],
) -> float:
    """Pure implementation of the magnitude rule. Takes pre-computed
    aggregate dicts and returns the magnitude. Used in unit tests to
    verify the math without touching a database.
    """
    if category is None:
        return 1.0
    peer_median = category_median_qty.get(category)
    if peer_median is None or peer_median <= 0:
        return 1.0
    customer_qty = customer_category_qty.get((customer, category))
    if customer_qty is None or customer_qty <= 0:
        return 1.0
    return customer_qty / peer_median
