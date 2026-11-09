-- =============================================================================
-- Forecasting migration 001 -- forecast table keys (2026-11-03)
--
-- Run once in SSMS against the server hosting [SOAssistant]. Idempotent:
-- safe to re-run; every step checks before it changes anything.
--
-- 1. UX_SOForecast_RunLeaf was UNIQUE (run, customer, SKU, week). The
--    engine writes ONE ROW PER ENABLED MODEL per leaf-week (shadow
--    predictions for the leaderboard), so enabling any second model
--    would fail on insert. Replace it with
--    UX_SOForecast_RunModelLeaf (run, model, customer, SKU, week).
--
-- 2. Enforce design cornerstone "exactly ONE active forecast per
--    (customer, SKU, week)" in the database with a filtered unique index.
--    Superseded active rows from older runs are deactivated first (latest
--    run wins -- the same rule the API uses), so this can't fail on old
--    data. The runner already deactivates the previous run's row before
--    inserting a new active one, in the same transaction.
--
-- Rollback (if ever needed):
--   DROP INDEX UX_SOForecast_OneActive ON [SOAssistant].dbo._tb_SOForecast;
--   ALTER TABLE [SOAssistant].dbo._tb_SOForecast DROP CONSTRAINT UX_SOForecast_RunModelLeaf;
--   ALTER TABLE [SOAssistant].dbo._tb_SOForecast ADD CONSTRAINT UX_SOForecast_RunLeaf
--       UNIQUE (idForecastRun, cCustomerAccount, cStockCode, dWeekStart);
-- =============================================================================

USE [SOAssistant];
GO

SET XACT_ABORT ON;
BEGIN TRANSACTION;

-- 1. Run key includes the model ------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.key_constraints
                WHERE name = 'UX_SOForecast_RunModelLeaf'
                  AND parent_object_id = OBJECT_ID('dbo._tb_SOForecast'))
BEGIN
    ALTER TABLE dbo._tb_SOForecast
        ADD CONSTRAINT UX_SOForecast_RunModelLeaf
        UNIQUE (idForecastRun, idForecastModel, cCustomerAccount, cStockCode, dWeekStart);
    PRINT 'Added UX_SOForecast_RunModelLeaf';
END;

IF EXISTS (SELECT 1 FROM sys.key_constraints
            WHERE name = 'UX_SOForecast_RunLeaf'
              AND parent_object_id = OBJECT_ID('dbo._tb_SOForecast'))
BEGIN
    ALTER TABLE dbo._tb_SOForecast DROP CONSTRAINT UX_SOForecast_RunLeaf;
    PRINT 'Dropped UX_SOForecast_RunLeaf';
END;

-- 2. One active forecast per leaf-week ------------------------------------------
;WITH ranked AS (
    SELECT bIsActive,
           ROW_NUMBER() OVER (
               PARTITION BY cCustomerAccount, cStockCode, dWeekStart
               ORDER BY idForecastRun DESC, idForecast DESC) AS rn
      FROM dbo._tb_SOForecast
     WHERE bIsActive = 1
)
UPDATE ranked SET bIsActive = 0 WHERE rn > 1;
PRINT CONCAT('Deactivated superseded active rows: ', @@ROWCOUNT);

IF NOT EXISTS (SELECT 1 FROM sys.indexes
                WHERE name = 'UX_SOForecast_OneActive'
                  AND object_id = OBJECT_ID('dbo._tb_SOForecast'))
BEGIN
    CREATE UNIQUE INDEX UX_SOForecast_OneActive
        ON dbo._tb_SOForecast (cCustomerAccount, cStockCode, dWeekStart)
        WHERE bIsActive = 1;
    PRINT 'Created UX_SOForecast_OneActive';
END;

COMMIT TRANSACTION;
GO

-- Verify: expect UX_SOForecast_RunModelLeaf + UX_SOForecast_OneActive,
-- and no UX_SOForecast_RunLeaf.
SELECT name, is_unique, has_filter, filter_definition
  FROM sys.indexes
 WHERE object_id = OBJECT_ID('dbo._tb_SOForecast')
 ORDER BY name;
GO
