-- =============================================================================
-- forecasting_inspection.sql
--
-- Inspection views + ready-to-run queries for walking through the test
-- fixtures seeded by forecasting_seed.sql. Run each named QUERY block
-- in SSMS, compare against the expected outputs in
-- FORECASTING_WALKTHROUGH.md.
--
-- Two kinds of SQL in this file:
--   1. CREATE VIEWs (idempotent CREATE OR ALTER) -- these become
--      permanent inspection surfaces the engine, UI, and ad-hoc
--      queries will reuse.
--   2. SELECT queries (prefixed "-- QUERY N:") -- highlight and run
--      individually.
-- =============================================================================

USE [SOAssistant];
GO

-- =============================================================================
-- VIEW: _vw_SOForecastActive
-- One row per (customer, SKU, week) -- the forecast currently marked
-- active for the leaf. Shadow predictions from other models are
-- filtered out.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOForecastActive AS
    SELECT
        f.idForecast,
        f.idForecastRun,
        f.idForecastModel,
        m.cCode              AS cModelCode,
        m.cName              AS cModelName,
        f.cCustomerAccount,
        f.cStockCode,
        f.dWeekStart,
        f.cRoute,
        f.cDistCentre,
        f.cCategory,
        f.cTelesales,
        f.cSalesRep,
        f.fPredictedQty,
        f.fPILower,
        f.fPIUpper,
        f.cDemandPattern,
        f.cConfidenceGrade,
        f.bIsFallback,
        f.cFallbackReason,
        f.fFallbackSource,
        f.dtCreatedAt
      FROM [SOAssistant].dbo._tb_SOForecast f
      JOIN [SOAssistant].dbo._tb_SOForecastModel m
        ON m.idForecastModel = f.idForecastModel
     WHERE f.bIsActive = 1;
GO


-- =============================================================================
-- VIEW: _vw_SOForecastClosedLoop
-- One row per (customer, SKU, week) joining the active forecast + the
-- materialised scenario target + the actuals + the summed suggestions.
-- The grain of the four-measurements picture. NULLs on target /
-- suggestions / actual mean "not yet / none".
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOForecastClosedLoop AS
    SELECT
        COALESCE(af.cCustomerAccount, st.cCustomerAccount, fa.cCustomerAccount) AS cCustomerAccount,
        COALESCE(af.cStockCode,       st.cStockCode,       fa.cStockCode)       AS cStockCode,
        COALESCE(af.dWeekStart,       st.dWeekStart,       fa.dWeekStart)       AS dWeekStart,
        COALESCE(af.cRoute,           st.cRoute,           fa.cRoute)           AS cRoute,
        COALESCE(af.cDistCentre,      st.cDistCentre,      fa.cDistCentre)      AS cDistCentre,
        COALESCE(af.cCategory,        st.cCategory,        fa.cCategory)        AS cCategory,
        -- Forecast
        af.idForecast,
        af.cModelCode,
        af.fPredictedQty                                             AS fForecastQty,
        af.cDemandPattern,
        af.cConfidenceGrade,
        af.bIsFallback,
        af.cFallbackReason,
        -- Target
        st.idScenarioTarget,
        st.fTargetQty,
        st.cSource                                                   AS cTargetSource,
        st.cScenariosAppliedJson,
        st.bIsFlagged,
        st.cFlagReasonsJson,
        st.bIsLocked,
        -- Suggestions (summed across all deliveries in the week)
        sg.fSuggestedQtySum,
        sg.fAcceptedQtySum,
        -- Actuals (closed-loop fact row)
        fa.fCommittedQty,
        fa.fDeliveredQty,
        fa.fShortDeliveredQty,
        fa.fEstimatedTrueDemand,
        fa.bHadStockout,
        -- Four separate error metrics
        fa.fForecastError,
        fa.fForecastMAPE,
        fa.fTargetAchievementPct,
        fa.fSuggestedAcceptanceRate,
        fa.fTargetAggression,
        fa.dtClosedAt
      FROM [SOAssistant].dbo._vw_SOForecastActive af
      FULL OUTER JOIN [SOAssistant].dbo._tb_SOScenarioTarget st
        ON st.cCustomerAccount = af.cCustomerAccount
       AND st.cStockCode       = af.cStockCode
       AND st.dWeekStart       = af.dWeekStart
      LEFT JOIN (
          SELECT
              cCustomerAccount,
              cStockCode,
              dWeekStart,
              SUM(fSuggestedQty)         AS fSuggestedQtySum,
              SUM(COALESCE(fAcceptedQty, 0)) AS fAcceptedQtySum
            FROM [SOAssistant].dbo._tb_SOOrderSuggestion
           GROUP BY cCustomerAccount, cStockCode, dWeekStart
      ) sg
        ON sg.cCustomerAccount = COALESCE(af.cCustomerAccount, st.cCustomerAccount)
       AND sg.cStockCode       = COALESCE(af.cStockCode,       st.cStockCode)
       AND sg.dWeekStart       = COALESCE(af.dWeekStart,       st.dWeekStart)
      LEFT JOIN [SOAssistant].dbo._tb_SOForecastActual fa
        ON fa.cCustomerAccount = COALESCE(af.cCustomerAccount, st.cCustomerAccount)
       AND fa.cStockCode       = COALESCE(af.cStockCode,       st.cStockCode)
       AND fa.dWeekStart       = COALESCE(af.dWeekStart,       st.dWeekStart);
