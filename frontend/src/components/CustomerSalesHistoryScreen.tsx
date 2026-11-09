import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ChevronDown, ChevronRight, GripVertical, Loader2, Pencil, Plus, Search, X, XCircle } from "lucide-react";
import { api } from "../api/client";
import type { HistoryTransaction, PivotDim, PivotRow } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { formatWeekLabel, getWeekRange, type WeekRange } from "../utils/history";
import { CustomerAssignmentModal } from "./CustomerAssignmentModal";

type Metric = "amount" | "litres";

const WEEKS = 12;
const STALE_DAYS = 14;

type CustomerSalesHistoryProps = {
  onBack?: () => void;
};

const DIM_LABEL: Record<PivotDim, string> = {
  route: "Route",
  salesrep: "Sales Rep",
  telesales: "Telesales",
  customer: "Customer",
  category: "Category",
  stock: "Stock",
};

const DIM_ALL: PivotDim[] = ["route", "salesrep", "telesales", "customer", "category", "stock"];

/** Dims that partition customers — only these produce a meaningful
 *  stale-% column. Category / stock buckets group transactions by item,
 *  not by who-bought-them, so a stale-% there is confusing noise. */
const DIM_HAS_STALE: Record<PivotDim, boolean> = {
  route: true,
  salesrep: true,
  telesales: true,
  customer: true,
  category: false,
  stock: false,
};

const DEFAULT_PATH: PivotDim[] = ["route", "customer", "category", "stock"];

/** Twelve Monday-to-Sunday week ranges, newest first. Matches the Order
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
  if (since >= STALE_DAYS) return "text-amber-700";
  if (since >= 7) return "text-yellow-700";
  return "text-emerald-700";
}

function stalePctClass(pct: number): string {
  if (pct >= 0.75) return "bg-red-100 text-red-800";
  if (pct >= 0.5) return "bg-amber-100 text-amber-800";
  if (pct >= 0.25) return "bg-yellow-100 text-yellow-800";
  if (pct > 0) return "bg-emerald-50 text-emerald-800";
  return "bg-gray-50 text-gray-600";
}

function fmtNum(n: number): string {
  if (!n) return "";
  return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

// ---------------------------------------------------------------------------
// Cache keys + hierarchy helpers
// ---------------------------------------------------------------------------

/** Opaque cache key for a given (path, depth, parent values). Changing
 *  the hierarchy produces fresh keys — old cached entries stay in memory
 *  harmlessly until the Map grows out, which won't happen in a session. */
function cacheKey(path: PivotDim[], depth: number, values: string[]): string {
  return `${path.join(",")}@${depth}|${values.join("|")}`;
}

/** Row id for expand-state and the React key. Deliberately *excludes*
 *  the full path — only the parent-value chain plus the row's own key
 *  matters. That way, appending a new dim at the end of the hierarchy
 *  (or any edit that keeps the prefix path identical to this row's
 *  depth) preserves whatever the user had expanded. A reorder or an
 *  insert earlier in the chain produces different parent values and
 *  therefore different row ids — which is correct, because the chain
 *  through the tree is different. */
function rowIdOf(values: string[], rowKey: string): string {
  return `${values.join("|")}|${rowKey}`;
}

// ---------------------------------------------------------------------------
// Tree flattening
// ---------------------------------------------------------------------------

type VisibleRow = {
  row: PivotRow;
  depth: number;
  rowId: string;
  values: string[];     // parent dim-values that scope this row
};

/** Walk the lazily-loaded cache into a flat list of rendered rows,
 *  respecting the current expand state. Rows whose children haven't
 *  loaded yet just render as a single row — children slot in when the
 *  fetch completes. */
function flattenVisible(
  path: PivotDim[],
  cache: Map<string, PivotRow[]>,
  expanded: Set<string>,
): VisibleRow[] {
  const out: VisibleRow[] = [];
  const walk = (depth: number, values: string[]) => {
    const rows = cache.get(cacheKey(path, depth, values));
    if (!rows) return;
    for (const row of rows) {
      const rowId = rowIdOf(values, row.key);
      out.push({ row, depth, rowId, values });
      if (expanded.has(rowId) && row.has_children && depth < path.length - 1) {
        walk(depth + 1, [...values, row.key]);
      }
    }
  };
  walk(0, []);
  return out;
}

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

type TxModalState = {
  visible: VisibleRow;
  weekStart: string | null;   // null = full window (Total column)
  weekLabel: string | null;   // human-readable week label for the title
};

