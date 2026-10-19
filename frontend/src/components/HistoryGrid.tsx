import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  Search,
  ChevronDown,
  ChevronRight,
  FileText,
  Loader2,
  Grid,
  AlertTriangle,
  Plus,
} from "lucide-react";
import type { HistoricalOrder, OrderSuggestion } from "../api/types";
import {
  aggregateOrders,
  detectProblemItems,
  formatQty,
  formatWeekLabel,
  getProblemSeverity,
  productKey,
  type CategoryData,
  type CategoryProduct,
  type ProblemInfo,
  type WeekRange,
} from "../utils/history";

type Props = {
  orders: HistoricalOrder[];
  isLoading?: boolean;
  emptyMessage?: string;
  /** If provided, adds a "Sug" + "This Order" column and shows Add buttons where a suggestion exists. */
  suggestions?: Map<string, OrderSuggestion>;
  /** Map of stock_code → price source ('Customer Deal' | 'Group Deal' | 'Price List' | 'None').
   *  When present, products with an active deal get a visible badge next to their name. */
  priceSources?: Map<string, string>;
  /** Called when user clicks Add on a product row. */
  onAddToOrder?: (
    product: CategoryProduct,
    category: string,
    suggestion: OrderSuggestion | undefined
  ) => void;
  /** Called when user clicks the "Add all suggested" bulk action. */
  onAddAllSuggested?: () => void;
  /** External height override; defaults to full-viewport minus 240px. */
  heightStyle?: string;
  /** stock_code → draft order qty. Shown in the "This Order" column and used
   *  to drop the problem-row highlight once a qty is picked (the "falls off
   *  the list" visual). */
  draftQuantities?: Map<string, number>;
  /** Imperative "set the picked row" trigger. When `bumpKey` changes, the
   *  grid marks `code` as the picked row — the row gets the blue highlight
   *  the moment the user opens its category. Does NOT auto-expand. */
  focusStockCode?: { code: string; bumpKey: number };
  /** Imperative "scroll a category header into view" trigger. When `bumpKey`
   *  changes, the matching header is scrolled into view. Doesn't expand it. */
  focusCategory?: { name: string; bumpKey: number };
  /** Controlled set of category names that should carry the blue "needs
   *  attention" highlight. Driven by the parent — the picker-block adds the
   *  blocked item's category, and Continue-to-order adds every unreviewed
   *  one. Entries drop automatically from the parent via `onCategoryOpened`
   *  as the rep opens each. */
  pickedCategories?: Set<string> | null;
  /** Fires whenever a category is OPENED (not closed) via its toggle. The
   *  parent uses this both to track "opened at least once in this session"
   *  for the Continue gate and to drop the entry from `pickedCategories`. */
  onCategoryOpened?: (category: string) => void;
};

