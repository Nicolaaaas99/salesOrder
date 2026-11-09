# Demand Forecasting & Target Management — Build Plan

Authoritative plan + status for the forecasting module build. Updated as
each phase lands. Companion to [FORECASTING_WALKTHROUGH.md](FORECASTING_WALKTHROUGH.md)
(the Phase 1 scenario walkthrough with expected outputs).

**Current status**: Phase 5 complete. 2026-11-06 rethink in progress (see the "Rethink 2026-11" section): Phases A–D done (quick fixes, ISO weeks, target resolver, Targets screen), 248 tests passing.
Manager flow (create → preview → review → approve → workbench → override →
clear → cancel) verified end to end against the live DB. Migration 001
applied. Next: Phase 6 (scheduling + opportunity detection).

---

## Phases at a glance

| Phase | Scope | Status | Delivers |
|---|---|---|---|
| **1**  | SQL schema + seed data + inspection views | ✅ **done** | 14 new tables + model registry + 5 test scenarios + 11 inspection queries + walkthrough doc |
| **2a** | Headless engine foundation (pure building blocks) | ✅ **done** | config, weeks, classifier, delivery_pattern, base_median model, history_loader, 53 unit tests |
| **2b** | Headless engine orchestration | ✅ **done** | fallback resolver, seasonal_naive_yoy model, engine orchestrator, nightly runner, 30 additional unit tests, smoke-test script |
| **3**  | Scenario cascade + target materialisation + API | ✅ **done** | reasonableness flags, cascade service, scenario CRUD endpoints, target endpoints |
| **4**  | Rep-facing wiring | ✅ **done** | persist suggestions on order save, target-aware suggested-qty in SalesOrderScreen, suggestion audit |
| **5**  | Management UI | ✅ **done** | TargetWorkbenchScreen, ScenarioModal, OpportunityInbox, ModelLeaderboard |
| **6**  | Scheduling + opportunity detection | ⏳ next | nightly job (full forecast run), hourly committed-qty refresh, weekly task generator |

Optional later: **Phase 7** — migrate off / retire the current `_tb_SOTargets` table and its UI once the new system covers that use case.

---

## Design cornerstones (locked in)

1. **Four separate measurements** — forecast, target, suggestion, actual. Four distinct columns on `_tb_SOForecastActual` + four distinct error metrics (`fForecastMAPE`, `fTargetAchievementPct`, `fSuggestedAcceptanceRate`, `fTargetAggression`). Never collapsed into a single "accuracy" number.
2. **ISO weeks, Monday to Sunday** (switched from Sunday-aligned on 2026-11-06). One source of truth: `weeks.week_start` (Python) / `weeks.week_start_sql` (T-SQL, DATEFIRST-invariant, anchored on Monday 1900-01-01). Used by history, analytics, the performance strip, legacy targets and the frontend (`weekStartIso`, `getWeekRange`). Every `dWeekStart DATE` is a Monday.
3. **DC from `Client.ulARDistCentre`** — denormalised onto fact rows at write time. No separate DC master table.
4. **Models never auto-promote** — only `BASE_MEDIAN_6W` is `bEnabled=1` by default. Promotion gate requires 90 days + 50 pairs + statistical significance + explicit manager approval.
5. **Fallbacks are first-class** — every forecast row carries `bIsFallback` + `cFallbackReason`. Classifier returns the fallback decision, orchestrator consumes it.
6. **Actual sales ≠ actual demand** — `_tb_SOStockoutEvent` + `_tb_SOShortDelivery` capture stockouts. `_tb_SOForecastActual.fEstimatedTrueDemand` = `fDeliveredQty` + shortfall. Models train on true demand; dashboards show delivered.
7. **Persist every suggestion + decision** — `_tb_SOOrderSuggestion` row per presentation with `fSuggestedQty`, `fAcceptedQty`, `cDecision`. Enables measuring rep execution gap separately from model accuracy.
8. **Scenarios cascade proportionally, with reasonableness flags** — `fTargetQty = fForecastQty × (1 + lever)`, then flag any leaf exceeding P95, >25% YoY, launched <52w ago, or below pack-size minimum. Scenario cannot be approved until each flag is individually reviewed.

---

## The 5 corrections — where each lives

| # | Correction | Schema location | Code location |
|---|---|---|---|
| 1 | Persist suggestions + rep decisions | `_tb_SOOrderSuggestion` | Phase 4 wires this into `history.py:order_suggestions` + order save |
| 2 | Appropriate measures, not blanket MAPE | 4 columns on `_tb_SOForecastActual` | Phase 3 (scenario cascade + scorecard writers) |
| 3 | Actual sales ≠ actual demand | `_tb_SOStockoutEvent`, `_tb_SOShortDelivery`, `_tb_SOForecastActual.fEstimatedTrueDemand` | `history_loader.combine_true_demand` (Phase 2a ✅) |
| 4 | Model selection with fallbacks | `_tb_SODemandClassification.iMinRequired` + `cRecommendedModel` + `cFallbackReason`; `_tb_SOForecast.bIsFallback` | `classifier.classify` (Phase 2a ✅); fallback-source resolver in Phase 2b |
| 5 | ISO + scheduling decisions upfront | Sunday-week convention locked everywhere | `weeks.py` (Phase 2a ✅); scheduler in Phase 6 |

