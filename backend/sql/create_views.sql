/******************************************************************************
 Sales Order app — lookup / history views
 ------------------------------------------------------------------------------
 All views are prefixed `_vw_SO` so they're greppable and safe to drop/recreate
 without touching core Evolution objects.

 IMPORTANT: run this whole file against the COMPANY database (e.g.
 "Copy of Limpopo Dairies (Pty) Ltd"), NOT SageCommon.

 The `USE` + `GO` batches are required because CREATE VIEW must be the first
 statement in its batch.
******************************************************************************/

USE [Copy of Limpopo Dairies (Pty) Ltd];
GO

-- =============================================================================
-- 1. _vw_SOCustomerInvoiceHistory  (thin wrapper around _uvCustomerInvoiceHistory)
-- =============================================================================
-- The view unions INVOICED invoice history (PostST, last 8 weeks) with
-- OPEN sales orders (InvNum, DocType=4 AND DocState=1 = Unprocessed), so
-- items a customer has already ordered this period are visible to both the
-- Customer Overview grid and the suggestion algorithm — preventing a
-- "suggest something they just ordered" blind spot.
--
-- Rows carry `OnOrder` = 0 for invoiced, 1 for pending.
CREATE OR ALTER VIEW [dbo].[_vw_SOCustomerInvoiceHistory] AS
SELECT
	CL.ulARRoute                     Route
	,CL.DCLink                       CustomerAccLink
	,CL.Account                      CustomerAccount
	,CL.Account+'-'+CL.Name          CustomerAccName
	,STK.ulIIMainCategory            Category
	,STK.StockLink                   StockLink
	,STK.Code                        StockCode
	,STK.Code+'-'+STK.Description_1  StockCodeDesc
	,TxDate
	,Order_No
	,Quantity
	,Credit                          ExclAmount
	,Tax_Amount                      VAT
	,Credit+Tax_Amount               InclAmount
	,0                               OnOrder
FROM PostST ST
JOIN (
		Select * from Client
		) CL ON CL.DCLink = ST.DrCrAccount
JOIN StkItem STK on STK.StockLink = ST.AccountLink
WHERE Id = 'OInv'
AND TxDate >= DATEADD(WEEK,-8,GETDATE())
AND STK.Code <> '999000'
UNION ALL
-- Open sales orders: DocState 1 (Unprocessed) + 3 (Partial Processed). We
-- surface only the REMAINING qty (fQuantity − fQtyProcessed) so an order
-- that's been partially invoiced doesn't double-count the invoiced slice.
-- NB: ExclAmount/VAT/InclAmount here are still the full line totals, not
-- prorated to the remaining qty — fine for the suggestion math (uses Qty
-- only) but something to be aware of if we ever display per-line money.
SELECT
	CL.ulARRoute                              Route
	,CL.DCLink                                CustomerAccLink
	,CL.Account                               CustomerAccount
	,CL.Account+'-'+CL.Name                   CustomerAccName
	,STK.ulIIMainCategory                     Category
	,STK.StockLink                            StockLink
	,STK.Code                                 StockCode
	,STK.Code+'-'+STK.Description_1           StockCodeDesc
	,NUM.OrderDate                            TxDate
	,NUM.OrderNum                             Order_No
	,(LIN.fQuantity - LIN.fQtyProcessed)      Quantity
	,LIN.fQuantityLineTotExcl                 ExclAmount
	,LIN.fQuantityLineTaxAmount               VAT
	,LIN.fQuantityLineTotIncl                 InclAmount
	,1                                        OnOrder
FROM _btblInvoiceLines LIN
JOIN InvNum NUM   ON NUM.AutoIndex = LIN.iInvoiceID
JOIN Client CL    ON CL.DCLink     = NUM.AccountID
JOIN StkItem STK  ON STK.StockLink = LIN.iStockCodeID
WHERE DocType = 4
AND DocState IN (1, 3);
GO
-- =============================================================================
-- 2. _vw_SORoute  (routes come from Client master data, not transactions)
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SORoute AS
SELECT DISTINCT ulARRoute AS Route
FROM dbo.Client
WHERE ulARRoute IS NOT NULL
  AND LTRIM(RTRIM(ulARRoute)) <> '';
GO

