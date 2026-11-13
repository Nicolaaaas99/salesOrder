import { useState } from "react";
import { AlertTriangle } from "lucide-react";
import type { CustomerDetail } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import { confirmDialog } from "./ConfirmDialog";
import { canChooseWarehouse, shortWarehouse } from "../utils/warehouse";
import { extractErrorMsg } from "../utils/format";

/** Blocking pop-up shown when the customer's DC (customer card) is not the
 *  warehouse a NEW order is being captured from. It covers the screen until
 *  it's resolved, so no lines can be added in the meantime (2026-11-12):
 *    - SUPERVISOR / ADMIN: switch to the customer's DC, or set the customer
 *      card to their own DC when the card is wrong.
 *    - everyone: pick another customer.
 *  Continue / Save / Save draft and the server block as well. Customers with
 *  a blank / unknown DC on their card show nothing. */
export function WarehouseMismatchBanner({
  customer,
  onCustomerUpdated,
  onPickAnother,
}: {
  customer: CustomerDetail | null;
  /** Called with the refreshed customer after its card DC was fixed. */
  onCustomerUpdated?: (c: CustomerDetail) => void;
  /** Clears the customer so another one can be picked. */
  onPickAnother?: () => void;
}) {
  const { defaultWarehouseCode, role, setDefaultWarehouseCode } = useAuth();
  const [switching, setSwitching] = useState(false);
  const [fixing, setFixing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const theirs = customer?.dc_warehouse_code ?? null;
  if (!customer || !theirs || !defaultWarehouseCode) return null;
  if (theirs.toUpperCase() === defaultWarehouseCode.toUpperCase()) return null;

  const canFix = canChooseWarehouse(role);
  const busy = switching || fixing;
  const ours = shortWarehouse(defaultWarehouseCode);
  const custDc = shortWarehouse(theirs);

  async function switchNow() {
    setSwitching(true);
    setError(null);
    try {
      await setDefaultWarehouseCode(theirs!);
    } catch (e) {
      setError(extractErrorMsg(e, "Couldn't switch warehouse"));
    } finally {
      setSwitching(false);
    }
  }

  // The card is wrong and the warehouse is right: set the customer's DC to
  // the warehouse being captured from (audited on the server).
  async function fixCard() {
    const ok = await confirmDialog({
      title: `Change ${customer!.name}'s DC to ${ours}?`,
      message: (
        <>
          This updates the customer card in Evolution: Dist Centre{" "}
          <strong>{customer!.dist_centre || "(blank)"}</strong> becomes the DC of{" "}
          <strong>{defaultWarehouseCode}</strong>. Do this only when the customer really is served from {ours}.
          The change is logged.
        </>
      ),
      confirmLabel: "Change DC",
    });
    if (!ok) return;
    setFixing(true);
    setError(null);
    try {
      const updated = await api.fixCustomerDistCentre(customer!.account, defaultWarehouseCode!);
      onCustomerUpdated?.(updated);
    } catch (e) {
      setError(extractErrorMsg(e, "Couldn't update the customer's DC"));
    } finally {
      setFixing(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" role="dialog" aria-modal="true">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md overflow-hidden">
        <div className="px-4 py-3 border-b bg-amber-50 flex items-center gap-2">
          <AlertTriangle className="w-5 h-5 text-amber-600 flex-shrink-0" />
          <h2 className="text-sm font-semibold text-gray-900">Wrong warehouse for this customer</h2>
        </div>
        <div className="px-4 py-3 text-sm text-gray-700 space-y-2">
          <p>
            <strong>{customer.name}</strong> is on DC <strong>{customer.dist_centre}</strong> ({custDc}), but you're
            capturing from <strong>{ours}</strong>.
          </p>
          <p className="text-gray-600">
            {canFix
              ? `Switch to ${custDc}, or if the customer card is wrong, set the customer to ${ours}.`
              : "Only a supervisor can fix this. Pick another customer, or ask a supervisor to capture this order."}
          </p>
          {error && <p className="text-red-700 text-xs">{error}</p>}
        </div>
        <div className="px-4 py-3 border-t bg-gray-50 flex flex-wrap items-center justify-end gap-2 text-sm">
          {onPickAnother && (
            <button
              type="button"
              onClick={onPickAnother}
              disabled={busy}
              className="mr-auto px-3 py-1.5 rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-50 disabled:opacity-60"
            >
              Pick another customer
            </button>
          )}
          {canFix && onCustomerUpdated && (
            <button
              type="button"
              onClick={fixCard}
              disabled={busy}
              title={`The customer card is wrong: set their DC to ${ours}`}
              className="px-3 py-1.5 rounded border border-amber-600 text-amber-800 bg-white font-semibold hover:bg-amber-50 disabled:opacity-60"
            >
              {fixing ? "Updating…" : `Set customer to ${ours}`}
            </button>
          )}
          {canFix && (
            <button
              type="button"
              onClick={switchNow}
              disabled={busy}
              title={`Capture this order from ${theirs}, the customer's DC`}
              className="px-3 py-1.5 rounded bg-amber-600 text-white font-semibold hover:bg-amber-700 disabled:opacity-60"
            >
              {switching ? "Switching…" : `Switch to ${custDc}`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