---

## Open decisions

As each decision lands, record the resolution here.

| # | Decision | Resolution | Phase settled |
|---|---|---|---|
| 1 | Week convention | **Revised 2026-11-06: ISO Mon–Sun.** The Sunday convention only existed because legacy code used it. Mon–Sat deliveries now fall in one week, and week numbers match Excel/SQL ISO weeks. Test forecast/target data was wiped and regenerated. | Phase 1, revised ✅ |
| 2 | DC source | `Client.ulARDistCentre` (confirmed exists), denormalised onto facts | Phase 1 ✅ |
| 3 | Pattern thresholds | ADI 1.32, CV² 0.49 (Syntetos-Boylan), min_non_zero_for_individual=12 | Phase 2a ✅ (reproduced in tests) |
| 4 | Legacy `_tb_SOTargets` fate | Keep for now, deprecate / migrate in Phase 7 | Phase 1 ✅ |
| 5 | Fallback source rule | **Revised 2026-11-03:** leaf has any own sales in the observed window → its own average weekly demand (total ÷ observed weeks, empty weeks included; `own_rate_fallback`). No own sales → peer SKU average × customer category magnitude (`magnitude_from_aggregates`). The fallback is carried by the recommended model if enabled, else `BASE_MEDIAN_6W` (`fallback_owner`). Peer × magnitude alone came out a median 7× the customer's typical order on live data (max 87×). | Phase 2b ✅, revised pre-Phase 5 |
| 6 | Reasonableness check thresholds | P95 of trailing-52w **non-zero** weekly true demand; >25% vs same Sunday-week last year (skipped with no baseline); **new product = SKU's first invoice (any customer) < 13 weeks ago** (decided 2026-11-03; launch is the first invoice, not `StkItem_dCreatedDate`; a first invoice within 4w of the earliest invoice on record = already selling, launch unknown), only when the lever changes the leaf; positive target < 1 pack. Every flag needs an individual ACCEPT/EXCLUDE review. All tunable in `config.py`. | Phase 3 ✅ (tunable -- revisit once real scenarios run) |
| 7 | Model auto-promotion gate | 90 days + 50 pairs + paired t-test p<0.05 + manager approval | Phase 2b (locked in config) |
| 8 | Weekly target → per-delivery suggestion | **Remaining week on this order**: suggested = max(0, weekly target − already ordered/invoiced this week), snapped to pack -- same shape as the legacy suggestion; later deliveries pick up any shortfall. No day-share split. **Target rows round UP to the next full pack** so the ask is always met (target 12, pack 10 → 20); base suggestions keep nearest-pack. | Phase 4 ✅ (2026-11-03) |

---

## Phase 1 — SQL schema + seed data (done)

**Goal**: lay down the fact tables, dimension columns, and 5 hand-computed test scenarios so the user can inspect in SSMS before any Python runs.

**Files**:
- [backend/sql/forecasting_schema.sql](backend/sql/forecasting_schema.sql) — 14 tables + model registry seed
- [backend/sql/forecasting_seed.sql](backend/sql/forecasting_seed.sql) — 5 scenarios with hand-computed values
- [backend/sql/forecasting_inspection.sql](backend/sql/forecasting_inspection.sql) — 4 views + 11 inspection queries
- [FORECASTING_WALKTHROUGH.md](FORECASTING_WALKTHROUGH.md) — narrative + expected outputs

**Tables created** (`_tb_SO*`, under `[SOAssistant].dbo`):
`_tb_SOForecastModel`, `_tb_SODemandClassification`, `_tb_SODeliveryPattern`, `_tb_SOForecastRun`, `_tb_SOForecast`, `_tb_SOScenario`, `_tb_SOScenarioTarget`, `_tb_SOOrderSuggestion`, `_tb_SOStockoutEvent`, `_tb_SOShortDelivery`, `_tb_SOForecastActual`, `_tb_SOForecastScorecard`, `_tb_SOTask`, `_tb_SOForecastAudit`.

**Views created** (`_vw_SO*`, under `[SOAssistant].dbo`):
`_vw_SOForecastActive`, `_vw_SOForecastClosedLoop`, `_vw_SOForecastHierarchyRollup`, `_vw_SOModelLeaderboard`.

**Verification**: run the 3 SQL files in SSMS; walk through 11 inspection queries against expected outputs in the walkthrough doc. All 11 matched on first run (after 2 trivial bug fixes).