GO


-- =============================================================================
-- VIEW: _vw_SOForecastHierarchyRollup
-- Target + delivered litres rolled up at every hierarchy level for
-- fast inspection. One row per (level, scope key(s), week). Managers
-- use this to see "DC-WC Milk Q4" at a glance; cDistCentre+cCategory
-- is the row key, cRoute/cCustomerAccount NULL at that level.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOForecastHierarchyRollup AS
    WITH leaf AS (
        SELECT cl.dWeekStart, cl.cDistCentre, cl.cCategory, cl.cRoute,
               cl.cCustomerAccount, cl.cStockCode,
               cl.fForecastQty, cl.fTargetQty,
               cl.fDeliveredQty, cl.fEstimatedTrueDemand
          FROM [SOAssistant].dbo._vw_SOForecastClosedLoop cl
    )
    -- LEAF
    SELECT 'LEAF' AS cScopeLevel, dWeekStart, cDistCentre, cCategory, cRoute,
           cCustomerAccount, cStockCode,
           fForecastQty, fTargetQty, fDeliveredQty, fEstimatedTrueDemand
      FROM leaf
    UNION ALL
    -- CUSTOMER x CATEGORY
    SELECT 'CUSTOMER_CATEGORY', dWeekStart, cDistCentre, cCategory, cRoute,
           cCustomerAccount, NULL,
           SUM(fForecastQty), SUM(fTargetQty), SUM(fDeliveredQty), SUM(fEstimatedTrueDemand)
      FROM leaf
     GROUP BY dWeekStart, cDistCentre, cCategory, cRoute, cCustomerAccount
    UNION ALL
    -- ROUTE x CATEGORY
    SELECT 'ROUTE_CATEGORY', dWeekStart, cDistCentre, cCategory, cRoute,
           NULL, NULL,
           SUM(fForecastQty), SUM(fTargetQty), SUM(fDeliveredQty), SUM(fEstimatedTrueDemand)
      FROM leaf
     GROUP BY dWeekStart, cDistCentre, cCategory, cRoute
    UNION ALL
    -- DC x CATEGORY
    SELECT 'DC_CATEGORY', dWeekStart, cDistCentre, cCategory, NULL,
           NULL, NULL,
           SUM(fForecastQty), SUM(fTargetQty), SUM(fDeliveredQty), SUM(fEstimatedTrueDemand)
      FROM leaf
     GROUP BY dWeekStart, cDistCentre, cCategory
    UNION ALL
    -- DC total
    SELECT 'DC_TOTAL', dWeekStart, cDistCentre, NULL, NULL,
           NULL, NULL,
           SUM(fForecastQty), SUM(fTargetQty), SUM(fDeliveredQty), SUM(fEstimatedTrueDemand)
      FROM leaf
     GROUP BY dWeekStart, cDistCentre
    UNION ALL
    -- COMPANY total
    SELECT 'COMPANY', dWeekStart, NULL, NULL, NULL,
           NULL, NULL,
           SUM(fForecastQty), SUM(fTargetQty), SUM(fDeliveredQty), SUM(fEstimatedTrueDemand)
      FROM leaf
     GROUP BY dWeekStart;
