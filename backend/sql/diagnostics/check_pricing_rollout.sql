/******************************************************************************
 Pricing rollout check — would the NEW pricing charge exactly what Evolution
 charges? Run BEFORE deploying the rounding change / inclusive saves.
 ------------------------------------------------------------------------------
 READ-ONLY (temp tables only). Needs rollout/pricing_v2_stage.sql run first.
 Run manually in SSMS, preferably outside peak hours — it scans the last
 @Days of sales order lines once. Never schedule it.

 The benchmark is orders your staff captured DIRECTLY in Evolution (tax-
 inclusive mode, Evolution's own pricing). For each of those lines it asks:
 what would the app charge for the same customer, item, warehouse and date?
   * Current = live _fn_SOCustomerPriceAsOf, exclusive save (today's app)
   * New     = _fn_SOCustomerPriceAsOf_v2, inclusive save (after rollout)

 Result sets:
   A. How many decimals Evolution stores on order-line prices. The new
      rounding needs excl kept to 4 decimals (e.g. 10.0870). If
      "Inclusive orders, excl > 2 decimals" is 0, STOP and tell the developer.
   B. Summary: lines where New matches Evolution, would charge MORE (claim
      risk), or LESS. The goal: MORE = 0.
   C1. Rounding differences (<= 2c) grouped per item — what the rollout is about.
   C2. Genuinely different prices (> 2c) grouped per item — NOT rounding;
       today's app has the same difference.
   D. Price-list rows whose stored excl and incl disagree (excl + VAT <> incl):
      D1 a summary per price list, D2 the rows. Inclusive saves charge the
      stored incl, as Evolution does.

 Everything is limited to the app's two DCs (@Whse: 48 = 01.HOF_MAK,
 49 = 01.HOF_POL).
   E. Active deals: today's charge vs the new charge vs the agreed price.
      The new charge must never be above the agreed incl.
   F. For the biggest C2 groups: which price list or deal carries the price
      Evolution charged ("nothing found" = typed by hand / imported).
******************************************************************************/
USE [BA_00_LPD_CON];
SET NOCOUNT ON;

DECLARE @Days int = 30;      -- how far back to compare staff-captured orders
DECLARE @From date = DATEADD(DAY, -@Days, CAST(GETDATE() AS date));

-- The DCs the app creates orders from (same as _vw_SOAgentDefaultWhse):
-- 48 = 01.HOF_MAK, 49 = 01.HOF_POL. Everything below is limited to these.
DECLARE @Whse TABLE (WhseLink int PRIMARY KEY);
INSERT INTO @Whse VALUES (48), (49);

IF OBJECT_ID('[SOAssistant].dbo._fn_SOCustomerPriceAsOf_v2') IS NULL
BEGIN
    RAISERROR('Run rollout/pricing_v2_stage.sql first.', 16, 1);
    RETURN;
END;

-- ---------------------------------------------------------------------------
-- Recent sales-order lines, one copy per order number (split orders keep
-- several InvNum copies — prefer Unprocessed > Partial > Archived).
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS #ord;
SELECT AutoIndex, OrderNum, AccountID, InvDate, TaxInclusive
INTO #ord
FROM (
    SELECT inv.AutoIndex, inv.OrderNum, inv.AccountID, inv.InvDate, inv.TaxInclusive,
           rn = ROW_NUMBER() OVER (PARTITION BY inv.OrderNum
                                   ORDER BY CASE inv.DocState WHEN 1 THEN 1 WHEN 3 THEN 2 WHEN 4 THEN 3 ELSE 4 END,
                                            inv.AutoIndex DESC)
    FROM dbo.InvNum inv
    WHERE inv.DocType = 4
      AND inv.InvDate >= @From
) X
WHERE rn = 1;

