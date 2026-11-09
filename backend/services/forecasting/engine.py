"""Forecast engine orchestrator.

Ties the Phase 2a building blocks together:
  classify -> pick model -> try individual fit -> fall back if needed
  -> emit a LeafResult ready to be written by the runner.

Scope: engine coordinates per-leaf logic. It does NOT open DB
transactions, create _tb_SOForecastRun rows, or write to the fact
tables. That's the runner's job (runner.py). This split lets us swap
transaction / batch / scheduler strategies without touching the
decision logic.

Models that compete for a leaf: every row in _tb_SOForecastModel with
bEnabled=1. The engine runs each enabled model and emits a LeafForecast
per model-week pair. Exactly ONE gets bIsActive=1 per (leaf, week) --
the one matching the classifier's recommended model, with fallback as
the tiebreaker if the recommended model declined.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import classifier as classifier_mod
from . import config
from . import delivery_pattern as delivery_mod
from . import history_loader
from . import weeks
from .classifier import Classification
from .delivery_pattern import DeliveryPattern
from .fallback import FallbackResolver, FallbackSource, own_rate_fallback
from .history_loader import INVOICE_LINES_SQL, LeafContext
from .models import base_median, seasonal_naive_yoy


@dataclass(frozen=True)
class LeafForecast:
    """One row ready to write to _tb_SOForecast."""
    customer_account: str
    stock_code: str
    week_start: date                   # Monday of the forecast week
    model_code: str
    model_id: int
    predicted_qty: float
    pi_lower: Optional[float]
    pi_upper: Optional[float]
    pattern: str
    confidence_grade: str
    is_fallback: bool
    fallback_reason: Optional[str]
    fallback_source: Optional[float]   # the raw fallback-source value, for audit
    is_active: bool                    # exactly one True per (leaf, week) across models


@dataclass
class LeafResult:
    """Everything the engine produces for one (customer, SKU) across the horizon."""
    customer_account: str
    stock_code: str
    classification: Classification
    context: Optional[LeafContext]
    forecasts: list[LeafForecast] = field(default_factory=list)
    skipped_reason: Optional[str] = None   # 'NO_CONTEXT' | 'ALL_MODELS_DECLINED' | None


# ---------------------------------------------------------------------------
# Pure dispatch helpers (unit-testable with hand-crafted history)
# ---------------------------------------------------------------------------

def run_model(
    model_code: str,
    history: dict[date, float],
    forecast_week: date,
    as_of_week: Optional[date] = None,
) -> Optional[float]:
    """Dispatch to the right pure model function. Returns the predicted
    qty or None if the model declined. CROSTON_SBA + LIGHTGBM_PANEL +
    ENSEMBLE aren't implemented yet -- they decline (return None) so
    the engine falls back cleanly.
    """
    if model_code == config.MODEL_CODE_BASE:
        # Level forecast: the median of the 6 COMPLETED weeks before the
        # as-of week, the same number for every week of the horizon --
        # exactly what history.py:order_suggestions does. Anchoring the
        # window on the forecast week instead pulled the in-progress and
        # future weeks in as zeros (W+1 lost a real week, W+4 declined).
        result = base_median.forecast(history, as_of_week or forecast_week)
        return result.predicted_qty if result is not None else None
    if model_code == config.MODEL_CODE_SEASONAL_NAIVE_YOY:
        result = seasonal_naive_yoy.forecast(history, forecast_week, as_of_week=as_of_week)
        return result.predicted_qty if result is not None else None
    # Other codes not implemented yet -- decline.
    return None


def build_week_forecasts(
    enabled_models: dict[str, int],
    context: LeafContext,
    history: dict[date, float],
    week: date,
    classification: Classification,
    fallback: Optional[FallbackSource],
    as_of_week: Optional[date] = None,
) -> list[LeafForecast]:
    """Build one LeafForecast per enabled model for one forecast week.

    `as_of_week` anchors every model's history window (see run_model);
    the engine always passes it. None = anchor on `week` (tests that
    fabricate history ending just before the forecast week).

    Pure function: no DB access, no state. Exposed for direct unit
    testing. The ForecastEngine method of the same shape delegates here.

    Rules (also enforced in production via the engine):
      - For each enabled model, call run_model. If a value comes back,
        emit a non-fallback forecast.
      - If the leaf's FALLBACK OWNER declined, emit a fallback forecast
        for it using the resolved value. The owner is the classifier's
        recommended model when it is enabled, otherwise BASE_MEDIAN_6W
        (the default active slot) -- so a leaf whose recommended model
        isn't enabled yet (e.g. INTERMITTENT -> CROSTON_SBA) still gets
        its fallback instead of no forecast at all. Exactly one model
        per leaf-week ever carries the fallback.
      - If NO model produced anything (owner declined, no fallback
        value either), return [].
      - Exactly ONE forecast per (leaf, week) is marked bIsActive=1:
        the recommended model's row (whether real or fallback). pick_active
        decides the tiebreaker.
    """
    rows_by_model: dict[str, LeafForecast] = {}
    owner = fallback_owner(enabled_models, classification)

    for model_code, model_id in enabled_models.items():
        predicted = run_model(model_code, history, week, as_of_week)

        if predicted is not None:
            rows_by_model[model_code] = LeafForecast(
                customer_account=context.customer_account,
                stock_code=context.stock_code,
                week_start=week,
                model_code=model_code,
                model_id=model_id,
                predicted_qty=predicted,
                pi_lower=None,
                pi_upper=None,
                pattern=classification.pattern,
                confidence_grade=classifier_mod.confidence_grade(
                    classification.pattern, False,
                ),
                is_fallback=False,
                fallback_reason=None,
                fallback_source=None,
                is_active=False,   # set below
            )
            continue

        # Model declined. Fallback applies only to the leaf's fallback
        # owner -- other models stay silent on this leaf.
        if model_code == owner and fallback is not None:
            rows_by_model[model_code] = LeafForecast(
                customer_account=context.customer_account,
                stock_code=context.stock_code,
                week_start=week,
                model_code=model_code,
                model_id=model_id,
                predicted_qty=fallback.value,
                pi_lower=None,
                pi_upper=None,
                pattern=classification.pattern,
                confidence_grade=classifier_mod.confidence_grade(
                    classification.pattern, True,
                ),
                is_fallback=True,
                fallback_reason=classification.fallback_reason or "SPARSE_HISTORY",
                fallback_source=fallback.value,
                is_active=False,   # set below
            )

    # Pick exactly one active row per week.
    active_code = pick_active(rows_by_model, classification)
    if active_code is not None:
        active_row = rows_by_model[active_code]
        rows_by_model[active_code] = LeafForecast(
            **{**active_row.__dict__, "is_active": True}
        )

    return list(rows_by_model.values())


def fallback_owner(
    enabled_models: dict[str, int],
    classification: Classification,
) -> Optional[str]:
    """The one enabled model that carries a leaf's fallback forecast.

    Recommended model if enabled; else BASE_MEDIAN_6W if enabled; else
    the first enabled model alphabetically (deterministic); None if no
    model is enabled at all.
    """
    if classification.recommended_model in enabled_models:
        return classification.recommended_model
    if config.MODEL_CODE_BASE in enabled_models:
        return config.MODEL_CODE_BASE
    return sorted(enabled_models)[0] if enabled_models else None


def pick_active(
    rows_by_model: dict[str, LeafForecast],
    classification: Classification,
) -> Optional[str]:
    """Choose the model whose row gets bIsActive=1 for one (leaf, week).

    Priority:
      1. Classifier's recommended model, if present in rows_by_model.
      2. BASE_MEDIAN_6W as the pragmatic default (always available
         when any model fit at all).
      3. Any single remaining row (deterministic: first alphabetically).
    """
    if not rows_by_model:
        return None
    rec = classification.recommended_model
    if rec in rows_by_model:
        return rec
    if config.MODEL_CODE_BASE in rows_by_model:
        return config.MODEL_CODE_BASE
    return sorted(rows_by_model.keys())[0]


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ForecastEngine:
    """Per-leaf forecast orchestration.

    Instantiated once per run. Loads the model registry + fallback
    aggregates at init so every subsequent classify_and_forecast() call
    is cheap.
    """

    def __init__(
        self,
        db: Session,
        as_of: date,
        fallback_resolver: Optional[FallbackResolver] = None,
    ) -> None:
        self._db = db
        self._as_of = weeks.week_start(as_of)
        self._models = self._load_enabled_models()
        self._fallback_resolver = fallback_resolver or FallbackResolver(db, as_of)
        # Earliest invoice on record: windows are clipped to it so weeks
        # before the data begins aren't read as zero demand.
        self._history_start = history_loader.load_history_start(db)

    # ---------------------------------------------------------------
    # Leaf discovery
    # ---------------------------------------------------------------

    def find_active_leaves(
        self,
        min_recent_weeks: int = 52,
    ) -> list[tuple[str, str]]:
        """Return (customer_account, stock_code) pairs with any delivered
        activity in the last `min_recent_weeks` weeks.

        Filters:
          - Customer must be On_Hold = 0 in Client.
          - SKU must be ItemActive = 1 in StkItem.
          - At least one invoiced row for the (customer, SKU) in the window.
        """
        window_start = self._as_of.toordinal() - 7 * min_recent_weeks
        from datetime import date as _date
        window_start_d = _date.fromordinal(window_start)

        sql = """
            SELECT DISTINCT h.CustomerAccount AS customer, h.StockCode AS stock
            FROM """ + INVOICE_LINES_SQL + """ h
            JOIN [BA_00_LPD_CON].dbo.Client CL
                ON CL.Account = h.CustomerAccount
               AND ISNULL(CL.On_Hold, 0) = 0
            JOIN [BA_00_LPD_CON].dbo.StkItem STK
                ON STK.Code = h.StockCode
               AND STK.ItemActive = 1
            WHERE h.OnOrder = 0
              AND h.TxDate >= :window_start
              AND h.TxDate <  :anchor
              AND h.Quantity > 0
            ORDER BY customer, stock   -- stable: --limit runs repeat the same leaves
        """
        rows = self._db.execute(
            text(sql),
            {"window_start": window_start_d, "anchor": self._as_of},
        ).mappings().all()

        return [(r["customer"], r["stock"]) for r in rows]

    # ---------------------------------------------------------------
    # Per-customer learnings (one per unique customer in the leaf set)
    # ---------------------------------------------------------------

    def learn_delivery_pattern(self, customer_account: str) -> DeliveryPattern:
        """Learn the customer's day-of-week delivery mix."""
        deliveries = history_loader.load_deliveries(
            self._db,
            customer_account,
            self._as_of,
        )
        return delivery_mod.learn(deliveries, self._as_of)

    # ---------------------------------------------------------------
    # Per-leaf forecast
    # ---------------------------------------------------------------

    def classify_and_forecast(
        self,
        customer_account: str,
        stock_code: str,
        forecast_weeks: list[date],
    ) -> LeafResult:
        """Produce classification + forecast rows for every enabled
        model across every requested forecast week.

        Args:
            forecast_weeks: list of week starts (Mondays) the engine should
                            predict for. Typically next 4 weeks.

        Returns:
            LeafResult. If context can't be resolved (customer or SKU
            unknown), returns a result with skipped_reason='NO_CONTEXT'.
        """
        # Normalise all weeks to their Monday.
        forecast_weeks = [weeks.week_start(w) for w in forecast_weeks]

        context = history_loader.load_leaf_context(
            self._db, customer_account, stock_code,
        )
        if context is None:
            return LeafResult(
                customer_account=customer_account,
                stock_code=stock_code,
                classification=self._empty_classification(),
                context=None,
                skipped_reason="NO_CONTEXT",
            )

        # Pull true-demand history once; reuse across all model + week pairs.
        history = history_loader.load_weekly_history(
            self._db,
            customer_account,
            stock_code,
            self._as_of,
            lookback_weeks=config.CLASSIFICATION_LOOKBACK_WEEKS,
        )

        classification = classifier_mod.classify(
            history, self._as_of, history_start=self._history_start,
        )

        # Resolve fallback value once per leaf (same for every forecast week).
        # Own weekly rate when the leaf has any sales of its own; peer SKU
        # average x customer magnitude only for leaves with none.
        fallback: Optional[FallbackSource] = None
        if classification.fallback_reason is not None or classification.pattern == "INSUFFICIENT":
            fallback = own_rate_fallback(
                history, self._as_of, config.CLASSIFICATION_LOOKBACK_WEEKS, self._history_start,
            ) or self._fallback_resolver.resolve(customer_account, stock_code)

        forecasts: list[LeafForecast] = []
        for week in forecast_weeks:
            week_rows = self._build_week_forecasts(
                context=context,
                history=history,
                week=week,
                classification=classification,
                fallback=fallback,
            )
            forecasts.extend(week_rows)

        skipped: Optional[str] = None
        if not forecasts:
            skipped = "ALL_MODELS_DECLINED"

        return LeafResult(
            customer_account=customer_account,
            stock_code=stock_code,
            classification=classification,
            context=context,
            forecasts=forecasts,
            skipped_reason=skipped,
        )

    # ---------------------------------------------------------------
    # Internals
    # ---------------------------------------------------------------

    def _build_week_forecasts(
        self,
        context: LeafContext,
        history: dict[date, float],
        week: date,
        classification: Classification,
        fallback: Optional[FallbackSource],
    ) -> list[LeafForecast]:
        """Instance wrapper around the pure build_week_forecasts function.
        Delegates so tests can hit the pure version with fabricated inputs."""
        return build_week_forecasts(
            enabled_models=self._models,
            context=context,
            history=history,
            week=week,
            classification=classification,
            fallback=fallback,
            as_of_week=self._as_of,
        )

    def _load_enabled_models(self) -> dict[str, int]:
        """{cCode: idForecastModel} for every bEnabled=1 model.

        Phase 2b default: only BASE_MEDIAN_6W. Phase 2c flips others on
        once their algo + leaderboard data is in place.
        """
        rows = self._db.execute(
            text("""
                SELECT idForecastModel, cCode
                FROM [SOAssistant].dbo._tb_SOForecastModel
                WHERE bEnabled = 1
            """)
        ).mappings().all()
        return {r["cCode"]: int(r["idForecastModel"]) for r in rows}

    @staticmethod
    def _empty_classification() -> Classification:
        return Classification(
            cv_squared=None,
            adi=None,
            pattern="INSUFFICIENT",
            non_zero_weeks=0,
            total_weeks_observed=0,
            min_required=config.MIN_NON_ZERO_WEEKS_FOR_INDIVIDUAL,
            recommended_model=config.MODEL_CODE_BASE,
            fallback_reason="NEW_CUSTOMER",
        )