GO


-- =============================================================================
-- VIEW: _vw_SOModelLeaderboard
-- Volume-weighted MAPE / MAE / bias per model, computed from
-- _tb_SOForecastActual rows that have a known model id. For now only
-- the base model has actuals -- the view generalises to multi-model
-- comparison in Phase 2.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOModelLeaderboard AS
    SELECT
        fa.idForecastModel,
        m.cCode   AS cModelCode,
        m.cName   AS cModelName,
        m.cFamily AS cModelFamily,
        COUNT(*)                     AS iLeafsScored,
        -- Volume-weighted MAPE: SUM(|err|) / SUM(true_demand).
        -- Guards against actual=0 by using true demand which is >= delivered.
        SUM(ABS(fa.fForecastError))
            / NULLIF(SUM(fa.fEstimatedTrueDemand), 0)              AS fWeightedMAPE,
        AVG(ABS(fa.fForecastError))                                AS fMAE,
        -- Bias: SUM(err) / SUM(true_demand) -- signed, +ve = over-forecast
        SUM(fa.fForecastError)
            / NULLIF(SUM(fa.fEstimatedTrueDemand), 0)              AS fBias,
        MIN(fa.dWeekStart)   AS dFirstScoredWeek,
        MAX(fa.dWeekStart)   AS dLastScoredWeek
      FROM [SOAssistant].dbo._tb_SOForecastActual fa
      JOIN [SOAssistant].dbo._tb_SOForecastModel m
        ON m.idForecastModel = fa.idForecastModel
     WHERE fa.idForecastModel IS NOT NULL
       AND fa.fEstimatedTrueDemand IS NOT NULL
       AND fa.fEstimatedTrueDemand > 0
     GROUP BY fa.idForecastModel, m.cCode, m.cName, m.cFamily;
GO


PRINT 'Views created. Highlight each QUERY block below and run it individually.';
GO


-- =============================================================================
-- QUERY 1: Model registry
-- Expected: 5 rows. BASE_MEDIAN_6W is enabled (bEnabled=1); the other
-- 4 are registered but disabled until their Python implementations land.
-- =============================================================================
SELECT idForecastModel, cCode, cName, cFamily, bEnabled, cDescription
  FROM [SOAssistant].dbo._tb_SOForecastModel
 ORDER BY idForecastModel;


-- =============================================================================
-- QUERY 2: All active forecasts for upcoming weeks
-- Expected: forecasts for Scenarios A (W-2, W+1), B (W0), C (W+1),
-- D (W+1), E (W-2). 6 rows total.
-- =============================================================================
SELECT
    cCustomerAccount, cStockCode, dWeekStart,
    cModelCode, fPredictedQty, cDemandPattern, cConfidenceGrade,
    bIsFallback, cFallbackReason, fFallbackSource
  FROM [SOAssistant].dbo._vw_SOForecastActive
 WHERE cCustomerAccount LIKE 'TEST_%'
 ORDER BY dWeekStart, cCustomerAccount;


-- =============================================================================
-- QUERY 3: Scenario A deep dive -- closed week W-2
-- The full closed-loop story for Shoprite Durbanville x Milk 2L.
-- Expected:
--   fForecastQty = 400, fTargetQty = 420, fDeliveredQty = 408
--   fForecastMAPE = 0.0196  (1.96% -- model good)
--   fTargetAchievementPct = 0.9714  (97.14% -- hit target)
--   fSuggestedAcceptanceRate = 0.9762  (rep executed cleanly)
--   fTargetAggression = 1.0500  (5% up)
-- =============================================================================
SELECT
    cCustomerAccount, cStockCode, dWeekStart, cDistCentre, cCategory,
    fForecastQty, fTargetQty, cTargetSource, cScenariosAppliedJson,
    fSuggestedQtySum, fAcceptedQtySum, fCommittedQty, fDeliveredQty,
    fShortDeliveredQty, bHadStockout,
    fForecastError, fForecastMAPE,
    fTargetAchievementPct, fSuggestedAcceptanceRate, fTargetAggression
  FROM [SOAssistant].dbo._vw_SOForecastClosedLoop
 WHERE cCustomerAccount = 'TEST_A_SHOPRITE_DBV'
   AND dWeekStart = '2026-10-18';

