-- =============================================================================
-- forecasting_seed.sql
--
-- Test fixtures for the Demand Forecasting module. Five scenarios, each
-- designed to exercise a specific feature of the design with a hand-
-- computed expected answer. All customer / stock codes use TEST_* prefix
-- so they do not collide with Evolution master data.
--
-- Anchor date: this seed assumes "today" is Mon 2026-11-02. Current
-- Sunday-week start = 2026-11-01. Historical weeks run back from there.
-- If you run this later, dates are LITERAL and will shift relative to
-- the real current date -- see FORECASTING_WALKTHROUGH.md for the full
-- date map and expected outputs.
--
-- Safe to re-run: deletes all TEST_* fixtures before inserting.
-- Does NOT touch Evolution tables.
-- =============================================================================

USE [SOAssistant];
GO

PRINT 'Clearing existing TEST_* fixtures...';
GO

-- Delete order matters: fact tables first, then dimensions.
-- Scorecard first because it has no FK to anything referenced by the others.
DELETE FROM [SOAssistant].dbo._tb_SOForecastScorecard
 WHERE cCustomerAccount LIKE 'TEST_%'
    OR cRoute LIKE 'TEST_%';
DELETE FROM [SOAssistant].dbo._tb_SOForecastActual
 WHERE cCustomerAccount LIKE 'TEST_%';
DELETE FROM [SOAssistant].dbo._tb_SOOrderSuggestion
 WHERE cCustomerAccount LIKE 'TEST_%';
DELETE FROM [SOAssistant].dbo._tb_SOScenarioTarget
 WHERE cCustomerAccount LIKE 'TEST_%';
DELETE FROM [SOAssistant].dbo._tb_SOForecast
 WHERE cCustomerAccount LIKE 'TEST_%';
DELETE FROM [SOAssistant].dbo._tb_SOShortDelivery
 WHERE cCustomerAccount LIKE 'TEST_%';
DELETE FROM [SOAssistant].dbo._tb_SOStockoutEvent
 WHERE cStockCode LIKE 'TEST_%';
DELETE FROM [SOAssistant].dbo._tb_SODemandClassification
 WHERE cCustomerAccount LIKE 'TEST_%';
DELETE FROM [SOAssistant].dbo._tb_SODeliveryPattern
 WHERE cCustomerAccount LIKE 'TEST_%';
DELETE FROM [SOAssistant].dbo._tb_SOTask
 WHERE cCustomerAccount LIKE 'TEST_%';
DELETE FROM [SOAssistant].dbo._tb_SOForecastAudit
 WHERE cNewStateJson LIKE '%"TEST_%';
-- Delete the test scenarios + forecast runs after the fact tables that
-- referenced them (via scenario id / run id -- not FK but logically owned).
DELETE FROM [SOAssistant].dbo._tb_SOScenario
 WHERE cName LIKE 'TEST:%';
DELETE FROM [SOAssistant].dbo._tb_SOForecastRun
 WHERE cNotes LIKE 'TEST seed%';
GO

-- =============================================================================
-- Shared references
--
-- Anchor week starts (Sunday-aligned):
--   W-10 = 2026-08-23
--   W-9  = 2026-08-30
--   W-8  = 2026-09-06
--   W-7  = 2026-09-13
--   W-6  = 2026-09-20
--   W-5  = 2026-09-27
--   W-4  = 2026-10-04
--   W-3  = 2026-10-11
--   W-2  = 2026-10-18   <- "last completed week" where closed-loop scenarios live
--   W-1  = 2026-10-25
--   W0   = 2026-11-01   <- "current week" (in progress)
--   W+1  = 2026-11-08   <- "next week" (forecast target for live scenarios)
-- =============================================================================

DECLARE @model_base      INT = (SELECT idForecastModel
                                  FROM [SOAssistant].dbo._tb_SOForecastModel
                                 WHERE cCode = 'BASE_MEDIAN_6W');
DECLARE @model_croston   INT = (SELECT idForecastModel
                                  FROM [SOAssistant].dbo._tb_SOForecastModel
                                 WHERE cCode = 'CROSTON_SBA');

-- One forecast run per model for the horizon that covers all scenarios.
-- Horizon W-2 .. W+4 so the closed scenarios (W-2) and the live ones (W0, W+1)
-- are all in the same run for simplicity.
INSERT INTO [SOAssistant].dbo._tb_SOForecastRun
    (idForecastModel, dtRunStartedAt, dtRunCompletedAt, cStatus,
     dHorizonFrom, dHorizonTo, iPredictionsWritten, cNotes, cCreatedBy)
VALUES
    (@model_base, '2026-10-17 23:00:00', '2026-10-17 23:04:12', 'SUCCESS',
     '2026-10-18', '2026-12-13', 5, 'TEST seed (base model)', 'test_seed'),
    (@model_croston, '2026-10-17 23:04:12', '2026-10-17 23:05:00', 'SUCCESS',
     '2026-10-18', '2026-12-13', 1, 'TEST seed (Croston)', 'test_seed');

DECLARE @run_base    BIGINT = (SELECT TOP 1 idForecastRun
                                FROM [SOAssistant].dbo._tb_SOForecastRun
                               WHERE cNotes = 'TEST seed (base model)'
                               ORDER BY idForecastRun DESC);
DECLARE @run_croston BIGINT = (SELECT TOP 1 idForecastRun
                                FROM [SOAssistant].dbo._tb_SOForecastRun
                               WHERE cNotes = 'TEST seed (Croston)'
                               ORDER BY idForecastRun DESC);


