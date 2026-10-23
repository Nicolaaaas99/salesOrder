/******************************************************************************
 Sales Order app — lookup / history views + app tables
 ------------------------------------------------------------------------------
 All objects live in the dedicated app database [SOAssistant]. The views
 reference the live Evolution company database via three-part names
 ([BA_00_LPD_CON].dbo.<table>). The app tables keep audit / contact-log data
 isolated from the ERP schema so Evolution upgrades won't touch them.

 Prereq: CREATE DATABASE SOAssistant;  (done manually)

 The USE + GO batches are required because CREATE/ALTER VIEW must be the
 first statement in its batch.

 Object list (15 views + 1 function + 2 tables):
   Views     — _vw_SOActiveDeals, _vw_SOAgentDefaultWhse, _vw_SOCustomerDetail,
               _vw_SOCustomerInvoiceHistory, _vw_SOCustomerOrdersPerDay,
               _vw_SOCustomers, _vw_SOInventory,
               _vw_SOPriceListPrices, _vw_SOProjects, _vw_SORoute,
               _vw_SOSalesOrderConfig, _vw_SOSeasonalFactors,
               _vw_SOSettlementTerms, _vw_SOTaxTypes, _vw_SOWarehouses
   Functions — _fn_SOCustomerPriceAsOf(@asOf date) — resolves deal/list price
               for a given session date; replaces the old _vw_SOCustomerPrice.
   Tables    — _tb_SOOrderAssistantAudit (existing), _tb_SOCustomerContactLog (new)
******************************************************************************/

USE [SOAssistant];
GO

-- =============================================================================
-- 1. _vw_SOActiveDeals  (customer- and group-level volume discount deals
--     that are effective today)
-- =============================================================================
-- Exposes CL.DCLink + iStockID so _fn_SOCustomerPriceAsOf can APPLY on IDs
-- (cheaper for the optimizer than Account + StockCode text keys).
-- fPriceDiscExcl / fPriceDiscIncl are both derived in the inner subselect
-- from cEnterInclExcl: if the deal is captured Excl ('E'), fPriceDisc is the
-- Excl price and Incl is grossed up by TaxRate; otherwise fPriceDisc is the
-- Incl price and Excl is derived by stripping the TaxRate.
--
-- Rounding: FLOOR(x*100)/100.0 instead of ROUND(x,2) — always truncate to
-- the cent, never round up. Overcharging a corporate customer by a cent
-- triggers a price-claim; undercharging doesn't. Worst case we shave 0.99c
-- off a line, which is the whole point.
--
-- No date filter here — this view intentionally returns every deal row
-- (past, current, future). _fn_SOCustomerPriceAsOf applies the
-- effective/expiry window against its @asOf parameter so the price resolver
-- can honour the rep's session date, not the server clock.
CREATE OR ALTER VIEW dbo._vw_SOActiveDeals AS
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
                 ELSE FLOOR(100.0/(100+TaxRate)*fPriceDisc*100)/100.0 END fPriceDiscExcl
                ,CASE WHEN cEnterInclExcl = 'E' THEN  FLOOR((100+TaxRate)/100.0*fPriceDisc*100)/100.0
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
         -- Date filter moved to _fn_SOCustomerPriceAsOf(@asOf).
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
                 ELSE FLOOR(100.0/(100+TaxRate)*fPriceDisc*100)/100.0 END fPriceDiscExcl
                ,CASE WHEN cEnterInclExcl = 'E' THEN  FLOOR((100+TaxRate)/100.0*fPriceDisc*100)/100.0
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
         -- Date filter moved to _fn_SOCustomerPriceAsOf(@asOf).
       ) VD ON VD.iGroupID = CL.iClassID;
GO

-- =============================================================================
-- 2. _vw_SOAgentDefaultWhse  (telesales agent -> default DC warehouse)
--     Filters to the two valid DCs (48 = 01.HOF_MAK, 49 = 01.HOF_POL) and
--     only active agents.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOAgentDefaultWhse AS
SELECT
    AG.idAgents,
    AG.cAgentName,
    AG.cDescription,
    AG.iDefWhseId,
    WH.Code         AS WhseCode,
    AG.bAgentActive
