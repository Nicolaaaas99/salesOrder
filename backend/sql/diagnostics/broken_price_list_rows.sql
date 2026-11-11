/******************************************************************************
 Broken price-list rows — stored incl doesn't match excl + the item's VAT.

 Read-only. Run by hand (SSMS) whenever the price list needs a clean-up; it
 is never run by the app.

 Same rule the app uses to show "check price list" on an order line
 (backend services/pricing.price_list_row_broken):
   correct incl = ROUND(excl x (1 + item VAT%), 2)
   broken       = |stored incl - correct incl| > R0.02   (or no incl at all)

 Why it matters: when an order is keyed in Evolution in INCLUSIVE mode,
 Evolution charges the STORED incl. The app charges the correct incl, so
 app orders are fine — but orders keyed directly in Evolution are not.
   * Evolution OVERcharges  -> price claim risk. Fix these first.
   * Evolution UNDERcharges -> lost revenue (e.g. MOO099: R25.90 instead of
                               R29.79).

 Scope: your two DCs (48 = 01.HOF_MAK, 49 = 01.HOF_POL) and only price
 lists that are assigned to at least one customer. Edit @Whse to change.

 Result sets:
   1. Summary per price list and warehouse.
   2. Every broken row, overcharges first, then largest difference.
   3. Corporate PO prices (Order Import) that disagree with our price —
      one row per customer group + item, largest difference first. Needs
      _tb_SOImportPriceDecision (create_views.sql); skipped until then.
******************************************************************************/
USE [BA_00_LPD_CON];
SET NOCOUNT ON;

DECLARE @Whse TABLE (WhseLink int PRIMARY KEY);
INSERT INTO @Whse VALUES (48), (49);

DROP TABLE IF EXISTS #broken;
SELECT PriceList   = PN.cName,
       Whse        = WH.Code,
       StockCode   = STK.Code,
       Description = STK.Description_1,
       StoredExcl  = CAST(PR.fExclPrice AS decimal(19, 2)),
       StoredIncl  = CAST(PR.fInclPrice AS decimal(19, 2)),
       VatPct      = CAST(T.TaxRate AS decimal(9, 2)),
       CorrectIncl = C.CorrectIncl,
       Diff        = CAST(PR.fInclPrice AS decimal(19, 6)) - C.CorrectIncl,
       Effect      = CASE WHEN CAST(PR.fInclPrice AS decimal(19, 6)) > C.CorrectIncl
                          THEN 'Evolution OVERCHARGES'
                          ELSE 'Evolution undercharges' END,
       Customers   = (SELECT COUNT(*) FROM dbo.Client CL
                      WHERE CL.iARPriceListNameID = PR.iPriceListNameID)
INTO #broken
FROM dbo._etblPriceListPrices PR
JOIN dbo._etblPriceListName PN ON PN.IDPriceListName = PR.iPriceListNameID
JOIN dbo.StkItem STK           ON STK.StockLink      = PR.iStockID
JOIN dbo.WhseMst WH            ON WH.WhseLink        = PR.iWarehouseID
-- Item's own VAT rate: master stock-details row, same as the app.
CROSS APPLY (
    SELECT TOP 1 TX.TaxRate
    FROM dbo._etblStockDetails DET
    JOIN dbo.TaxRate TX ON TX.idTaxRate = DET.TTInvID
    WHERE DET.StockID = STK.StockLink AND DET.WhseID < 1
) T
CROSS APPLY (
    SELECT CorrectIncl = ROUND(CAST(PR.fExclPrice AS decimal(19, 6))
                               * (1 + CAST(T.TaxRate AS decimal(9, 4)) / 100), 2)
) C
WHERE PR.fExclPrice > 0
  AND STK.ItemActive = 1
  AND PR.iWarehouseID IN (SELECT WhseLink FROM @Whse)
  AND EXISTS (SELECT 1 FROM dbo.Client CL WHERE CL.iARPriceListNameID = PR.iPriceListNameID)
  AND ABS(CAST(ISNULL(PR.fInclPrice, 0) AS decimal(19, 6)) - C.CorrectIncl) > 0.02;

-- 1. Summary
SELECT PriceList, Whse,
       BrokenRows    = COUNT(*),
       Overcharges   = SUM(CASE WHEN Diff > 0 THEN 1 ELSE 0 END),
       Undercharges  = SUM(CASE WHEN Diff < 0 THEN 1 ELSE 0 END),
       LargestDiff   = MAX(ABS(Diff)),
       Customers     = MAX(Customers)
FROM #broken
GROUP BY PriceList, Whse
ORDER BY Overcharges DESC, BrokenRows DESC;

-- 2. The rows to fix
SELECT Effect, PriceList, Whse, StockCode, Description,
       StoredExcl, StoredIncl, VatPct, CorrectIncl,
       Diff = CAST(Diff AS decimal(19, 2)), Customers
