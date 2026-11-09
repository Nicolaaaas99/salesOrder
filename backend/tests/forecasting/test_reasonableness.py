"""Reasonableness flag rules (decision #6) -- pure functions."""

from datetime import date, timedelta

import pytest

from services.forecasting.reasonableness import (
    FLAG_NEW_PRODUCT,
    FLAG_P95,
    FLAG_PACK_SIZE,
    FLAG_YOY,
    check_above_historical_p95,
    check_above_yoy_growth_limit,
    check_below_pack_size_minimum,
    check_new_product,
    evaluate_flags,
    percentile,
)

AS_OF_WEEK = date(2026, 11, 2)        # W0 (Monday)
W_PLUS_1 = date(2026, 11, 9)


def weekly(values: list[float], end_week: date = AS_OF_WEEK) -> dict[date, float]:
    """Oldest-first values for the weeks immediately before `end_week`."""
    n = len(values)
    return {end_week - timedelta(weeks=n - i): v for i, v in enumerate(values)}


class TestPercentile:
    def test_matches_numpy_linear_interpolation(self):
        # numpy.percentile(range(20), 95) == 18.05
        assert percentile([float(i) for i in range(20)], 0.95) == pytest.approx(18.05)

    def test_single_value(self):
        assert percentile([42.0], 0.95) == 42.0

    def test_unsorted_input(self):
        assert percentile([3.0, 1.0, 2.0], 0.5) == 2.0

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            percentile([], 0.95)


class TestAboveHistoricalP95:
    def test_flat_history_any_lift_flags(self):
        history = weekly([100.0] * 52)
        flag = check_above_historical_p95(110.0, history, AS_OF_WEEK)
        assert flag is not None
        assert flag.code == FLAG_P95
        assert flag.threshold_value == pytest.approx(100.0)
        assert flag.metric_value == pytest.approx(110.0)

    def test_at_p95_does_not_flag(self):
        assert check_above_historical_p95(100.0, weekly([100.0] * 52), AS_OF_WEEK) is None

    def test_float_noise_at_threshold_does_not_flag(self):
        # 100 * 1.1 / 1.1 style noise must not trip a strict >.
        assert check_above_historical_p95(100.0000000001, weekly([100.0] * 52), AS_OF_WEEK) is None

    def test_zero_weeks_are_ignored(self):
        # 3 orders in a year + 49 empty weeks. Over non-zero weeks P95 = 100,
        # so an 80 target is fine. (Counting the zeros would put P95 at 22.5
        # and wrongly flag every intermittent leaf.)
        history = weekly([0.0] * 49 + [50.0, 100.0, 100.0])
        assert check_above_historical_p95(80.0, history, AS_OF_WEEK) is None
        assert check_above_historical_p95(101.0, history, AS_OF_WEEK) is not None

    def test_only_trailing_52_weeks_count(self):
        # A huge week 60 weeks ago is outside the window.
        history = weekly([100.0] * 52)
        history[AS_OF_WEEK - timedelta(weeks=60)] = 10_000.0
        assert check_above_historical_p95(150.0, history, AS_OF_WEEK) is not None

    def test_as_of_week_itself_is_excluded(self):
        history = {AS_OF_WEEK: 10_000.0, **weekly([100.0] * 10)}
        assert check_above_historical_p95(150.0, history, AS_OF_WEEK) is not None

    def test_no_history_no_flag(self):
        assert check_above_historical_p95(500.0, {}, AS_OF_WEEK) is None


class TestYoYGrowth:
    def test_flags_above_25pct(self):
        history = {W_PLUS_1 - timedelta(weeks=52): 100.0}
        flag = check_above_yoy_growth_limit(130.0, history, W_PLUS_1)
        assert flag is not None
        assert flag.code == FLAG_YOY
        assert flag.metric_value == pytest.approx(30.0)
        assert flag.threshold_value == 25.0

    def test_exactly_25pct_does_not_flag(self):
        history = {W_PLUS_1 - timedelta(weeks=52): 100.0}
        assert check_above_yoy_growth_limit(125.0, history, W_PLUS_1) is None

    def test_prior_year_anchor_is_monday_aligned_364_days(self):
        anchor = W_PLUS_1 - timedelta(days=364)
        assert anchor.weekday() == 0   # Monday
        assert check_above_yoy_growth_limit(200.0, {anchor: 100.0}, W_PLUS_1) is not None

    @pytest.mark.parametrize("prior", [None, 0.0])
    def test_no_baseline_no_flag(self, prior):
        history = {} if prior is None else {W_PLUS_1 - timedelta(weeks=52): prior}
        assert check_above_yoy_growth_limit(1000.0, history, W_PLUS_1) is None


