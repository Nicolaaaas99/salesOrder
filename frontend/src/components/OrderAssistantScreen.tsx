import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Select from "react-select";
import AsyncSelect from "react-select/async";
import { Truck, Users, XCircle, User, Phone, Mail } from "lucide-react";
import { api } from "../api/client";
import type {
  CustomerDetail,
  HistoricalOrder,
  InventoryItem,
  OrderSuggestion,
  SalesOrderSearchResult,
  TaxType,
} from "../api/types";
import { HistoryGrid } from "./HistoryGrid";
import { DraftOrderPanel, type DraftLine } from "./DraftOrderPanel";
import { OrdersModal } from "./OrdersModal";
import { OrderSummaryModal } from "./OrderSummaryModal";
import { productKey, type CategoryProduct } from "../utils/history";
import { today } from "../utils/date";
import { makeEmptyLine, pickDefaultWarehouse, snapToPack } from "./LineItemsGrid";
import { SalesOrderScreen } from "./SalesOrderScreen";
import { useAuth } from "../context/AuthContext";

type Option = { value: string; label: string };

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

function draftFromSuggestion(
  s: OrderSuggestion,
  defaultTaxTypeId: number | null,
  preferredWhse: string | null,
): DraftLine {
  const line = makeEmptyLine(defaultTaxTypeId);
  const priced = s.unit_price > 0;
  const pack = s.pack_size || 1;
  return {
    ...line,
    stock_code: s.stock_code,
    item_description: s.item_description || s.description,
    description: s.item_description || s.description,
    warehouse_code: s.is_whse_item ? pickDefaultWarehouse(s.warehouse_codes, preferredWhse) : "",
    warehouse_codes: s.warehouse_codes,
    available: s.available,
    // Suggestion is already snapped to pack size on the backend
    quantity: s.suggested_quantity,
    pack_size: pack,
    uom: s.uom,
    is_uom_item: s.is_uom_item,
    is_whse_item: s.is_whse_item,
    unit_price: s.unit_price,
    unit_price_incl: s.unit_price_incl,
    price_source: s.price_source || (priced ? "Price List" : "None"),
    price_locked: priced,
    tax_type_id: s.default_tax_type_id ?? defaultTaxTypeId,
    discount_percent: 0,
    project_code: "",
  };
}

function draftFromInventoryItem(
  item: InventoryItem,
  quantity: number,
  defaultTaxTypeId: number | null,
  preferredWhse: string | null,
): DraftLine {
  const line = makeEmptyLine(defaultTaxTypeId);
  const pack = item.pack_size || 1;
  return {
    ...line,
    stock_code: item.code,
    item_description: item.description,
    description: item.description,
    warehouse_code: item.is_whse_item
      ? pickDefaultWarehouse(item.warehouse_codes, preferredWhse)
      : "",
    warehouse_codes: item.warehouse_codes,
    available: item.available,
    quantity: snapToPack(quantity, pack),
    pack_size: pack,
    uom: item.uom,
    is_uom_item: item.is_uom_item,
    is_whse_item: item.is_whse_item,
    unit_price: 0, // filled by the caller after resolving customer price
    tax_type_id: item.default_tax_type_id ?? defaultTaxTypeId,
    discount_percent: 0,
    project_code: "",
  };
}

