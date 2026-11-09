-- =============================================================================
-- forecasting_schema.sql
--
-- Demand Forecasting & Target Management module schema.
--
-- Design cornerstone: FOUR separate measurements. A forecast is the model's
-- prediction. A target is the forecast + management overlays. A suggestion
-- is what the rep saw on the screen. An actual is what Evolution delivered.
-- They never collapse into one number and we track accuracy for each
-- transition independently.
--
-- Week convention: ISO weeks, Monday to Sunday (services/forecasting/weeks.py;
-- switched from Sunday-aligned on 2026-11-06). Every forecast / target /
-- scorecard row carries dWeekStart DATE which is always a Monday.
-- Not ISO weeks (ISO starts Monday) -- resist the urge.
--
-- DC hierarchy: Customer.ulARDistCentre on [BA_00_LPD_CON].dbo.Client is
-- the source of truth. We DO NOT maintain our own DC master. Dimensions
-- are denormalized onto fact rows at write time so inspection queries
-- don't have to cross-DB-join on every select.
--
-- Idempotency: every CREATE TABLE and CREATE INDEX is guarded with
-- IF OBJECT_ID / IF NOT EXISTS checks matching the pattern used in
-- create_views.sql so this file is safe to re-run.
-- =============================================================================

USE [SOAssistant];
GO

