/******************************************************************************
 Order Intake — verification queries for _fn_SOOrderIntake.
 Run in SSMS against the production server. Read-only (SELECTs only).

 Run order:
   1. Check 1 FIRST — confirms the operator column the function uses exists.
   2. Create / re-create _fn_SOOrderIntake via create_views.sql.
   3. Checks 2-4: how often each known risk actually occurs in the data.
   4. Check 5: side-by-side comparison with the original hand-written query
      for one day. Change @d to a recent busy day. 5c is the timing — read
      it from the Messages tab.
   5. Check 6: index list (helps judge any remaining slowness).
******************************************************************************/
USE [SOAssistant];
GO

-- -----------------------------------------------------------------------------
-- Check 1 — Evolution operator who captured the order.
-- The function joins InvNum.InvNum_iCreatedAgentID -> _rtblAgents.
-- If this errors with "Invalid column name", tell Claude — the function
-- needs to fall back to iINVNUMAgentID instead.
-- Expect: CreatedBy filled in for most rows.
-- -----------------------------------------------------------------------------
SELECT TOP 30
    NUM.OrderNum, NUM.InvNum_dCreatedDate,
    NUM.InvNum_iCreatedAgentID, CR.cAgentName AS CreatedBy,
    NUM.iINVNUMAgentID,         AG.cAgentName AS InvNumAgent
FROM [BA_00_LPD_CON].dbo.InvNum NUM
LEFT JOIN [BA_00_LPD_CON].dbo._rtblAgents CR ON CR.idAgents = NUM.InvNum_iCreatedAgentID
LEFT JOIN [BA_00_LPD_CON].dbo._rtblAgents AG ON AG.idAgents = NUM.iINVNUMAgentID
WHERE NUM.DocType = 4
ORDER BY NUM.AutoIndex DESC;
GO

-- -----------------------------------------------------------------------------
-- Check 2 — split-order COPIES that reached Archived (DocState 4).
-- The original query keeps DocState IN (1, 4), so any copy counted here is
-- double-counted there (the function counts originals only).
-- Expect: 0 means the original query is safe on this point.
-- -----------------------------------------------------------------------------
SELECT COUNT(*) AS copies_archived,
       MIN(InvNum_dCreatedDate) AS first_seen,
       MAX(InvNum_dCreatedDate) AS last_seen
FROM [BA_00_LPD_CON].dbo.InvNum
WHERE DocType = 4
  AND DocState = 4
  AND OrigDocID > 0
  AND OrigDocID <> AutoIndex;
GO

-- -----------------------------------------------------------------------------
-- Check 3 — audit-table rows that make the original query's
-- MAX(iDocVersion) join misbehave:
--   a) several rows share the order's max iDocVersion  -> order joined twice
--      (its qty / litres / amount doubled)
--   b) iDocVersion is NULL                             -> join never matches,
--      app order shows as 'Evolution'
--   c) the max-version row isn't the CREATE row        -> UserName is the
--      last editor / canceller, not the person who placed it
-- Expect: all three 0 means the original query is safe on this point.
-- -----------------------------------------------------------------------------
WITH mx AS (
    SELECT iInvNumAutoIndex, MAX(iDocVersion) AS max_ver
    FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit
    GROUP BY iInvNumAutoIndex
)
SELECT
    (SELECT COUNT(*) FROM (
        SELECT a.iInvNumAutoIndex
        FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit a
        JOIN mx ON mx.iInvNumAutoIndex = a.iInvNumAutoIndex AND mx.max_ver = a.iDocVersion
        GROUP BY a.iInvNumAutoIndex HAVING COUNT(*) > 1) d)          AS a_orders_joined_twice,
    (SELECT COUNT(DISTINCT iInvNumAutoIndex)
     FROM mx WHERE max_ver IS NULL)                                     AS b_orders_null_version,
    (SELECT COUNT(DISTINCT a.iInvNumAutoIndex)
     FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit a
     JOIN mx ON mx.iInvNumAutoIndex = a.iInvNumAutoIndex AND mx.max_ver = a.iDocVersion
     WHERE a.cAction <> 'CREATE'
       AND NOT EXISTS (SELECT 1 FROM [SOAssistant].dbo._tb_SOOrderAssistantAudit c
                       WHERE c.iInvNumAutoIndex = a.iInvNumAutoIndex
                         AND c.iDocVersion = a.iDocVersion
                         AND c.cAction = 'CREATE'))                     AS c_user_is_not_creator;
GO

