# SalesOrder — Backlog & Roadmap

> **🤖 Reminder for Claude:** when a change requires anything on the
> server beyond a code pull (SQL script edit, backfill, Evolution config,
> GRANT) **always append it to the "Pending deploy" checklist in
> [`DEPLOYMENT.md`](DEPLOYMENT.md)** so the user has one place to look at
> deploy time. The runbook convention is at the bottom of that file.

Legend for the top list: `(N)` = aiming to do now · no marker = parked backlog. Tick a box when the item is shipped.

---

## TODO (SalesOrder app + future development)

- [ ] 1. Deactivate selecting customer without selecting route
- [ ] 2. Suggested order calc (Average or Median previous 2 months / % change previous year / Other model (TSB?)?)
- [ ] 3. **(N)** User Logins — User Rights. Scope must cover: (a) **target visibility** — normal agents see all targets except other agents' targets; supervisors see everything including per-agent breakdowns; (b) **target admin UI** gating — only supervisors/admins can CRUD targets via the in-app screen (regular agents can view but not edit); (c) general "which routes / dashboards / exports can this role see" scaffolding so Analytics screens can hide the right columns per role.
- [ ] 4. **(N)** Cancel orders with SDK and write username with that
- [ ] 5. **(N)** Load data for analytics section from table (previous day) for efficiency
- [x] 6. **(N)** Route Overview — A. Rules for ranking problem routes (show non-delivered routes). Route with most customers? How should we order it? Show it per Telesales / Per Sales Rep. Interactivity (modify it myself, work like pivot table). Remove total column?
- [x] 7. **(N)** Route Overview — B. Deliveries report with column showing reason and delivery date range
- [ ] 8. **(N)** Route Overview — C. Agent performance with Targets Bar — Distribution: Litre targets per category per route. Weekly and daily targets. What reps are linked to what routes. Reps vs Telesales. Telesales linked to which route. Measure how far the rep or telesale from target. Wie het werklik gebel. Sales week to date, month to date. Show: Litres, no of customer called (today / this week / this month)
- [ ] 9. Route Overview — D. Customer Contact Log Report
- [ ] 10. **(N)** Rep App — Work the same as OrderAssistant, but just mobile. Doing maintenance?
- [ ] 11. Delivery App: Bevestiging van aflewerings screen — Analytics screen to tick off that an order was delivered or not. Use View and query routing dbs
- [x] 12. **(N)** Analytics (Customer Sales History) — Maintenance workflow: from the reorderable pivot, allow reassigning customers from inactive buckets (route / telesales / sales rep) onto new "Inactive route", "Inactive telesales", "Inactive sales rep" placeholders. Drill into an empty bucket works (master-only customers appear with "Never" + red stale 1); single-customer Edit modal ships with pencil button on customer rows writing to Client + audit row in `_tb_SOCustomerAssignmentAudit`. Bulk-select + bulk reassign ships with per-row checkboxes + "Select N" button on expanded group rows + floating action bar + atomic transaction (one audit row per customer).
- [ ] **(N)** Get report showing what orders placed in Evo vs in SOAssistant
- [ ] **(N)** BI-style report with template support — pick a date (or date range), see a pivot table of the data for that period, save the current layout (dimensions, filters, measures) as a named template so the user can reopen it later with one click. Think "custom report builder, lite" — same hierarchy / drag + drop idea as the Customer Sales History pivot, but with saveable layouts.
- [ ] **Catalog list for Boxer stores** — Boxer clients may only transact in a restricted set of stock items. Needs a new table (e.g. `_tb_SOClientCatalog` with `(cClientGroup | iCustomerDCLink, iStockID)` rows) listing the catalog items permitted for Boxer customers. Apply the filter in two places: (a) **history** — Customer Sales History / Order Assistant history grid only shows lines whose stock is on the Boxer catalog when the customer is a Boxer client; (b) **item picker** — only Boxer-catalog stocks are selectable when drafting a Boxer order. Open questions: scope by customer group vs individual customer, admin UI for maintaining the catalog, how to flag a client as "Boxer" (new Client column vs user-dict option vs catalog-link presence).

---

## Analytics — Phase roadmap

### Phase 1 — Customer Sales History ✅ shipped

**Hub + screen**
- Analytics landing shows cards; clicking one opens that screen with a
  "Back to Analytics" link.
- `/api/route-overview/customer-history?level=X[&route=][&account=][&category=]`
  returns pre-aggregated weekly rows for one level at a time.
- Backend queries go direct to `PostST + Client + StkItem` (not through
  `_vw_SOCustomerInvoiceHistory`) for speed.
- Frontend tree: Route → Customer → Category → Stock. Lazy loads each
  level on expand, caches per cache key, spinner on the expanding
  chevron, inline Retry row on error.
- Zero-sale routes/customers come from `_vw_SORoute` / `_vw_SOCustomers`
  masters so stale accounts float up.
- Columns: Name · Last delivery (date + "Nd" stale subscript) · Total ·
  12 week columns (newest first, `formatWeekLabel`).
- Amount(incl) / Litres toggle.

**Current gaps worth revisiting**
- No grand-total row across all routes at the top.
- No cross-session cache (expands reset on page refresh / tab switch).
- Stats modals (Customer Summary / Route Totals) were dropped when the
  tree replaced them — the tree now holds the per-level totals, but
  there's no deeper per-customer or per-route detail panel anywhere.
- Related to top-list item 5: load from a nightly snapshot table instead
  of live-aggregating PostST on every click.
- Related to top-list item 6: ranking / sorting rules + "remove total
  column?" question.

