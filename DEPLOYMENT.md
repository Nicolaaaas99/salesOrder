# Deployment runbook

Code changes deploy by pushing + pulling the repo. **Database and Evolution
changes don't** — those have to be applied by hand on the server. This
file is the single source of truth for what to do between a `git pull`
and "the app is live".

- The **Pending deploy** section at the top is a checklist of
  everything that needs to happen on the server next time you deploy.
  Append to this list as we make new server-side changes; tick items off
  (or move them under a dated "Done" heading) once applied in prod.
- The **Runbook** section below is the standing "how to" — safe
  commands, ordering, verification. It doesn't change often.

---

## Pending deploy

Order matters where noted. Everything else can be done in any order.

### Release — Warehouse rules, Deliveries by route, Order Intake drill page
**SQL first, then the app** (the "fix card" button writes the new audit columns).
- [ ] Re-run `backend/sql/create_views.sql` (safe to re-run). `_vw_SOCustomerDetail`
      adds `DistCentre` (the customer card's Dist Centre);
      `_tb_SOCustomerAssignmentAudit` gets `cPrevDistCentre` / `cNewDistCentre`.
- [ ] Verify: `SELECT TOP 1 DistCentre FROM [SOAssistant].dbo._vw_SOCustomerDetail;` runs.
      The same run creates `_tb_SODraftOrder` (draft orders) if it's missing.
- [ ] Deploy the app with `deploy.ps1`.
- [ ] Users & Roles (admin): every registered user has a **Warehouse** (MAK / POL).
      An AGENT without one now gets "ask an admin" at login and can't pick it.
- [ ] Smoke test as a SUPERVISOR: top-bar warehouse dropdown switches MAK ↔ POL.
      Open a customer whose card DC is the other DC → amber banner with
      "Switch to …"; switching moves + re-prices the lines already added.
      "Customer is MAK — fix card" (after a confirm) sets the customer's Dist
      Centre and the banner disappears; check the audit row in
      `_tb_SOCustomerAssignmentAudit` (cPrev/cNewDistCentre).
- [ ] Order summary (open any order): lines show a **Whse** column.
- [ ] Mismatch BLOCKS: with the banner showing, Continue (Order Assistant) and
      Save (Sales Order Entry) and Save / Update draft (both screens) refuse with
      the DC message; the server refuses orders and drafts too.
      Customers with a blank / unknown card DC are not blocked.
- [ ] Smoke test as an AGENT: top bar shows the warehouse with a lock; no switch;
      a customer on the other DC can't be ordered (ask a supervisor).
- [ ] Users & Roles opens on **Registered**, with a Status column (Online / Away)
      and "n online now". As a SUPERVISOR: card visible, no admins listed, no
      Add / role / active changes, only an agent's Warehouse can be changed.
- [ ] Order Assistant: the draft panel shows Limit / Outst. / Existing / This
      order on one always-visible row (no Details toggle).
- [ ] Analytics → Deliveries: all routes listed (no route picker), Litres + Strike
      rate columns, sortable headers, search on route or customer.
- [ ] Analytics → Order Intake: clicking a bar opens a page (Back keeps the
      report as it was); "Show by" picks the levels.
- [ ] Drafts: Save draft on a customer, reopen it from Drafts (lines take the
      current warehouse). Drafts are deleted 3 days after first save.
- [ ] Login: after signing in, Chrome / Edge offers "Save password?". F12 →
      Console shows a `[login] password save:` line explaining if it didn't.
- Forecasting changes stay OUT of this release (not finished).

### Release — Order Import, Price Review, inclusive-tax saves (same deploy as below)
**Before deploy.ps1** — the new backend imports these, so it won't start without them:
- [ ] Install the two new Python packages into the server's backend Python
      (the one `backend\start_backend.bat` runs):
      `<python.exe> -m pip install "pdfplumber>=0.11,<0.12" "python-multipart>=0.0.9"`
- [ ] `create_views.sql` (the re-run below) creates `_tb_SOCustomerItemMap`,
      `_tb_SOImportPriceDecision`, `_tb_SOImportDocument`, `_tb_SOImportReview`.
      Grant the app login (`DB_USERNAME` in `backend\.env`) write access, in SSMS:
      ```sql
      USE [SOAssistant];
      GRANT INSERT, UPDATE, DELETE ON dbo._tb_SOCustomerItemMap      TO [<app_login>];
      GRANT INSERT, UPDATE         ON dbo._tb_SOImportPriceDecision TO [<app_login>];
      GRANT INSERT, UPDATE         ON dbo._tb_SOImportDocument      TO [<app_login>];
      GRANT INSERT, UPDATE         ON dbo._tb_SOImportReview        TO [<app_login>];
      ```
- `sql/rollout/*` and `sql/diagnostics/*` are manual tools — NOT part of this deploy.
- [ ] Smoke test: Order Import screen opens and reads a PO PDF; Price Review screen opens.

### Release — Order Intake: DC + Route filters and drill levels
**SQL first, then the app.** The new backend reads `DistCentre` / `Route`
from the function; the old app ignores them, so SQL first costs no downtime.
- [ ] Re-run `backend/sql/create_views.sql` end-to-end on the server (safe to re-run).
      `_fn_SOOrderIntake` now counts **DC warehouse lines only** (48 + 62 →
      Polokwane, 49 + 61 → Makhado, incl. the damaged-goods warehouses);
      orders with no DC line (01.HOF_MAN1 bulk/factory, 01.HOF, FFACT, …)
      drop out. Adds `DistCentre` (from the lines) and `Route` (customer
      card, blank → 'No route'); agent not found → 'SDK'.
- [ ] Verify: `SELECT CASE WHEN OBJECT_DEFINITION(OBJECT_ID('[SOAssistant].dbo._fn_SOOrderIntake')) LIKE '%DG_01.HOF_MAK%' THEN 'current' ELSE 'OLD' END;` returns `current`.
- [ ] Deploy the app with `deploy.ps1`.
- [ ] Smoke test: Analytics → Order Intake. The DC, Route, Corporate group and
      User filters are searchable; picking a DC narrows the Route list. Click a
      bar: the modal opens as DC → Route → Corporate group → Customer → Orders,
      and the export has DistCentre + Route columns.

### Release — Order Intake: Lost toggle, unprocessed + archived only
**SQL first, then the app.** The new backend reads the `Lost*` columns, so
the new app on the old function would error. The old app on the new
function is fine, so applying the SQL first causes no downtime.
- [ ] Re-run `backend/sql/create_views.sql` end-to-end on the server (safe to re-run).
      `_fn_SOOrderIntake` now counts DocState 1 + 4 only (DocState 3 is
      always a split copy), takes Invoiced amount from
      `fQtyProcessedLineTotExcl`, and adds `LostQty` / `LostLitres` /
      `LostExclAmnt` (ordered − invoiced on archived orders only).
- [ ] Verify: `SELECT CASE WHEN OBJECT_DEFINITION(OBJECT_ID('[SOAssistant].dbo._fn_SOOrderIntake')) LIKE '%LostExclAmnt%' THEN 'current' ELSE 'OLD' END;` returns `current`.
- [ ] Deploy the app with `deploy.ps1`.
- [ ] Smoke test: Analytics → Order Intake → toggle **Lost**. The KPI tiles show
      a "lost" line, and the drill modal has Lost litres / Lost excl columns,
      with "not invoiced yet" shown for unprocessed orders. Check `01.HOFSO871054` (5 Aug 2026):
      lost = 6 × Fruit Coctail 1 KG = R157.02. The **Pending** toggle and the
      modal's "Pending only" switch list the unprocessed orders.
- [ ] Smoke test: Order Assistant top strip (Today / This week / This month)
      shows non-zero litres + called for an agent who placed an order today.
      It showed 0 for everyone since 80d2045 (2026-11-06): a `:min_id` inside
      a SQL comment made `/auth/me/performance` fail on every call.

### Release 2026-11-09 — Sales Order Entry, Written order, order source, Order Intake
Do these in this order: **SQL first, then the app.** (If the backend goes
first, Sales Order Entry orders placed in between get no source and the
backfill would label them ORDER_ASSISTANT.)
- [ ] Re-run `backend/sql/create_views.sql` end-to-end on the server. This release adds:
      - `cSourceApp` column on `_tb_SOOrderAssistantAudit` (+ automatic
        backfill: CREATE -> ORDER_ASSISTANT, UPDATE -> SALES_ORDER_ENTRY).
      - Contact-log `cSourceApp` relabel `SO_ASSISTANT` -> `ORDER_ASSISTANT` (automatic).
      - `CK_SOCustomerContactLog_Outcome` rebuilt to accept `WRITTEN_ORDER`.
      - `IX_SOOrderAssistantAudit_InvNumAction` index + `_fn_SOOrderIntake`
        (dropped and re-created each run — it's a multi-statement function).
- [ ] Verify: `SELECT CASE WHEN OBJECT_DEFINITION(OBJECT_ID('[SOAssistant].dbo._fn_SOOrderIntake')) LIKE '%X.AgentID%' THEN 'current' ELSE 'OLD' END;` returns `current`.
- [ ] **One time only**, because the server's copy of
      `frontend/tsconfig.tsbuildinfo` blocks `git pull`:
      `cd C:\inetpub\salesOrder; git checkout -- frontend/tsconfig.tsbuildinfo; git pull`
      — then deploy with `deploy.ps1` (Runbook step 6). No new Python
      packages for prod (`pytest` / `httpx` in requirements.txt are test-only).
- [ ] Smoke test:
      - Sales Order Entry -> Create a new order: only corporate customers
        (Spar / Checkers / Boxer / Pick n Pay / OK) in the dropdown. Place
        one; its `_tb_SOOrderAssistantAudit` CREATE row has
        `cSourceApp = 'SALES_ORDER_ENTRY'` and the right `iInvNumAutoIndex`.
      - Order Assistant: leave a customer without ordering -> modal has
        "Written order"; saving it writes `cOutcome = 'WRITTEN_ORDER'`.
      - Analytics -> Order Intake (Supervisor/Admin only; agents don't see
        the card): a known app order shows in the hour it was placed
        (local time); Evolution bars green, SO Assistant blue.
      - Search `backend\logs\stdout_*.log` for `SLOW` at the end of day 1.
- [ ] Forecasting ships switched OFF (`FORECASTING_UI_ENABLED = false`). If
      the forecasting tables aren't on prod, the Order Assistant logs a
      "Target lookup failed" line per customer and falls back to normal
      suggestions — expected, harmless.

### SQL — safe to re-run end-to-end
- [ ] Run `backend/sql/create_views.sql` on the server (SSMS). Adds /
      updates all views, tables, columns, indexes. Idempotent — guarded
      with `CREATE OR ALTER`, `IF OBJECT_ID IS NULL`, `IF NOT EXISTS` so
      re-running is a no-op where the object already exists.
      **Latest additions:**
      - Two new BIT columns `bPrevOnHold` / `bNewOnHold` on
        `_tb_SOCustomerAssignmentAudit` for the On-hold toggle in the
        Edit modal (idempotent `ALTER TABLE ADD`).
      - New table `_tb_SOTargets` + supporting index `IX_SOTargets_Username`
        for Phase 4 litre targets. Guarded by `IF OBJECT_ID IS NULL` so
        re-running is a no-op.
      - **GRANT template update** — `_tb_SOTargets` needs INSERT/UPDATE/
        DELETE (not just INSERT) because the Analytics Performance screen
        lets users CRUD targets in-app. If you've already run the GRANT
        template, append the new line for the targets table.
      - New view `_vw_SOCategoryOptions` (STRING_SPLIT on
        `_rtblUserDict.cLookupOptions` for `STKITEM.ulIIMainCategory`) —
        powers the category dropdown in the target form. SELECT on dbo
        schema already covers it.
      - New `cTelesales VARCHAR(100) NULL` column on `_tb_SOTargets`
        (idempotent `ALTER TABLE ADD`) — enables telesales-rep scoped
        targets alongside the existing agent/route/customer/category/
        stock scopes.
      - **`CK_SOTargets_HasScope` rebuild** — the original constraint
        was defined before `cTelesales` existed, so on pre-existing
        tables it rejects telesales-only targets (DB sees every known-
        to-it scope column as NULL and 547s). Script now DROPs + ADDs
        the constraint when it's missing EITHER `cTelesales` OR
        `cSalesRep`, so re-running after the rebuild is a no-op.
      - New `cSalesRep VARCHAR(50) NULL` column on `_tb_SOTargets`
        (idempotent `ALTER TABLE ADD`) — new scope dim for "customers
        whose assigned sales rep is this" (filters `Client.RepID →
        SalesRep.Code`). Pairs with the rebuilt HasScope CHECK above.
      - `IX_SOTargets_Username` rebuilt to INCLUDE `cSalesRep` on the
        hot path. Script drops the old index when it's present but
        doesn't include that column, then recreates it. Idempotent.
      - `_vw_SOInventory` now exposes `StkItem.ulIIMainCategory` as
        the `Category` column so `/lookups/inventory` can return it +
        filter by it (powers the Target form's stock→category auto-
        fill and category→stock dropdown filter). Just a `CREATE OR
        ALTER VIEW` — no data migration.
      - New `iAgentID INT NULL` column on `_tb_SOTargets` (idempotent
        `ALTER TABLE ADD`). Preferred over `cUsername` for the
        "Placed by" measurement — stable `_rtblAgents.idAgents` key
        instead of drift-prone text. Both populated on save going
        forward; measurement prefers the id with a case-insensitive
        username fallback for legacy rows. Pairs with the `HasScope`
        CHECK rebuild above (now includes `iAgentID`).
      - **New `_tb_SOAppUsers` table** — Phase 3.1 user-rights
        whitelist. Login is now **gated on membership**: an Evolution
        login with no active row in this table cannot sign into the
        Sales Order Assistant (frontend shows "ask your admin"). One
        row per Evolution agent, role in {`AGENT`, `SUPERVISOR`,
        `ADMIN`}, `bActive` flag for shadow-delete. Guarded by
        `IF OBJECT_ID IS NULL` so re-running is a no-op.
      - **Target measurement now reads from
        `_vw_SOCustomerInvoiceHistory`** instead of its own custom
        joins — the exact same view the Analytics Customer Sales
        History pivot uses, so target totals and CSH stay in lockstep.
        Caveat: the view's PostST half is capped at the last 12 weeks
        (`TxDate >= GETDATE() - 84 days`), so a `Custom`-period target
        spanning more than 12 weeks of invoiced history will
        under-count. Daily / Weekly / Monthly all fit comfortably
        inside the cap. Lift the cap in `_vw_SOCustomerInvoiceHistory`
        if long custom windows become important.

### SQL — one-time backfills (templates live at the bottom of create_views.sql)
- [ ] **`dDeliveryDate` backfill** for pre-existing PLACED rows in
      `_tb_SOCustomerContactLog`. The template uses `CROSS APPLY TOP 1`
      against `InvNum`. Preview-count first, then run the UPDATE.
- [ ] **`iInvNumAutoIndex` backfill** for pre-existing PLACED rows in
      `_tb_SOCustomerContactLog`. Same shape (CROSS APPLY TOP 1 on
      `InvNum.AutoIndex` by `OrderNum`). You've got your own saved copy.
- [ ] **Audit `cUsername` casing fix + Target `iAgentID` backfill**
      (template at the bottom of `create_views.sql`). Canonicalises
      historical audit/target rows against `_rtblAgents.cAgentName` so
      drift ("Brendas" vs "BrendaS") no longer causes
      Target-measurement misses. Preview-count SELECTs run first; the
      UPDATEs are idempotent (re-run is a no-op once reconciled).

### Evolution config (manual via Evolution UI)
- [ ] Create `INACTIVE` **sales rep** in SalesRep master.
- [ ] Create `INACTIVE` **telesales option** in Client user-dict for
      `ulARTelesalesRep`.
- [ ] Create `INACTIVE` **route option** in Client user-dict for
      `ulARRoute`.

### Permissions — only on fresh installs
- [ ] If the backend login has never been used on `[SOAssistant]`, run
      the GRANT template at the bottom of `create_views.sql` (replace
      `<app_login>` first). Grants `SELECT` on the schema + `INSERT` on
      the three app tables + `UPDATE` on `[BA_00_LPD_CON].dbo.Client`
      (needed for the Customer Sales History maintenance modal). Also
      grants `INSERT, UPDATE, DELETE` on `_tb_SOAppUsers` for the
      admin management UI.

### First-admin bootstrap — only on fresh installs
- [ ] **Phase 3.1 user-rights foundation**: login is gated on
      `_tb_SOAppUsers` membership. With that table empty on first
      deploy, NOBODY (including you) can log in. Run the bootstrap
      INSERT template at the bottom of `create_views.sql`:
      1. Find your Evolution agent id:
         `SELECT idAgents, cAgentName FROM [BA_00_LPD_CON].dbo._rtblAgents WHERE cAgentName = '<your-login>';`
      2. Uncomment the `INSERT INTO _tb_SOAppUsers ... VALUES (<id>, 'ADMIN', 1, 'bootstrap');` block and edit the id.
      3. Run once. From then on you add + role-change everyone else via the Users & Roles screen in the app (ships with Phase 3.3).

### App deploy
- [ ] Pull latest on the server, restart the FastAPI backend service.
      New / updated endpoints since last deploy:
      - `PATCH /lookups/customers/bulk-assignment` — no schema change
        needed; reuses `_tb_SOCustomerAssignmentAudit` (one row per
        customer per bulk edit).
      - `GET /lookups/agent-options` — merged dropdown source
        (SalesRep ∪ TelesaleOptions ∪ Audit cUsername) for the Target
        form's Agent picker.
      - `GET /lookups/customers` now accepts `route`, `telesale`,
        `sales_rep` query params; `GET /lookups/inventory` accepts
        `category`. Powers Target-form dropdown filtering.
      - `GET /targets/mine` — every active target where the logged-in
        user is on `cUsername` / `cTelesales` / `cSalesRep`. Powers
        the Order Assistant's "All my targets" modal; `/my-primary`
        still works (used nowhere since the strip migrated).
- [ ] Build the frontend (`npm run build` in `frontend/`) and push `dist/`
      to wherever the SPA is served.

### Smoke test
- [ ] Analytics → Customer Sales History: hierarchy sidebar on the
      right, drill into a route works, pencil icon on customer rows opens
      the Edit modal with three dropdowns + Note.
- [ ] Edit modal save → confirm new row in
      `_tb_SOCustomerAssignmentAudit`.
- [ ] Bulk edit: tick a few customer checkboxes (or click
      "Select N" on an expanded group row); confirm the floating action
      bar appears; open bulk edit, pick a value in one dropdown, apply;
      confirm one audit row per customer, each with prev/new captured.
- [ ] On-hold toggle in the Edit modal (single): flip Active → On hold
      for a customer, save, confirm `Client.On_Hold` is 1 and audit row
      has `bPrevOnHold = 0`, `bNewOnHold = 1`.
- [ ] Analytics → Deliveries: From / To pickers, Reason column renders
      chips (PLACED green / outcome yellow / Not contacted red /
      — before 2026-10-26).
- [ ] Export: file name reflects range, Reason column uses uppercase codes.
- [ ] Place a test order in Order Assistant for a session date ≠ today
      — confirm `_tb_SOCustomerContactLog` has `dDeliveryDate` **and**
      `iInvNumAutoIndex` populated on the new PLACED row.

---

## Runbook (how to deploy, in order)

1. **Snapshot the DB.** Your usual backup — always, before any schema change.
2. **Create any new Evolution master data** (reps / routes / telesales
   options) via Evolution UI first. The `_vw_SO*Options` views read from
   `_rtblUserDict`, so new user-dict entries only appear in the app's
   dropdowns after Evolution has saved them.
3. **Run `create_views.sql`** in SSMS against `[SOAssistant]`. Verification
   `SELECT`s at the bottom print row counts — eyeball them.
4. **Run any one-time backfills** that are listed in Pending deploy.
   Each backfill template:
   - Starts with a `SELECT COUNT(*)` so you can preview the row count.
   - Has an `UPDATE … WHERE … IS NULL` body so re-running is a no-op.
5. **Grant permissions** (fresh installs only — see Pending deploy for
   when this is required).
6. **Deploy the code with the script — never by hand:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File C:\inetpub\salesOrder\deploy.ps1
   ```
   It pulls, builds and restarts the IIS site, stops at the first error,
   rolls back to the previous version if the build fails, and ends with
   `DEPLOYED <commit>`. Confirm in a browser: `https://<server>/version.json`
   shows the same commit. (Typing the steps by hand is what kept "deploying"
   old code: PowerShell carried on after a failed `git pull`.)
7. **Smoke test** per the checklist above.

### Why `create_views.sql` is always safe to re-run
- Views → `CREATE OR ALTER` (overwrites definition only).
- Tables → `IF OBJECT_ID … IS NULL BEGIN CREATE TABLE` (creates only
  when missing; existing data untouched).
- Columns → `IF NOT EXISTS (sys.columns … 'colname') ADD` (idempotent).
- Indexes → `IF NOT EXISTS (sys.indexes …) CREATE` (idempotent).
- Audit migration (`_tb_SOOrderAssistantAudit` from `[BA_00_LPD_CON]`) →
  guarded with `AND NOT EXISTS (SELECT 1 FROM destination)`, so it only
  moves rows the first time.
- Permissions block → commented out by default; uncomment manually for
  fresh installs.
- Backfill templates → commented out by default; uncomment, review,
  run.

- `_fn_SOOrderIntake` → `DROP FUNCTION IF EXISTS` + `CREATE` (a
  multi-statement function can't be converted with `CREATE OR ALTER`;
  it holds no data).
- Automatic backfills (contact-log / audit `cSourceApp`) → only touch rows
  still on the old value / NULL, so a re-run updates nothing.

Nothing in the file deletes rows or re-inserts duplicates.

---

## Convention for adding future items

Whenever we make a change that requires anything on the server beyond a
code pull:

1. Add a bullet under **Pending deploy** describing *what to run*
   (which file, which block, in what order, any preview-count to look at
   first).
2. If the item is a one-time thing, write it as a template at the bottom
   of `create_views.sql` (commented, with the preview `SELECT` above the
   `UPDATE` / `INSERT`).
3. When you deploy, tick the item off here. Optionally move it under a
   `## Done — YYYY-MM-DD` heading if you want a trail of what shipped
   when; otherwise just delete it.