DROP TABLE IF EXISTS #ln;
SELECT O.OrderNum, O.InvDate, O.TaxInclusive,
       Source = CASE WHEN EXISTS (
                    SELECT 1
                    FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit A
                    JOIN dbo.InvNum I2 ON I2.AutoIndex = A.iInvNumAutoIndex
                    WHERE A.cAction = 'CREATE' AND I2.OrderNum = O.OrderNum)
                THEN 'App' ELSE 'Evolution' END,
       CL.Account, STK.Code AS StockCode, WH.Code AS Whse,
       Qty      = CAST(IL.fQuantity AS decimal(19, 4)),
       EvoExcl  = CAST(IL.fUnitPriceExcl AS decimal(19, 6)),
       EvoIncl  = CAST(IL.fUnitPriceIncl AS decimal(19, 6)),
       LineIncl = CAST(IL.fQuantityLineTotIncl AS decimal(19, 4)),
       LineDisc = CAST(IL.fLineDiscount AS decimal(9, 4)),
       TaxRate  = CAST(T.TaxRate AS decimal(9, 4))
INTO #ln
FROM #ord O
JOIN dbo._btblInvoiceLines IL ON IL.iInvoiceID = O.AutoIndex
JOIN dbo.Client CL            ON CL.DCLink     = O.AccountID
JOIN dbo.StkItem STK          ON STK.StockLink = IL.iStockCodeID
JOIN dbo.WhseMst WH           ON WH.WhseLink   = IL.iWarehouseID
LEFT JOIN dbo.TaxRate T       ON T.idTaxRate   = IL.iTaxTypeID
WHERE IL.fQuantity > 0
  AND IL.iWarehouseID IN (SELECT WhseLink FROM @Whse);

-- ---------------------------------------------------------------------------
-- A. Decimals Evolution keeps on order-line prices
-- ---------------------------------------------------------------------------
SELECT Source,
       Mode = CASE WHEN TaxInclusive = 1 THEN 'Inclusive' ELSE 'Exclusive' END,
       Lines = COUNT(*),
       ExclMoreThan2Decimals = SUM(CASE WHEN EvoExcl <> ROUND(EvoExcl, 2) THEN 1 ELSE 0 END),
       ExclMoreThan4Decimals = SUM(CASE WHEN EvoExcl <> ROUND(EvoExcl, 4) THEN 1 ELSE 0 END),
       InclMoreThan2Decimals = SUM(CASE WHEN EvoIncl <> ROUND(EvoIncl, 2) THEN 1 ELSE 0 END),
       ExampleExcl = MAX(CASE WHEN EvoExcl <> ROUND(EvoExcl, 2) THEN EvoExcl END),
       -- Line incl = qty x unit incl exactly? (how Evolution totals an inclusive line)
       LineInclIsQtyTimesIncl = SUM(CASE WHEN ABS(LineIncl - ROUND(Qty * EvoIncl * (1 - LineDisc / 100), 2)) < 0.005
                                         THEN 1 ELSE 0 END)
FROM #ln
GROUP BY Source, TaxInclusive
ORDER BY Source, Mode;

-- ---------------------------------------------------------------------------
-- What the app would charge for each staff-captured line
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS #keys;
SELECT DISTINCT Account, StockCode, Whse, InvDate = CAST(InvDate AS date)
INTO #keys
FROM #ln
WHERE Source = 'Evolution';

DROP TABLE IF EXISTS #price;
SELECT K.Account, K.StockCode, K.Whse, K.InvDate,
       CurExcl = CAST(C.ExclPrice AS decimal(19, 6)),
       CurIncl = CAST(C.InclPrice AS decimal(19, 6)),
       NewExcl = CAST(N.ExclPrice AS decimal(19, 6)),
       NewIncl = CAST(N.InclPrice AS decimal(19, 6)),
       N.PriceSource