### Phase 2 — Deliveries ✅ lifted and shifted · ⏳ reason column pending

- Existing Route Overview deliveries code moved into its own screen
  (`RouteDeliveriesScreen`).
- Still has the route picker + delivery-date filter + export-to-xlsx
  button + per-customer drill-in.
- **Not yet built:** the "Reason" column that joins
  `_tb_SOCustomerContactLog` outcomes so a no-delivery day shows *why*
  (NO_ORDER / CALLBACK / NO_ANSWER / REFUSED). One backend change
  (join on route × customer × date) + one frontend column.
- Related to top-list item 7.

### Phase 3 — Customer Contact Log (not started)

**Views to add**
- `_vw_SOContactLogEvents` — event-level, denormalized with customer
  name, agent name, route.
- `_vw_SOContactLogLatest` — one row per (date, customer), latest
  outcome wins.

**Endpoint:** `GET /route-overview/contact-log?from=&to=&route=&agent=&outcome=`

**Screen layout (proposal)**
- Three stacked columns per route:
  - 🔴 Not contacted
  - 🟡 Contacted-no-order (sub-grouped by outcome)
  - 🟢 Placed
- Toggle: "Latest per customer" (effective status, default) vs
  "All events" (audit trail).
- Filters: date range · route · agent · outcome.
- xlsx export.
- Highlight overdue callbacks (outcome = CALLBACK + follow_up_date past).

Related to top-list item 9.

### Phase 4 — Agent Performance + Targets Bar (not started)

**View:** `_vw_SOAgentPerformanceDaily` — one row per (agent, date) with:
- Orders placed count + ZAR value
- Customers contacted (any outcome)
- Route coverage % (contacted ÷ assigned route size)
- Conversion % (placed ÷ contacted)

**Screen:** sortable leaderboard with 7-day sparklines per metric.
No charts dashboard — just a working leaderboard.

**Targets table in SOAssistant:**
```sql
_tb_SOAgentTargets
  idTarget         int identity PK
  iAgentID         int NULL         -- NULL = default for everyone
  cUsername        varchar(64) NULL
  cMetric          varchar(32)      -- 'ORDERS_PER_DAY' | 'VALUE_PER_DAY' | 'COVERAGE_PCT'
  fTargetValue     decimal(18,2)
  dEffectiveFrom   date
  dEffectiveTo     date NULL
```
Resolution order: agent-specific row → default row → unset (no bar shown).

**Order Assistant targets bar:** thin progress strip at the top with
three bars (orders / value / coverage) colour-grading grey → yellow →
green as the agent approaches target. Admin edits via SQL for v1 —
admin UI is YAGNI.

Related to top-list item 8 — also adds litre targets per category per
route, rep-vs-telesales distinction, week-to-date / month-to-date view.

---

## Extra stats ideas (open — not implemented)

### For the per-customer summary

Currently the tree only shows totals as roll-ups. If we ever bring back
a dedicated customer modal/panel, these are the strongest candidates:

| Field | Why it matters | Data source |
|---|---|---|
| Average weekly amount / litres | Baseline — "did this week beat average?" | Totals ÷ non-zero weeks |
| Peak week (week + value) | Context for seasonality | Max over the 12-week daily buckets |
| Trend (first half vs second half) | ↑ / ↓ / → signal — growing or declining? | Split the window, compare |
| **Latest contact outcome + timestamp** | "Called yesterday — CALLBACK" context before the next call | Join `_tb_SOCustomerContactLog` latest row |
| **Pending orders** (count + total value) | Flags "already ordering, no need to call" | `_vw_SOCustomerOrdersPerDay` or `InvNum` |
| Credit snapshot (over credit / over terms chip) | Fast "is it worth calling?" filter | `_vw_SOCustomerDetail` |
| Top 3 products in the window | What they typically buy | Aggregate by stock_code |
| Weeks since first delivery in window | "Newish customer" signal | Min date from daily sales |

### For the per-route totals summary

| Field | Why it matters |
|---|---|
| Average per customer (amount + litres) | Normalises comparison across routes of different sizes |
| **Most active customer** (name + total) | Spot the anchor account |
| **Most stale active customer** (name + days since) | "Who needs attention first" — biggest customer gone quiet |
| Peak week (week + total value) | Last week exceptional? |
| Weeks with any activity | Flags unusually quiet routes |
| Pending orders total | "We have X in the pipeline" at a glance |
| % of customers contacted today | Bridges to the contact-log work — coverage |

**First iteration pick** if we revisit: *Latest contact outcome* +
*Pending orders* on the customer side; *Average per customer* +
*Most stale active customer* on the route side. Those four give the
biggest "I now know what to do" uplift per field.

---

## Other parked design items

- **Section name alternatives** considered: Reports / **Analytics (picked)**
  / Insights / Dashboards / Operations / Review / Workspace.
- **Table/view naming for Axiom** — SQL names use `_SO` prefix; a global
  find/replace to `_AX` or similar can be done later. Keep table-name
  constants in Python code so a rename is a one-line change per table.
- **User rights / permissions** — brainstormed earlier (related to
  top-list item 3). Proposed shape:
  `_tb_SOAppUsers (cUsername, iAgentID, cRole in AGENT|SUPERVISOR|ADMIN, bActive)`
  + `_tb_SORouteAccess` for route-scoped visibility. Needed before
  anything "supervisor-only".
- **Grand-total row** at the top of the Customer Sales History pivot
  (across all routes). Nice-to-have.
- **sessionStorage cache** for the pivot so tab switches / refreshes
  don't re-fetch. Day-level freshness is fine.