-- -----------------------------------------------------------------------------
-- _tb_SOForecastModel  (model registry -- one row per forecasting model
-- implementation the engine can run). The engine picks a model per
-- (customer x SKU) based on demand pattern + available history. Models
-- are NEVER auto-promoted; management flips bEnabled explicitly.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOForecastModel', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOForecastModel (
        idForecastModel   INT IDENTITY(1,1) NOT NULL,
        cCode             VARCHAR(32)   NOT NULL,
        cName             VARCHAR(100)  NOT NULL,
        cFamily           VARCHAR(32)   NOT NULL,
        cDescription      NVARCHAR(500) NULL,
        cParamsJson       NVARCHAR(MAX) NULL,
        bEnabled          BIT           NOT NULL
            CONSTRAINT DF_SOForecastModel_bEnabled DEFAULT (1),
        dtCreatedAt       DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SOForecastModel_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOForecastModel PRIMARY KEY CLUSTERED (idForecastModel),
        CONSTRAINT UX_SOForecastModel_Code UNIQUE (cCode),
        CONSTRAINT CK_SOForecastModel_Family CHECK
            (cFamily IN ('base','statistical','intermittent','ml','ensemble'))
    );
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SODemandClassification  (per customer x SKU: ADI, CV^2, pattern,
-- recommended model, fallback reason if any). Recomputed weekly from the
-- trailing 52 weeks of _vw_SOCustomerInvoiceHistory.
--
-- Correction 4: Model selection with sensible fallbacks. iMinRequired is
-- the number of non-zero weeks needed to trust an individual (cust, SKU)
-- fit. Below that threshold the engine still produces a forecast but
-- flags bIsFallback on the forecast row and uses a group-level source
-- (category average, similar-customer average, etc.).
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SODemandClassification', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SODemandClassification (
        idDemandClass        BIGINT IDENTITY(1,1) NOT NULL,
        cCustomerAccount     VARCHAR(20)   NOT NULL,
        cStockCode           VARCHAR(20)   NOT NULL,
        fCVSquared           DECIMAL(18,6) NULL,
        fADI                 DECIMAL(18,4) NULL,
        cPattern             VARCHAR(16)   NOT NULL,
        iNonZeroWeeks        INT           NOT NULL,
        iTotalWeeksObserved  INT           NOT NULL,
        iMinRequired         INT           NOT NULL
            CONSTRAINT DF_SODemandClassification_MinReq DEFAULT (12),
        cRecommendedModel    VARCHAR(32)   NULL,
        cFallbackReason      VARCHAR(32)   NULL,
        dtClassifiedAt       DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SODemandClassification_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SODemandClassification PRIMARY KEY CLUSTERED (idDemandClass),
        CONSTRAINT UX_SODemandClassification_CustSku UNIQUE (cCustomerAccount, cStockCode),
        CONSTRAINT CK_SODemandClassification_Pattern CHECK
            (cPattern IN ('SMOOTH','ERRATIC','INTERMITTENT','LUMPY','INSUFFICIENT')),
        CONSTRAINT CK_SODemandClassification_Fallback CHECK
            (cFallbackReason IS NULL OR cFallbackReason IN
             ('SPARSE_HISTORY','NEW_SKU','NEW_CUSTOMER','DISCONTINUED_PEER','NO_PEERS'))
    );
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SODeliveryPattern  (one row per customer: learned day-of-week share
-- of their weekly deliveries). Lets the engine split a weekly forecast
-- into daily suggestions in proportion to the customer's actual delivery
-- cadence. Confidence reflects sample size -- a customer with 2 weeks of
-- history gets LOW and the engine falls back to an even split.
--
-- fMon..fSun must sum to 1.0 but we don't enforce that in CHECK -- the
-- engine normalizes before writing.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SODeliveryPattern', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SODeliveryPattern (
        idDeliveryPattern   BIGINT IDENTITY(1,1) NOT NULL,
        cCustomerAccount    VARCHAR(20)   NOT NULL,
        fMonShare           DECIMAL(6,4)  NOT NULL,
        fTueShare           DECIMAL(6,4)  NOT NULL,
        fWedShare           DECIMAL(6,4)  NOT NULL,
        fThuShare           DECIMAL(6,4)  NOT NULL,
        fFriShare           DECIMAL(6,4)  NOT NULL,
        fSatShare           DECIMAL(6,4)  NOT NULL,
        fSunShare           DECIMAL(6,4)  NOT NULL,
        iWeeksSampled       INT           NOT NULL,
        cConfidence         VARCHAR(8)    NOT NULL,
        dtComputedAt        DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SODeliveryPattern_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SODeliveryPattern PRIMARY KEY CLUSTERED (idDeliveryPattern),
        CONSTRAINT UX_SODeliveryPattern_Customer UNIQUE (cCustomerAccount),
        CONSTRAINT CK_SODeliveryPattern_Confidence CHECK
            (cConfidence IN ('LOW','MED','HIGH'))
    );
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOForecastRun  (nightly run metadata -- one row per model per run).
-- Multiple models run in parallel every night (correction 5: no auto
-- promotion). The one marked bIsActive on the forecast row is the one
-- that currently feeds targets / suggestions; the others are shadow
-- predictions kept for scorecard comparison.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOForecastRun', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOForecastRun (
        idForecastRun       BIGINT IDENTITY(1,1) NOT NULL,
        idForecastModel     INT           NOT NULL,
        dtRunStartedAt      DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SOForecastRun_dtStart DEFAULT (sysdatetime()),
        dtRunCompletedAt    DATETIME2(7)  NULL,
        cStatus             VARCHAR(16)   NOT NULL
            CONSTRAINT DF_SOForecastRun_cStatus DEFAULT ('RUNNING'),
        dHorizonFrom        DATE          NOT NULL,
        dHorizonTo          DATE          NOT NULL,
        iPredictionsWritten INT           NULL,
        cNotes              NVARCHAR(1000) NULL,
        cCreatedBy          VARCHAR(50)   NOT NULL,
        CONSTRAINT PK_SOForecastRun PRIMARY KEY CLUSTERED (idForecastRun),
        CONSTRAINT CK_SOForecastRun_Status CHECK
            (cStatus IN ('RUNNING','SUCCESS','FAILED','PARTIAL'))
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOForecastRun_ModelStart'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOForecastRun')
)
BEGIN
    CREATE INDEX IX_SOForecastRun_ModelStart
        ON [SOAssistant].dbo._tb_SOForecastRun (idForecastModel, dtRunStartedAt DESC)
        INCLUDE (cStatus, dHorizonFrom, dHorizonTo);
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOForecast  (leaf prediction -- one row per customer x SKU x week x
-- model x run). The key UNIQUE constraint is (run, model, customer, SKU,
-- week): every enabled model writes its own row per leaf-week (shadow
-- predictions), and UX_SOForecast_OneActive guarantees exactly one active
-- row per leaf-week across all runs (migration 001). Dimensions are DENORMALIZED (route / DC / category) because:
--   1. Inspection queries don't want cross-DB joins to [BA_00_LPD_CON]
--   2. We want frozen attributions for the forecast's effective date
--      (customer re-routed mid-period doesn't retroactively shift
--      earlier week's DC-WC rollup)
--
-- bIsActive marks the ONE forecast per (customer, SKU, week) that feeds
-- the target materialisation + rep screen. Other model predictions sit
-- alongside for scorecard comparison.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOForecast', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOForecast (
        idForecast          BIGINT IDENTITY(1,1) NOT NULL,
        idForecastRun       BIGINT        NOT NULL,
        idForecastModel     INT           NOT NULL,
        cCustomerAccount    VARCHAR(20)   NOT NULL,
        cStockCode          VARCHAR(20)   NOT NULL,
        dWeekStart          DATE          NOT NULL,
        -- Denormalized dims (snapshot at run time)
        cRoute              VARCHAR(100)  NULL,
        cDistCentre         VARCHAR(100)  NULL,
        cCategory           VARCHAR(100)  NULL,
        cTelesales          VARCHAR(100)  NULL,
        cSalesRep           VARCHAR(50)   NULL,
        -- Prediction
        fPredictedQty       DECIMAL(18,4) NOT NULL,
        fPILower            DECIMAL(18,4) NULL,
        fPIUpper            DECIMAL(18,4) NULL,
        cDemandPattern      VARCHAR(16)   NULL,
        cConfidenceGrade    VARCHAR(2)    NULL,
        -- Correction 4: fallback flag + reason + source value
        bIsFallback         BIT           NOT NULL
            CONSTRAINT DF_SOForecast_bFallback DEFAULT (0),
        cFallbackReason     VARCHAR(32)   NULL,
        fFallbackSource     DECIMAL(18,4) NULL,
        -- Active-forecast flag: ONE row per (cust, SKU, week) is active
        bIsActive           BIT           NOT NULL
            CONSTRAINT DF_SOForecast_bActive DEFAULT (0),
        dtCreatedAt         DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SOForecast_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOForecast PRIMARY KEY CLUSTERED (idForecast),
        CONSTRAINT UX_SOForecast_RunModelLeaf UNIQUE
            (idForecastRun, idForecastModel, cCustomerAccount, cStockCode, dWeekStart),
        CONSTRAINT CK_SOForecast_ConfidenceGrade CHECK
            (cConfidenceGrade IS NULL OR cConfidenceGrade IN
             ('A+','A','B','C','D','F')),
        CONSTRAINT CK_SOForecast_FallbackReason CHECK
            (cFallbackReason IS NULL OR cFallbackReason IN
             ('SPARSE_HISTORY','NEW_SKU','NEW_CUSTOMER','DISCONTINUED_PEER','NO_PEERS'))
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOForecast_ActiveLeaf'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOForecast')
)
BEGIN
    -- Filtered index: covers the hot read path "get the active forecast
    -- for this leaf" without pulling in historical / shadow predictions.
    CREATE INDEX IX_SOForecast_ActiveLeaf
        ON [SOAssistant].dbo._tb_SOForecast (cCustomerAccount, cStockCode, dWeekStart)
        INCLUDE (idForecast, idForecastModel, fPredictedQty, cConfidenceGrade,
                 bIsFallback, cRoute, cDistCentre, cCategory)
        WHERE bIsActive = 1;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOForecast_WeekModel'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOForecast')
)
BEGIN
    -- For model-leaderboard rollups: all predictions a model made in a
    -- given week across the catalogue.
    CREATE INDEX IX_SOForecast_WeekModel
        ON [SOAssistant].dbo._tb_SOForecast (dWeekStart, idForecastModel)
        INCLUDE (cCustomerAccount, cStockCode, fPredictedQty);
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UX_SOForecast_OneActive'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOForecast')
)
BEGIN
    -- Design cornerstone: exactly ONE active forecast per leaf-week.
    CREATE UNIQUE INDEX UX_SOForecast_OneActive
        ON [SOAssistant].dbo._tb_SOForecast (cCustomerAccount, cStockCode, dWeekStart)
        WHERE bIsActive = 1;
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOScenario  (management scenarios -- "+5% Milk at DC-WC for Q1").
-- A scenario is a scoped multiplier (or absolute delta or fixed qty) over
-- a period. Scope is JSON: any combination of DC, route, customer,
-- category, SKU, salesrep, telesales. Scenarios cascade down to leaf
-- targets via the materialisation job.
--
-- Status flow:
--   DRAFT      -> being built
--   PREVIEW    -> cascade computed, flagged leafs reviewed
--   ACTIVE     -> approved by manager, targets materialised
--   COMPLETED  -> period ended, retained for audit
--   CANCELLED  -> rolled back before completion
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOScenario', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOScenario (
        idScenario          INT IDENTITY(1,1) NOT NULL,
        cName               VARCHAR(100)   NOT NULL,
        cDescription        NVARCHAR(500)  NULL,
        cScopeJson          NVARCHAR(MAX)  NOT NULL,
        cLeverType          VARCHAR(16)    NOT NULL,
        fLeverValue         DECIMAL(18,4)  NOT NULL,
        dPeriodFrom         DATE           NOT NULL,
        dPeriodTo           DATE           NOT NULL,
        cStatus             VARCHAR(16)    NOT NULL
            CONSTRAINT DF_SOScenario_cStatus DEFAULT ('DRAFT'),
        cCreatedBy          VARCHAR(50)    NOT NULL,
        dtCreatedAt         DATETIME2(7)   NOT NULL
            CONSTRAINT DF_SOScenario_dt DEFAULT (sysdatetime()),
        cApprovedBy         VARCHAR(50)    NULL,
        dtApprovedAt        DATETIME2(7)   NULL,
        cCancelReason       NVARCHAR(500)  NULL,
        dtCancelledAt       DATETIME2(7)   NULL,
        -- migration 002: lever unit + the Targets-screen level it was set on
        cMeasure            VARCHAR(8)     NOT NULL
            CONSTRAINT DF_SOScenario_cMeasure DEFAULT ('UNITS'),
        cAnchorLevel        VARCHAR(16)    NULL,
        CONSTRAINT PK_SOScenario PRIMARY KEY CLUSTERED (idScenario),
        CONSTRAINT CK_SOScenario_Measure CHECK (cMeasure IN ('UNITS', 'LITRES')),
        CONSTRAINT CK_SOScenario_LeverType CHECK
            (cLeverType IN ('PERCENT','ABSOLUTE_DELTA','FIXED_QTY')),
        CONSTRAINT CK_SOScenario_Status CHECK
            (cStatus IN ('DRAFT','PREVIEW','ACTIVE','COMPLETED','CANCELLED')),
        CONSTRAINT CK_SOScenario_PeriodOrder CHECK
            (dPeriodTo >= dPeriodFrom)
    );
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOScenarioTarget  (materialised leaf target -- one row per
-- customer x SKU x week after scenarios and overrides have been applied
-- to the active forecast). This is what the rep ultimately sees as
-- "target" and what we measure actuals against.
--
-- fForecastQty is a SNAPSHOT taken at materialisation time. Even if the
-- underlying forecast re-runs and changes, the target's forecast basis
-- does not shift (goalposts don't move). bIsLocked flips true when the
-- week starts; after that any override requires an explicit break-lock
-- action with reason.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOScenarioTarget', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOScenarioTarget (
        idScenarioTarget      BIGINT IDENTITY(1,1) NOT NULL,
        cCustomerAccount      VARCHAR(20)   NOT NULL,
        cStockCode            VARCHAR(20)   NOT NULL,
        dWeekStart            DATE          NOT NULL,
        -- Denormalized dims (snapshot)
        cRoute                VARCHAR(100)  NULL,
        cDistCentre           VARCHAR(100)  NULL,
        cCategory             VARCHAR(100)  NULL,
        -- Forecast basis (snapshot)
        idForecastSnapshot    BIGINT        NULL,
        fForecastQty          DECIMAL(18,4) NOT NULL,
        -- Scenarios applied (JSON array of scenario ids)
        cScenariosAppliedJson NVARCHAR(500) NULL,
        -- Resulting target
        fTargetQty            DECIMAL(18,4) NOT NULL,
        cSource               VARCHAR(16)   NOT NULL,
        -- Manual override
        fOverrideQty          DECIMAL(18,4) NULL,
        cOverrideReason       NVARCHAR(500) NULL,
        cOverrideBy           VARCHAR(50)   NULL,
        dtOverrideAt          DATETIME2(7)  NULL,
        -- Reasonableness flags
        bIsFlagged            BIT           NOT NULL
            CONSTRAINT DF_SOScenarioTarget_bFlag DEFAULT (0),
        cFlagReasonsJson      NVARCHAR(500) NULL,
        -- Lock
        bIsLocked             BIT           NOT NULL
            CONSTRAINT DF_SOScenarioTarget_bLock DEFAULT (0),
        dtLockedAt            DATETIME2(7)  NULL,
        dtCreatedAt           DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SOScenarioTarget_dtCreate DEFAULT (sysdatetime()),
        dtUpdatedAt           DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SOScenarioTarget_dtUpdate DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOScenarioTarget PRIMARY KEY CLUSTERED (idScenarioTarget),
        CONSTRAINT UX_SOScenarioTarget_Leaf UNIQUE
            (cCustomerAccount, cStockCode, dWeekStart),
        CONSTRAINT CK_SOScenarioTarget_Source CHECK
            (cSource IN ('FORECAST','SCENARIO','OVERRIDE'))
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOScenarioTarget_WeekDC'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOScenarioTarget')
)
BEGIN
    CREATE INDEX IX_SOScenarioTarget_WeekDC
        ON [SOAssistant].dbo._tb_SOScenarioTarget (dWeekStart, cDistCentre, cCategory)
        INCLUDE (cRoute, cCustomerAccount, cStockCode, fTargetQty, fForecastQty, bIsFlagged);
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOOrderSuggestion  (CORRECTION 1: persist every suggestion shown
-- to a rep + their decision). Previously the suggested quantity was
-- computed on-the-fly in history.py:order_suggestions and never written
-- anywhere -- so we could never audit "what did we ask the rep to do
-- vs. what did they actually do".
--
-- One row per (customer, SKU, delivery date, presentation event). If
-- the rep refreshes the screen, write a new row -- lets us measure
-- "did showing them again change the decision". fAcceptedQty NULL means
-- they haven't yet decided (session abandoned).
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOOrderSuggestion', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOOrderSuggestion (
        idOrderSuggestion   BIGINT IDENTITY(1,1) NOT NULL,
        cCustomerAccount    VARCHAR(20)   NOT NULL,
        cStockCode          VARCHAR(20)   NOT NULL,
        dDeliveryDate       DATE          NOT NULL,
        dWeekStart          DATE          NOT NULL,
        fSuggestedQty       DECIMAL(18,4) NOT NULL,
        cSuggestionSource   VARCHAR(16)   NOT NULL,
        idScenarioTarget    BIGINT        NULL,
        idForecast          BIGINT        NULL,
        fTargetBasis        DECIMAL(18,4) NULL,
        fForecastBasis      DECIMAL(18,4) NULL,
        fDayShare           DECIMAL(6,4)  NULL,
        -- Rep decision
        fAcceptedQty        DECIMAL(18,4) NULL,
        cDecision           VARCHAR(16)   NULL,
        iAgentID            INT           NULL,
        cUsername           VARCHAR(64)   NOT NULL,
        cOrderNum           VARCHAR(20)   NULL,
        dtPresentedAt       DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SOOrderSuggestion_dtP DEFAULT (sysdatetime()),
        dtDecidedAt         DATETIME2(7)  NULL,
        CONSTRAINT PK_SOOrderSuggestion PRIMARY KEY CLUSTERED (idOrderSuggestion),
        CONSTRAINT CK_SOOrderSuggestion_Source CHECK
            (cSuggestionSource IN ('TARGET','FORECAST','BASE_MODEL','MANUAL')),
        CONSTRAINT CK_SOOrderSuggestion_Decision CHECK
            (cDecision IS NULL OR cDecision IN
             ('ACCEPTED','MODIFIED','REJECTED','MANUAL_ADD'))
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOOrderSuggestion_LeafWeek'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOOrderSuggestion')
)
BEGIN
    CREATE INDEX IX_SOOrderSuggestion_LeafWeek
        ON [SOAssistant].dbo._tb_SOOrderSuggestion
           (cCustomerAccount, cStockCode, dWeekStart)
        INCLUDE (fSuggestedQty, fAcceptedQty, cDecision, dtPresentedAt);
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOStockoutEvent  (CORRECTION 3: record when a SKU was unavailable
-- at a warehouse so we can distinguish "didn't sell" from "couldn't
-- sell"). Overlaps with _tb_SOShortDelivery -- an event here says the
-- stock was out; a shortfall there says a specific order got clipped.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOStockoutEvent', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOStockoutEvent (
        idStockoutEvent     BIGINT IDENTITY(1,1) NOT NULL,
        cStockCode          VARCHAR(20)   NOT NULL,
        cWarehouseCode      VARCHAR(10)   NULL,
        dStartDate          DATE          NOT NULL,
        dEndDate            DATE          NULL,
        cNotes              NVARCHAR(500) NULL,
        cRecordedBy         VARCHAR(50)   NOT NULL,
        dtCreatedAt         DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SOStockoutEvent_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOStockoutEvent PRIMARY KEY CLUSTERED (idStockoutEvent),
        CONSTRAINT CK_SOStockoutEvent_DateOrder CHECK
            (dEndDate IS NULL OR dEndDate >= dStartDate)
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOStockoutEvent_StockDate'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOStockoutEvent')
)
BEGIN
    CREATE INDEX IX_SOStockoutEvent_StockDate
        ON [SOAssistant].dbo._tb_SOStockoutEvent (cStockCode, dStartDate)
        INCLUDE (dEndDate, cWarehouseCode);
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOShortDelivery  (CORRECTION 3: per-order shortfall records).
-- Captured at delivery time -- "we tried to deliver 150, only 90 made
-- it". The delta feeds the "estimated true demand" uplift on the
-- forecast actual row so the model trains on demand, not clipped sales.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOShortDelivery', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOShortDelivery (
        idShortDelivery     BIGINT IDENTITY(1,1) NOT NULL,
        cCustomerAccount    VARCHAR(20)   NOT NULL,
        cStockCode          VARCHAR(20)   NOT NULL,
        cOrderNum           VARCHAR(20)   NULL,
        dDeliveryDate       DATE          NOT NULL,
        fOrderedQty         DECIMAL(18,4) NOT NULL,
        fDeliveredQty       DECIMAL(18,4) NOT NULL,
        fShortfallQty       DECIMAL(18,4) NOT NULL,
        cReason             VARCHAR(32)   NOT NULL,
        cNotes              NVARCHAR(500) NULL,
        cRecordedBy         VARCHAR(50)   NOT NULL,
        dtCreatedAt         DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SOShortDelivery_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOShortDelivery PRIMARY KEY CLUSTERED (idShortDelivery),
        CONSTRAINT CK_SOShortDelivery_Reason CHECK
            (cReason IN ('STOCKOUT','DAMAGED','CUSTOMER_REFUSED','OTHER')),
        CONSTRAINT CK_SOShortDelivery_QtyPositive CHECK
            (fOrderedQty > 0 AND fDeliveredQty >= 0 AND fShortfallQty >= 0)
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOShortDelivery_LeafDate'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOShortDelivery')
)
BEGIN
    CREATE INDEX IX_SOShortDelivery_LeafDate
        ON [SOAssistant].dbo._tb_SOShortDelivery
           (cCustomerAccount, cStockCode, dDeliveryDate)
        INCLUDE (fShortfallQty, cReason);
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOForecastActual  (the closed-loop fact -- one row per
-- customer x SKU x week with ALL four measurements + the derived error
-- metrics). Written by the nightly job that joins forecast / target /
-- suggestion / actual.
--
-- CORRECTION 2: four separate error signals, each named for what it
-- actually measures. Do not collapse into a single "accuracy" number.
--   fForecastError / fForecastMAPE      -- model quality
--   fTargetAchievementPct               -- strategy realism (not MAPE)
--   fSuggestedAcceptanceRate            -- rep execution
--   fTargetAggression                   -- how aggressive the overlay
--
-- CORRECTION 3: fDeliveredQty (what sales shows) vs
-- fEstimatedTrueDemand (uplifted when stockout clipped demand). Models
-- train on true demand; sales dashboards show delivered.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOForecastActual', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOForecastActual (
        idForecastActual         BIGINT IDENTITY(1,1) NOT NULL,
        cCustomerAccount         VARCHAR(20)   NOT NULL,
        cStockCode               VARCHAR(20)   NOT NULL,
        dWeekStart               DATE          NOT NULL,
        -- Denormalized dims
        cRoute                   VARCHAR(100)  NULL,
        cDistCentre              VARCHAR(100)  NULL,
        cCategory                VARCHAR(100)  NULL,
        -- The four measurements
        fForecastQty             DECIMAL(18,4) NULL,
        idForecastModel          INT           NULL,
        idForecastSnapshot       BIGINT        NULL,
        fTargetQty               DECIMAL(18,4) NULL,
        idScenarioTargetSnapshot BIGINT        NULL,
        fSuggestedQtySum         DECIMAL(18,4) NULL,
        fAcceptedQtySum          DECIMAL(18,4) NULL,
        fCommittedQty            DECIMAL(18,4) NULL,
        fDeliveredQty            DECIMAL(18,4) NULL,
        -- Correction 3: short delivery + true demand
        fShortDeliveredQty       DECIMAL(18,4) NOT NULL
            CONSTRAINT DF_SOForecastActual_Short DEFAULT (0),
        fEstimatedTrueDemand     DECIMAL(18,4) NULL,
        bHadStockout             BIT           NOT NULL
            CONSTRAINT DF_SOForecastActual_Stockout DEFAULT (0),
        -- Correction 2: four separate error signals
        fForecastError           DECIMAL(18,4) NULL,
        fForecastMAPE            DECIMAL(18,4) NULL,
        fTargetAchievementPct    DECIMAL(18,4) NULL,
        fSuggestedAcceptanceRate DECIMAL(18,4) NULL,
        fTargetAggression        DECIMAL(18,4) NULL,
        dtClosedAt               DATETIME2(7)  NULL,
        dtCreatedAt              DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SOForecastActual_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOForecastActual PRIMARY KEY CLUSTERED (idForecastActual),
        CONSTRAINT UX_SOForecastActual_Leaf UNIQUE
            (cCustomerAccount, cStockCode, dWeekStart)
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOForecastActual_WeekDC'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOForecastActual')
)
BEGIN
    CREATE INDEX IX_SOForecastActual_WeekDC
        ON [SOAssistant].dbo._tb_SOForecastActual (dWeekStart, cDistCentre, cCategory)
        INCLUDE (cRoute, cCustomerAccount, cStockCode, fDeliveredQty,
                 fTargetQty, fForecastQty, bHadStockout);
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOForecastScorecard  (nightly roll-up -- per-model performance
-- metrics at various hierarchy levels over a trailing window). Feeds
-- the model leaderboard UI and the "which model wins per pattern"
-- diagnostic. Scope columns are NULLable; NULLs on a level mean "all"
-- (so cDistCentre='WC' + cCategory='Milk' + others NULL = DC-WC Milk
-- rollup).
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOForecastScorecard', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOForecastScorecard (
        idScorecard         BIGINT IDENTITY(1,1) NOT NULL,
        idForecastModel     INT           NOT NULL,
        cScopeLevel         VARCHAR(16)   NOT NULL,
        cCustomerAccount    VARCHAR(20)   NULL,
        cStockCode          VARCHAR(20)   NULL,
        cRoute              VARCHAR(100)  NULL,
        cDistCentre         VARCHAR(100)  NULL,
        cCategory           VARCHAR(100)  NULL,
        cDemandPattern      VARCHAR(16)   NULL,
        dEvalFrom           DATE          NOT NULL,
        dEvalTo             DATE          NOT NULL,
        iLeafsScored        INT           NOT NULL,
        iLeafsWithActual    INT           NOT NULL,
        fWeightedMAPE       DECIMAL(18,4) NULL,
        fMAE                DECIMAL(18,4) NULL,
        fBias               DECIMAL(18,4) NULL,
        dtComputedAt        DATETIME2(7)  NOT NULL
            CONSTRAINT DF_SOForecastScorecard_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOForecastScorecard PRIMARY KEY CLUSTERED (idScorecard),
        CONSTRAINT CK_SOForecastScorecard_Level CHECK
            (cScopeLevel IN ('LEAF','ROUTE','DC','CATEGORY','PATTERN','COMPANY'))
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOForecastScorecard_ModelLevel'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOForecastScorecard')
)
BEGIN
    CREATE INDEX IX_SOForecastScorecard_ModelLevel
        ON [SOAssistant].dbo._tb_SOForecastScorecard
           (idForecastModel, cScopeLevel, dEvalTo DESC)
        INCLUDE (cDistCentre, cCategory, cDemandPattern,
                 fWeightedMAPE, fMAE, fBias, iLeafsScored);
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOTask  (weekly opportunities for reps -- auto-generated by the
-- opportunity-detection rules). One row per task; dStatus tracks the
-- lifecycle. Scope is light JSON (plus explicit customer / stock /
-- category / route columns for cheap filtering).
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOTask', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOTask (
        idTask              BIGINT IDENTITY(1,1) NOT NULL,
        cTaskType           VARCHAR(32)    NOT NULL,
        cPriority           VARCHAR(8)     NOT NULL,
        cAssignedToUsername VARCHAR(64)    NULL,
        iAssignedToAgentID  INT            NULL,
        cCustomerAccount    VARCHAR(20)    NULL,
        cStockCode          VARCHAR(20)    NULL,
        cCategory           VARCHAR(100)   NULL,
        cRoute              VARCHAR(100)   NULL,
        cDistCentre         VARCHAR(100)   NULL,
        idScenario          INT            NULL,
        cTitle              NVARCHAR(200)  NOT NULL,
        cDescription        NVARCHAR(1000) NULL,
        dDueDate            DATE           NOT NULL,
        cStatus             VARCHAR(16)    NOT NULL
            CONSTRAINT DF_SOTask_cStatus DEFAULT ('OPEN'),
        cResolutionNotes    NVARCHAR(1000) NULL,
        cResolvedBy         VARCHAR(64)    NULL,
        dtCreatedAt         DATETIME2(7)   NOT NULL
            CONSTRAINT DF_SOTask_dtCreate DEFAULT (sysdatetime()),
        dtResolvedAt        DATETIME2(7)   NULL,
        CONSTRAINT PK_SOTask PRIMARY KEY CLUSTERED (idTask),
        CONSTRAINT CK_SOTask_Priority CHECK
            (cPriority IN ('HIGH','MED','LOW')),
        CONSTRAINT CK_SOTask_Status CHECK
            (cStatus IN ('OPEN','IN_PROGRESS','SNOOZED','RESOLVED','DISMISSED')),
        CONSTRAINT CK_SOTask_Type CHECK
            (cTaskType IN
             ('VISIT_CUSTOMER','INVESTIGATE_SHORTFALL','RESCUE_SHORTFALL',
              'REACTIVATE_CUSTOMER','NEW_PRODUCT_PUSH','OFFER_SUBSTITUTE',
              'ESCALATE_CREDIT','UPSELL_OPPORTUNITY','RIGHT_SIZE_ORDER',
              'REVIEW_FLAGGED_TARGET','REVIEW_FORECAST_DRIFT'))
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOTask_AssignedOpen'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOTask')
)
BEGIN
    CREATE INDEX IX_SOTask_AssignedOpen
        ON [SOAssistant].dbo._tb_SOTask (cAssignedToUsername, dDueDate)
        INCLUDE (cTaskType, cPriority, cCustomerAccount, cTitle)
        WHERE cStatus IN ('OPEN','IN_PROGRESS');
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOForecastAudit  (append-only audit log for every scenario /
-- target / model-promotion change). Matches the before/after pattern
-- used in _tb_SOCustomerAssignmentAudit but keeps the state blobs as
-- JSON for flexibility -- scenarios have variable shape.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOForecastAudit', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOForecastAudit (
        idForecastAudit     BIGINT IDENTITY(1,1) NOT NULL,
        cObjectType         VARCHAR(32)    NOT NULL,
        iObjectId           BIGINT         NOT NULL,
        cAction             VARCHAR(32)    NOT NULL,
        cPrevStateJson      NVARCHAR(MAX)  NULL,
        cNewStateJson       NVARCHAR(MAX)  NULL,
        cUsername           VARCHAR(64)    NOT NULL,
        iAgentID            INT            NULL,
        cAppVersion         VARCHAR(32)    NULL,
        dtActionAt          DATETIME2(7)   NOT NULL
            CONSTRAINT DF_SOForecastAudit_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOForecastAudit PRIMARY KEY CLUSTERED (idForecastAudit),
        CONSTRAINT CK_SOForecastAudit_ObjectType CHECK
            (cObjectType IN
             ('SCENARIO','SCENARIO_TARGET','OVERRIDE','MODEL_PROMOTION',
              'FORECAST_RUN','STOCKOUT','SHORT_DELIVERY'))
    );
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOForecastAudit_Object'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOForecastAudit')
)
BEGIN
    CREATE INDEX IX_SOForecastAudit_Object
        ON [SOAssistant].dbo._tb_SOForecastAudit (cObjectType, iObjectId, dtActionAt DESC)
        INCLUDE (cAction, cUsername);