INTO #price
FROM #keys K
OUTER APPLY (
    SELECT TOP 1 F.ExclPrice, F.InclPrice
    FROM [SOAssistant].dbo._fn_SOCustomerPriceAsOf(K.InvDate) F
    WHERE F.Account   = K.Account   COLLATE DATABASE_DEFAULT
      AND F.WhseCode  = K.Whse      COLLATE DATABASE_DEFAULT
      AND F.StockCode = K.StockCode COLLATE DATABASE_DEFAULT
) C
OUTER APPLY (
    SELECT TOP 1 F.ExclPrice, F.InclPrice, F.PriceSource
    FROM [SOAssistant].dbo._fn_SOCustomerPriceAsOf_v2(K.InvDate) F
    WHERE F.Account   = K.Account   COLLATE DATABASE_DEFAULT
      AND F.WhseCode  = K.Whse      COLLATE DATABASE_DEFAULT
      AND F.StockCode = K.StockCode COLLATE DATABASE_DEFAULT
) N;

DROP TABLE IF EXISTS #cmp;
SELECT L.OrderNum, L.InvDate, L.Account, L.StockCode, L.Whse, L.Qty, L.LineDisc,
       Mode = CASE WHEN L.TaxInclusive = 1 THEN 'Inclusive' ELSE 'Exclusive' END,
       L.EvoExcl, L.EvoIncl,
       P.PriceSource,
       CurrentCharge = ROUND(P.CurExcl * (1 + L.TaxRate / 100), 2),   -- today: excl saved
       NewCharge     = P.NewIncl,                                     -- after: incl saved
       NewMinusEvo   = P.NewIncl - L.EvoIncl,
       -- Rounding (a cent or two) vs a genuinely different price.
       Size = CASE WHEN ABS(P.NewIncl - L.EvoIncl) <= 0.025 THEN 'rounding (<=2c)' ELSE 'different price' END,
       -- Does the rollout change this line's charge at all?
       RolloutChanges = CASE WHEN ABS(ROUND(P.CurExcl * (1 + L.TaxRate / 100), 2) - P.NewIncl) >= 0.005
                             THEN 1 ELSE 0 END,
       Verdict = CASE
           WHEN L.LineDisc <> 0          THEN 'skipped: line discount'
           WHEN P.NewIncl IS NULL        THEN 'no app price (whse/price list)'
           WHEN ABS(P.NewIncl - L.EvoIncl) < 0.005 THEN 'MATCH'
           WHEN P.NewIncl > L.EvoIncl    THEN 'APP WOULD CHARGE MORE'
           ELSE 'app would charge less' END
INTO #cmp
FROM #ln L
JOIN #price P ON P.Account = L.Account AND P.StockCode = L.StockCode
             AND P.Whse = L.Whse AND P.InvDate = CAST(L.InvDate AS date)
WHERE L.Source = 'Evolution';

-- ---------------------------------------------------------------------------
-- B. Summary
-- ---------------------------------------------------------------------------
SELECT Mode, Verdict, Size, Lines = COUNT(*),
       CurrentAlsoMatched = SUM(CASE WHEN ABS(CurrentCharge - EvoIncl) < 0.005 THEN 1 ELSE 0 END),
       RolloutChangesCharge = SUM(RolloutChanges)
FROM #cmp
GROUP BY Mode, Verdict, Size
ORDER BY Mode, CASE Verdict WHEN 'APP WOULD CHARGE MORE' THEN 1 WHEN 'app would charge less' THEN 2
                            WHEN 'MATCH' THEN 3 ELSE 4 END, Size DESC;

-- ---------------------------------------------------------------------------
-- C1. ROUNDING differences (<= 2c), grouped per item + price. These are the
--     ones the rollout is about.
-- ---------------------------------------------------------------------------
SELECT Mode, Verdict, StockCode, Whse, PriceSource, EvoExcl, EvoIncl,
       CurrentCharge, NewCharge,
       Lines = COUNT(*), Customers = COUNT(DISTINCT Account), ExampleAccount = MIN(Account),
       FirstDate = MIN(InvDate), LastDate = MAX(InvDate)
FROM #cmp
WHERE Verdict IN ('APP WOULD CHARGE MORE', 'app would charge less')
  AND Size = 'rounding (<=2c)'
