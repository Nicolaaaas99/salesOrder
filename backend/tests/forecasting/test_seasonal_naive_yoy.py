"""Unit tests for the seasonal-naive + YoY trend model."""
from datetime import date, timedelta

from services.forecasting.models import seasonal_naive_yoy


def _year_of_history(same_week_qty: float, trend_mult: float) -> dict[date, float]:
    """Build 2 years of weekly history where:
      - the prior-year same-week has `same_week_qty`
      - the last 16 weeks are `trend_mult` times the prior-year same window.

    Anchor: forecasting W+1 = 2026-11-08. 364 days prior = 2026-11-09?
    Nope -- 2026-11-09 - 364 = 2025-11-10 (also a Monday).

        >>> date(2026, 11, 9) - timedelta(days=364)
        datetime.date(2025, 11, 10)
    """
    forecast_week = date(2026, 11, 9)
    prior_week = forecast_week - timedelta(days=364)

    history: dict[date, float] = {}

    # Prior-year same week: anchor value.
    history[prior_week] = same_week_qty

    # Prior-year 16-week window (weeks 1..16 before prior-year same week).
    for i in range(1, 17):
        wk = prior_week - timedelta(days=7 * i)
        history[wk] = 100.0

    # This year's 16-week window: trend_mult * prior.
    for i in range(1, 17):
        wk = forecast_week - timedelta(days=7 * i)
        history[wk] = 100.0 * trend_mult

    return history


class TestForecastHappyPath:
    def test_flat_trend_returns_seasonal_baseline(self):
        """YoY ratio = 1.0 (prior and current sums equal) -> forecast
        exactly matches same-week-last-year."""
        history = _year_of_history(same_week_qty=200.0, trend_mult=1.0)

        result = seasonal_naive_yoy.forecast(history, date(2026, 11, 9))

        assert result is not None
        assert result.predicted_qty == 200.0
        assert result.seasonal_baseline == 200.0
        assert abs(result.yoy_ratio - 1.0) < 1e-9

    def test_growing_trend_lifts_the_forecast(self):
        """This year's 16w = 1.2x prior-year 16w. 200 * 1.2 = 240."""
        history = _year_of_history(same_week_qty=200.0, trend_mult=1.2)

        result = seasonal_naive_yoy.forecast(history, date(2026, 11, 9))

        assert result is not None
        assert result.predicted_qty == 240.0
        assert abs(result.yoy_ratio - 1.2) < 1e-9

    def test_declining_trend_lowers_the_forecast(self):
        """0.8x YoY. 200 * 0.8 = 160."""
        history = _year_of_history(same_week_qty=200.0, trend_mult=0.8)

        result = seasonal_naive_yoy.forecast(history, date(2026, 11, 9))

        assert result is not None
        assert result.predicted_qty == 160.0

    def test_half_up_rounding_matches_base_model(self):
        """Seasonal naive * trend yields 200 * 1.0025 = 200.5 -> 201."""
        history = _year_of_history(same_week_qty=200.0, trend_mult=1.0025)
        # 1.0025 implies total trend sum = 1600 * 1.0025 = 1604
        # yoy_ratio = 1604 / 1600 = 1.0025
        # predicted = 200 * 1.0025 = 200.5 -> half-up = 201

        result = seasonal_naive_yoy.forecast(history, date(2026, 11, 9))
        assert result is not None
        assert result.predicted_qty == 201.0


class TestDeclinesWhenRequirementsMissing:
    def test_no_prior_year_same_week_returns_none(self):
        """Scenario A's history (10 weeks) has no 2025 data -> no
        seasonal baseline -> model declines, engine falls back."""
        history = {
            date(2026, 9, 7):  400,
            date(2026, 9, 14): 400,
            date(2026, 10, 5): 400,
        }
        result = seasonal_naive_yoy.forecast(history, date(2026, 11, 9))
        assert result is None

    def test_zero_prior_year_same_week_returns_none(self):
        history = _year_of_history(same_week_qty=0.0, trend_mult=1.0)
        result = seasonal_naive_yoy.forecast(history, date(2026, 11, 9))
        assert result is None

    def test_zero_prior_year_trend_denominator_returns_none(self):
        """Prior-year same-week exists but the trend window prior year
        is all zeros -> can't divide by zero -> decline."""
        forecast_week = date(2026, 11, 9)
        prior_week = forecast_week - timedelta(days=364)

        history: dict[date, float] = {prior_week: 200.0}
        # This-year trend window has data; prior-year window doesn't.
        for i in range(1, 17):
            history[forecast_week - timedelta(days=7 * i)] = 100.0
        # Prior-year window intentionally absent (zeros).

        result = seasonal_naive_yoy.forecast(history, forecast_week)
        assert result is None


class TestWeekAlignment:
    def test_364_day_offset_preserves_monday(self):
        """A 52-week offset must land on the same weekday. 2026-11-09
        (Mon) - 364 days = 2025-11-10 which is also a Monday."""
        forecast_week = date(2026, 11, 9)
        prior = forecast_week - timedelta(days=364)
        # weekday Mon = 0
        assert prior.weekday() == 0, (
            "52-week offset must land on the same weekday"
        )

    def test_non_monday_input_is_normalised(self):
        """Caller passes any date; model internally normalises to the Monday."""
        # Pass a Wednesday
        history = _year_of_history(same_week_qty=200.0, trend_mult=1.0)
        result = seasonal_naive_yoy.forecast(history, date(2026, 11, 11))  # Wed
        assert result is not None
        # Wed 2026-11-11 normalises to Sun 2026-11-08 -> same test as above.
        assert result.predicted_qty == 200.0
