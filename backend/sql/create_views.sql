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

 Object list (19 views + 2 functions + 5 tables):
   Views     — _vw_SOActiveDeals, _vw_SOAgentDefaultWhse, _vw_SOCustomerDetail,
               _vw_SOCustomerInvoiceHistory, _vw_SOCustomerOrdersPerDay,
               _vw_SOCustomers, _vw_SOInventory,
               _vw_SOPriceListPrices, _vw_SOProjects, _vw_SORoute,
               _vw_SORouteOptions, _vw_SOTelesaleOptions, _vw_SOSalesRep,
               _vw_SOCategoryOptions,
               _vw_SOSalesOrderConfig, _vw_SOSeasonalFactors,
               _vw_SOSettlementTerms, _vw_SOTaxTypes, _vw_SOWarehouses
   Functions — _fn_SOCustomerPriceAsOf(@asOf date) — resolves deal/list price
               for a given session date; replaces the old _vw_SOCustomerPrice.
               _fn_SOOrderIntake(@from date, @to date) — one row per order
               created in the range (Order Intake dashboard).
   Tables    — _tb_SOOrderAssistantAudit, _tb_SOCustomerContactLog,
               _tb_SOCustomerAssignmentAudit, _tb_SOTargets, _tb_SOAppUsers
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
    ISNULL(CL.ulARTelesalesRep, 'None') TelesalesRep,
    -- SalesRepId: ISNULL to 0 so the column is never NULL. 0 is a safe
    -- sentinel because real SalesRep ids start at 1 — if a 0-id rep
    -- ever gets created, this column becomes ambiguous.
    ISNULL(CL.RepID, 0)              SalesRepId,
    -- SalesRep.Code (short handle like "Vincent", "M M") instead of
    -- SalesRep.Name — matches how the business refers to reps + the
    -- Analytics screen's label (both use Code). Must stay consistent
    -- across both halves of the UNION or the open-orders rows would
    -- carry a different rep display than the invoice-side rows.
    -- Falls back to 'None' when the LEFT JOIN finds nothing (NULL RepID
    -- or an orphan RepID that points at a missing SalesRep row).
    ISNULL(REP.Code, 'None')         SalesRep,
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
LEFT JOIN [BA_00_LPD_CON].dbo.SalesRep REP ON REP.idSalesRep = CL.RepID
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
    ISNULL(CL.ulARTelesalesRep, 'None')       TelesalesRep,
    ISNULL(CL.RepID, 0)                       SalesRepId,
    ISNULL(REP.Code, 'None')                  SalesRep,
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
LEFT JOIN [BA_00_LPD_CON].dbo.SalesRep REP ON REP.idSalesRep = CL.RepID
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
    STK.ulIIMainCategory AS Category,
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
--     Deliveries screen's route picker uses this — "routes actually in
--     use" is the right semantic there. For the Customer Sales History
--     maintenance modal, use _vw_SORouteOptions below instead, which
--     includes defined-but-unused options (e.g. a brand-new INACTIVE
--     route before any customers have been assigned to it).
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SORoute AS
SELECT DISTINCT ulARRoute AS Route
FROM [BA_00_LPD_CON].dbo.Client
WHERE ulARRoute IS NOT NULL
  AND LTRIM(RTRIM(ulARRoute)) <> '';
GO

-- =============================================================================
-- 11b. _vw_SORouteOptions  (every route option defined in Evolution's user-
--      dict, including unassigned ones). Powers the dropdown in the
--      Customer Sales History maintenance modal. Evolution stores the
--      option list as a single semi-colon-separated string in
--      _rtblUserDict.cLookupOptions — STRING_SPLIT turns it into one row
--      per option so the frontend consumes the same shape as every other
--      lookup view. Empty trimmed values are filtered out.
--      Requires SQL Server 2016+ (STRING_SPLIT).
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SORouteOptions AS
SELECT LTRIM(RTRIM(value)) AS Route
FROM [BA_00_LPD_CON].dbo._rtblUserDict
CROSS APPLY STRING_SPLIT(cLookupOptions, ';')
WHERE cTableName = 'CLIENT'
  AND cFieldName = 'ulARRoute'
  AND LTRIM(RTRIM(value)) <> '';
GO

-- =============================================================================
-- 11c. _vw_SOTelesaleOptions  (every telesales-rep option defined in the
--      Evolution user-dict). Same pattern as _vw_SORouteOptions.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOTelesaleOptions AS
SELECT LTRIM(RTRIM(value)) AS Telesale
FROM [BA_00_LPD_CON].dbo._rtblUserDict
CROSS APPLY STRING_SPLIT(cLookupOptions, ';')
WHERE cTableName = 'CLIENT'
  AND cFieldName = 'ulARTelesalesRep'
  AND LTRIM(RTRIM(value)) <> '';
GO

-- =============================================================================
-- 11d. _vw_SOSalesRep  (full SalesRep master — needed for the maintenance
--      modal so the user can assign a customer to any defined rep,
--      including a new INACTIVE one). The Analytics screen separately
--      uses MAX(REP.Code) for its labels — this view is purely for the
--      dropdown data source.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOSalesRep AS
SELECT idSalesRep AS Id, Code AS SalesRep, Name AS RepName
FROM [BA_00_LPD_CON].dbo.SalesRep;
GO