-- =============================================================================
-- 3. _vw_SOCustomers  (customer list keyed by route)
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOCustomers AS
SELECT
    LTRIM(RTRIM(c.Account))  AS AccountCode,
    c.Name                   AS CustomerName,
    c.ulARRoute              AS Route,
    c.DCLink                 AS DCLink       -- credit view joins on this
FROM dbo.Client c
WHERE ISNULL(c.On_Hold, 0) = 0;
GO

-- =============================================================================
-- 4. _vw_SOCustomerDetail  (credit / terms / addresses / contact / tax)
--    Full field set — the Sales Order Entry screen uses address+contact panels.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOCustomerDetail AS
SELECT
    C.Account, C.Name, C.DCLink,
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
        FROM dbo.PostAR
        WHERE AccountLink = C.DCLink
    ), 0) AS TotalOutstanding,
    ISNULL((
        -- Statement terms: an invoice is overdue when its month-end-of-TxDate
        --   is before (today − TermsDays). All invoices sharing a calendar
        --   month share the same due date.
        -- Invoice-date terms: an invoice is overdue when its TxDate is before
        --   (today − TermsDays). Day-N is still within terms; day-(N+1) tips.
        -- CutoffDate = (today − TermsDays) in both branches.
        SELECT ROUND(SUM(p.Outstanding), 2)
        FROM dbo.PostAR p
        WHERE p.AccountLink = C.DCLink
          AND ISNULL(p.Outstanding, 0) > 0
          AND (
                (ISNULL(t.iAgeTypeOption, 0) = 1 AND EOMONTH(p.TxDate)        < cutoff.CutoffDate)
             OR (ISNULL(t.iAgeTypeOption, 0) = 0 AND CAST(p.TxDate AS date)   < cutoff.CutoffDate)
          )
    ), 0) AS OverdueAmount,
    ISNULL((
        SELECT SUM(OrdTotIncl)
        FROM dbo.InvNum
        WHERE DocType = 4 AND AccountID = C.DCLink
    ), 0) AS TotalOrdered
FROM dbo.Client C
LEFT JOIN dbo._etblTerms t ON t.iTermID = C.iAgeingTermID
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
    -- Uniform cutoff for both aging types: today − TermsDays. The subquery
    -- above decides whether to compare TxDate directly (invoice-date terms)
    -- or EOMONTH(TxDate) (statement/month-end terms) against this date.
    SELECT DATEADD(day, -td.TermsDays, CAST(GETDATE() AS date)) AS CutoffDate
) cutoff;
GO

-- =============================================================================
-- 5. _vw_SOInventory  (one row per stock+warehouse)
--    Notes:
--    • LEFT JOIN _etblUnits  → items without a UOM (e.g. code '180') still appear.
--    • LEFT JOIN TaxRate     → items with no default tax still appear.
--    • QTY join has both StockID + WhseID → correct QtyOnHand per warehouse,
--      no row multiplication (removes need for DISTINCT).
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
FROM dbo.StkItem STK
JOIN dbo._etblStockDetails DET
        ON DET.StockID = STK.StockLink
LEFT JOIN dbo._etblUnits UOM
        ON UOM.idUnits = STK.iUOMDefSellUnitID
LEFT JOIN dbo.TaxRate TAX
        ON TAX.idTaxRate = DET.TTInvID
JOIN dbo.WhseMst WH
        ON WH.WhseLink = DET.WhseID
       AND WH.WhseLink NOT IN (38, 78)  -- exclude master (38) and assets (78) warehouses
LEFT JOIN dbo._etblStockQtys QTY
        ON QTY.StockID = STK.StockLink
       AND QTY.WhseID  = DET.WhseID     -- prevents cartesian multiplication
WHERE STK.ItemActive = 1
  AND DET.WhseID > 0;
GO

-- =============================================================================
-- 6. _vw_SOWarehouses
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOWarehouses AS
SELECT
    WhseLink,
    Code,
    Name  AS Description
FROM dbo.WhseMst
WHERE UPPER(Code) <> 'MSTR'
  AND ISNULL(Name, '') NOT LIKE 'Master%';
GO

-- =============================================================================
-- 7. _vw_SOTaxTypes
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOTaxTypes AS
SELECT
    idTaxRate      AS Id,
    Code,
    Description,
    TaxRate        AS Rate