FROM [BA_00_LPD_CON].dbo._rtblAgents AG
JOIN [BA_00_LPD_CON].dbo.WhseMst WH ON WH.WhseLink = AG.iDefWhseId
WHERE AG.bAgentActive = 1
  AND WH.WhseLink IN (48, 49);
GO

-- =============================================================================
-- 3. _vw_SOCustomerDetail  (credit / terms / addresses / contact / tax)
--     Full field set — the Sales Order Entry screen uses address+contact panels.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOCustomerDetail AS
SELECT
    C.DCLink,
    C.Account, C.Name,
    C.ulARRoute AS Route,
    C.Physical1, C.Physical2, C.Physical3, C.Physical4, C.Physical5, C.PhysicalPC,
    C.Post1,     C.Post2,     C.Post3,     C.Post4,     C.Post5,     C.PostPC,
    C.Contact_Person, C.Telephone, C.Telephone2, C.EMail,
    C.iDefTaxTypeID,
    C.Credit_Limit,
    ISNULL(C.CheckTerms, 0)         AS CheckTerms,
    ISNULL(t.cCode, '')             AS TermsCode,
    ISNULL(t.iAgeTypeOption, 0)     AS AgeTypeOption,   -- 0 = Invoice date, 1 = Statement (month-end)
    td.TermsDays,
    cutoff.CutoffDate,
    ISNULL((
        SELECT ROUND(SUM(Outstanding), 2)
        FROM [BA_00_LPD_CON].dbo.PostAR
        WHERE AccountLink = C.DCLink
    ), 0) AS TotalOutstanding,
    ISNULL((
        -- Statement terms: an invoice is overdue when its month-end-of-TxDate
        --   is before (today − TermsDays). All invoices in a calendar month
        --   share the same due date.
        -- Invoice-date terms: an invoice is overdue when its TxDate is before
        --   (today − TermsDays). Day-N is still within terms; day-(N+1) tips.
        SELECT ROUND(SUM(p.Outstanding), 2)
        FROM [BA_00_LPD_CON].dbo.PostAR p
        WHERE p.AccountLink = C.DCLink
          AND ISNULL(p.Outstanding, 0) > 0
          AND (
                (ISNULL(t.iAgeTypeOption, 0) = 1 AND EOMONTH(p.TxDate)      < cutoff.CutoffDate)
             OR (ISNULL(t.iAgeTypeOption, 0) = 0 AND CAST(p.TxDate AS date) < cutoff.CutoffDate)
          )
    ), 0) AS OverdueAmount,
    ISNULL((
        SELECT SUM(OrdTotIncl)
        FROM [BA_00_LPD_CON].dbo.InvNum
        WHERE DocType  = 4
          AND DocState IN (1)
          AND AccountID = C.DCLink
    ), 0) AS TotalOrdered
FROM [BA_00_LPD_CON].dbo.Client C
LEFT JOIN [BA_00_LPD_CON].dbo._etblTerms t ON t.iTermID = C.iAgeingTermID
CROSS APPLY (
    SELECT CASE ISNULL(C.AccountTerms, 0)
        WHEN 0 THEN ISNULL(t.iIntervalOption, 0)
        WHEN 1 THEN ISNULL(t.iInterval1Days, 0)
        WHEN 2 THEN ISNULL(t.iInterval2Days, 0)
        WHEN 3 THEN ISNULL(t.iInterval3Days, 0)
        WHEN 4 THEN ISNULL(t.iInterval4Days, 0)
        WHEN 5 THEN ISNULL(t.iInterval5Days, 0)
        WHEN 6 THEN ISNULL(t.iInterval6Days, 0)
        ELSE 0
    END AS TermsDays
) td
CROSS APPLY (
    -- Uniform cutoff for both aging types: today − TermsDays. The OverdueAmount
    -- subquery decides whether to compare TxDate directly (invoice-date terms)
    -- or EOMONTH(TxDate) (statement/month-end terms) against this date.
    SELECT DATEADD(day, -td.TermsDays, CAST(GETDATE() AS date)) AS CutoffDate
) cutoff;
GO

