import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronRight, Loader2, XCircle } from "lucide-react";
import { api } from "../api/client";
import type {
  PivotLevel,
  PivotLevelFilters,
  PivotRow,
} from "../api/types";
import { formatWeekLabel, getWeekRange, type WeekRange } from "../utils/history";

type Metric = "amount" | "litres";

const WEEKS = 12;

type RouteCustomerHistoryProps = {
  onBack?: () => void;
};

/** Twelve Sunday-based week ranges, newest first. Matches the Order
 *  Assistant's week definition so column headers stay consistent across
 *  the app. */
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

// ---------------------------------------------------------------------------
// Cache keys + hierarchy helpers
// ---------------------------------------------------------------------------

/** Opaque key under which a given level+filters response is cached in state.
 *  Collapsing + re-expanding a row doesn't refetch because the cache stays. */
function cacheKey(level: PivotLevel, f: PivotLevelFilters): string {
  switch (level) {
    case "routes":
      return "routes";
    case "customers":
      return `customers|${f.route}`;
    case "categories":
      return `categories|${f.route}|${f.account}`;
    case "stocks":
      return `stocks|${f.route}|${f.account}|${f.category}`;
  }
}

/** Hierarchical id for a rendered row — used for expand-state and the React
 *  key. Collisions are impossible because parent-level values are prepended. */
function rowIdOf(f: PivotLevelFilters, rowKey: string): string {
  return [f.route, f.account, f.category, rowKey].filter(Boolean).join("|");
}

/** Child level + filters for the given parent row. Returns null for stock
 *  rows (leaves). */
function childContext(
  parentLevel: PivotLevel,
  parentFilters: PivotLevelFilters,
  parentRowKey: string,
): { level: PivotLevel; filters: PivotLevelFilters } | null {
  switch (parentLevel) {
    case "routes":
      return { level: "customers", filters: { route: parentRowKey } };
    case "customers":
      return {
        level: "categories",
        filters: { route: parentFilters.route, account: parentRowKey },
      };
    case "categories":
      return {
        level: "stocks",
        filters: {
          route: parentFilters.route,
          account: parentFilters.account,
          category: parentRowKey,
        },
      };
    case "stocks":
      return null;
  }
}

// ---------------------------------------------------------------------------
// Tree flattening
// ---------------------------------------------------------------------------

type VisibleRow = {
  row: PivotRow;
  depth: number;
  rowId: string;
  level: PivotLevel;
  filters: PivotLevelFilters;
};

/** Walk the lazily-loaded cache into a flat list of rendered rows, respecting
 *  the current expand state. Rows whose children haven't loaded yet (either
 *  mid-fetch or never requested) just render as a single row — children slot
 *  in when the fetch completes. */
