/******************************************************************************
 ONE-OFF: drop and recreate _tb_SOCustomerItemMap (Order Import mappings)
 with cCustomerAccount before cScope, the same column order as
 _tb_SOImportPriceDecision.

 !! DELETES EVERY SAVED ITEM MAPPING. !!
 Lines that were matched through a mapping must be mapped again on the next
 import. Run by hand only — NOT part of create_views.sql, which is re-run on
 every deploy.

 Run as a login that may grant rights (sysadmin / db_owner). Dropping the
 table also drops its grants, so step 3 must be run again (edit the login).
******************************************************************************/
USE [SOAssistant];
GO

-- 1. What will be lost (for the record).
SELECT COUNT(*) AS mappings_to_delete
FROM dbo._tb_SOCustomerItemMap;
GO

-- 2. Drop and recreate (definition identical to create_views.sql).
DROP TABLE IF EXISTS dbo._tb_SOCustomerItemMap;
GO

CREATE TABLE dbo._tb_SOCustomerItemMap (
    idItemMap         INT IDENTITY(1,1) NOT NULL,
    cCustomerAccount  VARCHAR(20)    NULL,
    cScope            VARCHAR(120)   NOT NULL,
    cCustCode         VARCHAR(40)    NOT NULL,
    cCustItemCode     VARCHAR(40)    NOT NULL,
    cCustDescription  NVARCHAR(200)  NULL,
    cStockCode        VARCHAR(20)    NOT NULL,
    cCreatedBy        VARCHAR(50)    NOT NULL,
    dtCreatedAt       DATETIME2(7)   NOT NULL
        CONSTRAINT DF_SOCustomerItemMap_dt DEFAULT (sysdatetime()),
    cUpdatedBy        VARCHAR(50)    NULL,
    dtUpdatedAt       DATETIME2(7)   NULL,
    CONSTRAINT PK_SOCustomerItemMap PRIMARY KEY CLUSTERED (idItemMap),
    CONSTRAINT UX_SOCustomerItemMap_Key UNIQUE (cScope, cCustCode, cCustItemCode),
    CONSTRAINT CK_SOCustomerItemMap_HasCode CHECK (cCustCode <> '' OR cCustItemCode <> '')
);
GO

-- 3. Re-grant the app login (dropped with the table). Edit the login name.
--    Skip if the app connects as sysadmin / db_owner (typical on a dev box).
/*
DECLARE @login sysname = N'<app_login>';   -- <-- edit this
DECLARE @sql   nvarchar(max) =
    N'GRANT INSERT, UPDATE, DELETE ON OBJECT :: dbo._tb_SOCustomerItemMap TO '
    + QUOTENAME(@login) + N';';
EXEC sp_executesql @sql;
*/

-- 4. Check: column order and an empty table.
SELECT c.column_id, c.name
FROM sys.columns c
WHERE c.object_id = OBJECT_ID('dbo._tb_SOCustomerItemMap')
ORDER BY c.column_id;

SELECT COUNT(*) AS mappings_now FROM dbo._tb_SOCustomerItemMap;   -- expect 0
GO