-- =============================================================================
-- 4. _vw_SOCustomerInvoiceHistory  (invoiced history + open orders)
--     Unions PostST invoice-side rows (last 8 weeks) with OPEN sales orders
--     (InvNum, DocType=4, DocState=1 = Unprocessed).
--
--     Invoice-side includes:
--       • 'OInv' — Order-invoiced
--       • 'Inv'  — Direct invoice
--       • 'Crn'  — Credit note (quantities + amounts flipped to negative so
--                  returns subtract from the customer's running qty / spend)
--
--     Extra columns:
--       • Id    — the PostST posting type (passed through; set to 'SO' for
--                 open sales-order rows so a downstream consumer can tell
--                 the source apart without re-joining).
--       • Litre — ulIISize * signed Quantity, so volume aggregation (per
--                 category or per week) works directly off the view.
--
--     Credit-note sign convention:
--       PostST stores returns as Debit > 0 / Credit = 0. The CASE WHEN
--       Credit > 0 THEN +qty ELSE -qty mirrors that onto Quantity, VAT and
--       InclAmount so a credited line appears as a negative in the history
--       grid (matching how the customer actually transacted).
--
--     ExclAmount is `Credit - Debit`: positive for sales, negative for
--     credits — no CASE needed because that subtraction already carries
--     the sign.
--
--     OnOrder = 0 for all invoice-side rows, 1 for pending SO rows.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOCustomerInvoiceHistory AS
SELECT
    CL.ulARRoute                     Route,
    CL.DCLink                        CustomerAccLink,
    CL.Account                       CustomerAccount,
    CL.Account+'-'+CL.Name           CustomerAccName,
    STK.ulIIMainCategory             Category,
    STK.StockLink,
    STK.Code                         StockCode,
    STK.Code+'-'+STK.Description_1   StockCodeDesc,
    ST.TxDate,
    ST.Order_No,
    ST.Id,
    CASE WHEN ST.Credit > 0 THEN ST.Quantity ELSE -ST.Quantity END                                         AS Quantity,
    STK.ulIISize * (CASE WHEN ST.Credit > 0 THEN ST.Quantity ELSE -ST.Quantity END)                        AS Litre,
    ST.Credit - ST.Debit                                                                                   AS ExclAmount,
    CASE WHEN ST.Credit > 0 THEN ST.Tax_Amount             ELSE -ST.Tax_Amount             END             AS VAT,
    CASE WHEN ST.Credit > 0 THEN ST.Credit + ST.Tax_Amount ELSE -ST.Debit  - ST.Tax_Amount END             AS InclAmount,
    0                                                                                                      AS OnOrder
FROM [BA_00_LPD_CON].dbo.PostST ST
JOIN [BA_00_LPD_CON].dbo.Client CL ON CL.DCLink = ST.DrCrAccount
JOIN [BA_00_LPD_CON].dbo.StkItem STK ON STK.StockLink = ST.AccountLink
WHERE ST.Id IN ('Crn', 'OInv', 'Inv')
  AND ST.TxDate >= DATEADD(WEEK, -12, GETDATE())
  AND STK.Code <> '999000'

UNION ALL

SELECT
    CL.ulARRoute                              Route,
    CL.DCLink                                 CustomerAccLink,
    CL.Account                                CustomerAccount,
    CL.Account+'-'+CL.Name                    CustomerAccName,
    STK.ulIIMainCategory                      Category,
    STK.StockLink,
    STK.Code                                  StockCode,
    STK.Code+'-'+STK.Description_1            StockCodeDesc,
    NUM.OrderDate                             TxDate,
    NUM.OrderNum                              Order_No,
    'SO'                                      AS Id,
    (LIN.fQuantity - LIN.fQtyProcessed)       AS Quantity,
    STK.ulIISize * (LIN.fQuantity - LIN.fQtyProcessed) AS Litre,
    LIN.fQuantityLineTotExcl                  AS ExclAmount,
    LIN.fQuantityLineTaxAmount                AS VAT,
    LIN.fQuantityLineTotIncl                  AS InclAmount,
    1                                         AS OnOrder
FROM [BA_00_LPD_CON].dbo._btblInvoiceLines LIN
JOIN [BA_00_LPD_CON].dbo.InvNum   NUM ON NUM.AutoIndex = LIN.iInvoiceID
JOIN [BA_00_LPD_CON].dbo.Client   CL  ON CL.DCLink     = NUM.AccountID
JOIN [BA_00_LPD_CON].dbo.StkItem  STK ON STK.StockLink = LIN.iStockCodeID
WHERE NUM.DocType  = 4
  AND NUM.DocState IN (1);
GO

-- =============================================================================
-- 5. _vw_SOCustomerOrdersPerDay  (per route × customer × delivery date, how
--     many open sales orders exist today — fuels the "orders placed today"
--     dot on the customer dropdown)
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOCustomerOrdersPerDay AS
SELECT CL.ulARRoute AS Route,
       CL.DCLink,
       CL.Account,
       NUM.DeliveryDate,
       COUNT(NUM.OrderNum) AS NoOfOrders
FROM [BA_00_LPD_CON].dbo.InvNum NUM
JOIN [BA_00_LPD_CON].dbo.Client CL ON CL.DCLink = NUM.AccountID
WHERE NUM.DocType  = 4
  AND NUM.DocState IN (1, 3, 4)   -- unprocessed, partially processed, archived
GROUP BY CL.ulARRoute, CL.DCLink, CL.Account, NUM.DeliveryDate;
GO

-- =============================================================================
-- 6. _fn_SOCustomerPriceAsOf(@asOf date)  (resolved price per customer × stock
--     — deal beats price list; Customer Deal beats Group Deal). The @asOf
--     parameter is the rep's session date — only deals whose effective window
--     straddles that date count. Replaces the old _vw_SOCustomerPrice view
--     (which hard-coded GETDATE() and so couldn't honour a future-dated
--     session). Inline TVF — optimizer treats it like a view, same plan.
-- =============================================================================
-- Drop the old view first; a function can't share its name.
IF OBJECT_ID('dbo._vw_SOCustomerPrice', 'V') IS NOT NULL
    DROP VIEW dbo._vw_SOCustomerPrice;
GO

CREATE OR ALTER FUNCTION dbo._fn_SOCustomerPriceAsOf (@asOf date)
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
        FROM [SOAssistant].dbo._vw_SOActiveDeals AD
        WHERE AD.DCLink   = CL.DCLink
          AND AD.iStockID = PR.iStockID
          -- Both bounds inclusive: dEffDate is the first day valid, dExpDate
          -- the last day valid. A deal effective Aug 17 and expiring Oct 11
          -- is honoured on both Aug 17 and Oct 11 — matches Evolution's UI
          -- convention and the rep's expectation.
          AND AD.dEffDate <= @asOf
          AND AD.dExpDate >= @asOf
        ORDER BY CASE AD.DealType WHEN 'Customer Deal' THEN 1 ELSE 2 END
    ) VD
);
GO