-- -----------------------------------------------------------------------------
-- Check 4 — invoiced metrics on split orders.
-- The function reads invoiced qty from the ORIGINAL row. If the copy was
-- invoiced further later, the original understates it.
-- Expect: 0 means invoiced figures are complete.
-- -----------------------------------------------------------------------------
SELECT COUNT(*) AS copies_invoiced_further
FROM [BA_00_LPD_CON].dbo.InvNum CP
JOIN [BA_00_LPD_CON].dbo.InvNum OG ON OG.AutoIndex = CP.OrigDocID
CROSS APPLY (SELECT SUM(fQtyProcessed) q FROM [BA_00_LPD_CON].dbo._btblInvoiceLines WHERE iInvoiceID = CP.AutoIndex) CPQ
CROSS APPLY (SELECT SUM(fQtyProcessed) q FROM [BA_00_LPD_CON].dbo._btblInvoiceLines WHERE iInvoiceID = OG.AutoIndex) OGQ
WHERE CP.DocType = 4
  AND CP.OrigDocID > 0
  AND CP.OrigDocID <> CP.AutoIndex
  AND CP.InvNum_dCreatedDate >= DATEADD(day, -60, GETDATE())
  AND CPQ.q > OGQ.q;
GO

-- -----------------------------------------------------------------------------
-- Check 5 — compare the function with the original query for ONE day.
--
-- The original query is used exactly as written, except for a date filter
-- on InvNum_dCreatedDate inside the NUM subquery so both sides look at the
-- same orders. Both sides are then rolled up per OrderNum.
--
-- Output 5a: totals for both sides.
-- Output 5b: only the orders that differ, with the reason.
--   'only in function'   / 'only in original'  -> order counted on one side
--   'numbers differ'                           -> qty/litres/amount mismatch
--   'source differs'                           -> SO Assistant vs Evolution
-- UserName differences are SHOWN but not treated as a mismatch: the
-- function shows the Evolution operator's name where the original shows
-- 'Evolution'.
-- -----------------------------------------------------------------------------
DECLARE @d date = '2026-11-05';   -- <-- pick a recent busy day

IF OBJECT_ID('tempdb..#orig') IS NOT NULL DROP TABLE #orig;
IF OBJECT_ID('tempdb..#fn')   IS NOT NULL DROP TABLE #fn;

-- Original query (unchanged apart from the created-date filter).
SELECT
OrderNum,NUM.OrderDate, NUM.InvDate, ISNULL(AA.cUsername,'Evolution') UserName
,ISNULL(CL.ulARCorpGroup,'Non Corporate') CorpGroup
,CL.Name CustomerName
, DATEPART(HOUR, InvNum_dCreatedDate) Hour
,CASE WHEN iInvNumAutoIndex IS NULL THEN 'Evolution' ELSE 'SO Assistant' END as OrderSource
,iDocVersion
,COUNT(OrderNum) OrdersPlaced
, SUM(OrderedLitres) OrderedLitres
, SUM(InvoicedLitres) InvoicedLitres
,SUM(LIN.fQuantityLineTotExcl) OrderedExclAmnt
,SUM(LIN.fQtyLastProcessLineTotExcl) InvoicedExclAmnt
,SUM(LIN.fQuantity) OrderedQty
INTO #orig
FROM (
	Select iInvoiceId ,SUM(fQuantity) fQuantity,SUM(fQtyProcessed) fQtyProcessed,SUM(fQuantityLineTotExcl) fQuantityLineTotExcl,SUM(fQtyLastProcessLineTotExcl) fQtyLastProcessLineTotExcl
	,SUM(STK.ulIISize*(LIN.fQuantity)) OrderedLitres
	, SUM(STK.ulIISize*(fQtyProcessed)) InvoicedLitres
	from [BA_00_LPD_CON].dbo._btblInvoiceLines LIN
	JOIN (SELECT * FROM [BA_00_LPD_CON].dbo.StkItem) STK ON STK.StockLink = LIN.iStockCodeID
	GROUP BY iInvoiceId
	) LIN
JOIN (SELECT * FROM [BA_00_LPD_CON].dbo.InvNum NUM WHERE DocType = 4 AND DocState IN (1, 4)
        AND InvNum_dCreatedDate >= @d AND InvNum_dCreatedDate < DATEADD(day, 1, @d)   -- added
     ) NUM ON NUM.AutoIndex = LIN.iInvoiceID
JOIN [BA_00_LPD_CON].dbo.Client CL on CL.DCLink = NUM.AccountID
LEFT JOIN  (
	Select AA1.* from [SOAssistant].[dbo].[_tb_SOOrderAssistantAudit] AA1
	JOIN (
		Select iInvNumAutoIndex ZZiInvNumAutoIndex,MAX(iDocVersion) ZZiDocVersion  from [SOAssistant].[dbo].[_tb_SOOrderAssistantAudit] AA1
			GROUP BY iInvNumAutoIndex
			)ZZ on ZZ.ZZiDocVersion = iDocVersion  and ZZiInvNumAutoIndex = iInvNumAutoIndex
	) AA on AA.iInvNumAutoIndex = NUM.AutoIndex
GROUP BY NUM.OrderDate, NUM.InvDate,  ISNULL(AA.cUsername,'Evolution')
, DATEPART(HOUR, InvNum_dCreatedDate)
,CASE WHEN iInvNumAutoIndex IS NULL THEN 'Evolution' ELSE 'SO Assistant' END
,ISNULL(CL.ulARCorpGroup,'Non Corporate')
,CL.Name,OrderNum
,iDocVersion;

