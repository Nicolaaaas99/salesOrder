import { useEffect, useMemo, useRef, useState } from "react";
import Select from "react-select";
import { AlertTriangle, Loader2, Save, X, XCircle } from "lucide-react";
import { api } from "../api/client";
import type { CustomerAssignment, SalesRepOption } from "../api/types";

type Props = {
  /** One or more customer accounts. Length 1 → single-customer mode
   *  (dropdowns pre-filled with current values). Length > 1 → bulk mode
   *  (dropdowns blank, per-field "Change this field" toggle required,
   *  single transaction over all N on save). */
  accounts: string[];
  onSaved: () => void;
  onClose: () => void;
};

type Option = { value: string; label: string };

/** Over this many customers, require an extra confirm before firing the
 *  bulk UPDATE. Guards against a misplaced click on "Select all". */
const BULK_CONFIRM_THRESHOLD = 100;

/** Compact react-select styling that matches the look used elsewhere in
 *  the app (DeliveriesScreen uses the same shape). */
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
  menu: (base: any) => ({
    ...base,
    borderRadius: "6px",
    border: "1px solid #e5e7eb",
    boxShadow: "0 4px 6px -1px rgba(0, 0, 0, 0.1)",
    fontSize: "13px",
    zIndex: 60,
  }),
  // Portaled menu (menuPortalTarget={document.body}) needs its own
  // z-index — the modal backdrop is z-50 and the portaled menu lands
  // outside the modal's stacking context. Without this it renders behind
  // the modal and the user can't click the options.
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