-- =============================================================================
-- 7. _vw_SOCustomers  (customer list keyed by route)
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOCustomers AS
SELECT
    LTRIM(RTRIM(c.Account))  AS AccountCode,
    c.Name                   AS CustomerName,
    c.ulARRoute              AS Route,
    c.DCLink                 AS DCLink       -- credit view joins on this
FROM [BA_00_LPD_CON].dbo.Client c
WHERE ISNULL(c.On_Hold, 0) = 0;
GO

-- =============================================================================
-- 8. _vw_SOInventory  (one row per stock+warehouse)
--     Notes:
--     • LEFT JOIN _etblUnits  → items without a UOM (e.g. code '180') still appear.
--     • LEFT JOIN TaxRate     → items with no default tax still appear.
--     • QTY join has both StockID + WhseID → correct QtyOnHand per warehouse,
--       no row multiplication (removes need for DISTINCT).
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOInventory AS
SELECT
    STK.Code            AS StockCode,
    STK.Description_1   AS StockDesc,
    ISNULL(STK.ulIIPerCrate, 1) AS PackSize,
    STK.bUOMItem        AS IsUomItem,
    STK.WhseItem        AS IsWhseItem,
    CASE WHEN STK.bUOMItem = 1 THEN ISNULL(UOM.cUnitCode, '') ELSE '' END AS Uom,
    TAX.idTaxRate       AS DefaultTaxTypeId,
    WH.WhseLink         AS WhseId,
    WH.Code             AS WhseCode,
    ISNULL(QTY.QtyOnHand, 0) AS QtyOnHand
