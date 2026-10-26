import { useState } from "react";
import { History, Truck } from "lucide-react";
import { RouteDeliveriesScreen } from "./RouteDeliveriesScreen";
import { RouteCustomerHistoryScreen } from "./RouteCustomerHistoryScreen";

/** Hub landing for the Analytics tab. Contact Log and Agent Performance
 *  cards remain hidden until those phases are built — add them back here
 *  when ready. */
type View = "hub" | "deliveries" | "customer-history";

type Props = {
  /** Pass-through for the Deliveries screen's edit flow — opens a sales
   *  order in the Sales Order Entry tab. */
  onOpenOrder?: (orderNo: string) => void;
};

export function RouteOverviewScreen({ onOpenOrder }: Props = {}) {
  const [view, setView] = useState<View>("hub");
  const backToHub = () => setView("hub");

  if (view === "deliveries") {
    return <RouteDeliveriesScreen onOpenOrder={onOpenOrder} onBack={backToHub} />;
  }
  if (view === "customer-history") {
    return <RouteCustomerHistoryScreen onBack={backToHub} />;
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
            description="Route → customer → category → stock pivot of weekly sales."
            onClick={() => setView("customer-history")}
          />
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
