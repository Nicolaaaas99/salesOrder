import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, Edit3, Loader2, Plus, Trash2, X, XCircle } from "lucide-react";
import { api } from "../api/client";
import type { TargetWithActual } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { TargetFormModal } from "./TargetFormModal";
import { notifyTargetsMutated } from "./PerformanceStrip";

/** Analytics → Performance & Targets screen.
 *
 *  AGENTs: read-only. Add / Edit / Delete controls are hidden. Backend
 *  also 403s if an AGENT tries to call POST/PATCH/DELETE directly.
 *  The visibility filter on GET /targets already hides other agents'
 *  identity-scoped targets + any telesales / sales rep target. */

type Props = {
  onBack?: () => void;
};

type ScopeFilter =
  | "all"
  | "agent"
  | "telesales"
  | "sales_rep"
  | "route"
  | "customer"
  | "category"
  | "stock"
  | "compound";

const SCOPE_LABEL: Record<ScopeFilter, string> = {
  all: "All",
  agent: "Agent",
  telesales: "Telesales",
  sales_rep: "Sales rep",
  route: "Route",
  customer: "Customer",
  category: "Category",
  stock: "Stock",
  compound: "Compound",
};

const PERIOD_LABEL: Record<string, string> = {
  DAILY: "Daily",
  WEEKLY: "Weekly",
  MONTHLY: "Monthly",
  CUSTOM: "Custom",
};

