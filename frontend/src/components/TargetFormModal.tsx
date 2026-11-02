import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Select from "react-select";
import AsyncSelect from "react-select/async";
import { Loader2, Plus, Save, Trash2, X, XCircle } from "lucide-react";
import { api } from "../api/client";
import type {
  AgentOption,
  SalesRepOption,
  TargetPeriod,
  TargetUpsert,
  TargetWithActual,
} from "../api/types";
import { useAuth } from "../context/AuthContext";
import { DateInput } from "./DateInput";
import { today as todayIso } from "../utils/date";

type Props = {
  /** When set, the modal opens in EDIT mode and prefills from this
   *  target. When null, it opens in CREATE mode. */
  initial: TargetWithActual | null;
  onSaved: () => void;
  onClose: () => void;
};

type Option = { value: string; label: string };

/** The seven filter dimensions a target can scope by. Each occupies
 *  its own column in the DB. Grouped (in the UI) into three semantic
 *  categories so the user can reason about the compound structure:
 *    • who placed the order
 *    • who the customer is linked to
 *    • what was sold                                                  */
type DimKey =
  | "agent"
  | "telesales"
  | "sales_rep"
  | "route"
  | "customer"
  | "category"
  | "stock";

type ScopeChip = {
  dim: DimKey;
  value: string;      // the ID / code we send to the server (name for agent)
  label: string;      // what we display in the chip
  /** Only populated when dim === "agent". Set from the picked
   *  AgentOption so save can send the stable id to the backend. */
  agentId?: number;
};

const DIM_LABEL: Record<DimKey, string> = {
  agent: "Placed by",
  telesales: "Telesales rep",
  sales_rep: "Sales rep",
  route: "Route",
  customer: "Customer",
  category: "Category",
  stock: "Stock",
};

// What each dim "means" — one-line hints shown in the add-filter menu
// so the semantics are explicit while picking.
const DIM_HINT: Record<DimKey, string> = {
  agent: "Orders this user placed via the app",
  telesales: "Customers assigned to this telesales rep",
  sales_rep: "Customers assigned to this sales rep",
  route: "Customers on this route",
  customer: "This specific customer",
  category: "Stocks in this category",
  stock: "This specific stock item",
};

const PERIOD_OPTIONS: { value: TargetPeriod; label: string }[] = [
  { value: "DAILY", label: "Daily" },
  { value: "WEEKLY", label: "Weekly" },
  { value: "MONTHLY", label: "Monthly" },
  { value: "CUSTOM", label: "Custom" },
];

/** Compact react-select styling that matches the look used elsewhere. */
const selectStyles = {
  control: (base: any) => ({
    ...base,
    padding: "1px 4px",
    border: "1px solid #d1d5db",
    borderRadius: "6px",
    boxShadow: "none",
    fontSize: "13px",
    minHeight: "32px",
    "&:hover": { borderColor: "#3b82f6" },
  }),
  menu: (base: any) => ({ ...base, fontSize: "13px", zIndex: 60 }),
  menuPortal: (base: any) => ({ ...base, zIndex: 9999 }),
  option: (base: any, state: any) => ({
    ...base,
    fontSize: "13px",
    padding: "4px 8px",
    backgroundColor: state.isFocused ? "#eff6ff" : "white",
    color: "#1f2937",
    "&:active": { backgroundColor: "#dbeafe" },
  }),
};

