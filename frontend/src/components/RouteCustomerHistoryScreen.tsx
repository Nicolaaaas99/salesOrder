import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronRight, XCircle } from "lucide-react";
import { api } from "../api/client";
import type {
  CustomerHistoryCustomer,
  CustomerHistorySale,
} from "../api/types";
import { formatWeekLabel, getWeekRange, type WeekRange } from "../utils/history";

type Metric = "amount" | "litres";

const WEEKS = 12;

type RouteCustomerHistoryProps = {
  onBack?: () => void;
};

type NodeLevel = "route" | "customer" | "category" | "stock";

type TreeNode = {
  level: NodeLevel;
  /** Hierarchical key — used for expand-state tracking and React keys.
   *  Format: `route|account|category|stock` with trailing empty segments
   *  dropped. */
  key: string;
  /** Primary display label — route code / account / category / stock code. */
  label: string;
  /** Secondary display line below the label (customer name, stock desc). */
  sublabel?: string;
  weeklyAmount: Record<string, number>;
  weeklyLitres: Record<string, number>;
  totalAmount: number;
  totalLitres: number;
  lastDelivery: Date | null;
  children: TreeNode[];
};

/** Twelve Sunday-based week ranges, newest first. Matches the Order
 *  Assistant's week definition exactly so the labels stay consistent. */
function buildWeekColumnsDesc(): WeekRange[] {
  const weeks: WeekRange[] = [];
  const cursor = new Date(getWeekRange(new Date().toISOString()).start);
  for (let i = 0; i < WEEKS; i++) {
    weeks.push(getWeekRange(cursor.toISOString()));
    cursor.setDate(cursor.getDate() - 7);
  }
  return weeks;
}

const DAY_MS = 24 * 60 * 60 * 1000;

function daysSince(d: Date | null): number | null {
  if (!d) return null;
  const diff = Math.floor((Date.now() - d.getTime()) / DAY_MS);
  return diff < 0 ? 0 : diff;
}

function fmtDate(d: Date): string {
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
}

function staleTextClass(since: number | null): string {
  if (since === null) return "text-red-600";
  if (since >= 28) return "text-red-600";
  if (since >= 14) return "text-amber-700";
  if (since >= 7) return "text-yellow-700";
  return "text-emerald-700";
}

