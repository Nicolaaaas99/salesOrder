export type CustomerSummary = { account: string; name: string };

export type CustomerDetail = {
  account: string;
  name: string;
  /** Dist Centre on the customer card (Client.ulARDistCentre). */
  dist_centre?: string;
  /** The DC warehouse that Dist Centre maps to (01.HOF_POL / 01.HOF_MAK),
   *  or null when it's blank / not a DC. */
  dc_warehouse_code?: string | null;
  /** The AR route this customer is linked to (Client.ulARRoute). Shown next
   *  to contact info so reps know which route to adjust in master data when
   *  a customer appears under the "wrong" route. */
  route: string;
  delivery_address: string;
  postal_address: string;
  contact_name: string;
  contact_tel: string;
  contact_tel2: string;
  contact_email: string;
  default_tax_type_id: number | null;
  credit_limit: number;
  total_outstanding: number;
  total_ordered: number;
  available_credit: number;
  over_credit_limit: boolean;
  check_terms: boolean;
  terms_code: string;
  terms_age_type: number;
  terms_days: number;
  terms_cutoff_date: string | null;
  overdue_amount: number;
  over_terms: boolean;
};

export type InventoryItem = {
  code: string;
  description: string;
  uom: string;
  is_uom_item: boolean;
  is_whse_item: boolean;
  default_tax_type_id: number | null;
  available: number;
  warehouse_codes: string[];
  pack_size: number;
  /** StkItem.ulIIMainCategory — populated so the Target form can
   *  auto-fill a Category chip when the user picks a stock. */
  category: string;
};

export type CustomerPrice = {
  stock_code: string;
  excl_price: number;
  incl_price: number;
  source: string;    // 'Customer Deal' | 'Group Deal' | 'Price List' | 'None'
  /** True when the agent's default warehouse didn't have this stock priced,
   *  and we fell back to the lowest price from any other warehouse. UI shows
   *  a small badge so the rep knows it isn't from their DC's price list. */
  is_fallback_warehouse: boolean;
  /** Price-list row whose stored incl doesn't match excl + VAT — warning only. */
  price_list_broken?: boolean;
};

export type Warehouse = { code: string; description: string };
export type TaxType = { id: number; code: string; description: string; rate: number };
export type Project = { code: string; description: string };
export type SettlementTerm = { code: string; description: string };

export type SalesOrderLineInput = {
  stock_code: string;
  description?: string | null;
  warehouse_code?: string | null;
  quantity: number;
  unit_price: number;
  tax_type_id?: number | null;
  discount_percent?: number;
  project_code?: string | null;
};

export type SalesOrderCreate = {
  customer_account: string;
  order_date?: string | null;
  due_date?: string | null;
  invoice_date?: string | null;
  delivery_date?: string | null;
  external_order?: string | null;
  default_project_code?: string | null;
  discount_percent?: number;
  tax_mode?: "Exclusive" | "Inclusive";
  description?: string | null;
  lines: SalesOrderLineInput[];
  /** Order Assistant only: ids of the suggestions shown for this customer.
   *  When present (even empty) the backend records each suggestion's
   *  ACCEPTED / MODIFIED / REJECTED decision + MANUAL_ADD rows. */
  suggestion_ids?: number[];
  /** Which screen saved the order. Stamped onto cSourceApp in both
   *  _tb_SOOrderAssistantAudit and _tb_SOCustomerContactLog (PLACED row). */
  source?: OrderSource;
  /** The draft this order finishes: deleted once the order is created. */
  draft_id?: number | null;
};

/** An order saved half-way (see utils/drafts.ts). Private to its agent. */
export type DraftSource = "ORDER_ASSISTANT" | "SALES_ORDER_ENTRY";

export type DraftSummary = {
  id: number;
  source: DraftSource;
  customer_account: string;
  customer_name: string;
  line_count: number;
  total_incl: number | null;
  created_at: string;
  updated_at: string;
  /** The day it is deleted automatically (3 days after it was saved). */
  expires_on: string;
  /** Saved before today: the agent must finish or delete it. */
  stale: boolean;
};

export type DraftDetail = DraftSummary & { payload: unknown };