export function OrderAssistantScreen() {
  const { defaultWarehouseCode } = useAuth();
  const [routes, setRoutes] = useState<Option[]>([]);
  const [selectedRoute, setSelectedRoute] = useState<Option | null>(null);
  const [selectedCustomer, setSelectedCustomer] = useState<Option | null>(null);
  // Customer → number of orders already placed TODAY on this route. Fetched
  // whenever the route changes and after every successful order placement so
  // the badges and confirmation modal stay accurate.
  const [ordersTodayMap, setOrdersTodayMap] = useState<Map<string, number>>(new Map());
  // When the user clicks a customer that already has orders today, we hold the
  // pending pick here while asking for confirmation.
  const [pendingCustomerPick, setPendingCustomerPick] = useState<Option | null>(null);

  // Orders-today modal state for the SELECTED customer. Opens from the pill
  // in the draft panel; is view-only (no edit).
  const [showOrdersToday, setShowOrdersToday] = useState(false);
  const [ordersTodayList, setOrdersTodayList] = useState<SalesOrderSearchResult[]>([]);
  const [isLoadingOrdersToday, setIsLoadingOrdersToday] = useState(false);
  const [viewingSummaryOf, setViewingSummaryOf] = useState<string | null>(null);

  const [customerDetail, setCustomerDetail] = useState<CustomerDetail | null>(
    null
  );
  const [orders, setOrders] = useState<HistoricalOrder[]>([]);
  const [suggestions, setSuggestions] = useState<OrderSuggestion[]>([]);
  const [packSizes, setPackSizes] = useState<Map<string, number>>(new Map());
  const [priceSources, setPriceSources] = useState<Map<string, string>>(new Map());
  const [taxTypes, setTaxTypes] = useState<TaxType[]>([]);

  const [isLoadingRoutes, setIsLoadingRoutes] = useState(false);
  const [isLoadingOrders, setIsLoadingOrders] = useState(false);

  const [error, setError] = useState<string | null>(null);

  const [draftLines, setDraftLines] = useState<DraftLine[]>([]);
  // Object identity bumps whenever a line is added/merged, so the DraftOrderPanel
  // can focus the Qty cell at the given index each time (not just when length grows).
  const [focusTarget, setFocusTarget] = useState<{ idx: number } | null>(null);
  const [finalizeOpen, setFinalizeOpen] = useState(false);
  const [finalizeSessionKey, setFinalizeSessionKey] = useState(0);

  useEffect(() => {
    (async () => {
      const tt = await api.listTaxTypes().catch(() => []);
      setTaxTypes(tt);
    })();
  }, []);

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

  const refreshOrdersToday = useCallback(async (routeValue: string | undefined) => {
    if (!routeValue) {
      setOrdersTodayMap(new Map());
      return;
    }
    try {
      const data = await api.listCustomerOrdersToday(routeValue);
      setOrdersTodayMap(new Map(data.map((d) => [d.account, d.order_count])));
    } catch {
      setOrdersTodayMap(new Map());
    }
  }, []);

  // One cache entry per route — fetched the first time the AsyncSelect's
  // loader needs the route's customer list, then reused for subsequent
  // keystrokes (we filter in-memory). Lives in a ref so the loader can read
  // it synchronously without being tied to React state timing.
  const routeCustomersCacheRef = useRef<Map<string, Option[]>>(new Map());

  // Route change: refresh the orders-today map, and if the current customer
  // isn't in the new route's list clear them. Doesn't touch customer on
  // route-cleared (null) — that case is handled in the Route selector's
  // onChange so we can distinguish "typed in customer dropdown" (keep
  // customer) from "user clicked the X" (clear customer).
  useEffect(() => {
    if (!selectedRoute) {
      refreshOrdersToday(undefined);
      return;
    }
    refreshOrdersToday(selectedRoute.value);
    let cancelled = false;
    (async () => {
      let list = routeCustomersCacheRef.current.get(selectedRoute.value);
      if (!list) {
        try {
          const data = await api.listHistoryCustomers(selectedRoute.value);
          list = data.map((c) => ({ value: c.id, label: `${c.id} — ${c.display}` }));
          routeCustomersCacheRef.current.set(selectedRoute.value, list);
        } catch {
          list = [];
        }
      }
      if (cancelled) return;
      setSelectedCustomer((prev) =>
        prev && list!.some((c) => c.value === prev.value) ? prev : null
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedRoute, refreshOrdersToday]);

  // AsyncSelect loader. For the route case we fetch once per route (cached in
  // the ref above) and filter in-memory — one network call per route change,
  // zero per keystroke. For the no-route case we hit the backend search per
  // input (debounced by AsyncSelect) so the rep can find any customer.
  const loadCustomerOptions = useCallback(
    async (input: string): Promise<Option[]> => {
      const term = input.trim().toLowerCase();
      if (selectedRoute) {
        let list = routeCustomersCacheRef.current.get(selectedRoute.value);
        if (!list) {
          try {
            const data = await api.listHistoryCustomers(selectedRoute.value);
            list = data.map((c) => ({ value: c.id, label: `${c.id} — ${c.display}` }));
            routeCustomersCacheRef.current.set(selectedRoute.value, list);
          } catch {
            return [];
          }
        }
        return term ? list.filter((o) => o.label.toLowerCase().includes(term)) : list;
      }
      try {
        const data = await api.searchCustomers(term, 50);
        return data.map((c) => ({ value: c.account, label: `${c.account} — ${c.name}` }));
      } catch {
        return [];
      }
    },
    [selectedRoute],
  );

  useEffect(() => {
    if (!selectedCustomer) {
      setOrders([]);
      setSuggestions([]);
      setPackSizes(new Map());
      setPriceSources(new Map());
      setCustomerDetail(null);
      setDraftLines([]);
      return;
    }

    (async () => {
      setIsLoadingOrders(true);
      setError(null);
      try {
        const [ordersData, suggestionsData, detail] = await Promise.all([
          api.listHistoryOrders(selectedCustomer.value),
          api.listOrderSuggestions(selectedCustomer.value, undefined, undefined, defaultWarehouseCode),
          api.getCustomer(selectedCustomer.value).catch(() => null),
        ]);
        setOrders(ordersData);
        setSuggestions(suggestionsData);
        setCustomerDetail(detail);

        // When the customer carries an AR route and the dropdown isn't already
        // on it, snap the Route selector to match. The route-watching effect
        // below is adjusted so it won't clear the just-picked customer.
        if (detail?.route && detail.route !== selectedRoute?.value) {
          const match = routes.find((r) => r.value === detail.route);
          if (match) setSelectedRoute(match);
        }

        const codes = new Set<string>();
        suggestionsData.forEach((s) => codes.add(s.stock_code));
        ordersData.forEach((o) =>
          o.items.forEach((it) => it.stockCode && codes.add(it.stockCode))
        );
        if (codes.size > 0) {
          try {
            const map = await api.getPackSizes([...codes]);
            // Seed with suggestion pack sizes (which the backend already snapped against)
            suggestionsData.forEach((s) => {
              if (s.pack_size) map[s.stock_code] = s.pack_size;
            });
            setPackSizes(new Map(Object.entries(map)));
          } catch {
            setPackSizes(new Map());
          }
          try {
            const priceMap = await api.getCustomerPrices(
              selectedCustomer.value,
              [...codes],
              defaultWarehouseCode,
            );
            const srcMap = new Map<string, string>();
            Object.entries(priceMap).forEach(([code, p]) => {
              if (p.source) srcMap.set(code, p.source);
            });
            setPriceSources(srcMap);
          } catch {
            setPriceSources(new Map());
          }
        } else {
          setPackSizes(new Map());
          setPriceSources(new Map());
        }
      } catch {
        setError("Failed to load history / suggestions.");
      } finally {
        setIsLoadingOrders(false);
      }
    })();

    setDraftLines([]);
  }, [selectedCustomer]);

  // Suggestion quantities tick down live as the user drafts lines for the
  // same stock code. When the remaining suggestion reaches 0, the item drops
  // out of the map entirely so the row loses its blue "suggested" treatment —
  // visually signalling it's been fulfilled.
  const suggestionsMap = useMemo(() => {
    const draftQtyByStock = new Map<string, number>();
    draftLines.forEach((l) => {
      if (!l.stock_code) return;
      draftQtyByStock.set(
        l.stock_code,
        (draftQtyByStock.get(l.stock_code) || 0) + (l.quantity || 0)
      );
    });

    const m = new Map<string, OrderSuggestion>();
    suggestions.forEach((s) => {
      const drafted = draftQtyByStock.get(s.stock_code) || 0;
      const remaining = s.suggested_quantity - drafted;
      if (remaining > 0) {
        m.set(productKey(s.stock_code, s.description), {
          ...s,
          suggested_quantity: remaining,
        });
      }
    });
    return m;
  }, [suggestions, draftLines]);

  const defaultTaxTypeId = customerDetail?.default_tax_type_id ?? null;

  const addLine = useCallback(
    (line: DraftLine) => {
      // Compute the resulting row index up-front so the DraftOrderPanel can
      // focus the right Qty cell — both when the stock is new (appended) and
      // when it's already in the draft (merged into its existing row).
      const existingIdx = draftLines.findIndex(
        (l) => l.stock_code === line.stock_code
      );
      const targetIdx = existingIdx >= 0 ? existingIdx : draftLines.length;

      setDraftLines((prev) => {
        const existingIdxFresh = prev.findIndex(
          (l) => l.stock_code === line.stock_code
        );
        if (existingIdxFresh >= 0) {
          // Bump quantity if already in draft
          const next = [...prev];
          next[existingIdxFresh] = {
            ...next[existingIdxFresh],
            quantity: next[existingIdxFresh].quantity + line.quantity,
          };
          return next;
        }
        return [...prev, line];
      });
      // New object identity each call so the panel's effect re-fires even when
      // targetIdx happens to be unchanged across repeated adds.
      setFocusTarget({ idx: targetIdx });

      // If the line arrives without a resolved price (bare / inventory-only
      // path) and we know the customer, fetch the current price and lock it.
      // Suggestions already come priced from the backend so this is a no-op
      // for them.
      const needsPrice =
        !line.price_locked && line.unit_price === 0 && !!line.stock_code;
      if (needsPrice && customerDetail?.account) {
        const account = customerDetail.account;
        const code = line.stock_code;
        api
          .getCustomerPrices(account, [code], defaultWarehouseCode)
          .then((map) => {
            const p = map[code];
            // Store BOTH prices on the draft line — excl is the computational
            // invariant, incl is the exact Price List incl used by the
            // SalesOrderScreen's Tax Mode display.
            const excl = p?.excl_price ?? 0;
            const incl = p?.incl_price ?? 0;
            setDraftLines((prev) =>
              prev.map((l) =>
                l.stock_code === code
                  ? {
                      ...l,
                      unit_price: excl,
                      unit_price_incl: incl,
                      price_source: p?.source ?? "None",
                      price_locked: excl > 0,
                      is_fallback_warehouse: p?.is_fallback_warehouse ?? false,
                    }
                  : l
              )
            );
          })
          .catch(() => {
            setDraftLines((prev) =>
              prev.map((l) =>
                l.stock_code === code
                  ? { ...l, price_source: "None", price_locked: false, is_fallback_warehouse: false }
                  : l
              )
            );
          });
      }
    },
    [customerDetail?.account, draftLines]
  );

  const handleAddToOrder = useCallback(
    async (
      product: CategoryProduct,
      _category: string,
      suggestion: OrderSuggestion | undefined
    ) => {
      if (suggestion) {
        addLine(draftFromSuggestion(suggestion, defaultTaxTypeId, defaultWarehouseCode));
        return;
      }

      // Non-suggested item — fetch inventory for warehouse/tax info,
      // and default qty to last non-zero week's quantity.
      const weekKeys = Object.keys(product.weeks).sort();
      const lastNonZeroKey = [...weekKeys]
        .reverse()
        .find((k) => (product.weeks[k] || 0) > 0);
      const qty = lastNonZeroKey ? product.weeks[lastNonZeroKey] : 1;

      try {
        const results = await api.searchInventory(product.stockCode);
        const match =
          results.find((r) => r.code === product.stockCode) ?? results[0];
        if (match) {
          addLine(draftFromInventoryItem(match, qty, defaultTaxTypeId, defaultWarehouseCode));
          return;
        }
      } catch {
        /* fall through to bare add */
      }

      // Fallback: bare line the rep can complete manually.
      const pack = packSizes.get(product.stockCode) || 1;
      addLine({
        ...makeEmptyLine(defaultTaxTypeId),
        stock_code: product.stockCode,
        item_description: product.description,
        description: product.description,
        quantity: snapToPack(qty, pack),
        pack_size: pack,
      });
    },
    [addLine, defaultTaxTypeId, defaultWarehouseCode, packSizes]
  );

  const handleAddAllSuggested = useCallback(() => {
    // Iterate the live map (which has already subtracted drafted qty) so this
    // button only tops up the remaining suggestion instead of double-adding.
    suggestionsMap.forEach((s) => {
      addLine(draftFromSuggestion(s, defaultTaxTypeId, defaultWarehouseCode));
    });
  }, [suggestionsMap, addLine, defaultTaxTypeId, defaultWarehouseCode]);

  const handleUpdateLine = useCallback(
    (index: number, patch: Partial<DraftLine>) => {
      setDraftLines((prev) =>
        prev.map((l, i) => (i === index ? { ...l, ...patch } : l))
      );
    },
    []
  );

  const handleRemoveLine = useCallback((index: number) => {
    setDraftLines((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleClearAll = useCallback(() => setDraftLines([]), []);

  const handleAddItem = useCallback(
    (item: InventoryItem) => {
      addLine(draftFromInventoryItem(item, 0, defaultTaxTypeId, defaultWarehouseCode));
    },
    [addLine, defaultTaxTypeId, defaultWarehouseCode]
  );

  const handleContinue = useCallback(() => {
    if (!customerDetail || draftLines.length === 0) return;
    setFinalizeOpen(true);
  }, [customerDetail, draftLines.length]);

  const handleFinalizeClose = useCallback(() => setFinalizeOpen(false), []);

  // Fetch today's orders for the selected customer when the modal opens.
  useEffect(() => {
    if (!showOrdersToday || !selectedCustomer) {
      setOrdersTodayList([]);
      return;
    }
    (async () => {
      setIsLoadingOrdersToday(true);
      try {
        const data = await api.listOrdersByOrderDate(selectedCustomer.value, today());
        setOrdersTodayList(data);
      } catch {
        setOrdersTodayList([]);
      } finally {
        setIsLoadingOrdersToday(false);
      }
    })();
  }, [showOrdersToday, selectedCustomer]);

  const handleFinalizePlaced = useCallback(() => {
    // Soft reload: close the finalize sheet and clear the customer selection,
    // which cascades (via the selectedCustomer effect) to reset orders,
    // suggestions, customerDetail and draftLines. The route stays — telesales
    // typically work a route end-to-end, so we don't force them to re-pick it.
    setFinalizeOpen(false);
    setSelectedCustomer(null);
    // Bump session key so the finalize modal remounts fresh next time (dates,
    // description, discount reset to defaults).
    setFinalizeSessionKey((k) => k + 1);
    // Refresh the "orders today" badges so the just-placed order shows up next
    // to the customer immediately.
    refreshOrdersToday(selectedRoute?.value);
    // No second success modal — SalesOrderScreen already showed one with the
    // actual order number before it fired onPlaced.
  }, [refreshOrdersToday, selectedRoute]);

  return (
    <div className="h-full bg-gray-50 p-2 flex flex-col">
      {pendingCustomerPick && (
        <div className="fixed inset-0 z-50 bg-black bg-opacity-30 flex items-center justify-center p-4">
          <div className="bg-white rounded shadow-lg max-w-md w-full p-4">
            <h3 className="font-semibold text-gray-900 text-sm mb-2">
              Customer already has orders today
            </h3>
            <p className="text-sm text-gray-700 mb-4">
              <strong>{pendingCustomerPick.label}</strong> already has{" "}
              <strong>{ordersTodayMap.get(pendingCustomerPick.value) ?? 0}</strong>{" "}
              order(s) placed today. Do you want to place another one?
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setPendingCustomerPick(null)}
                className="px-3 py-1 text-sm border border-gray-300 rounded hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  setSelectedCustomer(pendingCustomerPick);
                  setPendingCustomerPick(null);
                }}
                className="px-3 py-1 text-sm bg-blue-600 text-white rounded hover:bg-blue-700"
              >
                Continue
              </button>
            </div>
          </div>
        </div>
      )}

      {showOrdersToday && selectedCustomer && (
        <OrdersModal
          customer={{
            id: selectedCustomer.value,
            display: selectedCustomer.label.replace(/^[^—]+—\s*/, ""),
          }}
          date={today()}
          orders={ordersTodayList}
          isLoading={isLoadingOrdersToday}
          onClose={() => setShowOrdersToday(false)}
          onViewOrder={(no) => setViewingSummaryOf(no)}
        />
      )}

      {viewingSummaryOf && (
        <OrderSummaryModal
          orderNo={viewingSummaryOf}
          onClose={() => setViewingSummaryOf(null)}
          // No onEdit — Order Assistant context is view-only, no "go to source".
        />
      )}

      <div className="max-w-[1600px] mx-auto w-full flex-1 min-h-0 flex flex-col gap-2">
        {/* Top bar: route + customer */}
        <div className="grid grid-cols-2 gap-2 flex-shrink-0">
          <div className="bg-white rounded border p-2 shadow-sm">
            <label className="flex items-center font-semibold text-gray-800 mb-1 text-sm">
              <Truck className="w-3 h-3 mr-1 text-blue-600" />
              Route
            </label>
            <Select
              options={routes}
              value={selectedRoute}
              onChange={(opt) => {
                const next = opt as Option | null;
                setSelectedRoute(next);
                // User explicitly cleared the Route → clear the customer too.
                // (Typing in the customer dropdown ALSO clears the route, but
                // via a separate handler that intentionally keeps the customer
                // mid-search — so this branch only runs for the X click.)
                if (!next) setSelectedCustomer(null);
              }}
              placeholder="Search route..."
              isDisabled={isLoadingRoutes}
              isLoading={isLoadingRoutes}
              styles={selectStyles}
              isSearchable
              isClearable
            />
          </div>

          <div className="bg-white rounded border p-2 shadow-sm">
            <label className="flex items-center font-semibold text-gray-800 mb-1 text-sm">
              <Users className="w-3 h-3 mr-1 text-green-600" />
              Customer
            </label>
            <AsyncSelect
              key={selectedRoute?.value ?? "all"}
              cacheOptions={!!selectedRoute}
              defaultOptions
              loadOptions={loadCustomerOptions}
              value={selectedCustomer}
              onChange={(opt) => {
                const next = opt as Option | null;
                if (!next) {
                  setSelectedCustomer(null);
                  setSelectedRoute(null);     // clearing the customer also clears the route
                  return;
                }
                const count = ordersTodayMap.get(next.value) ?? 0;
                if (count > 0 && next.value !== selectedCustomer?.value) {
                  setPendingCustomerPick(next);
                  return;
                }
                setSelectedCustomer(next);
              }}
              formatOptionLabel={(opt) => {
                const o = opt as Option;
                const count = ordersTodayMap.get(o.value) ?? 0;
                return (
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate">{o.label}</span>
                    {count > 0 && (
                      <span className="flex-shrink-0 text-[10px] font-semibold bg-emerald-100 text-emerald-800 border border-emerald-300 px-1.5 py-0.5 rounded-full">
                        {count} today
                      </span>
                    )}
                  </div>
                );
              }}
              placeholder={
                selectedRoute ? "Search customer in route..." : "Search any customer..."
              }
              styles={selectStyles}
              isClearable
            />
          </div>
        </div>

        {customerDetail && (
          <div className="bg-white rounded border px-3 py-1.5 shadow-sm flex items-center gap-4 text-xs flex-shrink-0">
            <span className="flex items-center gap-1 text-gray-700 min-w-0">
              <User className="w-3 h-3 text-gray-400 flex-shrink-0" />
              <span className="font-semibold truncate" title={customerDetail.contact_name || "—"}>
                {customerDetail.contact_name || "—"}
              </span>
            </span>
            <span className="flex items-center gap-1 text-gray-700 min-w-0">
              <Phone className="w-3 h-3 text-gray-400 flex-shrink-0" />
              {customerDetail.contact_tel ? (
                <a
                  href={`tel:${customerDetail.contact_tel}`}
                  className="font-mono hover:text-blue-600 truncate"
                  title={`Phone 1: ${customerDetail.contact_tel}`}
                >
                  {customerDetail.contact_tel}
                </a>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </span>
            <span className="flex items-center gap-1 text-gray-700 min-w-0">
              <Phone className="w-3 h-3 text-gray-400 flex-shrink-0" />
              {customerDetail.contact_tel2 ? (
                <a
                  href={`tel:${customerDetail.contact_tel2}`}
                  className="font-mono hover:text-blue-600 truncate"
                  title={`Phone 2: ${customerDetail.contact_tel2}`}
                >
                  {customerDetail.contact_tel2}
                </a>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </span>
            <span className="flex items-center gap-1 text-gray-700 min-w-0 flex-1">
              <Mail className="w-3 h-3 text-gray-400 flex-shrink-0" />
              {customerDetail.contact_email ? (
                <a
                  href={`mailto:${customerDetail.contact_email}`}
                  className="hover:text-blue-600 truncate"
                  title={customerDetail.contact_email}
                >
                  {customerDetail.contact_email}
                </a>
              ) : (
                <span className="text-gray-400">—</span>
              )}
            </span>
          </div>
        )}

        {error && (
          <div className="p-1 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs flex-shrink-0">
            <XCircle className="w-3 h-3 mr-1 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Split pane: history grid | draft */}
        <div className="grid grid-cols-[1fr_380px] gap-2 flex-1 min-h-0">
          <HistoryGrid
            orders={orders}
            isLoading={isLoadingOrders}
            emptyMessage={
              selectedCustomer
                ? "No history for this customer"
                : selectedRoute
                ? "Select a customer to see suggestions"
                : "Select a route to begin"
            }
            suggestions={suggestionsMap}
            priceSources={priceSources}
            onAddToOrder={handleAddToOrder}
            onAddAllSuggested={handleAddAllSuggested}
            heightStyle="100%"
          />

          <div className="h-full min-h-0">
            <DraftOrderPanel
              customer={customerDetail}
              lines={draftLines}
              onUpdateLine={handleUpdateLine}
              onRemoveLine={handleRemoveLine}
              onClearAll={handleClearAll}
              onAddItem={handleAddItem}
              onContinue={handleContinue}
              taxTypes={taxTypes}
              focusTarget={focusTarget}
              ordersTodayCount={
                selectedCustomer ? ordersTodayMap.get(selectedCustomer.value) : undefined
              }
              onShowOrdersToday={() => setShowOrdersToday(true)}
            />
          </div>
        </div>

        {customerDetail && (
          <div
            className="modal-backdrop modal-backdrop--sheet"
            style={{ display: finalizeOpen ? "flex" : "none" }}
          >
            <div className="modal-sheet" onClick={(e) => e.stopPropagation()}>
              <SalesOrderScreen
                key={`${customerDetail.account}-${finalizeSessionKey}`}
                initialCustomer={customerDetail}
                controlledLines={draftLines}
                onControlledLinesChange={setDraftLines}
                lockedCustomer
                hideOpenButton
                onClose={handleFinalizeClose}
                onPlaced={handleFinalizePlaced}
              />
            </div>
          </div>
        )}

      </div>
    </div>
  );
}
