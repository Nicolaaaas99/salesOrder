"""Unit tests for the Syntetos-Boylan demand pattern classifier.

Reproduces Phase 1 seed classifications so when the real engine
populates _tb_SODemandClassification we know it matches the hand-
computed values.
"""
from datetime import date

from services.forecasting import classifier, config


# Shared history fixtures matching the Phase 1 seed.
# NOTE: These use the FULL 10 weeks of history (not just the 6-week
# base-model window) because the classifier uses a 52-week lookback.
# Weeks outside the data range are implicitly zero.

SCENARIO_A_HISTORY = {
    date(2026, 8, 24):  395,  date(2026, 8, 31):  410,
    date(2026, 9, 7):   400,  date(2026, 9, 14):  405,
    date(2026, 9, 21):  390,  date(2026, 9, 28):  410,
    date(2026, 10, 5):  400,  date(2026, 10, 12): 395,
    date(2026, 10, 19): 405,  date(2026, 10, 26): 400,
}

SCENARIO_C_HISTORY = {
    date(2026, 8, 31):  120,
    date(2026, 9, 28):  100,
}


class TestScenarioA_Smooth:
    """Scenario A: 10/10 non-zero weeks, qtys hover tightly around 400.

    Expected:
      pattern = SMOOTH
      ADI = 1.0 (one event per observed week over the recent 10)
      CV^2 very small (< 0.01)
      recommended_model = BASE_MEDIAN_6W
      fallback_reason = SPARSE_HISTORY  (10 non-zero < 12 min_required)
    """
    def test_classifies_as_smooth(self):
        c = classifier.classify(
            SCENARIO_A_HISTORY,
            as_of=date(2026, 11, 2),
            lookback_weeks=10,   # look at exactly our 10 weeks of history
        )
        assert c.pattern == "SMOOTH"

    def test_adi_is_one(self):
        c = classifier.classify(SCENARIO_A_HISTORY, as_of=date(2026, 11, 2),
                                lookback_weeks=10)
        assert c.adi is not None
        assert abs(c.adi - 1.0) < 1e-9

    def test_cv_squared_is_tiny(self):
        c = classifier.classify(SCENARIO_A_HISTORY, as_of=date(2026, 11, 2),
                                lookback_weeks=10)
        assert c.cv_squared is not None
        # Scenario A seed value: approx 0.000281 per walkthrough. Our
        # population-variance formula gives the same number.
        assert c.cv_squared < 0.001

    def test_recommended_model_is_base_median(self):
        c = classifier.classify(SCENARIO_A_HISTORY, as_of=date(2026, 11, 2),
                                lookback_weeks=10)
        assert c.recommended_model == config.MODEL_CODE_BASE

    def test_sparse_history_flag_at_10_weeks(self):
        """10 non-zero < 12 min -> SPARSE_HISTORY, engine will fall back
        even though pattern is SMOOTH. Later weeks of real data will
        clear this flag."""
        c = classifier.classify(SCENARIO_A_HISTORY, as_of=date(2026, 11, 2),
                                lookback_weeks=10)
        assert c.fallback_reason == "SPARSE_HISTORY"


class TestScenarioC_Intermittent:
    """Scenario C: 2 non-zero weeks out of 10.

    Expected:
      pattern = INTERMITTENT
      ADI = 10 / 2 = 5.0   (way above 1.32 threshold)
      CV^2 of {120, 100}: mu=110, var=100, CV^2 = 100/110^2 ~ 0.00826
      recommended_model = CROSTON_SBA (intermittent owner)
      fallback_reason = SPARSE_HISTORY (2 < 12)
    """
    def test_classifies_as_intermittent(self):
        c = classifier.classify(
            SCENARIO_C_HISTORY,
            as_of=date(2026, 11, 2),
            lookback_weeks=10,
        )
        assert c.pattern == "INTERMITTENT"

    def test_adi_equals_five(self):
        c = classifier.classify(SCENARIO_C_HISTORY, as_of=date(2026, 11, 2),
                                lookback_weeks=10)
        assert c.adi is not None
        assert abs(c.adi - 5.0) < 1e-9

    def test_cv_squared_matches_hand_computation(self):
        """Non-zero demands {120, 100} -> mu=110, population variance=100,
        CV^2 = 100 / 110^2 = 0.008264..."""
        c = classifier.classify(SCENARIO_C_HISTORY, as_of=date(2026, 11, 2),
                                lookback_weeks=10)
        assert c.cv_squared is not None
        assert abs(c.cv_squared - (100 / 110 ** 2)) < 1e-9

    def test_recommended_model_is_croston(self):
        c = classifier.classify(SCENARIO_C_HISTORY, as_of=date(2026, 11, 2),
                                lookback_weeks=10)
        assert c.recommended_model == config.MODEL_CODE_CROSTON_SBA

    def test_sparse_history_fallback(self):
        c = classifier.classify(SCENARIO_C_HISTORY, as_of=date(2026, 11, 2),
                                lookback_weeks=10)
        assert c.fallback_reason == "SPARSE_HISTORY"