-- =============================================================================
-- 11e. _vw_SOCategoryOptions  (every category option defined in the
--      Evolution user-dict for StkItem.ulIIMainCategory). Powers the
--      category dropdown in the target form. Same STRING_SPLIT pattern
--      as _vw_SORouteOptions / _vw_SOTelesaleOptions — one row per
--      option, trimmed, blank values filtered out.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOCategoryOptions AS
SELECT LTRIM(RTRIM(value)) AS Category
FROM [BA_00_LPD_CON].dbo._rtblUserDict
CROSS APPLY STRING_SPLIT(cLookupOptions, ';')
WHERE cTableName = 'STKITEM'
  AND cFieldName = 'ulIIMainCategory'
  AND LTRIM(RTRIM(value)) <> '';
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
        -- Screen that performed the action: ORDER_ASSISTANT or
        -- SALES_ORDER_ENTRY (UNKNOWN = stale client). Same column name +
        -- values as _tb_SOCustomerContactLog.cSourceApp. NULL on
        -- RESCHEDULE / CANCEL rows; older CREATE / UPDATE rows are
        -- backfilled further down.
        cSourceApp         VARCHAR(32)    NULL,
        CONSTRAINT PK_SOOrderAssistantAudit PRIMARY KEY CLUSTERED (idSOAppAudit)
    );
END;
GO

-- Idempotent migration: adds cSourceApp to pre-existing deployments.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE name = 'cSourceApp'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOOrderAssistantAudit')
)
BEGIN
    ALTER TABLE [SOAssistant].dbo._tb_SOOrderAssistantAudit
        ADD cSourceApp VARCHAR(32) NULL;
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
--   WRITTEN_ORDER — customer sent a written order (email / paper) that is
--                 keyed in manually instead of through the assistant.
--   CALLBACK    — customer asked to be called back (optional dFollowUpDate).
--   NO_ANSWER   — call went to voicemail / no answer.
--   REFUSED     — refused, on stop, closed, or similar hard-stop.
--
-- "Opened by mistake" is NOT a stored outcome — the modal lets the agent
-- escape without writing a row, so the dropdown dot stays red.
--
-- cSourceApp — screen that wrote the row: ORDER_ASSISTANT or
-- SALES_ORDER_ENTRY (UNKNOWN = stale client). Rows written before
-- 2026-11-06 carried the legacy value 'SO_ASSISTANT'; the backfill further
-- down relabels them ORDER_ASSISTANT.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOCustomerContactLog', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOCustomerContactLog (
        idContactLog       BIGINT IDENTITY(1,1) NOT NULL,
        dCallDate          DATE           NOT NULL,
        -- The delivery date the rep was prepping for in the session when
        -- this row was written. Can lag dCallDate by a day or two when the
        -- rep is planning tomorrow's deliveries today. The Deliveries
        -- report filters by this column so a NO_ORDER logged on 27 Oct for
        -- a 28 Oct delivery lists under "28 Oct" where it belongs. NULL
        -- for pre-feature rows (October 2026); dropdown-dot queries still
        -- key off dCallDate so they stay about "today's work".
        dDeliveryDate      DATE           NULL,
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
            (cOutcome IN ('PLACED','NO_ORDER','WRITTEN_ORDER','CALLBACK','NO_ANSWER','REFUSED'))
    );
END;
GO

-- Rebuild CK_SOCustomerContactLog_Outcome on pre-existing deployments so
-- it accepts WRITTEN_ORDER. Acts only while the current definition lacks
-- it, so re-running the script is a no-op.
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_SOCustomerContactLog_Outcome'
      AND parent_object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOCustomerContactLog')
      AND OBJECT_DEFINITION(object_id) NOT LIKE '%WRITTEN[_]ORDER%'
)
BEGIN
    ALTER TABLE [SOAssistant].dbo._tb_SOCustomerContactLog
        DROP CONSTRAINT CK_SOCustomerContactLog_Outcome;
    ALTER TABLE [SOAssistant].dbo._tb_SOCustomerContactLog
        ADD CONSTRAINT CK_SOCustomerContactLog_Outcome CHECK
            (cOutcome IN ('PLACED','NO_ORDER','WRITTEN_ORDER','CALLBACK','NO_ANSWER','REFUSED'));
END;
GO

-- Idempotent migration: adds dDeliveryDate to pre-existing deployments.
-- SQL Server ALTER TABLE ADD always appends to the physical row, so on
-- existing installs this lands at the end rather than next to dCallDate
-- as in the fresh CREATE above — cosmetic only, every query is named.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE name = 'dDeliveryDate'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOCustomerContactLog')
)
BEGIN
    ALTER TABLE [SOAssistant].dbo._tb_SOCustomerContactLog
        ADD dDeliveryDate DATE NULL;
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

-- -----------------------------------------------------------------------------
-- _tb_SOCustomerAssignmentAudit  (one row per maintenance edit of a
-- customer's route / telesales / sales rep via the Customer Sales
-- History screen's Edit modal). Append-only; before/after columns per
-- field so you can see exactly what changed and who did it. Nulls on the
-- prev-* columns mean the previous value was blank/NULL in Client.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOCustomerAssignmentAudit', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOCustomerAssignmentAudit (
        idAssignmentAudit  BIGINT IDENTITY(1,1) NOT NULL,
        iCustomerDCLink    INT            NOT NULL,
        cCustomerAccount   VARCHAR(20)    NOT NULL,
        cPrevRoute         VARCHAR(64)    NULL,
        cNewRoute          VARCHAR(64)    NULL,
        cPrevTelesale      VARCHAR(64)    NULL,
        cNewTelesale       VARCHAR(64)    NULL,
        iPrevSalesRepId    INT            NULL,
        iNewSalesRepId     INT            NULL,
        cPrevSalesRepCode  VARCHAR(64)    NULL,
        cNewSalesRepCode   VARCHAR(64)    NULL,
        -- On-hold flag before/after the edit (Client.On_Hold). NULL on
        -- pre-feature rows written before this column was added.
        bPrevOnHold        BIT            NULL,
        bNewOnHold         BIT            NULL,
        cUsername          VARCHAR(64)    NOT NULL,
        cAppVersion        VARCHAR(32)    NULL,
        cNote              NVARCHAR(200)  NULL,
        dtActionAt         DATETIME2(7)   NOT NULL
            CONSTRAINT DF_SOCustomerAssignmentAudit_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOCustomerAssignmentAudit PRIMARY KEY CLUSTERED (idAssignmentAudit)
    );
