from datetime import date, datetime
from typing import Dict, Literal, Optional, List
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    # _rtblAgents.idAgents for the logged-in user. Exposed so the
    # frontend can match targets by stable id instead of the
    # drift-prone cUsername text. Null when the user isn't in
    # _rtblAgents (shouldn't normally happen — Evolution requires it
    # for login — but we keep the field optional for resilience).
    agent_id: Optional[int] = None
    # 'AGENT' | 'SUPERVISOR' | 'ADMIN' — resolved from `_tb_SOAppUsers`.
    # Login is gated on membership so this is always present on a
    # successful LoginResponse.
    role: str
    default_warehouse_code: Optional[str] = None
    # Mirrors OrdersDf.bForceExtOrderNum — when True, the frontend blocks
    # saving an order without an External Order number and the backend
    # rejects such payloads defensively.
    force_ext_order_num: bool = False


class MeResponse(BaseModel):
    username: str
    agent_id: Optional[int] = None
    role: str
    default_warehouse_code: Optional[str] = None
    force_ext_order_num: bool = False


# ---------- Admin: Users & Roles screen ------------------------------
# Phase 3.3 — read / add / edit / deactivate _tb_SOAppUsers rows via
# the admin screen. Only ADMIN role can hit these endpoints.

class AdminUserRow(BaseModel):
    """One row in the Users & Roles table. Lists every active
    Evolution agent and overlays whether they're registered as an app
    user. Unregistered agents appear with `app_user_id = null` so the
    admin can add them in-place."""
    agent_id: int
    agent_name: str
    agent_description: str = ""
    # Null means the agent exists in _rtblAgents but has no row in
    # _tb_SOAppUsers yet — can't log in until the admin adds them.
    app_user_id: Optional[int] = None
    role: Optional[str] = None            # 'AGENT' | 'SUPERVISOR' | 'ADMIN'
    is_active: Optional[bool] = None
    registered_at: Optional[datetime] = None
    registered_by: Optional[str] = None


class AdminUserCreate(BaseModel):
    agent_id: int
    role: str                             # must be one of the three roles


class AdminUserUpdate(BaseModel):
    """Partial update — only fields explicitly set take effect. Both
    optional so a toggle on `is_active` doesn't accidentally overwrite
    the role."""
    role: Optional[str] = None
    is_active: Optional[bool] = None


class PerformanceWindow(BaseModel):
    """Three rolling windows (anchored to today) for the agent-performance
    bar. `week` starts on the current Monday — matches the Analytics 12-
    week pivot columns. `month` is the calendar month to date."""
    today: float = 0.0
    week: float = 0.0
    month: float = 0.0


class AgentPerformance(BaseModel):
    """Current logged-in agent's output. Powers the compact 3-card strip
    at the top of the Order Assistant. Litres come from the audit table
    (CREATE actions by this user) joined to the current InvNum state —
    using our own audit, not InvNum.iINVNUMAgentID, because Evolution
    overwrites that column on processing and we'd lose the trail."""
    litres: PerformanceWindow
    customers_called: PerformanceWindow


class SetDefaultWarehouseRequest(BaseModel):
    code: str


class SetDefaultWarehouseResponse(BaseModel):
    default_warehouse_code: str


class CustomerSummary(BaseModel):
    account: str
    name: str


class CustomerDetail(BaseModel):
    account: str
    name: str
    route: str = ""
    delivery_address: str = ""
    postal_address: str = ""
    contact_name: str = ""
    contact_tel: str = ""
    contact_tel2: str = ""
    contact_email: str = ""
    default_tax_type_id: Optional[int] = None
    credit_limit: float = 0.0
    total_outstanding: float = 0.0
    total_ordered: float = 0.0
    available_credit: float = 0.0
    over_credit_limit: bool = False
    check_terms: bool = False
    terms_code: str = ""
    terms_age_type: int = 0  # 0=Invoice date, 1=Statement (month-end)
    terms_days: int = 0
    terms_cutoff_date: Optional[date] = None
    overdue_amount: float = 0.0
    over_terms: bool = False


