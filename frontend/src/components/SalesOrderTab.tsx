import { useState } from "react";
import { FilePlus, FolderOpen } from "lucide-react";
import { api } from "../api/client";
import type {
  CustomerDetail,
  SalesOrderDetail,
  SalesOrderSearchResult,
} from "../api/types";
import { SalesOrderScreen } from "./SalesOrderScreen";
import { SalesOrderSearchModal } from "./SalesOrderSearchModal";

type Mode =
  | { kind: "landing" }
  | { kind: "searching" }
  | { kind: "new" }
  | {
      kind: "loaded";
      detail: SalesOrderDetail;
      customer: CustomerDetail;
    };

export function SalesOrderTab() {
  const [mode, setMode] = useState<Mode>({ kind: "landing" });
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [formSessionKey, setFormSessionKey] = useState(0);

  const backToLanding = () => setMode({ kind: "landing" });

  async function handleSelectOrder(result: SalesOrderSearchResult) {
    setLoading(true);
    setErrorMsg(null);
    try {
      const [detail, customer] = await Promise.all([
        api.getSalesOrder(result.order_no),
        api.getCustomer(result.customer_account),
      ]);
      setFormSessionKey((k) => k + 1);
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
    return (
      <SalesOrderScreen
        key={`loaded-${formSessionKey}`}
        initialLoadedOrder={{ detail: mode.detail, customer: mode.customer }}
        onBack={backToLanding}
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