-- Scenario A: the three individual suggestions and the rep's decisions
SELECT
    dDeliveryDate, fSuggestedQty, cSuggestionSource,
    fTargetBasis, fForecastBasis, fDayShare,
    fAcceptedQty, cDecision, cUsername, cOrderNum
  FROM [SOAssistant].dbo._tb_SOOrderSuggestion
 WHERE cCustomerAccount = 'TEST_A_SHOPRITE_DBV'
   AND cStockCode = 'TEST_MILK_2L_FC'
   AND dWeekStart = '2026-10-18'
 ORDER BY dDeliveryDate;


-- =============================================================================
-- QUERY 4: Scenario B -- stockout masks true demand diagnostic
-- Shows how fDeliveredQty (90) differs from fEstimatedTrueDemand (150),
-- and that the forecast error is computed against TRUE DEMAND so the
-- model isn't penalised for stock being out.
-- Expected:
--   fDeliveredQty = 90, fEstimatedTrueDemand = 150
--   fShortDeliveredQty = 60, bHadStockout = 1
--   fForecastMAPE = 0.0000  (0% -- model was correct)
-- =============================================================================
SELECT
    fa.cCustomerAccount, fa.cStockCode, fa.dWeekStart,
    fa.fForecastQty, fa.fTargetQty,
    fa.fDeliveredQty, fa.fEstimatedTrueDemand, fa.fShortDeliveredQty,
    fa.bHadStockout, fa.fForecastError, fa.fForecastMAPE,
    sd.cReason AS cShortReason, sd.cNotes AS cShortNotes
  FROM [SOAssistant].dbo._tb_SOForecastActual fa
  LEFT JOIN [SOAssistant].dbo._tb_SOShortDelivery sd
    ON sd.cCustomerAccount = fa.cCustomerAccount
   AND sd.cStockCode = fa.cStockCode
   AND sd.dDeliveryDate BETWEEN fa.dWeekStart
                           AND DATEADD(day, 6, fa.dWeekStart)
 WHERE fa.cCustomerAccount = 'TEST_B_SPAR_PNL';

-- Scenario B: stockout events on this SKU
SELECT cStockCode, cWarehouseCode, dStartDate, dEndDate, cNotes
  FROM [SOAssistant].dbo._tb_SOStockoutEvent
 WHERE cStockCode = 'TEST_YOG_500G';

-- Scenario B: forecast for the next week trained on true demand
-- (should be 150, not pulled down toward 90)
SELECT
    cCustomerAccount, cStockCode, dWeekStart, cModelCode,
    fPredictedQty, cDemandPattern, cConfidenceGrade
  FROM [SOAssistant].dbo._vw_SOForecastActive
 WHERE cCustomerAccount = 'TEST_B_SPAR_PNL';


-- =============================================================================
-- QUERY 5: Scenario C -- intermittent demand, fallback forecast
-- Expected:
--   bIsFallback = 1, cFallbackReason = 'SPARSE_HISTORY'
--   fPredictedQty = 25 (category-average fallback)
--   cConfidenceGrade = 'D'
--   iNonZeroWeeks = 2, iTotalWeeksObserved = 10, iMinRequired = 12
-- =============================================================================
SELECT
    f.cCustomerAccount, f.cStockCode, f.dWeekStart,
    f.cModelCode, f.fPredictedQty, f.cConfidenceGrade,
    f.bIsFallback, f.cFallbackReason, f.fFallbackSource,
    dc.cPattern, dc.iNonZeroWeeks, dc.iTotalWeeksObserved,
    dc.iMinRequired, dc.cRecommendedModel
  FROM [SOAssistant].dbo._vw_SOForecastActive f
  JOIN [SOAssistant].dbo._tb_SODemandClassification dc
    ON dc.cCustomerAccount = f.cCustomerAccount
   AND dc.cStockCode = f.cStockCode
 WHERE f.cCustomerAccount = 'TEST_C_TUCKSHOP_STE';