END;
GO

-- Idempotent migration: adds bPrevOnHold + bNewOnHold to pre-existing
-- deployments. ALTER TABLE ADD appends to the physical row — logically
-- they belong between cNewSalesRepCode and cUsername as in the fresh
-- CREATE above, but every query is named so this is cosmetic.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE name = 'bPrevOnHold'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOCustomerAssignmentAudit')
)
BEGIN
    ALTER TABLE [SOAssistant].dbo._tb_SOCustomerAssignmentAudit
        ADD bPrevOnHold BIT NULL, bNewOnHold BIT NULL;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOCustomerAssignmentAudit_Customer'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOCustomerAssignmentAudit')
)
BEGIN
    CREATE INDEX IX_SOCustomerAssignmentAudit_Customer
        ON [SOAssistant].dbo._tb_SOCustomerAssignmentAudit (cCustomerAccount, dtActionAt DESC);
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOTargets  (litre-based performance targets — Phase 4 of Analytics).
-- Any combination of the five scope columns is valid: a target "about"
-- the agent (cUsername) alone, a route alone, or any compound ("Yoghurt
-- litres for CustomerX on RouteY by AgentZ" = four columns set). The
-- CHECK constraint only enforces "at least one scope dim set".
--
-- Column sizes match the Evolution tables they mirror so matching joins
-- never truncate: cUsername↔_rtblAgents.cAgentName (50),
-- cRoute↔Client.ulARRoute (100), cCustomerAccount↔Client.Account (20),
-- cCategory↔StkItem.ulIIMainCategory (100), cStockCode↔StkItem.cSimpleCode (20).
--
-- cPeriod values:
--   DAILY   → measure today's litres
--   WEEKLY  → current ISO week, Mon-Sun (matches PerformanceStrip)
--   MONTHLY → calendar month to date
--   CUSTOM  → fixed window: dEffectiveFrom..dEffectiveTo are the
--             measurement bounds (one-shot, non-rolling). Think promos.
--
-- For DAILY/WEEKLY/MONTHLY, dEffectiveFrom/dEffectiveTo are on/off
-- validity dates (dEffectiveTo NULL = still active).
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOTargets', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOTargets (
        idTarget          INT IDENTITY(1,1) NOT NULL,
        -- Agent id (_rtblAgents.idAgents). Preferred over cUsername
        -- for the "Placed by" dim — the id is stable while the text
        -- name can drift (case, trailing letter, re-spellings). Both
        -- are populated on save: iAgentID drives measurement, cUsername
        -- is the human-readable display.
        iAgentID          INT            NULL,
        cUsername         VARCHAR(50)    NULL,
        -- Telesales rep (Client.ulARTelesalesRep). A telesales target
        -- measures litres on orders delivered to customers assigned to
        -- that telesales rep — different from an agent target, which
        -- measures orders this login placed via the app.
        cTelesales        VARCHAR(100)   NULL,
        -- Sales rep (Client.RepID → SalesRep.Code). Same shape as
        -- cTelesales: filters by the salesrep assigned to the customer
        -- on the order, not by who logged in.
        cSalesRep         VARCHAR(50)    NULL,
        cRoute            VARCHAR(100)   NULL,
        cCustomerAccount  VARCHAR(20)    NULL,
        cCategory         VARCHAR(100)   NULL,
        cStockCode        VARCHAR(20)    NULL,
        cPeriod           VARCHAR(16)    NOT NULL,
        fTargetLitres     DECIMAL(18, 2) NOT NULL,
        dEffectiveFrom    DATE           NOT NULL,
        dEffectiveTo      DATE           NULL,
        cCreatedBy        VARCHAR(50)    NOT NULL,
        dtCreatedAt       DATETIME2(7)   NOT NULL
            CONSTRAINT DF_SOTargets_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOTargets PRIMARY KEY CLUSTERED (idTarget),
        CONSTRAINT CK_SOTargets_Period CHECK
            (cPeriod IN ('DAILY', 'WEEKLY', 'MONTHLY', 'CUSTOM')),
        CONSTRAINT CK_SOTargets_HasScope CHECK (
            iAgentID IS NOT NULL
            OR cUsername IS NOT NULL
            OR cTelesales IS NOT NULL
            OR cSalesRep IS NOT NULL
            OR cRoute IS NOT NULL
            OR cCustomerAccount IS NOT NULL
            OR cCategory IS NOT NULL
            OR cStockCode IS NOT NULL
        ),
        CONSTRAINT CK_SOTargets_CustomRange CHECK (
            cPeriod <> 'CUSTOM' OR dEffectiveTo IS NOT NULL
        ),
        CONSTRAINT CK_SOTargets_TargetPositive CHECK (fTargetLitres > 0)
    );
END;
GO

-- Idempotent migration: adds cTelesales to pre-existing deployments.
-- ALTER TABLE ADD appends to the physical row — logically it belongs
-- next to cUsername as in the fresh CREATE above, but every query is
-- named so this is cosmetic.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE name = 'cTelesales'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOTargets')
)
BEGIN
    ALTER TABLE [SOAssistant].dbo._tb_SOTargets
        ADD cTelesales VARCHAR(100) NULL;
END;
GO

-- Idempotent migration: adds cSalesRep to pre-existing deployments.
-- Same shape as the cTelesales migration above.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE name = 'cSalesRep'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOTargets')
)
BEGIN
    ALTER TABLE [SOAssistant].dbo._tb_SOTargets
        ADD cSalesRep VARCHAR(50) NULL;
END;
GO

