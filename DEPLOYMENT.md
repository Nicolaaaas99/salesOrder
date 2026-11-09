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
