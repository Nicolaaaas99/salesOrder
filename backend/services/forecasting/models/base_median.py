"""Base model -- 6-week non-zero median with half-up rounding.

Replicates the algorithm in history.py:order_suggestions EXACTLY so the
Phase 2+ engine's "Model A" is identical to what the app ships today.
When Model B / C / D compete against this on the scorecard, the
comparison is honest -- any accuracy delta is attributable to the new
model, not to a hidden difference in the baseline.

Scope: this module is PURE. No DB, no I/O. Caller passes an in-memory
weekly history dict and we return the prediction. Pack-size snapping
and the on-order / current-week-invoiced subtractions do NOT happen
here -- those decorate the suggestion downstream (Phase 4).
"""

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Optional

from .. import config
from .. import weeks


@dataclass(frozen=True)
class BaseMedianForecast:
    """Result of the base-median model for a single (customer, SKU, week).

    predicted_qty is the median of non-zero weekly qtys in the lookback
    window, rounded half-up to the nearest whole unit. non_zero_weeks
    tells the caller how many weeks actually contributed, which the
    engine uses to decide confidence grade and whether to mark the
    forecast a fallback.
    """
    predicted_qty: float
    non_zero_weeks: int
    window_weeks: int


def forecast(
    weekly_qtys: dict[date, float],
    forecast_week: date,
    window_weeks: int = config.BASE_MODEL_WINDOW_WEEKS,
    min_non_zero: int = config.BASE_MODEL_MIN_NON_ZERO,
) -> Optional[BaseMedianForecast]:
    """Predict next-week qty using the median of non-zero weekly qtys.

    Args:
        weekly_qtys: mapping of week-start -> delivered-or-true-demand qty.
                     Must contain at least `window_weeks` entries covering the
                     weeks strictly BEFORE `forecast_week`. Missing weeks are
                     treated as zero.
        forecast_week: Monday of the week being predicted. Only weeks strictly
                       before this participate in the median (the "current" or
                       target week itself is never in the window).
        window_weeks: how many completed weeks to look back. Default 6, matches
                      history.py:order_suggestions.
        min_non_zero: minimum non-zero weeks required to produce a prediction.
                      Default 3, matches history.py:order_suggestions. Return
                      None if fewer non-zero weeks are found.

    Returns:
        BaseMedianForecast if >= min_non_zero non-zero weeks are present in
        the window; None otherwise (engine will route the leaf to a fallback).

    Rounding: half-up to nearest whole unit. Implementation matches the
    exact Python idiom in history.py:
        base_median = float(int(raw_qty + 0.5))
    This is NOT banker's rounding -- 0.5 always rounds UP. Both 400.0 and
    400.5 round to 400; 400.6 and above round to 401.
    """
    forecast_week = weeks.week_start(forecast_week)
    window = weeks.previous_completed_weeks(forecast_week, window_weeks)

    # Collect non-zero qtys from the window. Missing weeks (key absent)
    # count as zero and are therefore excluded; explicit zeros are
    # likewise excluded.
    non_zero_qtys = [weekly_qtys.get(wk, 0.0) for wk in window]
    non_zero_qtys = [q for q in non_zero_qtys if q > 0]

    if len(non_zero_qtys) < min_non_zero:
        return None

    raw_qty = median(non_zero_qtys)
    # Half-up round to whole units -- exact match for the current algo.
    predicted = float(int(raw_qty + 0.5))

    return BaseMedianForecast(
        predicted_qty=predicted,
        non_zero_weeks=len(non_zero_qtys),
        window_weeks=window_weeks,
    )
