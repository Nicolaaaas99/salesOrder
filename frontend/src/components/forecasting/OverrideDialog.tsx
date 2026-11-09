import { useState } from "react";
import { forecastingApi, type WorkbenchRow } from "../../api/forecasting";
import { extractErrorMsg } from "../../utils/format";
import { ErrorBanner, fmtQty } from "./ui";

/** Manual target for one customer-product in one week. Pinned: the rest of
 *  every target it sits in rebalances around it. Clearing it goes back to
 *  the scenario value (or the forecast, if no scenario covers it any more). */
export function OverrideDialog({
  row,
  customerLabel,
  onClose,
  onSaved,
}: {
  row: WorkbenchRow;
  customerLabel: string;
  onClose: () => void;
  onSaved: (message: string) => void;
}) {
  const [qty, setQty] = useState<string>(String(row.override ?? row.target));
  const [reason, setReason] = useState("");
  const [breakLock, setBreakLock] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const product = `${row.key}${row.label && row.label !== row.key ? ` ${row.label}` : ""}`;

  async function save(clear: boolean) {
    const n = Number(qty);
    if (!clear && (qty.trim() === "" || !isFinite(n) || n < 0)) return setError("Enter a quantity of 0 or more.");
    if (!clear && !reason.trim()) return setError("Add a reason so others know why the target changed.");
    if (row.is_locked && !breakLock) return setError("Tick the box above to change a locked target.");
    setSaving(true);
    setError(null);
    try {
      const saved = await forecastingApi.overrideTarget(row.target_id!, {
        override_qty: clear ? null : n,
        reason: reason.trim() || "Manual target cleared",
        break_lock: breakLock,
      });
      onSaved(clear
        ? `Manual target cleared on ${product}. It is back to the scenario value: ${fmtQty(saved.target_qty)}.`
        : `Target for ${product} set to ${fmtQty(saved.target_qty)}.`);
    } catch (e: any) {
      // 410: clearing removed the row -- no active scenario covers it any more.
      if (e?.response?.status === 410) {
        return onSaved(`Manual target cleared on ${product}. No target covers it any more, ` +
                       `so it now follows the forecast (${fmtQty(row.forecast)}).`);
      }
      setError(extractErrorMsg(e, "Failed to save the override."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-5 space-y-3" onClick={(e) => e.stopPropagation()}>
        <div>
          <h3 className="font-semibold text-gray-900">Set this product's target</h3>
          <p className="text-xs text-gray-600 mt-0.5">
            {customerLabel} · <span className="font-mono">{row.key}</span> {row.label !== row.key && row.label}
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-xs">
          <div><div className="text-gray-500">Forecast</div><div className="font-mono">{fmtQty(row.forecast)}</div></div>
          <div><div className="text-gray-500">Current target</div><div className="font-mono">{fmtQty(row.target)}</div></div>
          <div><div className="text-gray-500">Committed</div><div className="font-mono">{fmtQty(row.committed)}</div></div>
        </div>
        <p className="text-[11px] text-gray-500">
          A manual target is fixed: the other products in the same targets share the difference, so their totals still hold.
        </p>
        <label className="block text-xs">
          <span className="text-gray-700">New target (units for the week)</span>
          <input type="number" min={0} step="any" className="input w-full mt-1" value={qty}
                 onChange={(e) => setQty(e.target.value)} />
        </label>
        <label className="block text-xs">
          <span className="text-gray-700">Reason{row.override != null && <span className="text-gray-400"> (optional when clearing)</span>}</span>
          <input className="input w-full mt-1" value={reason} maxLength={500}
                 placeholder="e.g. customer confirmed a promotion" onChange={(e) => setReason(e.target.value)} />
        </label>
        {row.is_locked && (
          <label className="flex items-start gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2">
            <input type="checkbox" checked={breakLock} onChange={(e) => setBreakLock(e.target.checked)} />
            <span>This week has started and the target is locked. Tick to change it anyway — the change is audited.</span>
          </label>
        )}
        <ErrorBanner message={error} />
        <div className="flex justify-between gap-2 pt-1">
          {row.override != null ? (
            <button className="btn btn-secondary text-xs text-amber-800 border-amber-300 hover:bg-amber-50"
                    disabled={saving}
                    title="Remove the manual target and go back to the scenario value"
                    onClick={() => save(true)}>
              Clear manual target
            </button>
          ) : <span />}
          <div className="flex gap-2">
            <button className="btn btn-ghost text-xs" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary text-xs" disabled={saving || (row.is_locked && !breakLock)}
                    onClick={() => save(false)}>
              {saving ? "Saving…" : "Save target"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