FROM [BA_00_LPD_CON].dbo.StkItem STK
JOIN [BA_00_LPD_CON].dbo._etblStockDetails DET
        ON DET.StockID = STK.StockLink
LEFT JOIN [BA_00_LPD_CON].dbo._etblUnits UOM
        ON UOM.idUnits = STK.iUOMDefSellUnitID
LEFT JOIN [BA_00_LPD_CON].dbo.TaxRate TAX
        ON TAX.idTaxRate = DET.TTInvID
JOIN [BA_00_LPD_CON].dbo.WhseMst WH
        ON WH.WhseLink = DET.WhseID
       AND WH.WhseLink NOT IN (38, 78)            -- exclude master and assets warehouse
LEFT JOIN [BA_00_LPD_CON].dbo._etblStockQtys QTY
        ON QTY.StockID = STK.StockLink
       AND QTY.WhseID  = DET.WhseID     -- prevents cartesian multiplication
WHERE STK.ItemActive = 1
  AND DET.WhseID > 0
  AND STK.ServiceItem = 0;
GO

-- =============================================================================
-- 9. _vw_SOPriceListPrices  (one row per stock × warehouse × price list)
--     Filter fExclPrice > 0 so unpriced list entries don't surface a "zero
--     price" to the resolver.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOPriceListPrices AS
SELECT
    PR.iStockID,
    PR.iPriceListNameID,
    PN.cName             AS PriceListName,
    STK.Code             AS StockCode,
    WH.Code              AS WhseCode,
    PR.fExclPrice,
    PR.fInclPrice
FROM [BA_00_LPD_CON].dbo._etblPriceListPrices PR
JOIN [BA_00_LPD_CON].dbo._etblPriceListName PN ON PN.IDPriceListName = PR.iPriceListNameID
JOIN [BA_00_LPD_CON].dbo.StkItem STK           ON STK.StockLink      = PR.iStockID
JOIN [BA_00_LPD_CON].dbo.WhseMst WH            ON WH.WhseLink        = PR.iWarehouseID
WHERE PR.fExclPrice > 0;
GO

-- =============================================================================
-- 10. _vw_SOProjects
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOProjects AS
SELECT
    LTRIM(RTRIM(ProjectCode))  AS ProjectCode,
    LTRIM(RTRIM(ProjectName))  AS ProjectName
FROM [BA_00_LPD_CON].dbo.Project
WHERE ActiveProject = 1;
GO

-- =============================================================================
-- 11. _vw_SORoute  (routes come from Client master data, not transactions)
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SORoute AS
SELECT DISTINCT ulARRoute AS Route
FROM [BA_00_LPD_CON].dbo.Client
WHERE ulARRoute IS NOT NULL
  AND LTRIM(RTRIM(ulARRoute)) <> '';
GO

-- =============================================================================
-- 12. _vw_SOSalesOrderConfig  (single-row tenant config)
--     Mirrors Evolution's OrdersDf.bForceExtOrderNum so the app can read the
--     Evolution-admin-controlled setting and force / allow blank External
--     Order on sales orders accordingly.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOSalesOrderConfig AS
SELECT TOP 1
    CAST(ISNULL(bForceExtOrderNum, 0) AS bit) AS ForceExtOrderNum