export type DraftSaveRequest = {
  id?: number | null;
  replace?: boolean;
  source: DraftSource;
  customer_account: string;
  customer_name: string;
  line_count: number;
  total_incl: number | null;
  payload: unknown;
};

/** Screen an order was saved from — lets reports tell Order Assistant
 *  orders apart from ones keyed manually in Sales Order Entry. */
export type OrderSource = "ORDER_ASSISTANT" | "SALES_ORDER_ENTRY" | "ORDER_IMPORT";

export type SalesOrderCreateResponse = {
  order_no: string;
  invoice_no: string | null;
};

export type SalesOrderSearchResult = {
  order_no: string;
  external_order: string;
  description: string;
  invoice_date: string | null;
  order_date: string | null;
  delivery_date: string | null;
  customer_account: string;
  customer_name: string;
  doc_state: number;
  doc_state_label: string;
  total_incl: number;
  is_archived: boolean;
};

export type SalesOrderSearchFilters = {
  account?: string;
  order_no?: string;
  ext_order?: string;
  description?: string;
  date_from?: string;
  date_to?: string;
  /** DocState integers to include. Backend defaults to [1,3,4] when omitted. */
  states?: number[];
};

export type SalesOrderLineDetail = {
  stock_code: string;
  item_description: string;
  description: string;
  warehouse_code: string;
  warehouse_codes: string[];
  is_whse_item: boolean;
  quantity: number;
  unit_price: number;        // always excl
  unit_price_incl: number;   // actual incl from Price List / Evolution
  // Stored line totals from Evolution — use these in read-only displays
  // (OrderSummaryModal) so the sums match Evolution exactly without
  // rounding drift from multiplying unit × qty on the frontend.
  line_total_excl: number;
  line_total_tax: number;
  line_total_incl: number;
  tax_type_id: number | null;
  discount_percent: number;
  project_code: string;
  pack_size: number;
};

export type SalesOrderDetail = {
  order_no: string;
  customer_account: string;
  customer_name: string;
  order_date: string | null;
  due_date: string | null;
  invoice_date: string | null;
  delivery_date: string | null;
  external_order: string;
  default_project_code: string;
  discount_percent: number;
  tax_mode: "Exclusive" | "Inclusive";
  description: string;
  doc_state: number;
  doc_state_label: string;
  is_archived: boolean;
  /** The PDF the order was imported from (Order Import), if any. */
  source_document_id?: number | null;
  lines: SalesOrderLineDetail[];
};

// ---------- Customer invoice history (telesales dashboard) ----------

export type HistoryRoute = { id: string; display: string };
export type HistoryCustomer = { id: string; display: string };
export type CustomerOrderCount = { account: string; order_count: number };
/** Deliveries report row: every active routed customer (+ any customer with
 *  an order in the range), with orders and ordered litres by delivery date. */
export type DeliveryCustomer = {
  route: string;
  account: string;
  customer_name: string;
  order_count: number;
  litres: number;
};
export type CustomerOrderCountRow = {
  order_date: string | null;
  route: string;
  account: string;
  customer_name: string;
  order_count: number;
};

export type HistoricalOrderLine = {
  stockCode: string;
  description: string;
  category: string;
  /** StkItem.ulIISize (litres/kg per unit); null/absent when not captured. */
  size?: number | null;
  quantity: number;
  exclAmount: number;
  vat: number;
  inclAmount: number;
  onOrder: boolean;   // true if this line includes any pending-but-not-invoiced qty
};

export type HistoricalOrder = {
  orderNum: string;
  orderDate: string | null;
  route: string;
  customerName: string;
  items: HistoricalOrderLine[];
  itemCount: number;
  totalQuantity: number;
  totalExclAmount: number;
  totalVat: number;
  totalInclAmount: number;
};

export type CustomerDealItem = {
  stock_code: string;
  description: string;
  category: string;
  source: string; // 'Customer Deal' | 'Group Deal'
  unit_price: number;
  unit_price_incl: number;
};

// ---------- Analytics — Customer History (lazy-loaded pivot) ----------

/** Dimension codes available in the pivot hierarchy. Must stay in sync
 *  with the backend's _DIMENSIONS list in analytics.py. */
export type PivotDim =
  | "route"
  | "salesrep"
  | "telesales"
  | "customer"
  | "category"
  | "stock";

