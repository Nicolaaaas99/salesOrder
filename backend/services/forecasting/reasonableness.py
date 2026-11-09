"""Reasonableness flag rules for scenario targets (decision #6).

A scenario cascades a lever down to every leaf (customer x SKU x week).
Before the scenario can be approved, every flagged leaf must be
individually reviewed by a manager (accepted or excluded). See
FORECASTING_WALKTHROUGH.md Scenario D for the reference example.

Four checks, flag codes stored verbatim in
_tb_SOScenarioTarget.cFlagReasonsJson:

  above_historical_p95         target > P95 of the leaf's non-zero weekly
                               true demand over the trailing 52 weeks.
  above_yoy_growth_limit       target implies > 25% growth vs the same
                               week last year (skipped when last
                               year's week had no demand -- no baseline).
  new_product_requires_review  the SKU's first-ever invoice is < 13 weeks
                               before the as-of week (or it has never sold).
                               Only raised when the scenario actually
                               changes the leaf (target != forecast). SKUs
                               already selling when invoice history begins
                               have an unknown launch date and are treated
                               as established.
  below_pack_size_minimum      0 < target < MIN_TARGET_PACKS x pack size.

All functions here are pure (no DB) -- the scenario service loads the
inputs in bulk and calls evaluate_flags() per leaf-week.

History convention: {week_start: qty}. Weeks absent from the dict
are zero-demand weeks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from . import config

FLAG_P95 = "above_historical_p95"
FLAG_YOY = "above_yoy_growth_limit"
FLAG_NEW_PRODUCT = "new_product_requires_review"
FLAG_PACK_SIZE = "below_pack_size_minimum"

ALL_FLAG_CODES = (FLAG_P95, FLAG_YOY, FLAG_NEW_PRODUCT, FLAG_PACK_SIZE)

# Float tolerance: targets are stored as DECIMAL(18,4), so anything
# closer than this is "equal" and must not trip a strict > / < check.
_EPS = 1e-6


@dataclass(frozen=True)
class ReasonablenessFlag:
    code: str
    message: str
    metric_value: float
    threshold_value: float


def percentile(values: list[float], pct: float) -> float:
    """Linear-interpolated percentile (numpy.percentile default method).

    `values` must be non-empty; `pct` in [0, 1].
    """
    if not values:
        raise ValueError("percentile() of empty list")
    ordered = sorted(values)
    idx = pct * (len(ordered) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    frac = idx - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def check_above_historical_p95(
    target_qty: float,
    history: dict[date, float],
    as_of_week: date,
    lookback_weeks: int = config.TARGET_P95_LOOKBACK_WEEKS,
    pct: float = config.TARGET_P95_PERCENTILE,
) -> Optional[ReasonablenessFlag]:
    """Flag when the target exceeds P95 of non-zero weekly demand in the
    `lookback_weeks` weeks strictly before `as_of_week`.

    Zero weeks are excluded so intermittent leaves are judged against the
    size of the orders they actually place (walkthrough Scenario D: 9
    non-zero weeks -> effective max ~60). No non-zero history -> no flag;
    the new-product check covers genuinely new SKUs.
    """
    window_start = as_of_week - timedelta(weeks=lookback_weeks)
    observed = [q for wk, q in history.items()
                if window_start <= wk < as_of_week and q > 0]
    if not observed:
        return None
    p95 = percentile(observed, pct)
    if target_qty > p95 + _EPS:
        return ReasonablenessFlag(
            code=FLAG_P95,
            message=(f"Target {target_qty:,.2f} exceeds the P{round(pct * 100)} of "
                     f"the last {lookback_weeks} weeks' demand ({p95:,.2f})"),
            metric_value=target_qty,
            threshold_value=p95,
        )
    return None


def check_above_yoy_growth_limit(
    target_qty: float,
    history: dict[date, float],
    forecast_week: date,
    max_growth_pct: float = config.TARGET_MAX_YOY_GROWTH_PCT,
) -> Optional[ReasonablenessFlag]:
    """Flag when target / same-week-last-year - 1 exceeds `max_growth_pct`.

    Same week last year = forecast_week - 52 weeks (364 days, so weeks stay
    aligned), matching the seasonal-naive model's anchor.
    """
    prior_qty = history.get(forecast_week - timedelta(weeks=52), 0.0)
    if prior_qty <= 0:
        return None
    growth_pct = (target_qty / prior_qty - 1.0) * 100.0
    if growth_pct > max_growth_pct + _EPS:
        return ReasonablenessFlag(
            code=FLAG_YOY,
            message=(f"Target {target_qty:,.2f} is {growth_pct:+.1f}% vs the same week "
                     f"last year ({prior_qty:,.2f}); limit is +{max_growth_pct:g}%"),
            metric_value=growth_pct,
            threshold_value=max_growth_pct,
        )
    return None


def check_new_product(
    sku_first_sale: Optional[date],
    as_of_week: date,
    target_qty: float,
    forecast_qty: float,
    history_start: Optional[date] = None,
    launch_window_weeks: int = config.NEW_PRODUCT_LAUNCH_WINDOW_WEEKS,
    grace_weeks: int = config.NEW_PRODUCT_HISTORY_START_GRACE_WEEKS,
) -> Optional[ReasonablenessFlag]:
    """Flag a scenario-changed leaf whose SKU launched < N weeks ago.

    `sku_first_sale` is the SKU's first invoice date across ALL customers
    (None = never sold). `history_start` is the earliest invoice on record:
    a first sale within `grace_weeks` of it only means "was already selling
    when records begin", not "launched then". A lever of 0 leaves the leaf
    unchanged, so there is nothing for a manager to review.
    """
    if abs(target_qty - forecast_qty) <= _EPS:
        return None
    if (sku_first_sale is not None and history_start is not None
            and sku_first_sale < history_start + timedelta(weeks=grace_weeks)):
        return None
    if sku_first_sale is None:
        return ReasonablenessFlag(
            code=FLAG_NEW_PRODUCT,
            message="SKU has no sales history yet -- target is speculative",
            metric_value=0.0,
            threshold_value=float(launch_window_weeks),
        )
    weeks_since_launch = (as_of_week - sku_first_sale).days / 7.0
    if weeks_since_launch < launch_window_weeks:
        return ReasonablenessFlag(
            code=FLAG_NEW_PRODUCT,
            message=(f"SKU first sold {sku_first_sale.isoformat()} "
                     f"({weeks_since_launch:.0f} weeks ago, < {launch_window_weeks}) "
                     f"-- no full year of history to justify the target"),
            metric_value=weeks_since_launch,
            threshold_value=float(launch_window_weeks),
        )
    return None


def check_below_pack_size_minimum(
    target_qty: float,
    pack_size: int,
    min_packs: int = config.MIN_TARGET_PACKS,
) -> Optional[ReasonablenessFlag]:
    """Flag a positive target smaller than `min_packs` packs.

    A zero target means "no ask" and is fine; an unknown / invalid pack
    size can't be evaluated.
    """
    if pack_size <= 0 or target_qty <= _EPS:
        return None
    minimum = float(pack_size * min_packs)
    if target_qty < minimum - _EPS:
        return ReasonablenessFlag(
            code=FLAG_PACK_SIZE,
            message=(f"Target {target_qty:,.2f} is below {min_packs} pack "
                     f"({minimum:g}) -- it will round to 0 or up to a full pack"),
            metric_value=target_qty,
            threshold_value=minimum,
        )
    return None


def evaluate_flags(
    *,
    target_qty: float,
    forecast_qty: float,
    history: dict[date, float],
    forecast_week: date,
    as_of_week: date,
    pack_size: int,
    sku_first_sale: Optional[date],
    history_start: Optional[date] = None,
) -> list[ReasonablenessFlag]:
    """Run all four checks for one leaf-week, in a stable order."""
    checks = (
        check_above_historical_p95(target_qty, history, as_of_week),
        check_above_yoy_growth_limit(target_qty, history, forecast_week),
        check_new_product(sku_first_sale, as_of_week, target_qty, forecast_qty, history_start),
        check_below_pack_size_minimum(target_qty, pack_size),
    )
    return [f for f in checks if f is not None]
