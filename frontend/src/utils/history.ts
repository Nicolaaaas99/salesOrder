import type { HistoricalOrder } from "../api/types";

export type WeekRange = {
  start: Date;
  end: Date;
  key: string;
  display: string;
};

export type CategoryProduct = {
  stockCode: string;
  description: string;
  totalQuantity: number;
  /** Total qty per week (invoiced + pending). */
  weeks: Record<string, number>;
  /** The PENDING slice of the week total, per week. 0 or absent means
   *  all-invoiced; equals weeks[weekKey] means all-pending. The HistoryGrid
   *  uses this to render invoiced vs pending separately. */
  pendingQty: Record<string, number>;
};

export type CategoryData = {
  totalQuantity: number;
  weeks: Record<string, { totalQuantity: number }>;
  products: CategoryProduct[];
};

export type ProblemInfo = {
  product: CategoryProduct;
  lastPurchaseWeekIndex: number;
  weeksSinceLastPurchase: number;
};

export type ProblemSeverity = "yellow" | "orange" | "red";

export function getProblemSeverity(weeksSince: number): ProblemSeverity {
  if (weeksSince > 4) return "red";
  if (weeksSince > 2) return "orange";
  return "yellow";
}

export const formatDate = (dateInput: Date | string | null | undefined) => {
  if (!dateInput) return "N/A";
  const d = dateInput instanceof Date ? dateInput : new Date(dateInput);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
};

/**
 * Compact single-line label for a week range. Same month → "16-22 Aug";
 * cross-month → "28 Aug - 3 Sep".
 */
export const formatWeekLabel = (week: WeekRange): string => {
  const start = week.start instanceof Date ? week.start : new Date(week.start);
  const end = week.end instanceof Date ? week.end : new Date(week.end);
  const sameMonth = start.getMonth() === end.getMonth();
  const dayFmt = (d: Date) => d.toLocaleDateString("en-GB", { day: "numeric" });
  const monthFmt = (d: Date) => d.toLocaleDateString("en-GB", { month: "short" });
  return sameMonth
    ? `${dayFmt(start)}-${dayFmt(end)} ${monthFmt(end)}`
    : `${dayFmt(start)} ${monthFmt(start)} - ${dayFmt(end)} ${monthFmt(end)}`;
};

export const formatQty = (quantity: number) => {
  const num = Number(quantity);
  return Number.isNaN(num) ? "0" : num.toFixed(0);
};

export const getWeekRange = (dateString: string): WeekRange => {
  const date = new Date(dateString);
  const startOfWeek = new Date(date);
  startOfWeek.setDate(date.getDate() - date.getDay());
  const endOfWeek = new Date(startOfWeek);
  endOfWeek.setDate(startOfWeek.getDate() + 6);
  return {
    start: startOfWeek,
    end: endOfWeek,
    key: startOfWeek.toISOString().split("T")[0],
    display: `${formatDate(startOfWeek)} to ${formatDate(endOfWeek)}`,
  };
};

/**
 * Bucket orders into categories → products → weekly quantities.
 * Filters out stock codes containing "999000" and descriptions containing "crate".
 */