class InventoryItem(BaseModel):
    code: str
    description: str
    uom: str = ""
    is_uom_item: bool = False
    is_whse_item: bool = False
    default_tax_type_id: Optional[int] = None
    available: float = 0.0
    warehouse_codes: List[str] = []
    pack_size: int = 1                    # ulIIPerCrate — qty must be a multiple of this
    # StkItem.ulIIMainCategory — exposed so the Target form can auto-fill
    # a Category chip when the user picks a Stock. Empty string when the
    # item hasn't been categorised.
    category: str = ""


class CustomerPrice(BaseModel):
    stock_code: str
    excl_price: float = 0.0
    incl_price: float = 0.0
    source: str = "None"        # 'Customer Deal' | 'Group Deal' | 'Price List' | 'None'
    # True when the agent's default warehouse had no price for this stock and
    # we fell back to the lowest price from any warehouse. UI shows a badge
    # so the rep sees the price didn't come from their DC's price list.
    is_fallback_warehouse: bool = False


class CustomerPricesRequest(BaseModel):
    account: str
    stock_codes: List[str]
    # Rep's session date — only deals effective on this date are applied.
    # Frontend sends AuthContext.sessionDate; required so a rep writing an
    # order dated e.g. next Monday sees Monday's deals, not today's.
    as_of: date
    # Optional warehouse scope — when provided, prices are preferred from
    # this warehouse (falling back to lowest-across-warehouses if the stock
    # has no row in it). Normally set to the logged-in agent's default
    # warehouse so the rep sees prices from their own DC.
    warehouse_code: Optional[str] = None


class PackSizesRequest(BaseModel):
    stock_codes: List[str]


class Warehouse(BaseModel):
    code: str
    description: str


class CustomerContactUpdate(BaseModel):
    contact_name: str = ""
    contact_tel: str = ""
    contact_tel2: str = ""


class TaxType(BaseModel):
    id: int
    code: str
    description: str
    rate: float


class Project(BaseModel):
    code: str
    description: str


class SettlementTerm(BaseModel):
    code: str
    description: str


class SalesOrderLineInput(BaseModel):
    stock_code: str
    description: Optional[str] = Field(None, max_length=100)
    warehouse_code: Optional[str] = None
    quantity: float = Field(gt=0)
    # Zero-price lines are allowed (crates, samples, promos). Quantity still
    # must be positive — a zero-qty line is a no-op.
    unit_price: float = Field(ge=0)
    tax_type_id: Optional[int] = None
    discount_percent: float = 0.0
    project_code: Optional[str] = None


class SalesOrderCreate(BaseModel):
    customer_account: str
    order_date: Optional[date] = None
    due_date: Optional[date] = None
    invoice_date: Optional[date] = None
    delivery_date: Optional[date] = None
    external_order: Optional[str] = None
    default_project_code: Optional[str] = None
    discount_percent: float = 0.0
    tax_mode: str = "Exclusive"
    description: Optional[str] = None
    lines: List[SalesOrderLineInput]
    # Order Assistant only: ids of the suggestions shown for this customer
    # (OrderSuggestion.suggestion_id). Present (even empty) => record each
    # suggestion's ACCEPTED / MODIFIED / REJECTED decision + MANUAL_ADDs.
    # Omitted by the plain Sales Order screen.
    suggestion_ids: Optional[List[int]] = None
    # Which screen saved the order — stamped onto cSourceApp in the audit
    # and contact-log tables. Optional only so a stale cached SPA build
    # doesn't 422; such rows land as UNKNOWN.
    source: Optional[Literal["ORDER_ASSISTANT", "SALES_ORDER_ENTRY"]] = None


class SalesOrderCreateResponse(BaseModel):
    order_no: str
    invoice_no: Optional[str] = None


class CancelOrderResponse(BaseModel):
    order_no: str
    doc_state: int
    doc_state_label: str


class UpdateDeliveryDateRequest(BaseModel):
    delivery_date: date


class SalesOrderSearchResult(BaseModel):
    order_no: str
    external_order: str = ""
    description: str = ""
    invoice_date: Optional[date] = None
    order_date: Optional[date] = None
    delivery_date: Optional[date] = None
    customer_account: str = ""
    customer_name: str = ""
    doc_state: int = 0
    doc_state_label: str = ""
    total_incl: float = 0.0
    is_archived: bool = False


