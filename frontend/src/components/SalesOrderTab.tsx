import { useEffect, useState } from "react";
import { FilePlus, FileUp, FolderOpen, Scale } from "lucide-react";
import { api } from "../api/client";
import type {
  CustomerDetail,
  SalesOrderDetail,
  SalesOrderSearchResult,
} from "../api/types";
import { SalesOrderScreen } from "./SalesOrderScreen";
import { SalesOrderSearchModal } from "./SalesOrderSearchModal";
import { OrderImportScreen } from "./OrderImportScreen";
import { PriceReviewScreen } from "./PriceReviewScreen";
import { CountBadge } from "./CountBadge";

// Flip to re-enable the "Create a new order" card. Keeping the code in place
// so flipping back is a one-line change if telesales needs manual entry again.
const CREATE_NEW_ORDER_ENABLED = true;

type Mode =
  | { kind: "landing" }
  | { kind: "searching" }
  | { kind: "new" }
  | { kind: "import" }
  | { kind: "priceReview" }
  | { kind: "review"; id: number }
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
  /** Imported orders waiting for a supervisor (0 for agents). */
  pendingReviews?: number;
};

export function SalesOrderTab({
  pendingOrderNo,
  clearPendingOrder,
  onEditDone,
  pendingReviews = 0,
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
        source="SALES_ORDER_ENTRY"
        onBack={backToLanding}
      />
    );
  }

  if (mode.kind === "import") {
    return <OrderImportScreen key={`import-${formSessionKey}`} onBack={backToLanding} />;
  }

  if (mode.kind === "priceReview") {
    return (
      <PriceReviewScreen
        onBack={backToLanding}
        onOpenReview={(id) => {
          setFormSessionKey((k) => k + 1);
          setMode({ kind: "review", id });
        }}
      />
    );
  }

  if (mode.kind === "review") {
    return (
      <OrderImportScreen
        key={`review-${mode.id}-${formSessionKey}`}
        reviewId={mode.id}
        onBack={() => setMode({ kind: "priceReview" })}
      />
    );
  }

  if (mode.kind === "loaded") {
    const routeBack =
      externallyLaunched && onEditDone ? onEditDone : backToLanding;
    return (
      <SalesOrderScreen
        key={`loaded-${formSessionKey}`}
        source="SALES_ORDER_ENTRY"
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
    <div className="h-full bg-gray-50 overflow-y-auto">
      <div className="max-w-5xl mx-auto w-full p-6">
        <header className="mb-5">
          <h1 className="text-xl font-semibold text-gray-900">Sales Orders</h1>
          <p className="text-sm text-gray-600">Choose how you'd like to get started.</p>
        </header>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {CREATE_NEW_ORDER_ENABLED && (
            <HubCard
              icon={<FilePlus className="w-6 h-6" />}
              iconClass="bg-blue-100 text-blue-700"
              title="Create a new order"
              description="Start from a blank sales order."
              onClick={() => {
                setFormSessionKey((k) => k + 1);
                setMode({ kind: "new" });
              }}
            />
          )}

          <HubCard
            icon={<FileUp className="w-6 h-6" />}
            iconClass="bg-amber-100 text-amber-700"
            title="Import an emailed order"
            description="Upload a customer's purchase order (PDF) and check it against Evolution."
            onClick={() => {
              setFormSessionKey((k) => k + 1);
              setMode({ kind: "import" });
            }}
          />

          <HubCard
            icon={<Scale className="w-6 h-6" />}
            iconClass="bg-violet-100 text-violet-700"
            title="Price review"
            badge={pendingReviews}
            description="Imported orders waiting for a supervisor, PO price differences and broken price-list rows."
            onClick={() => setMode({ kind: "priceReview" })}
          />

          <HubCard
            icon={<FolderOpen className="w-6 h-6" />}
            iconClass="bg-emerald-100 text-emerald-700"
            title="Open an existing order"
            description="Load a previous order to edit or re-use."
            onClick={() => setMode({ kind: "searching" })}
            disabled={loading}
          />
        </div>

        {loading && (
          <div className="mt-4 text-center text-xs text-gray-600">
            Loading sales order…
          </div>
        )}
        {errorMsg && (
          <div className="mt-4 text-center text-xs text-red-600">
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

/** Same visual language as AnalyticsScreen.HubCard — kept inline here
 *  rather than extracted so each tab stays self-contained. */
function HubCard({
  icon,
  iconClass,
  title,
  description,
  onClick,
  disabled,
  badge = 0,
}: {
  icon: React.ReactNode;
  iconClass: string;
  title: string;
  /** Count bubble next to the title (hidden at 0). */
  badge?: number;
  description: string;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="group bg-white rounded border shadow-sm hover:shadow-md transition-shadow p-4 text-left flex gap-3 items-start disabled:opacity-60 disabled:cursor-not-allowed"
    >
      <div className={`flex-shrink-0 w-10 h-10 rounded-md flex items-center justify-center ${iconClass}`}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <h2 className="text-sm font-semibold text-gray-900">
          {title}
          <CountBadge count={badge} title="waiting for review" />
        </h2>
        <p className="text-xs text-gray-600 mt-1 leading-snug">{description}</p>
      </div>
    </button>
  );
}
