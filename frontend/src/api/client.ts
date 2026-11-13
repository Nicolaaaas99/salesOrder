import axios from "axios";
import type {
  ContactLogCreate,
  DraftDetail,
  DraftSaveRequest,
  DraftSummary,
  ContactLogEntry,
  PivotDim,
  PivotLevelResponse,
  TransactionListResponse,
  CustomerSummary,
  CustomerDetail,
  CustomerPrice,
  InventoryItem,
  Warehouse,
  TaxType,
  Project,
  SettlementTerm,
  SalesOrderCreate,
  SalesOrderCreateResponse,
  SalesOrderSearchResult,
  SalesOrderSearchFilters,
  SalesOrderDetail,
  HistoryRoute,
  HistoryCustomer,
  CustomerOrderCount,
  HistoricalOrder,
  CustomerDealItem,
  BrokenPriceRow,
  ImportReviewDetail,
  ImportReviewStatus,
  ImportReviewSummary,
  PoDiffLine,
  PoDiffReport,
  CustomerAssignment,
  CustomerAssignmentUpdate,
  CustomerBulkAssignmentUpdate,
  CustomerBulkAssignmentResult,
  RouteOption,
  TelesaleOption,
  SalesRepOption,
  CategoryOption,
  AllAgentOption,
  AgentOption,
  OrderSuggestion,
  WeekTarget,
  AgentPerformance,
  TargetListResponse,
  TargetWithActual,
  TargetUpsert,
  OrderIntakeSummary,
  OrderIntakeOrder,
  DeliveryCustomer,
  OrderIntakeDrillFilter,
  ParsedOrder,
  ImportValidation,
  ImportCreateRequest,
  ImportStoreCheck,
  ItemMapping,
} from "./types";

// Serialize array params as repeated keys (`states=1&states=3`) rather than
// axios's default `states[]=…` form, so FastAPI's `List[int]` reads them.
export const http = axios.create({
  baseURL: "/api",
  paramsSerializer: (params) => {
    const sp = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined || value === null) return;
      if (Array.isArray(value)) {
        value.forEach((v) => sp.append(key, String(v)));
      } else {
        sp.append(key, String(value));
      }
    });
    return sp.toString();
  },
});

// Attach the current session token to every outbound request. We read from
// sessionStorage on each call rather than closing over the token so that
// login/logout take effect immediately without having to rebuild the client.
// Also stamps X-App-Version so the backend audit log can record which build
// the client was running when an order was placed/updated/cancelled.
const APP_VERSION =
  typeof __BUILD_VERSION__ === "string" ? __BUILD_VERSION__ : "";
http.interceptors.request.use((cfg) => {
  cfg.headers = cfg.headers ?? {};
  const token = sessionStorage.getItem("auth_token");
  if (token) {
    (cfg.headers as any).Authorization = `Bearer ${token}`;
  }
  if (APP_VERSION) {
    (cfg.headers as any)["X-App-Version"] = APP_VERSION;
  }
  return cfg;
});

// Any 401 means the session is dead — clear local state and let AuthContext
// (which listens for this event) swap back to the login screen.
http.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      sessionStorage.removeItem("auth_token");
      sessionStorage.removeItem("auth_username");
      window.dispatchEvent(new Event("auth:401"));
    }
    return Promise.reject(err);
  }
);

/** Role returned by the backend after a successful login. Determines
 *  which parts of the UI are visible and which backend endpoints will
 *  accept writes. See `_tb_SOAppUsers.cRole`. */
export type AppUserRole = "AGENT" | "SUPERVISOR" | "ADMIN";

export type LoginResponse = {
  token: string;
  username: string;
  /** _rtblAgents.idAgents for the logged-in user. Used by the strip to
   *  match targets by stable id instead of drift-prone username. */
  agent_id: number | null;
  /** Resolved on login against `_tb_SOAppUsers`. Login is gated on
   *  membership so this is always present on a successful response. */
  role: AppUserRole;
  default_warehouse_code: string | null;
  force_ext_order_num: boolean;
};