class SalesOrderLineDetail(BaseModel):
    stock_code: str = ""
    item_description: str = ""
    description: str = ""
    warehouse_code: str = ""
    warehouse_codes: List[str] = []   # warehouses this stock is set up in
    is_whse_item: bool = False
    quantity: float = 0.0
    # Always the EXCL unit price — invariant on the frontend LineRow too.
    unit_price: float = 0.0
    # The actual INCL unit price as stored by Evolution (may differ from
    # excl × (1+rate) when the price list overrides it). Used by the frontend
    # to display Incl mode exactly without floating-point drift.
    unit_price_incl: float = 0.0
    # Stored line totals — Evolution's authoritative values. Prefer these
    # over `qty × unit_price_incl` on the frontend to avoid cumulative
    # rounding drift (e.g. 12 × R9.19 = R110.28, but Evolution stored R110.26).
    line_total_excl: float = 0.0
    line_total_tax: float = 0.0
    line_total_incl: float = 0.0
    tax_type_id: Optional[int] = None
    discount_percent: float = 0.0
    project_code: str = ""
    # Pack size for the stock item (ulIIPerCrate). The frontend uses this to
    # flag lines where qty is not a multiple of pack size (the "off-pack"
    # chip in the OrderSummaryModal / edit grid).
    pack_size: int = 1


class SalesOrderDetail(BaseModel):
    order_no: str
    customer_account: str
    customer_name: str = ""
    order_date: Optional[date] = None
    due_date: Optional[date] = None
    invoice_date: Optional[date] = None
    delivery_date: Optional[date] = None
    external_order: str = ""
    default_project_code: str = ""
    discount_percent: float = 0.0
    tax_mode: str = "Exclusive"
    description: str = ""
    doc_state: int = 0
    doc_state_label: str = ""
    is_archived: bool = False
    lines: List[SalesOrderLineDetail] = []


# ---------- Customer invoice history (telesales dashboard) ----------

class HistoryRoute(BaseModel):
    id: str
    display: str


class HistoryCustomer(BaseModel):
    id: str
    display: str


class CustomerOrderCount(BaseModel):
    account: str
    order_count: int


class CustomerOrderCountRow(BaseModel):
    """One row per (date, route, customer) with order count. Used by the
    Route Overview CSV export."""
    order_date: Optional[date] = None
    route: str = ""
    account: str = ""
    customer_name: str = ""
    order_count: int = 0


class HistoricalOrderLine(BaseModel):
    stockCode: str
    description: str
    category: str = "Uncategorized"
    quantity: float = 0.0
    exclAmount: float = 0.0
    vat: float = 0.0
    inclAmount: float = 0.0
    # True when this (order × product) sum includes any pending-but-not-invoiced
    # qty — tells the UI/suggestion logic the number isn't settled yet.
    onOrder: bool = False


class HistoricalOrder(BaseModel):
    orderNum: str
    orderDate: Optional[str] = None
    route: str = ""
    customerName: str = ""
    items: List[HistoricalOrderLine] = []
    itemCount: int = 0
    totalQuantity: float = 0.0
    totalExclAmount: float = 0.0
    totalVat: float = 0.0
    totalInclAmount: float = 0.0


class CustomerDealItem(BaseModel):
    stock_code: str
    description: str
    category: str = "Uncategorized"
    source: str = "Price List"       # 'Customer Deal' | 'Group Deal'
    unit_price: float = 0.0
    unit_price_incl: float = 0.0


# ---------- Analytics — Customer History (lazy-loaded pivot) ----------

class PivotWeekCell(BaseModel):
    amount: float
    litres: float


class PivotRow(BaseModel):
    key: str                              # route code / account / category name / stock code
    label: str
    sublabel: Optional[str] = None        # customer name / stock description
    has_children: bool
    # Keyed by week-start (Monday) ISO date (YYYY-MM-DD).
    weekly: dict[str, PivotWeekCell] = {}
    total_amount: float = 0.0
    total_litres: float = 0.0
    last_sale_date: Optional[date] = None
    # Customer-coverage stats for the bucket. stale = customers whose
    # MAX(TxDate) in the window is older than 14 days (or who have no sales
    # in the window at all, for root-level route/salesrep/telesales dims
    # that augment counts from the Client master).
    stale_count: int = 0
    total_count: int = 0


class PivotLevelResponse(BaseModel):
    # The dimension code of this level (route | salesrep | telesales |
    # customer | category | stock).
    level: str
    rows: List[PivotRow] = []