GROUP BY Mode, Verdict, StockCode, Whse, PriceSource, EvoExcl, EvoIncl, CurrentCharge, NewCharge
ORDER BY CASE Verdict WHEN 'APP WOULD CHARGE MORE' THEN 1 ELSE 2 END, Lines DESC;

-- ---------------------------------------------------------------------------
-- C2. DIFFERENT PRICES (> 2c), grouped per item + price, most lines first.
--     Not caused by rounding — the app (today AND after the rollout) resolves
--     another price than the one on the Evolution order. Set F looks for
--     where the Evolution price came from.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS #grp;
SELECT StockCode, Whse, EvoExcl, EvoIncl, NewCharge, PriceSource,
       Lines = COUNT(*), Customers = COUNT(DISTINCT Account), ExampleAccount = MIN(Account),
       Over = SUM(CASE WHEN Verdict = 'APP WOULD CHARGE MORE' THEN 1 ELSE 0 END),
       FirstDate = MIN(InvDate), LastDate = MAX(InvDate)
INTO #grp
FROM #cmp
WHERE Verdict IN ('APP WOULD CHARGE MORE', 'app would charge less')
  AND Size = 'different price'
GROUP BY StockCode, Whse, EvoExcl, EvoIncl, NewCharge, PriceSource;

SELECT TOP 300 * FROM #grp ORDER BY Lines DESC;

-- ---------------------------------------------------------------------------
-- D. Price-list rows whose stored excl and incl disagree — only your two DCs
--    and only price lists that are assigned to at least one customer.
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS #pl;
SELECT PN.cName AS PriceList, STK.Code AS StockCode, STK.Description_1, WH.Code AS Whse,
       StoredExcl = CAST(PR.fExclPrice AS decimal(19, 6)),
       StoredIncl = CAST(PR.fInclPrice AS decimal(19, 6)),
       TaxRate    = CAST(T.TaxRate AS decimal(9, 4)),
       ExclPlusVat = ROUND(CAST(PR.fExclPrice AS decimal(19, 6)) * (1 + CAST(T.TaxRate AS decimal(9, 4)) / 100), 2),
       Diff        = CAST(PR.fInclPrice AS decimal(19, 6))
                     - ROUND(CAST(PR.fExclPrice AS decimal(19, 6)) * (1 + CAST(T.TaxRate AS decimal(9, 4)) / 100), 2)
INTO #pl
FROM dbo._etblPriceListPrices PR
JOIN dbo._etblPriceListName PN ON PN.IDPriceListName = PR.iPriceListNameID
JOIN dbo.StkItem STK           ON STK.StockLink      = PR.iStockID
JOIN dbo.WhseMst WH            ON WH.WhseLink        = PR.iWarehouseID
JOIN dbo._etblStockDetails DET ON DET.StockID = STK.StockLink AND DET.WhseID < 1
JOIN dbo.TaxRate T             ON T.idTaxRate = DET.TTInvID
WHERE PR.fExclPrice > 0
  AND PR.iWarehouseID IN (SELECT WhseLink FROM @Whse)
  AND EXISTS (SELECT 1 FROM dbo.Client CL WHERE CL.iARPriceListNameID = PR.iPriceListNameID)
  AND ABS(CAST(PR.fInclPrice AS decimal(19, 6))
          - ROUND(CAST(PR.fExclPrice AS decimal(19, 6)) * (1 + CAST(T.TaxRate AS decimal(9, 4)) / 100), 2)) >= 0.005;

-- D1. Summary per price list
SELECT PriceList, Whse, Rows = COUNT(*),
       InclAboveExclPlusVat = SUM(CASE WHEN Diff > 0 THEN 1 ELSE 0 END),
       InclBelowExclPlusVat = SUM(CASE WHEN Diff < 0 THEN 1 ELSE 0 END),
       LargestDiff = MAX(ABS(Diff))
FROM #pl
GROUP BY PriceList, Whse
ORDER BY Rows DESC;

-- D2. The rows (largest difference first)
SELECT TOP 500 * FROM #pl ORDER BY ABS(Diff) DESC, PriceList, StockCode;