-- =============================================================================
-- SCENARIO A -- Smooth demand, scenario applied, hits target
--
-- Customer: TEST_A_SHOPRITE_DBV  (Shoprite Durbanville analogue)
-- Stock:    TEST_MILK_2L_FC      (Milk 2L Full Cream)
-- Route:    TEST_RT_101   DC: WC   Category: Milk
--
-- History (10 weeks of weekly actuals, delivered = true demand):
--   W-10:395  W-9:410  W-8:400  W-7:405  W-6:390  W-5:410
--   W-4:400  W-3:395  W-2: closed week (target+actual below)  W-1:400
--
-- Base model forecast for W-2 using last 6 completed weeks W-8..W-3:
--   {400, 405, 390, 410, 400, 395}
--   sorted {390, 395, 400, 400, 405, 410}
--   median = (400 + 400) / 2 = 400.0
--   round half-up (algo: float(int(x + 0.5))) -> 400
--
-- Scenario applied: "+5% Milk at DC-WC" over 2026-10-18..2026-12-13
--   Target W-2 = 400 * 1.05 = 420
--
-- Deliveries during W-2: 408 L (across 3 delivery days per customer's
-- learned Mon/Wed/Fri = 40% / 30% / 30% pattern).
--
-- Expected scorecard for W-2:
--   fForecastQty = 400, fTargetQty = 420, fDeliveredQty = 408
--   fForecastError = 400 - 408 = -8  (model slightly under-forecast)
--   fForecastMAPE = 8/408 = 0.0196  (1.96%)
--   fTargetAchievementPct = 408/420 = 0.9714  (97.14%)
--   fSuggestedAcceptanceRate = 410/420 = 0.9762  (97.62%)
--   fTargetAggression = 420/400 = 1.0500  (105.00%)
-- =============================================================================

-- Delivery pattern (learned from history -- shown here as fixture).
INSERT INTO [SOAssistant].dbo._tb_SODeliveryPattern
    (cCustomerAccount, fMonShare, fTueShare, fWedShare, fThuShare, fFriShare,
     fSatShare, fSunShare, iWeeksSampled, cConfidence, dtComputedAt)
VALUES
    ('TEST_A_SHOPRITE_DBV', 0.4000, 0.0000, 0.3000, 0.0000, 0.3000, 0.0000, 0.0000,
     26, 'HIGH', '2026-10-17 23:00:00');

-- Demand classification (SMOOTH -- 10/10 non-zero, low CV^2).
INSERT INTO [SOAssistant].dbo._tb_SODemandClassification
    (cCustomerAccount, cStockCode, fCVSquared, fADI, cPattern,
     iNonZeroWeeks, iTotalWeeksObserved, iMinRequired,
     cRecommendedModel, cFallbackReason, dtClassifiedAt)
VALUES
    ('TEST_A_SHOPRITE_DBV', 'TEST_MILK_2L_FC',
     0.000281, 1.0000, 'SMOOTH', 10, 10, 12,
     'BASE_MEDIAN_6W', NULL, '2026-10-17 23:00:00');

-- Forecast for W-2 (the closed week).
INSERT INTO [SOAssistant].dbo._tb_SOForecast
    (idForecastRun, idForecastModel, cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     fPredictedQty, fPILower, fPIUpper, cDemandPattern, cConfidenceGrade,
     bIsFallback, cFallbackReason, bIsActive, dtCreatedAt)
VALUES
    (@run_base, @model_base, 'TEST_A_SHOPRITE_DBV', 'TEST_MILK_2L_FC', '2026-10-18',
     'TEST_RT_101', 'WC', 'Milk',
     400.0000, 380.0000, 420.0000, 'SMOOTH', 'A',
     0, NULL, 1, '2026-10-17 23:00:00');

-- Forecast for W+1 (the live upcoming week) -- also fed by this run.
INSERT INTO [SOAssistant].dbo._tb_SOForecast
    (idForecastRun, idForecastModel, cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     fPredictedQty, fPILower, fPIUpper, cDemandPattern, cConfidenceGrade,
     bIsFallback, cFallbackReason, bIsActive, dtCreatedAt)
VALUES
    (@run_base, @model_base, 'TEST_A_SHOPRITE_DBV', 'TEST_MILK_2L_FC', '2026-11-08',
     'TEST_RT_101', 'WC', 'Milk',
     400.0000, 380.0000, 420.0000, 'SMOOTH', 'A',
     0, NULL, 1, '2026-10-17 23:00:00');


-- =============================================================================
-- SCENARIO B -- Stockout masks true demand
--
-- Customer: TEST_B_SPAR_PNL      (Spar Paarl)
-- Stock:    TEST_YOG_500G        (Yoghurt 500g)
-- Route:    TEST_RT_102   DC: WC   Category: Yoghurt
--
-- History: steady 150 L/wk for weeks W-10..W-2, except W-1 where stock
-- was out and only 90 L delivered against a 150 L order.
--
-- Stockout event: 2026-10-25 to 2026-10-27 at WC warehouse.
-- Short delivery recorded: ordered 150, delivered 90, shortfall 60.
--
-- Forecast engine behaviour:
--   If training on DELIVERED (90 for W-1): base model median would be
--     median(150,150,148,152,150,90) = (150+150)/2 = 150 -- robust to
--     a single outlier, but subsequent stockouts would tank it.
--   If training on TRUE DEMAND (150): base model median = 150, clean.
--
-- Expected forecast for W0 (current week): 150 L (correct, trained on
-- true demand).
--
-- No scenario applied here. Target = forecast = 150. W0 is in progress
-- so no actuals yet.
-- =============================================================================

INSERT INTO [SOAssistant].dbo._tb_SODeliveryPattern
    (cCustomerAccount, fMonShare, fTueShare, fWedShare, fThuShare, fFriShare,
     fSatShare, fSunShare, iWeeksSampled, cConfidence, dtComputedAt)
VALUES
    ('TEST_B_SPAR_PNL', 0.0000, 0.5000, 0.0000, 0.5000, 0.0000, 0.0000, 0.0000,
     18, 'MED', '2026-10-31 23:00:00');

INSERT INTO [SOAssistant].dbo._tb_SODemandClassification
    (cCustomerAccount, cStockCode, fCVSquared, fADI, cPattern,
     iNonZeroWeeks, iTotalWeeksObserved, iMinRequired,
     cRecommendedModel, cFallbackReason, dtClassifiedAt)