FROM #broken
ORDER BY CASE WHEN Diff > 0 THEN 0 ELSE 1 END, ABS(Diff) DESC, PriceList, StockCode;

DROP TABLE #broken;

-- =============================================================================
-- 3. Corporate PO prices that disagree with our price.
--
-- Every imported order logs the lines whose PO price differed from ours
-- (_tb_SOImportPriceDecision). One row per customer group + item +
-- warehouse, largest difference first:
--   OurPriceThen / POPrice  what the last import saw
--   OurPriceNow             the customer's price list incl today (NULL when
--                           the item is on a deal or not on their list)
--   Status                  'FIX: set the list incl to the PO price' while
--                           our list still disagrees with the latest PO
--   ChargedPO / Ours / Typed  how often each price was charged
-- Fixing the price list stops the line being flagged on the next import.
-- NOTE: OurPriceNow / Status here read the PRICE LIST only. The app's Price
-- review tab prices each row as an import would (deals included) and shows
-- where the price comes from; use that when a deal may be running.
-- =============================================================================
IF OBJECT_ID('[SOAssistant].dbo._tb_SOImportPriceDecision', 'U') IS NULL
BEGIN
    PRINT 'Section 3 skipped: _tb_SOImportPriceDecision not created yet (run create_views.sql).';
    RETURN;
END;

WITH D AS (
    SELECT PD.*,
           rn = ROW_NUMBER() OVER (PARTITION BY PD.cScope, PD.cStockCode, PD.cWhseCode
                                   ORDER BY PD.dtCreatedAt DESC, PD.idPriceDecision DESC)
    FROM [SOAssistant].dbo._tb_SOImportPriceDecision PD
),
Agg AS (
    SELECT cScope, cStockCode, cWhseCode,
           Lines        = COUNT(*),
           ChargedPO    = SUM(CASE WHEN cChoice = 'PO' THEN 1 ELSE 0 END),
           ChargedOurs  = SUM(CASE WHEN cChoice = 'EVOLUTION' THEN 1 ELSE 0 END),
           ChargedTyped = SUM(CASE WHEN cChoice = 'OVERRIDE' THEN 1 ELSE 0 END),
           FirstSeen    = MIN(dtCreatedAt)
    FROM [SOAssistant].dbo._tb_SOImportPriceDecision
    GROUP BY cScope, cStockCode, cWhseCode
)
SELECT Status       = CASE
                        WHEN NowP.fInclPrice IS NULL THEN 'Check: not on the price list (deal?)'
                        WHEN ABS(NowP.fInclPrice - L.fPOIncl) <= 0.005 THEN 'Fixed: list now matches the PO'
                        ELSE 'FIX: set the list incl to the PO price' END,
       CustomerGroup = REPLACE(REPLACE(L.cScope, 'GROUP:', ''), 'ACCOUNT:', ''),
       L.cStockCode  AS StockCode,
       STK.Description_1 AS Description,
       L.cWhseCode   AS Whse,
       PriceList     = PN.cName,
       L.cPriceSource AS SourceThen,
       OurPriceThen  = CAST(L.fEvoIncl AS decimal(19, 2)),
       POPrice       = CAST(L.fPOIncl  AS decimal(19, 2)),
       Diff          = CAST(L.fPOIncl - L.fEvoIncl AS decimal(19, 2)),
       OurPriceNow   = CAST(NowP.fInclPrice AS decimal(19, 2)),
       LastPO        = L.cPONumber,
       LastOrder     = L.cOrderNum,
       LastSeen      = CAST(L.dtCreatedAt AS date),
       A.FirstSeen, A.Lines, A.ChargedPO, A.ChargedOurs, A.ChargedTyped
FROM D L
JOIN Agg A ON A.cScope = L.cScope AND A.cStockCode = L.cStockCode
          AND ISNULL(A.cWhseCode, '') = ISNULL(L.cWhseCode, '')
LEFT JOIN dbo.Client CL   ON CL.Account = L.cCustomerAccount COLLATE DATABASE_DEFAULT
LEFT JOIN dbo.StkItem STK ON STK.Code   = L.cStockCode       COLLATE DATABASE_DEFAULT
LEFT JOIN dbo.WhseMst WH  ON WH.Code    = L.cWhseCode        COLLATE DATABASE_DEFAULT
LEFT JOIN dbo._etblPriceListName PN ON PN.IDPriceListName = CL.iARPriceListNameID
OUTER APPLY (
    SELECT TOP 1 PR.fInclPrice
    FROM dbo._etblPriceListPrices PR
    WHERE PR.iPriceListNameID = CL.iARPriceListNameID
      AND PR.iStockID         = STK.StockLink
      AND PR.iWarehouseID     = WH.WhseLink
      AND PR.fExclPrice > 0
) NowP
WHERE L.rn = 1
ORDER BY ABS(L.fPOIncl - L.fEvoIncl) DESC,   -- largest difference first
         L.dtCreatedAt DESC, CustomerGroup, StockCode;