class Transaction(BaseModel):
    """One row from _vw_SOCustomerInvoiceHistory, scoped to the clicked
    cell's bucket. Quantity / amounts are already sign-flipped for credit
    notes by the view so the frontend just displays them."""
    tx_date: date
    order_no: str
    id: str              # 'OInv' | 'Inv' | 'Crn' (open sales orders are excluded)
    customer_account: str
    customer_name: str
    category: Optional[str] = None
    stock_code: str
    stock_desc: str
    quantity: float
    litre: float
    excl_amount: float
    vat: float
    incl_amount: float


class TransactionListResponse(BaseModel):
    transactions: List[Transaction] = []


# ---------- Order Intake dashboard ----------

class OrderIntakeMetrics(BaseModel):
    orders: int = 0
    ordered_qty: float = 0.0
    ordered_litres: float = 0.0
    ordered_excl: float = 0.0
    invoiced_qty: float = 0.0
    invoiced_litres: float = 0.0
    invoiced_excl: float = 0.0
    # Ordered - invoiced on ARCHIVED orders only (unprocessed orders aren't lost yet).
    lost_qty: float = 0.0
    lost_litres: float = 0.0
    lost_excl: float = 0.0
    # Ordered on orders still unprocessed in Evolution (not invoiced yet).
    pending_qty: float = 0.0
    pending_litres: float = 0.0
    pending_excl: float = 0.0


class OrderIntakeRow(OrderIntakeMetrics):
    """One (date, hour, source, app screen, user, corp group) bucket."""
    date: date
    hour: int
    # 'SO Assistant' | 'Evolution'
    source: str
    # ORDER_ASSISTANT | SALES_ORDER_ENTRY | UNKNOWN | None (Evolution / legacy)
    app_screen: Optional[str] = None
    user: str
    corp_group: str


class OrderIntakeSummary(BaseModel):
    from_date: date
    to_date: date
    rows: List[OrderIntakeRow] = []


class OrderIntakeOrder(BaseModel):
    order_no: str
    created_at: datetime
    hour: int
    source: str
    app_screen: Optional[str] = None
    user: str
    corp_group: str
    customer_account: str
    customer_name: str
    doc_state: int
    ordered_qty: float = 0.0
    ordered_litres: float = 0.0
    ordered_excl: float = 0.0
    invoiced_qty: float = 0.0
    invoiced_litres: float = 0.0
    invoiced_excl: float = 0.0
    # Ordered - invoiced on ARCHIVED orders only (unprocessed orders aren't lost yet).
    lost_qty: float = 0.0
    lost_litres: float = 0.0
    lost_excl: float = 0.0
    # Ordered on orders still unprocessed in Evolution (not invoiced yet).
    pending_qty: float = 0.0
    pending_litres: float = 0.0
    pending_excl: float = 0.0


# ---------- Customer contact log (route call outcomes) ----------

class ContactLogCreate(BaseModel):
    customer_account: str
    route_code: Optional[str] = None
    # One of: NO_ORDER, WRITTEN_ORDER, CALLBACK, NO_ANSWER, REFUSED. PLACED is
    # written internally by the order-save path and should not come from
    # this endpoint.
    outcome: str
    note: Optional[str] = Field(None, max_length=200)
    follow_up_date: Optional[date] = None
    # The session's planned delivery date — may differ from "today" when
    # the rep is prepping tomorrow's call list. The Deliveries report
    # filters strictly by this column; omit (None) for callers that have
    # no target delivery date.
    delivery_date: Optional[date] = None
    # Only the Order Assistant's leave-customer modal posts here today.
    # Rows written before 2026-11-06 carry the legacy value 'SO_ASSISTANT'.
    source_app: Literal["ORDER_ASSISTANT", "SALES_ORDER_ENTRY"] = "ORDER_ASSISTANT"


class ContactLogEntry(BaseModel):
    account: str
    outcome: str
    note: Optional[str] = None
    follow_up_date: Optional[date] = None
    order_no: Optional[str] = None
    username: str = ""


# ---------- Customer-assignment maintenance (route / telesales / sales rep) ----------

class RouteOption(BaseModel):
    """Picker option for a route — pulled from the Evolution user-dict so
    unused options (e.g. a freshly-created INACTIVE) are available before
    any customers have been reassigned onto them."""
    route: str