class TestInsufficientHistory:
    def test_one_non_zero_week_is_insufficient(self):
        history = {date(2026, 10, 12): 100}
        c = classifier.classify(history, as_of=date(2026, 11, 2), lookback_weeks=10)
        assert c.pattern == "INSUFFICIENT"
        assert c.fallback_reason == "SPARSE_HISTORY"

    def test_zero_non_zero_weeks_is_new_customer(self):
        c = classifier.classify({}, as_of=date(2026, 11, 2), lookback_weeks=10)
        assert c.pattern == "INSUFFICIENT"
        assert c.fallback_reason == "NEW_CUSTOMER"


class TestQuadrantBoundaries:
    """Direct checks at the Syntetos-Boylan cut points."""

    def _make_history(self, qtys: list[float]) -> dict:
        """Build a history dict with qtys in consecutive weeks
        working backward from 2026-10-25."""
        from datetime import timedelta
        anchor = date(2026, 10, 26)
        return {
            anchor - timedelta(days=7 * (len(qtys) - 1 - i)): q
            for i, q in enumerate(qtys)
        }

    def test_smooth_quadrant(self):
        # 6/6 non-zero, tightly clustered -> SMOOTH
        history = self._make_history([100, 102, 100, 98, 101, 99])
        c = classifier.classify(history, as_of=date(2026, 11, 2), lookback_weeks=6)
        assert c.pattern == "SMOOTH"

    def test_erratic_quadrant(self):
        # Non-zero every week (ADI = 1 < 1.32) but highly variable sizes
        # -> ERRATIC. qtys {10, 100, 10, 100, 10, 100} -> mu=55, var=2025,
        # CV^2 = 2025/3025 = 0.6694 (> 0.49).
        history = self._make_history([10, 100, 10, 100, 10, 100])
        c = classifier.classify(history, as_of=date(2026, 11, 2), lookback_weeks=6)
        assert c.pattern == "ERRATIC"

    def test_intermittent_quadrant(self):
        # Sparse (ADI > 1.32) but when present, similar sizes -> INTERMITTENT.
        # 3 non-zero in 10 weeks of {100, 102, 101} -> ADI=10/3 ~ 3.33,
        # CV^2 ~ 0.00005 (tiny).
        history = {
            date(2026, 8, 24): 100,
            date(2026, 9, 14): 102,
            date(2026, 10, 12): 101,
        }
        c = classifier.classify(history, as_of=date(2026, 11, 2), lookback_weeks=10)
        assert c.pattern == "INTERMITTENT"

    def test_lumpy_quadrant(self):
        # Sparse AND variable -> LUMPY. 3 non-zero in 10 weeks of
        # {10, 100, 50} -> ADI=3.33, mu=53.33, var ~ 1355.56, CV^2 ~ 0.476.
        # Need to push CV^2 past 0.49 -- use {10, 100, 10}.
        history = {
            date(2026, 8, 24): 10,
            date(2026, 9, 14): 100,
            date(2026, 10, 12): 10,
        }
        c = classifier.classify(history, as_of=date(2026, 11, 2), lookback_weeks=10)
        assert c.pattern == "LUMPY"


class TestConfidenceGrade:
    def test_smooth_individual_fit_gets_A(self):
        assert classifier.confidence_grade("SMOOTH", is_fallback=False) == "A"

    def test_smooth_fallback_degrades_to_C(self):
        assert classifier.confidence_grade("SMOOTH", is_fallback=True) == "C"

    def test_intermittent_fallback_gets_D(self):
        assert classifier.confidence_grade("INTERMITTENT", is_fallback=True) == "D"

    def test_insufficient_is_always_F(self):
        assert classifier.confidence_grade("INSUFFICIENT", is_fallback=False) == "F"
        assert classifier.confidence_grade("INSUFFICIENT", is_fallback=True) == "F"