---

## Phase 2a — Headless engine foundation (done)

**Goal**: pure Python building blocks (no DB writes yet). Classify demand patterns, learn delivery day-shares, replicate the current base-model algorithm exactly, apply short-delivery uplift. Everything unit-testable without a database.

**Files**:
- [backend/services/forecasting/__init__.py](backend/services/forecasting/__init__.py)
- [backend/services/forecasting/config.py](backend/services/forecasting/config.py) — all tunable thresholds
- [backend/services/forecasting/weeks.py](backend/services/forecasting/weeks.py) — Sunday-week math
- [backend/services/forecasting/history_loader.py](backend/services/forecasting/history_loader.py) — DB loaders + pure uplift transform
- [backend/services/forecasting/classifier.py](backend/services/forecasting/classifier.py) — Syntetos-Boylan quadrant + fallback decision + grade
- [backend/services/forecasting/delivery_pattern.py](backend/services/forecasting/delivery_pattern.py) — day-of-week share learner
- [backend/services/forecasting/models/__init__.py](backend/services/forecasting/models/__init__.py)
- [backend/services/forecasting/models/base_median.py](backend/services/forecasting/models/base_median.py) — exact replica of `history.py:order_suggestions` base algo

**Tests** (53 passing in ~0.7s):
- [backend/tests/forecasting/test_weeks.py](backend/tests/forecasting/test_weeks.py)
- [backend/tests/forecasting/test_base_median.py](backend/tests/forecasting/test_base_median.py)
- [backend/tests/forecasting/test_classifier.py](backend/tests/forecasting/test_classifier.py)
- [backend/tests/forecasting/test_delivery_pattern.py](backend/tests/forecasting/test_delivery_pattern.py)
- [backend/tests/forecasting/test_history_loader.py](backend/tests/forecasting/test_history_loader.py)

**Infra**:
- [backend/pytest.ini](backend/pytest.ini) — test discovery config
- `pytest>=8.0.0` added to [backend/requirements.txt](backend/requirements.txt)

**Verification**:
```powershell
cd c:\Dev\salesOrder\backend
.\.venv\Scripts\Activate.ps1
python -m pytest tests/forecasting -v
# Expected: 53 passed
```

**Key tests that lock in design**:
- `test_predicts_400` — Scenario A base-model forecast exactly 400 L
- `test_half_up_vs_bankers_rounding` — guards the rounding idiom from `history.py`
- `test_matches_legacy_history_py_implementation` — guards Sunday-week math vs legacy
- `test_without_uplift_recurring_stockouts_poison_the_median` — explicit demo of correction 3's value
- `test_classifies_as_intermittent` + `test_adi_equals_five` — classifier reproduces Scenario C

---

## Phase 2b — Headless engine orchestration (done)

**Goal**: tie the Phase 2a building blocks into a real nightly run that writes `_tb_SOForecast`, `_tb_SODemandClassification`, `_tb_SODeliveryPattern`, and manages a `_tb_SOForecastRun` row.

**Files**:
- [backend/services/forecasting/fallback.py](backend/services/forecasting/fallback.py) — `FallbackResolver` precomputes peer_sku_avg + customer category magnitude dicts; `.resolve(cust, sku)` is an O(1) lookup
- [backend/services/forecasting/models/seasonal_naive_yoy.py](backend/services/forecasting/models/seasonal_naive_yoy.py) — Model B: `same_week_last_year × (recent_16w / prior_16w)`
- [backend/services/forecasting/engine.py](backend/services/forecasting/engine.py) — orchestrator with pure helpers (`run_model`, `build_week_forecasts`, `pick_active`) + DB-coupled `ForecastEngine` class
- [backend/services/forecasting/runner.py](backend/services/forecasting/runner.py) — `run_nightly_forecast()` opens a run, iterates leaves, writes rows, closes run, returns `RunStats`
- [backend/scripts/run_forecast_smoke_test.py](backend/scripts/run_forecast_smoke_test.py) — manual-run script against the live DB with `--limit N` cap and sample-row print

**Tests** (30 additional, 83 total passing):
- [backend/tests/forecasting/test_fallback.py](backend/tests/forecasting/test_fallback.py) — pure magnitude math (8 tests)
- [backend/tests/forecasting/test_seasonal_naive_yoy.py](backend/tests/forecasting/test_seasonal_naive_yoy.py) — happy path + decline conditions + 52-week alignment (9 tests)
- [backend/tests/forecasting/test_engine.py](backend/tests/forecasting/test_engine.py) — model dispatch, `build_week_forecasts` cases, `pick_active` priority rule (13 tests)

**Verification**:
```powershell
cd c:\Dev\salesOrder\backend
.\.venv\Scripts\python.exe -m pytest tests/forecasting -v
# Expected: 83 passed

# Then smoke-test against live DB (writes real forecast rows):
.\.venv\Scripts\python.exe scripts\run_forecast_smoke_test.py --limit 20
```

