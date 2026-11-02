import { useState } from "react";
import { History, Shield, Target as TargetIcon, Truck } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { AdminUsersScreen } from "./AdminUsersScreen";
import { DeliveriesScreen } from "./DeliveriesScreen";
import { CustomerSalesHistoryScreen } from "./CustomerSalesHistoryScreen";
import { PerformanceScreen } from "./PerformanceScreen";
import { TARGETS_FEATURE_ENABLED } from "../featureFlags";

/** Hub landing for the Analytics tab. Contact Log card remains hidden
 *  until that phase is built — add it back here when ready. */
type View = "hub" | "deliveries" | "customer-history" | "performance" | "admin-users";

type Props = {
  /** Pass-through for the Deliveries screen's edit flow — opens a sales
   *  order in the Sales Order Entry tab. */
  onOpenOrder?: (orderNo: string) => void;
};

export function AnalyticsScreen({ onOpenOrder }: Props = {}) {
  const { role } = useAuth();
  const isAdmin = role === "ADMIN";
  const [view, setView] = useState<View>("hub");
  const backToHub = () => setView("hub");

  if (view === "deliveries") {
    return <DeliveriesScreen onOpenOrder={onOpenOrder} onBack={backToHub} />;
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