class TelesaleOption(BaseModel):
    telesale: str


class SalesRepOption(BaseModel):
    id: int
    code: str
    name: str = ""


class CategoryOption(BaseModel):
    """Picker option for a stock category — pulled from the Evolution
    user-dict so unused options appear too (useful for setting a target
    on a brand-new category before any sales land in it)."""
    category: str


class AllAgentOption(BaseModel):
    """Who can own an agent-scoped target. Merged from three sources
    (audit.cUsername, SalesRep.Code, _rtblAgents.cAgentName) so anyone
    the app has ever seen as an agent / rep / user is pickable — handy
    for non-rep logins (admins, team leads) who need their own target."""
    username: str
    label: str      # best display text — SalesRep.Name if available, else just the username
    source: str     # 'audit' | 'salesrep' | 'agent' (first seen, lowest-priority wins the label)


class AgentOption(BaseModel):
    """Single picker option for the Target form's "Placed by"
    dropdown. One row per active Evolution agent (`_rtblAgents`).
    `id` is `_rtblAgents.idAgents` — the stable key the Target form
    sends on save; `name` is `cAgentName` for display + legacy
    cUsername mirror on the target row."""
    id: int
    name: str


class CustomerAssignment(BaseModel):
    """Current route / telesales / sales-rep for a customer. Returned by
    GET so the modal can pre-fill its dropdowns."""
    account: str
    name: str = ""
    route: Optional[str] = None
    telesale: Optional[str] = None
    salesrep_id: Optional[int] = None
    salesrep_code: Optional[str] = None
    on_hold: bool = False


class CustomerAssignmentUpdate(BaseModel):
    """PATCH body. All fields are required so the frontend has to submit
    the complete set — avoids ambiguity about "unset vs unchanged" that
    a partial-update API introduces. None/empty writes through to Client
    (so you can clear a field by selecting nothing)."""
    route: Optional[str] = None
    telesale: Optional[str] = None
    salesrep_id: Optional[int] = None
    on_hold: bool = False
    note: Optional[str] = Field(None, max_length=200)


class CustomerBulkAssignmentUpdate(BaseModel):
    """PATCH body for the bulk endpoint. Unlike the single-customer
    endpoint, this one has explicit per-field "update?" flags because the
    N selected customers may have different current values — an unflagged
    field leaves each customer's existing value alone, instead of
    overwriting it with blank. The audit row still captures all four
    before/after pairs; untouched fields have new = prev."""
    accounts: List[str]
    route: Optional[str] = None
    update_route: bool = False
    telesale: Optional[str] = None
    update_telesale: bool = False
    salesrep_id: Optional[int] = None
    update_salesrep: bool = False
    on_hold: bool = False
    update_on_hold: bool = False
    note: Optional[str] = Field(None, max_length=200)


class CustomerBulkAssignmentResult(BaseModel):
    updated_count: int


class OrderSuggestion(BaseModel):
    stock_code: str
    description: str
    category: str = "Uncategorized"
    # Already-adjusted qty: `base_median − on_order_qty`, snapped to pack size.
    # Suggestions whose adjusted value rounds to 0 crates are dropped on the
    # backend, so a returned suggestion always has suggested_quantity > 0.
    suggested_quantity: float
    # EXCL unit price. `unit_price_incl` is the actual incl stored by Evolution
    # (may differ from excl × (1+rate) if the price list has an override).
    unit_price: float
    unit_price_incl: float = 0.0
    non_zero_weeks: int
    total_weeks: int
    # Pending (open-SO) qty for this stock + customer — shown next to the
    # Sug cell so the rep sees why the number is reduced.
    on_order_qty: float = 0.0
    # Invoiced qty for this stock in the CURRENT week — also subtracted from
    # the suggestion so the number doesn't jump back up the moment a pending
    # SO gets invoiced. Together with `on_order_qty` these form "already
    # covered for this week".
    current_week_invoiced_qty: float = 0.0
    # Inventory enrichment (from _vw_SOInventory)
    item_description: str = ""            # canonical description from StkItem
    uom: str = ""
    is_uom_item: bool = False
    is_whse_item: bool = False
    default_tax_type_id: Optional[int] = None
    available: float = 0.0                # summed QtyOnHand across warehouses
    warehouse_codes: List[str] = []       # warehouses this stock is set up in
    pack_size: int = 1                    # ulIIPerCrate — suggested_quantity is already snapped to a multiple of this
    price_source: str = "None"            # 'Customer Deal' | 'Group Deal' | 'Price List' | 'None'
    # --- Phase 4: target-aware suggestions ---
    # 'TARGET' = weekly basis is the materialised management target;
    # 'BASE_MODEL' = legacy weekly median. suggested_quantity is always
    # max(0, weekly basis − already covered) snapped to pack.
    source: str = "BASE_MODEL"
    base_weekly_qty: Optional[float] = None   # legacy weekly median (None if < min_weeks of history)
    forecast_qty: Optional[float] = None      # active forecast for the delivery week, if any
    target_qty: Optional[float] = None        # weekly target (source TARGET only)
    target_source: Optional[str] = None       # 'SCENARIO' | 'OVERRIDE'
    scenario_names: List[str] = []
    week_start: Optional[date] = None         # Monday of the delivery week
    # _tb_SOOrderSuggestion id when the presentation was recorded
    # (record=true). Sent back on order save to capture the rep's decision.
    suggestion_id: Optional[int] = None