VALUES
    ('TEST_B_SPAR_PNL', 'TEST_YOG_500G',
     0.000800, 1.0000, 'SMOOTH', 10, 10, 12,
     'BASE_MEDIAN_6W', NULL, '2026-10-31 23:00:00');

-- Stockout event last week.
INSERT INTO [SOAssistant].dbo._tb_SOStockoutEvent
    (cStockCode, cWarehouseCode, dStartDate, dEndDate, cNotes, cRecordedBy)
VALUES
    ('TEST_YOG_500G', 'WC', '2026-10-25', '2026-10-27',
     'Supplier delay -- 3 day stockout clipped Mon+Tue deliveries', 'test_seed');

-- Short delivery captured at delivery time.
INSERT INTO [SOAssistant].dbo._tb_SOShortDelivery
    (cCustomerAccount, cStockCode, cOrderNum, dDeliveryDate,
     fOrderedQty, fDeliveredQty, fShortfallQty, cReason, cNotes, cRecordedBy)
VALUES
    ('TEST_B_SPAR_PNL', 'TEST_YOG_500G', 'TEST_SO_B_W1', '2026-10-27',
     150.0000, 90.0000, 60.0000, 'STOCKOUT',
     'Driver confirmed 60 units short on arrival', 'test_seed');

-- Forecast for W0 (current week) -- trained on true demand so forecast
-- stays at 150 despite the clipped actual.
INSERT INTO [SOAssistant].dbo._tb_SOForecast
    (idForecastRun, idForecastModel, cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     fPredictedQty, fPILower, fPIUpper, cDemandPattern, cConfidenceGrade,
     bIsFallback, cFallbackReason, bIsActive, dtCreatedAt)
VALUES
    (@run_base, @model_base, 'TEST_B_SPAR_PNL', 'TEST_YOG_500G', '2026-11-01',
     'TEST_RT_102', 'WC', 'Yoghurt',
     150.0000, 142.0000, 158.0000, 'SMOOTH', 'A',
     0, NULL, 1, '2026-10-31 23:00:00');

-- Actual row for the closed W-1 week showing the delivered vs true-demand split.
INSERT INTO [SOAssistant].dbo._tb_SOForecastActual
    (cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     fForecastQty, idForecastModel, fTargetQty,
     fSuggestedQtySum, fAcceptedQtySum, fCommittedQty, fDeliveredQty,
     fShortDeliveredQty, fEstimatedTrueDemand, bHadStockout,
     fForecastError, fForecastMAPE,
     fTargetAchievementPct, fSuggestedAcceptanceRate, fTargetAggression,
     dtClosedAt, dtCreatedAt)
VALUES
    ('TEST_B_SPAR_PNL', 'TEST_YOG_500G', '2026-10-25',
     'TEST_RT_102', 'WC', 'Yoghurt',
     150.0000, @model_base, 150.0000,
     150.0000, 150.0000, 150.0000, 90.0000,
     60.0000, 150.0000, 1,
     -- Metrics are computed vs TRUE DEMAND (150) not delivered (90) --
     -- stockout clipped sales, model wasn't wrong.
     0.0000, 0.0000,
     1.0000, 1.0000, 1.0000,
     '2026-11-01 00:05:00', '2026-11-01 00:05:00');


-- =============================================================================
-- SCENARIO C -- Intermittent demand, fallback triggered
--
-- Customer: TEST_C_TUCKSHOP_STE  (Tuckshop Stellenbosch)
-- Stock:    TEST_CHZ_CHE_1KG     (Cheddar Cheese 1kg)
-- Route:    TEST_RT_103   DC: WC   Category: Cheese
--
-- History: 10 weeks observed, only 2 non-zero (W-9 = 120, W-5 = 100).
-- ADI = 10 / 2 = 5.0 (>> 1.32 threshold) -> INTERMITTENT.
-- Non-zero weeks (2) is well below iMinRequired (12) -> fallback.
--
-- Base model requires >= 3 non-zero weeks and gets only 1 in the last 6
-- (W-5=100) -> it returns NO prediction. Croston_SBA model owns this
-- pattern but is not shipped yet (bEnabled=0 in registry) -> engine
-- flags fallback.
--
-- Fallback source: category mean across all customers with cheese in
-- a comparable size band. Say the fallback resolver returns 25 L (small
-- customer bucket, cheese category).
--
-- Expected forecast for W+1:
--   bIsFallback = 1
--   cFallbackReason = 'SPARSE_HISTORY'
--   fFallbackSource = 25.0 (the group-level anchor)
--   fPredictedQty = 25.0
--   cConfidenceGrade = 'D' (low confidence by design when fallback used)
--
-- No scenario, no actual yet.
-- =============================================================================

INSERT INTO [SOAssistant].dbo._tb_SODemandClassification
    (cCustomerAccount, cStockCode, fCVSquared, fADI, cPattern,
     iNonZeroWeeks, iTotalWeeksObserved, iMinRequired,
     cRecommendedModel, cFallbackReason, dtClassifiedAt)
VALUES
    ('TEST_C_TUCKSHOP_STE', 'TEST_CHZ_CHE_1KG',
     0.008264, 5.0000, 'INTERMITTENT', 2, 10, 12,
     'CROSTON_SBA', 'SPARSE_HISTORY', '2026-10-31 23:00:00');

-- Forecast with fallback flag.
INSERT INTO [SOAssistant].dbo._tb_SOForecast
    (idForecastRun, idForecastModel, cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     fPredictedQty, fPILower, fPIUpper, cDemandPattern, cConfidenceGrade,
     bIsFallback, cFallbackReason, fFallbackSource, bIsActive, dtCreatedAt)
VALUES
    (@run_croston, @model_croston, 'TEST_C_TUCKSHOP_STE', 'TEST_CHZ_CHE_1KG',
     '2026-11-08',
     'TEST_RT_103', 'WC', 'Cheese',
     25.0000, 10.0000, 60.0000, 'INTERMITTENT', 'D',
     1, 'SPARSE_HISTORY', 25.0000, 1, '2026-10-31 23:00:00');


