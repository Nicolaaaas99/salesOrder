import type { SalesOrderSearchResult } from "../api/types";

export type DeliveryAgeTier = "overdue" | "today" | "future" | "none";

const TIER_RANK: Record<DeliveryAgeTier, number> = {
  none: 0,
  future: 1,
  today: 2,
  overdue: 3,
};

/** Tailwind classes for the pill — same scale used in OrdersModal rows and
 *  the "Existing orders" count badge in CreditStatus. */
export const TIER_CLASSES: Record<DeliveryAgeTier, string> = {
  overdue: "bg-red-100 text-red-800 border-red-300",
  today: "bg-amber-100 text-amber-800 border-amber-300",
  future: "bg-green-100 text-green-800 border-green-300",
  none: "bg-gray-100 text-gray-700 border-gray-300",
};

function daysBetween(laterIso: string, earlierIso: string): number {
  const [ya, ma, da] = laterIso.split("-").map(Number);
  const [yb, mb, db] = earlierIso.split("-").map(Number);
  const a = Date.UTC(ya, ma - 1, da);
  const b = Date.UTC(yb, mb - 1, db);
  return Math.round((a - b) / 86_400_000);
}

export function classifyDeliveryAge(
  deliveryIso: string | null,
  todayIso: string,
): { tier: DeliveryAgeTier; label: string } {
  if (!deliveryIso) return { tier: "none", label: "No date" };
  if (deliveryIso < todayIso) {
    const d = daysBetween(todayIso, deliveryIso);
    return { tier: "overdue", label: `Overdue ${d}d` };
  }
  if (deliveryIso === todayIso) return { tier: "today", label: "Today" };
  const d = daysBetween(deliveryIso, todayIso);
  return { tier: "future", label: `+${d}d` };
}

export function worstDeliveryTier(
  orders: SalesOrderSearchResult[],
  todayIso: string,
): DeliveryAgeTier {
  let worst: DeliveryAgeTier = "none";
  for (const o of orders) {
    const { tier } = classifyDeliveryAge(o.delivery_date, todayIso);
    if (TIER_RANK[tier] > TIER_RANK[worst]) worst = tier;
  }
  return worst;
}