-- ---------------------------------------------------------------------------
-- E. Deals active today: today's charge vs new charge vs the agreed price
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS #dl;
SELECT DISTINCT
       Kind   = CASE WHEN VD.iARAPID > 0 THEN 'Customer Deal' ELSE 'Group Deal' END,
       DealId = VD.IDVD, STK.Code AS StockCode,
       CapturedAs = CASE WHEN VDLN.cEnterInclExcl = 'E' THEN 'Excl' ELSE 'Incl' END,
       QtyBreak   = VDLVL.fQuantity,
       Agreed     = CAST(VDLVL.fPriceDisc AS decimal(19, 6)),
       TaxRate    = CAST(T.TaxRate AS decimal(9, 4)),
       OldExcl = CAST(CASE WHEN VDLN.cEnterInclExcl = 'E' THEN VDLVL.fPriceDisc
                      ELSE FLOOR(100.0 / (100 + T.TaxRate) * VDLVL.fPriceDisc * 100) / 100.0 END AS decimal(19, 6)),
       NewIncl = CAST(CASE WHEN VDLN.cEnterInclExcl = 'E'
                      THEN ROUND(CAST(VDLVL.fPriceDisc AS decimal(19, 6)) * (100 + CAST(T.TaxRate AS decimal(9, 4))) / 100, 2)
                      ELSE VDLVL.fPriceDisc END AS decimal(19, 6)),
       ExactIncl = CAST(CASE WHEN VDLN.cEnterInclExcl = 'E'
                        THEN CAST(VDLVL.fPriceDisc AS decimal(19, 6)) * (100 + CAST(T.TaxRate AS decimal(9, 4))) / 100
                        ELSE VDLVL.fPriceDisc END AS decimal(19, 6))
INTO #dl
FROM dbo._etblVDAR VD
JOIN dbo._etblVDLnAR VDLN      ON VDLN.iVDID     = VD.IDVD
JOIN dbo._etblVDLnLvlAR VDLVL  ON VDLVL.iVDLnID  = VDLN.IDVDLn
JOIN dbo.StkItem STK           ON STK.StockLink  = VDLN.iStockID
JOIN dbo._etblStockDetails DET ON DET.StockID = STK.StockLink AND DET.WhseID < 1
JOIN dbo.TaxRate T             ON T.idTaxRate = DET.TTInvID
WHERE VDLN.dEffDate <= CAST(GETDATE() AS date)
  AND VDLN.dExpDate >= CAST(GETDATE() AS date)
  -- Deals aren't per warehouse: keep items priced at your two DCs.
  AND EXISTS (SELECT 1 FROM dbo._etblPriceListPrices PR
              WHERE PR.iStockID = STK.StockLink
                AND PR.iWarehouseID IN (SELECT WhseLink FROM @Whse)
                AND PR.fExclPrice > 0);

SELECT CapturedAs,
       Deals = COUNT(*),
       TodayChargeDiffersFromNew = SUM(CASE WHEN ROUND(OldExcl * (1 + TaxRate / 100), 2) <> NewIncl THEN 1 ELSE 0 END),
       -- Incl-captured: the agreed price IS the incl. Must be 0.
       NewAboveAgreedIncl = SUM(CASE WHEN CapturedAs = 'Incl' AND NewIncl > Agreed THEN 1 ELSE 0 END),
       -- Excl-captured: new incl rounds excl x VAT to the cent, so can sit up
       -- to half a cent above the exact figure (R12.26 -> R14.099 -> R14.10).
       -- Compare with result set B to see whether Evolution charges the same.
       NewRoundedUp = SUM(CASE WHEN CapturedAs = 'Excl' AND NewIncl > ExactIncl THEN 1 ELSE 0 END)
FROM #dl
GROUP BY CapturedAs;

SELECT TOP 500 Kind, DealId, StockCode, CapturedAs, QtyBreak, Agreed, TaxRate,
       TodayCharge = ROUND(OldExcl * (1 + TaxRate / 100), 2),
       NewCharge   = NewIncl,
       ExactIncl