-- =============================================================================
-- QUERY 6: Scenario D -- flagged target, scenario in PREVIEW
-- Shows the materialised target with its flag reasons and the scenario
-- it was derived from (status = PREVIEW, not yet approved).
-- Expected:
--   fForecastQty = 57, fTargetQty = 85.5
--   bIsFlagged = 1
--   cFlagReasonsJson = '["above_historical_p95","new_product_requires_review"]'
--   scenario status = 'PREVIEW'
-- =============================================================================
SELECT
    st.cCustomerAccount, st.cStockCode, st.dWeekStart, st.cCategory,
    st.fForecastQty, st.fTargetQty, st.cSource,
    st.bIsFlagged, st.cFlagReasonsJson,
    s.cName AS cScenarioName, s.cStatus AS cScenarioStatus,
    s.cLeverType, s.fLeverValue, s.dPeriodFrom, s.dPeriodTo
  FROM [SOAssistant].dbo._tb_SOScenarioTarget st
  -- OPENJSON, not LIKE: '[' is a LIKE wildcard, so '%[5]%' matched any
  -- target whose JSON merely contained the digit 5.
  JOIN [SOAssistant].dbo._tb_SOScenario s
    ON EXISTS (SELECT 1 FROM OPENJSON(st.cScenariosAppliedJson) j
                WHERE TRY_CAST(j.value AS INT) = s.idScenario)
 WHERE st.cCustomerAccount = 'TEST_D_CAFE_ARDERNE';

-- Scenario D: the auto-generated REVIEW_FLAGGED_TARGET task
SELECT idTask, cTaskType, cPriority, cAssignedToUsername,
       cCustomerAccount, cStockCode, cTitle, cDescription, dDueDate, cStatus
  FROM [SOAssistant].dbo._tb_SOTask
 WHERE cCustomerAccount = 'TEST_D_CAFE_ARDERNE';


-- =============================================================================
-- QUERY 7: Scenario E -- rep execution gap diagnostic (CORRECTION 2)
-- Four error signals showing DIFFERENT stories about the same miss:
--   fForecastMAPE 33.33%        -- "model looks bad"
--   fTargetAchievementPct 71%   -- "we missed target by 29%"
--   fSuggestedAcceptanceRate 71% -- "rep only accepted 71% of suggestions"
--   fTargetAggression 1.05      -- "lever was modest, not the cause"
-- Together: the model was fine, the lever was fine, the gap is execution.
-- =============================================================================
SELECT
    cCustomerAccount, cStockCode, dWeekStart,
    fForecastQty, fTargetQty,
    fSuggestedQtySum, fAcceptedQtySum, fDeliveredQty,
    fForecastError, fForecastMAPE,
    fTargetAchievementPct, fSuggestedAcceptanceRate, fTargetAggression
  FROM [SOAssistant].dbo._tb_SOForecastActual
 WHERE cCustomerAccount = 'TEST_E_SPAR_WDSTK';

-- Scenario E: the individual suggestions and the rep's modified qtys
SELECT
    dDeliveryDate, fSuggestedQty, fAcceptedQty,
    (fAcceptedQty - fSuggestedQty) AS fQtyDelta,
    (fAcceptedQty * 1.0 / NULLIF(fSuggestedQty, 0)) AS fAcceptancePct,
    cDecision, cUsername
  FROM [SOAssistant].dbo._tb_SOOrderSuggestion
 WHERE cCustomerAccount = 'TEST_E_SPAR_WDSTK'
   AND cStockCode = 'TEST_JCE_1L_OJ'
 ORDER BY dDeliveryDate;