class WeekTarget(BaseModel):
    """A customer x SKU target for one delivery week (progress strip)."""
    stock_code: str
    description: str = ""
    category: str = ""
    week_start: date
    target_qty: float
    forecast_qty: float
    covered_qty: float                    # invoiced this week + open sales orders
    pack_size: int = 1
    source: str                           # 'SCENARIO' | 'OVERRIDE'
    scenario_names: List[str] = []


# ---------- Litre targets (Analytics → Performance) ----------

# Allowed period values. CUSTOM uses dEffectiveFrom..dEffectiveTo as the
# measurement window; the other three are rolling and ignore the
# measurement side of the effective range (they still gate on/off via it).
TARGET_PERIODS = {"DAILY", "WEEKLY", "MONTHLY", "CUSTOM"}


class TargetBase(BaseModel):
    """Shape common to create / update payloads. Any combination of the
    seven scope dims can be set; the database CHECK constraint enforces
    "at least one". Compound targets are first-class — e.g. a stock on a
    route for a customer is just three fields set at once.

    Scope dims fall into three semantic groups:
      • Who placed:   agent_id (preferred) + username (display mirror)
      • Who the customer is linked to: telesale, sales_rep, route,
                                       customer_account
      • What was sold: category, stock_code
    All AND-combine at measurement time."""
    agent_id: Optional[int] = None          # _rtblAgents.idAgents — preferred over username
    username: Optional[str] = Field(None, max_length=50)
    telesale: Optional[str] = Field(None, max_length=100)
    sales_rep: Optional[str] = Field(None, max_length=50)
    route: Optional[str] = Field(None, max_length=100)
    customer_account: Optional[str] = Field(None, max_length=20)
    category: Optional[str] = Field(None, max_length=100)
    stock_code: Optional[str] = Field(None, max_length=20)
    period: str                              # DAILY | WEEKLY | MONTHLY | CUSTOM
    target_litres: float = Field(gt=0)
    effective_from: date
    effective_to: Optional[date] = None     # required when period='CUSTOM'


class TargetCreate(TargetBase):
    pass


class TargetUpdate(TargetBase):
    """PATCH body — same shape as create. The whole target is replaced
    (no partial updates); keeps the API small."""
    pass


class Target(TargetBase):
    """Full read model."""
    id: int
    created_by: str
    created_at: datetime


class TargetWithActual(Target):
    """Returned by GET /targets. The server computes `actual_litres` for
    the target's current measurement window (not just its effective
    period) so the frontend can render the progress bar without a
    second round-trip. `progress_pct` is `actual / target_litres`
    (0 to >1; the bar colour tops out at 100 % on the UI side)."""
    actual_litres: float = 0.0
    progress_pct: float = 0.0
    # The actual date range used for `actual_litres` — exposes the
    # frontend to what Day/Week/Month resolved to at the request time.
    window_from: date
    window_to: date


class TargetListResponse(BaseModel):
    targets: List[TargetWithActual] = []


# ---------- Forecasting & Scenarios (Phase 3) ----------
# Lever semantics, scope keys and lifecycle are documented in
# services/forecasting/scenarios.py.


