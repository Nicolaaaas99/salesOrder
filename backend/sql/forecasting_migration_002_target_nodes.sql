-- =============================================================================
-- Forecasting migration 002 -- targets on any node (2026-11-06 rethink, Phase C)
--
--   _tb_SOScenario.cMeasure      UNITS | LITRES  -- what the lever value is in
--                                (a DC target of 250 000 L is LITRES; litres
--                                = qty x StkItem.ulIISize). Default UNITS so
--                                existing scenarios behave exactly as before.
--   _tb_SOScenario.cAnchorLevel  the Targets-screen level the target was set
--                                on (company / dist_centre / route / customer /
--                                category / sku). Informational -- the scope
--                                JSON is still what decides the customer-products.
--
-- Idempotent: safe to run more than once. Run in SSMS against [SOAssistant].
-- =============================================================================
USE [SOAssistant];
GO

IF COL_LENGTH('dbo._tb_SOScenario', 'cMeasure') IS NULL
BEGIN
    ALTER TABLE dbo._tb_SOScenario
        ADD cMeasure VARCHAR(8) NOT NULL
            CONSTRAINT DF_SOScenario_cMeasure DEFAULT ('UNITS');
END;
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_SOScenario_Measure')
BEGIN
    ALTER TABLE dbo._tb_SOScenario
        ADD CONSTRAINT CK_SOScenario_Measure CHECK (cMeasure IN ('UNITS', 'LITRES'));
END;
GO

IF COL_LENGTH('dbo._tb_SOScenario', 'cAnchorLevel') IS NULL
BEGIN
    ALTER TABLE dbo._tb_SOScenario ADD cAnchorLevel VARCHAR(16) NULL;
END;
GO