SELECT * INTO #fn FROM dbo._fn_SOOrderIntake(@d, @d);

-- 5a. Totals
SELECT 'original' AS side, SUM(OrdersPlaced) AS orders, SUM(OrderedQty) AS qty,
       SUM(OrderedLitres) AS litres, SUM(OrderedExclAmnt) AS excl_amnt,
       SUM(InvoicedLitres) AS inv_litres, SUM(InvoicedExclAmnt) AS inv_excl_amnt
FROM #orig
UNION ALL
SELECT 'function', COUNT(*), SUM(OrderedQty), SUM(OrderedLitres), SUM(OrderedExclAmnt),
       SUM(InvoicedLitres), SUM(InvoicedExclAmnt)
FROM #fn;

-- 5b. Orders that differ
-- Both sides normalised to one collation: #orig carries Evolution's
-- (SQL_Latin1_General_CP1_CI_AS), #fn the SOAssistant default
-- (Latin1_General_CI_AS) — comparing them raw fails with Msg 468.
WITH o AS (
    SELECT OrderNum COLLATE DATABASE_DEFAULT AS OrderNum,
           SUM(OrdersPlaced) AS orders, SUM(OrderedQty) AS qty,
           SUM(OrderedLitres) AS litres, SUM(OrderedExclAmnt) AS amnt,
           MIN(OrderSource COLLATE DATABASE_DEFAULT) AS src,
           MIN(UserName COLLATE DATABASE_DEFAULT) AS usr, MIN(Hour) AS hr
    FROM #orig GROUP BY OrderNum COLLATE DATABASE_DEFAULT
), f AS (
    SELECT OrderNum COLLATE DATABASE_DEFAULT AS OrderNum,
           COUNT(*) AS orders, SUM(OrderedQty) AS qty,
           SUM(OrderedLitres) AS litres, SUM(OrderedExclAmnt) AS amnt,
           MIN(OrderSource COLLATE DATABASE_DEFAULT) AS src,
           MIN(UserName COLLATE DATABASE_DEFAULT) AS usr, MIN(CreatedHour) AS hr
    FROM #fn GROUP BY OrderNum COLLATE DATABASE_DEFAULT
)
SELECT COALESCE(o.OrderNum, f.OrderNum) AS OrderNum,
       CASE WHEN o.OrderNum IS NULL THEN 'only in function'
            WHEN f.OrderNum IS NULL THEN 'only in original'
            WHEN o.src <> f.src     THEN 'source differs'
            ELSE 'numbers differ' END AS reason,
       o.orders AS orig_orders, f.orders AS fn_orders,
       o.qty    AS orig_qty,    f.qty    AS fn_qty,
       o.litres AS orig_litres, f.litres AS fn_litres,
       o.amnt   AS orig_amnt,   f.amnt   AS fn_amnt,
       o.src    AS orig_source, f.src    AS fn_source,
       o.usr    AS orig_user,   f.usr    AS fn_user,
       o.hr     AS orig_hour,   f.hr     AS fn_hour
FROM o
FULL JOIN f ON f.OrderNum = o.OrderNum
WHERE o.OrderNum IS NULL OR f.OrderNum IS NULL
   OR o.src <> f.src
   OR o.orders <> f.orders
   OR ABS(ISNULL(o.qty, 0)    - ISNULL(f.qty, 0))    > 0.001
   OR ABS(ISNULL(o.litres, 0) - ISNULL(f.litres, 0)) > 0.001
   OR ABS(ISNULL(o.amnt, 0)   - ISNULL(f.amnt, 0))   > 0.01
ORDER BY reason, OrderNum;

-- 5c. Timing for one day / 31 days (target: < 2 s each; it runs on screen
-- open or the Refresh button only).
SET STATISTICS TIME, IO ON;
SELECT COUNT(*), SUM(OrderedLitres) FROM dbo._fn_SOOrderIntake(@d, @d);
SELECT COUNT(*), SUM(OrderedLitres) FROM dbo._fn_SOOrderIntake(DATEADD(day, -30, @d), @d);
SET STATISTICS TIME, IO OFF;
GO

-- -----------------------------------------------------------------------------
-- Check 6 — existing indexes on the two big Evolution tables (read-only).
-- Tells us whether _btblInvoiceLines can be looked up by iInvoiceID and
-- whether anything on InvNum covers InvNum_dCreatedDate.
-- -----------------------------------------------------------------------------
SELECT t.name AS table_name, i.name AS index_name, i.type_desc,
       STRING_AGG(CASE WHEN ic.is_included_column = 0 THEN c.name END, ', ')
           WITHIN GROUP (ORDER BY ic.key_ordinal) AS key_columns
FROM [BA_00_LPD_CON].sys.indexes i
JOIN [BA_00_LPD_CON].sys.tables t         ON t.object_id = i.object_id
JOIN [BA_00_LPD_CON].sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN [BA_00_LPD_CON].sys.columns c        ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE t.name IN ('_btblInvoiceLines', 'InvNum')
GROUP BY t.name, i.name, i.type_desc
ORDER BY t.name, i.name;
GO