class ForecastModelRow(BaseModel):
    model_config = {"protected_namespaces": ()}
    model_code: str
    model_name: str
    family: str
    is_enabled: bool
    leaves_scored: int = 0
    weighted_mape: Optional[float] = None
    mae: Optional[float] = None
    bias: Optional[float] = None
    first_scored_week: Optional[date] = None
    last_scored_week: Optional[date] = None


class ForecastRow(BaseModel):
    model_config = {"protected_namespaces": ()}
    forecast_id: int
    run_id: int
    customer_account: str
    stock_code: str
    week_start: date
    route: Optional[str] = None
    dist_centre: Optional[str] = None
    category: Optional[str] = None
    telesales: Optional[str] = None
    sales_rep: Optional[str] = None
    forecast_qty: float
    model_code: str
    is_active: bool
    is_fallback: bool
    fallback_reason: Optional[str] = None
    demand_pattern: Optional[str] = None
    confidence_grade: Optional[str] = None


class ForecastListResponse(BaseModel):
    forecasts: List[ForecastRow] = []
    total: int


class ExcludedLeaf(BaseModel):
    customer: str
    sku: str


class ScenarioScopeModel(BaseModel):
    """AND across dimensions, OR within one. Empty = every forecast leaf."""
    distCentre: List[str] = []
    route: List[str] = []
    customer: List[str] = []
    category: List[str] = []
    sku: List[str] = []
    salesRep: List[str] = []
    telesales: List[str] = []
    excludeLeaves: List[ExcludedLeaf] = []


LeverType = Literal["PERCENT", "ABSOLUTE_DELTA", "FIXED_QTY"]
Measure = Literal["UNITS", "LITRES"]
AnchorLevel = Literal["company", "dist_centre", "route", "customer", "category", "sku"]


class ScenarioCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    scope: ScenarioScopeModel = ScenarioScopeModel()
    lever_type: LeverType = "PERCENT"
    lever_value: float              # PERCENT: 5 = +5%; others: qty per week for the whole scope
    period_from: date
    period_to: date
    measure: Measure = "UNITS"      # what lever_value is in (ABSOLUTE_DELTA / FIXED_QTY)
    anchor_level: Optional[AnchorLevel] = None   # Targets-screen level it was set on


class ScenarioUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    scope: Optional[ScenarioScopeModel] = None
    lever_type: Optional[LeverType] = None
    lever_value: Optional[float] = None
    period_from: Optional[date] = None
    period_to: Optional[date] = None
    measure: Optional[Measure] = None


class ScenarioOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    scope: ScenarioScopeModel
    lever_type: str
    lever_value: float
    period_from: date
    period_to: date
    status: str
    created_by: str
    created_at: datetime
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    cancel_reason: Optional[str] = None
    cancelled_at: Optional[datetime] = None
    measure: str = "UNITS"
    anchor_level: Optional[str] = None


class ReasonablenessFlagOut(BaseModel):
    code: str
    message: str
    metric_value: float
    threshold_value: float


class CascadedTargetOut(BaseModel):
    customer_account: str
    stock_code: str
    week_start: date
    route: Optional[str] = None
    dist_centre: Optional[str] = None
    category: Optional[str] = None
    telesales: Optional[str] = None
    sales_rep: Optional[str] = None
    forecast_qty: float
    target_qty: float                       # resolved target (all active scenarios + this one)
    override_qty: Optional[float] = None    # existing manager override, if any
    effective_target_qty: float             # what the rep will see
    before_qty: Optional[float] = None      # target before this scenario
    in_scope: bool = True                   # False = outside it, rebalanced by it
    scenario_ids: List[int] = []
    flags: List[ReasonablenessFlagOut] = []


class TargetConflictOut(BaseModel):
    scenario_id: Optional[int] = None
    week: date
    goal: float
    inner_total: float
    measure: str
    message: str


class FlaggedLeafOut(BaseModel):
    customer_account: str
    stock_code: str
    flag_codes: List[str]
    weeks: List[date]
    messages: List[str]


