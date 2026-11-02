# Forecasting & Target Management — Phase 1 Walkthrough

This document walks through the Phase 1 schema + test fixtures. Every
number in each scenario is hand-computed so you can verify the database
exactly matches the design.

## Files in this phase

| File | Purpose |
|---|---|
| [backend/sql/forecasting_schema.sql](backend/sql/forecasting_schema.sql) | DDL for 11 new tables + model registry seed. Idempotent. |
| [backend/sql/forecasting_seed.sql](backend/sql/forecasting_seed.sql) | 5 test scenarios with hand-crafted data. Safe to re-run (clears `TEST_*` fixtures first). |
| [backend/sql/forecasting_inspection.sql](backend/sql/forecasting_inspection.sql) | 3 inspection views + 11 ready-to-run SELECT queries. Highlight each `-- QUERY N` block and run. |

## Order to run

```sql
-- In SSMS, against the SOAssistant database:
1.  forecasting_schema.sql       -- creates tables + seeds model registry
2.  forecasting_seed.sql         -- inserts the 5 test scenarios
3.  forecasting_inspection.sql   -- creates views, then run each QUERY block
```

Nothing in Phase 1 touches the backend or frontend. No API changes, no
UI changes. Everything lives in SQL so you can inspect in SSMS and
verify the design is correct before we write a line of Python.

---

## Design decisions locked in

1. **Four separate measurements** — forecast, target, suggestion, actual
   are four distinct columns per leaf. Four distinct error metrics,
   each named for what it actually measures. See `_tb_SOForecastActual`.
2. **Sunday-aligned weeks** — matches `history.py:_week_start_sunday()`
   and `services/targets.py:sunday_of_current_week()`. Every
   `dWeekStart DATE` is a Sunday. Not ISO.
3. **DC from `Client.ulARDistCentre`** — denormalized onto fact rows
   at write time. No separate DC master.
4. **Models never auto-promote** — only `BASE_MEDIAN_6W` is enabled by
   default. Other 4 models register their shape but stay `bEnabled=0`
   until Phase 2.
5. **Fallbacks are first-class** — every forecast carries
   `bIsFallback` + `cFallbackReason`. Scenario C shows the data flow.

---

## Anchor date map

The seed is written against **anchor date Mon 2026-11-02**. All weeks
below are Sunday-starts.

| Label | Sunday start | Purpose |
|---|---|---|
| W-10 | 2026-08-23 | start of 10-week history |
| W-9  | 2026-08-30 | |
| W-8  | 2026-09-06 | |
| W-7  | 2026-09-13 | |
| W-6  | 2026-09-20 | start of base-model 6-week window |
| W-5  | 2026-09-27 | |
| W-4  | 2026-10-04 | |
| W-3  | 2026-10-11 | end of base-model window for the closed scenarios (A, E) |
| **W-2** | **2026-10-18** | **closed week — target locked, actual recorded** |
| W-1  | 2026-10-25 | last completed week before "today" |
| **W0**  | **2026-11-01** | **current week (in progress)** |
| **W+1** | **2026-11-08** | **next week — forecasts materialised, targets live** |

---

## Scenario A — Smooth demand hits target

**The happy path.** Steady weekly orders, a modest `+5%` scenario applied,
rep executes cleanly, actuals land close to target.

### Data

| Entity | Value |
|---|---|
| Customer | `TEST_A_SHOPRITE_DBV` |
| Stock | `TEST_MILK_2L_FC` |
| Route | `TEST_RT_101` |
| DC | `WC` |
| Category | `Milk` |
| Delivery pattern | Mon 40% / Wed 30% / Fri 30% (`HIGH` confidence, 26 weeks sampled) |
| Pattern class | `SMOOTH` (10/10 non-zero, CV² = 0.000281, ADI = 1.0) |

### Historical weekly delivered litres

| Week | W-10 | W-9 | W-8 | W-7 | W-6 | W-5 | W-4 | W-3 |
|---|---|---|---|---|---|---|---|---|
| Litres | 395 | 410 | 400 | 405 | 390 | 410 | 400 | 395 |

### Base model forecast for W-2

Algorithm (from `history.py:order_suggestions`): median of last 6
completed Sunday-weeks, rounded half-up.

```
Window: W-8..W-3 = {400, 405, 390, 410, 400, 395}
Sorted:                 {390, 395, 400, 400, 405, 410}
Median: (400 + 400) / 2 = 400.0
Round half-up (float(int(x + 0.5))): 400
```

**Expected forecast: 400 L**

### Scenario applied

