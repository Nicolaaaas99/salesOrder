import { useEffect, useMemo, useState } from "react";
import Select from "react-select";
import { Truck, Calendar, XCircle, FileText, ArrowLeft, Search, ListOrdered, ExternalLink } from "lucide-react";
import { api } from "../api/client";
import type {
  CustomerDetail,
  HistoricalOrder,
  OrderSuggestion,
  SalesOrderSearchResult,
} from "../api/types";
import { HistoryGrid } from "./HistoryGrid";
import { CreditStatus } from "./CreditStatus";
import { DateInput } from "./DateInput";
import { productKey } from "../utils/history";
import { today } from "../utils/date";
import { fmtCurrency } from "../utils/format";

type Option = { value: string; label: string };
type CustomerRow = { id: string; display: string; ordersOnDate: number };
type OrderFilter = "all" | "with" | "without";

const selectStyles = {
  control: (base: any) => ({
    ...base,
    padding: "1px 6px",
    border: "1px solid #d1d5db",
    borderRadius: "6px",
    boxShadow: "none",
    fontSize: "13px",
    minHeight: "32px",
    "&:hover": { borderColor: "#3b82f6" },
  }),
  menu: (base: any) => ({
    ...base,
    borderRadius: "6px",
    border: "1px solid #e5e7eb",
    boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
    fontSize: "13px",
    zIndex: 40,
  }),
  option: (base: any, state: any) => ({
    ...base,
    fontSize: "13px",
    padding: "4px 8px",
    backgroundColor: state.isFocused ? "#eff6ff" : "white",
    color: "#1f2937",
    "&:active": { backgroundColor: "#dbeafe" },
  }),
};

type RouteOverviewProps = {
  /** Called when the user clicks "Go to source" on an order in the orders
   *  modal — opens the order in the Sales Order Entry tab for editing. */
  onOpenOrder?: (orderNo: string) => void;
};