export function CustomerAssignmentModal({ accounts, onSaved, onClose }: Props) {
  const isBulk = accounts.length > 1;
  const singleAccount = accounts[0];

  // Prefill payload — fetched only in single mode (we don't fetch N
  // customers for bulk; the user explicitly ticks what to change).
  const [current, setCurrent] = useState<CustomerAssignment | null>(null);
  const [routeOptions, setRouteOptions] = useState<Option[]>([]);
  const [telesaleOptions, setTelesaleOptions] = useState<Option[]>([]);
  const [salesRepOptions, setSalesRepOptions] = useState<Option[]>([]);
  const [salesRepsById, setSalesRepsById] = useState<Map<number, SalesRepOption>>(
    new Map(),
  );

  // Form state — strings so react-select handles them uniformly. For
  // salesrep we store the stringified id and resolve to number at submit.
  // In bulk mode, blank means "don't change this field for any
  // customer"; a picked value means "set it to this for all". Same input
  // mental model as single mode — no separate "Change this field"
  // toggles needed.
  const [routeValue, setRouteValue] = useState<Option | null>(null);
  const [telesaleValue, setTelesaleValue] = useState<Option | null>(null);
  const [salesRepValue, setSalesRepValue] = useState<Option | null>(null);
  // On-hold uses a 2 (single) / 3 (bulk) option segmented control.
  //   • single mode: "active" | "on_hold" — prefilled with current state
  //   • bulk mode:   "keep" | "active" | "on_hold" — defaults to "keep"
  //                  which means "don't change On_Hold on any customer"
  const [onHoldChoice, setOnHoldChoice] = useState<"keep" | "active" | "on_hold">(
    isBulk ? "keep" : "active",
  );
  const [note, setNote] = useState("");

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Over-threshold confirmation gate. True = extra click required before
  // bulk commit actually fires.
  const [confirming, setConfirming] = useState(false);

  // Fetch everything in parallel on mount. Ignore results if the modal
  // closes before they resolve.
  const cancelledRef = useRef(false);
  useEffect(() => {
    cancelledRef.current = false;
    setLoading(true);
    setError(null);
    const assignmentPromise = isBulk
      ? Promise.resolve(null)
      : api.getCustomerAssignment(singleAccount);
    Promise.all([
      assignmentPromise,
      api.listRouteOptions(),
      api.listTelesaleOptions(),
      api.listSalesReps(),
    ])
      .then(([curr, routes, telesales, reps]) => {
        if (cancelledRef.current) return;
        setCurrent(curr);
        setRouteOptions(routes.map((r) => ({ value: r.route, label: r.route })));
        setTelesaleOptions(
          telesales.map((t) => ({ value: t.telesale, label: t.telesale })),
        );
        // Backend already filters out code-less reps; still, guard here
        // so a stale response can't produce a blank-label option.
        setSalesRepOptions(
          reps
            .filter((r) => r.code && r.code.trim())
            .map((r) => ({
              value: String(r.id),
              label: r.name ? `${r.code} · ${r.name}` : r.code,
            })),
        );
        setSalesRepsById(new Map(reps.map((r) => [r.id, r])));

        // Prefill in single mode only. In bulk the fields start blank.
        // For salesrep we only prefill when the customer's RepID
        // resolves to a readable Code — an orphan RepID (points at a
        // missing/code-less SalesRep row) renders blank, which honestly
        // reflects "no usable rep assigned" rather than inventing a
        // placeholder label.
        if (curr) {
          setRouteValue(
            curr.route ? { value: curr.route, label: curr.route } : null,
          );
          setTelesaleValue(
            curr.telesale ? { value: curr.telesale, label: curr.telesale } : null,
          );
          if (curr.salesrep_id != null && curr.salesrep_code) {
            setSalesRepValue({
              value: String(curr.salesrep_id),
              label: curr.salesrep_code,
            });
          }
          setOnHoldChoice(curr.on_hold ? "on_hold" : "active");
        }
      })
      .catch((e: any) => {
        if (cancelledRef.current) return;
        setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load.");
      })
      .finally(() => {
        if (!cancelledRef.current) setLoading(false);
      });
    return () => {
      cancelledRef.current = true;
    };
    // We purposely don't put `accounts` in the dep array — the parent
    // sets a brand-new array when it opens the modal; we track the
    // identity via the first account + length instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [singleAccount, accounts.length, isBulk]);

  // Esc closes (same as the transactions modal).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const effectiveRouteOptions = useMemo(() => {
    if (!routeValue || routeOptions.some((o) => o.value === routeValue.value)) {
      return routeOptions;
    }
    return [routeValue, ...routeOptions];
  }, [routeOptions, routeValue]);
  const effectiveTelesaleOptions = useMemo(() => {
    if (!telesaleValue || telesaleOptions.some((o) => o.value === telesaleValue.value)) {
      return telesaleOptions;
    }
    return [telesaleValue, ...telesaleOptions];
  }, [telesaleOptions, telesaleValue]);
  const effectiveRepOptions = useMemo(() => {
    if (!salesRepValue || salesRepOptions.some((o) => o.value === salesRepValue.value)) {
      return salesRepOptions;
    }
    return [salesRepValue, ...salesRepOptions];
  }, [salesRepOptions, salesRepValue]);

  // Save-button enablement rule differs per mode:
  //   • Single: enabled when any value differs from the loaded current state.
  //   • Bulk:   enabled when at least one dropdown has a picked value
  //     (blank means "leave each customer's existing value alone").
  const singleDirty =
    current &&
    ((current.route ?? null) !== (routeValue?.value ?? null) ||
      (current.telesale ?? null) !== (telesaleValue?.value ?? null) ||
      (current.salesrep_id ?? null) !==
        (salesRepValue ? Number(salesRepValue.value) : null) ||
      current.on_hold !== (onHoldChoice === "on_hold"));
  const bulkDirty =
    routeValue !== null ||
    telesaleValue !== null ||
    salesRepValue !== null ||
    onHoldChoice !== "keep";
  const canSave = isBulk ? bulkDirty : singleDirty;

  const needsConfirm = isBulk && accounts.length >= BULK_CONFIRM_THRESHOLD;

  const doSave = async () => {
    setSaving(true);
    setError(null);
    try {
      if (isBulk) {
        // A dropdown with a picked value → `update_*: true`. A dropdown
        // left blank → `update_*: false` (no overwrite). Same semantics
        // as single mode minus the "clear a field" capability (which is
        // intentional for bulk — if you need to NULL a field across N
        // customers, do them one at a time). On-hold "keep" = don't touch.
        await api.bulkUpdateCustomerAssignment({
          accounts,
          update_route: routeValue !== null,
          route: routeValue?.value ?? null,
          update_telesale: telesaleValue !== null,
          telesale: telesaleValue?.value ?? null,
          update_salesrep: salesRepValue !== null,
          salesrep_id: salesRepValue ? Number(salesRepValue.value) : null,
          update_on_hold: onHoldChoice !== "keep",
          on_hold: onHoldChoice === "on_hold",
          note: note.trim() || null,
        });
      } else {
        await api.updateCustomerAssignment(singleAccount, {
          route: routeValue?.value ?? null,
          telesale: telesaleValue?.value ?? null,
          salesrep_id: salesRepValue ? Number(salesRepValue.value) : null,
          on_hold: onHoldChoice === "on_hold",
          note: note.trim() || null,
        });
      }
      onSaved();
      onClose();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveClick = () => {
    if (needsConfirm && !confirming) {
      setConfirming(true);
      return;
    }
    doSave();
  };

  const title = isBulk ? `Edit ${accounts.length} customers` : "Edit customer";
  const subtitle = isBulk
    ? "Pick a value in any field to apply it to every selected customer. Leave a field blank to keep each customer's existing value."
    : null;

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white rounded shadow-lg w-full max-w-md flex flex-col"
      >
        <div className="flex items-start justify-between px-4 py-2 border-b">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-900">{title}</div>
            {isBulk ? (
              <div className="text-xs text-gray-600 mt-0.5">{subtitle}</div>
            ) : (
              <div className="text-xs text-gray-600 mt-0.5 font-mono truncate">
                {singleAccount}
                {current?.name && (
                  <span className="text-gray-500 font-sans"> · {current.name}</span>
                )}
              </div>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-1 rounded hover:bg-gray-100 text-gray-500 flex-shrink-0"
            title="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 flex flex-col gap-3">
          {loading ? (
            <div className="h-24 flex items-center justify-center text-sm text-gray-500">
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              Loading…
            </div>
          ) : (
            <>
              <Field label="Route">
                <Select
                  options={effectiveRouteOptions}
                  value={routeValue}
                  onChange={(opt) => setRouteValue(opt as Option | null)}
                  isSearchable
                  isClearable
                  placeholder={isBulk ? "" : "No route assigned"}
                  styles={selectStyles}
                  menuPortalTarget={document.body}
                />
              </Field>

              <Field label="Telesales">
                <Select
                  options={effectiveTelesaleOptions}
                  value={telesaleValue}
                  onChange={(opt) => setTelesaleValue(opt as Option | null)}
                  isSearchable
                  isClearable
                  placeholder={isBulk ? "" : "No telesales assigned"}
                  styles={selectStyles}
                  menuPortalTarget={document.body}
                />
              </Field>

              <Field label="Sales rep">
                <Select
                  options={effectiveRepOptions}
                  value={salesRepValue}
                  onChange={(opt) => setSalesRepValue(opt as Option | null)}
                  isSearchable
                  isClearable
                  placeholder={isBulk ? "" : "No rep assigned"}
                  styles={selectStyles}
                  menuPortalTarget={document.body}
                  filterOption={(opt, term) => {
                    const t = term.trim().toLowerCase();
                    if (!t) return true;
                    const rep = salesRepsById.get(Number(opt.value));
                    const hay = [opt.label, rep?.code, rep?.name]
                      .filter(Boolean)
                      .join(" ")
                      .toLowerCase();
                    return hay.includes(t);
                  }}
                />
              </Field>

              <Field label="Status">
                <OnHoldToggle
                  value={onHoldChoice}
                  onChange={setOnHoldChoice}
                  includeKeep={isBulk}
                />
              </Field>

              <Field label="Note (optional)">
                <textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value.slice(0, 200))}
                  rows={2}
                  placeholder="Why the reassignment? (e.g. 'customer closed Oct 2026')"
                  className="w-full px-2 py-1 border border-gray-300 rounded text-xs focus:border-blue-500 focus:ring-1 focus:ring-blue-500 resize-none"
                />
                <div className="text-[10px] text-gray-400 mt-0.5 text-right">
                  {note.length} / 200
                </div>
              </Field>
            </>
          )}

          {error && (
            <div className="p-2 bg-red-50 border border-red-200 text-red-800 rounded flex items-start text-xs">
              <XCircle className="w-3.5 h-3.5 mr-1.5 flex-shrink-0 mt-0.5" />
              <span>{error}</span>
            </div>
          )}

          {confirming && (
            <div className="p-2 bg-amber-50 border border-amber-300 text-amber-900 rounded flex items-start text-xs">
              <AlertTriangle className="w-3.5 h-3.5 mr-1.5 flex-shrink-0 mt-0.5" />
              <span>
                You're about to update <strong>{accounts.length}</strong>{" "}
                customers in one go. Click <strong>Confirm bulk edit</strong>{" "}
                to proceed, or Cancel to back out.
              </span>
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
            onClick={handleSaveClick}
            disabled={saving || loading || !canSave}
            className={`inline-flex items-center gap-1 px-3 py-1 text-xs font-semibold text-white rounded disabled:opacity-50 disabled:cursor-not-allowed ${
              confirming
                ? "bg-amber-600 hover:bg-amber-700"
                : "bg-blue-600 hover:bg-blue-700"
            }`}
          >
            {saving ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Save className="w-3 h-3" />
            )}
            {saving
              ? "Saving…"
              : confirming
                ? `Confirm bulk edit · ${accounts.length}`
                : isBulk
                  ? `Apply to ${accounts.length} customers`
                  : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-xs font-semibold text-gray-700 mb-1">{label}</label>
      {children}
    </div>
  );
}

/** Segmented control for the On_Hold flag. Matches the Analytics screen's
 *  "Amount / Litres" toggle styling. In bulk mode an extra "— keep —"
 *  option appears first and is the default, so "no change" is the safe
 *  default and the user has to click explicitly to flip on-hold across
 *  N customers. */
function OnHoldToggle({
  value,
  onChange,
  includeKeep,
}: {
  value: "keep" | "active" | "on_hold";
  onChange: (v: "keep" | "active" | "on_hold") => void;
  includeKeep: boolean;
}) {
  const buttons: { v: typeof value; label: string }[] = [
    ...(includeKeep ? ([{ v: "keep", label: "— keep —" }] as const) : []),
    { v: "active", label: "Active" },
    { v: "on_hold", label: "On hold" },
  ];
  return (
    <div className="inline-flex rounded border border-gray-300 overflow-hidden text-xs">
      {buttons.map((b, i) => (
        <button
          key={b.v}
          type="button"
          onClick={() => onChange(b.v)}
          className={`px-3 py-1 ${i > 0 ? "border-l border-gray-300" : ""} ${
            value === b.v
              ? b.v === "on_hold"
                ? "bg-red-600 text-white font-semibold"
                : "bg-blue-600 text-white font-semibold"
              : "bg-white text-gray-700 hover:bg-gray-50"
          }`}
        >
          {b.label}
        </button>
      ))}
    </div>
  );
}