-- =============================================================================
-- SCENARIO D -- Reasonableness flag triggered during scenario cascade
--
-- Customer: TEST_D_CAFE_ARDERNE  (Cafe Arderne)
-- Stock:    TEST_YOG_1L_SBE      (Yoghurt 1L Strawberry -- NEW product
--                                 launched 2026-07-01)
-- Route:    TEST_RT_101   DC: WC   Category: Yoghurt   Pack: 6
--
-- History: product launched 2026-07-01, first sales late Jul.
--   W-10..W-9 pre-launch: 0 (not sold)
--   W-9 to W-1 (ramp):
--     W-9: 30  W-8: 35  W-7: 40  W-6: 50  W-5: 55
--     W-4: 55  W-3: 60  W-2: 58  W-1: 60
--
-- Base model forecast for W+1 using W-6..W-1:
--   {50, 55, 55, 60, 58, 60}
--   sorted {50, 55, 55, 58, 60, 60}
--   median = (55 + 58) / 2 = 56.5
--   round half-up -> 57
--
-- Scenario applied: "+50% new product push" at Yoghurt category --
-- aggressive on purpose.
--   Target W+1 = 57 * 1.50 = 85.5
--
-- Reasonableness checks:
--   P95 of trailing 52 weeks (only 9 non-zero so trailing stat is weak;
--   effective max = 60) -> target 85.5 > 60 -> flag `above_historical_p95`
--   New product (<= 52 weeks since launch) with no prior-year data ->
--   flag `new_product_requires_review`
--
-- Scenario stays in PREVIEW status (not approved) until each flagged
-- leaf is individually reviewed. Target row materialises with
-- bIsFlagged=1 so the manager sees it in the preview panel.
-- =============================================================================

INSERT INTO [SOAssistant].dbo._tb_SODemandClassification
    (cCustomerAccount, cStockCode, fCVSquared, fADI, cPattern,
     iNonZeroWeeks, iTotalWeeksObserved, iMinRequired,
     cRecommendedModel, cFallbackReason, dtClassifiedAt)
VALUES
    ('TEST_D_CAFE_ARDERNE', 'TEST_YOG_1L_SBE',
     0.055000, 1.1111, 'SMOOTH', 9, 10, 12,
     'BASE_MEDIAN_6W', 'NEW_SKU', '2026-10-31 23:00:00');

