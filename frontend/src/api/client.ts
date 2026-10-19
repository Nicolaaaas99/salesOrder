import axios from "axios";
import type {
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
  OrderSuggestion,
} from "./types";

// Serialize array params as repeated keys (`states=1&states=3`) rather than
// axios's default `states[]=…` form, so FastAPI's `List[int]` reads them.
const http = axios.create({
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
http.interceptors.request.use((cfg) => {
  const token = sessionStorage.getItem("auth_token");
  if (token) {
    cfg.headers = cfg.headers ?? {};
    (cfg.headers as any).Authorization = `Bearer ${token}`;
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

export type LoginResponse = {
  token: string;
  username: string;
  default_warehouse_code: string | null;
  force_ext_order_num: boolean;
};

export const auth = {
  login: (username: string, password: string) =>
    http
      .post<LoginResponse>("/auth/login", { username, password })
      .then((r) => r.data),
  logout: () => http.post("/auth/logout").then((r) => r.data),
  me: () =>
    http
      .get<{
        username: string;
        default_warehouse_code: string | null;
        force_ext_order_num: boolean;
      }>("/auth/me")
      .then((r) => r.data),
  setDefaultWarehouse: (code: string) =>
    http
      .post<{ default_warehouse_code: string }>("/auth/default-warehouse", { code })
      .then((r) => r.data),
};

export const api = {
  searchCustomers: (q: string, limit = 50) =>
    http.get<CustomerSummary[]>("/lookups/customers", { params: { q, limit } }).then((r) => r.data),

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

  searchInventory: (q: string) =>
    http.get<InventoryItem[]>("/lookups/inventory", { params: { q, limit: 50 } }).then((r) => r.data),

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
    warehouseCode?: string | null,
  ) =>
    http
      .post<Record<string, CustomerPrice>>("/lookups/customer-prices", {
        account,
        stock_codes: stockCodes,
        warehouse_code: warehouseCode ?? null,
      })
      .then((r) => r.data),

  getPackSizes: (stockCodes: string[]) =>
    http
      .post<Record<string, number>>("/lookups/pack-sizes", {
        stock_codes: stockCodes,
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

  /** Download the xlsx workbook of all customer order counts for a date.
   *  Triggers a browser download — fetched as a binary blob so our auth
   *  interceptor still attaches the Bearer token. */
  exportOrdersPerDayXlsx: async (
    date: string,
    by: "order" | "delivery" = "order",
  ) => {
    const resp = await http.get<Blob>("/history/export-orders-per-day", {
      params: { date, by },
      responseType: "blob",
    });
    const blob = resp.data;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `orders-per-day-${date}.xlsx`;
    a.click();
    URL.revokeObjectURL(url);
  },

  listHistoryOrders: (customerId: string) =>
    http
      .get<HistoricalOrder[]>("/history/orders", { params: { customer_id: customerId } })
      .then((r) => r.data),

  listOrderSuggestions: (
    customerId: string,
    weeks = 6,
    minWeeks = 3,
    warehouseCode?: string | null,
  ) =>
    http
      .get<OrderSuggestion[]>("/history/suggestions", {
        params: {
          customer_id: customerId,
          weeks,
          min_weeks: minWeeks,
          ...(warehouseCode ? { warehouse_code: warehouseCode } : {}),
        },
      })
      .then((r) => r.data),
};