-- Idempotent migration: adds iAgentID to pre-existing deployments.
-- Preferred over cUsername for "Placed by" measurement — a stable int
-- key doesn't drift when the Evolution agent record is renamed and
-- doesn't false-mismatch on case ("BrendaS" vs "Brendas"). On save
-- both columns are populated; measurement prefers iAgentID.
IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE name = 'iAgentID'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOTargets')
)
BEGIN
    ALTER TABLE [SOAssistant].dbo._tb_SOTargets
        ADD iAgentID INT NULL;
END;
GO

-- Rebuild CK_SOTargets_HasScope so it includes cTelesales, cSalesRep
-- and iAgentID. The original constraint was defined before those
-- columns existed, so on pre-existing deployments it rejects targets
-- scoped only to those dims (every known-to-it scope column is NULL).
-- Drop + recreate behind an idempotent guard: act only if the current
-- constraint definition is missing ANY of the three, so re-running is
-- a no-op once rebuilt. OBJECT_DEFINITION returns the SQL text of the
-- constraint, which we grep for the column names.
IF EXISTS (
    SELECT 1 FROM sys.check_constraints
    WHERE name = 'CK_SOTargets_HasScope'
      AND parent_object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOTargets')
      AND (OBJECT_DEFINITION(object_id) NOT LIKE '%cTelesales%'
           OR OBJECT_DEFINITION(object_id) NOT LIKE '%cSalesRep%'
           OR OBJECT_DEFINITION(object_id) NOT LIKE '%iAgentID%')
)
BEGIN
    ALTER TABLE [SOAssistant].dbo._tb_SOTargets
        DROP CONSTRAINT CK_SOTargets_HasScope;
    ALTER TABLE [SOAssistant].dbo._tb_SOTargets
        ADD CONSTRAINT CK_SOTargets_HasScope CHECK (
            iAgentID IS NOT NULL
            OR cUsername IS NOT NULL
            OR cTelesales IS NOT NULL
            OR cSalesRep IS NOT NULL
            OR cRoute IS NOT NULL
            OR cCustomerAccount IS NOT NULL
            OR cCategory IS NOT NULL
            OR cStockCode IS NOT NULL
        );
END;
GO

-- Supporting index for the Order Assistant's per-agent lookup (most
-- recently created active target for this user). Drop + recreate if an
-- older version of this index exists that doesn't INCLUDE cSalesRep —
-- the key lookups add up on this hot path.
IF EXISTS (
    SELECT 1 FROM sys.indexes ix
    WHERE ix.name = 'IX_SOTargets_Username'
      AND ix.object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOTargets')
      AND NOT EXISTS (
          SELECT 1
          FROM sys.index_columns ixc
          JOIN sys.columns col
            ON col.object_id = ixc.object_id
           AND col.column_id = ixc.column_id
          WHERE ixc.object_id = ix.object_id
            AND ixc.index_id  = ix.index_id
            AND ixc.is_included_column = 1
            AND col.name = 'cSalesRep'
      )
)
BEGIN
    DROP INDEX IX_SOTargets_Username ON [SOAssistant].dbo._tb_SOTargets;
END;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOTargets_Username'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOTargets')
)
BEGIN
    CREATE INDEX IX_SOTargets_Username
        ON [SOAssistant].dbo._tb_SOTargets (cUsername, dtCreatedAt DESC)
        INCLUDE (cTelesales, cSalesRep, cRoute, cCustomerAccount, cCategory, cStockCode,
                 cPeriod, fTargetLitres, dEffectiveFrom, dEffectiveTo);
END;
GO

-- Supporting index for the Deliveries report Reason column, which asks
-- for the latest outcome per customer across a delivery-date range.
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOCustomerContactLog_DeliveryDate'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOCustomerContactLog')
)
BEGIN
    CREATE INDEX IX_SOCustomerContactLog_DeliveryDate
        ON [SOAssistant].dbo._tb_SOCustomerContactLog (dDeliveryDate, cCustomerAccount)
        INCLUDE (cOutcome, dtCreatedAt);
END;
GO

-- -----------------------------------------------------------------------------
-- _tb_SOAppUsers  (user-rights whitelist). An Evolution agent can only
-- log into the Sales Order Assistant if a matching active row exists
-- here. Role governs what they can do:
--   AGENT       read-only — can view targets they own + non-identity
--               targets (route / customer / category / stock), use
--               Customer Sales History filters but not its maintenance
--               actions. Cannot CRUD targets.
--   SUPERVISOR  full access to everything the app currently offers
--               (CRUD targets, CSH maintenance, bulk edits).
--   ADMIN       SUPERVISOR + the Users & Roles management screen.
--
-- iAgentID is the foreign key into [BA_00_LPD_CON].dbo._rtblAgents.
-- UNIQUE so there's at most one role per Evolution agent. bActive=0
-- shadow-deletes (keeps the row for audit but blocks login).
-- -----------------------------------------------------------------------------
IF OBJECT_ID('[SOAssistant].dbo._tb_SOAppUsers', 'U') IS NULL
BEGIN
    CREATE TABLE [SOAssistant].dbo._tb_SOAppUsers (
        idAppUser    INT IDENTITY(1,1) NOT NULL,
        iAgentID     INT          NOT NULL,
        cRole        VARCHAR(16)  NOT NULL,
        bActive      BIT          NOT NULL
            CONSTRAINT DF_SOAppUsers_bActive DEFAULT (1),
        cCreatedBy   VARCHAR(50)  NOT NULL,
        dtCreatedAt  DATETIME2(7) NOT NULL
            CONSTRAINT DF_SOAppUsers_dt DEFAULT (sysdatetime()),
        CONSTRAINT PK_SOAppUsers PRIMARY KEY CLUSTERED (idAppUser),
        CONSTRAINT UX_SOAppUsers_Agent UNIQUE (iAgentID),
        CONSTRAINT CK_SOAppUsers_Role CHECK
            (cRole IN ('AGENT', 'SUPERVISOR', 'ADMIN'))
    );
