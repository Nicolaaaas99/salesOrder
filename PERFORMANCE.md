# Performance rules

The app shares SQL Server with Evolution, M-Files and everything else the
business runs. On 2026-11-06 one query from this app (the Order Assistant
litres strip) held the server at ~95% CPU from 08:30 until the hotfix at
12:06. These rules exist so that doesn't happen again.

## The one-line rule

**Cost of a query × number of open sessions × how often it runs.** A 2-second
query is fine on a button click. The same query on a 60-second timer with 10
reps online is 20 seconds of SQL CPU per minute, and it keeps growing.

## Checklist for any new query or screen

1. **When does it run?** Button click, screen open, customer open, window
   focus, timer? Anything that runs automatically (timer / focus / event)
   needs extra care.
2. **Timers:** at least 60 s apart, only while the tab is visible, and only
   one request in flight at a time. A new `setInterval` fails
   `test_performance_guards.py` until it's reviewed and allowlisted.
3. **Window focus:** use `useFocusRefresh` (max once per 30 s), never a raw
   `window.addEventListener("focus", ...)`. Reps alt-tab to Evolution all day.
4. **Same answer for every rep?** (e.g. "orders today") Cache it on the
   backend with `services/short_cache.py` and invalidate on writes.
5. **SQL shapes to avoid on Evolution tables** (`InvNum`, `_btblInvoiceLines`,
   `PostST`, `PostAR`):
   - `JOIN ... ON (a = x OR b = x)`: this disables the index. Use two
     `UNION ALL` branches.
   - `CAST(col AS DATE) BETWEEN ...` / `CAST(col AS DATE) = ...`: use
     `col >= :from AND col < :to_plus_one_day`.
   - Queries whose cost grows with history ("everything since the 1st of
     the month") on a timer: bound them (e.g. by AutoIndex range) or cache.
6. **Measure before shipping.** Run the query in SSMS with
   `SET STATISTICS TIME, IO ON;` against production-sized data. Rough budget:
   - timer / focus / every-customer paths: **< 200 ms, < 10k reads**
   - screen open / button click: **< 2 s**
   - reports / exports: **< 30 s** (the hard timeout)

## Built-in safety nets

| Guard | Where | What it does |
|---|---|---|
| Query timeout | `backend/database.py` | Any SQL statement > `DB_QUERY_TIMEOUT_SECONDS` (default 30) is cancelled, so the request fails instead of the server stalling. |
| Slow-query log | `backend/database.py` | Any statement > `DB_SLOW_QUERY_SECONDS` (default 1.0) is logged as `SLOW SQL` with the API path. |
| Slow-request log | `backend/main.py` | Any API call > `SLOW_REQUEST_SECONDS` (default 2.0) is logged as `SLOW REQUEST`. |
| Static guards | `backend/tests/test_performance_guards.py` | Fails on OR-in-JOIN, CAST-date filters, unreviewed timers / focus listeners. |

All three thresholds can be overridden in the backend `.env`.

Logs are in `C:\inetpub\salesOrder\backend\logs\stdout_*.log` on the server.
After any deploy, search them for `SLOW` to catch problems on day one.

## Checking SQL Server yourself (SSMS)

**CPU per minute, last ~4 hours:**
```sql
DECLARE @ms_now bigint = (SELECT ms_ticks FROM sys.dm_os_sys_info);
SELECT TOP 240
    DATEADD(ms, -1 * (@ms_now - rb.[timestamp]), GETDATE()) AS sample_time,
    rb.record.value('(./Record/SchedulerMonitorEvent/SystemHealth/ProcessUtilization)[1]', 'int') AS sql_server_cpu_pct
FROM (SELECT [timestamp], CONVERT(xml, record) AS record
      FROM sys.dm_os_ring_buffers
      WHERE ring_buffer_type = N'RING_BUFFER_SCHEDULER_MONITOR'
        AND record LIKE N'%<SystemHealth>%') rb
ORDER BY sample_time DESC;
```

**Top CPU queries since a time:**
```sql
SELECT TOP 20
    qs.total_worker_time / 1000                      AS total_cpu_ms,
    qs.execution_count,
    qs.total_worker_time / qs.execution_count / 1000 AS avg_cpu_ms,
    qs.total_logical_reads / qs.execution_count      AS avg_reads,
    qs.creation_time, qs.last_execution_time,
    SUBSTRING(st.text, 1, 400)                       AS query_text
FROM sys.dm_exec_query_stats qs
CROSS APPLY sys.dm_exec_sql_text(qs.sql_handle) st
WHERE qs.last_execution_time >= DATEADD(hour, -1, GETDATE())
ORDER BY qs.total_worker_time DESC;
```

**Indexes SQL Server thinks are missing on the tables we query:**
```sql
SELECT TOP 20
    OBJECT_NAME(d.object_id, d.database_id) AS table_name,
    d.equality_columns, d.inequality_columns, d.included_columns,
    s.user_seeks, s.avg_user_impact,
    s.user_seeks * s.avg_total_user_cost * s.avg_user_impact AS benefit
FROM sys.dm_db_missing_index_details d
JOIN sys.dm_db_missing_index_groups g ON g.index_handle = d.index_handle
JOIN sys.dm_db_missing_index_group_stats s ON s.group_handle = g.index_group_handle
WHERE d.database_id = DB_ID('BA_00_LPD_CON')
  AND OBJECT_NAME(d.object_id, d.database_id) IN ('InvNum', '_btblInvoiceLines', 'PostST', 'Client', 'StkItem')
ORDER BY benefit DESC;
```
Indexes on Evolution tables must be agreed with IT / the Evolution partner
before they're created.