export const auth = {
  /** "Still here" ping for Users & Roles presence. No DB on the server. */
  heartbeat: () => http.post<{ ok: boolean }>("/auth/heartbeat").then((r) => r.data),

  login: (username: string, password: string) =>
    http
      .post<LoginResponse>("/auth/login", { username, password })
      .then((r) => r.data),
  logout: () => http.post("/auth/logout").then((r) => r.data),
  me: () =>
    http
      .get<{
        username: string;
        agent_id: number | null;
        role: AppUserRole;
        default_warehouse_code: string | null;
        force_ext_order_num: boolean;
      }>("/auth/me")
      .then((r) => r.data),
  setDefaultWarehouse: (code: string) =>
    http
      .post<{ default_warehouse_code: string }>("/auth/default-warehouse", { code })
      .then((r) => r.data),
  getMyPerformance: () =>
    http.get<AgentPerformance>("/auth/me/performance").then((r) => r.data),
};

export const api = {
  searchCustomers: (
    q: string,
    limit = 50,
    filters?: {
      route?: string | null;
      telesale?: string | null;
      sales_rep?: string | null;
      /** Only corporate groups — Spar / Checkers / Boxer / Pick n Pay / OK
       *  (Client.ulARCorpGroup in view _vw_SOCorporateGroups: Evolution's dropdown options except Non Corporate). */
      corporate_only?: boolean;
    },
  ) =>
    http
      .get<CustomerSummary[]>("/lookups/customers", {
        params: {
          q,
          limit,
          route: filters?.route || undefined,
          telesale: filters?.telesale || undefined,
          sales_rep: filters?.sales_rep || undefined,
          corporate_only: filters?.corporate_only || undefined,
        },
      })
      .then((r) => r.data),

  getCustomer: (account: string) =>
    http.get<CustomerDetail>(`/lookups/customers/${encodeURIComponent(account)}`).then((r) => r.data),

  updateCustomerContact: (
    account: string,
    payload: { contact_name: string; contact_tel: string; contact_tel2: string },
  ) =>
    http
      .patch<CustomerDetail>(
        `/lookups/customers/${encodeURIComponent(account)}/contact`,
        payload,
      )
      .then((r) => r.data),

  // ---------- Customer-assignment maintenance ----------
  listRouteOptions: () =>
    http.get<RouteOption[]>("/lookups/route-options").then((r) => r.data),

  listTelesaleOptions: () =>
    http.get<TelesaleOption[]>("/lookups/telesale-options").then((r) => r.data),

  listSalesReps: () =>
    http.get<SalesRepOption[]>("/lookups/sales-reps").then((r) => r.data),

  listCategoryOptions: () =>
    http.get<CategoryOption[]>("/lookups/category-options").then((r) => r.data),

  /** Union of audit.cUsername, SalesRep.Code and _rtblAgents.cAgentName —
   *  anyone the app has ever seen as an agent. Used by the target form
   *  so non-rep logins (admins) can own targets too. */
  listAllAgents: () =>
    http.get<AllAgentOption[]>("/lookups/all-agents").then((r) => r.data),

  /** Merged Agent dropdown: SalesRep.Code ∪ telesale user-dict options,
   *  each tagged with source. Lets the target form present one dropdown
   *  and still know whether to persist the pick as cUsername or
   *  cTelesales. */
  listAgentOptions: () =>
    http.get<AgentOption[]>("/lookups/agent-options").then((r) => r.data),

  getCustomerAssignment: (account: string) =>
    http
      .get<CustomerAssignment>(
        `/lookups/customers/${encodeURIComponent(account)}/assignment`,
      )
      .then((r) => r.data),

  updateCustomerAssignment: (
    account: string,
    payload: CustomerAssignmentUpdate,
  ) =>
    http
      .patch<CustomerAssignment>(
        `/lookups/customers/${encodeURIComponent(account)}/assignment`,
        payload,
      )
      .then((r) => r.data),

  bulkUpdateCustomerAssignment: (payload: CustomerBulkAssignmentUpdate) =>
    http
      .patch<CustomerBulkAssignmentResult>(
        "/lookups/customers/bulk-assignment",
        payload,
      )
      .then((r) => r.data),

  searchInventory: (q: string, filters?: { category?: string | null }) =>
    http
      .get<InventoryItem[]>("/lookups/inventory", {
        params: {
          q,
          limit: 50,
          category: filters?.category || undefined,
        },
      })
      .then((r) => r.data),

  listWarehouses: () => http.get<Warehouse[]>("/lookups/warehouses").then((r) => r.data),
  listDefaultWarehouseChoices: () =>
    http.get<Warehouse[]>("/lookups/warehouses/default-choices").then((r) => r.data),
  listTaxTypes: () => http.get<TaxType[]>("/lookups/tax-types").then((r) => r.data),
  listProjects: () => http.get<Project[]>("/lookups/projects").then((r) => r.data),
  searchProjects: (q: string) =>
    http.get<Project[]>("/lookups/projects", { params: { q, limit: 50 } }).then((r) => r.data),
  listSettlementTerms: () => http.get<SettlementTerm[]>("/lookups/settlement-terms").then((r) => r.data),

  getCustomerPrices: (
    account: string,
    stockCodes: string[],
    asOf: string,
    warehouseCode?: string | null,
  ) =>
    http
      .post<Record<string, CustomerPrice>>("/lookups/customer-prices", {
        account,
        stock_codes: stockCodes,
        as_of: asOf,
        warehouse_code: warehouseCode ?? null,
      })
      .then((r) => r.data),

  getPackSizes: (stockCodes: string[]) =>
    http
      .post<Record<string, number>>("/lookups/pack-sizes", {
        stock_codes: stockCodes,
      })
      .then((r) => r.data),

  // --- Order Import (emailed purchase-order PDFs) ---
  parseImportPdf: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return http.post<ParsedOrder>("/order-import/parse", form).then((r) => r.data);
  },
  checkImportStore: (customerAccount: string, storeCode: string) =>
    http
      .get<ImportStoreCheck>("/order-import/store-check", {
        params: { account: customerAccount, store_code: storeCode },
      })
      .then((r) => r.data),
  validateImport: (
    customerAccount: string,
    document: ParsedOrder,
    deliveryDate: string,
    priceOverrides: Record<number, number> = {},
    reviewId: number | null = null,
  ) =>
    http
      .post<ImportValidation>("/order-import/validate", {
        customer_account: customerAccount,
        document,
        delivery_date: deliveryDate,
        price_overrides: priceOverrides,
        review_id: reviewId,
      })
      .then((r) => r.data),
  saveImportMapping: (payload: {
    customer_account: string;
    supplier_code: string;
    item_code: string;
    description: string;
    stock_code: string;
  }) => http.post<ItemMapping>("/order-import/mappings", payload).then((r) => r.data),
  deleteImportMapping: (id: number) =>
    http.delete(`/order-import/mappings/${id}`).then(() => undefined),
  createImportedOrder: (payload: ImportCreateRequest) =>
    http.post<SalesOrderCreateResponse>("/order-import/create", payload).then((r) => r.data),
  submitImportReview: (payload: ImportCreateRequest & { note?: string | null }) =>
    http.post<ImportReviewSummary>("/order-import/reviews", payload).then((r) => r.data),
  listImportReviews: (status: ImportReviewStatus | "" = "PENDING") =>
    http
      .get<ImportReviewSummary[]>("/order-import/reviews", { params: { status } })
      .then((r) => r.data),
  /** The signed-in agent's draft orders (also purges expired ones). */
  listDrafts: () => http.get<DraftSummary[]>("/drafts").then((r) => r.data),
  getDraft: (id: number) => http.get<DraftDetail>(`/drafts/${id}`).then((r) => r.data),
  /** 409 when the agent already has a draft for the customer (replace: true overwrites it). */
  saveDraft: (payload: DraftSaveRequest) =>
    http.post<DraftSummary>("/drafts", payload).then((r) => r.data),
  deleteDraft: (id: number) => http.delete(`/drafts/${id}`).then(() => undefined),
  /** Orders waiting for a supervisor (badge). Always 0 for agents. */
  getPendingReviewCount: () =>
    http
      .get<{ pending: number }>("/order-import/reviews/pending-count")
      .then((r) => r.data.pending),
  getImportReview: (id: number) =>
    http.get<ImportReviewDetail>(`/order-import/reviews/${id}`).then((r) => r.data),
  rejectImportReview: (id: number, note: string) =>
    http
      .post<ImportReviewSummary>(`/order-import/reviews/${id}/reject`, { note })
      .then((r) => r.data),
  /** Open the original PO PDF in a new tab. Fetched as a blob so the auth
   *  header is sent; the tab is opened first so popup blockers allow it. */
  openImportDocument: async (id: number) => {
    const tab = window.open("", "_blank");
    try {
      const resp = await http.get(`/order-import/documents/${id}`, { responseType: "blob" });
      const url = URL.createObjectURL(resp.data);
      if (tab) tab.location.href = url;
      else window.open(url, "_blank");
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    } catch (e) {
      tab?.close();
      throw e;
    }
  },
  getBrokenPriceRows: () =>
    http.get<BrokenPriceRow[]>("/price-review/broken-rows").then((r) => r.data),
  /** Re-prices the open lines and archives the settled ones (on demand). */
  getPoPriceDifferences: (allPos = false) =>
    http
      .get<PoDiffReport>("/price-review/po-differences", { params: { all_pos: allPos } })
      .then((r) => r.data),
  /** A supervisor chooses not to fix a difference; a reason is required. */
  dismissPoDiffLine: (id: number, reason: string) =>
    http
      .post<PoDiffLine>(`/price-review/po-differences/${id}/dismiss`, { reason })
      .then((r) => r.data),
  getArchivedPoDiffLines: (account: string, po: string, orderNum: string) =>
    http
      .get<PoDiffLine[]>("/price-review/po-differences/lines", {
        params: { account, po, order_num: orderNum },
      })
      .then((r) => r.data),

  placeOrder: (payload: SalesOrderCreate) =>
    http.post<SalesOrderCreateResponse>("/sales-orders", payload).then((r) => r.data),

  searchSalesOrders: (filters: SalesOrderSearchFilters) =>
    http
      .get<SalesOrderSearchResult[]>("/sales-orders", { params: filters })
      .then((r) => r.data),

  listOrdersByOrderDate: (
    account: string,
    date: string,
    by: "order" | "delivery" = "order",
  ) =>
    http
      .get<SalesOrderSearchResult[]>("/sales-orders/by-order-date", {
        params: { account, date, by },
      })
      .then((r) => r.data),

  /** Orders for one customer across a date range (inclusive). Used by the
   *  Deliveries drill-in panel + orders modal when the user picks a span
   *  of days instead of a single day. */
  listOrdersByDateRange: (
    account: string,
    from: string,
    to: string,
    by: "order" | "delivery" = "order",
  ) =>
    http
      .get<SalesOrderSearchResult[]>("/sales-orders/by-order-date", {
        params: { account, from, to, by },
      })
      .then((r) => r.data),

  getSalesOrder: (orderNo: string) =>
    http
      .get<SalesOrderDetail>(`/sales-orders/${encodeURIComponent(orderNo)}`)
      .then((r) => r.data),

  updateOrder: (orderNo: string, payload: SalesOrderCreate) =>
    http
      .put<SalesOrderCreateResponse>(`/sales-orders/${encodeURIComponent(orderNo)}`, payload)
      .then((r) => r.data),

  cancelSalesOrder: (orderNo: string) =>
    http
      .post<{ order_no: string; doc_state: number; doc_state_label: string }>(
        `/sales-orders/${encodeURIComponent(orderNo)}/cancel`,
      )
      .then((r) => r.data),

  updateSalesOrderDeliveryDate: (orderNo: string, deliveryDate: string) =>
    http
      .patch<SalesOrderDetail>(
        `/sales-orders/${encodeURIComponent(orderNo)}/delivery-date`,
        { delivery_date: deliveryDate },
      )
      .then((r) => r.data),

  // ---------- History (telesales dashboard) ----------
  listHistoryRoutes: () =>
    http.get<HistoryRoute[]>("/history/routes").then((r) => r.data),

  listHistoryCustomers: (route: string) =>
    http
      .get<HistoryCustomer[]>("/history/customers", { params: { route } })
      .then((r) => r.data),

  listCustomerOrdersToday: (
    route?: string | null,
    date?: string,
    by: "order" | "delivery" = "order",
  ) =>
    http
      .get<CustomerOrderCount[]>("/history/orders-today", {
        params: {
          by,
          ...(route ? { route } : {}),
          ...(date ? { date } : {}),
        },
      })
      .then((r) => r.data),

  /** Per-customer order counts across a date range (inclusive). Used by
   *  the Deliveries report. For a single day pass `from === to` (same as
   *  calling listCustomerOrdersToday with that date). */
  listCustomerOrdersInRange: (
    route: string | null,
    from: string,
    to: string,
    by: "order" | "delivery" = "order",
  ) =>
    http
      .get<CustomerOrderCount[]>("/history/orders-today", {
        params: {
          by,
          from,
          to,
          ...(route ? { route } : {}),
        },
      })
      .then((r) => r.data),

  /** Download the xlsx workbook of all customer order counts for a date
   *  range (inclusive — pass `from === to` for a single day). Triggers a
   *  browser download — fetched as a binary blob so our auth interceptor
   *  still attaches the Bearer token. */
  exportOrdersPerDayXlsx: async (
    from: string,
    to: string,
    by: "order" | "delivery" = "order",
  ) => {
    const resp = await http.get<Blob>("/history/export-orders-per-day", {
      params: { from, to, by },
      responseType: "blob",
    });
    const blob = resp.data;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download =
      from === to
        ? `orders-per-day-${from}.xlsx`
        : `orders-per-day-${from}_to_${to}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
  },

  // ---------- Order Intake dashboard ----------
  /** Hourly buckets for orders created in [from, to] (max 31 days). Called
   *  on screen open / Refresh / date change only — never on a timer. */
  getOrderIntake: (from: string, to: string) =>
    http
      .get<OrderIntakeSummary>("/analytics/order-intake", { params: { from, to } })
      .then((r) => r.data),

  listOrderIntakeOrders: (from: string, to: string, filter: OrderIntakeDrillFilter) =>
    http
      .get<OrderIntakeOrder[]>("/analytics/order-intake/orders", {
        params: {
          from,
          to,
          hour: filter.hour ?? undefined,
          source: filter.source || undefined,
          user: filter.user || undefined,
          corp_group: filter.corp_group || undefined,
          dist_centre: filter.dist_centre || undefined,
          route: filter.route || undefined,
        },
      })
      .then((r) => r.data),

  exportOrderIntakeXlsx: async (from: string, to: string) => {
    const resp = await http.get<Blob>("/analytics/order-intake/export", {
      params: { from, to },
      responseType: "blob",
    });
    const url = URL.createObjectURL(resp.data);
    const a = document.createElement("a");
    a.href = url;
    a.download = from === to ? `order-intake-${from}.xlsx` : `order-intake-${from}_to_${to}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
  },

  listHistoryOrders: (customerId: string) =>
    http
      .get<HistoricalOrder[]>("/history/orders", { params: { customer_id: customerId } })
      .then((r) => r.data),

  listCustomerDealItems: (
    customerId: string,
    asOf: string,
    warehouseCode?: string | null,
  ) =>
    http
      .get<CustomerDealItem[]>("/history/deal-items", {
        params: {
          customer_id: customerId,
          as_of: asOf,
          ...(warehouseCode ? { warehouse_code: warehouseCode } : {}),
        },
      })
      .then((r) => r.data),

  // ---------- Customer contact log ----------
  /** Latest outcome per customer for the given day across ALL routes
   *  (default: today). Customers are 1:1 with their route so route-scoped
   *  filtering adds no discrimination value here — the frontend uses this
   *  to colour every dropdown dot regardless of whether a route is picked
   *  yet. The counter at the top of the picker filters client-side by
   *  intersecting with the current route's customer list. */
  listTodayOutcomes: (date?: string) =>
    http
      .get<ContactLogEntry[]>("/contact-log/by-date", {
        params: { ...(date ? { date } : {}) },
      })
      .then((r) => r.data),

  /** Latest outcome per customer across a date range (inclusive). Used by
   *  the Deliveries Reason column. For a single day pass `from === to`. */
  /** Deliveries report: all routes' customers with orders + litres for
   *  orders delivered in [from, to]. Cached 20 s on the backend. */
  listDeliveries: (from: string, to: string) =>
    http
      .get<DeliveryCustomer[]>("/history/deliveries", { params: { from, to } })
      .then((r) => r.data),

  /** SUPERVISOR+: set the customer card's Dist Centre to this DC warehouse's
   *  DC (fixing a wrongly set-up customer from the mismatch banner). */
  fixCustomerDistCentre: (account: string, warehouseCode: string) =>
    http
      .patch<CustomerDetail>(`/lookups/customers/${encodeURIComponent(account)}/dist-centre`, {
        warehouse_code: warehouseCode,
      })
      .then((r) => r.data),

  listOutcomesInRange: (from: string, to: string) =>
    http
      .get<ContactLogEntry[]>("/contact-log/by-date", {
        params: { from, to },
      })
      .then((r) => r.data),

  logContactOutcome: (payload: ContactLogCreate) =>
    http.post<{ id: number; outcome: string }>("/contact-log", payload).then((r) => r.data),

  // ---------- Analytics ----------
  /** One level of the Customer History pivot. The caller passes the full
   *  dim hierarchy (`path`), which level to fetch (`depth`), and the
   *  parent values matching the first `depth` dims. Lazy per level so
   *  initial load (depth=0) is tiny and each expand click fetches just
   *  that slice. */
  getCustomerHistoryLevel: (
    path: PivotDim[],
    depth: number,
    values: string[],
    weeks = 12,
  ) => {
    const params: Record<string, string | number> = {
      path: path.join(","),
      depth,
      weeks,
    };
    values.forEach((v, i) => {
      params[`v${i}`] = v;
    });
    return http
      .get<PivotLevelResponse>("/analytics/customer-history", { params })
      .then((r) => r.data);
  },

  /** Individual invoice/credit-note lines that roll up into a bucket.
   *  `values` has length = clicked row's depth + 1 (parent chain + the
   *  row's own key). `weekStart` is the Monday ISO date of the clicked
   *  week column — omit to span the full window (used by the Total
   *  column click). */
  getCustomerHistoryTransactions: (
    path: PivotDim[],
    values: string[],
    weekStart?: string,
    weeks = 12,
  ) => {
    const params: Record<string, string | number> = {
      path: path.join(","),
      levels: values.length,
      weeks,
    };
    values.forEach((v, i) => {
      params[`v${i}`] = v;
    });
    if (weekStart) params.week_start = weekStart;
    return http
      .get<TransactionListResponse>("/analytics/transactions", { params })
      .then((r) => r.data);
  },

  listOrderSuggestions: (
    customerId: string,
    asOf: string,
    weeks = 6,
    minWeeks = 3,
    warehouseCode?: string | null,
    /** Persist this presentation so the rep's decision can be captured on
     *  save. Only the Order Assistant passes true. */
    record = false,
  ) =>
    http
      .get<OrderSuggestion[]>("/history/suggestions", {
        params: {
          customer_id: customerId,
          as_of: asOf,
          weeks,
          min_weeks: minWeeks,
          ...(warehouseCode ? { warehouse_code: warehouseCode } : {}),
          ...(record ? { record: true } : {}),
        },
      })
      .then((r) => r.data),

  listWeekTargets: (customerId: string, asOf: string) =>
    http
      .get<WeekTarget[]>("/history/week-targets", {
        params: { customer_id: customerId, as_of: asOf },
      })
      .then((r) => r.data),

  // ---------- Litre targets ----------
  listTargets: (params?: {
    scope?:
      | "agent"
      | "telesales"
      | "sales_rep"
      | "route"
      | "customer"
      | "category"
      | "stock"
      | "compound"
      | "all";
    username?: string;
    activeOnly?: boolean;
  }) =>
    http
      .get<TargetListResponse>("/targets", {
        params: {
          ...(params?.scope ? { scope: params.scope } : {}),
          ...(params?.username ? { username: params.username } : {}),
          ...(params?.activeOnly !== undefined ? { active_only: params.activeOnly } : {}),
        },
      })
      .then((r) => r.data),

  createTarget: (payload: TargetUpsert) =>
    http.post<TargetWithActual>("/targets", payload).then((r) => r.data),

  updateTarget: (id: number, payload: TargetUpsert) =>
    http.patch<TargetWithActual>(`/targets/${id}`, payload).then((r) => r.data),

  deleteTarget: (id: number) =>
    http.delete<void>(`/targets/${id}`).then(() => undefined),

  /** The agent-only, most-recently-created, currently-active target for
   *  the logged-in user. Powers the single progress bar on the Order
   *  Assistant performance strip. Returns `null` when the user has no
   *  such target. */
  getMyPrimaryTarget: () =>
    http
      .get<TargetWithActual | null>("/targets/my-primary")
      .then((r) => r.data),

  /** Every active target where the logged-in user is on one of the
   *  three "who" dims (cUsername, cTelesales, cSalesRep). Powers the
   *  "All my targets" modal opened from the Order Assistant strip. */
  getMyTargets: () =>
    http
      .get<TargetListResponse>("/targets/mine")
      .then((r) => r.data),
};