END;
GO

-- Supporting index for _fn_SOOrderIntake: one CREATE row per order,
-- looked up by InvNum.AutoIndex.
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_SOOrderAssistantAudit_InvNumAction'
      AND object_id = OBJECT_ID('[SOAssistant].dbo._tb_SOOrderAssistantAudit')
)
BEGIN
    CREATE INDEX IX_SOOrderAssistantAudit_InvNumAction
        ON [SOAssistant].dbo._tb_SOOrderAssistantAudit (iInvNumAutoIndex, cAction)
        INCLUDE (cUsername, cSourceApp, dtActionAt, iAgentID);
END;
GO

-- =============================================================================
-- _fn_SOOrderIntake(@from date, @to date)  — Order Intake dashboard source.
--
-- One row per sales order CREATED (InvNum_dCreatedDate) in [@from, @to],
-- with ordered + invoiced qty / litres / excl amount and who placed it.
-- Lives down here (not with the other functions) because it reads
-- _tb_SOOrderAssistantAudit, which must exist first.
--
-- Split orders: when Evolution partially processes an order it archives the
-- original (DocState 4) and creates a COPY carrying the full order lines
-- (DocState 3, OrigDocID = original AutoIndex), stamped with the processing
-- time. Only the original row is counted — counting the copy would double
-- every split order and invent a second "order" at processing time.
--
-- OrderSource: 'SO Assistant' when the app wrote a CREATE audit row for the
-- order, else 'Evolution'. AppScreen keeps the finer split
-- (ORDER_ASSISTANT / SALES_ORDER_ENTRY) for later use.
-- AgentID / UserName: resolved by Evolution agent ID, and the name is always
-- read from _rtblAgents.cAgentName — one place, so 'Brendas' / 'BrendaS'
-- collapse into one person and a rename in Evolution shows the new name on
-- old orders too. App orders: the audit row's iAgentID (falls back to a
-- case-insensitive name match if it was never resolved). Evolution orders:
-- InvNum_iCreatedAgentID. No agent found -> the audit username, else
-- 'Evolution'.
--
-- Shape: MULTI-STATEMENT on purpose (2026-11-08 measurement). The inline
-- version used a per-order OUTER APPLY over _btblInvoiceLines; SQL Server
-- spooled every invoice line ever and re-read the spool once per order
-- (56M worktable reads, ~64 s for ONE day). Here the day's orders land in
-- @orders first, then their lines are summed in ONE set-based join, so
-- invoice lines cost at most a single pass no matter how many orders.
--
-- InvNum itself is still scanned (no index on InvNum_dCreatedDate, and
-- AutoIndex can't bound it: branch ID ranges mean it isn't in date order).
-- An index on InvNum_dCreatedDate would remove that — Evolution table, so
-- agree it with IT / the Evolution partner first (PERFORMANCE.md).
--
-- Time zone: Evolution stamps InvNum_dCreatedDate in UTC, while our audit
-- dtActionAt is SQL Server local time (SAST). Verified 2026-11-08: an
-- app order's created date was exactly 2 h before its audit row. All
-- dates/hours here are LOCAL: the @from/@to day bounds are shifted to UTC
-- for the InvNum filter, and CreatedAt = audit dtActionAt for app orders
-- (exact capture time) else InvNum_dCreatedDate + the server's UTC offset.
-- App orders whose InvNum_dCreatedDate is NULL (seen on a dev copy where
-- the SDK didn't stamp it) are still picked up via the audit table.
--
-- Keep ranges short (the API caps at 31 days).
-- =============================================================================
-- Inline -> multi-statement can't be done with ALTER; drop first.
DROP FUNCTION IF EXISTS dbo._fn_SOOrderIntake;
GO

CREATE FUNCTION dbo._fn_SOOrderIntake (@from date, @to date)
RETURNS @out TABLE (
    AutoIndex         INT            NOT NULL PRIMARY KEY,
    OrderNum          VARCHAR(50)    NULL,
    OrderDate         DATETIME       NULL,
    InvDate           DATETIME       NULL,
    DocState          INT            NULL,
    CreatedAt         DATETIME       NULL,
    CreatedDate       DATE           NULL,
    CreatedHour       INT            NULL,
    OrderSource       VARCHAR(20)    NOT NULL,
    AppScreen         VARCHAR(32)    NULL,
    AgentID           INT            NULL,
    UserName          VARCHAR(100)   NOT NULL,
    CorpGroup         VARCHAR(100)   NOT NULL,
    CustomerAccount   VARCHAR(50)    NULL,
    CustomerName      VARCHAR(200)   NULL,
    OrderedQty        FLOAT          NOT NULL,
    InvoicedQty       FLOAT          NOT NULL,
    OrderedLitres     FLOAT          NOT NULL,
    InvoicedLitres    FLOAT          NOT NULL,
    OrderedExclAmnt   FLOAT          NOT NULL,
    InvoicedExclAmnt  FLOAT          NOT NULL
)
AS
BEGIN
    -- 1. The orders captured in the range (originals only — see header).
    DECLARE @orders TABLE (
        AutoIndex   INT      NOT NULL PRIMARY KEY,
        OrderNum    VARCHAR(50) NULL,
        OrderDate   DATETIME NULL,
        InvDate     DATETIME NULL,
        DocState    INT      NULL,
        CreatedAt   DATETIME NULL,
        AccountID   INT      NULL,
        CreatedAgentID INT   NULL
    );

    -- Local day bounds, and the same bounds in UTC for InvNum. The offset
    -- is read from the server clock (SAST = +120 min, no daylight saving).
    DECLARE @offsetMin INT       = DATEDIFF(MINUTE, SYSUTCDATETIME(), SYSDATETIME());
    DECLARE @fromLocal DATETIME  = CAST(@from AS DATETIME);
    DECLARE @toLocal   DATETIME  = DATEADD(DAY, 1, CAST(@to AS DATETIME));
    DECLARE @fromUtc   DATETIME  = DATEADD(MINUTE, -@offsetMin, @fromLocal);
    DECLARE @toUtc     DATETIME  = DATEADD(MINUTE, -@offsetMin, @toLocal);

    -- 1a. Orders whose (UTC) created date falls in the range.
    INSERT INTO @orders
    SELECT NUM.AutoIndex, NUM.OrderNum, NUM.OrderDate, NUM.InvDate, NUM.DocState,
           DATEADD(MINUTE, @offsetMin, NUM.InvNum_dCreatedDate),
           NUM.AccountID, NUM.InvNum_iCreatedAgentID
    FROM [BA_00_LPD_CON].dbo.InvNum NUM
    WHERE NUM.DocType = 4
      AND NUM.DocState IN (1, 3, 4)
      -- originals only: split-order copies carry OrigDocID = original
      AND (NUM.OrigDocID IS NULL OR NUM.OrigDocID = 0 OR NUM.OrigDocID = NUM.AutoIndex)
      AND NUM.InvNum_dCreatedDate >= @fromUtc
      AND NUM.InvNum_dCreatedDate <  @toUtc;

    -- 1b. App orders captured in the range that 1a missed (NULL created
    -- date). PK seeks on InvNum — the audit table is small.
    INSERT INTO @orders
    SELECT NUM.AutoIndex, NUM.OrderNum, NUM.OrderDate, NUM.InvDate, NUM.DocState,
           NULL, NUM.AccountID, NUM.InvNum_iCreatedAgentID
    FROM [BA_00_LPD_CON].dbo.InvNum NUM
    WHERE NUM.AutoIndex IN (
            SELECT A.iInvNumAutoIndex
            FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit A
            WHERE A.cAction = 'CREATE'
              AND A.dtActionAt >= @fromLocal
              AND A.dtActionAt <  @toLocal)
      AND NUM.InvNum_dCreatedDate IS NULL
      AND NUM.DocType = 4
      AND NUM.DocState IN (1, 3, 4);

    -- 2. Line totals for exactly those orders, in one set-based pass.
    DECLARE @lines TABLE (
        AutoIndex         INT   NOT NULL PRIMARY KEY,
        OrderedQty        FLOAT NULL,
        InvoicedQty       FLOAT NULL,
        OrderedLitres     FLOAT NULL,
        InvoicedLitres    FLOAT NULL,
        OrderedExclAmnt   FLOAT NULL,
        InvoicedExclAmnt  FLOAT NULL
    );

    INSERT INTO @lines
    SELECT O.AutoIndex,
           SUM(LIN.fQuantity),
           SUM(LIN.fQtyProcessed),
           SUM(STK.ulIISize * LIN.fQuantity),
           SUM(STK.ulIISize * LIN.fQtyProcessed),
           SUM(LIN.fQuantityLineTotExcl),
           SUM(LIN.fQtyLastProcessLineTotExcl)
    FROM @orders O
    JOIN [BA_00_LPD_CON].dbo._btblInvoiceLines LIN ON LIN.iInvoiceID = O.AutoIndex
    JOIN [BA_00_LPD_CON].dbo.StkItem STK           ON STK.StockLink  = LIN.iStockCodeID
    GROUP BY O.AutoIndex;

    -- 3. Who / where, plus the totals.
    INSERT INTO @out
    SELECT
        O.AutoIndex,
        O.OrderNum,
        O.OrderDate,
        O.InvDate,
        O.DocState,
        -- Local capture time: the app's own audit timestamp when we have
        -- it (exact), else Evolution's created date shifted from UTC.
        COALESCE(CAST(AA.dtActionAt AS DATETIME), O.CreatedAt),
        CAST(COALESCE(CAST(AA.dtActionAt AS DATETIME), O.CreatedAt) AS date),
        DATEPART(HOUR, COALESCE(CAST(AA.dtActionAt AS DATETIME), O.CreatedAt)),
        CASE WHEN AA.idSOAppAudit IS NULL THEN 'Evolution' ELSE 'SO Assistant' END,
        AA.cSourceApp,
        X.AgentID,
        -- [SOAssistant] and [BA_00_LPD_CON] have different collations
        -- (Latin1_General_CI_AS vs SQL_Latin1_General_CP1_CI_AS); mixing
        -- them in one COALESCE needs an explicit COLLATE (Msg 457).
        COALESCE(AG.cAgentName COLLATE DATABASE_DEFAULT,
                 AA.cUsername  COLLATE DATABASE_DEFAULT,
                 'Evolution'),
        ISNULL(NULLIF(LTRIM(RTRIM(CL.ulARCorpGroup)), ''), 'Non Corporate'),
        LTRIM(RTRIM(CL.Account)),
        CL.Name,
        ISNULL(L.OrderedQty, 0),
        ISNULL(L.InvoicedQty, 0),
        ISNULL(L.OrderedLitres, 0),
        ISNULL(L.InvoicedLitres, 0),
        ISNULL(L.OrderedExclAmnt, 0),
        ISNULL(L.InvoicedExclAmnt, 0)
    FROM @orders O
    JOIN [BA_00_LPD_CON].dbo.Client CL ON CL.DCLink = O.AccountID
    LEFT JOIN @lines L ON L.AutoIndex = O.AutoIndex
    OUTER APPLY (
        SELECT TOP 1 A.idSOAppAudit, A.cUsername, A.cSourceApp, A.dtActionAt, A.iAgentID
        FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit A
        WHERE A.iInvNumAutoIndex = O.AutoIndex
          AND A.cAction = 'CREATE'
        ORDER BY A.idSOAppAudit
    ) AA
    -- App order whose audit row has no iAgentID: match the username to the
    -- agent master (case-insensitive) so it still lands on the right person.
    OUTER APPLY (
        SELECT TOP 1 AN.idAgents
        FROM [BA_00_LPD_CON].dbo._rtblAgents AN
        WHERE AA.idSOAppAudit IS NOT NULL
          AND AA.iAgentID IS NULL
          AND AN.cAgentName COLLATE DATABASE_DEFAULT = AA.cUsername COLLATE DATABASE_DEFAULT
        ORDER BY AN.bAgentActive DESC, AN.idAgents
    ) AN
    CROSS APPLY (
        SELECT AgentID = CASE WHEN AA.idSOAppAudit IS NOT NULL
                              THEN COALESCE(AA.iAgentID, AN.idAgents)
                              ELSE NULLIF(O.CreatedAgentID, 0) END
    ) X
    LEFT JOIN [BA_00_LPD_CON].dbo._rtblAgents AG ON AG.idAgents = X.AgentID;

    RETURN;
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
SET @sql = N'GRANT INSERT ON OBJECT :: dbo._tb_SOCustomerAssignmentAudit TO ' + QUOTENAME(@login) + N';';
EXEC sp_executesql @sql;
-- Targets table needs full CRUD since the Analytics Performance screen
-- lets users create / edit / delete targets. Not append-only like the
-- audit tables.
SET @sql = N'GRANT INSERT, UPDATE, DELETE ON OBJECT :: dbo._tb_SOTargets TO ' + QUOTENAME(@login) + N';';
EXEC sp_executesql @sql;
-- AppUsers table — the admin Users & Roles screen does full CRUD
-- (add, change role, deactivate) under the ADMIN role gate.
SET @sql = N'GRANT INSERT, UPDATE, DELETE ON OBJECT :: dbo._tb_SOAppUsers TO ' + QUOTENAME(@login) + N';';
EXEC sp_executesql @sql;

PRINT 'Granted SELECT + INSERT on [SOAssistant] to login ' + @login + '.';

-- The Customer Sales History maintenance modal writes directly to the
-- Evolution Client table (3 scalar columns — ulARRoute, ulARTelesalesRep,
-- RepID). On a fresh deploy the backend login almost always already has
-- UPDATE on dbo.Client (because Evolution itself uses this login), but
-- grant it explicitly here just in case:
USE [BA_00_LPD_CON];
SET @sql = N'GRANT UPDATE ON OBJECT :: dbo.Client TO ' + QUOTENAME(@login) + N';';
EXEC sp_executesql @sql;
USE [SOAssistant];
PRINT 'Granted UPDATE on Client in [BA_00_LPD_CON] to login ' + @login + '.';
*/


-- =============================================================================
-- One-time backfill of dDeliveryDate for pre-existing PLACED rows.
--
-- Rows written before the dDeliveryDate column existed get NULL on migration
-- and are therefore invisible to the Deliveries report's Reason column
-- (which filters strictly by dDeliveryDate — a NULL doesn't match any date
-- range). For PLACED rows we can recover the delivery date from the order
-- itself (InvNum.DeliveryDate on the row sharing cOrderNum). Non-PLACED
-- rows stay NULL because we have no reliable record of what delivery date
-- the rep was prepping for at the time.
--
-- CROSS APPLY TOP 1 picks one row per OrderNum (Evolution splits
-- partially-processed orders across multiple InvNum rows, but they all
-- share the same DeliveryDate).
--
-- Review the row count returned by the SELECT first; run the UPDATE once.
/*
SELECT COUNT(*) AS placed_rows_to_backfill
FROM [SOAssistant].dbo._tb_SOCustomerContactLog
WHERE cOutcome = 'PLACED'
  AND dDeliveryDate IS NULL
  AND cOrderNum IS NOT NULL;

UPDATE log
SET log.dDeliveryDate = x.delivery_date
FROM [SOAssistant].dbo._tb_SOCustomerContactLog log
CROSS APPLY (
    SELECT TOP 1 CAST(inv.DeliveryDate AS DATE) AS delivery_date
    FROM dbo.InvNum inv
    WHERE inv.OrderNum = log.cOrderNum
      AND inv.DocType = 4
) x
WHERE log.cOutcome = 'PLACED'
  AND log.dDeliveryDate IS NULL;
*/


-- =============================================================================
-- One-time backfill: legacy cSourceApp = 'SO_ASSISTANT' -> 'ORDER_ASSISTANT'.
--
-- Before 2026-11-06 every contact-log row was stamped 'SO_ASSISTANT'
-- ("this app"). Safe to relabel because:
--   • Non-PLACED rows are only ever written by the Order Assistant's
--     leave-customer modal -> certainly ORDER_ASSISTANT.
--   • PLACED rows: the table went live 2026-10-23, and Sales Order Entry's
--     "Create a new order" card was disabled from 2026-10-19 until
--     2026-11-06. The only other way Sales Order Entry could create an
--     order was re-using an ARCHIVED order as a template — rare, and not
--     distinguishable after the fact. Accept that small error margin.
--
-- Runs automatically and is idempotent: once relabelled, no rows match
-- 'SO_ASSISTANT' any more, so re-running the script updates nothing.
-- =============================================================================
UPDATE [SOAssistant].dbo._tb_SOCustomerContactLog
SET cSourceApp = 'ORDER_ASSISTANT'
WHERE cSourceApp = 'SO_ASSISTANT';

PRINT CONCAT('Relabelled ', @@ROWCOUNT, ' contact-log rows SO_ASSISTANT -> ORDER_ASSISTANT.');
GO


-- =============================================================================
-- One-time backfill: cSourceApp on pre-existing _tb_SOOrderAssistantAudit
-- rows (NULL because the column didn't exist when they were written).
--
--   • CREATE -> ORDER_ASSISTANT. Audit writes started 2026-10-21, after
--     Sales Order Entry's "Create a new order" card was disabled
--     (2026-10-19). Same archived-order-as-template caveat as the
--     contact-log backfill above.
--   • UPDATE -> SALES_ORDER_ENTRY. Edits always run through the Sales Order
--     Entry screen (the Order Assistant hands the order over to it), which
--     is also what new UPDATE rows are stamped with.
--   • RESCHEDULE / CANCEL stay NULL — they come from the shared order-
--     summary modal, and new rows of those types are NULL too.
--
-- Idempotent: only touches rows still NULL. Run this script BEFORE
-- deploying the backend — if the backend goes first, Sales Order Entry
-- creates written before the column exists would also land NULL and be
-- relabelled ORDER_ASSISTANT here.
-- =============================================================================
UPDATE [SOAssistant].dbo._tb_SOOrderAssistantAudit
SET cSourceApp = CASE cAction
                     WHEN 'CREATE' THEN 'ORDER_ASSISTANT'
                     WHEN 'UPDATE' THEN 'SALES_ORDER_ENTRY'
                 END
WHERE cSourceApp IS NULL
  AND cAction IN ('CREATE', 'UPDATE');

PRINT CONCAT('Backfilled cSourceApp on ', @@ROWCOUNT, ' audit rows.');
GO


-- =============================================================================
-- One-time backfill: canonicalise historical cUsername casing + fill
-- iAgentID on existing target rows.
--
-- Why: audit rows carried whatever casing the user typed at login
-- ("Brendas"), while the Evolution master has it canonically
-- ("BrendaS"). Measurement by cUsername string-matched inconsistently.
-- Going forward we match by iAgentID + canonicalise at login time, but
-- historical data still carries the mismatched values. Running these
-- UPDATEs once fixes the drift.
--
-- Preview-count SELECTs are above each UPDATE; uncomment the block,
-- review the counts, then run the UPDATE.
/*
-- Audit rows with cUsername that doesn't exactly match the Evolution master.
SELECT COUNT(*) AS audit_rows_to_canonicalise
FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit a
JOIN [BA_00_LPD_CON].dbo._rtblAgents g
       ON LOWER(g.cAgentName) = LOWER(a.cUsername)
WHERE a.cUsername <> g.cAgentName COLLATE Latin1_General_CS_AS;

UPDATE a
SET a.cUsername = g.cAgentName
FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit a
JOIN [BA_00_LPD_CON].dbo._rtblAgents g
       ON LOWER(g.cAgentName) = LOWER(a.cUsername)
WHERE a.cUsername <> g.cAgentName COLLATE Latin1_General_CS_AS;

-- Target rows: fill iAgentID from cUsername match + canonicalise text.
SELECT COUNT(*) AS targets_to_fill_agent_id
FROM [SOAssistant].dbo._tb_SOTargets t
JOIN [BA_00_LPD_CON].dbo._rtblAgents g
       ON LOWER(g.cAgentName) = LOWER(t.cUsername)
WHERE t.iAgentID IS NULL
  AND t.cUsername IS NOT NULL;

UPDATE t
SET t.iAgentID = g.idAgents,
    t.cUsername = g.cAgentName
FROM [SOAssistant].dbo._tb_SOTargets t
JOIN [BA_00_LPD_CON].dbo._rtblAgents g
       ON LOWER(g.cAgentName) = LOWER(t.cUsername)
WHERE t.cUsername IS NOT NULL;

-- cCreatedBy audit-trail column — purely for display, canonicalise too.
UPDATE t
SET t.cCreatedBy = g.cAgentName
FROM [SOAssistant].dbo._tb_SOTargets t
JOIN [BA_00_LPD_CON].dbo._rtblAgents g
       ON LOWER(g.cAgentName) = LOWER(t.cCreatedBy)
WHERE t.cCreatedBy <> g.cAgentName COLLATE Latin1_General_CS_AS;
*/


-- =============================================================================
-- Bootstrap first ADMIN — MUST BE RUN ONCE BY HAND on a fresh deploy.
--
-- Login is gated on `_tb_SOAppUsers` membership — if the table is
-- empty, nobody can log in, including whoever would normally promote
-- others. Run this once with the Evolution agent id of your first
-- admin. From then on they can add + role-change everyone else via
-- the Users & Roles screen in the app.
--
-- Find the agent id first:
--   SELECT idAgents, cAgentName
--   FROM [BA_00_LPD_CON].dbo._rtblAgents
--   WHERE cAgentName = '<your-login>';
--
-- Then uncomment + edit the INSERT below:
/*
INSERT INTO [SOAssistant].dbo._tb_SOAppUsers
    (iAgentID, cRole, bActive, cCreatedBy)
VALUES
    (445, 'ADMIN', 1, 'bootstrap');   -- <-- edit the id
*/


-- =============================================================================
-- Verification
-- =============================================================================
SELECT name FROM [SOAssistant].sys.views  WHERE name LIKE '_vw_SO%' ORDER BY name;
-- Expected 19 rows.

SELECT name FROM [SOAssistant].sys.objects WHERE name LIKE '_fn_SO%' AND type IN ('IF','TF','FN') ORDER BY name;
-- Expected 2 rows: _fn_SOCustomerPriceAsOf, _fn_SOOrderIntake.

SELECT name FROM [SOAssistant].sys.tables WHERE name LIKE '_tb_SO%' ORDER BY name;
-- Expected 5 rows: _tb_SOAppUsers, _tb_SOCustomerAssignmentAudit, _tb_SOCustomerContactLog, _tb_SOOrderAssistantAudit, _tb_SOTargets.

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
