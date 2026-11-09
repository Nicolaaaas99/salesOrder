"""Demand pattern classifier -- Syntetos-Boylan quadrant.

Takes a weekly history dict and produces:
  - ADI  (Average Demand Interval -- weeks per non-zero event)
  - CV^2 (squared coefficient of variation of non-zero demand values)
  - Pattern in {SMOOTH, ERRATIC, INTERMITTENT, LUMPY, INSUFFICIENT}
  - cRecommendedModel (which model should own this leaf)
  - cFallbackReason (if the engine must fall back regardless of model)

Pure functions -- DB plumbing lives in the orchestrator (Phase 2b).

Cut-points from Syntetos, Boylan & Croston (FPP Ch 13):

                 CV^2 < 0.49       CV^2 >= 0.49
    ADI <  1.32  SMOOTH            ERRATIC
    ADI >= 1.32  INTERMITTENT      LUMPY

INSUFFICIENT returned when fewer than CLASSIFICATION_MIN_WEEKS_OBSERVED
weeks are present (classifier gives up rather than report a noisy
quadrant from 2 points).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

from . import config
from . import weeks


@dataclass(frozen=True)
class Classification:
    cv_squared: Optional[float]
    adi: Optional[float]
    pattern: str                      # SMOOTH | ERRATIC | INTERMITTENT | LUMPY | INSUFFICIENT
    non_zero_weeks: int
    total_weeks_observed: int
    min_required: int                 # threshold for individual-fit confidence
    recommended_model: Optional[str]  # cCode in _tb_SOForecastModel
    fallback_reason: Optional[str]    # SPARSE_HISTORY | NEW_SKU | NEW_CUSTOMER | ...


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _recommend_model(pattern: str) -> str:
    """Which model owns this pattern in the shelf?"""
    if pattern == "SMOOTH":
        return config.MODEL_CODE_BASE
    if pattern == "ERRATIC":
        return config.MODEL_CODE_SEASONAL_NAIVE_YOY
    if pattern in ("INTERMITTENT", "LUMPY"):
        return config.MODEL_CODE_CROSTON_SBA
    # INSUFFICIENT -- no good model owner; fallback will be used
    return config.MODEL_CODE_BASE


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _variance(xs: list[float], mean: float) -> float:
    """Population variance (N divisor, not N-1). Matches the convention
    used by Syntetos-Boylan papers for CV^2 on short series."""
    if not xs:
        return 0.0
    return sum((x - mean) ** 2 for x in xs) / len(xs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def observation_window(
    as_of: date,
    lookback_weeks: int,
    history_start: Optional[date] = None,
) -> list[date]:
    """Completed weeks before as_of's week that we actually have
    data for, oldest first.

    Weeks before invoice history begins are UNKNOWN, not zero demand --
    counting them as zeros inflated ADI ~4x on a 13-week database and
    classified steady buyers as intermittent. `history_start` is the
    earliest invoice on record; the window starts at the first FULL
    week on or after it (a partial first week would look like a
    low-demand week).
    """
    window = weeks.previous_completed_weeks(weeks.week_start(as_of), lookback_weeks)
    if history_start is None:
        return window
    first_full = (history_start if weeks.week_start(history_start) == history_start
                  else weeks.week_start(history_start) + timedelta(days=7))
    return [w for w in window if w >= first_full]


def classify(
    weekly_qtys: dict[date, float],
    as_of: date,
    lookback_weeks: int = config.CLASSIFICATION_LOOKBACK_WEEKS,
    *,
    history_start: Optional[date] = None,
    adi_threshold: float = config.ADI_THRESHOLD,
    cv_squared_threshold: float = config.CV_SQUARED_THRESHOLD,
    min_weeks_observed: int = config.CLASSIFICATION_MIN_WEEKS_OBSERVED,
    min_non_zero_for_individual: int = config.MIN_NON_ZERO_WEEKS_FOR_INDIVIDUAL,
) -> Classification:
    """Classify demand pattern over the trailing `lookback_weeks` before `as_of`.

    Args:
        weekly_qtys: {week-start: delivered-or-true-demand}. Missing weeks
                     are treated as zero demand events (the point of ADI).
        as_of: anchor date. Classification looks at the lookback_weeks weeks
               strictly BEFORE as_of's week.
        lookback_weeks: trailing window. Default 52 (one year).

    Returns:
        A Classification. Always returns a result; use the pattern field to
        decide what to do.

    Interpretation:
        - fallback_reason is set to SPARSE_HISTORY when individual-fit
          confidence is not met (non_zero_weeks < min_required). The engine
          still consults recommended_model but marks the forecast
          bIsFallback=1 and uses a group-level anchor to compute the value.
        - fallback_reason stays None when individual fit is OK.
        - pattern = INSUFFICIENT when total_weeks_observed < min_weeks_observed.
          Even a well-tuned model needs at least a handful of weeks.
        - history_start clips the window to weeks with data (see
          observation_window).
    """
    window = observation_window(as_of, lookback_weeks, history_start)

    # Count observed weeks (any value, including zero) vs non-zero weeks.
    observed_qtys = [weekly_qtys.get(wk, 0.0) for wk in window]
    non_zero = [q for q in observed_qtys if q > 0]

    total_observed = len(observed_qtys)
    non_zero_count = len(non_zero)

    # Guard: not enough history even to classify.
    if non_zero_count < 2 or total_observed < min_weeks_observed:
        return Classification(
            cv_squared=None,
            adi=None,
            pattern="INSUFFICIENT",
            non_zero_weeks=non_zero_count,
            total_weeks_observed=total_observed,
            min_required=min_non_zero_for_individual,
            recommended_model=config.MODEL_CODE_BASE,
            fallback_reason="SPARSE_HISTORY" if non_zero_count > 0 else "NEW_CUSTOMER",
        )

    # ADI = observed weeks / non-zero events.
    adi = total_observed / non_zero_count

    # CV^2 on the non-zero demand sizes.
    mu = _mean(non_zero)
    var = _variance(non_zero, mu)
    cv_squared = var / (mu ** 2) if mu > 0 else 0.0

    # Quadrant cut.
    if adi < adi_threshold and cv_squared < cv_squared_threshold:
        pattern = "SMOOTH"
    elif adi < adi_threshold and cv_squared >= cv_squared_threshold:
        pattern = "ERRATIC"
    elif adi >= adi_threshold and cv_squared < cv_squared_threshold:
        pattern = "INTERMITTENT"
    else:
        pattern = "LUMPY"

    # Fallback decision: not enough non-zero weeks for individual-leaf trust.
    fallback_reason: Optional[str] = None
    if non_zero_count < min_non_zero_for_individual:
        fallback_reason = "SPARSE_HISTORY"

    return Classification(
        cv_squared=cv_squared,
        adi=adi,
        pattern=pattern,
        non_zero_weeks=non_zero_count,
        total_weeks_observed=total_observed,
        min_required=min_non_zero_for_individual,
        recommended_model=_recommend_model(pattern),
        fallback_reason=fallback_reason,
    )


def confidence_grade(pattern: str, is_fallback: bool) -> str:
    """Translate (pattern, fallback) -> confidence letter grade.

    Simple heuristic for Phase 2 -- when conformal calibration lands in
    Phase 5+, the grade should come from empirical coverage instead.
    """
    return config.CONFIDENCE_GRADE_MATRIX.get((pattern, is_fallback), "F")
