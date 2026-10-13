/* =============================================================================
   Customer "Over Terms" check – standalone test script
   -----------------------------------------------------------------------------
   Aging rule (from _etblTerms + Client.AccountTerms + Client.iAgeingTermID):

     Client.iAgeingTermID  -> _etblTerms.iTermID   (which term-set applies)
     Client.AccountTerms   (0..6)                  (which bucket applies)

     Bucket -> day threshold column on _etblTerms:
       0 -> iIntervalOption      (usually 0 = "current")
       1 -> iInterval1Days
       2 -> iInterval2Days
       3 -> iInterval3Days
       4 -> iInterval4Days
       5 -> iInterval5Days
       6 -> iInterval6Days

     _etblTerms.iAgeTypeOption:
       0 = Invoice date  (e.g. MNTH-INV, 7DAYINV)
       1 = Statement / month-end date  (e.g. MNTH-STM)

     CutoffDate:
       - Invoice date terms  : CutoffDate = today - TermsDays
                               Overdue if TxDate <  CutoffDate
       - Statement terms     : CutoffDate = EOMONTH(today - TermsDays)
                               Overdue if TxDate <= CutoffDate

     For AccountTerms = 0 ("Current"):
       - MNTH-STM  -> CutoffDate = EOMONTH(today)   (end of current month) --
                      so any outstanding invoice counts.
       - MNTH-INV  -> CutoffDate = today            -- any outstanding invoice
                      dated before today counts.

     OverdueAmount = SUM of PostAR.Outstanding for rows where Outstanding > 0
                     and TxDate satisfies the cutoff rule.

     over_terms = (Client.CheckTerms = 1) AND (OverdueAmount > 0)
   =========================================================================== */


/* ------------------------------------------------------------------
   1) Single-customer view – swap the account code and run
   ------------------------------------------------------------------ */
DECLARE @Account varchar(20) = 'GRE001';

SELECT
    C.Account,
    C.Name,
    C.AccountTerms,
    C.CheckTerms,
    t.iTermID,
    t.cCode                                       AS TermsCode,
    t.cDescription                                AS TermsDescription,
    t.iAgeTypeOption,                             -- 0=Invoice date, 1=Statement
    td.TermsDays,
    cutoff.CutoffDate,
    ISNULL((
        SELECT ROUND(SUM(p.Outstanding), 2)
        FROM PostAR p
        WHERE p.AccountLink = C.DCLink
    ), 0)                                         AS TotalOutstandingNet,
    ISNULL((
        SELECT ROUND(SUM(p.Outstanding), 2)
        FROM PostAR p
        WHERE p.AccountLink = C.DCLink
          AND ISNULL(p.Outstanding, 0) > 0
          AND (
                (ISNULL(t.iAgeTypeOption,0) = 1 AND CAST(p.TxDate AS date) <= cutoff.CutoffDate)
             OR (ISNULL(t.iAgeTypeOption,0) = 0 AND CAST(p.TxDate AS date) <  cutoff.CutoffDate)
          )
    ), 0)                                         AS OverdueAmount,
    CASE
        WHEN ISNULL(C.CheckTerms, 0) = 1
         AND ISNULL((
                SELECT SUM(p.Outstanding)
                FROM PostAR p
                WHERE p.AccountLink = C.DCLink
                  AND ISNULL(p.Outstanding, 0) > 0
                  AND (
                        (ISNULL(t.iAgeTypeOption,0) = 1 AND CAST(p.TxDate AS date) <= cutoff.CutoffDate)
                     OR (ISNULL(t.iAgeTypeOption,0) = 0 AND CAST(p.TxDate AS date) <  cutoff.CutoffDate)
                  )
             ), 0) > 0
        THEN 1 ELSE 0
    END                                            AS OverTerms
FROM Client C
LEFT JOIN _etblTerms t ON t.iTermID = C.iAgeingTermID
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
    SELECT CASE
        WHEN ISNULL(t.iAgeTypeOption,0) = 1
            THEN EOMONTH(DATEADD(day, -td.TermsDays, CAST(GETDATE() AS date)))
        ELSE DATEADD(day, -td.TermsDays, CAST(GETDATE() AS date))
    END AS CutoffDate
) cutoff
WHERE C.Account = @Account;


/* ------------------------------------------------------------------
   2) See which PostAR rows are contributing to the overdue amount
   ------------------------------------------------------------------ */
