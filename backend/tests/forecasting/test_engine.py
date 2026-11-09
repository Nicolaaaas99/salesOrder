"""Unit tests for the pure engine decision logic.

The ForecastEngine class itself is DB-coupled; the pure helpers
(run_model, build_week_forecasts, pick_active) are tested here with
fabricated inputs. Full DB-backed verification is in
scripts/run_forecast_smoke_test.py (manual integration test).
"""
from datetime import date, timedelta

import pytest

from services.forecasting import config
from services.forecasting.classifier import Classification
from services.forecasting.engine import (
    LeafForecast,
    build_week_forecasts,
    fallback_owner,
    pick_active,
    run_model,
)
from services.forecasting.fallback import FallbackSource
from services.forecasting.history_loader import LeafContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SMOOTH_CLASSIFICATION = Classification(
    cv_squared=0.0003,
    adi=1.0,
    pattern="SMOOTH",
    non_zero_weeks=15,
    total_weeks_observed=15,
    min_required=12,
    recommended_model=config.MODEL_CODE_BASE,
    fallback_reason=None,
)

SMOOTH_SPARSE_CLASSIFICATION = Classification(
    cv_squared=0.0003,
    adi=1.0,
    pattern="SMOOTH",
    non_zero_weeks=5,
    total_weeks_observed=10,
    min_required=12,
    recommended_model=config.MODEL_CODE_BASE,
    fallback_reason="SPARSE_HISTORY",
)

INTERMITTENT_CLASSIFICATION = Classification(
    cv_squared=0.008,
    adi=5.0,
    pattern="INTERMITTENT",
    non_zero_weeks=2,
    total_weeks_observed=10,
    min_required=12,
    recommended_model=config.MODEL_CODE_CROSTON_SBA,
    fallback_reason="SPARSE_HISTORY",
)

CONTEXT_A = LeafContext(
    customer_account="TEST_A_SHOPRITE_DBV",
    stock_code="TEST_MILK_2L_FC",
    route="TEST_RT_101",
    dist_centre="WC",
    category="Milk",
    telesales=None,
    sales_rep=None,
)

STEADY_HISTORY = {
    date(2026, 8, 24):  395, date(2026, 8, 31):  410,
    date(2026, 9, 7):   400, date(2026, 9, 14):  405,
    date(2026, 9, 21):  390, date(2026, 9, 28):  410,
    date(2026, 10, 5):  400, date(2026, 10, 12): 395,
    date(2026, 10, 19): 405, date(2026, 10, 26): 400,
}


# ---------------------------------------------------------------------------
# run_model dispatch
# ---------------------------------------------------------------------------

class TestRunModel:
    def test_base_median_returns_predicted_qty(self):
        got = run_model(config.MODEL_CODE_BASE, STEADY_HISTORY, date(2026, 10, 19))
        assert got == 400.0

    def test_base_median_declines_on_sparse(self):
        sparse = {date(2026, 10, 12): 100}
        got = run_model(config.MODEL_CODE_BASE, sparse, date(2026, 10, 19))
        assert got is None

    def test_unknown_model_code_declines(self):
        got = run_model("UNKNOWN_FUTURE_MODEL", STEADY_HISTORY, date(2026, 10, 19))
        assert got is None

    def test_croston_not_implemented_yet_declines(self):
        """Phase 2b doesn't implement Croston; engine falls back cleanly."""
        got = run_model(config.MODEL_CODE_CROSTON_SBA, STEADY_HISTORY, date(2026, 10, 19))
        assert got is None


# ---------------------------------------------------------------------------
# build_week_forecasts
# ---------------------------------------------------------------------------