export function HistoryGrid({
  orders,
  isLoading = false,
  emptyMessage = "No data",
  suggestions,
  priceSources,
  onAddToOrder,
  onAddAllSuggested,
  heightStyle = "calc(100vh - 240px)",
  draftQuantities,
  focusStockCode,
  focusCategory,
  pickedCategories,
  onCategoryOpened,
}: Props) {
  const [searchTerm, setSearchTerm] = useState("");
  // Accordion: only one category open at a time. `null` = all collapsed.
  const [expandedCategory, setExpandedCategory] = useState<string | null>(null);

  // Collapse everything whenever a fresh dataset arrives (new route / customer).
  useEffect(() => {
    setExpandedCategory(null);
    setSearchTerm("");
  }, [orders]);

  const { categories, weeks } = useMemo(() => aggregateOrders(orders), [orders]);
  const problemItems = useMemo(
    () => detectProblemItems(categories, weeks),
    [categories, weeks]
  );

  const filteredCategories = useMemo<[string, CategoryData][]>(() => {
    if (!searchTerm.trim()) return Object.entries(categories);

    const searchLower = searchTerm.toLowerCase().trim();
    const filtered: Record<string, CategoryData> = {};

    Object.entries(categories).forEach(([category, categoryData]) => {
      const matching = categoryData.products.filter(
        (product) =>
          product.stockCode.toLowerCase().includes(searchLower) ||
          product.description.toLowerCase().includes(searchLower) ||
          category.toLowerCase().includes(searchLower)
      );

      if (matching.length > 0) {
        let totalQuantity = 0;
        const wk: Record<string, { totalQuantity: number }> = {};
        matching.forEach((product) => {
          totalQuantity += product.totalQuantity;
          Object.entries(product.weeks).forEach(([weekKey, weekQty]) => {
            if (!wk[weekKey]) wk[weekKey] = { totalQuantity: 0 };
            wk[weekKey].totalQuantity += weekQty;
          });
        });
        filtered[category] = {
          ...categoryData,
          totalQuantity,
          weeks: wk,
          products: matching,
        };
      }
    });

    return Object.entries(filtered).sort(
      ([, a], [, b]) => b.totalQuantity - a.totalQuantity
    );
  }, [categories, searchTerm]);

  const toggleCategory = (category: string) => {
    setExpandedCategory((prev) => {
      const next = prev === category ? null : category;
      // Only fire on OPEN — collapses don't count towards "reviewed".
      if (next) onCategoryOpened?.(next);
      return next;
    });
  };

  const scrollRootRef = useRef<HTMLDivElement>(null);
  // "Last picked from the item picker" row marker. The row only visibly
  // highlights once the user opens its category — we don't auto-expand.
  // Cleared when the item leaves the draft, when the rep actions another
  // row, or when they click elsewhere in the grid.
  const [pickedCode, setPickedCode] = useState<string | null>(null);

  useEffect(() => {
    if (!focusStockCode?.code) return;
    setPickedCode(focusStockCode.code);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusStockCode?.bumpKey]);

  // Clear the marker as soon as the item is gone from the draft.
  useEffect(() => {
    if (pickedCode && !draftQuantities?.has(pickedCode)) {
      setPickedCode(null);
    }
  }, [draftQuantities, pickedCode]);

  // Any click inside the grid that doesn't land on the picked row's cells
  // clears the marker — covers category toggles, + buttons on other rows,
  // week cells, and empty space inside the scroll area. Fires on mousedown
  // so the clear happens before any subsequent + onClick that re-sets it.
  function handleGridMouseDown(e: React.MouseEvent<HTMLDivElement>) {
    if (!pickedCode) return;
    const target = e.target as HTMLElement;
    const onPickedRow = !!target.closest(
      `tr[data-stock-code="${CSS.escape(pickedCode)}"]`,
    );
    if (!onPickedRow) setPickedCode(null);
  }

  // When the category is open and we have a picked row, keep it in view.
  useLayoutEffect(() => {
    if (!pickedCode) return;
    const row = scrollRootRef.current?.querySelector<HTMLTableRowElement>(
      `tr[data-stock-code="${CSS.escape(pickedCode)}"]`,
    );
    row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [pickedCode, expandedCategory]);

  // Scroll the parent-requested category header into view (no expand).
  useEffect(() => {
    if (!focusCategory?.name) return;
    const row = scrollRootRef.current?.querySelector<HTMLTableRowElement>(
      `tr[data-category="${CSS.escape(focusCategory.name)}"]`,
    );
    row?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusCategory?.bumpKey]);

  const showSuggestCols = !!suggestions;
  const showAddButton = !!onAddToOrder;
  const stickyLeftWidth = showSuggestCols ? "230px" : "210px";
  const weekColWidth = 95;

  // Weeks are stored chronologically (oldest → newest) so problem detection
  // can slice recent/older windows correctly, but we RENDER most-recent-first
  // so the rep sees the latest activity on the left.
  const displayWeeks = [...weeks].reverse();

  return (
    <div
      className="bg-white rounded border shadow-sm overflow-hidden flex flex-col relative z-10"
      style={{ height: heightStyle }}
    >
      <div className="bg-white border-b border-gray-200 px-2 py-1 shadow-sm flex-shrink-0 sticky top-0 z-20">
        <div className="flex justify-between items-center gap-2">
          <div className="flex items-center space-x-1">
            <Grid className="w-3 h-3 text-blue-600" />
            <h2 className="font-semibold text-gray-900 text-sm">
              Category Sales
            </h2>
            {orders.length > 0 && (
              <span className="text-xs text-gray-500">
                ({filteredCategories.length} categories)
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            {onAddAllSuggested && suggestions && suggestions.size > 0 && (
              <button
                onClick={onAddAllSuggested}
                className="flex items-center gap-1 px-2 py-1 bg-blue-600 text-white text-xs font-semibold rounded hover:bg-blue-700"
              >
                <Plus className="w-3 h-3" />
                Add all suggested ({suggestions.size})
              </button>
            )}

            {orders.length > 0 && (
              <div className="relative w-56">
                <Search className="w-3 h-3 absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  placeholder="Search categories or products..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="w-full pl-7 pr-2 py-1 border border-gray-300 rounded text-xs focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
                />
              </div>
            )}
          </div>
        </div>
      </div>

      <div
        ref={scrollRootRef}
        onMouseDown={handleGridMouseDown}
        className="flex-1 overflow-auto"
      >
        {isLoading && (
          <div className="h-full flex flex-col items-center justify-center text-gray-500 p-4">
            <Loader2 className="w-6 h-6 animate-spin text-blue-600 mb-1" />
            <p className="text-xs">Loading data...</p>
          </div>
        )}

        {!isLoading && orders.length === 0 && (
          <div className="h-full flex flex-col items-center justify-center text-gray-500 p-4">
            <FileText className="w-6 h-6 mb-1 text-gray-400" />
            <p className="text-sm">{emptyMessage}</p>
          </div>
        )}

        {!isLoading && filteredCategories.length > 0 && (
          <div className="p-1">
            <table className="w-full text-xs border-collapse" style={{ tableLayout: "fixed" }}>
              <thead>
                <tr className="bg-gray-100 border-b border-gray-300 sticky top-0 z-20">
                  <th
                    className="px-2 py-1 text-left font-semibold text-gray-700 text-sm sticky left-0 bg-gray-100 z-40 border-r border-gray-200"
                    style={{ width: stickyLeftWidth }}
                  >
                    Category / Product
                  </th>
                  {showSuggestCols && (
                    <>
                      <th
                        className="px-1 py-1 text-center font-semibold text-gray-700 text-xs border-l border-gray-300 whitespace-nowrap"
                        style={{ width: 55 }}
                      >
                        Sug
                      </th>
                      <th
                        className="px-1 py-1 text-center font-semibold text-gray-700 text-xs border-l border-gray-300 whitespace-nowrap"
                        style={{ width: 60 }}
                        title="Quantity currently on the draft order"
                      >
                        This Order
                      </th>
                    </>
                  )}
                  {displayWeeks.map((week) => (
                    <th
                      key={week.key}
                      className="px-1 py-1 text-center font-semibold text-gray-700 border-l border-gray-300 text-xs sticky top-0 bg-gray-100 z-30"
                      style={{ width: weekColWidth, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={formatWeekLabel(week)}
                    >
                      {formatWeekLabel(week)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filteredCategories.map(([category, categoryData]) => (
                  <CategoryRow
                    key={category}
                    category={category}
                    weeks={displayWeeks}
                    categoryData={categoryData}
                    isExpanded={expandedCategory === category}
                    onToggle={toggleCategory}
                    problemItems={problemItems}
                    suggestions={suggestions}
                    priceSources={priceSources}
                    onAddToOrder={onAddToOrder}
                    showAddButton={showAddButton}
                    stickyLeftWidth={stickyLeftWidth}
                    draftQuantities={draftQuantities}
                    pickedCode={pickedCode}
                    isPickedCategory={!!pickedCategories?.has(category)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!isLoading &&
          filteredCategories.length === 0 &&
          Object.keys(categories).length > 0 && (
            <div className="h-full flex flex-col items-center justify-center text-gray-500 p-4">
              <Search className="w-6 h-6 mb-1 text-gray-400" />
              <p className="text-xs">
                No categories or products match your search
              </p>
            </div>
          )}
      </div>
    </div>
  );
}

type CategoryRowProps = {
  category: string;
  weeks: WeekRange[];
  categoryData: CategoryData;
  isExpanded: boolean;
  onToggle: (category: string) => void;
  problemItems: Map<string, ProblemInfo>;
  suggestions?: Map<string, OrderSuggestion>;
  priceSources?: Map<string, string>;
  onAddToOrder?: Props["onAddToOrder"];
  showAddButton: boolean;
  stickyLeftWidth: string;
  draftQuantities?: Map<string, number>;
  /** Stock code last added via the item picker — gets a persistent outline. */
  pickedCode: string | null;
  /** This category is in the parent's `pickedCategories` set and should
   *  carry the persistent blue "needs attention" highlight. */
  isPickedCategory: boolean;
};

function CategoryRow({
  category,
  weeks,
  categoryData,
  isExpanded,
  onToggle,
  problemItems,
  suggestions,
  priceSources,
  onAddToOrder,
  showAddButton,
  stickyLeftWidth,
  draftQuantities,
  pickedCode,
  isPickedCategory,
}: CategoryRowProps) {
  const sortedProducts = useMemo(() => {
    return [...categoryData.products].sort((a, b) => {
      // Suggested items float to the top when suggestions present
      if (suggestions) {
        const asug = suggestions.get(productKey(a.stockCode, a.description));
        const bsug = suggestions.get(productKey(b.stockCode, b.description));
        if (asug && !bsug) return -1;
        if (!asug && bsug) return 1;
      }

      const ap = problemItems.get(productKey(a.stockCode, a.description));
      const bp = problemItems.get(productKey(b.stockCode, b.description));
      if (ap && bp) return bp.weeksSinceLastPurchase - ap.weeksSinceLastPurchase;
      if (ap && !bp) return -1;
      if (!ap && bp) return 1;

      return b.totalQuantity - a.totalQuantity;
    });
  }, [categoryData.products, problemItems, suggestions]);

  const suggestedCount = suggestions
    ? categoryData.products.filter((p) =>
        suggestions.has(productKey(p.stockCode, p.description))
      ).length
    : 0;

  const showSuggestCols = !!suggestions;

  return (
    <>
      <tr
        data-category={category}
        className={
          "border-b border-gray-200 bg-gray-50 hover:bg-gray-100" +
          (isPickedCategory ? " history-category-picked" : "")
        }
      >
        <td
          className="px-2 py-1 font-semibold text-gray-900 text-sm sticky left-0 bg-gray-50 z-10 border-r border-gray-200"
          style={{ width: stickyLeftWidth }}
        >
          <button
            onClick={() => onToggle(category)}
            className="flex items-center space-x-1 w-full text-left"
          >
            {isExpanded ? (
              <ChevronDown className="w-3 h-3 text-blue-600" />
            ) : (
              <ChevronRight className="w-3 h-3 text-gray-500" />
            )}
            <span className="text-sm">{category}</span>
            <span className="text-xs text-gray-500 bg-gray-200 px-1 py-0.5 rounded">
              {categoryData.products.length}
            </span>
            {suggestedCount > 0 && (
              <span className="text-xs bg-blue-100 text-blue-700 px-1 py-0.5 rounded">
                {suggestedCount} sug
              </span>
            )}
            {categoryData.products.some((p) =>
              problemItems.has(productKey(p.stockCode, p.description))
            ) && <AlertTriangle className="w-3 h-3 text-yellow-500" />}
          </button>
        </td>
        {showSuggestCols && (
          <>
            <td className="px-1 py-1 text-center border-l border-gray-200"></td>
            <td className="px-1 py-1 text-center border-l border-gray-200"></td>
          </>
        )}
        {weeks.map((week) => {
          const weekData = categoryData.weeks[week.key] || { totalQuantity: 0 };
          return (
            <td
              key={week.key}
              className="px-1 py-1 text-center text-sm font-semibold text-gray-900 border-l border-gray-200"
            >
              {formatQty(weekData.totalQuantity)}
            </td>
          );
        })}
      </tr>

      {isExpanded &&
        sortedProducts.map((product, index) => {
          const key = productKey(product.stockCode, product.description);
          const problemData = problemItems.get(key);
          const suggestion = suggestions?.get(key);
          const isProblem = !!problemData;
          const weeksSince = problemData?.weeksSinceLastPurchase;
          const severity = problemData
            ? getProblemSeverity(problemData.weeksSinceLastPurchase)
            : null;
          const problemRowBg =
            severity === "red"
              ? "bg-red-50 border-l-4 border-l-red-500"
              : severity === "orange"
              ? "bg-orange-50 border-l-4 border-l-orange-400"
              : "bg-yellow-50 border-l-4 border-l-yellow-400";
          const problemIconClass =
            severity === "red"
              ? "text-red-600"
              : severity === "orange"
              ? "text-orange-500"
              : "text-yellow-500";
          const problemBadgeClass =
            severity === "red"
              ? "bg-red-100 text-red-700"
              : severity === "orange"
              ? "bg-orange-100 text-orange-700"
              : "bg-yellow-100 text-yellow-700";

          const priceSource = priceSources?.get(product.stockCode);
          const dealBadge =
            priceSource === "Customer Deal"
              ? { label: "Cust Deal", cls: "bg-emerald-100 text-emerald-700" }
              : priceSource === "Group Deal"
              ? { label: "Grp Deal", cls: "bg-sky-100 text-sky-700" }
              : null;

          const drafted = draftQuantities?.get(product.stockCode) ?? 0;
          const isDrafted = drafted > 0;
          const isPicked = pickedCode === product.stockCode;

          // Blue is reserved for the picked row (see .history-row-picked).
          // Suggested rows still surface via the Sug column and the + button
          // on the stock code, so they don't need a row-level highlight.
          // Problem rows keep their orange/red/yellow until a qty lands on
          // the draft, at which point they "fall off" to white — the
          // weeks-since badge stays as a reminder of the original state.
          const rowBg = isDrafted
            ? "bg-white"
            : isProblem
            ? problemRowBg
            : "bg-white";

          return (
            <tr
              key={index}
              data-stock-code={product.stockCode}
              className={
                `border-b border-gray-100 hover:bg-blue-50 ${rowBg}` +
                (isPicked ? " history-row-picked" : "")
              }
            >
              <td
                className="px-2 py-1 pl-6 text-gray-700 sticky left-0 bg-inherit z-10 border-r border-gray-200"
                style={{ width: stickyLeftWidth }}
              >
                <div className="flex items-center gap-1">
                  {showAddButton && suggestion && onAddToOrder && (
                    <button
                      onClick={() =>
                        onAddToOrder(product, category, suggestion)
                      }
                      title={`Add ${suggestion.suggested_quantity} to order`}
                      className="flex-shrink-0 p-0.5 bg-blue-600 hover:bg-blue-700 text-white rounded"
                    >
                      <Plus className="w-3 h-3" />
                    </button>
                  )}
                  {showAddButton && !suggestion && onAddToOrder && (
                    <button
                      onClick={() =>
                        onAddToOrder(product, category, undefined)
                      }
                      title="Add to order"
                      className="flex-shrink-0 p-0.5 border border-gray-300 hover:border-blue-500 hover:text-blue-600 text-gray-400 rounded"
                    >
                      <Plus className="w-3 h-3" />
                    </button>
                  )}
                  {isProblem && (
                    <div className="flex items-center gap-0.5">
                      <AlertTriangle className={`w-3 h-3 flex-shrink-0 ${problemIconClass}`} />
                      {weeksSince !== undefined && (
                        <span className={`text-xs px-1 py-0.5 rounded ${problemBadgeClass}`}>
                          {weeksSince}w
                        </span>
                      )}
                    </div>
                  )}
                  <div
                    className="min-w-0"
                    title={`${product.stockCode} — ${product.description}${
                      priceSource ? ` · ${priceSource}` : ""
                    }`}
                  >
                    <div className="font-medium text-sm leading-tight truncate flex items-center gap-1">
                      <span className="truncate">{product.stockCode}</span>
                      {dealBadge && (
                        <span
                          className={`text-[9px] font-bold uppercase px-1 py-0.5 rounded whitespace-nowrap ${dealBadge.cls}`}
                        >
                          {dealBadge.label}
                        </span>
                      )}
                    </div>
                    <div className="text-gray-500 text-xs leading-tight truncate">
                      {product.description}
                    </div>
                  </div>
                </div>
              </td>
              {showSuggestCols && (
                <>
                  <td className="px-1 py-1 text-center border-l border-gray-100">
                    {suggestion ? (
                      <>
                        <div className="font-mono text-sm font-bold text-blue-700">
                          {formatQty(suggestion.suggested_quantity)}
                        </div>
                        {/* Only surface the PENDING subtraction. Current-week
                            invoiced qty is also subtracted in the math, but
                            it's already visible in the current-week cell, so
                            a redundant subscript here would just clutter. */}
                        {suggestion.on_order_qty > 0 && (
                          <div
                            className="font-mono text-[10px] text-amber-700 leading-tight"
                            title={`${formatQty(
                              suggestion.on_order_qty
                            )} on open sales order — subtracted from the suggestion`}
                          >
                            −{formatQty(suggestion.on_order_qty)}
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="text-gray-300 text-xs">—</div>
                    )}
                  </td>
                  <td className="px-1 py-1 text-center border-l border-gray-100">
                    {isDrafted ? (
                      <span className="font-mono text-sm font-bold text-emerald-700">
                        {formatQty(drafted)}
                      </span>
                    ) : (
                      <div className="text-gray-300 text-xs">—</div>
                    )}
                  </td>
                </>
              )}
              {weeks.map((week) => {
                const weekQty = product.weeks[week.key] || 0;
                const pending = product.pendingQty[week.key] || 0;
                const allPending = pending > 0 && pending >= weekQty;
                const mixed = pending > 0 && pending < weekQty;
                // Rendering:
                //   • 0 qty       → faint "0"
                //   • all invoiced → blue bold number
                //   • all pending  → amber italic number
                //   • mixed        → blue bold total + amber "+N" subline
                const qtyClass =
                  weekQty <= 0
                    ? "text-gray-400"
                    : allPending
                    ? "text-amber-700 italic font-semibold"
                    : "text-blue-700 font-semibold";
                const title =
                  mixed
                    ? `${formatQty(
                        weekQty - pending
                      )} invoiced · ${formatQty(pending)} on open sales order`
                    : allPending
                    ? `${formatQty(pending)} on open sales order (not yet invoiced)`
                    : undefined;
                return (
                  <td
                    key={week.key}
                    className="px-1 py-1 text-center border-l border-gray-100"
                    title={title}
                  >
                    <div className={`font-mono text-sm ${qtyClass}`}>
                      {formatQty(weekQty)}
                    </div>
                    {mixed && (
                      <div className="font-mono text-[10px] text-amber-700 italic leading-tight">
                        +{formatQty(pending)}
                      </div>
                    )}
                  </td>
                );
              })}
            </tr>
          );
        })}
    </>
  );
}
