/******************************************************************************
 Order Import — why does Evolution's price differ from the purchase order?
 ------------------------------------------------------------------------------
 READ-ONLY. Run in SSMS. Only temp tables are created.

 Fill in the four variables, run, and read the result sets top to bottom:
   1. The customer, its price list and group.
   2. Price-list rows for each item (every warehouse; your DC marked).
   3. Every deal row (customer + group) for each item, with:
        - RawPrice      what was captured in Evolution
        - CapturedAs    Incl or Excl
        - ViewExcl/Incl what _vw_SOActiveDeals turns it into (float + FLOOR)
        - ExactExcl/Incl the same without any rounding
        - FloatNoise    YES when FLOOR cut a cent only because of float
                        noise (e.g. 16.00 stored as 15.99999999)
   4. Per item: what the app resolves (_fn_SOCustomerPriceAsOf, the same
      function the import uses), what Evolution will CHARGE (saved excl
      price + VAT), the PO price, and a verdict.

 How the app prices a line (important for reading the results):
   The order line is saved with the EXCL price. Evolution adds VAT on top,
   so the customer is charged ROUND(excl x 1.15, 2) per unit. The incl
   price the app shows is display-only. Price-list items whose stored excl
   and incl disagree (e.g. R10.07 excl with R11.59 incl) are charged
   excl + VAT (R11.58).
******************************************************************************/
USE [BA_00_LPD_CON];
SET NOCOUNT ON;

DECLARE @Account  varchar(20) = 'BOX07D';        -- customer
DECLARE @AsOf     date        = '2026-11-09';    -- delivery date (deals are dated)
DECLARE @Whse     varchar(20) = '01.HOF_MAK';    -- your default DC
-- Items to check as  code=PO UnitCost (Incl) , ...   (price may be left out: '311,321')
DECLARE @PoPrices varchar(max) = '311=14.10,321=14.10,326=14.10,620=11.60,161=18.40';


DECLARE @DCLink int, @ClassID int, @PriceListID int;
SELECT @DCLink = DCLink, @ClassID = iClassID, @PriceListID = iARPriceListNameID
FROM dbo.Client WHERE Account = @Account;

IF @DCLink IS NULL
BEGIN
    RAISERROR('Customer %s not found.', 16, 1, @Account);
    RETURN;
END;

-- ---------------------------------------------------------------------------
-- 1. Customer
-- ---------------------------------------------------------------------------
SELECT CL.Account, CL.Name, CL.DCLink, CL.ucARStoreCode AS StoreCode,
       GRP.Code AS CustGroup, CL.iClassID, PN.cName AS PriceList, CL.iARPriceListNameID
FROM dbo.Client CL
LEFT JOIN dbo.Cliclass GRP           ON GRP.IdCliClass      = CL.iClassID
LEFT JOIN dbo._etblPriceListName PN  ON PN.IDPriceListName  = CL.iARPriceListNameID
WHERE CL.DCLink = @DCLink;

-- Items + PO prices + the item's tax rate (same source as _vw_SOActiveDeals).
DROP TABLE IF EXISTS #stk;
SELECT P.StockCode, P.PoIncl, STK.StockLink, STK.Description_1, TX.TaxRate
INTO #stk
FROM (
    SELECT StockCode = LTRIM(RTRIM(LEFT(value, CHARINDEX('=', value + '=') - 1))),
           PoIncl    = TRY_CAST(NULLIF(LTRIM(RTRIM(
                           SUBSTRING(value, CHARINDEX('=', value + '=') + 1, 50))), '')
                       AS decimal(19, 4))
    FROM STRING_SPLIT(@PoPrices, ',')
    WHERE LTRIM(RTRIM(value)) <> ''
) P
LEFT JOIN dbo.StkItem STK ON STK.Code = P.StockCode COLLATE DATABASE_DEFAULT
OUTER APPLY (
    SELECT TOP 1 CAST(T.TaxRate AS decimal(9, 4)) AS TaxRate
    FROM dbo._etblStockDetails DET
    JOIN dbo.TaxRate T ON T.idTaxRate = DET.TTInvID
    WHERE DET.StockID = STK.StockLink AND DET.WhseID < 1
) TX;

-- ---------------------------------------------------------------------------
-- 2. Price list rows (the fallback when no deal is active)
-- ---------------------------------------------------------------------------
SELECT S.StockCode, WH.Code AS Whse,
       CASE WHEN WH.Code = @Whse THEN '<- your DC' ELSE '' END AS Note,
       CAST(PR.fExclPrice AS decimal(19, 6)) AS ListExcl,
       CAST(PR.fInclPrice AS decimal(19, 6)) AS ListIncl,
       S.TaxRate,
       ROUND(CAST(PR.fExclPrice AS decimal(19, 6)) * (1 + S.TaxRate / 100), 2) AS ChargedIncl