FROM dbo.TaxRate
WHERE bActiveTaxType = 1;
GO

-- =============================================================================
-- 8. _vw_SOProjects
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOProjects AS
SELECT
    LTRIM(RTRIM(ProjectCode))  AS ProjectCode,
    LTRIM(RTRIM(ProjectName))  AS ProjectName
FROM dbo.Project
WHERE ActiveProject = 1;
GO

-- =============================================================================
-- 9. _vw_SOSettlementTerms
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOSettlementTerms AS
SELECT
    iTermID,
    cCode         AS Code,
    cDescription  AS Description
FROM dbo._etblTerms
WHERE iModule = 0;
GO

-- =============================================================================
-- 10. _vw_SOPriceListPrices  (one row per stock × warehouse × price list)
--     Filter fExclPrice > 0 so unpriced list entries don't surface a "zero
--     price" to the resolver.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOPriceListPrices AS
SELECT
    PR.iStockID,
    PR.iPriceListNameID,
    STK.Code             AS StockCode,
    ISNULL(WH.Code, '0') AS WhseCode,
    PR.fExclPrice,
    PR.fInclPrice
FROM dbo._etblPriceListPrices PR
JOIN dbo.StkItem STK     ON STK.StockLink = PR.iStockID
LEFT JOIN dbo.WhseMst WH ON WH.WhseLink   = PR.iWarehouseID
WHERE PR.fExclPrice > 0;
GO

-- =============================================================================
-- 11. _vw_SOActiveDeals  (customer- and group-level volume discount deals
--     that are effective today)
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOActiveDeals AS
SELECT 'Customer Deal' AS DealType,
       CL.Account, CL.Name,
       GRP.Code AS CustGroup,
       VD.StockCode, VD.Description_1,
       VD.dEffDate, VD.dExpDate,
       VD.fQuantity, VD.fPriceDisc, VD.fPriceDiscIncl
FROM   dbo.Client CL
JOIN   ( SELECT IdCliClass, Code, Description FROM dbo.Cliclass ) GRP
       ON GRP.IdCliClass = CL.iClassID
JOIN   ( SELECT VD.IDVD, VD.iARAPID,
                STK.Code AS StockCode, STK.Description_1,
                VDLN.dEffDate, VDLN.dExpDate,
                VDLN.fQuantity, VDLN.fPriceDisc,
                ROUND((100 + TAX.TaxRate) / 100 * VDLN.fPriceDisc, 2) AS fPriceDiscIncl
         FROM   dbo._etblVDAR VD
         JOIN   ( SELECT VDLN.IDVDLn, VDLN.iVDID,
                         VDLN.iStockID,
                         VDLN.dEffDate, VDLN.dExpDate,
                         VDLVL.fQuantity, VDLVL.fPriceDisc
                  FROM   dbo._etblVDLnAR   VDLN
                  JOIN   dbo._etblVDLnLvlAR VDLVL
                         ON VDLVL.iVDLnID = VDLN.IDVDLn ) VDLN
                ON VDLN.iVDID = VD.IDVD
         JOIN   dbo.StkItem STK       ON STK.StockLink = VDLN.iStockID
         JOIN   ( SELECT * FROM dbo._etblStockDetails WHERE WhseID < 1 ) DET
                ON DET.StockID = STK.StockLink
         JOIN   dbo.TaxRate TAX       ON TAX.idTaxRate = DET.TTInvID
         WHERE  VDLN.dEffDate <  DATEADD(DAY, 1, CAST(GETDATE() AS date))
           AND  VDLN.dExpDate >  DATEADD(DAY, 1, CAST(GETDATE() AS date))
       ) VD ON VD.iARAPID = CL.DCLink

UNION ALL

SELECT 'Group Deal' AS DealType,
       CL.Account, CL.Name,
       GRP.Code AS CustGroup,
       VD.StockCode, VD.Description_1,
       VD.dEffDate, VD.dExpDate,
       VD.fQuantity, VD.fPriceDisc, VD.fPriceDiscIncl
FROM   dbo.Client CL
JOIN   ( SELECT IdCliClass, Code, Description FROM dbo.Cliclass ) GRP
       ON GRP.IdCliClass = CL.iClassID