export function TargetFormModal({ initial, onSaved, onClose }: Props) {
  const { role } = useAuth();
  const isEdit = !!initial;

  // Pre-loaded options for the small-cardinality dims. The agent list
  // merges SalesRep + telesale user-dict + audit cUsername so an
  // app-only login still appears. We keep the raw AgentOption too so
  // we can route the saved target to cUsername vs cTelesales based on
  // the pick's `source`.
  const [agentOpts, setAgentOpts] = useState<AgentOption[]>([]);
  const [telesaleOpts, setTelesaleOpts] = useState<Option[]>([]);
  const [salesRepOpts, setSalesRepOpts] = useState<SalesRepOption[]>([]);
  const [routeOpts, setRouteOpts] = useState<Option[]>([]);
  const [categoryOpts, setCategoryOpts] = useState<Option[]>([]);
  const [loadingOptions, setLoadingOptions] = useState(true);
  const [optionsError, setOptionsError] = useState<string | null>(null);

  // Scope filter chips. Ordered insertion = ordered display, but the
  // meaning is order-independent (AND-filtered). At most one chip per
  // DimKey.
  const [chips, setChips] = useState<ScopeChip[]>([]);

  // When set, the "pick a value for this dim" sub-form is open. Null
  // = the add-filter menu is open or nothing is open.
  const [pending, setPending] = useState<DimKey | null>(null);
  const [showAddMenu, setShowAddMenu] = useState(false);
  // Ref on the Add filter button — the portaled menu reads its
  // bounding rect to position itself with `fixed` coords, which
  // escapes the modal's `overflow-auto` clipping.
  const addFilterBtnRef = useRef<HTMLButtonElement | null>(null);

  const [period, setPeriod] = useState<TargetPeriod>("MONTHLY");
  const [targetLitres, setTargetLitres] = useState<string>("");
  const [effectiveFrom, setEffectiveFrom] = useState<string>(todayIso());
  const [effectiveTo, setEffectiveTo] = useState<string>("");

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // -----------------------------------------------------------------
  // Load options once.
  // -----------------------------------------------------------------
  const cancelledRef = useRef(false);
  useEffect(() => {
    cancelledRef.current = false;
    setLoadingOptions(true);
    setOptionsError(null);
    Promise.all([
      api.listAgentOptions(),
      api.listTelesaleOptions(),
      api.listSalesReps(),
      api.listRouteOptions(),
      api.listCategoryOptions(),
    ])
      .then(([agents, telesales, salesreps, routes, categories]) => {
        if (cancelledRef.current) return;
        setAgentOpts(agents.filter((a) => a.name));
        setTelesaleOpts(
          telesales.map((t) => ({ value: t.telesale, label: t.telesale })),
        );
        setSalesRepOpts(salesreps);
        setRouteOpts(routes.map((r) => ({ value: r.route, label: r.route })));
        setCategoryOpts(categories.map((c) => ({ value: c.category, label: c.category })));
      })
      .catch((e: any) => {
        if (!cancelledRef.current) {
          setOptionsError(
            e?.response?.data?.detail ?? e?.message ?? "Failed to load options.",
          );
        }
      })
      .finally(() => {
        if (!cancelledRef.current) setLoadingOptions(false);
      });
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  // -----------------------------------------------------------------
  // Prefill when editing. Expand each stored dim back into a chip.
  // -----------------------------------------------------------------
  useEffect(() => {
    if (!initial) return;
    const next: ScopeChip[] = [];
    if (initial.agent_id != null || initial.username) {
      next.push({
        dim: "agent",
        value: initial.username ?? String(initial.agent_id),
        label: initial.username ?? `#${initial.agent_id}`,
        agentId: initial.agent_id ?? undefined,
      });
    }
    if (initial.telesale)
      next.push({ dim: "telesales", value: initial.telesale, label: initial.telesale });
    if (initial.sales_rep)
      next.push({ dim: "sales_rep", value: initial.sales_rep, label: initial.sales_rep });
    if (initial.route)
      next.push({ dim: "route", value: initial.route, label: initial.route });
    if (initial.customer_account)
      next.push({
        dim: "customer",
        value: initial.customer_account,
        label: initial.customer_account,
      });
    if (initial.category)
      next.push({ dim: "category", value: initial.category, label: initial.category });
    if (initial.stock_code)
      next.push({ dim: "stock", value: initial.stock_code, label: initial.stock_code });
    setChips(next);
    setPeriod(initial.period);
    setTargetLitres(String(initial.target_litres));
    setEffectiveFrom(initial.effective_from);
    setEffectiveTo(initial.effective_to ?? "");
  }, [initial]);

  // Esc closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (pending) {
        setPending(null);
        return;
      }
      if (showAddMenu) {
        setShowAddMenu(false);
        return;
      }
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pending, showAddMenu, onClose]);

  // -----------------------------------------------------------------
  // Helpers
  // -----------------------------------------------------------------
  const chipByDim = useMemo(() => {
    const m: Partial<Record<DimKey, ScopeChip>> = {};
    for (const c of chips) m[c.dim] = c;
    return m;
  }, [chips]);

  const alreadyAdded = (dim: DimKey) => chipByDim[dim] !== undefined;

  const addChip = (c: ScopeChip) => {
    setChips((prev) => {
      const filtered = prev.filter((x) => x.dim !== c.dim);
      return [...filtered, c];
    });
  };

  const removeChip = (dim: DimKey) => {
    setChips((prev) => prev.filter((x) => x.dim !== dim));
  };

  /** Which dim types can still be added. A dim is excluded when:
   *   - It's already present as a chip.
   *   - Or picking the "primary" (customer / stock) implies this dim.
   *     In practice we still allow the sibling to be added since the
   *     user explicitly asked for the ability to compound, but the
   *     siblings read as redundant. We leave them enabled; the
   *     dropdown is just filtered to one option. */
  const availableDims: DimKey[] = (
    ["agent", "telesales", "sales_rep", "route", "customer", "category", "stock"] as DimKey[]
  ).filter((d) => !alreadyAdded(d));

  // -----------------------------------------------------------------
  // Dropdown data for each pending-dim picker. Filtered by the chips
  // already set so we never offer an "always zero" compound target.
  // -----------------------------------------------------------------

  // Current customer-side filters (used to narrow the Customer AsyncSelect).
  const customerFilters = useMemo(
    () => ({
      route: chipByDim.route?.value ?? null,
      telesale: chipByDim.telesales?.value ?? null,
      sales_rep: chipByDim.sales_rep?.value ?? null,
    }),
    [chipByDim.route, chipByDim.telesales, chipByDim.sales_rep],
  );

  const categoryFilter = chipByDim.category?.value ?? null;

  // When a Customer chip is set, siblings only have one valid value.
  // We lazy-load the customer's assignment the first time any of
  // these pickers open after picking a customer.
  const [customerAssignment, setCustomerAssignment] = useState<{
    account: string;
    route: string | null;
    telesale: string | null;
    salesrep_code: string | null;
  } | null>(null);

  useEffect(() => {
    const custChip = chipByDim.customer;
    if (!custChip) {
      setCustomerAssignment(null);
      return;
    }
    if (customerAssignment?.account === custChip.value) return;
    let cancelled = false;
    api
      .getCustomerAssignment(custChip.value)
      .then((a) => {
        if (cancelled) return;
        setCustomerAssignment({
          account: a.account,
          route: a.route,
          telesale: a.telesale,
          salesrep_code: a.salesrep_code,
        });
      })
      .catch(() => {
        /* non-fatal — picker just falls back to the full list */
      });
    return () => {
      cancelled = true;
    };
  }, [chipByDim.customer, customerAssignment]);

  const effectiveTelesaleOpts = useMemo(() => {
    if (customerAssignment?.telesale) {
      return [{ value: customerAssignment.telesale, label: customerAssignment.telesale }];
    }
    return telesaleOpts;
  }, [telesaleOpts, customerAssignment]);

  const effectiveSalesRepOpts = useMemo(() => {
    if (customerAssignment?.salesrep_code) {
      const code = customerAssignment.salesrep_code;
      return salesRepOpts.filter((r) => r.code === code);
    }
    return salesRepOpts;
  }, [salesRepOpts, customerAssignment]);

  const effectiveRouteOpts = useMemo(() => {
    if (customerAssignment?.route) {
      return [{ value: customerAssignment.route, label: customerAssignment.route }];
    }
    return routeOpts;
  }, [routeOpts, customerAssignment]);

  // -----------------------------------------------------------------
  // AsyncSelect loaders — Customer honours route / telesales /
  // sales_rep chips; Stock honours Category chip.
  // -----------------------------------------------------------------
  const loadCustomers = async (q: string): Promise<Option[]> => {
    try {
      const results = await api.searchCustomers(q, 50, customerFilters);
      return results.map((c) => ({
        value: c.account,
        label: c.name ? `${c.account} — ${c.name}` : c.account,
      }));
    } catch {
      return [];
    }
  };

  const loadStocks = async (q: string): Promise<Option[]> => {
    try {
      const results = await api.searchInventory(q, { category: categoryFilter });
      const seen = new Set<string>();
      const out: Option[] = [];
      for (const r of results) {
        if (seen.has(r.code)) continue;
        seen.add(r.code);
        out.push({
          value: r.code,
          label: r.description ? `${r.code} — ${r.description}` : r.code,
        });
      }
      return out;
    } catch {
      return [];
    }
  };

  // -----------------------------------------------------------------
  // Validation + save
  // -----------------------------------------------------------------
  const scopeIsSet = chips.length > 0;
  const litresNum = parseFloat(targetLitres);
  const litresOk = !isNaN(litresNum) && litresNum > 0;
  const customRangeOk =
    period !== "CUSTOM" || (!!effectiveTo && effectiveTo >= effectiveFrom);
  const canSave = scopeIsSet && litresOk && customRangeOk && !saving;

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    // Agent chip carries both the stable id (_rtblAgents.idAgents) and
    // the display name. We send both: `agent_id` drives measurement
    // (immune to casing drift); `username` is the display mirror.
    const agentChip = chipByDim.agent;
    const payload: TargetUpsert = {
      agent_id: agentChip?.agentId ?? null,
      username: agentChip?.value ?? null,
      telesale: chipByDim.telesales?.value ?? null,
      sales_rep: chipByDim.sales_rep?.value ?? null,
      route: chipByDim.route?.value ?? null,
      customer_account: chipByDim.customer?.value ?? null,
      category: chipByDim.category?.value ?? null,
      stock_code: chipByDim.stock?.value ?? null,
      period,
      target_litres: litresNum,
      effective_from: effectiveFrom,
      effective_to: effectiveTo || null,
    };
    try {
      if (isEdit && initial) {
        await api.updateTarget(initial.id, payload);
      } else {
        await api.createTarget(payload);
      }
      onSaved();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  // -----------------------------------------------------------------
  // Picker for the currently-pending dim. Each picker commits its
  // selection via addChip and then clears `pending`.
  // -----------------------------------------------------------------
  const commitPickerValue = (dim: DimKey, value: string, label: string, extras?: ScopeChip[]) => {
    addChip({ dim, value, label });
    if (extras) {
      for (const e of extras) addChip(e);
    }
    setPending(null);
  };

  const renderPicker = (dim: DimKey) => {
    const commonProps: any = {
      autoFocus: true,
      isSearchable: true,
      isClearable: false,
      placeholder: `Select ${DIM_LABEL[dim].toLowerCase()}…`,
      styles: selectStyles,
      menuPortalTarget: document.body,
      menuIsOpen: true,
    };

    if (dim === "agent") {
      // Each option carries the agent's id in a side channel so save
      // can send `agent_id` (stable) alongside `username` (display).
      const options = agentOpts.map((a) => ({
        value: a.name,
        label: a.name,
        agentId: a.id,
      }));
      return (
        <Select
          {...commonProps}
          options={options}
          onChange={(opt) => {
            if (!opt) return;
            const o = opt as Option & { agentId: number };
            addChip({ dim, value: o.value, label: o.label, agentId: o.agentId });
            setPending(null);
          }}
        />
      );
    }
    if (dim === "telesales") {
      return (
        <Select
          {...commonProps}
          options={effectiveTelesaleOpts}
          onChange={(opt) => opt && commitPickerValue(dim, (opt as Option).value, (opt as Option).label)}
        />
      );
    }
    if (dim === "sales_rep") {
      return (
        <Select
          {...commonProps}
          options={effectiveSalesRepOpts.map((r) => ({
            value: r.code,
            label: r.name ? `${r.code} — ${r.name}` : r.code,
          }))}
          onChange={(opt) => opt && commitPickerValue(dim, (opt as Option).value, (opt as Option).label)}
        />
      );
    }
    if (dim === "route") {
      return (
        <Select
          {...commonProps}
          options={effectiveRouteOpts}
          onChange={(opt) => opt && commitPickerValue(dim, (opt as Option).value, (opt as Option).label)}
        />
      );
    }
    if (dim === "customer") {
      return (
        <AsyncSelect
          {...commonProps}
          cacheOptions
          defaultOptions
          loadOptions={loadCustomers}
          // Picking a customer auto-fills route/telesales/sales-rep
          // chips (unless those slots are already filled) so the user
          // sees the full compound filter at a glance.
          onChange={async (opt) => {
            if (!opt) return;
            const o = opt as Option;
            // Fetch the customer's assignment so we can seed sibling chips.
            try {
              const a = await api.getCustomerAssignment(o.value);
              const extras: ScopeChip[] = [];
              if (a.route && !alreadyAdded("route"))
                extras.push({ dim: "route", value: a.route, label: a.route });
              if (a.telesale && !alreadyAdded("telesales"))
                extras.push({ dim: "telesales", value: a.telesale, label: a.telesale });
              if (a.salesrep_code && !alreadyAdded("sales_rep"))
                extras.push({
                  dim: "sales_rep",
                  value: a.salesrep_code,
                  label: a.salesrep_code,
                });
              commitPickerValue(dim, o.value, o.label, extras);
            } catch {
              commitPickerValue(dim, o.value, o.label);
            }
          }}
        />
      );
    }
    if (dim === "category") {
      return (
        <Select
          {...commonProps}
          options={categoryOpts}
          onChange={(opt) => opt && commitPickerValue(dim, (opt as Option).value, (opt as Option).label)}
        />
      );
    }
    if (dim === "stock") {
      return (
        <AsyncSelect
          {...commonProps}
          cacheOptions
          defaultOptions
          loadOptions={loadStocks}
          // Picking a stock auto-fills Category if not already set —
          // picking BUT500 implicitly means "DAIRY", and showing that
          // chip makes the compound filter transparent.
          onChange={async (opt) => {
            if (!opt) return;
            const o = opt as Option;
            try {
              const results = await api.searchInventory(o.value, {
                category: null,
              });
              const match = results.find((r) => r.code === o.value);
              const extras: ScopeChip[] = [];
              if (match?.category && !alreadyAdded("category")) {
                extras.push({ dim: "category", value: match.category, label: match.category });
              }
              commitPickerValue(dim, o.value, o.label, extras);
            } catch {
              commitPickerValue(dim, o.value, o.label);
            }
          }}
        />
      );
    }
    return null;
  };

  // -----------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------
  // Defensive — the parent (PerformanceScreen) already hides the
  // Add / Edit buttons for AGENT, so this modal shouldn't ever mount
  // under an AGENT session. If something slips through (deep link,
  // dev tool, future caller), refuse to render the form. Placed after
  // all hooks so the Rules of Hooks hold if the role toggles mid-
  // mount.
  if (role !== "SUPERVISOR" && role !== "ADMIN") {
    return null;
  }
  return (
    // Backdrop no longer closes on click — too easy to lose a half-filled
    // target by accident. Only the X button + Esc close the modal. The
    // inner stopPropagation is kept as a defensive guard in case the
    // backdrop ever gets a click handler again.
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4">
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white rounded shadow-lg w-full max-w-3xl max-h-[92vh] flex flex-col"
      >
        <div className="flex items-start justify-between px-4 py-2 border-b">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-900">
              {isEdit ? "Edit target" : "Add target"}
            </div>
            <div className="text-xs text-gray-600 mt-0.5">
              Build the scope by adding one or more filters. Add a Period and
              a litres value. Save.
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

        <div className="p-4 flex flex-col gap-3 overflow-auto">
          {loadingOptions ? (
            <div className="h-24 flex items-center justify-center text-sm text-gray-500">
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              Loading options…
            </div>
          ) : (
            <>
              {optionsError && (
                <div className="p-2 bg-amber-50 border border-amber-200 text-amber-800 rounded text-xs">
                  Some option lists failed to load. ({optionsError})
                </div>
              )}

              <section className="border rounded p-3">
                <div className="flex items-center justify-between mb-2">
                  <div className="text-[11px] uppercase text-gray-500 font-semibold">
                    Scope — add filters
                  </div>
                  {chips.length > 0 && (
                    <button
                      onClick={() => setChips([])}
                      className="text-[11px] text-gray-500 hover:text-red-600 inline-flex items-center gap-1"
                    >
                      <Trash2 className="w-3 h-3" />
                      Clear all
                    </button>
                  )}
                </div>

                {/* Chips row */}
                <div className="flex flex-wrap items-center gap-1.5 min-h-[32px]">
                  {chips.map((c) => (
                    <span
                      key={c.dim}
                      className="inline-flex items-center gap-1 bg-blue-50 border border-blue-200 text-blue-900 rounded px-2 py-1 text-xs"
                      title={DIM_HINT[c.dim]}
                    >
                      <span className="text-blue-600 font-semibold">{DIM_LABEL[c.dim]}:</span>
                      <span className="font-mono">{c.label}</span>
                      <button
                        onClick={() => removeChip(c.dim)}
                        className="ml-0.5 text-blue-500 hover:text-red-600"
                        title="Remove filter"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}

                  {/* Add-filter button. The menu itself is rendered
                      via a portal (see AddFilterMenu below) so the
                      modal's `overflow-auto` section can't clip the
                      bottom options (Stock was getting hidden when
                      the menu opened near the modal's bottom edge). */}
                  {availableDims.length > 0 && !pending && (
                    <button
                      ref={addFilterBtnRef}
                      onClick={() => setShowAddMenu((v) => !v)}
                      className="inline-flex items-center gap-1 px-2 py-1 text-xs text-blue-700 bg-white border border-dashed border-blue-400 rounded hover:bg-blue-50"
                    >
                      <Plus className="w-3 h-3" />
                      Add filter
                    </button>
                  )}
                </div>

                {/* The pending picker — shows inline below the chip row. */}
                {pending && (
                  <div className="mt-2 border rounded p-2 bg-gray-50">
                    <div className="flex items-center justify-between mb-1">
                      <div className="text-[11px] text-gray-600">
                        <span className="font-semibold text-gray-900">
                          {DIM_LABEL[pending]}
                        </span>{" "}
                        — {DIM_HINT[pending]}
                      </div>
                      <button
                        onClick={() => setPending(null)}
                        className="text-[11px] text-gray-500 hover:text-gray-800"
                      >
                        Cancel
                      </button>
                    </div>
                    {renderPicker(pending)}
                  </div>
                )}

                {!scopeIsSet && !pending && (
                  <div className="mt-2 text-[11px] text-amber-700">
                    Add at least one filter.
                  </div>
                )}
              </section>

              <section className="border rounded p-2">
                <div className="text-[11px] uppercase text-gray-500 font-semibold mb-2">
                  Measurement
                </div>
                <div className="grid grid-cols-2 gap-2">
                  <Field label="Period">
                    <Select
                      options={PERIOD_OPTIONS}
                      value={PERIOD_OPTIONS.find((o) => o.value === period) ?? null}
                      onChange={(opt) =>
                        opt && setPeriod((opt as (typeof PERIOD_OPTIONS)[number]).value)
                      }
                      isSearchable={false}
                      styles={selectStyles}
                      menuPortalTarget={document.body}
                    />
                  </Field>
                  <Field label="Target (litres)">
                    {/* Grow as the number gets longer so even a 7-digit
                        target stays fully visible. Same pattern as the
                        line-items Qty column: digits × monospace glyph
                        width + input chrome, floored at the original
                        96 px so small numbers look identical to before. */}
                    <input
                      type="text"
                      inputMode="numeric"
                      pattern="[0-9]*"
                      value={targetLitres}
                      onChange={(e) =>
                        setTargetLitres(e.target.value.replace(/[^\d.]/g, ""))
                      }
                      style={{
                        width: Math.max(56, targetLitres.length * 9 + 20),
                      }}
                      className="px-2 py-2 border border-gray-300 rounded text-base text-right focus:border-blue-500 focus:ring-1 focus:ring-blue-500 font-mono font-semibold transition-[width] duration-150"
                    />
                  </Field>
                  <Field label="Effective from">
                    <DateInput value={effectiveFrom} onChange={setEffectiveFrom} />
                  </Field>
                  <Field
                    label={
                      period === "CUSTOM"
                        ? "Effective to (window end)"
                        : "Effective to (optional)"
                    }
                  >
                    <DateInput value={effectiveTo} onChange={setEffectiveTo} />
                  </Field>
                </div>
                {period === "CUSTOM" && !effectiveTo && (
                  <div className="mt-2 text-[11px] text-amber-700">
                    CUSTOM period needs an end date — that's the window the
                    target measures over.
                  </div>
                )}
                {!!effectiveTo && effectiveTo < effectiveFrom && (
                  <div className="mt-2 text-[11px] text-red-700">
                    Effective to must be on or after effective from.
                  </div>
                )}
              </section>
            </>
          )}

          {error && (
            <div className="p-2 bg-red-50 border border-red-200 text-red-800 rounded flex items-start text-xs">
              <XCircle className="w-3.5 h-3.5 mr-1.5 flex-shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-4 py-2 border-t bg-gray-50">
          <button
            onClick={onClose}
            disabled={saving}
            className="px-3 py-1 text-xs text-gray-700 hover:bg-gray-100 rounded"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!canSave}
            className="inline-flex items-center gap-1 px-3 py-1 text-xs font-semibold bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {saving ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Save className="w-3 h-3" />
            )}
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create target"}
          </button>
        </div>
      </div>

      {/* Portaled Add-filter menu. Rendered to document.body so the
          modal's `overflow-auto` doesn't clip the lower options. */}
      {showAddMenu && !pending && (
        <AddFilterMenu
          anchorRef={addFilterBtnRef}
          onClose={() => setShowAddMenu(false)}
          availableDims={availableDims}
          onPick={(d) => {
            setShowAddMenu(false);
            setPending(d);
          }}
        />
      )}
    </div>
  );
}

/** Portaled popover for the Add-filter button. Positioned with `fixed`
 *  coords read off the trigger button's bounding rect; recalculates on
 *  mount + on window scroll/resize so it tracks even when the modal's
 *  scroll area moves. Flips to open UPWARD when there isn't enough
 *  room below (near the bottom of the modal). */
function AddFilterMenu({
  anchorRef,
  availableDims,
  onPick,
  onClose,
}: {
  anchorRef: React.RefObject<HTMLButtonElement>;
  availableDims: DimKey[];
  onPick: (d: DimKey) => void;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number; openUp: boolean } | null>(null);

  // Estimated menu height — number of options × ~34px row + 8px
  // padding. Used only to decide whether to flip; the real rendered
  // size takes over once the DOM lands.
  const estimatedHeight = availableDims.length * 34 + 8;
  const MENU_WIDTH = 256;

  useLayoutEffect(() => {
    const compute = () => {
      const btn = anchorRef.current;
      if (!btn) return;
      const r = btn.getBoundingClientRect();
      const spaceBelow = window.innerHeight - r.bottom;
      const openUp = spaceBelow < estimatedHeight + 8 && r.top > estimatedHeight + 8;
      // Clamp horizontally so a right-edge trigger doesn't push the
      // menu off-screen.
      const left = Math.min(r.left, window.innerWidth - MENU_WIDTH - 8);
      const top = openUp ? r.top - estimatedHeight - 4 : r.bottom + 4;
      setPos({ top, left: Math.max(8, left), openUp });
    };
    compute();
    window.addEventListener("scroll", compute, true);
    window.addEventListener("resize", compute);
    return () => {
      window.removeEventListener("scroll", compute, true);
      window.removeEventListener("resize", compute);
    };
  }, [anchorRef, estimatedHeight]);

  // Outside-click closes. Can't rely on React's onMouseLeave because
  // the user may click somewhere else entirely.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current?.contains(target)) return;
      if (anchorRef.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [anchorRef, onClose]);

  if (!pos) return null;

  return createPortal(
    // stopPropagation kept as defensive hygiene: React portals bubble
    // events through the React component tree, not the DOM tree. The
    // modal backdrop no longer has an onClick, but any ancestor that
    // DID would otherwise see this menu's clicks.
    <div
      ref={menuRef}
      style={{ position: "fixed", top: pos.top, left: pos.left, width: MENU_WIDTH, zIndex: 10000 }}
      className="bg-white border border-gray-200 rounded shadow-lg py-1"
      onClick={(e) => e.stopPropagation()}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {availableDims.map((d) => (
        <button
          key={d}
          onClick={() => onPick(d)}
          className="w-full text-left px-3 py-1.5 text-xs hover:bg-blue-50"
        >
          <div className="font-semibold text-gray-800">{DIM_LABEL[d]}</div>
          <div className="text-[10px] text-gray-500">{DIM_HINT[d]}</div>
        </button>
      ))}
    </div>,
    document.body,
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-[11px] font-semibold text-gray-700 mb-1">
        {label}
      </label>
      {children}
    </div>
  );
}