END;
GO

-- =============================================================================
-- Model registry seed (production data -- not test fixtures). Idempotent
-- via MERGE: inserts rows missing by cCode, updates descriptions in
-- place, leaves existing rows alone.
-- =============================================================================

MERGE [SOAssistant].dbo._tb_SOForecastModel AS tgt
USING (VALUES
    ('BASE_MEDIAN_6W',
     'Base: 6-week non-zero median',
     'base',
     'Current production algorithm. Median of non-zero weekly qtys over '
     + 'last 6 completed weeks (Mon-Sun). Requires >= 3 non-zero weeks to '
     + 'produce a prediction. Snaps to pack size downstream. Replicates '
     + 'history.py:order_suggestions.',
     '{"window_weeks":6,"min_non_zero_weeks":3,"round":"half_up"}'),

    ('SEASONAL_NAIVE_YOY',
     'Seasonal naive + YoY trend',
     'statistical',
     'Prediction = same_week_last_year * (sum last 16 weeks / sum same '
     + '16 weeks prior year). Captures seasonality + recent trend. '
     + 'Requires full year of history for naive component.',
     '{"trend_window_weeks":16,"require_prior_year":true}'),

    ('CROSTON_SBA',
     'Croston''s method / Syntetos-Boylan Approximation',
     'intermittent',
     'For intermittent demand patterns (ADI >= 1.32). Decomposes series '
     + 'into demand-size and inter-arrival-interval components, '
     + 'exponentially smooths each. SBA variant removes known positive '
     + 'bias of plain Croston. Default alpha = 0.1.',
     '{"alpha":0.1,"variant":"sba"}'),

    ('LIGHTGBM_PANEL',
     'Panel LightGBM with engineered features',
     'ml',
     'One global LightGBM model trained on all (customer x SKU x week) '
     + 'rows. Features: lag_1,2,4,52; rolling means; category / DC / '
     + 'route embeddings; week-of-year; holiday flag; launch-week-number. '
     + 'Not shipped in Phase 2; registered for the leaderboard shape.',
     '{"n_estimators":500,"learning_rate":0.05,"num_leaves":63}'),

    ('ENSEMBLE_INVERSE_MAPE',
     'Ensemble (inverse-MAPE weighted)',
     'ensemble',
     'Weighted blend of enabled models where each weight is 1 / (model''s '
     + '90-day MAPE on this leaf). Falls back to equal weights when any '
     + 'component has no scored history for the leaf.',
     '{"blend_window_days":90,"require_min_scored":4}')
) AS src (cCode, cName, cFamily, cDescription, cParamsJson)
ON tgt.cCode = src.cCode
WHEN NOT MATCHED BY TARGET THEN
    INSERT (cCode, cName, cFamily, cDescription, cParamsJson, bEnabled)
    VALUES (src.cCode, src.cName, src.cFamily, src.cDescription,
            src.cParamsJson, 1)
WHEN MATCHED THEN
    UPDATE SET cName = src.cName,
               cFamily = src.cFamily,
               cDescription = src.cDescription,
               cParamsJson = src.cParamsJson;
GO

-- Only BASE is enabled by default; the others get flipped on in Phase 2
-- when the Python implementations land. Prevents a half-built shelf
-- from writing partial predictions to the fact tables.
UPDATE [SOAssistant].dbo._tb_SOForecastModel
   SET bEnabled = CASE WHEN cCode = 'BASE_MEDIAN_6W' THEN 1 ELSE 0 END
 WHERE cCode IN ('BASE_MEDIAN_6W','SEASONAL_NAIVE_YOY','CROSTON_SBA',
                 'LIGHTGBM_PANEL','ENSEMBLE_INVERSE_MAPE');
GO

PRINT 'forecasting_schema.sql completed.';
GO