FROM [BA_00_LPD_CON].dbo.OrdersDf
WHERE DefaultCounter = 1;
GO

-- =============================================================================
-- 13. _vw_SOSeasonalFactors  (per stock × calendar year × month, MonthAvg /
--     AnnualAvg = seasonality multiplier used by the suggestion algorithm)
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOSeasonalFactors AS
SELECT
    MT.StockId,
    MT.CalYear,
    MT.YearMonth,
    MT.MonthAvg,
    ZZ.AnnualAvg,
    MT.MonthAvg / ZZ.AnnualAvg AS SeasonalFactor
FROM
(
    SELECT
        StockId,
        StockCode   AS MTStockCode,
        CalYear,
        YearMonth,
        ROUND(AVG(Quantity), 2) AS MonthAvg
    FROM [BA_00_LPD_CON].dbo.ZZSalesWeekly
    GROUP BY StockId, StockCode, CalYear, YearMonth
) MT
JOIN
(
    SELECT
        StockId AS ZZStockId,
        StockCode,
        CalYear AS ZZCalYear,
        ROUND(AVG(Quantity), 2) AS AnnualAvg
    FROM [BA_00_LPD_CON].dbo.ZZSalesWeekly
    GROUP BY StockId, StockCode, CalYear
) ZZ
    ON MT.StockId = ZZ.ZZStockId
   AND MT.CalYear = ZZ.ZZCalYear
WHERE ZZ.AnnualAvg > 0;
GO

-- =============================================================================
-- 14. _vw_SOSettlementTerms
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOSettlementTerms AS
SELECT
    iTermID,
    cCode         AS Code,
    cDescription  AS Description
FROM [BA_00_LPD_CON].dbo._etblTerms
WHERE iModule = 0;
GO

-- =============================================================================
-- 15. _vw_SOTaxTypes
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOTaxTypes AS
SELECT
    idTaxRate      AS Id,
    Code,
    Description,
    TaxRate        AS Rate
FROM [BA_00_LPD_CON].dbo.TaxRate
WHERE bActiveTaxType = 1;
GO

-- =============================================================================
-- 16. _vw_SOWarehouses
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOWarehouses AS
SELECT
    WhseLink,
    Code,
    Name  AS Description
FROM [BA_00_LPD_CON].dbo.WhseMst
WHERE UPPER(Code) <> 'MSTR'
  AND ISNULL(Name, '') NOT LIKE 'Master%';
GO


-- =============================================================================
-- Tables
-- =============================================================================