-- Forecast for W+1 (new product with limited history -- note fallback
-- reason = NEW_SKU even though bIsFallback=0, because the base model
-- CAN still fit on 6 recent weeks; the NEW_SKU reason flags "treat
-- confidence with caution").
INSERT INTO [SOAssistant].dbo._tb_SOForecast
    (idForecastRun, idForecastModel, cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     fPredictedQty, fPILower, fPIUpper, cDemandPattern, cConfidenceGrade,
     bIsFallback, cFallbackReason, bIsActive, dtCreatedAt)
VALUES
    (@run_base, @model_base, 'TEST_D_CAFE_ARDERNE', 'TEST_YOG_1L_SBE', '2026-11-08',
     'TEST_RT_101', 'WC', 'Yoghurt',
     57.0000, 48.0000, 66.0000, 'SMOOTH', 'C',
     0, 'NEW_SKU', 1, '2026-10-31 23:00:00');


-- =============================================================================
-- SCENARIO E -- Rep modified the suggestion
--
-- Customer: TEST_E_SPAR_WDSTK
-- Stock:    TEST_JCE_1L_OJ        (Juice 1L Orange)
-- Route:    TEST_RT_101   DC: WC   Category: Juice   Pack: 6
--
-- History: steady 400 L/wk.
--   W-10: 395  W-9: 405  W-8: 400  W-7: 400  W-6: 395  W-5: 410
--   W-4:  400  W-3: 405  W-2: closed week  W-1: 395
--
-- Base model forecast for W-2 using W-8..W-3: median = 400.
-- Scenario applied: "+5% Juice" -> target W-2 = 420.
--
-- Suggestions presented during W-2 (Fri 2026-10-16 prep for the Mon
-- delivery, etc.). Rep systematically modified every line downward --
-- a clear execution gap, NOT a model problem.
--   Mon 2026-10-19: suggested 168, accepted 120 (MODIFIED)
--   Wed 2026-10-21: suggested 126, accepted  90 (MODIFIED)
--   Fri 2026-10-23: suggested 126, accepted  90 (MODIFIED)
--   Suggested sum = 420, accepted sum = 300.
--
-- Deliveries during W-2: 300 L (what the rep ordered got delivered).
--
-- Expected scorecard for W-2 -- illustrates CORRECTION 2 (different
-- error signals tell different stories):
--   fForecastQty = 400, fTargetQty = 420, fDeliveredQty = 300
--   fForecastError = 400 - 300 = +100
--   fForecastMAPE = 100 / 300 = 33.33%     -- looks BAD
--   fTargetAchievementPct = 300 / 420 = 71.43%   -- looks BAD
--   fSuggestedAcceptanceRate = 300 / 420 = 71.43%  -- the diagnostic:
--     rep accepted only 71% of what we asked, so the gap is execution,
--     not the model.
--   fTargetAggression = 420 / 400 = 105.00%  -- lever was reasonable
-- =============================================================================

INSERT INTO [SOAssistant].dbo._tb_SODeliveryPattern
    (cCustomerAccount, fMonShare, fTueShare, fWedShare, fThuShare, fFriShare,
     fSatShare, fSunShare, iWeeksSampled, cConfidence, dtComputedAt)
VALUES
    ('TEST_E_SPAR_WDSTK', 0.4000, 0.0000, 0.3000, 0.0000, 0.3000, 0.0000, 0.0000,
     24, 'HIGH', '2026-10-17 23:00:00');

INSERT INTO [SOAssistant].dbo._tb_SODemandClassification
    (cCustomerAccount, cStockCode, fCVSquared, fADI, cPattern,
     iNonZeroWeeks, iTotalWeeksObserved, iMinRequired,
     cRecommendedModel, cFallbackReason, dtClassifiedAt)
VALUES
    ('TEST_E_SPAR_WDSTK', 'TEST_JCE_1L_OJ',
     0.000225, 1.0000, 'SMOOTH', 10, 10, 12,
     'BASE_MEDIAN_6W', NULL, '2026-10-17 23:00:00');

INSERT INTO [SOAssistant].dbo._tb_SOForecast
    (idForecastRun, idForecastModel, cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     fPredictedQty, fPILower, fPIUpper, cDemandPattern, cConfidenceGrade,
     bIsFallback, cFallbackReason, bIsActive, dtCreatedAt)
VALUES
    (@run_base, @model_base, 'TEST_E_SPAR_WDSTK', 'TEST_JCE_1L_OJ', '2026-10-18',
     'TEST_RT_101', 'WC', 'Juice',
     400.0000, 380.0000, 420.0000, 'SMOOTH', 'A',
     0, NULL, 1, '2026-10-17 23:00:00');


-- =============================================================================
-- Scenarios (management levers) referenced by scenarios A, D, E above.
-- =============================================================================

-- Scenario A/E: "+5% Milk/Juice at DC-WC" -- active (approved).
-- Simplified: one scenario covering both categories (so both scenarios A
-- and E draw their lever from the same scenario id for inspection).
INSERT INTO [SOAssistant].dbo._tb_SOScenario
    (cName, cDescription, cScopeJson, cLeverType, fLeverValue,
     dPeriodFrom, dPeriodTo, cStatus,
     cCreatedBy, dtCreatedAt, cApprovedBy, dtApprovedAt)
VALUES
    ('TEST: Q4 Dairy/Juice push +5%',
     'Lift Milk + Juice at DC-WC by 5% over 2026-W43 to 2026-W50',
     '{"distCentre":["WC"],"category":["Milk","Juice"]}',
     'PERCENT', 5.0000,
     '2026-10-18', '2026-12-13', 'ACTIVE',
     'test_mgr_national', '2026-10-15 10:00:00',
     'test_mgr_national', '2026-10-15 14:30:00');

DECLARE @scenario_milkjuice INT = (SELECT TOP 1 idScenario
                                     FROM [SOAssistant].dbo._tb_SOScenario
                                    WHERE cName = 'TEST: Q4 Dairy/Juice push +5%'
                                    ORDER BY idScenario DESC);

-- Scenario D: "+50% new product push" -- in PREVIEW, flag triggered.
INSERT INTO [SOAssistant].dbo._tb_SOScenario
    (cName, cDescription, cScopeJson, cLeverType, fLeverValue,
     dPeriodFrom, dPeriodTo, cStatus,
     cCreatedBy, dtCreatedAt)
VALUES
    ('TEST: Strawberry Yoghurt launch push +50%',
     'Aggressive push on new 1L strawberry yoghurt. Scope: Yoghurt category '
     + 'at DC-WC, 6 weeks from launch anniversary.',
     '{"distCentre":["WC"],"category":["Yoghurt"],"sku":["TEST_YOG_1L_SBE"]}',
     'PERCENT', 50.0000,
     '2026-11-08', '2026-12-20', 'PREVIEW',
     'test_mgr_national', '2026-11-01 09:00:00');

DECLARE @scenario_yog INT = (SELECT TOP 1 idScenario
                               FROM [SOAssistant].dbo._tb_SOScenario
                              WHERE cName = 'TEST: Strawberry Yoghurt launch push +50%'
                              ORDER BY idScenario DESC);


-- =============================================================================
-- Materialised ScenarioTargets (one row per leaf after cascade+flag).
-- Only populated for the weeks each scenario covers.
-- =============================================================================

-- Scenario A: closed-week target W-2 (approved + locked).
INSERT INTO [SOAssistant].dbo._tb_SOScenarioTarget
    (cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     idForecastSnapshot, fForecastQty,
     cScenariosAppliedJson, fTargetQty, cSource,
     bIsFlagged, bIsLocked, dtLockedAt, dtCreatedAt, dtUpdatedAt)
SELECT
    'TEST_A_SHOPRITE_DBV', 'TEST_MILK_2L_FC', '2026-10-18',
    'TEST_RT_101', 'WC', 'Milk',
    f.idForecast, 400.0000,
    CONCAT('[', @scenario_milkjuice, ']'), 420.0000, 'SCENARIO',
    0, 1, '2026-10-18 00:00:00',
    '2026-10-17 23:30:00', '2026-10-17 23:30:00'
  FROM [SOAssistant].dbo._tb_SOForecast f
 WHERE f.cCustomerAccount = 'TEST_A_SHOPRITE_DBV'
   AND f.cStockCode = 'TEST_MILK_2L_FC'
   AND f.dWeekStart = '2026-10-18';

-- Scenario A: live target W+1 (upcoming, not locked).
INSERT INTO [SOAssistant].dbo._tb_SOScenarioTarget
    (cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     idForecastSnapshot, fForecastQty,
     cScenariosAppliedJson, fTargetQty, cSource,
     bIsFlagged, bIsLocked, dtCreatedAt, dtUpdatedAt)
SELECT
    'TEST_A_SHOPRITE_DBV', 'TEST_MILK_2L_FC', '2026-11-08',
    'TEST_RT_101', 'WC', 'Milk',
    f.idForecast, 400.0000,
    CONCAT('[', @scenario_milkjuice, ']'), 420.0000, 'SCENARIO',
    0, 0,
    '2026-10-31 23:30:00', '2026-10-31 23:30:00'
  FROM [SOAssistant].dbo._tb_SOForecast f
 WHERE f.cCustomerAccount = 'TEST_A_SHOPRITE_DBV'
   AND f.cStockCode = 'TEST_MILK_2L_FC'
   AND f.dWeekStart = '2026-11-08';

-- Scenario B: no scenario -- target == forecast.
INSERT INTO [SOAssistant].dbo._tb_SOScenarioTarget
    (cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     idForecastSnapshot, fForecastQty,
     cScenariosAppliedJson, fTargetQty, cSource,
     bIsFlagged, bIsLocked, dtCreatedAt, dtUpdatedAt)
SELECT
    'TEST_B_SPAR_PNL', 'TEST_YOG_500G', '2026-11-01',
    'TEST_RT_102', 'WC', 'Yoghurt',
    f.idForecast, 150.0000,
    NULL, 150.0000, 'FORECAST',
    0, 0,
    '2026-10-31 23:30:00', '2026-10-31 23:30:00'
  FROM [SOAssistant].dbo._tb_SOForecast f
 WHERE f.cCustomerAccount = 'TEST_B_SPAR_PNL'
   AND f.cStockCode = 'TEST_YOG_500G'
   AND f.dWeekStart = '2026-11-01';

-- Scenario C: no scenario -- target == forecast.
INSERT INTO [SOAssistant].dbo._tb_SOScenarioTarget
    (cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     idForecastSnapshot, fForecastQty,
     cScenariosAppliedJson, fTargetQty, cSource,
     bIsFlagged, bIsLocked, dtCreatedAt, dtUpdatedAt)
SELECT
    'TEST_C_TUCKSHOP_STE', 'TEST_CHZ_CHE_1KG', '2026-11-08',
    'TEST_RT_103', 'WC', 'Cheese',
    f.idForecast, 25.0000,
    NULL, 25.0000, 'FORECAST',
    0, 0,
    '2026-10-31 23:30:00', '2026-10-31 23:30:00'
  FROM [SOAssistant].dbo._tb_SOForecast f
 WHERE f.cCustomerAccount = 'TEST_C_TUCKSHOP_STE'
   AND f.cStockCode = 'TEST_CHZ_CHE_1KG'
   AND f.dWeekStart = '2026-11-08';

-- Scenario D: flagged target W+1 (scenario still in PREVIEW).
INSERT INTO [SOAssistant].dbo._tb_SOScenarioTarget
    (cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     idForecastSnapshot, fForecastQty,
     cScenariosAppliedJson, fTargetQty, cSource,
     bIsFlagged, cFlagReasonsJson,
     bIsLocked, dtCreatedAt, dtUpdatedAt)
SELECT
    'TEST_D_CAFE_ARDERNE', 'TEST_YOG_1L_SBE', '2026-11-08',
    'TEST_RT_101', 'WC', 'Yoghurt',
    f.idForecast, 57.0000,
    CONCAT('[', @scenario_yog, ']'), 85.5000, 'SCENARIO',
    1, '["above_historical_p95","new_product_requires_review"]',
    0, '2026-11-01 09:30:00', '2026-11-01 09:30:00'
  FROM [SOAssistant].dbo._tb_SOForecast f
 WHERE f.cCustomerAccount = 'TEST_D_CAFE_ARDERNE'
   AND f.cStockCode = 'TEST_YOG_1L_SBE'
   AND f.dWeekStart = '2026-11-08';

-- Scenario E: closed-week target W-2 (approved + locked).
INSERT INTO [SOAssistant].dbo._tb_SOScenarioTarget
    (cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     idForecastSnapshot, fForecastQty,
     cScenariosAppliedJson, fTargetQty, cSource,
     bIsFlagged, bIsLocked, dtLockedAt, dtCreatedAt, dtUpdatedAt)
SELECT
    'TEST_E_SPAR_WDSTK', 'TEST_JCE_1L_OJ', '2026-10-18',
    'TEST_RT_101', 'WC', 'Juice',
    f.idForecast, 400.0000,
    CONCAT('[', @scenario_milkjuice, ']'), 420.0000, 'SCENARIO',
    0, 1, '2026-10-18 00:00:00',
    '2026-10-17 23:30:00', '2026-10-17 23:30:00'
  FROM [SOAssistant].dbo._tb_SOForecast f
 WHERE f.cCustomerAccount = 'TEST_E_SPAR_WDSTK'
   AND f.cStockCode = 'TEST_JCE_1L_OJ'
   AND f.dWeekStart = '2026-10-18';


-- =============================================================================
-- Order suggestions (CORRECTION 1 -- persisted presentations).
-- Scenario A: 3 suggestions, mostly accepted (small rep variance).
-- Scenario E: 3 suggestions, systematically modified downward.
-- =============================================================================

-- Scenario A: rep accepted close to full
INSERT INTO [SOAssistant].dbo._tb_SOOrderSuggestion
    (cCustomerAccount, cStockCode, dDeliveryDate, dWeekStart,
     fSuggestedQty, cSuggestionSource,
     idScenarioTarget, idForecast,
     fTargetBasis, fForecastBasis, fDayShare,
     fAcceptedQty, cDecision,
     iAgentID, cUsername, cOrderNum,
     dtPresentedAt, dtDecidedAt)
SELECT
    'TEST_A_SHOPRITE_DBV', 'TEST_MILK_2L_FC', '2026-10-19', '2026-10-18',
    168.0000, 'TARGET',
    st.idScenarioTarget, st.idForecastSnapshot,
    420.0000, 400.0000, 0.4000,
    170.0000, 'MODIFIED',
    NULL, 'test_rep_pieter', 'TEST_SO_A_W2_MON',
    '2026-10-16 15:30:00', '2026-10-16 15:35:00'
  FROM [SOAssistant].dbo._tb_SOScenarioTarget st
 WHERE st.cCustomerAccount = 'TEST_A_SHOPRITE_DBV'
   AND st.cStockCode = 'TEST_MILK_2L_FC'
   AND st.dWeekStart = '2026-10-18';

INSERT INTO [SOAssistant].dbo._tb_SOOrderSuggestion
    (cCustomerAccount, cStockCode, dDeliveryDate, dWeekStart,
     fSuggestedQty, cSuggestionSource,
     idScenarioTarget, idForecast,
     fTargetBasis, fForecastBasis, fDayShare,
     fAcceptedQty, cDecision,
     iAgentID, cUsername, cOrderNum,
     dtPresentedAt, dtDecidedAt)
SELECT
    'TEST_A_SHOPRITE_DBV', 'TEST_MILK_2L_FC', '2026-10-21', '2026-10-18',
    126.0000, 'TARGET',
    st.idScenarioTarget, st.idForecastSnapshot,
    420.0000, 400.0000, 0.3000,
    125.0000, 'MODIFIED',
    NULL, 'test_rep_pieter', 'TEST_SO_A_W2_WED',
    '2026-10-18 15:30:00', '2026-10-18 15:35:00'
  FROM [SOAssistant].dbo._tb_SOScenarioTarget st
 WHERE st.cCustomerAccount = 'TEST_A_SHOPRITE_DBV'
   AND st.cStockCode = 'TEST_MILK_2L_FC'
   AND st.dWeekStart = '2026-10-18';

INSERT INTO [SOAssistant].dbo._tb_SOOrderSuggestion
    (cCustomerAccount, cStockCode, dDeliveryDate, dWeekStart,
     fSuggestedQty, cSuggestionSource,
     idScenarioTarget, idForecast,
     fTargetBasis, fForecastBasis, fDayShare,
     fAcceptedQty, cDecision,
     iAgentID, cUsername, cOrderNum,
     dtPresentedAt, dtDecidedAt)
SELECT
    'TEST_A_SHOPRITE_DBV', 'TEST_MILK_2L_FC', '2026-10-23', '2026-10-18',
    126.0000, 'TARGET',
    st.idScenarioTarget, st.idForecastSnapshot,
    420.0000, 400.0000, 0.3000,
    115.0000, 'MODIFIED',
    NULL, 'test_rep_pieter', 'TEST_SO_A_W2_FRI',
    '2026-10-20 15:30:00', '2026-10-20 15:35:00'
  FROM [SOAssistant].dbo._tb_SOScenarioTarget st
 WHERE st.cCustomerAccount = 'TEST_A_SHOPRITE_DBV'
   AND st.cStockCode = 'TEST_MILK_2L_FC'
   AND st.dWeekStart = '2026-10-18';

-- Scenario E: rep systematically underordered
INSERT INTO [SOAssistant].dbo._tb_SOOrderSuggestion
    (cCustomerAccount, cStockCode, dDeliveryDate, dWeekStart,
     fSuggestedQty, cSuggestionSource,
     idScenarioTarget, idForecast,
     fTargetBasis, fForecastBasis, fDayShare,
     fAcceptedQty, cDecision,
     iAgentID, cUsername, cOrderNum,
     dtPresentedAt, dtDecidedAt)
SELECT
    'TEST_E_SPAR_WDSTK', 'TEST_JCE_1L_OJ', '2026-10-19', '2026-10-18',
    168.0000, 'TARGET',
    st.idScenarioTarget, st.idForecastSnapshot,
    420.0000, 400.0000, 0.4000,
    120.0000, 'MODIFIED',
    NULL, 'test_rep_sarah', 'TEST_SO_E_W2_MON',
    '2026-10-16 16:00:00', '2026-10-16 16:03:00'
  FROM [SOAssistant].dbo._tb_SOScenarioTarget st
 WHERE st.cCustomerAccount = 'TEST_E_SPAR_WDSTK'
   AND st.cStockCode = 'TEST_JCE_1L_OJ'
   AND st.dWeekStart = '2026-10-18';

INSERT INTO [SOAssistant].dbo._tb_SOOrderSuggestion
    (cCustomerAccount, cStockCode, dDeliveryDate, dWeekStart,
     fSuggestedQty, cSuggestionSource,
     idScenarioTarget, idForecast,
     fTargetBasis, fForecastBasis, fDayShare,
     fAcceptedQty, cDecision,
     iAgentID, cUsername, cOrderNum,
     dtPresentedAt, dtDecidedAt)
SELECT
    'TEST_E_SPAR_WDSTK', 'TEST_JCE_1L_OJ', '2026-10-21', '2026-10-18',
    126.0000, 'TARGET',
    st.idScenarioTarget, st.idForecastSnapshot,
    420.0000, 400.0000, 0.3000,
    90.0000, 'MODIFIED',
    NULL, 'test_rep_sarah', 'TEST_SO_E_W2_WED',
    '2026-10-18 16:00:00', '2026-10-18 16:03:00'
  FROM [SOAssistant].dbo._tb_SOScenarioTarget st
 WHERE st.cCustomerAccount = 'TEST_E_SPAR_WDSTK'
   AND st.cStockCode = 'TEST_JCE_1L_OJ'
   AND st.dWeekStart = '2026-10-18';

INSERT INTO [SOAssistant].dbo._tb_SOOrderSuggestion
    (cCustomerAccount, cStockCode, dDeliveryDate, dWeekStart,
     fSuggestedQty, cSuggestionSource,
     idScenarioTarget, idForecast,
     fTargetBasis, fForecastBasis, fDayShare,
     fAcceptedQty, cDecision,
     iAgentID, cUsername, cOrderNum,
     dtPresentedAt, dtDecidedAt)
SELECT
    'TEST_E_SPAR_WDSTK', 'TEST_JCE_1L_OJ', '2026-10-23', '2026-10-18',
    126.0000, 'TARGET',
    st.idScenarioTarget, st.idForecastSnapshot,
    420.0000, 400.0000, 0.3000,
    90.0000, 'MODIFIED',
    NULL, 'test_rep_sarah', 'TEST_SO_E_W2_FRI',
    '2026-10-20 16:00:00', '2026-10-20 16:03:00'
  FROM [SOAssistant].dbo._tb_SOScenarioTarget st
 WHERE st.cCustomerAccount = 'TEST_E_SPAR_WDSTK'
   AND st.cStockCode = 'TEST_JCE_1L_OJ'
   AND st.dWeekStart = '2026-10-18';


-- =============================================================================
-- ForecastActuals (closed-loop fact for the W-2 closed week).
-- Scenario A: hit target. Scenario E: forecast MAPE looks bad; the
-- suggested-acceptance diagnostic reveals it's an execution gap.
-- =============================================================================

-- Scenario A actual for W-2 (closed week)
INSERT INTO [SOAssistant].dbo._tb_SOForecastActual
    (cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     fForecastQty, idForecastModel, idForecastSnapshot,
     fTargetQty, idScenarioTargetSnapshot,
     fSuggestedQtySum, fAcceptedQtySum, fCommittedQty, fDeliveredQty,
     fShortDeliveredQty, fEstimatedTrueDemand, bHadStockout,
     fForecastError, fForecastMAPE,
     fTargetAchievementPct, fSuggestedAcceptanceRate, fTargetAggression,
     dtClosedAt, dtCreatedAt)
SELECT
    'TEST_A_SHOPRITE_DBV', 'TEST_MILK_2L_FC', '2026-10-18',
    'TEST_RT_101', 'WC', 'Milk',
    400.0000, @model_base, f.idForecast,
    420.0000, st.idScenarioTarget,
    420.0000, 410.0000, 410.0000, 408.0000,
    0.0000, 408.0000, 0,
    -8.0000, 0.0196,
    0.9714, 0.9762, 1.0500,
    '2026-10-25 00:05:00', '2026-10-25 00:05:00'
  FROM [SOAssistant].dbo._tb_SOForecast f
  JOIN [SOAssistant].dbo._tb_SOScenarioTarget st
    ON st.cCustomerAccount = f.cCustomerAccount
   AND st.cStockCode = f.cStockCode
   AND st.dWeekStart = f.dWeekStart
 WHERE f.cCustomerAccount = 'TEST_A_SHOPRITE_DBV'
   AND f.cStockCode = 'TEST_MILK_2L_FC'
   AND f.dWeekStart = '2026-10-18';

-- Scenario E actual for W-2 (closed week) -- the execution-gap story
INSERT INTO [SOAssistant].dbo._tb_SOForecastActual
    (cCustomerAccount, cStockCode, dWeekStart,
     cRoute, cDistCentre, cCategory,
     fForecastQty, idForecastModel, idForecastSnapshot,
     fTargetQty, idScenarioTargetSnapshot,
     fSuggestedQtySum, fAcceptedQtySum, fCommittedQty, fDeliveredQty,
     fShortDeliveredQty, fEstimatedTrueDemand, bHadStockout,
     fForecastError, fForecastMAPE,
     fTargetAchievementPct, fSuggestedAcceptanceRate, fTargetAggression,
     dtClosedAt, dtCreatedAt)
SELECT
    'TEST_E_SPAR_WDSTK', 'TEST_JCE_1L_OJ', '2026-10-18',
    'TEST_RT_101', 'WC', 'Juice',
    400.0000, @model_base, f.idForecast,
    420.0000, st.idScenarioTarget,
    420.0000, 300.0000, 300.0000, 300.0000,
    0.0000, 300.0000, 0,
    100.0000, 0.3333,
    0.7143, 0.7143, 1.0500,
    '2026-10-25 00:05:00', '2026-10-25 00:05:00'
  FROM [SOAssistant].dbo._tb_SOForecast f
  JOIN [SOAssistant].dbo._tb_SOScenarioTarget st
    ON st.cCustomerAccount = f.cCustomerAccount
   AND st.cStockCode = f.cStockCode
   AND st.dWeekStart = f.dWeekStart
 WHERE f.cCustomerAccount = 'TEST_E_SPAR_WDSTK'
   AND f.cStockCode = 'TEST_JCE_1L_OJ'
   AND f.dWeekStart = '2026-10-18';


-- =============================================================================
-- Tasks auto-generated by the opportunity detector (hand-seeded here
-- to show the shape).
-- =============================================================================

-- Scenario D: flagged target -> management-level review task
INSERT INTO [SOAssistant].dbo._tb_SOTask
    (cTaskType, cPriority, cAssignedToUsername,
     cCustomerAccount, cStockCode, cCategory, cRoute, cDistCentre, idScenario,
     cTitle, cDescription, dDueDate, cStatus, dtCreatedAt)
VALUES
    ('REVIEW_FLAGGED_TARGET', 'HIGH', 'test_mgr_national',
     'TEST_D_CAFE_ARDERNE', 'TEST_YOG_1L_SBE', 'Yoghurt', 'TEST_RT_101', 'WC',
     @scenario_yog,
     'Review flagged target: Cafe Arderne x Yoghurt 1L Strawberry',
     'Scenario "+50% new product push" produced a target (85.5 L) that '
     + 'exceeds historical P95 (60 L). Also flagged as new product '
     + 'requiring manual review. Accept, override, or exclude from scenario.',
     '2026-11-07', 'OPEN', '2026-11-01 09:30:00');

-- Scenario E: rep systematically underperforming -> coaching task
INSERT INTO [SOAssistant].dbo._tb_SOTask
    (cTaskType, cPriority, cAssignedToUsername,
     cCustomerAccount, cStockCode, cCategory, cRoute,
     cTitle, cDescription, dDueDate, cStatus, dtCreatedAt)
VALUES
    ('VISIT_CUSTOMER', 'MED', 'test_rep_sarah',
     'TEST_E_SPAR_WDSTK', 'TEST_JCE_1L_OJ', 'Juice', 'TEST_RT_101',
     'Investigate low acceptance rate: Spar Woodstock x Juice 1L OJ',
     'Last week''s suggestion-acceptance was 71% (300 of 420 L accepted). '
     + 'Forecast tracked actual at 25% MAPE but target missed by 29%. '
     + 'Likely execution gap -- check with customer whether they still '
     + 'want Juice at this volume.',
     '2026-11-07', 'OPEN', '2026-11-01 00:10:00');


-- =============================================================================
-- Summary print
-- =============================================================================

PRINT 'forecasting_seed.sql completed. Seeded 5 scenarios, run `forecasting_inspection.sql` to walk through.';
PRINT '  Scenario A: smooth demand, hit target';
PRINT '  Scenario B: stockout masks true demand';
PRINT '  Scenario C: intermittent, fallback triggered';
PRINT '  Scenario D: reasonableness flag triggered';
PRINT '  Scenario E: rep execution gap (not a model problem)';
GO