export type PivotWeekCell = {
  amount: number;
  litres: number;
};

export type PivotRow = {
  key: string;
  label: string;
  sublabel: string | null;
  has_children: boolean;
  /** Keyed by week-start (Monday) ISO date (YYYY-MM-DD). */
  weekly: Record<string, PivotWeekCell>;
  total_amount: number;
  total_litres: number;
  last_sale_date: string | null;
  /** Customer-coverage stats for this bucket. stale = count of customers
   *  with no sales in the last 14 days (null safe: 0 when total_count is 0). */
  stale_count: number;
  total_count: number;
};

export type PivotLevelResponse = {
  /** The dim code of this level (echoes path[depth]). */
  level: PivotDim;
  rows: PivotRow[];
};

/** One invoice / credit-note line within a bucket's scope. Returned by
 *  `/analytics/transactions`. Open sales orders are excluded so the
 *  totals match the pivot grid. */
export type HistoryTransaction = {
  tx_date: string;        // ISO yyyy-mm-dd
  order_no: string;
  id: string;             // 'OInv' | 'Inv' | 'Crn'
  customer_account: string;
  customer_name: string;
  category: string | null;
  stock_code: string;
  stock_desc: string;
  quantity: number;
  litre: number;
  excl_amount: number;
  vat: number;
  incl_amount: number;
};

export type TransactionListResponse = {
  transactions: HistoryTransaction[];
};


// ---------- Customer contact log (route call outcomes) ----------

/** Outcomes an agent can log via the Order Assistant modal. PLACED is written
 *  server-side by the order-save path and is never submitted by the modal. */
export type ContactOutcomeModal =
  | "NO_ORDER"
  | "WRITTEN_ORDER"
  | "CALLBACK"
  | "NO_ANSWER"
  | "REFUSED";

/** Outcomes as returned by GET /contact-log/by-date — includes PLACED
 *  because the order-save path writes it internally. */
export type ContactOutcome = ContactOutcomeModal | "PLACED";

export type ContactLogEntry = {
  account: string;
  outcome: ContactOutcome;
  note: string | null;
  follow_up_date: string | null;
  order_no: string | null;
  username: string;
};

export type ContactLogCreate = {
  customer_account: string;
  route_code?: string | null;
  outcome: ContactOutcomeModal;
  note?: string | null;
  follow_up_date?: string | null;
  /** Session's planned delivery date — stored as `dDeliveryDate` so the
   *  Deliveries report can filter outcomes by the day the rep was
   *  prepping for (not necessarily today). */
  delivery_date?: string | null;
  source_app?: OrderSource;
};


export type OrderSuggestion = {
  stock_code: string;
  description: string;
  category: string;
  suggested_quantity: number;    // already-adjusted (base_median − pending), pack-snapped
  unit_price: number;            // always excl
  unit_price_incl: number;       // actual incl from Price List / Evolution
  price_list_broken?: boolean;   // see CustomerPrice.price_list_broken
  non_zero_weeks: number;
  total_weeks: number;
  on_order_qty: number;              // pending (not-yet-invoiced) qty
  current_week_invoiced_qty: number; // invoiced this week — also subtracted

  item_description: string;
  uom: string;
  is_uom_item: boolean;
  is_whse_item: boolean;
  default_tax_type_id: number | null;
  available: number;
  warehouse_codes: string[];
  pack_size: number;
  price_source: string;   // 'Customer Deal' | 'Group Deal' | 'Price List' | 'None'

  // Phase 4 — target-aware suggestions. suggested_quantity is always
  // max(0, weekly basis − already covered) snapped to pack, where the weekly
  // basis is the management target (source TARGET) or the weekly median.
  source: "TARGET" | "BASE_MODEL";
  base_weekly_qty: number | null;   // weekly median (null if too little history)
  forecast_qty: number | null;      // active forecast for the delivery week
  target_qty: number | null;        // weekly target (TARGET only)
  target_source: "SCENARIO" | "OVERRIDE" | null;
  scenario_names: string[];
  week_start: string | null;        // Monday of the delivery week
  suggestion_id: number | null;     // set when the presentation was recorded
};