-- -----------------------------------------------------------------------------
-- _tb_SOOrderAssistantAudit  (existing — audit log for every sales-order
-- action the app places into Evolution). Shape must match the production
-- table currently in [BA_00_LPD_CON] so a one-time INSERT ... SELECT can
-- migrate rows across without re-mapping columns.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOOrderAssistantAudit', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOOrderAssistantAudit (
        idSOAppAudit       BIGINT IDENTITY(1,1) NOT NULL,
        iInvNumAutoIndex   INT            NULL,
        cOrderNum          VARCHAR(20)    NOT NULL,
        cAction            VARCHAR(16)    NOT NULL,
        iDocVersion        INT            NULL,
        iAgentID           INT            NULL,
        cUsername          VARCHAR(64)    NOT NULL,
        cCustomerAccount   VARCHAR(20)    NULL,
        fOrderTotalIncl    DECIMAL(18, 4) NULL,
        cAppVersion        VARCHAR(32)    NULL,
        dtActionAt         DATETIME2(7)   NOT NULL
            CONSTRAINT DF_SOOrderAssistantAudit_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOOrderAssistantAudit PRIMARY KEY CLUSTERED (idSOAppAudit)
    );
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOCustomerContactLog  (new — one row per contact attempt an agent
-- makes with a customer; multiple rows per customer per day are expected,
-- latest wins for dropdown dot colouring).
--
-- Outcomes:
--   PLACED      — order was placed (written by the order-save path, not
--                 the modal — PLACED customers never see the modal).
--   NO_ORDER    — customer did not want / need to order today.
--   CALLBACK    — customer asked to be called back (optional dFollowUpDate).
--   NO_ANSWER   — call went to voicemail / no answer.
--   REFUSED     — refused, on stop, closed, or similar hard-stop.
--
-- "Opened by mistake" is NOT a stored outcome — the modal lets the agent
-- escape without writing a row, so the dropdown dot stays red.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOCustomerContactLog', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOCustomerContactLog (
        idContactLog       BIGINT IDENTITY(1,1) NOT NULL,
        dCallDate          DATE           NOT NULL,
        cRouteCode         VARCHAR(32)    NULL,
        iCustomerDCLink    INT            NOT NULL,
        cCustomerAccount   VARCHAR(20)    NOT NULL,
        cOutcome           VARCHAR(16)    NOT NULL,
        cNote              NVARCHAR(200)  NULL,
        dFollowUpDate      DATE           NULL,
        cOrderNum          VARCHAR(20)    NULL,
        iInvNumAutoIndex   INT            NULL,
        iAgentID           INT            NULL,
        cUsername          VARCHAR(64)    NOT NULL,
        cSourceApp         VARCHAR(32)    NOT NULL,
        cAppVersion        VARCHAR(32)    NULL,
        dtCreatedAt        DATETIME2(7)   NOT NULL
            CONSTRAINT DF_SOCustomerContactLog_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOCustomerContactLog PRIMARY KEY CLUSTERED (idContactLog),
        CONSTRAINT CK_SOCustomerContactLog_Outcome CHECK
            (cOutcome IN ('PLACED','NO_ORDER','CALLBACK','NO_ANSWER','REFUSED'))
    );
END;
GO

-- Supporting index: the dropdown dot query asks for all of today's outcomes
-- for a single route. Date-first ordering keeps that lookup cheap.
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOCustomerContactLog_RouteDate'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOCustomerContactLog')
)
BEGIN
    CREATE INDEX IX_SOCustomerContactLog_RouteDate
        ON [SOAssistant].dbo._tb_SOCustomerContactLog (dCallDate, cRouteCode)
        INCLUDE (iCustomerDCLink, cOutcome, dtCreatedAt);
END;
GO


-- =============================================================================
-- One-time data migration: copy existing audit rows from the Evolution-side
-- table into the new SOAssistant copy.
--
-- Guards:
--   • Only runs when the source table exists in [BA_00_LPD_CON].
--   • Only runs when the destination is still empty (prevents duplicates on
--     re-run of this script).
--   • Uses SET IDENTITY_INSERT so the original idSOAppAudit values are
--     preserved — any downstream reporting that referenced those IDs stays
--     correct.
--
-- After this script has run once and you've confirmed the row counts match,
-- the Evolution-side table can be dropped by hand with:
--     DROP TABLE [BA_00_LPD_CON].dbo._tb_SOOrderAssistantAudit;
-- (not done here — kept as a manual step so you can verify first).
-- =============================================================================
IF OBJECT_ID('[BA_00_LPD_CON].dbo._tb_SOOrderAssistantAudit', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit)
BEGIN
    SET IDENTITY_INSERT [SOAssistant].dbo._tb_SOOrderAssistantAudit ON;

    INSERT INTO [SOAssistant].dbo._tb_SOOrderAssistantAudit
        (idSOAppAudit, iInvNumAutoIndex, cOrderNum, cAction, iDocVersion,
         iAgentID, cUsername, cCustomerAccount, fOrderTotalIncl,
         cAppVersion, dtActionAt)
    SELECT
         idSOAppAudit, iInvNumAutoIndex, cOrderNum, cAction, iDocVersion,
         iAgentID, cUsername, cCustomerAccount, fOrderTotalIncl,
         cAppVersion, dtActionAt
    FROM [BA_00_LPD_CON].dbo._tb_SOOrderAssistantAudit;

    SET IDENTITY_INSERT [SOAssistant].dbo._tb_SOOrderAssistantAudit OFF;

    PRINT CONCAT(
        'Migrated ', @@ROWCOUNT,
        ' audit rows from [BA_00_LPD_CON] to [SOAssistant].'
    );
