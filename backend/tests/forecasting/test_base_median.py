"""Unit tests for the base-median model.

Primary goal: reproduce the hand-computed Scenario A/B/C/D/E values
from the Phase 1 seed, so when Phase 2b wires this into the engine
we know the leaderboard comparison with future models will be honest.
"""
from datetime import date

from services.forecasting.models import base_median


# Shared history fixtures matching the Phase 1 seed. Keys are Monday-
# week-starts; values are what the model should see (delivered qty for
# normal weeks, estimated true demand for weeks with a stockout uplift).

# Scenario A -- 10 weeks of steady Milk at Shoprite Durbanville
SCENARIO_A_HISTORY = {
    date(2026, 8, 24):  395,   # W-10
    date(2026, 8, 31):  410,   # W-9
    date(2026, 9, 7):   400,   # W-8
    date(2026, 9, 14):  405,   # W-7
    date(2026, 9, 21):  390,   # W-6
    date(2026, 9, 28):  410,   # W-5
    date(2026, 10, 5):  400,   # W-4
    date(2026, 10, 12): 395,   # W-3
    # W-2 is the week being forecast; absent from history
    date(2026, 10, 26): 400,   # W-1
}

# Scenario C -- intermittent cheese purchase
SCENARIO_C_HISTORY = {
    date(2026, 8, 31):  120,   # W-9
    date(2026, 9, 28):  100,   # W-5
    # all other weeks absent (treated as zero)
}


class TestForecastScenarioA:
    """Base model for Scenario A (closed week W-2 = 2026-10-18).

    Window = last 6 completed weeks before W-2 = W-8..W-3
             = {400, 405, 390, 410, 400, 395}
    Sorted = {390, 395, 400, 400, 405, 410}
    Median = (400 + 400) / 2 = 400.0
    Round half-up = 400
    Non-zero weeks = 6
    """
    def test_predicts_400(self):
        result = base_median.forecast(
            SCENARIO_A_HISTORY,
            forecast_week=date(2026, 10, 19),
        )
        assert result is not None
        assert result.predicted_qty == 400.0

    def test_non_zero_weeks_is_six(self):
        result = base_median.forecast(
            SCENARIO_A_HISTORY,
            forecast_week=date(2026, 10, 19),
        )
        assert result is not None
        assert result.non_zero_weeks == 6

    def test_also_predicts_400_for_w_plus_1(self):
        """Window shifts to W-6..W-1 = {390, 410, 400, 395, 410, 400}
        Sorted = {390, 395, 400, 400, 410, 410}
        Median = 400 -- same as W-2 because the series is steady."""
        result = base_median.forecast(
            SCENARIO_A_HISTORY,
            forecast_week=date(2026, 11, 9),
        )
        assert result is not None
        assert result.predicted_qty == 400.0


class TestForecastScenarioC:
    """Base model for Scenario C (intermittent cheese).

    Window for W+1 (2026-11-08) = W-6..W-1
    Non-zero weeks in window: only W-5 = 100
    1 < 3 (min_non_zero) -> no forecast.
    """
    def test_sparse_history_returns_none(self):
        result = base_median.forecast(
            SCENARIO_C_HISTORY,
            forecast_week=date(2026, 11, 9),
        )
        assert result is None, "below min_non_zero -> engine must fall back"


class TestRoundHalfUp:
    """The exact rounding idiom from history.py:
        base_median = float(int(raw_qty + 0.5))
    Verify we match on the boundary cases."""
    def test_400_point_5_rounds_up_to_401(self):
        """Hypothetical: three weeks of {400, 401, 401}
        median = 401 -- not a boundary.
        Four weeks of {400, 400, 401, 402}
        median = (400 + 401) / 2 = 400.5 -> half-up = 401
        """
        history = {
            date(2026, 10, 5):  400,
            date(2026, 10, 12): 400,
            date(2026, 10, 19): 401,
            date(2026, 10, 26): 402,
        }
        result = base_median.forecast(history, forecast_week=date(2026, 11, 2),
                                      window_weeks=4, min_non_zero=3)
        assert result is not None
        assert result.predicted_qty == 401.0

    def test_half_up_vs_bankers_rounding(self):
        """Python's round() uses banker's (half-to-even), which would
        round 0.5 DOWN to 0. The algorithm must round UP."""
        history = {
            date(2026, 10, 12): 0,     # ignored (zero)
            date(2026, 10, 19): 400,
            date(2026, 10, 26): 401,
        }
        # median of {400, 401} = 400.5
        result = base_median.forecast(history, forecast_week=date(2026, 11, 2),
                                      window_weeks=3, min_non_zero=2)
        assert result is not None
        assert result.predicted_qty == 401.0, "0.5 must round UP, not to even"


class TestEdgeCases:
    def test_empty_history_returns_none(self):
        result = base_median.forecast({}, forecast_week=date(2026, 11, 9))
        assert result is None

    def test_exactly_min_non_zero_still_forecasts(self):
        history = {
            date(2026, 10, 5):  100,
            date(2026, 10, 12): 100,
            date(2026, 10, 19): 100,
            # weeks before window: ignored
            date(2026, 7, 1):   999,
        }
        result = base_median.forecast(history, forecast_week=date(2026, 11, 2),
                                      window_weeks=6, min_non_zero=3)
        assert result is not None
        assert result.predicted_qty == 100.0
        assert result.non_zero_weeks == 3

    def test_zeros_dont_count_toward_min_non_zero(self):
        history = {
            date(2026, 10, 5):  100,
            date(2026, 10, 12): 0,   # explicit zero -- still "doesn't count"
            date(2026, 10, 19): 100,
            # 2 non-zero < 3 min -> None
        }
        result = base_median.forecast(history, forecast_week=date(2026, 11, 2),
                                      window_weeks=6, min_non_zero=3)
        assert result is None

    def test_current_week_not_in_window(self):
        """Even if the caller passes the forecast-week itself in history,
        the model must ignore it. Matches history.py behaviour."""
        history = {
            date(2026, 10, 5):  100,
            date(2026, 10, 12): 100,
            date(2026, 10, 19): 100,
            date(2026, 10, 26): 100,
            date(2026, 11, 2):  999,  # this is forecast_week -- must be ignored
        }
        result = base_median.forecast(history, forecast_week=date(2026, 11, 2),
                                      window_weeks=6, min_non_zero=3)
        assert result is not None
        assert result.predicted_qty == 100.0, "current week must not pollute median"
