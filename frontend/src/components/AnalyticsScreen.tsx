import { Suspense, lazy, useState } from "react";
import { BarChart3, ClipboardList, History, Inbox, Shield, Target as TargetIcon, Truck } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { AdminUsersScreen } from "./AdminUsersScreen";
import { DeliveriesScreen } from "./DeliveriesScreen";
import { OrderIntakeScreen } from "./OrderIntakeScreen";
import { CustomerSalesHistoryScreen } from "./CustomerSalesHistoryScreen";
import { PerformanceScreen } from "./PerformanceScreen";
import { FORECASTING_UI_ENABLED, TARGETS_FEATURE_ENABLED } from "../featureFlags";

// Management screens are code-split: agents never download them.
const TargetsScreen = lazy(() =>
  import("./TargetsScreen").then((m) => ({ default: m.TargetsScreen })));
const OpportunityInboxScreen = lazy(() =>
  import("./OpportunityInboxScreen").then((m) => ({ default: m.OpportunityInboxScreen })));
const ModelLeaderboardScreen = lazy(() =>
  import("./ModelLeaderboardScreen").then((m) => ({ default: m.ModelLeaderboardScreen })));

const loadingScreen = (
  <div className="h-full flex items-center justify-center text-sm text-gray-400">Loading…</div>
);

/** Hub landing for the Analytics tab. Contact Log card remains hidden
 *  until that phase is built — add it back here when ready. */
type View =
  | "hub" | "deliveries" | "order-intake" | "customer-history" | "performance" | "admin-users"
  | "targets" | "inbox" | "models";

type Props = {
  /** Pass-through for the Deliveries screen's edit flow — opens a sales
   *  order in the Sales Order Entry tab. */
  onOpenOrder?: (orderNo: string) => void;
};

export function AnalyticsScreen({ onOpenOrder }: Props = {}) {
  const { role } = useAuth();
  const isAdmin = role === "ADMIN";
  const isSupervisorPlus = role === "SUPERVISOR" || role === "ADMIN";
  // Forecasting management screens: SUPERVISOR + ADMIN (backend enforces too).
  const canManage = FORECASTING_UI_ENABLED && (role === "SUPERVISOR" || role === "ADMIN");
  const [view, setView] = useState<View>("hub");
  const backToHub = () => setView("hub");

  if (view === "deliveries") {
    return <DeliveriesScreen onOpenOrder={onOpenOrder} onBack={backToHub} />;
  }
  // Order Intake: SUPERVISOR + ADMIN only (backend enforces too).
  if (view === "order-intake" && isSupervisorPlus) {
    return <OrderIntakeScreen onOpenOrder={onOpenOrder} onBack={backToHub} />;
  }
  if (view === "customer-history") {
    return <CustomerSalesHistoryScreen onBack={backToHub} />;
  }
  if (view === "performance" && TARGETS_FEATURE_ENABLED) {
    return <PerformanceScreen onBack={backToHub} />;
  }
  if (view === "admin-users" && isAdmin) {
    return <AdminUsersScreen onBack={backToHub} />;
  }
  if (canManage && (view === "targets" || view === "inbox" || view === "models")) {
    return (
      <Suspense fallback={loadingScreen}>
        {view === "targets" && <TargetsScreen onBack={backToHub} />}
        {view === "inbox" && <OpportunityInboxScreen onBack={backToHub} />}
        {view === "models" && <ModelLeaderboardScreen onBack={backToHub} />}
      </Suspense>
    );
  }

  return (
    <div className="h-full bg-gray-50 overflow-y-auto">
      <div className="max-w-5xl mx-auto w-full p-6">
        <header className="mb-5">
          <h1 className="text-xl font-semibold text-gray-900">Analytics</h1>
          <p className="text-sm text-gray-600">Pick a report.</p>
        </header>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <HubCard
            icon={<Truck className="w-6 h-6" />}
            iconClass="bg-blue-100 text-blue-700"
            title="Deliveries"
            description="Order counts per customer on a given date."
            onClick={() => setView("deliveries")}
          />
          {isSupervisorPlus && (
            <HubCard
              icon={<ClipboardList className="w-6 h-6" />}
              iconClass="bg-sky-100 text-sky-700"
              title="Order Intake"
              description="Orders, qty, litres and amount per hour — SO Assistant vs Evolution."
              onClick={() => setView("order-intake")}
            />
          )}
          <HubCard
            icon={<History className="w-6 h-6" />}
            iconClass="bg-emerald-100 text-emerald-700"
            title="Customer Sales History"
            description="Weekly sales pivot."
            onClick={() => setView("customer-history")}
          />
          {/* Performance & Targets is behind a feature flag — hidden
              for a client demo while the design is being revised. Flip
              TARGETS_FEATURE_ENABLED in src/featureFlags.ts to show. */}
          {TARGETS_FEATURE_ENABLED && (
            <HubCard
              icon={<TargetIcon className="w-6 h-6" />}
              iconClass="bg-amber-100 text-amber-700"
              title="Performance & Targets"
              description="Set and track litre targets."
              onClick={() => setView("performance")}
            />
          )}
          {canManage && (
            <>
              <HubCard
                icon={<TargetIcon className="w-6 h-6" />}
                iconClass="bg-amber-100 text-amber-700"
                title="Targets"
                description="Forecast, target and progress from company down to product. Set a target on any level and see the split before you approve it."
                onClick={() => setView("targets")}
              />
              <HubCard
                icon={<Inbox className="w-6 h-6" />}
                iconClass="bg-teal-100 text-teal-700"
                title="Opportunity Inbox"
                description="Follow-up tasks from flagged targets and opportunity signals."
                onClick={() => setView("inbox")}
              />
              <HubCard
                icon={<BarChart3 className="w-6 h-6" />}
                iconClass="bg-indigo-100 text-indigo-700"
                title="Model Leaderboard"
                description="Forecast accuracy per model and the promotion gate."
                onClick={() => setView("models")}
              />
            </>
          )}
          {isAdmin && (
            <HubCard
              icon={<Shield className="w-6 h-6" />}
              iconClass="bg-purple-100 text-purple-700"
              title="Users & Roles"
              description="Add agents, set roles, deactivate access."
              onClick={() => setView("admin-users")}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function HubCard({
  icon,
  iconClass,
  title,
  description,
  onClick,
}: {
  icon: React.ReactNode;
  iconClass: string;
  title: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className="group bg-white rounded border shadow-sm hover:shadow-md transition-shadow p-4 text-left flex gap-3 items-start"
    >
      <div className={`flex-shrink-0 w-10 h-10 rounded-md flex items-center justify-center ${iconClass}`}>
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        <p className="text-xs text-gray-600 mt-1 leading-snug">{description}</p>
      </div>
    </button>
  );
}