function fmtNum(n: number): string {
  if (!n) return "";
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

/** Build the full tree once per sales/customer payload. Rolls aggregates
 *  up from the leaf (stock) rows so every level has its own totals. */
function buildTree(
  routes: string[],
  customers: CustomerHistoryCustomer[],
  sales: CustomerHistorySale[],
): TreeNode[] {
  // Pre-create a stable route list so zero-sale routes still appear.
  const routeOrder = routes.length > 0 ? routes : [];

  // Keyed buckets at every depth so we can accumulate as we iterate sales.
  const routeMap = new Map<string, TreeNode>();
  const customerMap = new Map<string, TreeNode>();
  const categoryMap = new Map<string, TreeNode>();
  const stockMap = new Map<string, TreeNode>();

  const emptyWeekMap = (): Record<string, number> => ({});

  const makeRoute = (route: string): TreeNode => ({
    level: "route",
    key: route,
    label: route,
    weeklyAmount: emptyWeekMap(),
    weeklyLitres: emptyWeekMap(),
    totalAmount: 0,
    totalLitres: 0,
    lastDelivery: null,
    children: [],
  });

  // Seed routes in the master order so even zero-sale routes appear.
  routeOrder.forEach((r) => {
    if (!routeMap.has(r)) {
      const node = makeRoute(r);
      routeMap.set(r, node);
    }
  });

  // Seed customers so zero-sale customers appear under their route.
  customers.forEach((c) => {
    const route = c.route;
    if (!route) return;
    if (!routeMap.has(route)) routeMap.set(route, makeRoute(route));
    const custKey = `${route}|${c.account}`;
    if (!customerMap.has(custKey)) {
      const node: TreeNode = {
        level: "customer",
        key: custKey,
        label: c.account,
        sublabel: c.name,
        weeklyAmount: emptyWeekMap(),
        weeklyLitres: emptyWeekMap(),
        totalAmount: 0,
        totalLitres: 0,
        lastDelivery: null,
        children: [],
      };
      customerMap.set(custKey, node);
      routeMap.get(route)!.children.push(node);
    }
  });

  // Fold sales into the tree, creating category/stock nodes on first touch
  // and rolling the amounts into every ancestor along the way.
  sales.forEach((s) => {
    const weekKey = getWeekRange(s.week_start).key;
    const custKey = `${s.route}|${s.account}`;
    const catKey = `${custKey}|${s.category}`;
    const stockKey = `${catKey}|${s.stock_code}`;

    // Backfill nodes that might not exist (sale for a customer not in the
    // customer master, or a route with no customer-master entry).
    if (!routeMap.has(s.route)) routeMap.set(s.route, makeRoute(s.route));
    if (!customerMap.has(custKey)) {
      const node: TreeNode = {
        level: "customer",
        key: custKey,
        label: s.account,
        weeklyAmount: emptyWeekMap(),
        weeklyLitres: emptyWeekMap(),
        totalAmount: 0,
        totalLitres: 0,
        lastDelivery: null,
        children: [],
      };
      customerMap.set(custKey, node);
      routeMap.get(s.route)!.children.push(node);
    }
    if (!categoryMap.has(catKey)) {
      const node: TreeNode = {
        level: "category",
        key: catKey,
        label: s.category,
        weeklyAmount: emptyWeekMap(),
        weeklyLitres: emptyWeekMap(),
        totalAmount: 0,
        totalLitres: 0,
        lastDelivery: null,
        children: [],
      };
      categoryMap.set(catKey, node);
      customerMap.get(custKey)!.children.push(node);
    }
    if (!stockMap.has(stockKey)) {
      // stock_desc is "CODE-Description" from the base view — strip the
      // prefix so the sublabel is just the description.
      const descOnly = s.stock_desc.startsWith(s.stock_code + "-")
        ? s.stock_desc.slice(s.stock_code.length + 1)
        : s.stock_desc;
      const node: TreeNode = {
        level: "stock",
        key: stockKey,
        label: s.stock_code,
        sublabel: descOnly,
        weeklyAmount: emptyWeekMap(),
        weeklyLitres: emptyWeekMap(),
        totalAmount: 0,
        totalLitres: 0,
        lastDelivery: null,
        children: [],
      };
      stockMap.set(stockKey, node);
      categoryMap.get(catKey)!.children.push(node);
    }

    const lastSale = s.last_sale_date ? new Date(s.last_sale_date) : null;
    const ancestors = [
      stockMap.get(stockKey)!,
      categoryMap.get(catKey)!,
      customerMap.get(custKey)!,
      routeMap.get(s.route)!,
    ];
    ancestors.forEach((n) => {
      n.weeklyAmount[weekKey] = (n.weeklyAmount[weekKey] || 0) + s.week_amount_incl;
      n.weeklyLitres[weekKey] = (n.weeklyLitres[weekKey] || 0) + s.week_litres;
      n.totalAmount += s.week_amount_incl;
      n.totalLitres += s.week_litres;
      if (lastSale && (!n.lastDelivery || lastSale > n.lastDelivery)) {
        n.lastDelivery = lastSale;
      }
    });
  });

  // Return routes in the master order.
  const seen = new Set<string>();
  const out: TreeNode[] = [];
  routeOrder.forEach((r) => {
    if (routeMap.has(r)) {
      out.push(routeMap.get(r)!);
      seen.add(r);
    }
  });
  routeMap.forEach((node, r) => {
    if (!seen.has(r)) out.push(node);
  });

  // Within each parent, sort children: customers by stalest last-delivery
  // first (same signal as before); categories/stocks by total desc so the
  // biggest-selling ones surface at the top.
  const sortChildren = (nodes: TreeNode[], level: NodeLevel) => {
    nodes.forEach((n) => sortChildren(n.children, levelBelow(level)));
    if (level === "route") {
      nodes.sort((a, b) => {
        if (!a.lastDelivery && !b.lastDelivery) return a.label.localeCompare(b.label);
        if (!a.lastDelivery) return -1;
        if (!b.lastDelivery) return 1;
        return a.lastDelivery.getTime() - b.lastDelivery.getTime();
      });
    } else if (level === "customer" || level === "category") {
      nodes.sort((a, b) => b.totalAmount - a.totalAmount);
    }
  };
  const levelBelow = (l: NodeLevel): NodeLevel =>
    l === "route" ? "customer" : l === "customer" ? "category" : l === "category" ? "stock" : "stock";
  sortChildren(out, "route");

  // Routes themselves: biggest first (so the busy ones are on top).
  out.sort((a, b) => b.totalAmount - a.totalAmount);

  return out;
}

/** Flatten the tree into the visible row list, respecting expand state.
 *  Each visible row carries its depth so the UI can indent it. */
function flattenVisible(
  tree: TreeNode[],
  expanded: Set<string>,
): { node: TreeNode; depth: number }[] {
  const rows: { node: TreeNode; depth: number }[] = [];
  const walk = (nodes: TreeNode[], depth: number) => {
    nodes.forEach((n) => {
      rows.push({ node: n, depth });
      if (expanded.has(n.key) && n.children.length > 0) {
        walk(n.children, depth + 1);
      }
    });
  };
  walk(tree, 0);
  return rows;
}

export function RouteCustomerHistoryScreen({ onBack }: RouteCustomerHistoryProps = {}) {
  const [data, setData] = useState<{
    routes: string[];
    customers: CustomerHistoryCustomer[];
    sales: CustomerHistorySale[];
  } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [metric, setMetric] = useState<Metric>("amount");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const weekCols = useMemo(() => buildWeekColumnsDesc(), []);

  useEffect(() => {
    (async () => {
      setIsLoading(true);
      setError(null);
      try {
        const resp = await api.getCustomerHistory(WEEKS);
        setData(resp);
      } catch (e: any) {
        setError(`Failed to load customer history: ${e?.message ?? e}`);
      } finally {
        setIsLoading(false);
      }
    })();
  }, []);

  const tree = useMemo(
    () => (data ? buildTree(data.routes, data.customers, data.sales) : []),
    [data],
  );
  const rows = useMemo(() => flattenVisible(tree, expanded), [tree, expanded]);

  const toggle = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="h-full bg-gray-50 p-2 flex flex-col">
      <div className="max-w-[1600px] mx-auto w-full flex-1 min-h-0 flex flex-col gap-2">
        {onBack && (
          <div className="flex items-center flex-shrink-0">
            <button
              onClick={onBack}
              className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              Back to Analytics
            </button>
          </div>
        )}

        {/* Top bar: metric toggle only (no route selector any more — the
            tree starts at All Routes). */}
        <div className="flex items-end gap-2 flex-shrink-0">
          <div className="bg-white rounded border p-2 shadow-sm">
            <label className="block font-semibold text-gray-800 mb-1 text-xs">Show</label>
            <div className="inline-flex rounded border border-gray-300 overflow-hidden text-xs">
              {(
                [
                  { v: "amount", label: "Amount (incl)" },
                  { v: "litres", label: "Litres" },
                ] as const
              ).map((opt, i) => (
                <button
                  key={opt.v}
                  onClick={() => setMetric(opt.v)}
                  className={`px-3 py-1 ${i > 0 ? "border-l border-gray-300" : ""} ${
                    metric === opt.v
                      ? "bg-blue-600 text-white font-semibold"
                      : "bg-white text-gray-700 hover:bg-gray-50"
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <div className="text-xs text-gray-500 pb-1">
            Click a row to expand · Route → Customer → Category → Stock
          </div>
        </div>

        {error && (
          <div className="p-1 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs flex-shrink-0">
            <XCircle className="w-3 h-3 mr-1 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="bg-white rounded border shadow-sm flex-1 min-h-0 flex flex-col overflow-hidden">
          {isLoading ? (
            <div className="h-full flex items-center justify-center text-gray-500 text-sm">
              Loading…
            </div>
          ) : rows.length === 0 ? (
            <div className="h-full flex items-center justify-center text-gray-500 text-sm">
              No data.
            </div>
          ) : (
            <div className="flex-1 overflow-auto">
              <table className="w-full text-xs border-collapse">
                <thead className="bg-gray-100 sticky top-0 z-10">
                  <tr className="text-gray-700 border-b border-gray-300">
                    <th className="text-left px-2 py-1.5 font-semibold sticky left-0 bg-gray-100 z-20">
                      Route · Customer · Category · Stock
                    </th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">
                      Last delivery
                    </th>
                    <th className="text-right px-2 py-1.5 font-semibold whitespace-nowrap">
                      Total
                    </th>
                    {weekCols.map((w) => (
                      <th
                        key={w.key}
                        className="text-right px-2 py-1.5 font-semibold whitespace-nowrap"
                        title={w.display}
                      >
                        {formatWeekLabel(w)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map(({ node, depth }) => (
                    <PivotRow
                      key={node.key}
                      node={node}
                      depth={depth}
                      metric={metric}
                      weekCols={weekCols}
                      isExpanded={expanded.has(node.key)}
                      onToggle={() => toggle(node.key)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function PivotRow({
  node,
  depth,
  metric,
  weekCols,
  isExpanded,
  onToggle,
}: {
  node: TreeNode;
  depth: number;
  metric: Metric;
  weekCols: WeekRange[];
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const canExpand = node.children.length > 0;
  const since = daysSince(node.lastDelivery);
  const map = metric === "amount" ? node.weeklyAmount : node.weeklyLitres;
  const total = metric === "amount" ? node.totalAmount : node.totalLitres;

  const rowShade =
    node.level === "route"
      ? "bg-slate-50 font-semibold"
      : node.level === "customer"
      ? "bg-white font-medium"
      : node.level === "category"
      ? "bg-white"
      : "bg-white text-gray-700";

  const labelTone =
    node.level === "route"
      ? "text-gray-900"
      : node.level === "customer"
      ? "font-mono text-gray-900"
      : node.level === "category"
      ? "text-gray-800"
      : "font-mono text-gray-800";

  return (
    <tr
      onClick={canExpand ? onToggle : undefined}
      className={`border-b border-gray-100 ${rowShade} ${
        canExpand ? "cursor-pointer hover:bg-blue-50/60" : ""
      }`}
    >
      <td className="px-2 py-1 sticky left-0 bg-inherit">
        <div className="flex items-start gap-1" style={{ paddingLeft: `${depth * 14}px` }}>
          <span className="w-3.5 flex-shrink-0 pt-0.5">
            {canExpand ? (
              isExpanded ? (
                <ChevronDown className="w-3.5 h-3.5 text-gray-500" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5 text-gray-500" />
              )
            ) : null}
          </span>
          <div className="min-w-0">
            <div className={labelTone}>{node.label}</div>
            {node.sublabel && (
              <div className="text-[9px] text-gray-500 truncate max-w-[14rem]">
                {node.sublabel}
              </div>
            )}
          </div>
        </div>
      </td>
      <td className="px-2 py-1 whitespace-nowrap">
        {node.lastDelivery ? (
          <>
            <div className="text-gray-900">{fmtDate(node.lastDelivery)}</div>
            <div className={`text-[9px] ${staleTextClass(since)}`}>{(since ?? 0)}d</div>
          </>
        ) : (
          <div className="text-red-600 font-semibold">Never</div>
        )}
      </td>
      <td className="px-2 py-1 text-right font-mono font-semibold text-gray-900">
        {total === 0 ? "·" : fmtNum(total)}
      </td>
      {weekCols.map((w) => {
        const v = map[w.key] || 0;
        return (
          <td
            key={w.key}
            className={`px-2 py-1 text-right font-mono ${
              v === 0 ? "text-gray-300" : v < 0 ? "text-red-600" : "text-gray-800"
            }`}
          >
            {v === 0 ? "·" : fmtNum(v)}
          </td>
        );
      })}
    </tr>
  );
}
