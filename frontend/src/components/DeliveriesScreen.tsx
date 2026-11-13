import { Fragment, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, Calendar, ChevronDown, ChevronRight, XCircle, ArrowLeft, Search, ListOrdered, Download } from "lucide-react";
import { api } from "../api/client";
import type { ContactOutcome, SalesOrderSearchResult } from "../api/types";
import { DateInput } from "./DateInput";
import { OrdersModal } from "./OrdersModal";
import { OrderSummaryModal } from "./OrderSummaryModal";
import { today } from "../utils/date";
import { useFocusRefresh } from "../utils/useFocusRefresh";

/** Deliveries report: every route, and under each route its customers, with
 *  the orders delivered in the chosen date range, their ordered litres and
 *  the latest call outcome (Reason). One request for all routes — cached
 *  20 s on the backend and cleared by every order write. */

type CustomerRow = {
  route: string;
  id: string;
  display: string;
  ordersInRange: number;
  litres: number;
  /** Latest contact-log outcome for this customer across the chosen range,
   *  or null if there's no row. */
  outcome: ContactOutcome | null;
};
type OrderFilter = "all" | "with" | "without";
/** Column sort, File Explorer style: click a header to sort ascending,
 *  click it again for descending; another header starts ascending. */
type SortKey = "route" | "strike" | "litres" | "orders";
type SortState = { key: SortKey; dir: "asc" | "desc" };
type RouteGroup = {
  route: string;
  customers: CustomerRow[];
  total: number;
  ordered: number;
  orders: number;
  litres: number;
  placed: number;
  contacted: number;
  notContacted: number;
};

/** The contact-log table went live on this date. Before this, no row ≠
 *  "not contacted" — the system simply wasn't recording. The Reason
 *  column shows a muted dash with a tooltip for ranges that end before
 *  this date, so historical data isn't mislabelled. */
const CONTACT_LOG_START_DATE = "2026-10-26";

/** Format a (from, to) pair for display. Collapses to a single date when
 *  the two bounds match. */
function fmtRange(from: string, to: string): string {
  return from === to ? from : `${from} to ${to}`;
}

const int = new Intl.NumberFormat("en-ZA", { maximumFractionDigits: 0 });

type ReasonKind = "placed" | "contacted" | "not_contacted" | "unknown";
function reasonKind(c: CustomerRow, showNotContacted: boolean): ReasonKind {
  if (c.ordersInRange > 0 || c.outcome === "PLACED") return "placed";
  if (c.outcome) return "contacted";
  return showNotContacted ? "not_contacted" : "unknown";
}

type DeliveriesProps = {
  /** Called when the user clicks "Go to source" on an order in the orders
   *  modal — opens the order in the Sales Order Entry tab for editing. */
  onOpenOrder?: (orderNo: string) => void;
  /** Return to the Analytics hub (closes this screen). */
  onBack?: () => void;
};