/** A management target for one customer × stock in the delivery week. */
export type WeekTarget = {
  stock_code: string;
  description: string;
  category: string;
  week_start: string;
  target_qty: number;
  forecast_qty: number;
  covered_qty: number;   // invoiced that week + open sales orders
  pack_size: number;
  source: "SCENARIO" | "OVERRIDE";
  scenario_names: string[];
};


// ---------- Customer-assignment maintenance ----------

export type RouteOption = { route: string };
export type TelesaleOption = { telesale: string };
export type SalesRepOption = { id: number; code: string; name: string };
export type CategoryOption = { category: string };
/** Merged list of anyone the app has ever seen as an agent — audit
 *  users ∪ SalesRep.Code ∪ _rtblAgents.cAgentName. Used by the target
 *  form so non-rep logins (admins, team leads) can be targets too. */
export type AllAgentOption = {
  username: string;
  label: string;
  source: "audit" | "salesrep" | "agent";
};

/** Single picker option for the Target form's "Placed by" dropdown.
 *  `id` is `_rtblAgents.idAgents` — the stable key sent on save and
 *  used for measurement; `name` is `cAgentName` for display. */
export type AgentOption = {
  id: number;
  name: string;
};

export type CustomerAssignment = {
  account: string;
  name: string;
  route: string | null;
  telesale: string | null;
  salesrep_id: number | null;
  salesrep_code: string | null;
  on_hold: boolean;
};

export type CustomerAssignmentUpdate = {
  route?: string | null;
  telesale?: string | null;
  salesrep_id?: number | null;
  on_hold: boolean;
  note?: string | null;
};

/** Bulk-edit payload. Per-field `update_*` flags are required so an
 *  untouched field leaves each customer's existing value alone. */
export type CustomerBulkAssignmentUpdate = {
  accounts: string[];
  route?: string | null;
  update_route: boolean;
  telesale?: string | null;
  update_telesale: boolean;
  salesrep_id?: number | null;
  update_salesrep: boolean;
  on_hold: boolean;
  update_on_hold: boolean;
  note?: string | null;
};

export type CustomerBulkAssignmentResult = {
  updated_count: number;
};


// ---------- Agent performance strip (Order Assistant top bar) ----------

export type PerformanceWindow = {
  today: number;
  week: number;   // Monday-to-Sunday, anchored on the current week's Monday
  month: number;  // Calendar month to date
};

export type AgentPerformance = {
  litres: PerformanceWindow;
  customers_called: PerformanceWindow;
};


// ---------- Litre targets (Analytics → Performance + Order Assistant bar) ----------

export type TargetPeriod = "DAILY" | "WEEKLY" | "MONTHLY" | "CUSTOM";

/** Server-side scope-kind classification. Returned so the Performance
 *  screen can group / filter without recomputing the dim combination
 *  logic in the client. */
export type TargetScopeKind =
  | "agent"
  | "telesales"
  | "sales_rep"
  | "route"
  | "customer"
  | "category"
  | "stock"
  | "compound";

export type Target = {
  id: number;
  /** _rtblAgents.idAgents — preferred over `username` for the
   *  "Placed by" axis. Stable across casing drift. Null for legacy
   *  rows written before this column existed. */
  agent_id: number | null;
  username: string | null;
  telesale: string | null;
  sales_rep: string | null;
  route: string | null;
  customer_account: string | null;
  category: string | null;
  stock_code: string | null;
  period: TargetPeriod;
  target_litres: number;
  effective_from: string;        // yyyy-mm-dd
  effective_to: string | null;   // yyyy-mm-dd or null
  created_by: string;
  created_at: string;            // ISO datetime
};

export type TargetWithActual = Target & {
  actual_litres: number;
  progress_pct: number;          // 0..∞ (frontend caps the bar at 100 %)
  window_from: string;           // yyyy-mm-dd — the measurement window used
  window_to: string;
};

export type TargetListResponse = {
  targets: TargetWithActual[];
};

/** POST / PATCH body. Shape intentionally mirrors the Pydantic
 *  `TargetBase` — any combination of scope dims is allowed, server
 *  enforces "at least one". */
export type TargetUpsert = {
  agent_id?: number | null;
  username?: string | null;
  telesale?: string | null;
  sales_rep?: string | null;
  route?: string | null;
  customer_account?: string | null;
  category?: string | null;
  stock_code?: string | null;
  period: TargetPeriod;
  target_litres: number;
  effective_from: string;
  effective_to?: string | null;
};