;WITH t AS (
    SELECT
        C.DCLink,
        C.Account,
        C.Name,
        ISNULL(tm.iAgeTypeOption, 0) AS AgeTypeOption,
        CASE ISNULL(C.AccountTerms, 0)
            WHEN 0 THEN ISNULL(tm.iIntervalOption, 0)
            WHEN 1 THEN ISNULL(tm.iInterval1Days, 0)
            WHEN 2 THEN ISNULL(tm.iInterval2Days, 0)
            WHEN 3 THEN ISNULL(tm.iInterval3Days, 0)
            WHEN 4 THEN ISNULL(tm.iInterval4Days, 0)
            WHEN 5 THEN ISNULL(tm.iInterval5Days, 0)
            WHEN 6 THEN ISNULL(tm.iInterval6Days, 0)
            ELSE 0
        END AS TermsDays
    FROM Client C
    LEFT JOIN _etblTerms tm ON tm.iTermID = C.iAgeingTermID
    WHERE C.Account = @Account
),
c AS (
    SELECT
        t.*,
        CASE
            WHEN t.AgeTypeOption = 1
                THEN EOMONTH(DATEADD(day, -t.TermsDays, CAST(GETDATE() AS date)))
            ELSE DATEADD(day, -t.TermsDays, CAST(GETDATE() AS date))
        END AS CutoffDate
    FROM t
)
SELECT
    c.Account,
    c.Name,
    c.AgeTypeOption,
    c.TermsDays,
    c.CutoffDate,
    p.TxDate,
    p.Reference,
    p.Description,
    p.Debit,
    p.Credit,
    p.Outstanding,
    DATEDIFF(day, p.TxDate, GETDATE()) AS DaysOld,
    CASE
        WHEN c.AgeTypeOption = 1 AND CAST(p.TxDate AS date) <= c.CutoffDate THEN 'OVERDUE'
        WHEN c.AgeTypeOption = 0 AND CAST(p.TxDate AS date) <  c.CutoffDate THEN 'OVERDUE'
        ELSE 'current'
    END AS Bucket
FROM c
INNER JOIN PostAR p ON p.AccountLink = c.DCLink
WHERE ISNULL(p.Outstanding, 0) > 0
ORDER BY p.TxDate;


/* ------------------------------------------------------------------
   3) List every customer flagged as OVER TERMS
   ------------------------------------------------------------------ */
SELECT
    C.Account,
    C.Name,
    tm.cCode                         AS TermsCode,
    tm.iAgeTypeOption                AS AgeType,   -- 0=Invoice, 1=Statement
    C.AccountTerms,
    td.TermsDays,
    cutoff.CutoffDate,
    overdue.OverdueAmount
FROM Client C
LEFT JOIN _etblTerms tm ON tm.iTermID = C.iAgeingTermID
CROSS APPLY (
    SELECT CASE ISNULL(C.AccountTerms, 0)
        WHEN 0 THEN ISNULL(tm.iIntervalOption, 0)
        WHEN 1 THEN ISNULL(tm.iInterval1Days, 0)
        WHEN 2 THEN ISNULL(tm.iInterval2Days, 0)
        WHEN 3 THEN ISNULL(tm.iInterval3Days, 0)
        WHEN 4 THEN ISNULL(tm.iInterval4Days, 0)
        WHEN 5 THEN ISNULL(tm.iInterval5Days, 0)
        WHEN 6 THEN ISNULL(tm.iInterval6Days, 0)
        ELSE 0
    END AS TermsDays
) td
CROSS APPLY (
    SELECT CASE
        WHEN ISNULL(tm.iAgeTypeOption,0) = 1
            THEN EOMONTH(DATEADD(day, -td.TermsDays, CAST(GETDATE() AS date)))
        ELSE DATEADD(day, -td.TermsDays, CAST(GETDATE() AS date))
    END AS CutoffDate
) cutoff
CROSS APPLY (
    SELECT ISNULL((
        SELECT ROUND(SUM(p.Outstanding), 2)
        FROM PostAR p
        WHERE p.AccountLink = C.DCLink
          AND ISNULL(p.Outstanding, 0) > 0
          AND (
                (ISNULL(tm.iAgeTypeOption,0) = 1 AND CAST(p.TxDate AS date) <= cutoff.CutoffDate)
             OR (ISNULL(tm.iAgeTypeOption,0) = 0 AND CAST(p.TxDate AS date) <  cutoff.CutoffDate)
          )
    ), 0) AS OverdueAmount
) overdue
WHERE ISNULL(C.CheckTerms, 0) = 1
  AND overdue.OverdueAmount > 0
ORDER BY overdue.OverdueAmount DESC;