export function RouteOverviewScreen({ onOpenOrder }: RouteOverviewProps = {}) {
  const [routes, setRoutes] = useState<Option[]>([]);
  const [selectedRoute, setSelectedRoute] = useState<Option | null>(null);
  const [selectedDate, setSelectedDate] = useState<string>(today());
  const [customers, setCustomers] = useState<CustomerRow[]>([]);
  const [search, setSearch] = useState("");
  const [orderFilter, setOrderFilter] = useState<OrderFilter>("all");

  // Drill-in state — when set, we switch to the HistoryGrid view.
  const [drillCustomer, setDrillCustomer] = useState<{ id: string; display: string } | null>(null);

  // Orders modal — when set, show the orders placed by this customer on the
  // selected date, with a "Go to source" link to edit each one.
  const [viewingOrdersFor, setViewingOrdersFor] = useState<{ id: string; display: string } | null>(null);
  const [orderList, setOrderList] = useState<SalesOrderSearchResult[]>([]);
  const [isLoadingOrderList, setIsLoadingOrderList] = useState(false);
  const [customerDetail, setCustomerDetail] = useState<CustomerDetail | null>(null);
  const [orders, setOrders] = useState<HistoricalOrder[]>([]);
  const [suggestions, setSuggestions] = useState<OrderSuggestion[]>([]);

  const [isLoadingRoutes, setIsLoadingRoutes] = useState(false);
  const [isLoadingCustomers, setIsLoadingCustomers] = useState(false);
  const [isLoadingOrders, setIsLoadingOrders] = useState(false);

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setIsLoadingRoutes(true);
      setError(null);
      try {
        const data = await api.listHistoryRoutes();
        setRoutes(data.map((r) => ({ value: r.id, label: r.display })));
      } catch (e: any) {
        setError(`Failed to load routes: ${e?.message ?? e}`);
      } finally {
        setIsLoadingRoutes(false);
      }
    })();
  }, []);

  // Fetch customers + order counts whenever route or date changes.
  useEffect(() => {
    if (!selectedRoute) {
      setCustomers([]);
      return;
    }
    (async () => {
      setIsLoadingCustomers(true);
      setError(null);
      try {
        const [custs, counts] = await Promise.all([
          api.listHistoryCustomers(selectedRoute.value),
          api.listCustomerOrdersToday(selectedRoute.value, selectedDate),
        ]);
        const countMap = new Map(counts.map((c) => [c.account, c.order_count]));
        const merged: CustomerRow[] = custs.map((c) => ({
          id: c.id,
          display: c.display,
          ordersOnDate: countMap.get(c.id) ?? 0,
        }));
        setCustomers(merged);
      } catch {
        setError("Failed to load customers / order counts.");
      } finally {
        setIsLoadingCustomers(false);
      }
    })();
  }, [selectedRoute, selectedDate]);

  // Fetch history/suggestions/detail when drilling into a customer.
  useEffect(() => {
    if (!drillCustomer) {
      setOrders([]);
      setSuggestions([]);
      setCustomerDetail(null);
      return;
    }
    (async () => {
      setIsLoadingOrders(true);
      setError(null);
      try {
        const [ordersData, suggestionsData, detail] = await Promise.all([
          api.listHistoryOrders(drillCustomer.id),
          api.listOrderSuggestions(drillCustomer.id),
          api.getCustomer(drillCustomer.id).catch(() => null),
        ]);
        setOrders(ordersData);
        setSuggestions(suggestionsData);
        setCustomerDetail(detail);
      } catch {
        setError("Failed to load history / suggestions.");
      } finally {
        setIsLoadingOrders(false);
      }
    })();
  }, [drillCustomer]);

  // Fetch orders for the chosen customer+date when the modal opens.
  useEffect(() => {
    if (!viewingOrdersFor) {
      setOrderList([]);
      return;
    }
    (async () => {
      setIsLoadingOrderList(true);
      try {
        const data = await api.listOrdersByOrderDate(viewingOrdersFor.id, selectedDate);
        setOrderList(data);
      } catch {
        setOrderList([]);
      } finally {
        setIsLoadingOrderList(false);
      }
    })();
  }, [viewingOrdersFor, selectedDate]);

  const suggestionsMap = useMemo(() => {
    const m = new Map<string, OrderSuggestion>();
    suggestions.forEach((s) => {
      m.set(productKey(s.stock_code, s.description), s);
    });
    return m;
  }, [suggestions]);

  const filteredCustomers = useMemo(() => {
    const term = search.trim().toLowerCase();
    return customers
      .filter((c) => {
        if (orderFilter === "with") return c.ordersOnDate > 0;
        if (orderFilter === "without") return c.ordersOnDate === 0;
        return true;
      })
      .filter((c) =>
        term
          ? c.id.toLowerCase().includes(term) ||
            c.display.toLowerCase().includes(term)
          : true
      )
      .sort((a, b) => {
        // Customers with orders first, then alphabetical
        if ((a.ordersOnDate > 0) !== (b.ordersOnDate > 0)) {
          return a.ordersOnDate > 0 ? -1 : 1;
        }
        return a.display.localeCompare(b.display);
      });
  }, [customers, search, orderFilter]);

  const totalOrders = useMemo(
    () => customers.reduce((sum, c) => sum + c.ordersOnDate, 0),
    [customers]
  );
  const customersWithOrders = useMemo(
    () => customers.filter((c) => c.ordersOnDate > 0).length,
    [customers]
  );

  return (
    <div className="h-full bg-gray-50 p-2 flex flex-col">
      <div className="max-w-[1600px] mx-auto w-full flex-1 min-h-0 flex flex-col gap-2">
        {/* Top bar: route + date, compact and left-aligned */}
        <div className="flex items-center gap-2 flex-shrink-0">
          <div className="bg-white rounded border p-2 shadow-sm w-[36rem]">
            <label className="flex items-center font-semibold text-gray-800 mb-1 text-xs">
              <Truck className="w-3 h-3 mr-1 text-blue-600" />
              Route
            </label>
            <Select
              options={routes}
              value={selectedRoute}
              onChange={(opt) => {
                setSelectedRoute(opt as Option | null);
                setDrillCustomer(null);
              }}
              placeholder="Search route..."
              isDisabled={isLoadingRoutes}
              isLoading={isLoadingRoutes}
              styles={selectStyles}
              isSearchable
              isClearable
            />
          </div>

          <div className="bg-white rounded border p-2 shadow-sm w-44">
            <label className="flex items-center font-semibold text-gray-800 mb-1 text-xs">
              <Calendar className="w-3 h-3 mr-1 text-blue-600" />
              Date
            </label>
            <DateInput value={selectedDate} onChange={setSelectedDate} />
          </div>
        </div>

        {error && (
          <div className="p-1 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs flex-shrink-0">
            <XCircle className="w-3 h-3 mr-1 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Default view: route summary table. Drill view: history grid + info. */}
        {!drillCustomer ? (
          <RouteSummary
            selectedRoute={selectedRoute}
            customers={filteredCustomers}
            isLoading={isLoadingCustomers}
            search={search}
            setSearch={setSearch}
            orderFilter={orderFilter}
            setOrderFilter={setOrderFilter}
            totalOrders={totalOrders}
            customersWithOrders={customersWithOrders}
            totalCustomers={customers.length}
            onDrillIn={(c) => setDrillCustomer(c)}
            onViewOrders={(c) => setViewingOrdersFor(c)}
          />
        ) : (
          <>
            <div className="flex items-center justify-between bg-white rounded border p-2 shadow-sm flex-shrink-0">
              <button
                onClick={() => setDrillCustomer(null)}
                className="flex items-center gap-1 text-sm text-blue-600 hover:text-blue-800"
              >
                <ArrowLeft className="w-4 h-4" />
                Back to route overview
              </button>
              <div className="text-sm font-semibold text-gray-800">
                {drillCustomer.id} · {drillCustomer.display}
              </div>
            </div>

            <div className="grid grid-cols-[1fr_380px] gap-2 flex-1 min-h-0">
              <HistoryGrid
                orders={orders}
                isLoading={isLoadingOrders}
                emptyMessage="No history for this customer"
                suggestions={suggestionsMap}
                heightStyle="100%"
              />
              <CustomerInfoPanel customer={customerDetail} />
            </div>
          </>
        )}
      </div>

      {viewingOrdersFor && (
        <OrdersModal
          customer={viewingOrdersFor}
          date={selectedDate}
          orders={orderList}
          isLoading={isLoadingOrderList}
          onClose={() => setViewingOrdersFor(null)}
          onOpenOrder={
            onOpenOrder
              ? (no) => {
                  onOpenOrder(no);
                  setViewingOrdersFor(null);
                }
              : undefined
          }
        />
      )}
    </div>
  );
}

type RouteSummaryProps = {
  selectedRoute: Option | null;
  customers: CustomerRow[];
  isLoading: boolean;
  search: string;
  setSearch: (s: string) => void;
  orderFilter: OrderFilter;
  setOrderFilter: (f: OrderFilter) => void;
  totalOrders: number;
  customersWithOrders: number;
  totalCustomers: number;
  onDrillIn: (c: { id: string; display: string }) => void;
  onViewOrders: (c: { id: string; display: string }) => void;
};

function RouteSummary({
  selectedRoute,
  customers,
  isLoading,
  search,
  setSearch,
  orderFilter,
  setOrderFilter,
  totalOrders,
  customersWithOrders,
  totalCustomers,
  onDrillIn,
  onViewOrders,
}: RouteSummaryProps) {
  if (!selectedRoute) {
    return (
      <div className="bg-white rounded border shadow-sm flex-1 min-h-0 flex flex-col items-center justify-center text-gray-500 p-6">
        <Truck className="w-8 h-8 mb-2 text-gray-300" />
        <p className="text-sm">Select a route to see customer summary</p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded border shadow-sm flex-1 min-h-0 flex flex-col overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-gray-200 bg-gray-50 flex-shrink-0">
        <div className="flex items-center gap-3 text-xs text-gray-700">
          <span>
            <strong>{customersWithOrders}</strong> of{" "}
            <strong>{totalCustomers}</strong> customers ordered
          </span>
          <span>
            <strong>{totalOrders}</strong> total orders
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="inline-flex rounded border border-gray-300 overflow-hidden text-xs">
            {([
              { v: "all",     label: "All" },
              { v: "with",    label: "With orders" },
              { v: "without", label: "No orders" },
            ] as const).map((opt, i) => (
              <button
                key={opt.v}
                onClick={() => setOrderFilter(opt.v)}
                className={`px-2 py-1 ${i > 0 ? "border-l border-gray-300" : ""} ${
                  orderFilter === opt.v
                    ? "bg-blue-600 text-white font-semibold"
                    : "bg-white text-gray-700 hover:bg-gray-50"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <div className="relative w-56">
            <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              type="text"
              placeholder="Search customer..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full pl-7 pr-2 py-1 border border-gray-300 rounded text-xs focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
            />
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="h-full flex items-center justify-center text-gray-500 text-sm p-4">
            Loading…
          </div>
        ) : customers.length === 0 ? (
          <div className="h-full flex items-center justify-center text-gray-500 text-sm p-4">
            No customers match the filters.
          </div>
        ) : (
          <table className="w-full text-xs border-collapse">
            <thead className="bg-gray-100 sticky top-0 z-10">
              <tr className="text-gray-700 border-b border-gray-300">
                <th className="text-left px-3 py-1.5 font-semibold">Account</th>
                <th className="text-left px-3 py-1.5 font-semibold">Customer</th>
                <th className="text-center px-3 py-1.5 font-semibold w-32">
                  Orders on date
                </th>
                <th className="w-28"></th>
              </tr>
            </thead>
            <tbody>
              {customers.map((c) => (
                <tr
                  key={c.id}
                  className="border-b border-gray-100 hover:bg-blue-50"
                >
                  <td className="px-3 py-1.5 font-mono text-gray-800">{c.id}</td>
                  <td className="px-3 py-1.5 text-gray-900">{c.display}</td>
                  <td className="px-3 py-1.5 text-center">
                    {c.ordersOnDate > 0 ? (
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          onViewOrders({ id: c.id, display: c.display });
                        }}
                        title="View orders"
                        className="inline-flex items-center gap-1 text-[10px] font-semibold bg-emerald-100 text-emerald-800 border border-emerald-300 px-2 py-0.5 rounded-full hover:bg-emerald-200"
                      >
                        <ListOrdered className="w-3 h-3" />
                        {c.ordersOnDate}
                      </button>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDrillIn({ id: c.id, display: c.display });
                      }}
                      className="text-xs text-blue-600 hover:text-blue-800 underline"
                    >
                      View history
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function CustomerInfoPanel({ customer }: { customer: CustomerDetail | null }) {
  if (!customer) {
    return (
      <div className="bg-white rounded border shadow-sm flex flex-col items-center justify-center text-gray-400 p-6 h-full">
        <FileText className="w-6 h-6 mb-1 text-gray-300" />
        <p className="text-xs">Loading…</p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded border shadow-sm flex flex-col overflow-hidden h-full">
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50">
        <div className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Customer</div>
        <div className="text-sm text-gray-900 font-medium">{customer.account} · {customer.name}</div>
      </div>

      <div className="flex-1 overflow-auto">
        <div className="px-2 py-2 border-b border-gray-200">
          <CreditStatus customer={customer} currentOrderIncl={0} compact />
        </div>

        {customer.credit_limit > 0 && (
          <div className="px-3 py-2 border-b border-gray-200 text-xs">
            <div className="font-semibold text-gray-700 mb-1 uppercase tracking-wide text-[11px]">Available credit</div>
            <InfoRow label="Available" value={fmtCurrency(customer.available_credit)} emphasize />
          </div>
        )}

        {customer.check_terms && (
          <div className="px-3 py-2 border-b border-gray-200 text-xs">
            <div className="font-semibold text-gray-700 mb-1 uppercase tracking-wide text-[11px]">Terms</div>
            <InfoRow label="Terms" value={customer.terms_code || "—"} />
            <InfoRow
              label="Days"
              value={customer.terms_days > 0 ? `${customer.terms_days}` : "current period"}
            />
          </div>
        )}

        <div className="px-3 py-2 border-b border-gray-200 text-xs">
          <div className="font-semibold text-gray-700 mb-1 uppercase tracking-wide text-[11px]">Delivery Address</div>
          <pre className="whitespace-pre-wrap text-gray-700 text-xs leading-snug font-sans">
            {customer.delivery_address || "—"}
          </pre>
        </div>

        <div className="px-3 py-2 border-b border-gray-200 text-xs">
          <div className="font-semibold text-gray-700 mb-1 uppercase tracking-wide text-[11px]">Contact</div>
          <InfoRow label="Name" value={customer.contact_name || "—"} />
          <InfoRow label="Tel" value={customer.contact_tel || "—"} />
          <InfoRow label="Email" value={customer.contact_email || "—"} />
        </div>
      </div>
    </div>
  );
}

function InfoRow({
  label,
  value,
  emphasize,
}: {
  label: string;
  value: string;
  emphasize?: boolean;
}) {
  return (
    <div className="flex justify-between py-0.5">
      <span className="text-gray-500">{label}</span>
      <span className={emphasize ? "font-semibold text-gray-900" : "text-gray-800"}>{value}</span>
    </div>
  );
}

type OrdersModalProps = {
  customer: { id: string; display: string };
  date: string;
  orders: SalesOrderSearchResult[];
  isLoading: boolean;
  onClose: () => void;
  onOpenOrder?: (orderNo: string) => void;
};

function OrdersModal({ customer, date, orders, isLoading, onClose, onOpenOrder }: OrdersModalProps) {
  const total = orders.reduce((s, o) => s + (o.total_incl || 0), 0);
  return (
    <div className="fixed inset-0 z-50 bg-black bg-opacity-30 flex items-center justify-center p-4">
      <div className="bg-white rounded shadow-lg max-w-3xl w-full max-h-[80vh] flex flex-col overflow-hidden">
        <div className="px-4 py-3 border-b bg-gray-50 flex items-start justify-between gap-2">
          <div>
            <div className="text-xs text-gray-500 uppercase tracking-wide">Orders on {date}</div>
            <div className="text-sm font-semibold text-gray-900">{customer.id} · {customer.display}</div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 text-xl leading-none px-2"
            title="Close"
          >
            ×
          </button>
        </div>

        <div className="flex-1 overflow-auto">
          {isLoading ? (
            <div className="h-32 flex items-center justify-center text-sm text-gray-500">Loading…</div>
          ) : orders.length === 0 ? (
            <div className="h-32 flex items-center justify-center text-sm text-gray-500">
              No orders found for this customer on {date}.
            </div>
          ) : (
            <table className="w-full text-xs border-collapse">
              <thead className="bg-gray-100 sticky top-0 z-10">
                <tr className="text-gray-700 border-b border-gray-300">
                  <th className="text-left px-3 py-1.5 font-semibold">Order #</th>
                  <th className="text-left px-3 py-1.5 font-semibold">Description</th>
                  <th className="text-left px-3 py-1.5 font-semibold">Status</th>
                  <th className="text-right px-3 py-1.5 font-semibold">Total (incl)</th>
                  <th className="w-28"></th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <tr key={o.order_no} className="border-b border-gray-100 hover:bg-blue-50">
                    <td className="px-3 py-1.5 font-mono text-gray-800">{o.order_no}</td>
                    <td className="px-3 py-1.5 text-gray-700 truncate max-w-[16rem]" title={o.description}>
                      {o.description || "—"}
                    </td>
                    <td className="px-3 py-1.5 text-gray-700">
                      {o.doc_state_label}
                      {o.is_archived && (
                        <span className="ml-1 text-[10px] bg-gray-200 text-gray-700 rounded px-1">archived</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono text-gray-900">
                      {o.total_incl.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {onOpenOrder && (
                        <button
                          onClick={() => onOpenOrder(o.order_no)}
                          className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 underline"
                        >
                          <ExternalLink className="w-3 h-3" />
                          Go to source
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-gray-300 bg-gray-50">
                  <td className="px-3 py-1.5 font-semibold text-gray-700" colSpan={3}>
                    {orders.length} order{orders.length === 1 ? "" : "s"}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono font-semibold text-gray-900">
                    {total.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                  </td>
                  <td></td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
