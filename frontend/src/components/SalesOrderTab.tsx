import { useEffect, useState } from "react";
import { FilePlus, FolderOpen } from "lucide-react";
import { api } from "../api/client";
import type {
  CustomerDetail,
  SalesOrderDetail,
  SalesOrderSearchResult,
} from "../api/types";
import { SalesOrderScreen } from "./SalesOrderScreen";
import { SalesOrderSearchModal } from "./SalesOrderSearchModal";

// Flip to re-enable the "Create a new order" card. Keeping the code in place
// so flipping back is a one-line change if telesales needs manual entry again.
const CREATE_NEW_ORDER_ENABLED = false;

type Mode =
  | { kind: "landing" }
  | { kind: "searching" }
  | { kind: "new" }
  | {
      kind: "loaded";
      detail: SalesOrderDetail;
      customer: CustomerDetail;
    };

type Props = {
  /** When another tab asks us to open an order, its number lands here. */
  pendingOrderNo?: string | null;
  /** Clear the pending order once we've consumed it so it won't re-fire. */
  clearPendingOrder?: () => void;
  /** Called after a successful save in edit-mode WHEN the edit was launched
   *  from another tab — App.tsx uses this to route the user back to where
   *  they came from instead of leaving them on the Sales Order Entry landing. */
  onEditDone?: () => void;
};

export function SalesOrderTab({
  pendingOrderNo,
  clearPendingOrder,
  onEditDone,
}: Props = {}) {
  const [mode, setMode] = useState<Mode>({ kind: "landing" });
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [formSessionKey, setFormSessionKey] = useState(0);
  // True when the current "loaded" session came from another tab's edit
  // trigger — we route back on save instead of returning to this tab's
  // landing. Reset whenever we re-enter "loaded" or go back to landing.
  const [externallyLaunched, setExternallyLaunched] = useState(false);

  const backToLanding = () => {
    setMode({ kind: "landing" });
    setExternallyLaunched(false);
  };

  // Load the pending order when another tab (e.g. Route Overview) hands us one.
  useEffect(() => {
    if (!pendingOrderNo) return;
    const orderNo = pendingOrderNo;
    (async () => {
      setLoading(true);
      setErrorMsg(null);
      try {
        const detail = await api.getSalesOrder(orderNo);
        const customer = await api.getCustomer(detail.customer_account);
        setFormSessionKey((k) => k + 1);
        setExternallyLaunched(true);
        setMode({ kind: "loaded", detail, customer });
      } catch (e: any) {
        setErrorMsg(
          e?.response?.data?.detail ??
            e?.message ??
            "Failed to load sales order"
        );
        setMode({ kind: "landing" });
      } finally {
        setLoading(false);
        clearPendingOrder?.();
      }
    })();
  }, [pendingOrderNo, clearPendingOrder]);

  async function handleSelectOrder(result: SalesOrderSearchResult) {
    setLoading(true);
    setErrorMsg(null);
    try {
      const [detail, customer] = await Promise.all([
        api.getSalesOrder(result.order_no),
        api.getCustomer(result.customer_account),
      ]);
      setFormSessionKey((k) => k + 1);
      setExternallyLaunched(false);
      setMode({ kind: "loaded", detail, customer });
    } catch (e: any) {
      setErrorMsg(
        e?.response?.data?.detail ??
          e?.message ??
          "Failed to load sales order"
      );
      setMode({ kind: "landing" });
    } finally {
      setLoading(false);
    }
  }

  if (mode.kind === "new") {
    return (
      <SalesOrderScreen
        key={`new-${formSessionKey}`}
        onBack={backToLanding}
      />
    );
  }

  if (mode.kind === "loaded") {
    const routeBack =
      externallyLaunched && onEditDone ? onEditDone : backToLanding;
    return (
      <SalesOrderScreen
        key={`loaded-${formSessionKey}`}
        initialLoadedOrder={{ detail: mode.detail, customer: mode.customer }}
        onBack={backToLanding}
        // Fires after the user dismisses the success modal; routing back to
        // the originating tab happens here when the edit was externally
        // launched, otherwise we land on the Sales Order Entry home.
        onPlaced={routeBack}
      />
    );
  }

  return (
    <div className="so-landing">
      <div className="so-landing-inner">
        <header className="so-landing-header">
          <h1>Sales Orders</h1>
          <p>Choose how you'd like to get started.</p>
        </header>

        <div className="so-landing-cards">
          {CREATE_NEW_ORDER_ENABLED && (
            <button
              className="so-landing-card"
              onClick={() => {
                setFormSessionKey((k) => k + 1);
                setMode({ kind: "new" });
              }}
            >
              <div className="so-landing-card-icon so-landing-card-icon--new">
                <FilePlus className="w-6 h-6" />
              </div>
              <div className="so-landing-card-body">
                <h2>Create a new order</h2>
                <p>
                  Start from a blank sales order. Pick a customer, add lines and
                  fill in the header details.
                </p>
              </div>
            </button>
          )}

          <button
            className="so-landing-card"
            onClick={() => setMode({ kind: "searching" })}
            disabled={loading}
          >
            <div className="so-landing-card-icon so-landing-card-icon--open">
              <FolderOpen className="w-6 h-6" />
            </div>
            <div className="so-landing-card-body">
              <h2>Open an existing order</h2>
              <p>
                Load a previous sales order to edit, or use an archived one as
                a template for a new order.
              </p>
            </div>
          </button>
        </div>

        {loading && (
          <div className="so-landing-status">Loading sales order…</div>
        )}
        {errorMsg && (
          <div className="so-landing-status so-landing-status--err">
            {errorMsg}
          </div>
        )}
      </div>

      {mode.kind === "searching" && (
        <SalesOrderSearchModal
          onClose={backToLanding}
          onSelect={handleSelectOrder}
        />
      )}
    </div>
  );
}