class TestNewProduct:
    def test_launched_recently_flags_when_lever_changes_leaf(self):
        flag = check_new_product(date(2026, 8, 31), AS_OF_WEEK, target_qty=85.5, forecast_qty=57.0)
        assert flag is not None
        assert flag.code == FLAG_NEW_PRODUCT
        assert flag.metric_value == pytest.approx(9.0)
        assert flag.threshold_value == 13.0

    def test_window_is_13_weeks(self):
        # Decision #6: new for 13 weeks after the first invoice.
        assert check_new_product(AS_OF_WEEK - timedelta(weeks=12), AS_OF_WEEK, 20.0, 10.0) is not None
        assert check_new_product(AS_OF_WEEK - timedelta(weeks=13), AS_OF_WEEK, 20.0, 10.0) is None
        assert check_new_product(date(2026, 7, 1), AS_OF_WEEK, 20.0, 10.0) is None

    def test_unchanged_leaf_is_not_flagged(self):
        assert check_new_product(date(2026, 8, 31), AS_OF_WEEK, 57.0, 57.0) is None

    def test_never_sold_flags(self):
        flag = check_new_product(None, AS_OF_WEEK, 10.0, 5.0)
        assert flag is not None and flag.metric_value == 0.0

    def test_established_product_not_flagged(self):
        assert check_new_product(date(2020, 1, 1), AS_OF_WEEK, 200.0, 100.0) is None

    def test_sku_selling_when_history_begins_is_not_new(self):
        # Live DB: PostST starts 2026-08-01, so every SKU "first sold" then.
        # That's the start of the records, not a launch.
        start = date(2026, 8, 1)
        assert check_new_product(date(2026, 8, 3), AS_OF_WEEK, 20.0, 10.0, history_start=start) is None

    def test_sku_appearing_after_history_begins_is_new(self):
        start = date(2026, 8, 1)
        flag = check_new_product(date(2026, 9, 15), AS_OF_WEEK, 20.0, 10.0, history_start=start)
        assert flag is not None and flag.code == FLAG_NEW_PRODUCT

    def test_intermittent_but_old_product_not_flagged(self):
        # Regression: the original implementation flagged any leaf with
        # < 52 non-zero weeks, permanently blocking intermittent products.
        assert check_new_product(AS_OF_WEEK - timedelta(weeks=60), AS_OF_WEEK, 20.0, 10.0) is None


class TestPackSize:
    def test_below_one_pack_flags(self):
        flag = check_below_pack_size_minimum(5.0, 12)
        assert flag is not None
        assert flag.code == FLAG_PACK_SIZE
        assert flag.threshold_value == 12.0

    @pytest.mark.parametrize("target", [12.0, 24.0])
    def test_at_or_above_one_pack_ok(self, target):
        assert check_below_pack_size_minimum(target, 12) is None

    def test_zero_target_is_not_a_pack_problem(self):
        assert check_below_pack_size_minimum(0.0, 12) is None

    @pytest.mark.parametrize("pack", [0, -1])
    def test_invalid_pack_size_not_evaluated(self, pack):
        assert check_below_pack_size_minimum(5.0, pack) is None


class TestWalkthroughScenarioD:
    """FORECASTING_WALKTHROUGH.md Scenario D, reproduced end to end."""

    HISTORY = weekly([30, 35, 40, 50, 55, 55, 60, 58, 60])   # W-9 .. W-1

    def test_flags_match_expected_row(self):
        flags = evaluate_flags(
            target_qty=57 * 1.5,            # 85.5
            forecast_qty=57.0,
            history=self.HISTORY,
            forecast_week=W_PLUS_1,
            as_of_week=AS_OF_WEEK,
            pack_size=6,
            sku_first_sale=date(2026, 8, 31),   # first invoice = W-9
        )
        # cFlagReasonsJson = '["above_historical_p95","new_product_requires_review"]'
        assert [f.code for f in flags] == [FLAG_P95, FLAG_NEW_PRODUCT]
        assert flags[0].threshold_value == pytest.approx(60.0)


class TestEvaluateFlags:
    def test_clean_leaf_has_no_flags(self):
        history = weekly([100.0, 120.0, 90.0, 130.0, 110.0] * 11)   # 55 weeks
        history[W_PLUS_1 - timedelta(weeks=52)] = 100.0
        flags = evaluate_flags(target_qty=110.0, forecast_qty=100.0, history=history,
                               forecast_week=W_PLUS_1, as_of_week=AS_OF_WEEK,
                               pack_size=12, sku_first_sale=date(2019, 3, 1))
        assert flags == []

    def test_all_four_can_fire_together(self):
        history = {W_PLUS_1 - timedelta(weeks=52): 1.0, AS_OF_WEEK - timedelta(weeks=1): 1.0}
        flags = evaluate_flags(target_qty=3.0, forecast_qty=1.0, history=history,
                               forecast_week=W_PLUS_1, as_of_week=AS_OF_WEEK,
                               pack_size=12, sku_first_sale=date(2026, 10, 1))
        assert [f.code for f in flags] == [FLAG_P95, FLAG_YOY, FLAG_NEW_PRODUCT, FLAG_PACK_SIZE]