// ---------- Order Intake dashboard ----------

/** 'SO Assistant' (app wrote a CREATE audit row) or 'Evolution'. */
export type OrderIntakeSource = "SO Assistant" | "Evolution";

export type OrderIntakeMetrics = {
  orders: number;
  ordered_qty: number;
  ordered_litres: number;
  ordered_excl: number;
  invoiced_qty: number;
  invoiced_litres: number;
  invoiced_excl: number;
  /** Ordered - invoiced on ARCHIVED orders only (unprocessed orders aren't lost yet). */
  lost_qty: number;
  lost_litres: number;
  lost_excl: number;
  /** Ordered on orders still unprocessed in Evolution (not invoiced yet). */
  pending_qty: number;
  pending_litres: number;
  pending_excl: number;
};

/** One (date, hour, source, app screen, user, corp group, DC, route) bucket. */
export type OrderIntakeRow = OrderIntakeMetrics & {
  date: string;
  hour: number;
  source: OrderIntakeSource;
  /** ORDER_ASSISTANT | SALES_ORDER_ENTRY | UNKNOWN | null (Evolution / legacy). */
  app_screen: string | null;
  user: string;
  corp_group: string;
  /** Customer card's DC / route ("No DC" / "No route" when blank). */
  dist_centre: string;
  route: string;
};

export type OrderIntakeSummary = {
  from_date: string;
  to_date: string;
  rows: OrderIntakeRow[];
};

export type OrderIntakeOrder = Omit<OrderIntakeMetrics, "orders"> & {
  order_no: string;
  created_at: string;
  hour: number;
  source: OrderIntakeSource;
  app_screen: string | null;
  user: string;
  corp_group: string;
  dist_centre: string;
  route: string;
  customer_account: string;
  customer_name: string;
  doc_state: number;
};

export type OrderIntakeDrillFilter = {
  hour?: number | null;
  source?: string | null;
  user?: string | null;
  corp_group?: string | null;
  dist_centre?: string | null;
  route?: string | null;
};

// ---------------------------------------------------------------------------
// Order Import (emailed purchase-order PDFs). Mirrors
// backend/services/order_import/models.py + parsers.py.
// ---------------------------------------------------------------------------

export type ParsedOrderLine = {
  line_no: number;
  supplier_code: string;
  item_code: string;
  /** Barcode, when the layout prints it (Pick n Pay). */
  barcode?: string;
  description: string;
  /** Units: what goes on our order line (packs x pack size for Pick n Pay). */
  qty: number;
  pack_size?: number | null;
  pack_qty?: number | null;
  unit_excl: number;
  /** Can be a fraction of a cent when the layout prints only line totals. */
  unit_incl: number;
  vat_rate: number;
  /** False when the layout prints no VAT (Pick n Pay). */
  vat_known?: boolean;
  /** Excl prices only on the PO (OK Foods): incl comes from the item's VAT. */
  prices_excl?: boolean;
  line_excl: number;
  line_vat: number;
  line_incl: number;
  parse_warning: string | null;
};

export type ParsedOrder = {
  format_name: string;
  retailer: string;
  order_no: string;
  /** The user typed the PO number (it wasn't on the document). */
  order_no_typed?: boolean;
  order_date: string | null;
  delivery_from: string | null;
  delivery_to: string | null;
  store_code: string;
  store_name: string;
  lines: ParsedOrderLine[];
  total_qty: number | null;
  total_excl: number | null;
  total_vat: number | null;
  total_incl: number | null;
  warnings: string[];
  /** The uploaded PDF as stored on the server ("Source document"). */
  document_id?: number | null;
};

export type ImportLineStatus =
  | "OK"
  | "UNMATCHED"
  | "ITEM_ISSUE"
  | "NO_PRICE"
  | "PRICE_MISMATCH"
  | "PACK_MISMATCH"   // case sizes differ and rounding down to our crates leaves 0: fix it in Evolution
  | "VAT_MISMATCH"
  | "PARSE_ERROR"
  | "EXCLUDED";       // crates: not charged, left off the order and the PO total