FROM #stk S
JOIN dbo._etblPriceListPrices PR ON PR.iStockID = S.StockLink
                                AND PR.iPriceListNameID = @PriceListID
JOIN dbo.WhseMst WH              ON WH.WhseLink = PR.iWarehouseID
ORDER BY S.StockCode, Note DESC, WH.Code;

-- ---------------------------------------------------------------------------
-- 3. Deal rows (customer deals + group deals for the customer's group)
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS #deals;
SELECT S.StockCode,
       DealType     = CASE WHEN VD.iARAPID = @DCLink THEN 'Customer Deal' ELSE 'Group Deal' END,
       DealId       = VD.IDVD,
       VDLN.dEffDate, VDLN.dExpDate,
       ActiveOnAsOf = CASE WHEN VDLN.dEffDate <= @AsOf AND VDLN.dExpDate >= @AsOf THEN 1 ELSE 0 END,
       CapturedAs   = CASE WHEN VDLN.cEnterInclExcl = 'E' THEN 'Excl' ELSE 'Incl' END,
       QtyBreak     = VDLVL.fQuantity,
       RawPrice     = CAST(VDLVL.fPriceDisc AS decimal(19, 6)),
       TaxRate      = CAST(T.TaxRate AS decimal(9, 4)),
       -- Exactly what _vw_SOActiveDeals computes (float maths + FLOOR):
       ViewExcl = CAST(CASE WHEN VDLN.cEnterInclExcl = 'E' THEN VDLVL.fPriceDisc
                       ELSE FLOOR(100.0 / (100 + T.TaxRate) * VDLVL.fPriceDisc * 100) / 100.0 END
                  AS decimal(19, 6)),
       ViewIncl = CAST(CASE WHEN VDLN.cEnterInclExcl = 'E'
                       THEN FLOOR((100 + T.TaxRate) / 100.0 * VDLVL.fPriceDisc * 100) / 100.0
                       ELSE VDLVL.fPriceDisc END
                  AS decimal(19, 6)),
       -- The same without any rounding:
       ExactExcl = CAST(CASE WHEN VDLN.cEnterInclExcl = 'E' THEN VDLVL.fPriceDisc
                        ELSE VDLVL.fPriceDisc * 100.0 / (100 + T.TaxRate) END AS decimal(19, 6)),
       ExactIncl = CAST(CASE WHEN VDLN.cEnterInclExcl = 'E'
                        THEN VDLVL.fPriceDisc * (100 + T.TaxRate) / 100.0
                        ELSE VDLVL.fPriceDisc END AS decimal(19, 6)),
       -- FLOOR after removing float noise (round to 4 decimals of a cent first):
       NoiseFreeFloorExcl = CAST(CASE WHEN VDLN.cEnterInclExcl = 'E' THEN VDLVL.fPriceDisc
                                 ELSE FLOOR(ROUND(100.0 / (100 + T.TaxRate) * VDLVL.fPriceDisc * 100, 4)) / 100.0 END
                            AS decimal(19, 6)),
       NoiseFreeFloorIncl = CAST(CASE WHEN VDLN.cEnterInclExcl = 'E'
                                 THEN FLOOR(ROUND((100 + T.TaxRate) / 100.0 * VDLVL.fPriceDisc * 100, 4)) / 100.0
                                 ELSE VDLVL.fPriceDisc END
                            AS decimal(19, 6))
INTO #deals
FROM #stk S
JOIN dbo._etblVDLnAR VDLN     ON VDLN.iStockID = S.StockLink
JOIN dbo._etblVDAR VD         ON VD.IDVD       = VDLN.iVDID
JOIN dbo._etblVDLnLvlAR VDLVL ON VDLVL.iVDLnID = VDLN.IDVDLn
JOIN dbo._etblStockDetails DET ON DET.StockID  = S.StockLink AND DET.WhseID < 1
JOIN dbo.TaxRate T            ON T.idTaxRate   = DET.TTInvID
WHERE VD.iARAPID = @DCLink
   OR VD.iGroupID = @ClassID;

SELECT StockCode, DealType, DealId, dEffDate, dExpDate, ActiveOnAsOf, CapturedAs,
       QtyBreak, RawPrice, TaxRate, ViewExcl, ViewIncl, ExactExcl, ExactIncl,
       FloatNoise = CASE WHEN ViewExcl <> NoiseFreeFloorExcl OR ViewIncl <> NoiseFreeFloorIncl
                         THEN 'YES' ELSE '' END
FROM #deals
ORDER BY StockCode, ActiveOnAsOf DESC,
         CASE DealType WHEN 'Customer Deal' THEN 1 ELSE 2 END, QtyBreak;

-- ---------------------------------------------------------------------------
-- 4. What the app resolves vs the PO, with a verdict
-- ---------------------------------------------------------------------------
SELECT S.StockCode, S.Description_1, S.TaxRate,
       S.PoIncl,
       F.PriceSource,
       AppExcl       = CAST(F.ExclPrice AS decimal(19, 6)),
       AppShownIncl  = CAST(F.InclPrice AS decimal(19, 6)),
       C.ChargedIncl,
       DiffVsPo      = C.ChargedIncl - S.PoIncl,
       D.CapturedAs  AS ActiveDealCapturedAs,
       D.RawPrice    AS ActiveDealRawPrice,
       ActiveDealRows = (SELECT COUNT(*) FROM #deals X
                         WHERE X.StockCode = S.StockCode AND X.ActiveOnAsOf = 1),
       Verdict = CASE
           WHEN S.StockLink IS NULL
               THEN 'Stock code not found'
           WHEN F.ExclPrice IS NULL
               THEN 'NO PRICE for this customer in ' + @Whse
           WHEN S.PoIncl IS NULL
               THEN '(no PO price given)'
           WHEN ABS(C.ChargedIncl - S.PoIncl) < 0.005 AND ABS(F.InclPrice - S.PoIncl) >= 0.005
               THEN 'OK - charge matches the PO. Only the incl shown in the app differs (FLOOR in _vw_SOActiveDeals, or the price list''s stored incl).'
           WHEN ABS(C.ChargedIncl - S.PoIncl) < 0.005
               THEN 'OK'
           WHEN D.CapturedAs = 'Incl' AND ABS(D.RawPrice - S.PoIncl) < 0.005
               THEN 'Deal is captured at the PO price incl, but the app saves the excl FLOORed to '
                    + CONVERT(varchar(20), CAST(F.ExclPrice AS decimal(19, 4)))
                    + ', so Evolution charges ' + CONVERT(varchar(20), C.ChargedIncl)
                    + ' (FLOOR in _vw_SOActiveDeals, not a wrong deal).'
           WHEN F.PriceSource = 'Price List' AND ABS(C.ChargedIncl - F.InclPrice) >= 0.005
               THEN 'Price list excl/incl disagree: excl + VAT = ' + CONVERT(varchar(20), C.ChargedIncl)
                    + ' (what is charged) but the stored incl is '
                    + CONVERT(varchar(20), CAST(F.InclPrice AS decimal(19, 2))) + '.'
                    + CASE WHEN ABS(F.InclPrice - S.PoIncl) < 0.005 THEN ' The stored incl matches the PO.'
                           ELSE ' Neither matches the PO.' END
           WHEN D.CapturedAs IS NOT NULL
               THEN 'Real difference: the active ' + D.DealType + ' (id '
                    + CONVERT(varchar(20), D.DealId) + ') is '
                    + CONVERT(varchar(20), D.RawPrice) + ' ' + D.CapturedAs
                    + '. Check the deal against the PO.'
           ELSE 'Real difference: no active deal; the price list ' + @Whse
                + ' price differs from the PO. Check the price list / is a deal missing?'
       END
FROM #stk S
OUTER APPLY (
    SELECT TOP 1 F.ExclPrice, F.InclPrice, F.PriceSource
    FROM [SOAssistant].dbo._fn_SOCustomerPriceAsOf(@AsOf) F
    WHERE F.Account   = @Account
      AND F.WhseCode  = @Whse
      AND F.StockCode = S.StockCode COLLATE DATABASE_DEFAULT
) F
OUTER APPLY (
    -- Same pick as _fn_SOCustomerPriceAsOf: Customer Deal before Group Deal.
    -- NOTE: the function does not order by QtyBreak, so if ActiveDealRows > 1
    -- it may be picking an arbitrary quantity-break level.
    SELECT TOP 1 X.DealType, X.DealId, X.CapturedAs, X.RawPrice
    FROM #deals X
    WHERE X.StockCode = S.StockCode AND X.ActiveOnAsOf = 1
    ORDER BY CASE X.DealType WHEN 'Customer Deal' THEN 1 ELSE 2 END
) D
CROSS APPLY (
    SELECT ChargedIncl = ROUND(CAST(F.ExclPrice AS decimal(19, 6)) * (1 + S.TaxRate / 100), 2)
) C
ORDER BY S.StockCode;