class TestBuildWeekForecasts:
    def test_single_model_smooth_emits_one_active_row(self):
        """BASE enabled, SMOOTH classification, enough history ->
        one row, bIsActive=1, bIsFallback=0."""
        rows = build_week_forecasts(
            enabled_models={config.MODEL_CODE_BASE: 1},
            context=CONTEXT_A,
            history=STEADY_HISTORY,
            week=date(2026, 10, 19),
            classification=SMOOTH_CLASSIFICATION,
            fallback=None,
        )
        assert len(rows) == 1
        assert rows[0].predicted_qty == 400.0
        assert rows[0].is_active is True
        assert rows[0].is_fallback is False
        assert rows[0].confidence_grade == "A"  # SMOOTH + individual fit

    def test_intermittent_recommends_croston_but_declines_falls_back(self):
        """INTERMITTENT -> recommended CROSTON_SBA, which is NOT enabled.
        BASE (the only enabled model) declines too (sparse). The fallback
        must still be emitted -- on BASE, the default active slot.

        Regression: run 6 left 97 of 200 leaves with no forecast at all
        because the fallback was only ever attached to the recommended
        model, which wasn't enabled.
        """
        fb = FallbackSource(
            value=25.0, peer_sku_avg=25.0, customer_magnitude=1.0,
            description="peer avg",
        )
        rows = build_week_forecasts(
            enabled_models={config.MODEL_CODE_BASE: 1},  # CROSTON not enabled
            context=CONTEXT_A,
            history={date(2026, 10, 12): 100},   # too sparse for BASE too
            week=date(2026, 10, 19),
            classification=INTERMITTENT_CLASSIFICATION,
            fallback=fb,
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.model_code == config.MODEL_CODE_BASE
        assert row.predicted_qty == 25.0
        assert row.is_fallback is True
        assert row.fallback_reason == "SPARSE_HISTORY"
        assert row.is_active is True
        assert row.confidence_grade == "D"   # INTERMITTENT + fallback

    def test_intermittent_falls_back_when_croston_enabled(self):
        """When the recommended model IS enabled but declines (not yet
        implemented), fallback produces a bIsFallback=1 row for it."""
        fb = FallbackSource(
            value=25.0, peer_sku_avg=25.0, customer_magnitude=1.0,
            description="peer avg",
        )
        rows = build_week_forecasts(
            # Pretend both are enabled.
            enabled_models={
                config.MODEL_CODE_BASE: 1,
                config.MODEL_CODE_CROSTON_SBA: 2,
            },
            context=CONTEXT_A,
            history={date(2026, 10, 12): 100},
            week=date(2026, 10, 19),
            classification=INTERMITTENT_CLASSIFICATION,
            fallback=fb,
        )
        # CROSTON declines + fallback kicks in; BASE also declines (sparse)
        # and no fallback applies (BASE isn't the recommended model) -> 1 row.
        assert len(rows) == 1
        row = rows[0]
        assert row.model_code == config.MODEL_CODE_CROSTON_SBA
        assert row.predicted_qty == 25.0
        assert row.is_fallback is True
        assert row.fallback_reason == "SPARSE_HISTORY"
        assert row.fallback_source == 25.0
        assert row.is_active is True
        assert row.confidence_grade == "D"   # INTERMITTENT + fallback

    def test_fallback_is_emitted_exactly_once(self):
        """With CROSTON enabled (and declining) it owns the fallback; BASE
        declines silently. Never two fallback rows for one leaf-week."""
        fb = FallbackSource(value=25.0, peer_sku_avg=25.0,
                            customer_magnitude=1.0, description="")
        rows = build_week_forecasts(
            enabled_models={config.MODEL_CODE_BASE: 1, config.MODEL_CODE_CROSTON_SBA: 2},
            context=CONTEXT_A,
            history={date(2026, 10, 12): 100},
            week=date(2026, 10, 19),
            classification=INTERMITTENT_CLASSIFICATION,
            fallback=fb,
        )
        assert [r.model_code for r in rows if r.is_fallback] == [config.MODEL_CODE_CROSTON_SBA]

    def test_no_fallback_value_still_means_no_rows(self):
        rows = build_week_forecasts(
            enabled_models={config.MODEL_CODE_BASE: 1},
            context=CONTEXT_A,
            history={date(2026, 10, 12): 100},
            week=date(2026, 10, 19),
            classification=INTERMITTENT_CLASSIFICATION,
            fallback=None,
        )
        assert rows == []


class TestFallbackOwner:
    def test_recommended_when_enabled(self):
        assert fallback_owner({config.MODEL_CODE_BASE: 1, config.MODEL_CODE_CROSTON_SBA: 2},
                              INTERMITTENT_CLASSIFICATION) == config.MODEL_CODE_CROSTON_SBA

    def test_base_when_recommended_disabled(self):
        assert fallback_owner({config.MODEL_CODE_BASE: 1},
                              INTERMITTENT_CLASSIFICATION) == config.MODEL_CODE_BASE

    def test_none_when_nothing_enabled(self):
        assert fallback_owner({}, INTERMITTENT_CLASSIFICATION) is None

    def test_two_models_fit_one_marked_active(self):
        """Both BASE and SEASONAL fit with real values. SMOOTH -> BASE
        is recommended -> BASE row is active, SEASONAL row is shadow."""
        # Build history long enough for seasonal naive to also fit.
        from datetime import timedelta
        forecast_week = date(2026, 11, 9)
        prior_week = forecast_week - timedelta(days=364)
        history = dict(STEADY_HISTORY)
        history[prior_week] = 400.0
        for i in range(1, 17):
            history[prior_week - timedelta(days=7 * i)] = 400.0
            history[forecast_week - timedelta(days=7 * i)] = 400.0

        rows = build_week_forecasts(
            enabled_models={
                config.MODEL_CODE_BASE: 1,
                config.MODEL_CODE_SEASONAL_NAIVE_YOY: 5,
            },
            context=CONTEXT_A,
            history=history,
            week=forecast_week,
            classification=SMOOTH_CLASSIFICATION,
            fallback=None,
        )
        assert len(rows) == 2
        active = [r for r in rows if r.is_active]
        shadow = [r for r in rows if not r.is_active]
        assert len(active) == 1
        assert len(shadow) == 1
        assert active[0].model_code == config.MODEL_CODE_BASE


# ---------------------------------------------------------------------------
# pick_active
# ---------------------------------------------------------------------------

class TestPickActive:
    def _mock_row(self, code: str) -> LeafForecast:
        return LeafForecast(
            customer_account="x", stock_code="y", week_start=date(2026, 11, 9),
            model_code=code, model_id=0, predicted_qty=100.0,
            pi_lower=None, pi_upper=None,
            pattern="SMOOTH", confidence_grade="A",
            is_fallback=False, fallback_reason=None, fallback_source=None,
            is_active=False,
        )

    def test_prefers_classifier_recommended(self):
        rows = {
            config.MODEL_CODE_BASE: self._mock_row(config.MODEL_CODE_BASE),
            config.MODEL_CODE_SEASONAL_NAIVE_YOY:
                self._mock_row(config.MODEL_CODE_SEASONAL_NAIVE_YOY),
        }
        classif = Classification(
            cv_squared=0.1, adi=1.0, pattern="ERRATIC",
            non_zero_weeks=15, total_weeks_observed=15, min_required=12,
            recommended_model=config.MODEL_CODE_SEASONAL_NAIVE_YOY,
            fallback_reason=None,
        )
        assert pick_active(rows, classif) == config.MODEL_CODE_SEASONAL_NAIVE_YOY

    def test_falls_back_to_base_when_recommended_absent(self):
        """Recommended model didn't produce a row (declined). Base is
        the pragmatic default when any fit at all exists."""
        rows = {
            config.MODEL_CODE_BASE: self._mock_row(config.MODEL_CODE_BASE),
        }
        classif = Classification(
            cv_squared=0.1, adi=1.0, pattern="ERRATIC",
            non_zero_weeks=15, total_weeks_observed=15, min_required=12,
            recommended_model=config.MODEL_CODE_SEASONAL_NAIVE_YOY,
            fallback_reason=None,
        )
        assert pick_active(rows, classif) == config.MODEL_CODE_BASE

    def test_empty_rows_returns_none(self):
        classif = SMOOTH_CLASSIFICATION
        assert pick_active({}, classif) is None

    def test_single_non_base_non_recommended_still_chosen(self):
        """Edge case: only CROSTON row present, recommended was something
        else, BASE absent. Deterministic fallback: alphabetical first."""
        rows = {
            config.MODEL_CODE_CROSTON_SBA: self._mock_row(config.MODEL_CODE_CROSTON_SBA),
        }
        classif = Classification(
            cv_squared=0.1, adi=1.0, pattern="LUMPY",
            non_zero_weeks=5, total_weeks_observed=10, min_required=12,
            recommended_model="SOMETHING_ELSE",
            fallback_reason=None,
        )
        assert pick_active(rows, classif) == config.MODEL_CODE_CROSTON_SBA


_ANCHOR_AS_OF = date(2026, 11, 2)                 # this week's Monday
# W-6 .. W-1 = 44, 40, 38, 42, 41, 40  -> median 40.5 -> 41
_ANCHOR_HIST = {_ANCHOR_AS_OF - timedelta(weeks=n): q
                for n, q in zip(range(6, 0, -1), [44, 40, 38, 42, 41, 40])}


class TestWindowAnchoredOnAsOf:
    """Regression: model history windows end at the last COMPLETED week
    before the as-of week, whatever week is being forecast."""

    AS_OF = _ANCHOR_AS_OF
    HIST = _ANCHOR_HIST

    def test_base_is_the_same_level_across_the_horizon(self):
        got = [run_model(config.MODEL_CODE_BASE, self.HIST, self.AS_OF + timedelta(weeks=h), self.AS_OF)
               for h in (1, 2, 3, 4)]
        assert got == [41.0, 41.0, 41.0, 41.0]

    def test_unanchored_window_was_the_bug(self):
        # Anchored on the forecast week, W+1 drops W-6 (44) and counts the
        # in-progress week as zero -> 40; W+4 sees only 2 real weeks -> declines.
        assert run_model(config.MODEL_CODE_BASE, self.HIST, self.AS_OF + timedelta(weeks=1)) == 40.0
        assert run_model(config.MODEL_CODE_BASE, self.HIST, self.AS_OF + timedelta(weeks=4)) is None

    def test_seasonal_trend_window_ends_at_as_of(self):
        w1 = self.AS_OF + timedelta(weeks=1)
        h = {w1 - timedelta(weeks=52): 100.0}
        for n in range(1, 17):   # 16 completed weeks before as-of at 110, same weeks last year at 100
            h[self.AS_OF - timedelta(weeks=n)] = 110.0
            h[self.AS_OF - timedelta(weeks=n + 52)] = 100.0
        assert run_model(config.MODEL_CODE_SEASONAL_NAIVE_YOY, h, w1, self.AS_OF) == 110.0
