"""Unit tests for the pure history-loader transformation.

The DB-dependent loaders (load_weekly_history, load_deliveries,
load_leaf_context) require a live SOAssistant database and are
covered by integration tests in Phase 2b. Here we only test the
pure combine_true_demand function.
"""
from datetime import date

from services.forecasting import history_loader


class TestCombineTrueDemand:
    def test_matches_scenario_b_hand_computation(self):
        """Scenario B: 90 L delivered, 60 L short -> 150 L true demand.
        One of the clearest payoffs of the four-measurement design."""
        delivered = {date(2026, 10, 26): 90.0}
        shortfall = {date(2026, 10, 26): 60.0}

        true_demand = history_loader.combine_true_demand(delivered, shortfall)

        assert true_demand == {date(2026, 10, 26): 150.0}

    def test_week_with_delivery_no_short_is_passthrough(self):
        delivered = {date(2026, 10, 26): 400.0, date(2026, 10, 19): 395.0}
        shortfall = {}

        got = history_loader.combine_true_demand(delivered, shortfall)

        assert got == delivered

    def test_week_with_shortfall_but_no_delivery_still_counts(self):
        """Edge case: shortfall logged for a week where no row ever
        appeared on the invoice side (e.g. order placed but Evolution
        has not yet posted the delivery). The uplift still kicks in."""
        delivered = {}
        shortfall = {date(2026, 10, 26): 60.0}

        got = history_loader.combine_true_demand(delivered, shortfall)

        assert got == {date(2026, 10, 26): 60.0}

    def test_both_empty_returns_empty(self):
        assert history_loader.combine_true_demand({}, {}) == {}

    def test_multiple_weeks_combine_independently(self):
        delivered = {
            date(2026, 10, 12): 400,
            date(2026, 10, 19): 90,    # stockout week
            date(2026, 10, 26): 400,
        }
        shortfall = {
            date(2026, 10, 19): 310,  # recovers to 400
        }

        got = history_loader.combine_true_demand(delivered, shortfall)

        assert got == {
            date(2026, 10, 12): 400,
            date(2026, 10, 19): 400,   # uplifted
            date(2026, 10, 26): 400,
        }

    def test_median_through_uplifted_history_matches_true_signal(self):
        """Integration-ish: feed the uplifted dict to the base median
        and verify we get the same answer as if the stockout had never
        happened. This is the whole point of correction 3."""
        from services.forecasting.models import base_median

        # Six steady weeks of 150, with W-3 clipped by stockout to 90.
        delivered = {
            date(2026, 9, 7):   150,
            date(2026, 9, 14):  150,
            date(2026, 9, 21):  150,
            date(2026, 9, 28):  150,
            date(2026, 10, 5):   90,   # <- stockout
            date(2026, 10, 12): 150,
        }
        shortfall = {date(2026, 10, 5): 60}

        true_demand = history_loader.combine_true_demand(delivered, shortfall)
        result = base_median.forecast(true_demand, forecast_week=date(2026, 10, 19))

        assert result is not None
        assert result.predicted_qty == 150.0, (
            "with uplift the median is 150; without it would be 150 too thanks "
            "to robustness, but a repeated stockout WOULD poison it -- test "
            "the mechanism explicitly"
        )

    def test_without_uplift_recurring_stockouts_poison_the_median(self):
        """Illustrative: three stockout weeks DO shift the delivered-only
        median below the true demand. This is the failure mode uplift
        prevents."""
        from services.forecasting.models import base_median
        from statistics import median

        delivered_only = {
            date(2026, 9, 7):   150,
            date(2026, 9, 14):  150,
            date(2026, 9, 21):   90,  # stockout
            date(2026, 9, 28):   90,  # stockout
            date(2026, 10, 5):   90,  # stockout
            date(2026, 10, 12): 150,
        }
        shortfalls = {
            date(2026, 9, 21): 60,
            date(2026, 9, 28): 60,
            date(2026, 10, 5): 60,
        }

        # Without uplift: median of {150,150,90,90,90,150} = 120
        delivered_median = median(delivered_only.values())
        assert delivered_median == 120, (
            "without uplift the median drops to 120 -- confirming the "
            "stockouts materially shifted the signal"
        )

        # With uplift: all weeks = 150, median clean
        true_demand = history_loader.combine_true_demand(delivered_only, shortfalls)
        result = base_median.forecast(true_demand, forecast_week=date(2026, 10, 19))
        assert result is not None
        assert result.predicted_qty == 150.0