**Design decisions locked in**:
- Fallback value = `peer_sku_avg × customer_category_magnitude` (decision #5)
- Fallback applies only to the classifier's recommended model, not every enabled model (keeps the fact table focused)
- Exactly ONE forecast per (leaf, week) is marked `bIsActive=1`; others are shadow predictions
- Transaction strategy: one commit per leaf (bounded blast radius); run metadata committed separately so partial failures still produce a visible `_tb_SOForecastRun` row

---

## Phase 3 — Scenario cascade + target materialisation + API (done)

**Goal**: management can create a scenario, see cascaded leaf targets with reasonableness flags, review each flag, approve, and have targets materialised into `_tb_SOScenarioTarget`. Exposed via REST API.

**Files**:
- [backend/services/forecasting/reasonableness.py](backend/services/forecasting/reasonableness.py) — the four flag rules, pure functions
- [backend/services/forecasting/scenarios.py](backend/services/forecasting/scenarios.py) — scope, lever maths, stacking, bulk loaders, `ScenarioService` (create / edit / preview / approve / cancel / override)
- [backend/routers/forecasting.py](backend/routers/forecasting.py) — endpoints below, all `SUPERVISOR`+
- [backend/schemas.py](backend/schemas.py) — Phase 3 Pydantic models
- [backend/scripts/run_scenario_smoke_test.py](backend/scripts/run_scenario_smoke_test.py) — live-DB smoke test
- [backend/services/forecasting/runner.py](backend/services/forecasting/runner.py) — fix: a new run's active forecast now deactivates earlier runs' active rows for the same leaf-week

**Endpoints** (`/api/forecasting/...`):

| Method | Path | Notes |
|---|---|---|
| GET | `/models` | registry + `_vw_SOModelLeaderboard` metrics |
| GET | `/forecasts` | active (latest-run) leaf forecasts; filters + paging |
| GET | `/scenarios`, `/scenarios/{id}` | list (`?status=`) / one |
| POST | `/scenarios` | create → `DRAFT` |
| PATCH | `/scenarios/{id}` | edit `DRAFT`/`PREVIEW` → back to `DRAFT` |
| POST | `/scenarios/{id}/preview` | cascade + flags, no target writes; `DRAFT` → `PREVIEW` |
| POST | `/scenarios/{id}/approve` | body `{reviews: [{customer_account, stock_code, decision, note}]}`, decision = `ACCEPT` or `EXCLUDE`; every flagged leaf must be reviewed; → `ACTIVE` |
| POST | `/scenarios/{id}/cancel` | body `{reason}`; an `ACTIVE` scenario's unlocked targets are recomputed without it |
| GET | `/targets` | filters, `scenario_id`, `flagged_only`, `rollup` = route / dist_centre / category / customer / sku / week |
| PATCH | `/targets/{id}/override` | `{override_qty (null clears), reason, break_lock}` |

**Design decisions locked in**:
- **Scope** = `cScopeJson` (seed format): `distCentre / route / customer / category / sku / salesRep / telesales` lists, AND across dims, OR within; `excludeLeaves` holds leaves excluded at review. No agent dimension (forecasts carry no agent).
- **Levers**: `PERCENT` (5 = +5%), `ABSOLUTE_DELTA` and `FIXED_QTY` (per-week scope totals, split pro rata to forecast share).
- **Stacking**: several `ACTIVE` scenarios on one leaf-week apply in `idScenario` order on top of the latest active forecast.
- **Target row**: `fTargetQty` is always the *effective* target (override if set) so every view keeps working; `fOverrideQty` holds the manual value; `cSource` = OVERRIDE / SCENARIO / FORECAST.
- **Goalposts**: locked rows and weeks before the current week are never touched by (re)materialisation; overriding a locked row needs `break_lock`.
- **Preview does not persist targets** (per this plan). The Phase 1 seed's Scenario D has a target row while in `PREVIEW`; that row is a fixture only.
- Flag history is read from PostST directly (104 weeks, invoice/credit-note signs as in `_vw_SOCustomerInvoiceHistory`) plus `_tb_SOShortDelivery` — the view only spans 12 weeks.
- Every create / edit / approve (incl. each flag review) / cancel / override writes `_tb_SOForecastAudit`.

**Tests** (85 new, 168 total): `test_reasonableness.py`, `test_scenarios.py` (incl. walkthrough Scenario D: 57 → 85.5 with `above_historical_p95` + `new_product_requires_review`), `test_forecasting_router.py` (auth, validation, error mapping). `httpx` added to requirements for FastAPI's TestClient.

**Verification**:
```powershell
cd c:\Dev\salesOrder\backend
.\.venv\Scripts\python.exe -m pytest tests/forecasting -v
# Expected: 168 passed

# Live DB: create -> preview -> cancel (no target writes)
.\.venv\Scripts\python.exe scripts\run_scenario_smoke_test.py --dc Makhado --category Juice
# Also exercise approve (MERGE) + cancel (recompute/delete):
.\.venv\Scripts\python.exe scripts\run_scenario_smoke_test.py --dc Makhado --category Juice --approve
```

### Known issues found during Phase 3 — fixed before Phase 5 (2026-11-03)
1. ✅ **History capped at 12 weeks.** All engine queries (leaf discovery, weekly history, delivery patterns, both fallback aggregates) now read `history_loader.INVOICE_LINES_SQL` — PostST with the view's invoice logic, no date cap.
2. ✅ **`UX_SOForecast_RunLeaf` blocked shadow models.** [backend/sql/forecasting_migration_001_forecast_keys.sql](backend/sql/forecasting_migration_001_forecast_keys.sql) replaces it with `UX_SOForecast_RunModelLeaf` (adds the model) and adds `UX_SOForecast_OneActive` (one active forecast per leaf-week, enforced by the DB). `forecasting_schema.sql` updated for fresh installs. **Run the migration in SSMS.**
3. ✅ **Inspection QUERY 6** now joins scenarios with `OPENJSON` instead of `LIKE '%[id]%'`.
4. ⏳ **Year-over-year check can't fire until there's a year of invoices** — data limitation, not a bug. Importing older history into PostST resolves it (and now flows through to the engine, see 1).

Also found and fixed while doing the above:
5. ✅ **97 of 200 leaves got no forecast.** The fallback only attached to the recommended model; INTERMITTENT leaves recommend `CROSTON_SBA`, which isn't enabled. Fallback now goes to `fallback_owner` (recommended if enabled, else `BASE_MEDIAN_6W`). Run 8: 0 declined.
6. ✅ **Fallback values median 7× the customer's own orders** → own-rate fallback (decision #5 revised).
7. ✅ **Classifier counted weeks before the data began as zero demand** (52-week window on ~13 weeks of data → ADI inflated ~4×, steady buyers labelled intermittent). Windows now start at the first full week of invoice history (`classifier.observation_window`); the documented min-4-observed-weeks guard is now enforced. Run 8: 53 SMOOTH leaves (was ~0).
8. ✅ Leaf discovery is ordered, so `--limit` smoke runs repeat the same leaves. Verified: rerunning 20 leaves superseded all 76 previous active rows, 0 duplicates.

Tests: `test_short_history.py` (12) + engine fallback-owner tests.

Found while writing [FORECASTING_GUIDE.md](FORECASTING_GUIDE.md) (hand-worked example 5):
9. ✅ **Model history windows slid forward with the forecast week.** The base model used the 6 weeks before *each forecast week*, so W+1 counted the in-progress week as zero (lost a real week: 40 instead of the legacy 41) and W+3/W+4 often declined; the seasonal trend window had the same flaw. Windows are now anchored on the as-of week (`run_model(..., as_of_week)`), so the base forecast is one flat level across the horizon, exactly like `history.py:order_suggestions`. Smoke run 11: 800 forecasts for 200 leaves (was 770). Tests: `TestWindowAnchoredOnAsOf`. The guide's examples are checked against the real code by `backend/scripts/learn_forecasting_examples.py` (40/40).

---

## Phase 4 — Rep-facing wiring (done)

**Goal**: the Order Assistant becomes target-aware. Every presented suggestion is persisted; every rep decision (accept/modify/reject) is captured.

**Files**:
- [backend/services/forecasting/suggestions.py](backend/services/forecasting/suggestions.py) — week-target + forecast lookup, remaining-week maths, presentation recording, decision classification + recording (all best-effort: failures are logged, never block the rep)
- [backend/routers/history.py](backend/routers/history.py) — `/history/suggestions` is target-aware and takes `record=true`; new `/history/week-targets`
- [backend/routers/sales_orders.py](backend/routers/sales_orders.py) — `POST /sales-orders` records decisions when `suggestion_ids` is sent
- [backend/schemas.py](backend/schemas.py) — `OrderSuggestion` gains `source / base_weekly_qty / forecast_qty / target_qty / target_source / scenario_names / week_start / suggestion_id`; new `WeekTarget`; `SalesOrderCreate.suggestion_ids`
- [frontend/src/components/OrderAssistantScreen.tsx](frontend/src/components/OrderAssistantScreen.tsx) — loads with `record=true` + week targets, adds grid rows for targeted stocks never bought, passes presented ids to the save
- [frontend/src/components/HistoryGrid.tsx](frontend/src/components/HistoryGrid.tsx) — 🎯 on target-driven Sug cells; hover shows forecast → target (+% , scenario) → already covered → suggested
- [frontend/src/components/DraftOrderPanel.tsx](frontend/src/components/DraftOrderPanel.tsx) — "This week's targets" strip: committed (covered + draft) vs target per stock, live as the rep drafts
- [frontend/src/components/SalesOrderScreen.tsx](frontend/src/components/SalesOrderScreen.tsx) — `suggestionIds` prop → `suggestion_ids` on new orders only
- [backend/scripts/run_suggestion_smoke_test.py](backend/scripts/run_suggestion_smoke_test.py) — live-DB smoke test (no Evolution order is created)

**Behaviour**:
- **Weekly basis** = the leaf-week's materialised target (`cSource` SCENARIO/OVERRIDE) if there is one, else the legacy weekly median. Suggested = max(0, basis − (open SO qty + invoiced this week)), nearest pack -- decision #8.
- Targeted stocks are suggested even with too little history for a median, and get a grid row if the customer never bought them.
- **No targets → no visible change**: suggestions are byte-for-byte the legacy ones (verified against live data).
- **Presentation recording** happens only for the Order Assistant (`record=true`); the Deliveries drill-down reads suggestions without recording.
- **Decisions** on order save, for undecided rows of this customer + user only: ACCEPTED (qty = suggested), MODIFIED (other qty), REJECTED (not ordered), plus MANUAL_ADD rows for ordered stocks that weren't suggested. If a stock was presented more than once, the latest presentation is decided. Edits and the plain Sales Order screen record nothing.
- Order placement never fails because of suggestion recording.

**Tests** (20 new): `test_suggestions.py`.

**Verification**:
```powershell
cd c:\Dev\salesOrder\backend
.\.venv\Scripts\python.exe -m pytest tests/forecasting -v          # 185 passed
.\.venv\Scripts\python.exe scripts\run_suggestion_smoke_test.py --customer SPA227 --sku 323 --as-of 2026-11-09
# -> TARGET row with suggestion_id, week target listed, ACCEPTED/MODIFIED/REJECTED/MANUAL_ADD recorded, cleanup, RESULT: PASS
cd ..\frontend; npx tsc -b; npx vite build
```

**Rounding** (decided 2026-11-03): target-driven suggestions round *up* to the next full pack; base suggestions keep the legacy nearest-pack rule. Smoke test: SPA227 × 323, target 12, pack 10 → suggested 20.

---

## Phase 5 — Management UI (done)

**Goal**: management-facing screens for the people who create scenarios and watch progress.

**Where**: Analytics hub → four cards, shown to SUPERVISOR + ADMIN only (backend enforces the role too). `FORECASTING_UI_ENABLED` in [frontend/src/featureFlags.ts](frontend/src/featureFlags.ts) hides them (e.g. for a demo). The screens are code-split, so agents never download them.

**Frontend files**:
- [frontend/src/components/TargetWorkbenchScreen.tsx](frontend/src/components/TargetWorkbenchScreen.tsx) — one week, Company → DC → Route → Customer → Product drill with breadcrumbs; forecast / target (+% vs forecast) / delivered / on order / committed-vs-target bar; units or litres; product rows with a target open an override dialog (set, clear, break lock with reason)
- [frontend/src/components/ScenariosScreen.tsx](frontend/src/components/ScenariosScreen.tsx) — scenario list with status filter
- [frontend/src/components/ScenarioModal.tsx](frontend/src/components/ScenarioModal.tsx) — create / edit (lever, period, 7 scope dimensions with type-ahead), preview (totals, warnings, sample targets), per-leaf ACCEPT/EXCLUDE review with accept-all, approve, cancel with reason; read-only cascade view for ACTIVE scenarios
- [frontend/src/components/OpportunityInboxScreen.tsx](frontend/src/components/OpportunityInboxScreen.tsx) — `_tb_SOTask` cards: start / snooze / resolve (notes) / dismiss / reopen; open-closed-all + "assigned to me"
- [frontend/src/components/ModelLeaderboardScreen.tsx](frontend/src/components/ModelLeaderboardScreen.tsx) — weighted MAPE / MAE / bias per model + the promotion gate rules
- [frontend/src/api/forecasting.ts](frontend/src/api/forecasting.ts) — typed client; [frontend/src/components/forecasting/ui.tsx](frontend/src/components/forecasting/ui.tsx) — shared frame, formatters, progress bar

**Backend additions** ([backend/routers/forecasting.py](backend/routers/forecasting.py), [backend/services/forecasting/workbench.py](backend/services/forecasting/workbench.py)):
- `GET /forecasting/workbench?week&level&dist_centre&route&customer&measure` — planned leaves = active forecast or target in the week; target = effective target, else forecast; delivered = PostST in the week; on order = open SOs delivering in the week; litres via `StkItem.ulIISize` (same as Analytics)
- `GET /forecasting/scope-options?dim&q` — picker values taken from the active forecasts, so every scope value selects something
- `GET /forecasting/tasks`, `PATCH /forecasting/tasks/{id}`

**Tests**: `test_workbench.py` (6) — 210 total.

**Notes**:
- The inbox only has the two Phase 1 seed tasks until the Phase 6 opportunity detector writes real ones.
- Leaderboard accuracy stays empty until actuals are scored (Phase 6 nightly job).
- The workbench measures the plan: deliveries to customer × product pairs with no forecast or target aren't counted.

---

## Rethink 2026-11 (approved 2026-11-06)

Order: **A** quick fixes ✅ → **B** ISO weeks ✅ → **C** target resolver ✅ (set a target on any node; nested targets are pinned and the parent rebalances) → **D** one Targets screen (Workbench + Scenarios) ✅ → **E** Order Assistant rings (Customer/Route/DC top, Category/Product on the grid, fill after save) → **F** Opportunity Inbox (snooze date, evidence, plain language) → **G** Model Leaderboard detail + Croston → **H0** batch the forecast run → **H** Phase 6 below.

- **A** ✅ wider week picker with "Wk 46 · 9–15 Nov"; Clear Override now explains itself (it deletes the row when no active scenario covers it -> 410 -> "now follows the forecast"), amber button, reason optional when clearing; formula text removed; "leaf" -> "customer-product" in UI and API messages.
- **B** ✅ see decision #1.
- **C** ✅ [resolver.py](backend/services/forecasting/resolver.py) solves each week jointly: every scenario is a target on a node (goal from that node's forecast: PERCENT / ABSOLUTE_DELTA / FIXED_QTY, in UNITS or LITRES); nested targets and overrides/locked rows are pinned and the outer node's remainder is shared by forecast, so totals hold at every level; inner > outer = conflict (blocks approval, 409 with `conflicts`); partial overlaps -> newer wins on the shared part. `ScenarioService._rebuild` replaces the per-scenario stacking + `_recompute_keys`: approve / cancel / override all re-solve the affected weeks and write only changed rows. Preview returns before/after per customer-product plus `rebalanced_count` / `rebalanced_delta_qty`. Migration 002 adds `cMeasure` + `cAnchorLevel` to `_tb_SOScenario` (applied). Workbench gains the Category level + a category filter at any level. Tests: `test_plan_resolver.py` (21). Live smoke: `scripts/run_target_nesting_smoke_test.py --dc Makhado --week 2026-11-16` -> PASS (DC 199 909 holds after a customer target and an override inside it; ~15 s per approve for 4 400 customer-products).
  - Behaviour change: two scenarios on the same customer-product no longer multiply/stack -- the inner (more specific) one is pinned and the outer total holds. Guide example 13 rewritten.
  - Seen on live data: a +10% DC target raised 2 859 reasonableness flags out of 4 409 -- one-by-one review won't scale; Phase D should group flags by reason with accept-by-group.
- **D** ✅ [TargetsScreen.tsx](frontend/src/components/TargetsScreen.tsx) replaces Target Workbench + Scenarios (one "Targets" hub card). Drill Company → DC → Route → Customer → Category → Product (category filter at any level skips the Category step); **Target** button on every row and a "Set target for <node>" tile for the node you're in. [SetTargetPanel](frontend/src/components/forecasting/SetTargetPanel.tsx): weekly total (units/litres, follows the screen) or % change, 1–12 weeks, optional name → Preview creates/updates a DRAFT scenario (scope = drill path) → the table gets a **Proposed** column (per-row change from `preview?rollup_level=…`, only that week/view, no customer-product rows shipped) → plain-language summary (node before → after, what other customer-products give up), conflicts block, warnings **grouped by reason** with Accept/Exclude per group + per-product drill-down → Approve (`group_decisions`) or Discard (cancels the draft). Product rows with an existing target open the manual-target dialog. **Changes** drawer: active targets touching the current view, Remove (with reason), Details / Advanced target (the old ScenarioModal, for sales-rep / telesales / multi-customer scopes).
  - Backend: `expand_group_decisions` (own decision wins; any excluded group excludes; accepted only if every group accepted; also applied to warnings that appear after exclusions), preview `rollup_*` fields, approve `group_decisions`.
  - Live API smoke `scripts/run_targets_screen_smoke_test.py` passes preview/approve/cancel; the workbench-total checks drifted while the forecast regeneration was still adding Makhado customer-products -- rerun once it finishes.
  - **Design note for H:** targets are materialised for the customer-products that exist at approval. When a forecast run adds new ones, a node's total only holds if active scenarios are re-solved afterwards -> the nightly job must run `ScenarioService._rebuild` for the live weeks after the forecast.
- **H0** (added 2026-11-06): the forecast run handles one customer-product at a time (4–6 round trips + a commit each, ~0.2 s) -> 38 900 took ~2.5 h and grows linearly with customers and history. Batch it: load history per DC in a few set-based queries, compute in memory, bulk insert. Needed before the nightly schedule.

## Phase 6 — Scheduling + opportunity detection

**Goal**: the loop closes automatically. Nightly job runs the engine + materialises targets + computes scorecards. Hourly job refreshes committed qty. Weekly job generates tasks.

**Planned files**:
- `backend/services/scheduler.py` — APScheduler setup
- Nightly: full forecast run + materialise targets + join actuals + compute scorecard
- Hourly (business hours): update `fCommittedQty` on current-week leafs
- Weekly (Mon 00:00): opportunity detection → `_tb_SOTask` writes
- `backend/services/forecasting/opportunity_detector.py` — the 10 signal rules

---

## File index (as of Phase 5)

```
c:\Dev\salesOrder\
├── FORECASTING_BUILD_PLAN.md           (this file)
├── FORECASTING_WALKTHROUGH.md          Phase 1 scenario walkthrough
├── BACKLOG.md
├── DEPLOYMENT.md
├── README.md
└── backend\
    ├── pytest.ini
    ├── requirements.txt                (added pytest, httpx)
    ├── routers\
    │   └── forecasting.py              (Phase 3 API)
    ├── scripts\
    │   ├── run_forecast_smoke_test.py  (Phase 2b)
    │   ├── run_scenario_smoke_test.py  (Phase 3)
    │   └── run_suggestion_smoke_test.py (Phase 4)
    ├── services\
    │   └── forecasting\
    │       ├── __init__.py
    │       ├── config.py               (2a)
    │       ├── weeks.py                (2a)
    │       ├── classifier.py           (2a)
    │       ├── delivery_pattern.py     (2a)
    │       ├── history_loader.py       (2a)
    │       ├── fallback.py             (2b)
    │       ├── engine.py               (2b)
    │       ├── runner.py               (2b)
    │       ├── reasonableness.py       (3)
    │       ├── scenarios.py            (3)
    │       ├── suggestions.py          (4)
    │       ├── workbench.py            (5)
    │       └── models\
    │           ├── __init__.py
    │           ├── base_median.py           (2a)
    │           └── seasonal_naive_yoy.py    (2b)
    ├── sql\
    │   ├── create_views.sql            (legacy — unchanged by this build)
    │   ├── forecasting_schema.sql      (Phase 1)
    │   ├── forecasting_seed.sql        (Phase 1)
    │   └── forecasting_inspection.sql  (Phase 1)
    └── tests\
        ├── __init__.py
        └── forecasting\
            ├── __init__.py
            ├── test_weeks.py                (2a)
            ├── test_base_median.py          (2a)
            ├── test_classifier.py           (2a)
            ├── test_delivery_pattern.py     (2a)
            ├── test_history_loader.py       (2a)
            ├── test_fallback.py             (2b)
            ├── test_seasonal_naive_yoy.py   (2b)
            ├── test_engine.py               (2b)
            ├── test_reasonableness.py       (3)
            ├── test_scenarios.py            (3)
            ├── test_forecasting_router.py   (3)
            ├── test_suggestions.py          (4)
            ├── test_short_history.py        (pre-5 fixes)
            └── test_workbench.py            (5)
```

---

## Verification snapshot — what should be true right now

**Database** (SOAssistant DB after running Phase 1 SQL):
- 14 new `_tb_SO*` tables exist
- 5 model-registry rows (BASE_MEDIAN_6W enabled, others disabled)
- 5 test scenarios seeded with `TEST_*` prefixed codes
- 11 inspection queries return the expected rows (per walkthrough)

**Python** (after `pip install -r backend/requirements.txt`):
```powershell
cd c:\Dev\salesOrder\backend
.\.venv\Scripts\python.exe -m pytest tests/forecasting -v
# 210 passed in ~2s
```

**Smoke test against real DB** (Phase 2b):
```powershell
cd c:\Dev\salesOrder\backend
.\.venv\Scripts\python.exe scripts\run_forecast_smoke_test.py --limit 20
# Writes 1 _tb_SOForecastRun + up to (20 × 4) _tb_SOForecast rows + classifications + delivery patterns
```

**Not yet wired**:
- No UI changes have shipped (Phase 5)
- Targets only exist once a manager approves a scenario via the API (no management UI until Phase 5)
- No scheduler running — forecasts only get written when `run_forecast_smoke_test.py` is invoked manually (Phase 6)

---

## Rough effort estimate (5h/day)

| Phase | Working days | Calendar (5h/day) |
|---|---|---|
| 3  | 3–4 | 5–7 days |
| 4  | 2–3 | 3–5 days |
| 5  | 4–6 | 7–10 days |
| 6  | 2–3 | 3–5 days |
| **Total remaining** | **11–16 days** | **~2–4 weeks** |

Phase 7 (legacy retire) can slot in opportunistically — not blocking.