-- =============================================================================
-- QUERY 8: Hierarchy roll-up for the closed week W-2 (2026-10-18)
-- Expected rows at each level:
--   LEAF: 2 rows (A x Milk, E x Juice)
--   CUSTOMER_CATEGORY: 2 rows (one per customer)
--   ROUTE_CATEGORY: 2 rows (RT_101 x Milk, RT_101 x Juice)
--   DC_CATEGORY: 2 rows (WC x Milk = 420/408, WC x Juice = 420/300)
--   DC_TOTAL: 1 row (WC = 840/708)
--   COMPANY: 1 row (840/708)
-- =============================================================================
SELECT
    cScopeLevel, dWeekStart, cDistCentre, cCategory, cRoute,
    cCustomerAccount, cStockCode,
    fForecastQty, fTargetQty, fDeliveredQty
  FROM [SOAssistant].dbo._vw_SOForecastHierarchyRollup
 WHERE dWeekStart = '2026-10-18'
   AND (cDistCentre = 'WC' OR cDistCentre IS NULL)
 ORDER BY
    CASE cScopeLevel
        WHEN 'LEAF' THEN 1
        WHEN 'CUSTOMER_CATEGORY' THEN 2
        WHEN 'ROUTE_CATEGORY' THEN 3
        WHEN 'DC_CATEGORY' THEN 4
        WHEN 'DC_TOTAL' THEN 5
        WHEN 'COMPANY' THEN 6
    END,
    cCategory, cCustomerAccount, cStockCode;


-- =============================================================================
-- QUERY 9: Model leaderboard (sanity check)
-- With only base model running on 2 closed-loop rows (A and E), the
-- leaderboard shows weighted MAPE for BASE_MEDIAN_6W.
-- Expected:
--   iLeafsScored = 2
--   fWeightedMAPE = (|-8| + |100|) / (408 + 300) = 108 / 708 = 0.1525 (15.25%)
--   fBias = (-8 + 100) / 708 = 0.1299 (12.99% over-forecast on average,
--     driven by scenario E's execution gap)
-- Phase 2 will add more scored leafs so this becomes meaningful.
-- =============================================================================
SELECT
    cModelCode, cModelFamily,
    iLeafsScored, dFirstScoredWeek, dLastScoredWeek,
    fWeightedMAPE, fMAE, fBias
  FROM [SOAssistant].dbo._vw_SOModelLeaderboard
 ORDER BY fWeightedMAPE;


-- =============================================================================
-- QUERY 10: Open tasks
-- Expected: 2 open tasks -- the REVIEW_FLAGGED_TARGET (national manager)
-- and the VISIT_CUSTOMER (rep Sarah).
-- =============================================================================
SELECT
    idTask, cTaskType, cPriority, cAssignedToUsername,
    cCustomerAccount, cStockCode, cCategory, cRoute,
    cTitle, dDueDate, cStatus, dtCreatedAt
  FROM [SOAssistant].dbo._tb_SOTask
 WHERE cCustomerAccount LIKE 'TEST_%' OR cStockCode LIKE 'TEST_%'
 ORDER BY cPriority DESC, dDueDate;


-- =============================================================================
-- QUERY 11: Scenario inventory
-- Shows both scenarios (one ACTIVE, one PREVIEW with flagged leafs).
-- =============================================================================
SELECT
    s.idScenario, s.cName, s.cStatus,
    s.cLeverType, s.fLeverValue, s.dPeriodFrom, s.dPeriodTo,
    s.cScopeJson, s.cCreatedBy, s.dtCreatedAt, s.cApprovedBy,
    (SELECT COUNT(*) FROM [SOAssistant].dbo._tb_SOScenarioTarget st
      WHERE st.cScenariosAppliedJson LIKE CONCAT('%', s.idScenario, '%')) AS iLeafsCascaded,
    (SELECT COUNT(*) FROM [SOAssistant].dbo._tb_SOScenarioTarget st
      WHERE st.cScenariosAppliedJson LIKE CONCAT('%', s.idScenario, '%')
        AND st.bIsFlagged = 1) AS iLeafsFlagged
  FROM [SOAssistant].dbo._tb_SOScenario s
 WHERE s.cName LIKE 'TEST:%'
 ORDER BY s.idScenario;


PRINT 'Inspection queries complete. See FORECASTING_WALKTHROUGH.md for the full narrative.';
GO