END
ELSE
BEGIN
    PRINT 'Audit migration skipped — source missing or destination already populated.';
END;
GO


-- =============================================================================
-- Permissions — MUST BE RUN ONCE BY A SYSADMIN
-- -----------------------------------------------------------------------------
-- The backend connects to the server with the login configured as DB_USERNAME
-- in the backend's .env. That login already has rights on the Evolution DB
-- (because the ERP uses it), but [SOAssistant] is a brand-new database it
-- has NEVER been granted anything on. Without this block the backend's
-- INSERT into _tb_SOCustomerContactLog (and _tb_SOOrderAssistantAudit) will
-- silently fail — the modal will look like it worked, but no row is written.
--
-- Replace '<app_login>' with the actual login name (whatever DB_USERNAME is)
-- and uncomment the block, OR run it manually after this script.
-- -----------------------------------------------------------------------------
/*
DECLARE @login sysname = N'<app_login>';   -- <-- edit this
DECLARE @sql   nvarchar(max);

-- Create a DB user mapped to that login, if one doesn't already exist.
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @login)
BEGIN
    SET @sql = N'CREATE USER ' + QUOTENAME(@login) + N' FOR LOGIN ' + QUOTENAME(@login) + N';';
    EXEC sp_executesql @sql;
END;

-- Read access on all the lookup/history views.
SET @sql = N'GRANT SELECT ON SCHEMA :: dbo TO ' + QUOTENAME(@login) + N';';
EXEC sp_executesql @sql;

-- Insert access on the two app tables. (No UPDATE/DELETE — these are
-- append-only logs; preventing writes-elsewhere is the whole point of
-- isolating them in [SOAssistant].)
SET @sql = N'GRANT INSERT ON OBJECT :: dbo._tb_SOOrderAssistantAudit TO ' + QUOTENAME(@login) + N';';
EXEC sp_executesql @sql;
SET @sql = N'GRANT INSERT ON OBJECT :: dbo._tb_SOCustomerContactLog TO ' + QUOTENAME(@login) + N';';
EXEC sp_executesql @sql;

PRINT 'Granted SELECT + INSERT to login ' + @login + ' on [SOAssistant].';
*/


-- =============================================================================
-- Verification
-- =============================================================================
SELECT name FROM [SOAssistant].sys.views  WHERE name LIKE '_vw_SO%' ORDER BY name;
-- Expected 15 rows.

SELECT name FROM [SOAssistant].sys.objects WHERE name LIKE '_fn_SO%' AND type IN ('IF','TF','FN') ORDER BY name;
-- Expected 1 row: _fn_SOCustomerPriceAsOf.

SELECT name FROM [SOAssistant].sys.tables WHERE name LIKE '_tb_SO%' ORDER BY name;
-- Expected 2 rows: _tb_SOCustomerContactLog, _tb_SOOrderAssistantAudit.

-- Row-count sanity check — should match the Evolution-side table.
SELECT 'SOAssistant audit rows' AS label, COUNT(*) AS n
FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit
UNION ALL
SELECT 'Evolution audit rows (source)', COUNT(*)
FROM [BA_00_LPD_CON].dbo._tb_SOOrderAssistantAudit;


-- =============================================================================
-- Cleanup — _vw_SORouteCustomerDaily was replaced by inline aggregation on
-- the base _vw_SOCustomerInvoiceHistory view (see Analytics → Customer
-- History endpoint). Safe to drop if it was previously created.
-- =============================================================================
IF OBJECT_ID('[SOAssistant].dbo._vw_SORouteCustomerDaily', 'V') IS NOT NULL
    DROP VIEW _vw_SORouteCustomerDaily;
GO
