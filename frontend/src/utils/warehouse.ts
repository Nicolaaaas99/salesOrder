import { useEffect, useRef } from "react";
import { api } from "../api/client";
import type { LineRow } from "../components/LineItemsGrid";
import { useAuth } from "../context/AuthContext";
import { resolvedPriceSource } from "./history";

/** Who may switch their own warehouse (backend: services/dc_warehouse.py).
 *  An AGENT's warehouse is fixed and set by an admin on Users & Roles. */
export function canChooseWarehouse(role: string | null | undefined): boolean {
  return role === "SUPERVISOR" || role === "ADMIN";
}

/** "01.HOF_MAK" -> "MAK" for compact labels. */
export function shortWarehouse(code: string | null | undefined): string {
  const c = (code ?? "").trim();
  return c.includes("_") ? c.slice(c.lastIndexOf("_") + 1) : c;
}

/** When a supervisor switches warehouse mid-order, move the order's lines
 *  that were on the old warehouse to the new one and re-price them there
 *  (pricing is per warehouse). Pass enabled=false for a loaded existing
 *  order — its saved lines keep their warehouse. */
export function useMoveLinesOnWarehouseSwitch(
  lines: LineRow[],
  setLines: (updater: (prev: LineRow[]) => LineRow[]) => void,
  account: string | null | undefined,
  enabled: boolean,
) {
  const { defaultWarehouseCode, sessionDate } = useAuth();
  const prev = useRef(defaultWarehouseCode);
  useEffect(() => {
    const from = prev.current;
    const to = defaultWarehouseCode;
    prev.current = to;
    if (!enabled || !from || !to || from === to) return;
    const moving = lines.filter((l) => l.warehouse_code === from);
    if (moving.length === 0) return;
    const move = (l: LineRow) => (l.warehouse_code === from ? { ...l, warehouse_code: to } : l);
    const codes = [...new Set(moving.map((l) => l.stock_code).filter(Boolean))];
    if (!account || codes.length === 0 || !sessionDate) {
      setLines((cur) => cur.map(move));
      return;
    }
    api
      .getCustomerPrices(account, codes, sessionDate, to)
      .then((map) =>
        setLines((cur) =>
          cur.map((l) => {
            if (l.warehouse_code !== from) return l;
            const p = l.stock_code ? map[l.stock_code] : undefined;
            const excl = p?.excl_price ?? 0;
            return {
              ...l,
              warehouse_code: to,
              unit_price: excl,
              unit_price_incl: p?.incl_price ?? 0,
              price_source: resolvedPriceSource(l.stock_code, excl, p?.source ?? null),
              price_locked: excl > 0,
              is_fallback_warehouse: p?.is_fallback_warehouse ?? false,
              price_list_broken: p?.price_list_broken ?? false,
            };
          }),
        ),
      )
      // Prices couldn't be fetched: still move the lines, unpriced, so the
      // save is blocked until they are priced at the new warehouse.
      .catch(() =>
        setLines((cur) =>
          cur.map((l) =>
            l.warehouse_code === from
              ? { ...l, warehouse_code: to, unit_price: 0, unit_price_incl: 0, price_source: "None", price_locked: false }
              : l,
          ),
        ),
      );
    // Runs only when the warehouse changes; lines are read at that moment.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultWarehouseCode]);
}

/** The reason a NEW order for this customer can't be placed from this
 *  warehouse, or null. The customer card's DC (dc_warehouse_code) must be
 *  the warehouse the order is captured from; blank / unknown card DCs are
 *  not checked. Same rule as the server (services/dc_warehouse.py). */
export function dcMismatchMessage(
  customer: { name: string; dist_centre?: string; dc_warehouse_code?: string | null } | null | undefined,
  warehouse: string | null | undefined,
  role: string | null | undefined,
): string | null {
  const theirs = customer?.dc_warehouse_code;
  if (!customer || !theirs || !warehouse || theirs.toUpperCase() === warehouse.toUpperCase()) return null;
  const how = canChooseWarehouse(role)
    ? `Switch to ${shortWarehouse(theirs)}, or fix the customer's DC if their card is wrong`
    : "Ask a supervisor to fix the customer's DC (or capture it for you)";
  return (
    `${customer.name} is on DC ${customer.dist_centre ?? ""} (${shortWarehouse(theirs)}) but you're capturing ` +
    `from ${shortWarehouse(warehouse)}. ${how} before placing the order.`
  );
}
