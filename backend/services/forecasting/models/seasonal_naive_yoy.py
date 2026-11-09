"""Seasonal-naive + YoY trend model.

    forecast(week W) = qty_at(W - 364 days) * (sum last 16 weeks /
                                               sum same 16 weeks prior year)

Why:
  - "Seasonal naive" (same-week-last-year) captures a stable seasonal
    pattern -- e.g. ice-cream Nov is up, cheese Dec is up -- without
    any model fitting.
  - The YoY trend ratio corrects for drift: if the customer is up 10%
    on the category overall this year, the forecast lifts proportionally.
  - 364 days = exactly 52 weeks, so week-aligned points land on
    the same ISO weekday one year prior.

Requirements:
  - Full year of history (52+ weeks) for the seasonal naive component.
  - Non-zero prior-year 16-week sum for the trend ratio denominator.

Return None when either requirement fails -- engine falls back
appropriately.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from .. import weeks


_SEASONAL_WEEKS_PRIOR = 52  # 52 * 7 = 364 days
_TREND_WINDOW_WEEKS = 16


@dataclass(frozen=True)
class SeasonalNaiveYoYForecast:
    predicted_qty: float
    seasonal_baseline: float   # same-week-last-year qty before trend applied
    yoy_ratio: float            # recent-16w / prior-16w


def forecast(
    weekly_qtys: dict[date, float],
    forecast_week: date,
    trend_window_weeks: int = _TREND_WINDOW_WEEKS,
    as_of_week: Optional[date] = None,
) -> Optional[SeasonalNaiveYoYForecast]:
    """Predict next-week qty via same-week-last-year * YoY trend ratio.

    Args:
        weekly_qtys: {week-start: delivered-or-true-demand qty}.
                     Must span at least the last year to be usable.
        forecast_week: Monday of the week being predicted.
        trend_window_weeks: how many recent weeks (and same-window-prior-
                            year) to use for the YoY trend ratio.

    Returns:
        SeasonalNaiveYoYForecast if the requirements are met; None otherwise.

    Rounding: half-up to nearest whole unit (same convention as the
    base model).
    """
    forecast_week = weeks.week_start(forecast_week)

    # Seasonal baseline: same ISO week one year prior.
    prior_week = forecast_week - timedelta(days=_SEASONAL_WEEKS_PRIOR * 7)
    seasonal_baseline = weekly_qtys.get(prior_week, 0.0)

    if seasonal_baseline <= 0:
        # No prior-year point to anchor on. A different model owns this.
        return None

    # YoY trend ratio: recent window vs same window prior year. The
    # window ends at the last COMPLETED week before as_of (when given);
    # anchoring it on a future forecast week would count weeks that
    # haven't happened yet as zero demand and drag the ratio down.
    anchor = weeks.week_start(as_of_week) if as_of_week else forecast_week
    recent_window = weeks.previous_completed_weeks(anchor, trend_window_weeks)
    prior_window = [
        w - timedelta(days=_SEASONAL_WEEKS_PRIOR * 7) for w in recent_window
    ]

    recent_sum = sum(weekly_qtys.get(w, 0.0) for w in recent_window)
    prior_sum = sum(weekly_qtys.get(w, 0.0) for w in prior_window)

    if prior_sum <= 0:
        # No prior-year trend denominator -- fall through to another model.
        return None

    yoy_ratio = recent_sum / prior_sum
    predicted = seasonal_baseline * yoy_ratio

    # Match base model's half-up rounding (-> int, back to float).
    predicted_qty = float(int(predicted + 0.5)) if predicted >= 0 else 0.0

    return SeasonalNaiveYoYForecast(
        predicted_qty=predicted_qty,
        seasonal_baseline=seasonal_baseline,
        yoy_ratio=yoy_ratio,
    )