// ---------- Admin: Users & Roles (Phase 3.3) ------------------------
// All ADMIN-only. The frontend already hides the entrypoint (the hub
// card) for non-admins, but the backend returns 403 if anyone else
// hits these endpoints.

export type AdminUserRow = {
  agent_id: number;
  agent_name: string;
  agent_description: string;
  /** null when the agent exists in _rtblAgents but isn't registered
   *  as an app user yet. The UI renders those as "Add as …" rows. */
  app_user_id: number | null;
  role: AppUserRole | null;
  is_active: boolean | null;
  registered_at: string | null;
  registered_by: string | null;
  /** _rtblAgents.iDefWhseId as a code; null = not set. */
  default_warehouse_code: string | null;
};

/** A signed-in user (Users & Roles "who is logged in"). */
export type UserPresence = {
  username: string;
  /** Active in the last ~2.5 minutes. */
  online: boolean;
  since: string;
  last_seen: string;
  sessions: number;
};

export const adminApi = {
  listUsers: () =>
    http.get<AdminUserRow[]>("/admin/users").then((r) => r.data),

  /** Memory-only on the server; polled every 60 s while the screen is open. */
  listPresence: () => http.get<UserPresence[]>("/admin/presence").then((r) => r.data),

  createUser: (payload: { agent_id: number; role: AppUserRole }) =>
    http
      .post<AdminUserRow>("/admin/users", payload)
      .then((r) => r.data),

  updateUser: (
    appUserId: number,
    payload: { role?: AppUserRole; is_active?: boolean; default_warehouse_code?: string },
  ) =>
    http
      .patch<AdminUserRow>(`/admin/users/${appUserId}`, payload)
      .then((r) => r.data),
};