export function CustomerSalesHistoryScreen({ onBack }: CustomerSalesHistoryProps = {}) {
  const { role } = useAuth();
  // Supervisors + Admins can reassign customers (route / telesales /
  // sales rep) and toggle On_Hold via the Edit modal. Agents can
  // explore + filter the pivot but can't maintain it — the pencil
  // icon, bulk-select tooling and the Edit modal all stay hidden,
  // and the backend 403s those PATCHes anyway as a backstop.
  const canMaintain = role === "SUPERVISOR" || role === "ADMIN";
  const [path, setPath] = useState<PivotDim[]>(DEFAULT_PATH);
  const [cache, setCache] = useState<Map<string, PivotRow[]>>(new Map());
  const [loadingKeys, setLoadingKeys] = useState<Set<string>>(new Set());
  const [errorsByKey, setErrorsByKey] = useState<Map<string, string>>(new Map());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [metric, setMetric] = useState<Metric>("amount");
  const [topLevelError, setTopLevelError] = useState<string | null>(null);
  const [txModal, setTxModal] = useState<TxModalState | null>(null);
  // When set, the Customer Assignment modal opens for this account /
  // these accounts. Length 1 → single-customer edit via the pencil icon;
  // length > 1 → bulk edit from the floating action bar.
  const [editingAccounts, setEditingAccounts] = useState<string[] | null>(null);
  // Set of customer account codes currently selected via checkboxes.
  // Keyed by account alone (independent of hierarchy) so a selection
  // survives drill-ins / collapses / hierarchy reorders.
  const [selectedAccounts, setSelectedAccounts] = useState<Set<string>>(new Set());
  // Text search filter for the visible-rows list. Only filters what's
  // already expanded + loaded — the pivot is lazy, so a route the user
  // hasn't drilled into won't have children in the match pool. The
  // placeholder spells this out so expectations are set.
  const [search, setSearch] = useState("");

  const weekCols = useMemo(() => buildWeekColumnsDesc(), []);

  /** Fetch a level's rows if we don't already have them cached. */
  const ensureLoaded = useCallback(
    async (p: PivotDim[], depth: number, values: string[]) => {
      const key = cacheKey(p, depth, values);
      // Snapshot checks against latest state — the early-out guards a
      // double-click from firing two fetches.
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
        const resp = await api.getCustomerHistoryLevel(p, depth, values, WEEKS);
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

  // Reload the root whenever the hierarchy path changes. We DON'T clear
  // the expand state here: rowIds are keyed by (parent-values, rowKey)
  // only, so edits that keep the prefix-chain intact (e.g. appending a
  // deeper dim) preserve what the user had open. Edits that change the
  // chain produce different rowIds and so are effectively collapsed —
  // old entries stay in the Set but match nothing.
  const pathKey = path.join(",");
  useEffect(() => {
    setTopLevelError(null);
    if (path.length === 0) return;  // nothing to fetch until the user adds a dim
    ensureLoaded(path, 0, []).catch((e) =>
      setTopLevelError(e?.message ?? "Failed to load."),
    );
    // ensureLoaded is stable; pathKey tracks the real dependency without
    // pulling the array reference in as a dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathKey]);

  const toggle = useCallback(
    (visible: VisibleRow) => {
      const { rowId, row, depth, values } = visible;
      const wasExpanded = expanded.has(rowId);
      setExpanded((prev) => {
        const next = new Set(prev);
        if (wasExpanded) next.delete(rowId);
        else next.add(rowId);
        return next;
      });
      if (wasExpanded || !row.has_children || depth >= path.length - 1) return;
      ensureLoaded(path, depth + 1, [...values, row.key]);
    },
    [expanded, ensureLoaded, path],
  );

  const retry = useCallback(
    (depth: number, values: string[]) => {
      const key = cacheKey(path, depth, values);
      setErrorsByKey((prev) => {
        const next = new Map(prev);
        next.delete(key);
        return next;
      });
      ensureLoaded(path, depth, values);
    },
    [ensureLoaded, path],
  );

  const openTx = useCallback(
    (v: VisibleRow, weekStart: string | null) => {
      const weekLabel = weekStart
        ? formatWeekLabel(weekCols.find((w) => w.key === weekStart) ?? weekCols[0])
        : null;
      setTxModal({ visible: v, weekStart, weekLabel });
    },
    [weekCols],
  );

  const openEdit = useCallback((account: string) => {
    setEditingAccounts([account]);
  }, []);

  const toggleSelected = useCallback((account: string) => {
    setSelectedAccounts((prev) => {
      const next = new Set(prev);
      if (next.has(account)) next.delete(account);
      else next.add(account);
      return next;
    });
  }, []);

  const addToSelection = useCallback((accounts: string[]) => {
    setSelectedAccounts((prev) => {
      const next = new Set(prev);
      for (const a of accounts) next.add(a);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelectedAccounts(new Set()), []);

  // After a successful edit, drop any cached level responses that could
  // contain this customer so the next render re-fetches the row with its
  // new label / stale state. Cheapest wholesale invalidation that keeps
  // scroll + expand state intact.
  const invalidateAfterEdit = useCallback(() => {
    setCache(new Map());
    ensureLoaded(path, 0, []).catch((e) =>
      setTopLevelError(e?.message ?? "Failed to reload."),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path.join(","), ensureLoaded]);

  const visibleRows = useMemo(
    () => flattenVisible(path, cache, expanded),
    [path, cache, expanded],
  );

  /** Text-search-filtered subset of `visibleRows`. When a row matches
   *  the search we keep:
   *    • the matching row itself,
   *    • every ancestor (so the depth-indent + hierarchy context
   *      stay coherent), and
   *    • every already-expanded descendant (so a match on "NEL"
   *      shows the customers underneath it, not just the route row).
   *  Descendants aren't fetched / expanded by the search — they only
   *  surface if the user had already drilled into them. */
  const displayedRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return visibleRows;
    const keep = new Set<string>();
    for (let i = 0; i < visibleRows.length; i++) {
      const v = visibleRows[i];
      const hay = (v.row.label + " " + (v.row.sublabel ?? "")).toLowerCase();
      if (!hay.includes(q)) continue;
      keep.add(v.rowId);
      // Ancestors — walk backwards for contiguous strictly-shallower
      // rows, one per depth up to the root.
      let needDepth = v.depth - 1;
      for (let j = i - 1; j >= 0 && needDepth >= 0; j--) {
        if (visibleRows[j].depth === needDepth) {
          keep.add(visibleRows[j].rowId);
          needDepth--;
        }
      }
      // Descendants — walk forwards keeping every row deeper than the
      // match, until we hit a sibling or shallower row (end of subtree).
      for (let j = i + 1; j < visibleRows.length; j++) {
        if (visibleRows[j].depth <= v.depth) break;
        keep.add(visibleRows[j].rowId);
      }
    }
    return visibleRows.filter((v) => keep.has(v.rowId));
  }, [visibleRows, search]);

  /** Return every customer-level account currently visible under
   *  displayedRows[startIdx]. "Under" = contiguous rows whose depth is
   *  greater than the start row's depth, up until a row at ≤ start depth
   *  (end of subtree). Only counts rows whose dim is "customer" — the
   *  selection granularity is always customer. Walks the search-
   *  filtered list so "Select N" counts match what the user actually
   *  sees. */
  const customersUnder = useCallback(
    (startIdx: number): string[] => {
      const start = displayedRows[startIdx];
      if (!start) return [];
      const out: string[] = [];
      for (let i = startIdx + 1; i < displayedRows.length; i++) {
        const v = displayedRows[i];
        if (v.depth <= start.depth) break;
        if (path[v.depth] === "customer") out.push(v.row.key);
      }
      return out;
    },
    [displayedRows, path],
  );

  // When a prefix-compatible edit (e.g. appending Category→Stock below
  // Route→Customer) leaves rows expanded, proactively fetch their
  // children — the expand toggle only fires on click, so without this
  // the user would see their expanded rows render as flat parents until
  // they click each one.
  useEffect(() => {
    if (expanded.size === 0) return;
    for (const v of visibleRows) {
      if (!expanded.has(v.rowId)) continue;
      if (!v.row.has_children || v.depth >= path.length - 1) continue;
      const childValues = [...v.values, v.row.key];
      if (!cache.has(cacheKey(path, v.depth + 1, childValues))) {
        ensureLoaded(path, v.depth + 1, childValues);
      }
    }
  }, [visibleRows, expanded, path, cache, ensureLoaded]);

  const rootKey = cacheKey(path, 0, []);
  const topLoading = loadingKeys.has(rootKey) && !cache.has(rootKey);
  const showStaleColumn = path.some((d) => DIM_HAS_STALE[d]);

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

        <div className="flex-1 min-h-0 flex gap-2">
          <div className="flex-1 min-w-0 flex flex-col gap-2">
            {topLevelError && (
              <div className="p-1 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs flex-shrink-0">
                <XCircle className="w-3 h-3 mr-1 flex-shrink-0" />
                <span>{topLevelError}</span>
              </div>
            )}

            <div className="bg-white rounded border shadow-sm flex-1 min-h-0 flex flex-col overflow-hidden">
          {path.length === 0 ? (
            <div className="h-full flex items-center justify-center text-gray-400 text-sm italic">
              Drag a dimension into the hierarchy on the right to begin.
            </div>
          ) : topLoading ? (
            <div className="h-full flex items-center justify-center text-gray-500 text-sm">
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
              Loading…
            </div>
          ) : displayedRows.length === 0 ? (
            <div className="h-full flex items-center justify-center text-gray-500 text-sm">
              {search ? "No matches in the currently-visible rows." : "No data."}
            </div>
          ) : (
            <div className="flex-1 overflow-auto">
              <table className="w-full text-xs border-collapse">
                <thead className="bg-gray-100 sticky top-0 z-10">
                  <tr className="text-gray-700 border-b border-gray-300">
                    <th className="text-left px-2 py-1.5 font-semibold sticky left-0 bg-gray-100 z-20">
                      {path.map((d) => DIM_LABEL[d]).join(" · ")}
                    </th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">
                      Last delivery
                    </th>
                    {showStaleColumn && (
                      <th
                        className="text-right px-2 py-1.5 font-semibold whitespace-nowrap"
                        title={`Customers with no sales in the last ${STALE_DAYS} days`}
                      >
                        Stale
                      </th>
                    )}
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
                  {displayedRows.map((v, idx) => (
                    <PivotRowView
                      key={v.rowId}
                      visible={v}
                      visibleIdx={idx}
                      path={path}
                      isExpanded={expanded.has(v.rowId)}
                      metric={metric}
                      weekCols={weekCols}
                      cache={cache}
                      loadingKeys={loadingKeys}
                      errorsByKey={errorsByKey}
                      showStaleColumn={showStaleColumn}
                      canMaintain={canMaintain}
                      onToggle={toggle}
                      onRetry={retry}
                      onOpenTx={openTx}
                      onEditCustomer={openEdit}
                      selectedAccounts={selectedAccounts}
                      onToggleSelect={toggleSelected}
                      onSelectSubtree={(startIdx) => addToSelection(customersUnder(startIdx))}
                      customersUnder={customersUnder}
                      colSpan={3 + (showStaleColumn ? 1 : 0) + weekCols.length}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
            </div>
          </div>

          <aside className="w-[102px] flex-shrink-0 flex flex-col gap-2 overflow-y-auto">
            {/* Live search over whatever's currently visible in the
                pivot. Pivot is lazy — expand a route first, then
                search inside it to narrow. */}
            <div className="bg-white rounded border p-2 shadow-sm">
              <label className="block font-semibold text-gray-800 mb-1 text-xs">
                Search
              </label>
              <div className="relative">
                <Search className="w-3 h-3 text-gray-400 absolute left-1.5 top-1/2 -translate-y-1/2 pointer-events-none" />
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Filter…"
                  className="w-full pl-5 pr-5 py-1 text-xs border border-gray-300 rounded focus:border-blue-500 focus:outline-none"
                />
                {search && (
                  <button
                    onClick={() => setSearch("")}
                    className="absolute right-1 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-gray-100 text-gray-500"
                    title="Clear search"
                  >
                    <X className="w-3 h-3" />
                  </button>
                )}
              </div>
              {search && (
                <div className="mt-1 text-[10px] text-gray-500 text-center">
                  {displayedRows.length} match{displayedRows.length === 1 ? "" : "es"}
                </div>
              )}
            </div>

            <div className="bg-white rounded border p-2 shadow-sm">
              <label className="block font-semibold text-gray-800 mb-1 text-xs">Show</label>
              <div className="flex flex-col rounded border border-gray-300 overflow-hidden text-xs">
                {(
                  [
                    { v: "amount", label: "Amount" },
                    { v: "litres", label: "Litres" },
                  ] as const
                ).map((opt, i) => (
                  <button
                    key={opt.v}
                    onClick={() => setMetric(opt.v)}
                    className={`px-2 py-1 ${i > 0 ? "border-t border-gray-300" : ""} ${
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

            <HierarchyChipBar path={path} onChange={setPath} />
          </aside>
        </div>
      </div>

      {txModal && (
        <TransactionsModal
          state={txModal}
          path={path}
          onClose={() => setTxModal(null)}
        />
      )}

      {canMaintain && editingAccounts && editingAccounts.length > 0 && (
        <CustomerAssignmentModal
          accounts={editingAccounts}
          onSaved={() => {
            // Any successful edit (single or bulk) could change which
            // bucket a customer belongs to — wipe the pivot cache so the
            // next render re-fetches with fresh labels / counts, and
            // clear the selection since the "selected customers" may now
            // live under different parents.
            invalidateAfterEdit();
            clearSelection();
          }}
          onClose={() => setEditingAccounts(null)}
        />
      )}

      {canMaintain && selectedAccounts.size > 0 && !editingAccounts && (
        <SelectionActionBar
          count={selectedAccounts.size}
          onClear={clearSelection}
          onEdit={() => setEditingAccounts(Array.from(selectedAccounts))}
        />
      )}
    </div>
  );
}

/** Fixed-position bar that slides up from the bottom when at least one
 *  customer checkbox is ticked. Minimal: count + Clear + Edit. */
function SelectionActionBar({
  count,
  onClear,
  onEdit,
}: {
  count: number;
  onClear: () => void;
  onEdit: () => void;
}) {
  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 bg-slate-900 text-white rounded-full shadow-lg px-3 py-2 flex items-center gap-3 text-xs">
      <span className="font-semibold">
        {count} customer{count === 1 ? "" : "s"} selected
      </span>
      <span className="text-gray-500">·</span>
      <button
        onClick={onClear}
        className="text-gray-300 hover:text-white"
      >
        Clear
      </button>
      <button
        onClick={onEdit}
        className="bg-blue-600 hover:bg-blue-700 text-white font-semibold rounded-full px-3 py-1"
      >
        Edit {count}
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Hierarchy chip bar — drag-and-drop reorder, add from palette, remove with X
// ---------------------------------------------------------------------------

type ChipBarProps = {
  path: PivotDim[];
  onChange: (next: PivotDim[]) => void;
};

function HierarchyChipBar({ path, onChange }: ChipBarProps) {
  const palette = useMemo(
    () => DIM_ALL.filter((d) => !path.includes(d)),
    [path],
  );
  // Live drag state — the ref carries the dragged dim through the native
  // drag events (state updates are async and would miss early
  // dragover ticks).
  const draggingDim = useRef<PivotDim | null>(null);
  // Force a re-render when a drag starts/ends so chips can dim and
  // the drop indicator can appear. The ref is the source of truth for
  // handlers; this flag just triggers re-renders.
  const [, setDragTick] = useState(0);
  const bump = () => setDragTick((n) => n + 1);
  // Insertion index for the drop indicator. 0..path.length.
  const [dropIdx, setDropIdx] = useState<number | null>(null);

  const removeAt = (idx: number) => {
    onChange(path.filter((_, i) => i !== idx));
  };

  const appendDim = (dim: PivotDim) => {
    if (path.includes(dim)) return;
    onChange([...path, dim]);
  };

  const commitDrop = () => {
    const dim = draggingDim.current;
    const idx = dropIdx;
    draggingDim.current = null;
    setDropIdx(null);
    bump();
    if (!dim || idx === null) return;
    const without = path.filter((d) => d !== dim);
    // If moving an existing chip to the right of its own position, idx
    // was computed against the *displayed* array (with the chip still
    // present); subtract 1 so we don't overshoot once it's removed.
    const originalIdx = path.indexOf(dim);
    const effective =
      originalIdx !== -1 && idx > originalIdx ? idx - 1 : idx;
    const insertAt = Math.max(0, Math.min(effective, without.length));
    const next = [
      ...without.slice(0, insertAt),
      dim,
      ...without.slice(insertAt),
    ];
    if (next.join(",") !== path.join(",")) onChange(next);
  };

  const handleDragStart = (dim: PivotDim) => (e: React.DragEvent) => {
    draggingDim.current = dim;
    // Firefox needs setData for a drag to initiate at all.
    e.dataTransfer.setData("text/plain", dim);
    e.dataTransfer.effectAllowed = "move";
    bump();
  };

  const handleDragEnd = () => {
    // Fired even on cancel; clear state so the indicator disappears.
    draggingDim.current = null;
    setDropIdx(null);
    bump();
  };

  /** Over a chip in the vertical stack: split into top/bottom halves by
   *  cursor Y to decide "insert above chip" (dropIdx = idx) vs "insert
   *  below" (idx + 1). stopPropagation keeps the container's dragover
   *  from overwriting dropIdx with path.length (the two handlers would
   *  otherwise race on every pointer move). */
  const handleChipDragOver = (idx: number) => (e: React.DragEvent) => {
    if (!draggingDim.current) return;
    e.preventDefault();
    e.stopPropagation();
    e.dataTransfer.dropEffect = "move";
    const rect = e.currentTarget.getBoundingClientRect();
    const after = e.clientY >= rect.top + rect.height / 2;
    setDropIdx(after ? idx + 1 : idx);
  };

  /** Over the trailing empty space inside the hierarchy bar: default to
   *  "append" (dropIdx = path.length). The chip's dragover will have
   *  already called stopPropagation if the cursor is on a chip, so this
   *  only fires when the cursor is actually in the gap after the last
   *  chip. */
  const handleTailDragOver = (e: React.DragEvent) => {
    if (!draggingDim.current) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDropIdx(path.length);
  };

  const handleChipDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    commitDrop();
  };

  const handleTailDrop = (e: React.DragEvent) => {
    e.preventDefault();
    commitDrop();
  };

  return (
    <div className="bg-white rounded border p-2 shadow-sm">
      <label className="block font-semibold text-gray-800 mb-1 text-xs">
        Hierarchy <span className="font-normal text-gray-400">· drag to reorder</span>
      </label>
      <div
        className={`flex flex-col min-h-[64px] rounded border border-dashed p-1 transition-colors ${
          draggingDim.current ? "border-blue-300 bg-blue-50/40" : "border-gray-200"
        }`}
        onDragOver={handleTailDragOver}
        onDrop={handleTailDrop}
      >
        {path.length === 0 && (
          <span className="px-2 py-4 text-[11px] text-gray-400 italic text-center select-none pointer-events-none">
            Drop a dimension here to start
          </span>
        )}
        {path.map((dim, idx) => (
          <div key={`slot-${idx}`} className="flex flex-col">
            <DropIndicator active={dropIdx === idx} />
            <div
              draggable
              onDragStart={handleDragStart(dim)}
              onDragEnd={handleDragEnd}
              onDragOver={handleChipDragOver(idx)}
              onDrop={handleChipDrop}
              className={`flex items-center gap-1 pl-1.5 pr-1 py-1 my-0.5 rounded border text-xs cursor-move select-none bg-blue-50 border-blue-200 text-blue-900 ${
                draggingDim.current === dim ? "opacity-40" : "hover:bg-blue-100"
              }`}
              title="Drag to reorder"
            >
              <GripVertical className="w-3.5 h-3.5 text-blue-500 flex-shrink-0" />
              <span className="font-semibold leading-tight flex-1 break-words">{DIM_LABEL[dim]}</span>
              <button
                onClick={() => removeAt(idx)}
                className="w-4 h-4 flex items-center justify-center rounded hover:bg-blue-200 text-blue-700 flex-shrink-0"
                title="Remove"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          </div>
        ))}
        <DropIndicator active={dropIdx === path.length} />
        {/* Grow-filler so drops in the empty area below the last chip
            still register as "append". */}
        <div className="flex-1 min-h-[12px]" />
      </div>

      {palette.length > 0 && (
        <div className="mt-1.5 pt-1.5 border-t border-gray-100">
          <div className="text-[10px] text-gray-500 mb-1">Add:</div>
          <div className="flex flex-col gap-1">
            {palette.map((dim) => (
              <button
                key={dim}
                draggable
                onDragStart={handleDragStart(dim)}
                onDragEnd={handleDragEnd}
                onClick={() => appendDim(dim)}
                className={`flex items-center gap-1 pl-1 pr-1.5 py-0.5 rounded border border-dashed border-gray-300 text-[11px] text-gray-700 hover:bg-gray-50 cursor-move text-left leading-tight ${
                  draggingDim.current === dim ? "opacity-40" : ""
                }`}
                title="Drag into the hierarchy, or click to append at the end"
              >
                <Plus className="w-3 h-3 text-gray-400 flex-shrink-0" />
                <span className="break-words flex-1">{DIM_LABEL[dim]}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function DropIndicator({ active }: { active: boolean }) {
  return (
    <div
      className={`transition-all rounded-full ${
        active ? "h-1 bg-blue-500" : "h-0"
      }`}
    />
  );
}

// ---------------------------------------------------------------------------
// Row renderer
// ---------------------------------------------------------------------------

type PivotRowViewProps = {
  visible: VisibleRow;
  /** Index of this row inside the flattened `visibleRows` array — needed
   *  by `onSelectSubtree` so it can walk descendants without re-finding
   *  itself in the array. */
  visibleIdx: number;
  path: PivotDim[];
  isExpanded: boolean;
  metric: Metric;
  weekCols: WeekRange[];
  cache: Map<string, PivotRow[]>;
  loadingKeys: Set<string>;
  errorsByKey: Map<string, string>;
  showStaleColumn: boolean;
  /** Role flag: when false, the row renders in read-only mode —
   *  no checkbox, no "Select N" button, no pencil icon. */
  canMaintain: boolean;
  onToggle: (v: VisibleRow) => void;
  onRetry: (depth: number, values: string[]) => void;
  /** Open the transactions modal for this row's bucket. `weekStart` is
   *  the Monday ISO of the clicked week, or null for the Total column. */
  onOpenTx: (v: VisibleRow, weekStart: string | null) => void;
  /** Open the customer-assignment edit modal. Only rendered for customer
   *  rows; the handler receives the customer account. */
  onEditCustomer: (account: string) => void;
  /** Set of currently-selected customer accounts. The checkbox on each
   *  customer row reads from this. */
  selectedAccounts: Set<string>;
  /** Toggle this specific customer's selection. */
  onToggleSelect: (account: string) => void;
  /** Called from the "Select N" button on an expanded group row. Adds
   *  every customer-level descendant to the selection. */
  onSelectSubtree: (visibleIdx: number) => void;
  /** Count of customer-level descendants under this row — used to size
   *  the "Select N" button label without re-walking the tree twice. */
  customersUnder: (visibleIdx: number) => string[];
  colSpan: number;
};

function PivotRowView({
  visible,
  visibleIdx,
  path,
  isExpanded,
  metric,
  weekCols,
  cache,
  loadingKeys,
  errorsByKey,
  showStaleColumn,
  canMaintain,
  onToggle,
  onRetry,
  onOpenTx,
  onEditCustomer,
  selectedAccounts,
  onToggleSelect,
  onSelectSubtree,
  customersUnder,
  colSpan,
}: PivotRowViewProps) {
  const { row, depth, values } = visible;
  const canExpand = row.has_children && depth < path.length - 1;
  const since = row.last_sale_date ? daysSince(new Date(row.last_sale_date)) : null;
  const lastDate = row.last_sale_date ? new Date(row.last_sale_date) : null;
  const map = metric === "amount" ? "amount" : "litres";
  const total = metric === "amount" ? row.total_amount : row.total_litres;
  const dim = path[depth];
  const stalePct = row.total_count > 0 ? row.stale_count / row.total_count : 0;
  const showStaleCell = DIM_HAS_STALE[dim];
  // "Has any transactions in the window" — used to distinguish an empty
  // cell (never any sale) from a net-zero cell (invoice + credit note
  // that cancel out, which the user still wants to drill into).
  const anyTx = Object.keys(row.weekly).length > 0;

  const rowShade =
    depth === 0
      ? "bg-slate-50 font-semibold"
      : depth === 1
      ? "bg-white font-medium"
      : depth === 2
      ? "bg-white"
      : "bg-white text-gray-700";

  const labelTone =
    dim === "customer" || dim === "stock"
      ? "font-mono text-gray-900"
      : "text-gray-900";

  // Child-fetch state for the spinner / inline error row.
  const childValues = canExpand ? [...values, row.key] : null;
  const childKey = childValues ? cacheKey(path, depth + 1, childValues) : null;
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
            {/* Customer selection checkbox. Only on customer-level rows;
                stopPropagation keeps the row-click (expand) from firing.
                Hidden for AGENTs — they can view but not maintain. */}
            {canMaintain && dim === "customer" && (
              <input
                type="checkbox"
                checked={selectedAccounts.has(row.key)}
                onChange={() => onToggleSelect(row.key)}
                onClick={(e) => e.stopPropagation()}
                className="mt-0.5 w-3.5 h-3.5 flex-shrink-0 accent-blue-600 cursor-pointer"
                title="Select for bulk edit"
              />
            )}
            <div className="min-w-0 flex-1">
              <div className={labelTone}>{row.label}</div>
              {row.sublabel && (
                <div className="text-[9px] text-gray-500 truncate max-w-[14rem]">
                  {row.sublabel}
                </div>
              )}
            </div>
            {/* "Select N below" on expanded group rows that have customer
                descendants visible — covers the "move everyone from this
                bucket" use case. Hidden for AGENTs. */}
            {canMaintain && isExpanded && dim !== "customer" && dim !== "stock" && (() => {
              const below = customersUnder(visibleIdx);
              if (below.length === 0) return null;
              return (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    onSelectSubtree(visibleIdx);
                  }}
                  title={`Select ${below.length} customer${below.length === 1 ? "" : "s"} under this row`}
                  className="flex-shrink-0 px-2 py-0.5 text-[10px] font-semibold bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 rounded whitespace-nowrap"
                >
                  Select {below.length}
                </button>
              );
            })()}
            {canMaintain && dim === "customer" && (
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onEditCustomer(row.key);
                }}
                title="Edit route / telesales / sales rep"
                className="flex-shrink-0 w-5 h-5 flex items-center justify-center rounded hover:bg-blue-100 text-gray-400 hover:text-blue-700"
              >
                <Pencil className="w-3 h-3" />
              </button>
            )}
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
        {showStaleColumn && (
          <td className="px-2 py-1 whitespace-nowrap text-right">
            {!showStaleCell || row.total_count === 0 ? (
              <span className="text-gray-300">·</span>
            ) : dim === "customer" ? (
              // At the customer level stale is a yes/no, not a ratio —
              // render just 1 (red) or 0 (muted) so the column stays
              // scannable.
              <span
                className={`font-mono font-semibold ${
                  row.stale_count > 0 ? "text-red-600" : "text-gray-400"
                }`}
                title={
                  row.stale_count > 0
                    ? `No sales in the last ${STALE_DAYS} days`
                    : `Sold within the last ${STALE_DAYS} days`
                }
              >
                {row.stale_count > 0 ? 1 : 0}
              </span>
            ) : (
              <span
                className={`inline-flex items-baseline gap-1 px-1.5 py-0.5 rounded font-mono ${stalePctClass(stalePct)}`}
                title={`${row.stale_count} of ${row.total_count} customers with no sales in the last ${STALE_DAYS} days`}
              >
                <span className="font-semibold">{Math.round(stalePct * 100)}%</span>
                <span className="text-[9px] opacity-70">{row.stale_count}/{row.total_count}</span>
              </span>
            )}
          </td>
        )}
        <td className="px-2 py-1 text-right font-mono font-semibold text-gray-900">
          {anyTx ? (
            <button
              onClick={(e) => {
                e.stopPropagation();
                onOpenTx(visible, null);
              }}
              className={`font-mono font-semibold hover:underline ${
                total < 0 ? "text-red-600" : "text-gray-900"
              }`}
              title="Show transactions for the full window"
            >
              {total === 0 ? "0" : fmtNum(total)}
            </button>
          ) : (
            <span className="text-gray-300">·</span>
          )}
        </td>
        {weekCols.map((w) => {
          const cell = row.weekly[w.key];
          // A missing entry means the bucket had no activity that week.
          // A present entry with net=0 means invoices + credit notes
          // cancelled out — we want to show 0 (clickable) so the user
          // can still drill in and see what happened.
          if (!cell) {
            return (
              <td
                key={w.key}
                className="px-2 py-1 text-right font-mono text-gray-300"
              >
                ·
              </td>
            );
          }
          const v = cell[map];
          return (
            <td key={w.key} className="px-2 py-1 text-right font-mono">
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenTx(visible, w.key);
                }}
                className={`font-mono hover:underline ${
                  v < 0 ? "text-red-600" : "text-gray-800"
                }`}
                title={`Show transactions for ${w.display}`}
              >
                {v === 0 ? "0" : fmtNum(v)}
              </button>
            </td>
          );
        })}
      </tr>

      {/* Inline "children failed to load" row with a Retry button. */}
      {isExpanded && childError && childValues && (
        <tr className="bg-red-50 border-b border-red-200">
          <td colSpan={colSpan} className="px-2 py-1 text-xs text-red-800">
            <div className="flex items-center gap-2" style={{ paddingLeft: `${(depth + 1) * 14 + 20}px` }}>
              <XCircle className="w-3.5 h-3.5 flex-shrink-0" />
              <span className="flex-1 truncate">{childError}</span>
              <button
                onClick={() => onRetry(depth + 1, childValues)}
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

// ---------------------------------------------------------------------------
// Transactions modal — one invoice / credit-note row per table row,
// scoped to the clicked cell's bucket.
// ---------------------------------------------------------------------------

function fmtMoney(n: number): string {
  return n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function fmtTxDate(iso: string): string {
  const d = new Date(iso);
  return fmtDate(d);
}

/** Colour per raw TrnId value. OInv/Inv are invoice-side (green), Crn is
 *  a credit note (red). SO rows are excluded upstream. */
const TX_ID_CLASS: Record<string, string> = {
  OInv: "bg-emerald-100 text-emerald-800",
  Inv:  "bg-emerald-100 text-emerald-800",
  Crn:  "bg-rose-100 text-rose-800",
};

type TxModalProps = {
  state: TxModalState;
  path: PivotDim[];
  onClose: () => void;
};

function TransactionsModal({ state, path, onClose }: TxModalProps) {
  const { visible, weekStart, weekLabel } = state;
  const [rows, setRows] = useState<HistoryTransaction[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The clicked row's full dim-value chain is [...parent values, own key].
  // We memoise it so the fetch effect doesn't retrigger on every render
  // when the parent re-renders unrelated state.
  const values = useMemo(
    () => [...visible.values, visible.row.key],
    [visible],
  );
  const valuesKey = values.join("|");

  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(null);
    api
      .getCustomerHistoryTransactions(path, values, weekStart ?? undefined)
      .then((r) => {
        if (!cancelled) setRows(r.transactions);
      })
      .catch((e: any) => {
        if (cancelled) return;
        const msg =
          e?.response?.data?.detail ??
          e?.message ??
          "Failed to load transactions.";
        setError(String(msg));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path.join(","), valuesKey, weekStart]);

  // Esc to close.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Totals for a quick sanity check against the grid.
  const totals = useMemo(() => {
    if (!rows) return null;
    return rows.reduce(
      (acc, r) => ({
        quantity: acc.quantity + r.quantity,
        litre: acc.litre + r.litre,
        incl: acc.incl + r.incl_amount,
      }),
      { quantity: 0, litre: 0, incl: 0 },
    );
  }, [rows]);

  // Breadcrumb of dim: value pairs for the title.
  const breadcrumbs = path
    .slice(0, values.length)
    .map((d, i) => ({ dim: DIM_LABEL[d], value: values[i] }));

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white rounded shadow-lg w-full max-w-[1200px] max-h-[85vh] flex flex-col"
      >
        <div className="flex items-start justify-between px-4 py-2 border-b">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-900">
              Transactions
            </div>
            <div className="text-xs text-gray-600 mt-0.5 flex flex-wrap gap-x-2 gap-y-0.5">
              {breadcrumbs.map((b, i) => (
                <span key={i}>
                  <span className="text-gray-500">{b.dim}:</span>{" "}
                  <span className="font-mono">{b.value}</span>
                  {i < breadcrumbs.length - 1 && (
                    <span className="text-gray-400"> ›</span>
                  )}
                </span>
              ))}
              <span className="text-gray-400">·</span>
              <span>{weekLabel ? `Week of ${weekLabel}` : "Full window"}</span>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-gray-100 text-gray-500 flex-shrink-0"
            title="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-auto">
          {error ? (
            <div className="h-32 flex items-center justify-center text-sm text-red-700">
              <XCircle className="w-4 h-4 mr-2" />
              {error}
            </div>
          ) : rows === null ? (
            <div className="h-32 flex items-center justify-center text-sm text-gray-500">
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              Loading…
            </div>
          ) : rows.length === 0 ? (
            <div className="h-32 flex items-center justify-center text-sm text-gray-500">
              No transactions in this period.
            </div>
          ) : (
            <table className="w-full text-xs border-collapse">
              <thead className="bg-gray-100 sticky top-0 z-10">
                <tr className="text-gray-700 border-b border-gray-300">
                  <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Date</th>
                  <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">TrnId</th>
                  <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Order</th>
                  <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Customer</th>
                  <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Category</th>
                  <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Stock</th>
                  <th className="text-right px-2 py-1.5 font-semibold whitespace-nowrap">Qty</th>
                  <th className="text-right px-2 py-1.5 font-semibold whitespace-nowrap">Litres</th>
                  <th className="text-right px-2 py-1.5 font-semibold whitespace-nowrap">Incl Amount</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => {
                  const idCls = TX_ID_CLASS[r.id] ?? "bg-gray-100 text-gray-800";
                  const neg = r.incl_amount < 0;
                  return (
                    <tr key={i} className="border-b border-gray-100">
                      <td className="px-2 py-1 whitespace-nowrap">{fmtTxDate(r.tx_date)}</td>
                      <td className="px-2 py-1 whitespace-nowrap">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold ${idCls}`}>
                          {r.id}
                        </span>
                      </td>
                      <td className="px-2 py-1 whitespace-nowrap font-mono">{r.order_no}</td>
                      <td className="px-2 py-1 whitespace-nowrap">
                        <div className="font-mono">{r.customer_account}</div>
                        {r.customer_name && (
                          <div className="text-[10px] text-gray-500 truncate max-w-[18rem]">
                            {r.customer_name}
                          </div>
                        )}
                      </td>
                      <td className="px-2 py-1 whitespace-nowrap text-gray-700">{r.category ?? ""}</td>
                      <td className="px-2 py-1 whitespace-nowrap">
                        <div className="font-mono">{r.stock_code}</div>
                        {r.stock_desc && (
                          <div className="text-[10px] text-gray-500 truncate max-w-[22rem]">
                            {r.stock_desc}
                          </div>
                        )}
                      </td>
                      <td className={`px-2 py-1 text-right font-mono ${neg ? "text-red-600" : ""}`}>
                        {r.quantity.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </td>
                      <td className={`px-2 py-1 text-right font-mono ${neg ? "text-red-600" : ""}`}>
                        {r.litre.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                      </td>
                      <td className={`px-2 py-1 text-right font-mono ${neg ? "text-red-600" : ""}`}>
                        {fmtMoney(r.incl_amount)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              {totals && (
                <tfoot className="bg-gray-50 border-t border-gray-300 font-semibold">
                  <tr>
                    <td colSpan={6} className="px-2 py-1.5 text-right text-gray-700">
                      Totals ({rows.length} line{rows.length === 1 ? "" : "s"})
                    </td>
                    <td className={`px-2 py-1.5 text-right font-mono ${totals.quantity < 0 ? "text-red-600" : ""}`}>
                      {totals.quantity.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                    <td className={`px-2 py-1.5 text-right font-mono ${totals.litre < 0 ? "text-red-600" : ""}`}>
                      {totals.litre.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                    </td>
                    <td className={`px-2 py-1.5 text-right font-mono ${totals.incl < 0 ? "text-red-600" : ""}`}>
                      {fmtMoney(totals.incl)}
                    </td>
                  </tr>
                </tfoot>
              )}
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