| Field | Value |
|---|---|
| Scenario name | TEST: Q4 Dairy/Juice push +5% |
| Scope | DC=WC, Categories=[Milk, Juice] |
| Lever | PERCENT, 5.00 |
| Period | 2026-10-18 → 2026-12-13 |
| Status | ACTIVE (approved) |

Target materialisation: `fTargetQty = fForecastQty × 1.05 = 420.00`

### Suggestions presented + rep decisions

| Delivery date | Day | Day-share | Suggested | Accepted | Decision |
|---|---|---|---|---|---|
| Mon 2026-10-19 | Mon | 40% | 168.00 | 170.00 | MODIFIED |
| Wed 2026-10-21 | Wed | 30% | 126.00 | 125.00 | MODIFIED |
| Fri 2026-10-23 | Fri | 30% | 126.00 | 115.00 | MODIFIED |
| **Sum** | | **100%** | **420.00** | **410.00** | |

### Actual delivered during W-2: 408 L

### Expected scorecard metrics

```
fForecastError           = 400 - 408   = -8.0
fForecastMAPE            = 8 / 408     = 0.0196   ( 1.96%)
fTargetAchievementPct    = 408 / 420   = 0.9714   (97.14%)
fSuggestedAcceptanceRate = 410 / 420   = 0.9762   (97.62%)
fTargetAggression        = 420 / 400   = 1.0500   (+5.00%)
```

### How to verify

Run **QUERY 3** in `forecasting_inspection.sql`. The row should match
the metrics above.

---

## Scenario B — Stockout masks true demand (correction 3)

The point: **sales ≠ demand** when stock is out. Model trains on
estimated true demand, not clipped actuals.

### Data

| Entity | Value |
|---|---|
| Customer | `TEST_B_SPAR_PNL` |
| Stock | `TEST_YOG_500G` |
| Route | `TEST_RT_102` |
| DC | `WC` |
| Category | `Yoghurt` |

### What happened last week (W-1)