/** What a line whose PO price differs from ours is charged at:
 *  EVOLUTION = our price (automatic when the PO is up to 5c above ours),
 *  PO = the PO price, OVERRIDE = a price a supervisor typed. */
export type ImportPriceChoice = "PO" | "EVOLUTION" | "OVERRIDE";

export type ImportLineResult = {
  line_no: number;
  supplier_code: string;
  item_code: string;
  description: string;
  qty: number;
  doc_unit_excl: number;
  doc_unit_incl: number;
  doc_vat_rate: number;
  doc_line_excl: number;
  doc_line_incl: number;
  /** Packs x pack size, when the PO orders in packs (Pick n Pay). After a
   *  supervisor corrected THEIR pack, the corrected pack. */
  doc_pack_size?: number | null;
  doc_pack_qty?: number | null;
  /** Our pack size (StkItem.ulIIPerCrate); null until matched. */
  our_pack_size?: number | null;
  /** The PO's case size (> 1) isn't ours and nobody resolved it: blocks. */
  /** Their case isn't our crate: units rounded down to whole crates of ours (flag only). */
  pack_conflict?: boolean;
  /** The PO's units before that rounding. */
  qty_before_pack?: number | null;
  /** Rounding down left nothing: blocks create. */
  pack_blocks?: boolean;
  /** Units aren't whole crates of ours (warning: the order shows off-pack). */
  off_pack?: boolean;
  /** Remembered supervisor decision on THEIR pack. */
  pack_fix?: "CORRECTED" | "ACCEPTED" | null;
  pack_fix_id?: number | null;
  /** What the PO printed, when a correction changed it. */
  doc_pack_printed?: number | null;
  doc_qty_printed?: number | null;
  barcode?: string;
  match_source: "MAPPING" | "DIRECT" | "NONE";
  mapping_id: number | null;
  stock_code: string | null;
  stock_description: string;
  warehouse_code: string | null;
  tax_type_id: number | null;
  tax_rate: number | null;
  price_source: string | null;
  evo_unit_excl: number | null;
  /** What the inclusive order charges per unit incl. */
  evo_unit_incl: number | null;
  /** Incl stored on the price list / deal. */
  evo_list_incl: number | null;
  /** Price-list row whose stored incl doesn't match excl + VAT — warning only. */
  price_list_broken?: boolean;
  /** PO incl differs from evo_unit_incl (stays flagged). */
  price_differs: boolean;
  /** PO more than 5c above ours, or ours above the PO: a supervisor must
   *  set the price (agent: submit for review). */
  needs_review: boolean;
  price_choice: ImportPriceChoice | null;
  /** Our price charged automatically (PO at most 5c above ours). */
  price_auto: boolean;
  /** Supervisor's price, when one was set. */
  price_override: number | null;
  /** What the order line charges per unit incl. */
  unit_incl: number | null;
  excluded: boolean;
  notes: string[];
  line_excl: number;
  line_tax: number;
  line_incl: number;
  status: ImportLineStatus;
  issues: string[];
};

export type ImportIssue = { code: string; message: string; blocking: boolean };

export type ImportTotals = {
  doc_qty: number | null;
  doc_excl: number | null;
  doc_vat: number | null;
  doc_incl: number | null;
  /** The PO prints excl only (Spar): doc_incl is its lines' incl at each item's VAT. */
  doc_incl_from_lines?: boolean;
  parsed_qty: number;
  parsed_incl: number;
  order_qty: number;
  order_excl: number;
  order_tax: number;
  order_incl: number;
  excluded_qty: number;
  excluded_incl: number;
  /** Lines charged at a price other than the PO's move the PO total by this. */
  price_adjust_incl: number;
  /** PO prices rounded down to the cent per unit (never overcharge). */
  rounding_incl: number;
  /** Units added / removed by correcting a retailer's pack size. */
  pack_adjust_qty?: number;
  pack_round_incl?: number;
  /** PO total after crates and price choices — what the order must match. */
  expected_incl: number | null;
  difference_incl: number | null;
  tolerance: number;
  agrees: boolean;
};