export function DeliveriesScreen({ onOpenOrder, onBack }: DeliveriesProps = {}) {
  // Date range. Default both to today. The pickers enforce from ≤ to.
  const [fromDate, setFromDate] = useState<string>(today());
  const [toDate, setToDate] = useState<string>(today());
  const [customers, setCustomers] = useState<CustomerRow[]>([]);
  // Bumped after any order mutation so the report re-queries.
  const [refreshKey, setRefreshKey] = useState(0);

  // `orders:mutated` is dispatched after cancel / delivery-date / save;
  // `focus` catches other users' changes (throttled to once per 30 s).
  useEffect(() => {
    const bump = () => setRefreshKey((k) => k + 1);
    window.addEventListener("orders:mutated", bump);
    return () => window.removeEventListener("orders:mutated", bump);
  }, []);
  useFocusRefresh(() => setRefreshKey((k) => k + 1));

  const [search, setSearch] = useState("");
  const [orderFilter, setOrderFilter] = useState<OrderFilter>("all");
  const [openRoutes, setOpenRoutes] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<SortState>({ key: "route", dir: "asc" });
  const clickSort = (key: SortKey) =>
    setSort((s) => (s.key === key ? { key, dir: s.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" }));

  // Orders modal for one customer + its read-only order summary.
  const [viewingOrdersFor, setViewingOrdersFor] = useState<{ id: string; display: string } | null>(null);
  const [orderList, setOrderList] = useState<SalesOrderSearchResult[]>([]);
  const [isLoadingOrderList, setIsLoadingOrderList] = useState(false);
  const [viewingSummaryOf, setViewingSummaryOf] = useState<string | null>(null);

  const [isExporting, setIsExporting] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // All routes' customers + orders/litres, and the latest call outcomes.
  useEffect(() => {
    let alive = true;
    (async () => {
      setIsLoading(true);
      setError(null);
      try {
        const [rows, outcomes] = await Promise.all([
          api.listDeliveries(fromDate, toDate),
          api.listOutcomesInRange(fromDate, toDate),
        ]);
        if (!alive) return;
        const outcomeMap = new Map(outcomes.map((o) => [o.account, o.outcome]));
        setCustomers(
          rows.map((r) => ({
            route: r.route,
            id: r.account,
            display: r.customer_name,
            ordersInRange: r.order_count,
            litres: r.litres,
            outcome: outcomeMap.get(r.account) ?? null,
          })),
        );
      } catch {
        if (alive) setError("Failed to load the deliveries report.");
      } finally {
        if (alive) setIsLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [fromDate, toDate, refreshKey]);

  // Orders for the chosen customer + range when the modal opens.
  useEffect(() => {
    if (!viewingOrdersFor) {
      setOrderList([]);
      return;
    }
    (async () => {
      setIsLoadingOrderList(true);
      try {
        setOrderList(await api.listOrdersByDateRange(viewingOrdersFor.id, fromDate, toDate, "delivery"));
      } catch {
        setOrderList([]);
      } finally {
        setIsLoadingOrderList(false);
      }
    })();
  }, [viewingOrdersFor, fromDate, toDate, refreshKey]);

  const showNotContacted = toDate >= CONTACT_LOG_START_DATE;
  const term = search.trim().toLowerCase();

  // Group by route. Search matches a route name (whole route shown) or a
  // customer's account / name (just those customers). The order filter
  // applies to customers; routes left with no customers are hidden.
  const groups = useMemo<RouteGroup[]>(() => {
    const byRoute = new Map<string, CustomerRow[]>();
    for (const c of customers) {
      if (!byRoute.has(c.route)) byRoute.set(c.route, []);
      byRoute.get(c.route)!.push(c);
    }
    const out: RouteGroup[] = [];
    for (const [route, list] of byRoute) {
      const routeHit = !term || route.toLowerCase().includes(term);
      const shown = list
        .filter((c) =>
          orderFilter === "with" ? c.ordersInRange > 0 : orderFilter === "without" ? c.ordersInRange === 0 : true,
        )
        .filter((c) => routeHit || c.id.toLowerCase().includes(term) || c.display.toLowerCase().includes(term))
        .sort((a, b) => {
          // Litres / Orders sort the customers inside each route too.
          if (sort.key === "litres" || sort.key === "orders") {
            const d = sort.key === "litres" ? a.litres - b.litres : a.ordersInRange - b.ordersInRange;
            if (d !== 0) return sort.dir === "asc" ? d : -d;
          }
          // Default: customers with orders first, then alphabetical.
          if ((a.ordersInRange > 0) !== (b.ordersInRange > 0)) return a.ordersInRange > 0 ? -1 : 1;
          return a.display.localeCompare(b.display);
        });
      if (shown.length === 0) continue;
      const kinds = shown.map((c) => reasonKind(c, showNotContacted));
      out.push({
        route,
        customers: shown,
        total: shown.length,
        ordered: shown.filter((c) => c.ordersInRange > 0).length,
        orders: shown.reduce((a, c) => a + c.ordersInRange, 0),
        litres: shown.reduce((a, c) => a + c.litres, 0),
        placed: kinds.filter((k) => k === "placed").length,
        contacted: kinds.filter((k) => k === "contacted").length,
        notContacted: kinds.filter((k) => k === "not_contacted").length,
      });
    }
    const byName = (a: RouteGroup, b: RouteGroup) => a.route.localeCompare(b.route);
    const strike = (g: RouteGroup) => (g.total > 0 ? g.ordered / g.total : 0);
    const metric: Record<Exclude<SortKey, "route">, (g: RouteGroup) => number> = {
      strike,
      litres: (g) => g.litres,
      orders: (g) => g.orders,
    };
    const sign = sort.dir === "asc" ? 1 : -1;
    return out.sort((a, b) => {
      if (sort.key === "route") {
        // "No route" always last when sorting by name.
        if (a.route === "No route") return 1;
        if (b.route === "No route") return -1;
        return sign * byName(a, b);
      }
      const f = metric[sort.key];
      // Ties fall back to route name, A-Z.
      return sign * (f(a) - f(b)) || byName(a, b);
    });
  }, [customers, term, orderFilter, showNotContacted, sort]);

  const totals = useMemo(
    () => ({
      routes: groups.length,
      customers: groups.reduce((a, g) => a + g.total, 0),
      ordered: groups.reduce((a, g) => a + g.ordered, 0),
      orders: groups.reduce((a, g) => a + g.orders, 0),
      litres: groups.reduce((a, g) => a + g.litres, 0),
    }),
    [groups],
  );

  const toggleRoute = (r: string) =>
    setOpenRoutes((prev) => {
      const next = new Set(prev);
      if (next.has(r)) next.delete(r);
      else next.add(r);
      return next;
    });
  // While searching, every matching route is open so the hits are visible.
  const isOpen = (r: string) => !!term || openRoutes.has(r);

  async function handleExport() {
    setIsExporting(true);
    try {
      await api.exportOrdersPerDayXlsx(fromDate, toDate, "delivery");
    } catch (e: any) {
      setError(`Export failed: ${e?.message ?? e}`);
    } finally {
      setIsExporting(false);
    }
  }

  const rangeLabel = fmtRange(fromDate, toDate);

  return (
    <div className="h-full bg-gray-50 p-2 flex flex-col">
      <div className="max-w-[1600px] mx-auto w-full flex-1 min-h-0 flex flex-col gap-2">
        {onBack && (
          <div className="flex items-center flex-shrink-0">
            <button onClick={onBack} className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800">
              <ArrowLeft className="w-3.5 h-3.5" />
              Back to Analytics
            </button>
          </div>
        )}
        {/* Top bar: delivery dates + export */}
        <div className="flex items-center gap-2 flex-shrink-0">
          <div className="bg-white rounded border p-2 shadow-sm w-44">
            <label className="flex items-center font-semibold text-gray-800 mb-1 text-xs">
              <Calendar className="w-3 h-3 mr-1 text-blue-600" />
              Delivery From
            </label>
            <DateInput
              value={fromDate}
              onChange={(v) => {
                setFromDate(v);
                if (v > toDate) setToDate(v);
              }}
            />
          </div>
          <div className="bg-white rounded border p-2 shadow-sm w-44">
            <label className="flex items-center font-semibold text-gray-800 mb-1 text-xs">
              <Calendar className="w-3 h-3 mr-1 text-blue-600" />
              Delivery To
            </label>
            <DateInput
              value={toDate}
              onChange={(v) => {
                setToDate(v);
                if (v < fromDate) setFromDate(v);
              }}
            />
          </div>
          <button
            onClick={handleExport}
            disabled={isExporting}
            title="Download an .xlsx workbook of orders, litres and reasons for every route in this date range"
            className="inline-flex items-center gap-1.5 px-4 py-1.5 text-xs font-semibold bg-emerald-600 text-white rounded-md shadow-sm hover:bg-emerald-700 disabled:opacity-60 disabled:cursor-not-allowed self-center"
          >
            <Download className="w-3.5 h-3.5" />
            {isExporting ? "Exporting…" : "Export"}
          </button>
        </div>

        {error && (
          <div className="p-1 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs flex-shrink-0">
            <XCircle className="w-3 h-3 mr-1 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="bg-white rounded border shadow-sm flex-1 min-h-0 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-gray-200 bg-gray-50 flex-shrink-0">
            <div className="flex items-center gap-3 text-xs text-gray-700">
              <span>
                <strong>{totals.routes}</strong> routes
              </span>
              <span>
                <strong>{int.format(totals.ordered)}</strong> of <strong>{int.format(totals.customers)}</strong>{" "}
                customers ordered
              </span>
              <span className="inline-flex items-center gap-1">
                Strike rate <StrikeRate ordered={totals.ordered} total={totals.customers} />
              </span>
              <span>
                <strong>{int.format(totals.orders)}</strong> orders
              </span>
              <span>
                <strong>{int.format(totals.litres)}</strong> litres
              </span>
            </div>
            <div className="flex items-center gap-2 text-xs">
              <button
                type="button"
                className="text-blue-700 hover:underline"
                onClick={() => setOpenRoutes(new Set(groups.map((g) => g.route)))}
              >
                Expand all
              </button>
              <button type="button" className="text-blue-700 hover:underline" onClick={() => setOpenRoutes(new Set())}>
                Collapse all
              </button>
              <div className="inline-flex rounded border border-gray-300 overflow-hidden">
                {([
                  { v: "all", label: "All" },
                  { v: "with", label: "With orders" },
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
              <div className="relative w-64">
                <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search route or customer..."
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="w-full pl-7 pr-2 py-1 border border-gray-300 rounded text-xs focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                />
              </div>
            </div>
          </div>

          <div className="flex-1 overflow-auto">
            {isLoading && customers.length === 0 ? (
              <div className="h-full flex items-center justify-center text-gray-500 text-sm p-4">Loading…</div>
            ) : groups.length === 0 ? (
              <div className="h-full flex items-center justify-center text-gray-500 text-sm p-4">
                No routes or customers match the filters.
              </div>
            ) : (
              <table className="w-full text-xs border-collapse tabular-nums">
                <thead className="bg-gray-100 sticky top-0 z-10">
                  <tr className="text-gray-700 border-b border-gray-300">
                    <SortHeader label="Route / Account" k="route" sort={sort} onSort={clickSort} align="left" className="w-56" />
                    <th className="text-left px-3 py-1.5 font-semibold">Customer</th>
                    <SortHeader
                      label="Strike rate"
                      k="strike"
                      sort={sort}
                      onSort={clickSort}
                      align="center"
                      className="w-28"
                      title="Strike rate: share of the route's customers who placed an order"
                    />
                    <SortHeader
                      label="Litres"
                      k="litres"
                      sort={sort}
                      onSort={clickSort}
                      align="right"
                      className="w-32"
                      title="Ordered litres on orders delivered in the range"
                    />
                    <SortHeader label="Orders" k="orders" sort={sort} onSort={clickSort} align="center" className="w-32" />
                    <th className="text-center px-3 py-1.5 font-semibold w-56">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {groups.map((g) => {
                    const open = isOpen(g.route);
                    return (
                      <Fragment key={g.route}>
                        <tr
                          className="bg-slate-100 border-b border-slate-200 cursor-pointer hover:bg-slate-200"
                          onClick={() => toggleRoute(g.route)}
                        >
                          <td className="px-3 py-1.5 font-semibold text-slate-900">
                            <span className="inline-flex items-center gap-1">
                              {open ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                              {g.route}
                            </span>
                          </td>
                          <td className="px-3 py-1.5 text-slate-600">
                            {g.ordered} of {g.total} customers ordered
                          </td>
                          <td className="px-3 py-1.5 text-center">
                            <StrikeRate ordered={g.ordered} total={g.total} />
                          </td>
                          <td className="px-3 py-1.5 text-right font-semibold text-slate-900">
                            {g.litres > 0 ? int.format(g.litres) : <span className="text-gray-300">—</span>}
                          </td>
                          <td className="px-3 py-1.5 text-center font-semibold text-slate-900">
                            {g.orders > 0 ? g.orders : <span className="text-gray-300">—</span>}
                          </td>
                          <td className="px-3 py-1.5 text-center">
                            <span className="inline-flex items-center gap-1">
                              {g.placed > 0 && <CountPill kind="placed" n={g.placed} />}
                              {g.contacted > 0 && <CountPill kind="contacted" n={g.contacted} />}
                              {g.notContacted > 0 && <CountPill kind="not_contacted" n={g.notContacted} />}
                            </span>
                          </td>
                        </tr>
                        {open &&
                          g.customers.map((c) => (
                            <tr key={c.id} className="border-b border-gray-100 hover:bg-blue-50">
                              <td className="pl-8 pr-3 py-1.5 font-mono text-gray-800">{c.id}</td>
                              <td className="px-3 py-1.5 text-gray-900">{c.display}</td>
                              <td />

                              <td className="px-3 py-1.5 text-right text-gray-900">
                                {c.litres > 0 ? int.format(c.litres) : <span className="text-gray-300">—</span>}
                              </td>
                              <td className="px-3 py-1.5 text-center">
                                {c.ordersInRange > 0 ? (
                                  <button
                                    onClick={() => setViewingOrdersFor({ id: c.id, display: c.display })}
                                    title="View orders"
                                    className="inline-flex items-center gap-1 text-[10px] font-semibold bg-amber-100 text-amber-800 border border-amber-300 px-2 py-0.5 rounded-full hover:bg-amber-200"
                                  >
                                    <ListOrdered className="w-3 h-3" />
                                    {c.ordersInRange}
                                  </button>
                                ) : (
                                  <span className="text-gray-300">—</span>
                                )}
                              </td>
                              <td className="px-3 py-1.5 text-center">
                                <ReasonChip outcome={c.outcome} kind={reasonKind(c, showNotContacted)} />
                              </td>
                            </tr>
                          ))}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {viewingOrdersFor && (
        <OrdersModal
          customer={viewingOrdersFor}
          date={rangeLabel}
          orders={orderList}
          isLoading={isLoadingOrderList}
          onClose={() => setViewingOrdersFor(null)}
          onViewOrder={(no) => setViewingSummaryOf(no)}
        />
      )}

      {viewingSummaryOf && (
        <OrderSummaryModal
          orderNo={viewingSummaryOf}
          onClose={() => setViewingSummaryOf(null)}
          onEdit={
            onOpenOrder
              ? (no) => {
                  onOpenOrder(no);
                  setViewingSummaryOf(null);
                  setViewingOrdersFor(null);
                }
              : undefined
          }
          onCancel={() => {
            setViewingSummaryOf(null);
            setViewingOrdersFor(null);
            // Re-fetch so the cancelled order vanishes from the counts.
            setRefreshKey((k) => k + 1);
          }}
          onDeliveryDateChanged={() => setRefreshKey((k) => k + 1)}
        />
      )}
    </div>
  );
}

const PILL: Record<Exclude<ReasonKind, "unknown">, { cls: string; title: string }> = {
  placed: { cls: "bg-emerald-100 text-emerald-800 border-emerald-300", title: "Placed" },
  contacted: { cls: "bg-amber-100 text-amber-800 border-amber-300", title: "Contacted, no order" },
  not_contacted: { cls: "bg-red-100 text-red-800 border-red-300", title: "Not contacted" },
};

/** Clickable column header: first click sorts ascending, the next click on
 *  the same header descending. The active column shows an arrow. */
function SortHeader({
  label, k, sort, onSort, align, className = "", title,
}: {
  label: string;
  k: SortKey;
  sort: SortState;
  onSort: (k: SortKey) => void;
  align: "left" | "center" | "right";
  className?: string;
  title?: string;
}) {
  const active = sort.key === k;
  const justify = align === "left" ? "justify-start" : align === "right" ? "justify-end" : "justify-center";
  return (
    <th
      className={`px-3 py-1.5 font-semibold ${className}`}
      aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      title={title}
    >
      <button
        type="button"
        onClick={() => onSort(k)}
        className={`w-full inline-flex items-center gap-1 ${justify} hover:text-blue-700 ${active ? "text-blue-700" : ""}`}
      >
        {label}
        {active ? (
          sort.dir === "asc" ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />
        ) : (
          <ArrowUpDown className="w-3 h-3 text-gray-400" />
        )}
      </button>
    </th>
  );
}

/** Strike rate: share of the customers who placed at least one order
 *  (customers ordered ÷ customers, so it never passes 100%). */
function StrikeRate({ ordered, total }: { ordered: number; total: number }) {
  const pct = total > 0 ? Math.round((ordered / total) * 100) : 0;
  return (
    <span
      className="inline-flex text-[10px] font-semibold bg-white text-slate-800 border border-slate-300 px-1.5 py-0.5 rounded tabular-nums"
      title={`Strike rate: ${ordered} of ${total} customers ordered`}
    >
      {pct}%
    </span>
  );
}

/** Route-row summary: how many of the route's customers are in each state. */
function CountPill({ kind, n }: { kind: Exclude<ReasonKind, "unknown">; n: number }) {
  const p = PILL[kind];
  return (
    <span
      className={`inline-flex justify-center min-w-[2rem] text-[10px] font-semibold border px-2 py-0.5 rounded-full ${p.cls}`}
      title={`${p.title}: ${n}`}
    >
      {n}
    </span>
  );
}

/** One Reason-column cell. Mirrors the Order Assistant dropdown-dot
 *  colours: green placed, yellow contacted but no order (shows the
 *  outcome), red not contacted. Ranges that end before the contact log
 *  went live show a muted dash instead of "Not contacted". */
function ReasonChip({ outcome, kind }: { outcome: ContactOutcome | null; kind: ReasonKind }) {
  if (kind === "placed") {
    return (
      <span className="inline-flex text-[10px] font-semibold bg-emerald-100 text-emerald-800 border border-emerald-300 px-2 py-0.5 rounded-full">
        Placed
      </span>
    );
  }
  if (kind === "contacted") {
    return (
      <span className="inline-flex text-[10px] font-semibold bg-amber-100 text-amber-800 border border-amber-300 px-2 py-0.5 rounded-full">
        {outcome}
      </span>
    );
  }
  if (kind === "unknown") {
    return (
      <span className="text-gray-300" title={`Contact log started on ${CONTACT_LOG_START_DATE} — no data for earlier dates`}>
        —
      </span>
    );
  }
  return (
    <span className="inline-flex text-[10px] font-semibold bg-red-100 text-red-800 border border-red-300 px-2 py-0.5 rounded-full">
      Not contacted
    </span>
  );
}