export function aggregateOrders(orders: HistoricalOrder[]): {
  categories: Record<string, CategoryData>;
  weeks: WeekRange[];
} {
  if (!orders.length) return { categories: {}, weeks: [] };

  const categoriesData: Record<string, CategoryData> = {};
  const weekSet = new Set<string>();

  orders.forEach((order) => {
    if (!order.orderDate) return;
    weekSet.add(JSON.stringify(getWeekRange(order.orderDate)));
  });

  const weeks: WeekRange[] = Array.from(weekSet)
    .map((str) => JSON.parse(str) as WeekRange)
    .sort((a, b) => new Date(a.start).getTime() - new Date(b.start).getTime());

  // Extend the grid forward to "this week" so any recent silent gap is visible.
  // Without this, the last N of the shown columns wouldn't match the suggestion
  // window (which is last 6 *calendar* weeks from today), causing the freq
  // badge to appear "off" by the number of missing weeks.
  if (weeks.length > 0) {
    const todayWeekStart = new Date(getWeekRange(new Date().toISOString()).start).getTime();
    const cursor = new Date(weeks[weeks.length - 1].start);
    cursor.setDate(cursor.getDate() + 7);
    let added = 0;
    while (cursor.getTime() <= todayWeekStart && added < 12) {
      weeks.push(getWeekRange(cursor.toISOString()));
      cursor.setDate(cursor.getDate() + 7);
      added++;
    }
  }

  const productBuckets: Record<string, Record<string, CategoryProduct>> = {};

  orders.forEach((order) => {
    if (!order.orderDate) return;
    const weekKey = getWeekRange(order.orderDate).key;

    order.items.forEach((item) => {
      if (
        item.stockCode.includes("999000") ||
        item.description.toLowerCase().includes("crate")
      ) {
        return;
      }

      const category = item.category || "Uncategorized";
      if (!categoriesData[category]) {
        categoriesData[category] = {
          totalQuantity: 0,
          weeks: {},
          products: [],
        };
        productBuckets[category] = {};
      }

      const productKey = `${item.stockCode}-${item.description}`;
      if (!productBuckets[category][productKey]) {
        productBuckets[category][productKey] = {
          stockCode: item.stockCode,
          description: item.description,
          totalQuantity: 0,
          weeks: {},
          pendingQty: {},
        };
      }

      const product = productBuckets[category][productKey];
      product.weeks[weekKey] = (product.weeks[weekKey] || 0) + item.quantity;
      product.totalQuantity += item.quantity;
      if (item.onOrder) {
        product.pendingQty[weekKey] = (product.pendingQty[weekKey] || 0) + item.quantity;
      }

      if (!categoriesData[category].weeks[weekKey]) {
        categoriesData[category].weeks[weekKey] = { totalQuantity: 0 };
      }
      categoriesData[category].weeks[weekKey].totalQuantity += item.quantity;
      categoriesData[category].totalQuantity += item.quantity;
    });
  });

  Object.keys(categoriesData).forEach((category) => {
    categoriesData[category].products = Object.values(
      productBuckets[category]
    ).sort((a, b) => b.totalQuantity - a.totalQuantity);
  });

  return { categories: categoriesData, weeks };
}

export function detectProblemItems(
  categories: Record<string, CategoryData>,
  weeks: WeekRange[]
): Map<string, ProblemInfo> {
  const problems = new Map<string, ProblemInfo>();

  // The grid is extended forward to today's week so a silent gap is visible,
  // but today's week is partial and will almost always read as 0 at the start.
  // Compare the last two *completed* weeks to avoid false positives.
  const todayWeekStart = new Date(getWeekRange(new Date().toISOString()).start).getTime();
  const completed = weeks.filter((w) => {
    const ws = w.start instanceof Date ? w.start : new Date(w.start);
    return ws.getTime() < todayWeekStart;
  });

  if (completed.length < 3) return problems;

  const recentKeys = completed.slice(-2).map((w) => w.key);
  const olderKeys = completed.slice(0, -2).map((w) => w.key);

  Object.values(categories).forEach((cat) => {
    cat.products.forEach((product) => {
      const purchasedOlder = olderKeys.some((k) => (product.weeks[k] || 0) > 0);
      const missedRecent = recentKeys.every((k) => (product.weeks[k] || 0) === 0);
      if (purchasedOlder && missedRecent) {
        // Weeks-since-last-purchase is measured against today's week position
        // in the FULL `weeks` array (so the badge stays accurate as the
        // in-progress week ticks on without ordering).
        let lastIdx = -1;
        for (let i = weeks.length - 1; i >= 0; i--) {
          if ((product.weeks[weeks[i].key] || 0) > 0) {
            lastIdx = i;
            break;
          }
        }
        problems.set(`${product.stockCode}-${product.description}`, {
          product,
          lastPurchaseWeekIndex: lastIdx,
          weeksSinceLastPurchase: weeks.length - 1 - lastIdx,
        });
      }
    });
  });

  return problems;
}

export const productKey = (stockCode: string, description: string) =>
  `${stockCode}-${description}`;