function flattenVisible(
  cache: Map<string, PivotRow[]>,
  expanded: Set<string>,
): VisibleRow[] {
  const out: VisibleRow[] = [];
  const walk = (
    level: PivotLevel,
    filters: PivotLevelFilters,
    depth: number,
  ) => {
    const rows = cache.get(cacheKey(level, filters));
    if (!rows) return;
    for (const row of rows) {
      const rowId = rowIdOf(filters, row.key);
      out.push({ row, depth, rowId, level, filters });
      if (expanded.has(rowId) && row.has_children) {
        const child = childContext(level, filters, row.key);
        if (child) walk(child.level, child.filters, depth + 1);
      }
    }
  };
  walk("routes", {}, 0);
  return out;
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export function RouteCustomerHistoryScreen({ onBack }: RouteCustomerHistoryProps = {}) {
  const [cache, setCache] = useState<Map<string, PivotRow[]>>(new Map());
  const [loadingKeys, setLoadingKeys] = useState<Set<string>>(new Set());
  const [errorsByKey, setErrorsByKey] = useState<Map<string, string>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [metric, setMetric] = useState<Metric>("amount");
  const [topLevelError, setTopLevelError] = useState<string | null>(null);

  const weekCols = useMemo(() => buildWeekColumnsDesc(), []);

  /** Fetch a level's rows if we don't already have them cached. Writes
   *  loading/error state keyed by cacheKey so the UI can show per-row
   *  spinners and messages. */
  const ensureLoaded = useCallback(
    async (level: PivotLevel, filters: PivotLevelFilters) => {
      const key = cacheKey(level, filters);
      // Snapshot checks against latest state via functional updates below;
      // the early-outs here are a cheap guard against duplicate fetches
      // when the user double-clicks a row.
      let alreadyCached = false;
      let alreadyLoading = false;
      setCache((prev) => {
        alreadyCached = prev.has(key);
        return prev;
      });
      setLoadingKeys((prev) => {
        alreadyLoading = prev.has(key);
        return prev;
      });
      if (alreadyCached || alreadyLoading) return;

      setLoadingKeys((prev) => {
        const next = new Set(prev);
        next.add(key);
        return next;
      });
      setErrorsByKey((prev) => {
        if (!prev.has(key)) return prev;
        const next = new Map(prev);
        next.delete(key);
        return next;
      });

      try {
        const resp = await api.getCustomerHistoryLevel(level, filters, WEEKS);
        setCache((prev) => new Map(prev).set(key, resp.rows));
      } catch (e: any) {
        const msg =
          e?.response?.data?.detail ??
          e?.message ??
          "Failed to load. Try again.";
        setErrorsByKey((prev) => new Map(prev).set(key, String(msg)));
      } finally {
        setLoadingKeys((prev) => {
          const next = new Set(prev);
          next.delete(key);
          return next;
        });
      }
    },
    [],
  );

  // Prime the top-level load.
  useEffect(() => {
    ensureLoaded("routes", {}).catch((e) =>
      setTopLevelError(e?.message ?? "Failed to load routes."),
    );
  }, [ensureLoaded]);

  const toggle = useCallback(
    (visible: VisibleRow) => {
      const { rowId, row, level, filters } = visible;
      const wasExpanded = expanded.has(rowId);
      setExpanded((prev) => {
        const next = new Set(prev);
        if (wasExpanded) next.delete(rowId);
        else next.add(rowId);
        return next;
      });
      if (wasExpanded || !row.has_children) return;
      const child = childContext(level, filters, row.key);
      if (child) ensureLoaded(child.level, child.filters);
    },
    [expanded, ensureLoaded],
  );

  const retry = useCallback(
    (level: PivotLevel, filters: PivotLevelFilters) => {
      const key = cacheKey(level, filters);
      setErrorsByKey((prev) => {
        const next = new Map(prev);
        next.delete(key);
        return next;
      });
      ensureLoaded(level, filters);
    },
    [ensureLoaded],
  );

  const visibleRows = useMemo(
    () => flattenVisible(cache, expanded),
    [cache, expanded],
  );

  const topLoading = loadingKeys.has("routes") && !cache.has("routes");

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
            Click a row to drill in · Route → Customer → Category → Stock
          </div>
        </div>

        {topLevelError && (
          <div className="p-1 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs flex-shrink-0">
            <XCircle className="w-3 h-3 mr-1 flex-shrink-0" />
            <span>{topLevelError}</span>
          </div>
        )}

        <div className="bg-white rounded border shadow-sm flex-1 min-h-0 flex flex-col overflow-hidden">
          {topLoading ? (
            <div className="h-full flex items-center justify-center text-gray-500 text-sm">
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
              Loading routes…
            </div>
          ) : visibleRows.length === 0 ? (
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
                  {visibleRows.map((v) => (
                    <PivotRowView
                      key={v.rowId}
                      visible={v}
                      isExpanded={expanded.has(v.rowId)}
                      metric={metric}
                      weekCols={weekCols}
                      cache={cache}
                      loadingKeys={loadingKeys}
                      errorsByKey={errorsByKey}
                      onToggle={toggle}
                      onRetry={retry}
                      colSpan={3 + weekCols.length}
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

// ---------------------------------------------------------------------------
// Row renderer
// ---------------------------------------------------------------------------

type PivotRowViewProps = {
  visible: VisibleRow;
  isExpanded: boolean;
  metric: Metric;
  weekCols: WeekRange[];
  cache: Map<string, PivotRow[]>;
  loadingKeys: Set<string>;
  errorsByKey: Map<string, string>;
  onToggle: (v: VisibleRow) => void;
  onRetry: (level: PivotLevel, filters: PivotLevelFilters) => void;
  colSpan: number;
};

function PivotRowView({
  visible,
  isExpanded,
  metric,
  weekCols,
  cache,
  loadingKeys,
  errorsByKey,
  onToggle,
  onRetry,
  colSpan,
}: PivotRowViewProps) {
  const { row, depth, level, filters } = visible;
  const canExpand = row.has_children;
  const since = row.last_sale_date ? daysSince(new Date(row.last_sale_date)) : null;
  const lastDate = row.last_sale_date ? new Date(row.last_sale_date) : null;
  const map = metric === "amount" ? "amount" : "litres";
  const total = metric === "amount" ? row.total_amount : row.total_litres;

  const rowShade =
    level === "routes"
      ? "bg-slate-50 font-semibold"
      : level === "customers"
      ? "bg-white font-medium"
      : level === "categories"
      ? "bg-white"
      : "bg-white text-gray-700";

  const labelTone =
    level === "routes"
      ? "text-gray-900"
      : level === "customers"
      ? "font-mono text-gray-900"
      : level === "categories"
      ? "text-gray-800"
      : "font-mono text-gray-800";

  // Child-fetch state for the loading spinner / inline error row.
  const child = canExpand ? childContext(level, filters, row.key) : null;
  const childKey = child ? cacheKey(child.level, child.filters) : null;
  const childLoading = childKey ? loadingKeys.has(childKey) && !cache.has(childKey) : false;
  const childError = childKey ? errorsByKey.get(childKey) ?? null : null;

  return (
    <>
      <tr
        onClick={canExpand ? () => onToggle(visible) : undefined}
        className={`border-b border-gray-100 ${rowShade} ${
          canExpand ? "cursor-pointer hover:bg-blue-50/60" : ""
        }`}
      >
        <td className="px-2 py-1 sticky left-0 bg-inherit">
          <div className="flex items-start gap-1" style={{ paddingLeft: `${depth * 14}px` }}>
            <span className="w-3.5 flex-shrink-0 pt-0.5">
              {canExpand ? (
                childLoading ? (
                  <Loader2 className="w-3.5 h-3.5 text-gray-400 animate-spin" />
                ) : isExpanded ? (
                  <ChevronDown className="w-3.5 h-3.5 text-gray-500" />
                ) : (
                  <ChevronRight className="w-3.5 h-3.5 text-gray-500" />
                )
              ) : null}
            </span>
            <div className="min-w-0">
              <div className={labelTone}>{row.label}</div>
              {row.sublabel && (
                <div className="text-[9px] text-gray-500 truncate max-w-[14rem]">
                  {row.sublabel}
                </div>
              )}
            </div>
          </div>
        </td>
        <td className="px-2 py-1 whitespace-nowrap">
          {lastDate ? (
            <>
              <div className="text-gray-900">{fmtDate(lastDate)}</div>
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
          const cell = row.weekly[w.key];
          const v = cell ? cell[map] : 0;
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

      {/* Inline "children failed to load" row with a Retry button. Keeps
          the error adjacent to the parent so the context is obvious. */}
      {isExpanded && childError && child && (
        <tr className="bg-red-50 border-b border-red-200">
          <td colSpan={colSpan} className="px-2 py-1 text-xs text-red-800">
            <div className="flex items-center gap-2" style={{ paddingLeft: `${(depth + 1) * 14 + 20}px` }}>
              <XCircle className="w-3.5 h-3.5 flex-shrink-0" />
              <span className="flex-1 truncate">{childError}</span>
              <button
                onClick={() => onRetry(child.level, child.filters)}
                className="text-blue-600 hover:text-blue-800 underline"
              >
                Retry
              </button>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
