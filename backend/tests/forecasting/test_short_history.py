"""Short invoice history: observation window clipping + own-rate fallback.

Live DB (2026-11): PostST starts 2026-08-01, so a 52-week window has
~13 weeks of real data. Weeks before that are unknown, not zero demand.
"""

from datetime import date, timedelta

import pytest

from services.forecasting import config
from services.forecasting.classifier import classify, observation_window
from services.forecasting.fallback import own_rate_fallback

AS_OF = date(2026, 11, 3)                 # Tuesday -> anchor week 2026-11-01
HISTORY_START = date(2026, 8, 1)          # Saturday -> first full week 2026-08-02


def weekly(values: list[float]) -> dict[date, float]:
    """Oldest-first values for the weeks ending just before the anchor."""
    anchor = date(2026, 11, 2)
    n = len(values)
    return {anchor - timedelta(weeks=n - i): v for i, v in enumerate(values) if v}


class TestObservationWindow:
    def test_unclipped_is_full_lookback(self):
        assert len(observation_window(AS_OF, 52)) == 52

    def test_clipped_to_first_full_week(self):
        w = observation_window(AS_OF, 52, HISTORY_START)
        assert w[0] == date(2026, 8, 3)            # partial week of 07-26 excluded
        assert w[-1] == date(2026, 10, 26)
        assert len(w) == 13

    def test_history_start_on_a_monday_is_included(self):
        assert observation_window(AS_OF, 52, date(2026, 8, 3))[0] == date(2026, 8, 3)

    def test_history_after_anchor_gives_empty_window(self):
        assert observation_window(AS_OF, 52, date(2026, 12, 1)) == []


class TestClassifyWithShortHistory:
    # Bought in 10 of 13 weeks, steady size: a steady buyer.
    STEADY_10_OF_13 = weekly([15, 0, 15, 14, 16, 0, 15, 15, 0, 16, 15, 14, 15])

    def test_regression_unclipped_window_called_steady_buyer_intermittent(self):
        c = classify(self.STEADY_10_OF_13, AS_OF)
        assert c.adi == pytest.approx(5.2)          # 52 / 10 -- the old bug
        assert c.pattern == "INTERMITTENT"

    def test_clipped_window_sees_a_steady_buyer(self):
        c = classify(self.STEADY_10_OF_13, AS_OF, history_start=HISTORY_START)
        assert c.total_weeks_observed == 13
        assert c.adi == pytest.approx(1.3)
        assert c.pattern == "SMOOTH"

    def test_too_few_observed_weeks_is_insufficient(self):
        # Data began 3 weeks ago: 3 buying weeks, but < 4 observed weeks.
        start = date(2026, 10, 12)
        c = classify(weekly([10, 10, 10]), AS_OF, history_start=start)
        assert c.total_weeks_observed == 3 < config.CLASSIFICATION_MIN_WEEKS_OBSERVED
        assert c.pattern == "INSUFFICIENT"
        assert c.fallback_reason == "SPARSE_HISTORY"


class TestOwnRateFallback:
    def test_total_over_observed_weeks(self):
        # 10 orders of 15 over 13 observed weeks -> 150 / 13
        h = weekly([15, 0, 15, 15, 15, 0, 15, 15, 0, 15, 15, 15, 15])
        fb = own_rate_fallback(h, AS_OF, 52, HISTORY_START)
        assert fb is not None
        assert fb.basis == "OWN_RATE"
        assert fb.value == pytest.approx(150 / 13, abs=1e-4)
        assert fb.peer_sku_avg is None and fb.customer_magnitude is None

    def test_sho159_style_leaf_stays_near_own_rate(self):
        # Live case: ~15 per order in 10 of 13 weeks was forecast 1,307 by
        # peer x magnitude. Own rate keeps it at the customer's scale.
        fb = own_rate_fallback(TestClassifyWithShortHistory.STEADY_10_OF_13, AS_OF, 52, HISTORY_START)
        assert 10 < fb.value < 16

    def test_no_own_sales_returns_none_so_peer_rule_applies(self):
        assert own_rate_fallback({}, AS_OF, 52, HISTORY_START) is None

    def test_sales_before_history_start_window_ignored(self):
        old = {date(2026, 7, 27): 500.0}            # partial first week, outside window
        assert own_rate_fallback(old, AS_OF, 52, HISTORY_START) is None

    def test_credits_cannot_make_rate_negative(self):
        h = weekly([10, -30, 0, 0])
        fb = own_rate_fallback(h, AS_OF, 52, date(2026, 10, 5))
        assert fb.value == pytest.approx(10 / 4)