FROM #dl
WHERE ROUND(OldExcl * (1 + TaxRate / 100), 2) <> NewIncl
ORDER BY CapturedAs, StockCode, DealId;

-- ---------------------------------------------------------------------------
-- F. Where did the Evolution price in C2 come from? For the 50 biggest
--    groups, every price-list row (any list, same warehouse) and every deal
--    (any customer or group, any date) for that item carrying exactly that
--    price. "Nothing found" = most likely typed in by hand or imported (EDI).
-- ---------------------------------------------------------------------------
DROP TABLE IF EXISTS #top;
SELECT TOP 50 G.*, CustPriceList = PN.cName
INTO #top
FROM #grp G
JOIN dbo.Client CL ON CL.Account = G.ExampleAccount
LEFT JOIN dbo._etblPriceListName PN ON PN.IDPriceListName = CL.iARPriceListNameID
ORDER BY G.Lines DESC;

SELECT T.StockCode, T.Whse, T.EvoExcl, T.EvoIncl, T.NewCharge, T.Lines, T.Customers,
       T.ExampleAccount, T.CustPriceList,
       FoundIn = COALESCE(S.FoundIn, 'Nothing found - typed by hand or imported?')
FROM #top T
OUTER APPLY (
    SELECT 'Price list ' + PN.cName + ' @ ' + WH.Code
           + ' (excl ' + CONVERT(varchar(20), CAST(PR.fExclPrice AS decimal(19, 4)))
           + ' / incl ' + CONVERT(varchar(20), CAST(PR.fInclPrice AS decimal(19, 4))) + ')' AS FoundIn
    FROM dbo.StkItem STK
    JOIN dbo._etblPriceListPrices PR ON PR.iStockID = STK.StockLink
    JOIN dbo._etblPriceListName PN   ON PN.IDPriceListName = PR.iPriceListNameID
    JOIN dbo.WhseMst WH              ON WH.WhseLink = PR.iWarehouseID
    WHERE STK.Code = T.StockCode
      AND WH.Code  = T.Whse
      AND (ABS(PR.fInclPrice - T.EvoIncl) < 0.005 OR ABS(PR.fExclPrice - T.EvoExcl) < 0.005)
    UNION ALL
    SELECT CASE WHEN VD.iARAPID > 0 THEN 'Customer deal for ' + ISNULL(CL.Account, '?')
                ELSE 'Group deal for ' + ISNULL(GRP.Code, '?') END
           + ' (id ' + CONVERT(varchar(20), VD.IDVD) + ', '
           + CASE WHEN VDLN.cEnterInclExcl = 'E' THEN 'excl ' ELSE 'incl ' END
           + CONVERT(varchar(20), CAST(VDLVL.fPriceDisc AS decimal(19, 4)))
           + ', qty>=' + CONVERT(varchar(20), VDLVL.fQuantity)
           + ', ' + CONVERT(varchar(10), VDLN.dEffDate, 23) + ' to ' + CONVERT(varchar(10), VDLN.dExpDate, 23) + ')'
    FROM dbo.StkItem STK
    JOIN dbo._etblVDLnAR VDLN     ON VDLN.iStockID = STK.StockLink
    JOIN dbo._etblVDAR VD         ON VD.IDVD = VDLN.iVDID
    JOIN dbo._etblVDLnLvlAR VDLVL ON VDLVL.iVDLnID = VDLN.IDVDLn
    LEFT JOIN dbo.Client CL       ON CL.DCLink = VD.iARAPID
    LEFT JOIN dbo.Cliclass GRP    ON GRP.IdCliClass = VD.iGroupID
    WHERE STK.Code = T.StockCode
      AND (ABS(VDLVL.fPriceDisc - T.EvoIncl) < 0.005 OR ABS(VDLVL.fPriceDisc - T.EvoExcl) < 0.005)
) S
ORDER BY T.Lines DESC, T.StockCode;