class ScenarioCascadeResponse(BaseModel):
    scenario: ScenarioOut
    as_of_week: date
    weeks: List[date]
    leaf_count: int
    leaf_week_count: int
    total_forecast_qty: float
    total_target_qty: float
    total_effective_target_qty: float
    total_before_qty: float = 0.0           # in-scope targets before this scenario
    rebalanced_count: int = 0               # customer-product weeks outside it that it moves
    rebalanced_delta_qty: float = 0.0
    conflicts: List[TargetConflictOut] = [] # block approval
    locked_skipped: int = 0
    warnings: List[str] = []
    flagged_leaves: List[FlaggedLeafOut] = []
    targets: List[CascadedTargetOut] = []    # one page (see targets_offset / limit)
    targets_offset: int = 0
    rows_written: Optional[int] = None       # approve only
    # Targets screen "Proposed" column: change per row at `rollup_level`
    # for `rollup_week`, in units or litres (preview with rollup_level only)
    rollup_level: Optional[str] = None
    rollup_deltas: Dict[str, float] = {}
    rollup_total_delta: float = 0.0
    rollup_scope_delta: float = 0.0      # inside the scenario (its node), that week
    rollup_outside_delta: float = 0.0    # rebalanced customer-products outside it, that week


class FlagReview(BaseModel):
    customer_account: str
    stock_code: str
    decision: Literal["ACCEPT", "EXCLUDE"]
    note: Optional[str] = Field(None, max_length=200)


class ScenarioApproveRequest(BaseModel):
    reviews: List[FlagReview] = []
    # {flag code: ACCEPT|EXCLUDE} -- decides every flagged customer-product
    # with that warning that has no individual review.
    group_decisions: Dict[str, Literal["ACCEPT", "EXCLUDE"]] = {}


class ScenarioCancelRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


class ScenarioCancelResponse(BaseModel):
    scenario: ScenarioOut
    targets_recomputed: int
    targets_deleted: int


class ScenarioTargetRow(BaseModel):
    id: int
    customer_account: str
    stock_code: str
    week_start: date
    route: Optional[str] = None
    dist_centre: Optional[str] = None
    category: Optional[str] = None
    forecast_qty: float
    target_qty: float                       # effective target
    override_qty: Optional[float] = None
    override_reason: Optional[str] = None
    override_by: Optional[str] = None
    source: str
    scenario_ids: List[int] = []
    is_flagged: bool
    flag_codes: List[str] = []
    is_locked: bool


class TargetRollupRow(BaseModel):
    dim_value: Optional[str] = None
    week_start: date
    forecast_qty: float
    target_qty: float
    leaf_count: int
    flagged_count: int


class ScenarioTargetListResponse(BaseModel):
    rollup: Optional[str] = None
    total: int
    targets: List[ScenarioTargetRow] = []       # when rollup is None
    rollup_rows: List[TargetRollupRow] = []     # when rollup is set


class TargetOverrideRequest(BaseModel):
    override_qty: Optional[float] = Field(..., ge=0)   # null clears the override
    reason: str = Field(..., min_length=1, max_length=500)
    break_lock: bool = False


# ---------- Forecasting management UI (Phase 5) ----------

class WorkbenchRowOut(BaseModel):
    key: str
    label: str = ""
    forecast: float
    target: float
    delivered: float
    on_order: float
    committed: float
    leaf_count: int
    targeted_count: int
    flagged_count: int
    override_count: int
    # SKU level only
    target_id: Optional[int] = None
    override: Optional[float] = None
    source: Optional[str] = None
    is_locked: bool = False


class WorkbenchResponse(BaseModel):
    week_start: date
    level: str                       # dist_centre | route | customer | sku
    measure: str                     # units | litres
    filters: dict = {}
    total: WorkbenchRowOut
    rows: List[WorkbenchRowOut] = []


class ScopeOption(BaseModel):
    value: str
    label: str = ""


class ForecastTaskOut(BaseModel):
    id: int
    task_type: str
    priority: str
    assigned_to: Optional[str] = None
    customer_account: Optional[str] = None
    stock_code: Optional[str] = None
    category: Optional[str] = None
    route: Optional[str] = None
    dist_centre: Optional[str] = None
    scenario_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    due_date: date
    status: str
    resolution_notes: Optional[str] = None
    resolved_by: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None


class ForecastTaskUpdate(BaseModel):
    status: Literal["OPEN", "IN_PROGRESS", "SNOOZED", "RESOLVED", "DISMISSED"]
    resolution_notes: Optional[str] = Field(None, max_length=1000)