export type ImportValidation = {
  document: ParsedOrder;
  customer_account: string;
  customer_name: string;
  customer_store_code: string;
  store_check: "MATCH" | "MISMATCH" | "UNVERIFIED";
  store_code_customers: { account: string; name: string }[];
  warehouse_code: string | null;
  price_date: string;
  duplicate_orders: string[];
  lines: ImportLineResult[];
  totals: ImportTotals;
  issues: ImportIssue[];
  can_create: boolean;
  /** Only price lines stand in the way: the agent may submit for review. */
  can_submit_review: boolean;
  /** The user may set prices (SUPERVISOR / ADMIN). */
  can_override_prices: boolean;
  /** Reviews already waiting for this PO number. */
  pending_reviews: number[];
};

export type ImportCreateRequest = {
  customer_account: string;
  document: ParsedOrder;
  delivery_date: string;
  order_date: string;
  due_date?: string | null;
  invoice_date?: string | null;
  description?: string | null;
  dates_confirmed: boolean;
  /** Supervisor prices (incl) per line_no; ignored for agents. */
  price_overrides?: Record<number, number>;
  review_id?: number | null;
};

export type ImportReviewStatus = "PENDING" | "CREATED" | "REJECTED";

export type ImportReviewSummary = {
  id: number;
  status: ImportReviewStatus;
  customer_account: string;
  customer_name: string;
  po_number: string;
  delivery_date: string | null;
  po_total_incl: number | null;
  review_lines: number;
  document_id: number | null;
  submitted_by: string;
  submitted_at: string | null;
  submit_note: string;
  reviewed_by: string;
  reviewed_at: string | null;
  review_note: string;
  order_num: string;
};

export type ImportReviewDetail = ImportReviewSummary & {
  document: ParsedOrder;
  order_date: string | null;
  description: string;
};

export type BrokenPriceRow = {
  effect: string;
  price_list: string;
  whse: string;
  stock_code: string;
  description: string;
  stored_excl: number;
  stored_incl: number;
  vat_pct: number;
  correct_incl: number;
  diff: number;
  customers: number;
};

/** PROBLEM: needs fixing. TOLERANCE: PO up to 5c above ours (charged ours
 *  automatically). FIXED: ours matches the PO. Every
 *  status except PROBLEM is archived. */
export type PoDifferenceStatus = "PROBLEM" | "TOLERANCE" | "FIXED" | "DISMISSED";

/** One imported PO line whose PO price differed from ours. */
export type PoDiffLine = {
  id: number;
  status: PoDifferenceStatus;
  archived: boolean;
  archived_at: string | null;
  archived_by: string;
  /** Why a supervisor dismissed it (DISMISSED only). */
  archive_note: string;
  customer_account: string;
  po_number: string;
  order_num: string;
  stock_code: string;
  description: string;
  whse: string;
  qty: number;
  price_list: string;
  source_then: string;
  our_price_then: number;
  po_price: number;
  charged: "PO" | "OURS" | "SET" | string;
  charged_price: number;
  charged_auto: boolean;
  /** Ours today (open lines) or when archived, priced as an import would. */
  our_price_now: number | null;
  source_now: string;          // Price List / Customer Deal / Group Deal / None / "" (unknown)
  diff_now: number | null;     // PO - ours now
  deal_for: string;            // customer account (customer deal) / customer group (group deal)
  deal_from: string | null;
  deal_to: string | null;
};

/** One imported PO (= one sales order) with differing lines. */
export type PoDiffPo = {
  customer_group: string;
  customer_account: string;
  customer_name: string;
  po_number: string;
  order_num: string;
  imported_on: string | null;
  document_id: number | null;
  to_fix: number;
  tolerance: number;
  fixed: number;
  dismissed: number;
};

export type PoDiffReport = {
  pos: PoDiffPo[];
  /** The open (not archived) lines. */
  lines: PoDiffLine[];
};

export type ImportStoreCheck = {
  customer_account: string;
  customer_name: string;
  customer_store_code: string;
  store_code: string;
  store_check: "MATCH" | "MISMATCH" | "UNVERIFIED";
  store_code_customers: { account: string; name: string }[];
  message: string | null;
};

export type ItemMapping = {
  id: number;
  scope: string;
  /** Customer the mapping was captured for (applies to the whole group). */
  customer_account: string;
  supplier_code: string;
  item_code: string;
  description: string;
  stock_code: string;
};
