/******************************************************************************
 Pricing v2 — STAGING COPY (safe to run on the live server)
 ------------------------------------------------------------------------------
 Creates _vw_SOActiveDeals_v2 and _fn_SOCustomerPriceAsOf_v2 in [SOAssistant]:
 the new rounding from create_views.sql under different names. The app does
 NOT use these objects, so creating them changes no prices anywhere.

 They exist only so diagnostics/check_pricing_rollout.sql can compare the
 new pricing against real orders BEFORE create_views.sql is deployed.

 Must stay identical to the live objects in create_views.sql except for the
 two rounding expressions. Remove with the DROP statements at the bottom
 once the rollout is done.
******************************************************************************/
USE [SOAssistant];
GO

CREATE OR ALTER VIEW dbo._vw_SOActiveDeals_v2 AS
SELECT 'Customer Deal' AS DealType,
       CL.Account, CL.DCLink, CL.Name,
       GRP.Code AS CustGroup,
       VD.iStockID,
       VD.StockCode, VD.Description_1,
       VD.dEffDate, VD.dExpDate,
       VD.fQuantity, VD.fPriceDiscExcl, VD.fPriceDiscIncl
FROM   [BA_00_LPD_CON].dbo.Client CL
JOIN   ( SELECT IdCliClass, Code, Description FROM [BA_00_LPD_CON].dbo.Cliclass ) GRP
       ON GRP.IdCliClass = CL.iClassID
JOIN   ( SELECT VD.IDVD, VD.iARAPID,
                VDLN.iStockID,
                STK.Code AS StockCode, STK.Description_1,
                VDLN.dEffDate, VDLN.dExpDate,
                VDLN.fQuantity, VDLN.fPriceDisc
                ,CASE WHEN cEnterInclExcl = 'E' THEN  fPriceDisc
                 ELSE CAST(ROUND(CAST(fPriceDisc AS decimal(19,6)) * 100
                                 / (100 + CAST(TaxRate AS decimal(9,4))), 4) AS float) END fPriceDiscExcl
                ,CASE WHEN cEnterInclExcl = 'E'
                      THEN CAST(ROUND(CAST(fPriceDisc AS decimal(19,6))
                                      * (100 + CAST(TaxRate AS decimal(9,4))) / 100, 2) AS float)
                 ELSE fPriceDisc  END fPriceDiscIncl
         FROM   [BA_00_LPD_CON].dbo._etblVDAR VD
         JOIN   ( SELECT VDLN.IDVDLn, VDLN.iVDID,
                         VDLN.iStockID,
                         VDLN.dEffDate, VDLN.dExpDate,
                         VDLVL.fQuantity, VDLVL.fPriceDisc, VDLN.cEnterInclExcl
                  FROM   [BA_00_LPD_CON].dbo._etblVDLnAR    VDLN
                  JOIN   [BA_00_LPD_CON].dbo._etblVDLnLvlAR VDLVL
                         ON VDLVL.iVDLnID = VDLN.IDVDLn ) VDLN
                ON VDLN.iVDID = VD.IDVD
         JOIN   [BA_00_LPD_CON].dbo.StkItem STK  ON STK.StockLink = VDLN.iStockID
         JOIN   ( SELECT * FROM [BA_00_LPD_CON].dbo._etblStockDetails WHERE WhseID < 1 ) DET
                ON DET.StockID = STK.StockLink
         JOIN   [BA_00_LPD_CON].dbo.TaxRate TAX  ON TAX.idTaxRate = DET.TTInvID
       ) VD ON VD.iARAPID = CL.DCLink

UNION ALL

SELECT 'Group Deal' AS DealType,
       CL.Account, CL.DCLink, CL.Name,
       GRP.Code AS CustGroup,
       VD.iStockID,
       VD.StockCode, VD.Description_1,
       VD.dEffDate, VD.dExpDate,
       VD.fQuantity, VD.fPriceDiscExcl, VD.fPriceDiscIncl
FROM   [BA_00_LPD_CON].dbo.Client CL
JOIN   ( SELECT IdCliClass, Code, Description FROM [BA_00_LPD_CON].dbo.Cliclass ) GRP
       ON GRP.IdCliClass = CL.iClassID
