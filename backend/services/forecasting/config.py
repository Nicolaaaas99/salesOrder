"""Tunable thresholds for the forecasting engine.

Everything that affects a classification cut, a fallback decision, a
reasonableness flag, or a confidence grade lives here. Importing modules
should pull constants from here rather than hard-coding values, so when
we tune after seeing real data we touch one file and nothing else.
"""


# ---------------------------------------------------------------------------
# Base model (replicates history.py:order_suggestions exactly)
# ---------------------------------------------------------------------------

# Number of completed weeks to look back when computing the median.
BASE_MODEL_WINDOW_WEEKS = 6

# Minimum non-zero weeks in the window required to produce a prediction.
# Below this, the base model declines to forecast and the engine routes
# the leaf to a fallback.
BASE_MODEL_MIN_NON_ZERO = 3


# ---------------------------------------------------------------------------
# Pattern classification (Syntetos-Boylan quadrant)
# ---------------------------------------------------------------------------

# Average Demand Interval (weeks between non-zero demand events).
# >= 1.32 -> sporadic timing (INTERMITTENT or LUMPY).
ADI_THRESHOLD = 1.32

# Squared coefficient of variation of non-zero demand values.
# >= 0.49 -> variable size (ERRATIC or LUMPY).
CV_SQUARED_THRESHOLD = 0.49

# Trailing weeks used to compute ADI / CV^2.
CLASSIFICATION_LOOKBACK_WEEKS = 52

# Below this many weeks observed, pattern = INSUFFICIENT and engine
# forces fallback regardless of model recommendation.
CLASSIFICATION_MIN_WEEKS_OBSERVED = 4


# ---------------------------------------------------------------------------
# Model selection: trust an individual-leaf fit vs. fall back to group
# ---------------------------------------------------------------------------

# Minimum non-zero weeks needed to trust a per-(customer, SKU) model fit.
# Fewer than this -> engine marks the forecast as a fallback with reason
# SPARSE_HISTORY and uses a group-level anchor (category average by default).
MIN_NON_ZERO_WEEKS_FOR_INDIVIDUAL = 12


# ---------------------------------------------------------------------------
# Delivery pattern learner
# ---------------------------------------------------------------------------

# How far back to look when learning a customer's day-of-week delivery mix.
DELIVERY_PATTERN_LOOKBACK_WEEKS = 26

# Confidence tiers based on sample size. Below LOW threshold the engine
# falls back to an even split across the days the customer did deliver.
DELIVERY_PATTERN_HIGH_WEEKS = 20
DELIVERY_PATTERN_MED_WEEKS = 8


# ---------------------------------------------------------------------------
# Confidence grade on individual forecast predictions
# ---------------------------------------------------------------------------

# Simple heuristic until Phase 5 (conformal calibration): grade based on
# whether the forecast used an individual fit or a fallback, modified by
# pattern. Can be overridden per-model in Phase 2b.
#
# Pattern + fallback => grade mapping:
#   SMOOTH + individual   -> A
#   SMOOTH + fallback     -> C
#   ERRATIC + individual  -> B
#   ERRATIC + fallback    -> D
#   INTERMITTENT          -> C (never truly confident at a weekly grain)
#   LUMPY                 -> D
#   INSUFFICIENT          -> F
CONFIDENCE_GRADE_MATRIX = {
    ("SMOOTH", False):       "A",
    ("SMOOTH", True):        "C",
    ("ERRATIC", False):      "B",
    ("ERRATIC", True):       "D",
    ("INTERMITTENT", False): "C",
    ("INTERMITTENT", True):  "D",
    ("LUMPY", False):        "D",
    ("LUMPY", True):         "D",
    ("INSUFFICIENT", False): "F",
    ("INSUFFICIENT", True):  "F",
}


# ---------------------------------------------------------------------------
# Reasonableness check thresholds (scenario cascade -- used in Phase 3)
# ---------------------------------------------------------------------------

# Trailing window for the P95 observed-qty check.
TARGET_P95_LOOKBACK_WEEKS = 52

# Target implies YoY growth greater than this percent -> flag.
TARGET_MAX_YOY_GROWTH_PCT = 25.0

# Decision #6 (settled 2026-11-03): a SKU whose FIRST INVOICE (any
# customer) is within this many weeks of as_of is a "new product" and
# triggers new_product_requires_review whenever a scenario changes its
# target. Launch = first invoice, not StkItem_dCreatedDate.
NEW_PRODUCT_LAUNCH_WINDOW_WEEKS = 13

# A target below one pack size is impossible to fulfil -> flag or snap up.
MIN_TARGET_PACKS = 1

# Percentile used by the above_historical_p95 check (0..1, linear
# interpolation -- same as numpy.percentile's default).
TARGET_P95_PERCENTILE = 0.95

# How much history the reasonableness checks load per leaf. Must cover
# P95 lookback (52w) PLUS the furthest forecast week's same-week-last-
# year anchor, so 104 weeks supports scenario periods up to a year out.
TARGET_FLAG_HISTORY_LOOKBACK_WEEKS = 104

# A SKU whose first invoice falls within this many weeks of the earliest
# invoice on record was already selling before history begins -- its
# launch date is unknown, so it is NOT treated as a new product.
NEW_PRODUCT_HISTORY_START_GRACE_WEEKS = 4


# ---------------------------------------------------------------------------
# Model registry codes (string -> idForecastModel resolved at engine init)
# ---------------------------------------------------------------------------

MODEL_CODE_BASE = "BASE_MEDIAN_6W"
MODEL_CODE_SEASONAL_NAIVE_YOY = "SEASONAL_NAIVE_YOY"
MODEL_CODE_CROSTON_SBA = "CROSTON_SBA"
MODEL_CODE_LIGHTGBM_PANEL = "LIGHTGBM_PANEL"
MODEL_CODE_ENSEMBLE = "ENSEMBLE_INVERSE_MAPE"