- Rep ordered 150 L (per the model's forecast)
- Supplier delay 2026-10-25 → 2026-10-27 at WC warehouse
- Only 90 L delivered — 60 L shortfall logged with reason `STOCKOUT`

### The actual row for W-1

```
fCommittedQty         = 150
fDeliveredQty         =  90    <- what sales shows
fShortDeliveredQty    =  60
fEstimatedTrueDemand  = 150    <- what the model trains on
bHadStockout          =  1

Error metrics computed against TRUE DEMAND (not delivered):
fForecastError  = 150 - 150 = 0
fForecastMAPE   = 0 / 150   = 0.0000  (0%)
```

If we had computed error against delivered (90): MAPE would have been
40% — the model would get blamed for a supply chain problem.

### What the forecast for W0 shows

Trained on true demand → prediction stays at **150 L**. Not pulled
down by the clipped actual.

### How to verify

Run **QUERY 4** in `forecasting_inspection.sql`.

---

## Scenario C — Intermittent demand, fallback triggered (correction 4)

The point: **not every (customer × SKU) pair has enough history for its
own individual model.** Fall back to a group-level anchor rather than
failing or guessing.

### Data

| Entity | Value |
|---|---|
| Customer | `TEST_C_TUCKSHOP_STE` |
| Stock | `TEST_CHZ_CHE_1KG` |
| Route | `TEST_RT_103` |
| DC | `WC` |
| Category | `Cheese` |

### History (10 weeks observed)

| Week | W-10 | W-9 | W-8 | W-7 | W-6 | W-5 | W-4 | W-3 | W-2 | W-1 |
|---|---|---|---|---|---|---|---|---|---|---|
| Litres | 0 | 120 | 0 | 0 | 0 | 100 | 0 | 0 | 0 | 0 |

### Classification

```
Non-zero weeks: 2 (W-9=120, W-5=100)
Total observed: 10
ADI = 10 / 2 = 5.0           (>> 1.32 threshold)
CV² of {120, 100}: μ=110, σ²=100, CV² = 100/110² ≈ 0.00826

Pattern: INTERMITTENT     (because ADI ≥ 1.32)
```

### Fallback logic

```
iNonZeroWeeks (2) < iMinRequired (12) → fallback required.
Recommended model: CROSTON_SBA (intermittent pattern).
But bEnabled = 0 on that model in Phase 1 → engine uses the
category-level fallback source.

Fallback source: 25 L (small-customer bucket, Cheese category average)
```

### Expected forecast row

```
fPredictedQty      = 25.00
bIsFallback        = 1
cFallbackReason    = 'SPARSE_HISTORY'
fFallbackSource    = 25.00
cConfidenceGrade   = 'D'
```

### How to verify

Run **QUERY 5** in `forecasting_inspection.sql`.

---

## Scenario D — Reasonableness flag triggered

The point: a management scenario cannot be approved **until each
flagged leaf is individually reviewed**. The scenario sits in `PREVIEW`
status, the flagged target shows up with reasons, and a task is
auto-generated for the manager.

### Data

| Entity | Value |
|---|---|
| Customer | `TEST_D_CAFE_ARDERNE` |
| Stock | `TEST_YOG_1L_SBE` (new product, launched 2026-07-01) |
| Route | `TEST_RT_101` |
| DC | `WC` |
| Category | `Yoghurt` |

### History (9 non-zero weeks since launch)

| Week | W-9 | W-8 | W-7 | W-6 | W-5 | W-4 | W-3 | W-2 | W-1 |
|---|---|---|---|---|---|---|---|---|---|
| Litres | 30 | 35 | 40 | 50 | 55 | 55 | 60 | 58 | 60 |

### Base forecast for W+1

```
Window: W-6..W-1 = {50, 55, 55, 60, 58, 60}
Sorted:            {50, 55, 55, 58, 60, 60}
Median: (55 + 58) / 2 = 56.5
Round half-up: 57
```

### Scenario applied

| Field | Value |
|---|---|
| Scenario name | TEST: Strawberry Yoghurt launch push +50% |
| Scope | DC=WC, Category=Yoghurt, SKU=TEST_YOG_1L_SBE |
| Lever | PERCENT, 50.00 |
| Period | 2026-11-08 → 2026-12-20 |
| Status | **PREVIEW** (not approved yet) |

### Target cascade

```
57 × 1.50 = 85.5

Reasonableness checks against the leaf:
  - P95 of trailing 52w (only 9 non-zero so effective max ≈ 60):
      85.5 > 60  →  flag 'above_historical_p95'
  - SKU launched < 52 weeks ago, no prior-year baseline:
      → flag 'new_product_requires_review'
```

### Expected materialised target row

```
fForecastQty       = 57.00
fTargetQty         = 85.50
cSource            = 'SCENARIO'
bIsFlagged         = 1
cFlagReasonsJson   = '["above_historical_p95","new_product_requires_review"]'
bIsLocked          = 0  (week hasn't started)
```

### Auto-generated task

```
cTaskType = 'REVIEW_FLAGGED_TARGET'
cPriority = 'HIGH'
cAssignedTo = test_mgr_national
cTitle = 'Review flagged target: Cafe Arderne × Yoghurt 1L Strawberry'
```

### How to verify

Run **QUERY 6** in `forecasting_inspection.sql`.

---

## Scenario E — Rep execution gap (correction 2)

The point: when the week misses target, **the four error signals tell
different stories**. A single collapsed MAPE would hide which link in
the chain actually failed. Here it's execution, not the model and not
the lever.

### Data

| Entity | Value |
|---|---|
| Customer | `TEST_E_SPAR_WDSTK` |
| Stock | `TEST_JCE_1L_OJ` |
| Route | `TEST_RT_101` |
| DC | `WC` |
| Category | `Juice` |

### Forecast + target for W-2

```
fForecastQty = 400  (base model on steady 400L history)
fTargetQty   = 420  (Q4 Dairy/Juice +5% scenario)
```

### Suggestions presented, rep systematically underordered

| Delivery date | Suggested | Accepted | Decision |
|---|---|---|---|
| Mon 2026-10-19 | 168.00 | 120.00 | MODIFIED |
| Wed 2026-10-21 | 126.00 |  90.00 | MODIFIED |
| Fri 2026-10-23 | 126.00 |  90.00 | MODIFIED |
| **Sum** | **420.00** | **300.00** | |

### Delivered during W-2: 300 L (matches accepted — no stockout)

### Four error signals, four stories

```
fForecastError           = 400 - 300    =  100         ← looks bad
fForecastMAPE            = 100 / 300    =  0.3333  (33.33%)  ← looks bad
fTargetAchievementPct    = 300 / 420    =  0.7143  (71.43%)  ← looks bad
fSuggestedAcceptanceRate = 300 / 420    =  0.7143  (71.43%)  ← the diagnostic:
                                                               rep accepted
                                                               only 71% of
                                                               what we asked
fTargetAggression        = 420 / 400    =  1.0500  (+5.00%)  ← lever was modest
```

### The diagnosis

- Forecast MAPE 33% looks like the model is broken.
- Target achievement 71% looks like we missed strategic goals.
- **But** suggested-acceptance 71% matches target-achievement 71%
  exactly — meaning the rep is the bottleneck.
- Target aggression at only 5% rules out "the ask was unreasonable".

→ A single "accuracy" number would have blamed the model. The four
separate signals correctly isolate the problem as execution.

### Auto-generated task

```
cTaskType = 'VISIT_CUSTOMER'
cPriority = 'MED'
cAssignedTo = test_rep_sarah
cDescription = "Last week's suggestion-acceptance was 71% ..."
```

### How to verify

Run **QUERY 7** in `forecasting_inspection.sql`.

---

## Roll-up verification (QUERY 8)

For closed week W-2 (`2026-10-18`), you should see this pyramid:

```
LEAF (2 rows)
  TEST_A × MILK   forecast 400  target 420  delivered 408
  TEST_E × JUICE  forecast 400  target 420  delivered 300

CUSTOMER_CATEGORY (2 rows)
  TEST_A × Milk   400 / 420 / 408
  TEST_E × Juice  400 / 420 / 300

ROUTE_CATEGORY (2 rows)
  RT_101 × Milk   400 / 420 / 408
  RT_101 × Juice  400 / 420 / 300

DC_CATEGORY (2 rows)
  WC × Milk       400 / 420 / 408
  WC × Juice      400 / 420 / 300

DC_TOTAL (1 row)
  WC              800 / 840 / 708

COMPANY (1 row)
  —               800 / 840 / 708
```

Every roll-up is a plain `SUM()` of its leaves. No weighting at
aggregation time — weighting applies only to the accuracy metrics
(MAPE is `SUM(abs_err) / SUM(true_demand)`, not an average of leaf
MAPEs).

---

## Model leaderboard (QUERY 9)

Three scored leaves (A, B, E — all three have an actual row with a
model_id attributed). B's stockout actual contributes a zero error
(the model was correct against true demand), so it dilutes the
overall MAPE a touch:

```
BASE_MEDIAN_6W
  iLeafsScored   = 3
  fWeightedMAPE  = (|-8| + |0| + |100|) / (408 + 150 + 300)
                 = 108 / 858
                 = 0.1259   (12.59%)
  fMAE           = AVG(8, 0, 100)
                 = 36.0
  fBias          = (-8 + 0 + 100) / 858
                 = 92 / 858
                 = 0.1072   (10.72% over-forecast on average,
                             driven by scenario E's execution gap)
```

Note how Scenario B being in the leaderboard with 0 error demonstrates
the correction-3 payoff: the model doesn't get blamed for the
stockout. If error were computed against delivered (90) instead of
true demand (150), B would have contributed +60 to the error numerator
and the model's MAPE would unfairly climb to 19.6%.

In Phase 2, when Model B (seasonal naive + YoY) and later models get
turned on, they'll each produce their own row and the UI leaderboard
starts to make real decisions.

---

## What's NOT in Phase 1

Explicitly out of scope to keep this phase small enough to review:

- No Python engine — no forecasts are *computed*, they're hand-seeded
- No API routes — nothing wired into the FastAPI app
- No UI — no `TargetWorkbenchScreen`, no scenario modal
- No scheduler — no nightly / hourly jobs
- No migration from the existing `_tb_SOTargets` — that table still
  exists untouched and its UI (`PerformanceScreen`, `TargetFormModal`,
  `PerformanceStrip`) is still live

Phase 2 adds the Python forecasting engine that produces these rows
for real customers / SKUs. Phase 3 adds the scenario cascade service
and API routes. Phase 4 wires it into the rep screen. Phase 5 is the
management UI. Phase 6 is scheduling.

---

## Open decisions to lock before Phase 2

1. **Is the Sunday-week convention OK?** Confirmed in the explore —
   existing codebase uses Sunday-aligned weeks everywhere. I've matched
   that. (Alternative: ISO weeks starting Monday.)
2. **Does `_tb_SOTargets` get retired at some point?** Currently the
   new system is additive. You said we can scratch the old one — happy
   to plan the migration + decommission as a Phase 7, but want to
   confirm it's not needed sooner.
3. **Fallback source lookup** — in Scenario C, the fallback is set to
   25 L by hand. The real engine needs a well-defined rule for picking
   the fallback (category average, peer-customer average, nearest-
   neighbour, etc.). Default proposal: "category-level average scaled
   by this customer's overall purchase magnitude vs peers". Need your
   sign-off on this before Phase 2.
4. **Pattern thresholds** — I've used standard Syntetos-Boylan cuts
   (ADI 1.32, CV² 0.49) and 12 non-zero weeks as the min-for-individual
   fit. These are starting points; expect to tune after you see them
   run against real data.
5. **Reasonableness check thresholds** — I used:
   - P95 of trailing 52 weeks
   - 25% implied YoY growth
   - New-product review for < 52 weeks since launch
   - Pack-size minimum = 1 pack
   Each is a tunable; happy to adjust.