function fmtInt(n: number): string {
  return Math.round(n).toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function fmtDate(iso: string): string {
  // yyyy-mm-dd → dd/mm/yy
  const [y, m, d] = iso.split("-");
  return y && m && d ? `${d}/${m}/${y.slice(2)}` : iso;
}

/** Describes the scope of a target as a short, scannable row of chips.
 *  "Sales Rep: Vincent · Route: NEL - NELSPRUIT CBD" etc. */
function scopeChips(t: TargetWithActual): { label: string; value: string }[] {
  const chips: { label: string; value: string }[] = [];
  if (t.username || t.agent_id != null) {
    chips.push({ label: "Placed by", value: t.username ?? `#${t.agent_id}` });
  }
  if (t.telesale) chips.push({ label: "Telesales", value: t.telesale });
  if (t.sales_rep) chips.push({ label: "Sales rep", value: t.sales_rep });
  if (t.route) chips.push({ label: "Route", value: t.route });
  if (t.customer_account) chips.push({ label: "Customer", value: t.customer_account });
  if (t.category) chips.push({ label: "Category", value: t.category });
  if (t.stock_code) chips.push({ label: "Stock", value: t.stock_code });
  return chips;
}

export function PerformanceScreen({ onBack }: Props = {}) {
  const { role } = useAuth();
  const canEdit = role === "SUPERVISOR" || role === "ADMIN";
  const [targets, setTargets] = useState<TargetWithActual[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [scopeFilter, setScopeFilter] = useState<ScopeFilter>("all");
  const [activeOnly, setActiveOnly] = useState(true);

  // When truthy, the Add/Edit modal is open. `null` id = creating a
  // new one; a number = editing that target.
  const [modal, setModal] = useState<
    { mode: "create" } | { mode: "edit"; target: TargetWithActual } | null
  >(null);
  // Delete flow: `pending` is what the user clicked, `deletingId` is
  // the one we're currently waiting on the server for. Keeping them
  // separate lets the modal stay open with a spinner on the button
  // without flashing closed on the server round-trip.
  const [pendingDelete, setPendingDelete] = useState<TargetWithActual | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const refetch = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .listTargets({ scope: scopeFilter, activeOnly })
      .then((r) => setTargets(r.targets))
      .catch((e: any) =>
        setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load."),
      )
      .finally(() => setLoading(false));
  }, [scopeFilter, activeOnly]);

  useEffect(() => {
    refetch();
  }, [refetch]);

  const sorted = useMemo(() => {
    if (!targets) return [];
    // Furthest from target first (lowest progress %) so attention
    // naturally sits on what needs it.
    return [...targets].sort((a, b) => a.progress_pct - b.progress_pct);
  }, [targets]);

  const handleConfirmDelete = async () => {
    if (!pendingDelete) return;
    setDeletingId(pendingDelete.id);
    try {
      await api.deleteTarget(pendingDelete.id);
      setPendingDelete(null);
      notifyTargetsMutated();
      await refetch();
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Delete failed.");
      setPendingDelete(null);
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="h-full bg-gray-50 p-2 flex flex-col">
      <div className="max-w-[1400px] mx-auto w-full flex-1 min-h-0 flex flex-col gap-2">
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

        {/* Toolbar: scope filter chips + active-only toggle + Add button */}
        <div className="flex items-center gap-2 flex-wrap flex-shrink-0 bg-white rounded border p-2 shadow-sm">
          <div className="inline-flex rounded border border-gray-300 overflow-hidden text-xs">
            {(Object.keys(SCOPE_LABEL) as ScopeFilter[]).map((k, i) => (
              <button
                key={k}
                onClick={() => setScopeFilter(k)}
                className={`px-2 py-1 ${i > 0 ? "border-l border-gray-300" : ""} ${
                  scopeFilter === k
                    ? "bg-blue-600 text-white font-semibold"
                    : "bg-white text-gray-700 hover:bg-gray-50"
                }`}
              >
                {SCOPE_LABEL[k]}
              </button>
            ))}
          </div>

          <label className="flex items-center gap-1 text-xs text-gray-700 cursor-pointer">
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(e) => setActiveOnly(e.target.checked)}
              className="w-3.5 h-3.5 accent-blue-600"
            />
            Active only
          </label>

          <div className="flex-1" />

          {canEdit && (
            <button
              onClick={() => setModal({ mode: "create" })}
              className="inline-flex items-center gap-1 px-3 py-1 text-xs font-semibold bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              <Plus className="w-3.5 h-3.5" />
              Add target
            </button>
          )}
        </div>

        {error && (
          <div className="p-1 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs flex-shrink-0">
            <XCircle className="w-3 h-3 mr-1 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="bg-white rounded border shadow-sm flex-1 min-h-0 flex flex-col overflow-hidden">
          {loading ? (
            <div className="h-full flex items-center justify-center text-gray-500 text-sm">
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
              Loading…
            </div>
          ) : sorted.length === 0 ? (
            <div className="h-full flex items-center justify-center text-gray-400 text-sm italic">
              {canEdit ? (
                <>
                  No targets. Click <span className="mx-1 font-semibold">Add target</span>{" "}
                  to create one.
                </>
              ) : (
                <>No targets to show.</>
              )}
            </div>
          ) : (
            <div className="flex-1 overflow-auto">
              <table className="w-full text-xs border-collapse">
                <thead className="bg-gray-100 sticky top-0 z-10">
                  <tr className="text-gray-700 border-b border-gray-300">
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Scope</th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Period</th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Window</th>
                    <th className="text-right px-2 py-1.5 font-semibold whitespace-nowrap">Actual</th>
                    <th className="text-right px-2 py-1.5 font-semibold whitespace-nowrap">Target</th>
                    <th className="text-left px-2 py-1.5 font-semibold w-56">Progress</th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Effective</th>
                    <th className="w-28"></th>
                  </tr>
                </thead>
                <tbody>
                  {sorted.map((t) => (
                    <TargetRow
                      key={t.id}
                      target={t}
                      canEdit={canEdit}
                      isDeleting={deletingId === t.id}
                      onEdit={() => setModal({ mode: "edit", target: t })}
                      onDelete={() => setPendingDelete(t)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {modal && (
        <TargetFormModal
          initial={modal.mode === "edit" ? modal.target : null}
          onSaved={async () => {
            setModal(null);
            notifyTargetsMutated();
            await refetch();
          }}
          onClose={() => setModal(null)}
        />
      )}

      {pendingDelete && (
        <DeleteConfirmModal
          target={pendingDelete}
          isDeleting={deletingId === pendingDelete.id}
          onConfirm={handleConfirmDelete}
          onCancel={() => setPendingDelete(null)}
        />
      )}
    </div>
  );
}

function DeleteConfirmModal({
  target,
  isDeleting,
  onConfirm,
  onCancel,
}: {
  target: TargetWithActual;
  isDeleting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const chips = scopeChips(target);

  // Esc cancels (same as the backdrop click), but only when the user
  // isn't mid-delete — Esc during a running request would strand the
  // spinner.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !isDeleting) onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isDeleting, onCancel]);

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={() => !isDeleting && onCancel()}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white rounded shadow-lg w-full max-w-md flex flex-col"
      >
        <div className="flex items-start justify-between px-4 py-2 border-b">
          <div className="flex items-center gap-2 min-w-0">
            <AlertTriangle className="w-4 h-4 text-amber-500 flex-shrink-0" />
            <div className="text-sm font-semibold text-gray-900">Delete target?</div>
          </div>
          <button
            onClick={onCancel}
            disabled={isDeleting}
            className="p-1 rounded hover:bg-gray-100 text-gray-500 flex-shrink-0 disabled:opacity-40"
            title="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-4 flex flex-col gap-3 text-sm">
          <div className="text-gray-700">
            You're about to delete this{" "}
            <span className="font-semibold">
              {PERIOD_LABEL[target.period] ?? target.period}
            </span>{" "}
            target of{" "}
            <span className="font-mono font-semibold text-gray-900">
              {fmtInt(target.target_litres)} L
            </span>
            . This can't be undone.
          </div>
          <div className="rounded border border-gray-200 bg-gray-50 p-2 flex flex-wrap gap-1">
            {chips.length > 0 ? (
              chips.map((c, i) => (
                <span
                  key={i}
                  className="inline-flex items-center gap-1 bg-white border border-slate-200 rounded px-1.5 py-0.5 text-[11px]"
                >
                  <span className="text-gray-500">{c.label}:</span>
                  <span className="font-mono text-gray-800">{c.value}</span>
                </span>
              ))
            ) : (
              <span className="text-[11px] italic text-gray-500">No scope dims</span>
            )}
          </div>
          <div className="text-[11px] text-gray-500">
            Effective {fmtDate(target.effective_from)}
            {" → "}
            {target.effective_to ? fmtDate(target.effective_to) : "open"}
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 px-4 py-2 border-t bg-gray-50">
          <button
            onClick={onCancel}
            disabled={isDeleting}
            className="px-3 py-1 text-xs text-gray-700 hover:bg-gray-100 rounded disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={isDeleting}
            className="inline-flex items-center gap-1 px-3 py-1 text-xs font-semibold bg-red-600 text-white rounded hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isDeleting ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Trash2 className="w-3 h-3" />
            )}
            {isDeleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TargetRow({
  target,
  canEdit,
  isDeleting,
  onEdit,
  onDelete,
}: {
  target: TargetWithActual;
  canEdit: boolean;
  isDeleting: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const pct = Math.max(0, target.progress_pct);
  const barPct = Math.min(100, pct * 100);
  const over = pct >= 1;
  const chips = scopeChips(target);
  // Progress bar colour grading matches the typical "red → amber →
  // green → blue (over)" story: dangerous at 0 %, achieved at ≥ 100 %.
  const barClass = over
    ? "bg-blue-500"
    : pct >= 0.75
    ? "bg-emerald-500"
    : pct >= 0.4
    ? "bg-amber-500"
    : "bg-red-500";

  return (
    <tr className="border-b border-gray-100 hover:bg-blue-50/40">
      <td className="px-2 py-1.5">
        <div className="flex flex-wrap gap-1">
          {chips.map((c, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 bg-slate-100 border border-slate-200 rounded px-1.5 py-0.5 text-[10px]"
              title={`${c.label}: ${c.value}`}
            >
              <span className="text-gray-500">{c.label}:</span>
              <span className="font-mono text-gray-800">{c.value}</span>
            </span>
          ))}
        </div>
      </td>
      <td className="px-2 py-1.5 whitespace-nowrap">{PERIOD_LABEL[target.period] ?? target.period}</td>
      <td className="px-2 py-1.5 whitespace-nowrap text-gray-600 text-[10px]">
        {fmtDate(target.window_from)} → {fmtDate(target.window_to)}
      </td>
      <td className="px-2 py-1.5 text-right font-mono font-semibold text-gray-900">
        {fmtInt(target.actual_litres)}L
      </td>
      <td className="px-2 py-1.5 text-right font-mono text-gray-700">
        {fmtInt(target.target_litres)}L
      </td>
      <td className="px-2 py-1.5">
        <div className="flex items-center gap-2">
          <div className="flex-1 h-2 bg-gray-100 rounded overflow-hidden">
            <div
              className={`h-full ${barClass} transition-all`}
              style={{ width: `${barPct}%` }}
            />
          </div>
          <span className="text-[10px] font-mono font-semibold text-gray-700 w-10 text-right">
            {Math.round(pct * 100)}%
          </span>
        </div>
      </td>
      <td className="px-2 py-1.5 whitespace-nowrap text-gray-600 text-[10px]">
        {fmtDate(target.effective_from)}
        {" → "}
        {target.effective_to ? fmtDate(target.effective_to) : "open"}
      </td>
      <td className="px-2 py-1.5 text-right">
        {canEdit && (
          <div className="inline-flex items-center gap-1">
            <button
              onClick={onEdit}
              disabled={isDeleting}
              className="p-1 rounded hover:bg-blue-100 text-gray-500 hover:text-blue-700 disabled:opacity-40"
              title="Edit"
            >
              <Edit3 className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={onDelete}
              disabled={isDeleting}
              className="p-1 rounded hover:bg-red-100 text-gray-500 hover:text-red-700 disabled:opacity-40"
              title="Delete"
            >
              {isDeleting ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Trash2 className="w-3.5 h-3.5" />
              )}
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}
