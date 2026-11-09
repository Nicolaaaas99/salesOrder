"""Unit tests for the delivery pattern learner."""
from datetime import date, timedelta

from services.forecasting import delivery_pattern


class TestLearn:
    def test_mon_wed_fri_customer_matches_seed_scenario_a(self):
        """Scenario A seed: Mon 40%, Wed 30%, Fri 30%, HIGH confidence,
        26 weeks sampled. We reconstruct that distribution and check the
        learner recovers it."""
        # Simulate 26 weeks of deliveries: each week has one Mon delivery
        # of 400 L, one Wed delivery of 300 L, one Fri delivery of 300 L.
        deliveries = []
        anchor = date(2026, 10, 26)  # Monday (week start)
        for weeks_back in range(26):
            week = anchor - timedelta(days=7 * weeks_back)
            # week is the Monday; Wed = +2, Fri = +4
            deliveries.append((week, 400.0))
            deliveries.append((week + timedelta(days=2), 300.0))
            deliveries.append((week + timedelta(days=4), 300.0))

        pattern = delivery_pattern.learn(deliveries, as_of=date(2026, 11, 2),
                                         lookback_weeks=26)

        assert abs(pattern.mon_share - 0.4) < 0.001
        assert abs(pattern.wed_share - 0.3) < 0.001
        assert abs(pattern.fri_share - 0.3) < 0.001
        assert pattern.tue_share == 0.0
        assert pattern.thu_share == 0.0
        assert pattern.sat_share == 0.0
        assert pattern.sun_share == 0.0
        assert pattern.confidence == "HIGH"
        assert pattern.weeks_sampled == 26

    def test_tue_thu_customer_matches_seed_scenario_b(self):
        """Scenario B seed: Tue 50%, Thu 50%, MED confidence, 18 weeks."""
        deliveries = []
        anchor = date(2026, 10, 26)
        for weeks_back in range(18):
            week = anchor - timedelta(days=7 * weeks_back)
            deliveries.append((week + timedelta(days=1), 75.0))  # Tue
            deliveries.append((week + timedelta(days=3), 75.0))  # Thu

        pattern = delivery_pattern.learn(deliveries, as_of=date(2026, 11, 2),
                                         lookback_weeks=18)

        assert abs(pattern.tue_share - 0.5) < 0.001
        assert abs(pattern.thu_share - 0.5) < 0.001
        assert pattern.confidence == "MED"
        assert pattern.weeks_sampled == 18

    def test_sparse_customer_gets_low_confidence(self):
        """Below the MED threshold -> LOW confidence."""
        deliveries = [
            (date(2026, 10, 19), 100.0),  # Mon
            (date(2026, 10, 26), 100.0),  # Mon
            (date(2026, 11, 2),  100.0),  # Mon
        ]
        pattern = delivery_pattern.learn(deliveries, as_of=date(2026, 11, 3))
        assert pattern.confidence == "LOW"
        assert pattern.weeks_sampled == 3

    def test_empty_deliveries_zero_shares(self):
        pattern = delivery_pattern.learn([], as_of=date(2026, 11, 2))
        assert pattern.mon_share == 0.0
        assert pattern.confidence == "LOW"
        assert pattern.weeks_sampled == 0

    def test_lookback_window_excludes_older_deliveries(self):
        """Deliveries from 30 weeks ago shouldn't count when lookback=20."""
        # 5 Monday deliveries from 25 weeks ago (outside the window)
        old_mon_deliveries = [
            (date(2026, 10, 26) - timedelta(days=7 * 25), 100.0)
            for _ in range(5)
        ]
        # 10 Tuesday deliveries from the last 10 weeks (inside the window)
        recent_tue_deliveries = [
            (date(2026, 10, 26) - timedelta(days=7 * w) + timedelta(days=1), 100.0)
            for w in range(10)
        ]
        pattern = delivery_pattern.learn(
            old_mon_deliveries + recent_tue_deliveries,
            as_of=date(2026, 11, 2),
            lookback_weeks=20,
        )
        # Only Tuesday deliveries should have landed in the window.
        assert pattern.tue_share == 1.0
        assert pattern.mon_share == 0.0

    def test_zero_qty_deliveries_are_ignored(self):
        """A delivery with qty=0 is a cancelled/empty line -- not a signal."""
        deliveries = [
            (date(2026, 10, 19), 0.0),    # Mon -- ignored
            (date(2026, 10, 21), 100.0),  # Wed -- counts
        ]
        pattern = delivery_pattern.learn(deliveries, as_of=date(2026, 11, 2))
        assert pattern.mon_share == 0.0
        assert pattern.wed_share == 1.0


class TestShareFor:
    def test_share_for_lookup_by_date(self):
        """share_for(d) returns the share allocated to d's weekday."""
        deliveries = [
            (date(2026, 10, 19), 400.0),  # Mon
            (date(2026, 10, 21), 300.0),  # Wed
            (date(2026, 10, 23), 300.0),  # Fri
        ] * 10
        pattern = delivery_pattern.learn(deliveries, as_of=date(2026, 11, 2))
        # Share for a future Monday delivery
        assert abs(pattern.share_for(date(2026, 11, 9)) - 0.4) < 0.001
        # Share for a Wednesday
        assert abs(pattern.share_for(date(2026, 11, 11)) - 0.3) < 0.001
        # Share for a Tuesday (not served)
        assert pattern.share_for(date(2026, 11, 10)) == 0.0