JOIN   ( SELECT VD.IDVD, VD.iGroupID,
                VDLN.iStockID,
                STK.Code AS StockCode, STK.Description_1,
                VDLN.dEffDate, VDLN.dExpDate,
                VDLN.fQuantity, VDLN.fPriceDisc
                ,CASE WHEN cEnterInclExcl = 'E' THEN  fPriceDisc
                 ELSE CAST(ROUND(CAST(fPriceDisc AS decimal(19,6)) * 100
                                 / (100 + CAST(TaxRate AS decimal(9,4))), 4) AS float) END fPriceDiscExcl
                ,CASE WHEN cEnterInclExcl = 'E'
                      THEN CAST(ROUND(CAST(fPriceDisc AS decimal(19,6))
                                      * (100 + CAST(TaxRate AS decimal(9,4))) / 100, 2) AS float)
                 ELSE fPriceDisc  END fPriceDiscIncl
         FROM   [BA_00_LPD_CON].dbo._etblVDAR VD
         JOIN   ( SELECT VDLN.IDVDLn, VDLN.iVDID,
                         VDLN.iStockID,
                         VDLN.dEffDate, VDLN.dExpDate,
                         VDLVL.fQuantity, VDLVL.fPriceDisc, VDLN.cEnterInclExcl
                  FROM   [BA_00_LPD_CON].dbo._etblVDLnAR    VDLN
                  JOIN   [BA_00_LPD_CON].dbo._etblVDLnLvlAR VDLVL
                         ON VDLVL.iVDLnID = VDLN.IDVDLn ) VDLN
                ON VDLN.iVDID = VD.IDVD
         JOIN   [BA_00_LPD_CON].dbo.StkItem STK  ON STK.StockLink = VDLN.iStockID
         JOIN   ( SELECT * FROM [BA_00_LPD_CON].dbo._etblStockDetails WHERE WhseID < 1 ) DET
                ON DET.StockID = STK.StockLink
         JOIN   [BA_00_LPD_CON].dbo.TaxRate TAX  ON TAX.idTaxRate = DET.TTInvID
       ) VD ON VD.iGroupID = CL.iClassID;
GO

CREATE OR ALTER FUNCTION dbo._fn_SOCustomerPriceAsOf_v2 (@asOf date)
RETURNS TABLE
AS RETURN (
    SELECT
        CL.Account,
        PR.PriceListName,
        PR.iStockID,
        PR.StockCode,
        PR.WhseCode,
        ExclPrice   = COALESCE(VD.fPriceDiscExcl, PR.fExclPrice),
        InclPrice   = COALESCE(VD.fPriceDiscIncl, PR.fInclPrice),
        PriceSource = CASE WHEN VD.StockCode IS NOT NULL
                           THEN VD.DealType ELSE 'Price List' END
    FROM [BA_00_LPD_CON].dbo.Client CL
    JOIN [SOAssistant].dbo._vw_SOPriceListPrices PR
          ON PR.iPriceListNameID = CL.iARPriceListNameID
    OUTER APPLY (
        SELECT TOP 1 DealType, StockCode, fPriceDiscExcl, fPriceDiscIncl
        FROM [SOAssistant].dbo._vw_SOActiveDeals_v2 AD
        WHERE AD.DCLink   = CL.DCLink
          AND AD.iStockID = PR.iStockID
          AND AD.dEffDate <= @asOf
          AND AD.dExpDate >= @asOf
        ORDER BY CASE AD.DealType WHEN 'Customer Deal' THEN 1 ELSE 2 END
    ) VD
);
GO

-- The app's SQL login needs SELECT on the staging objects only if you run
-- the check script as that login; under your own SSMS login it is not needed.

/* ---- Cleanup after the rollout (run once create_views.sql is live) ----
DROP FUNCTION IF EXISTS dbo._fn_SOCustomerPriceAsOf_v2;
DROP VIEW     IF EXISTS dbo._vw_SOActiveDeals_v2;
*/