JOIN   ( SELECT VD.IDVD, VD.iGroupID,
                STK.Code AS StockCode, STK.Description_1,
                VDLN.dEffDate, VDLN.dExpDate,
                VDLN.fQuantity, VDLN.fPriceDisc,
                ROUND((100 + TAX.TaxRate) / 100 * VDLN.fPriceDisc, 2) AS fPriceDiscIncl
         FROM   dbo._etblVDAR VD
         JOIN   ( SELECT VDLN.IDVDLn, VDLN.iVDID,
                         VDLN.iStockID,
                         VDLN.dEffDate, VDLN.dExpDate,
                         VDLVL.fQuantity, VDLVL.fPriceDisc
                  FROM   dbo._etblVDLnAR   VDLN
                  JOIN   dbo._etblVDLnLvlAR VDLVL
                         ON VDLVL.iVDLnID = VDLN.IDVDLn ) VDLN
                ON VDLN.iVDID = VD.IDVD
         JOIN   dbo.StkItem STK       ON STK.StockLink = VDLN.iStockID
         JOIN   ( SELECT * FROM dbo._etblStockDetails WHERE WhseID < 1 ) DET
                ON DET.StockID = STK.StockLink
         JOIN   dbo.TaxRate TAX       ON TAX.idTaxRate = DET.TTInvID
         WHERE  VDLN.dEffDate <  DATEADD(DAY, 1, CAST(GETDATE() AS date))
           AND  VDLN.dExpDate >  DATEADD(DAY, 1, CAST(GETDATE() AS date))
       ) VD ON VD.iGroupID = CL.iClassID;
GO

-- =============================================================================
-- 12. _vw_SOCustomerPrice  (resolved price per customer × stock — deal beats
--     price list; Customer Deal beats Group Deal)
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOCustomerPrice AS
SELECT
    CL.Account,
    PR.iStockID,
    PR.StockCode,
    PR.WhseCode,
    ExclPrice   = COALESCE(VD.fPriceDisc,     PR.fExclPrice),
    InclPrice   = COALESCE(VD.fPriceDiscIncl, PR.fInclPrice),
    PriceSource = CASE WHEN VD.StockCode IS NOT NULL
                       THEN VD.DealType ELSE 'Price List' END
FROM dbo.Client CL
JOIN dbo._vw_SOPriceListPrices PR
      ON PR.iPriceListNameID = CL.iARPriceListNameID
OUTER APPLY (
    SELECT TOP 1 DealType, StockCode, fPriceDisc, fPriceDiscIncl
    FROM dbo._vw_SOActiveDeals AD
    WHERE AD.Account   = CL.Account
      AND AD.StockCode = PR.StockCode
    ORDER BY CASE AD.DealType WHEN 'Customer Deal' THEN 1 ELSE 2 END
) VD;
GO

-- =============================================================================
-- 13. _vw_SOAgentDefaultWhse  (telesales agent -> default warehouse code)
--     Pricing uses this so each rep sees prices from the warehouse at the
--     distribution centre they work from. iDefWhseId is filtered to the two
--     valid DCs (48 = 01.HOF_MAK, 49 = 01.HOF_POL) and only Telesales agents
--     are included so other operators don't accidentally match.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOAgentDefaultWhse AS
SELECT
    AG.idAgents,
    AG.cAgentName,
    AG.cDescription,
    AG.iDefWhseId,
    WH.Code      AS WhseCode
FROM dbo._rtblAgents AG
JOIN dbo.WhseMst WH ON WH.WhseLink = AG.iDefWhseId
WHERE AG.bAgentActive = 1
GO

-- =============================================================================
-- 14. _vw_SOSalesOrderConfig  (single-row tenant config)
--     Mirrors Evolution's OrdersDf.bForceExtOrderNum so the app can read the
--     Evolution-admin-controlled setting and force / allow blank External
--     Order on sales orders accordingly. One row, one bit column.
-- =============================================================================
CREATE OR ALTER VIEW dbo._vw_SOSalesOrderConfig AS
SELECT TOP 1
    CAST(ISNULL(bForceExtOrderNum, 0) AS bit) AS ForceExtOrderNum
FROM dbo.OrdersDf
WHERE DefaultCounter = 1;
GO

-- =============================================================================
-- Verification
-- =============================================================================
SELECT name FROM sys.views WHERE name LIKE '_vw_SO%' ORDER BY name;
-- Expected 14 rows.
