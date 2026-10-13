import { useEffect, useMemo, useState } from "react";
import Select from "react-select";
import { Truck, Users, XCircle, FileText } from "lucide-react";
import { api } from "../api/client";
import type {
  CustomerDetail,
  HistoricalOrder,
  OrderSuggestion,
} from "../api/types";
import { HistoryGrid } from "./HistoryGrid";
import { CreditStatus } from "./CreditStatus";
import { productKey } from "../utils/history";
import { fmtCurrency } from "../utils/format";

type Option = { value: string; label: string };

const selectStyles = {
  control: (base: any) => ({
    ...base,
    padding: "1px 6px",
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
    zIndex: 40,
  }),
  option: (base: any, state: any) => ({
    ...base,
    fontSize: "13px",
    padding: "4px 8px",
    backgroundColor: state.isFocused ? "#eff6ff" : "white",
    color: "#1f2937",
    "&:active": { backgroundColor: "#dbeafe" },
  }),
};

export function CustomerOverviewScreen() {
  const [routes, setRoutes] = useState<Option[]>([]);
  const [customers, setCustomers] = useState<Option[]>([]);
  const [selectedRoute, setSelectedRoute] = useState<Option | null>(null);
  const [selectedCustomer, setSelectedCustomer] = useState<Option | null>(null);

  const [customerDetail, setCustomerDetail] = useState<CustomerDetail | null>(
    null
  );
  const [orders, setOrders] = useState<HistoricalOrder[]>([]);
  const [suggestions, setSuggestions] = useState<OrderSuggestion[]>([]);

  const [isLoadingRoutes, setIsLoadingRoutes] = useState(false);
  const [isLoadingCustomers, setIsLoadingCustomers] = useState(false);
  const [isLoadingOrders, setIsLoadingOrders] = useState(false);

  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      setIsLoadingRoutes(true);
      setError(null);
      try {
        const data = await api.listHistoryRoutes();
        setRoutes(data.map((r) => ({ value: r.id, label: r.display })));
      } catch (e: any) {
        setError(`Failed to load routes: ${e?.message ?? e}`);
      } finally {
        setIsLoadingRoutes(false);
      }
    })();
  }, []);

  useEffect(() => {
    if (!selectedRoute) {
      setCustomers([]);
      setSelectedCustomer(null);
      return;
    }
    (async () => {
      setIsLoadingCustomers(true);
      setError(null);
      try {
        const data = await api.listHistoryCustomers(selectedRoute.value);
        // Prefix the account code so reps can search / scan on it.
        setCustomers(data.map((c) => ({ value: c.id, label: `${c.id} — ${c.display}` })));
      } catch {
        setError("Failed to load customers.");
      } finally {
        setIsLoadingCustomers(false);
      }
    })();
    setSelectedCustomer(null);
  }, [selectedRoute]);

  useEffect(() => {
    if (!selectedCustomer) {
      setOrders([]);
      setSuggestions([]);
      setCustomerDetail(null);
      return;
    }

    (async () => {
      setIsLoadingOrders(true);
      setError(null);
      try {
        const [ordersData, suggestionsData, detail] = await Promise.all([
          api.listHistoryOrders(selectedCustomer.value),
          api.listOrderSuggestions(selectedCustomer.value),
          api.getCustomer(selectedCustomer.value).catch(() => null),
        ]);
        setOrders(ordersData);
        setSuggestions(suggestionsData);
        setCustomerDetail(detail);
      } catch {
        setError("Failed to load history / suggestions.");
      } finally {
        setIsLoadingOrders(false);
      }
    })();
  }, [selectedCustomer]);

  const suggestionsMap = useMemo(() => {
    const m = new Map<string, OrderSuggestion>();
    suggestions.forEach((s) => {
      m.set(productKey(s.stock_code, s.description), s);
    });
    return m;
  }, [suggestions]);

  return (
    <div className="h-full bg-gray-50 p-2 flex flex-col">
      <div className="max-w-[1600px] mx-auto w-full flex-1 min-h-0 flex flex-col gap-2">
        <div className="grid grid-cols-2 gap-2 flex-shrink-0">
          <div className="bg-white rounded border p-2 shadow-sm">
            <label className="flex items-center font-semibold text-gray-800 mb-1 text-sm">
              <Truck className="w-3 h-3 mr-1 text-blue-600" />
              Route
            </label>
            <Select
              options={routes}
              value={selectedRoute}
              onChange={(opt) => setSelectedRoute(opt as Option | null)}
              placeholder="Search route..."
              isDisabled={isLoadingRoutes}
              isLoading={isLoadingRoutes}
              styles={selectStyles}
              isSearchable
              isClearable
            />
          </div>

          <div className="bg-white rounded border p-2 shadow-sm">
            <label className="flex items-center font-semibold text-gray-800 mb-1 text-sm">
              <Users className="w-3 h-3 mr-1 text-green-600" />
              Customer
            </label>
            <Select
              options={customers}
              value={selectedCustomer}
              onChange={(opt) => setSelectedCustomer(opt as Option | null)}
              placeholder={
                selectedRoute ? "Search customer..." : "Select route first"
              }
              isDisabled={!selectedRoute || isLoadingCustomers}
              isLoading={isLoadingCustomers}
              styles={selectStyles}
              isSearchable
              isClearable
            />
          </div>
        </div>

        {error && (
          <div className="p-1 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs flex-shrink-0">
            <XCircle className="w-3 h-3 mr-1 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="grid grid-cols-[1fr_380px] gap-2 flex-1 min-h-0">
          <HistoryGrid
            orders={orders}
            isLoading={isLoadingOrders}
            emptyMessage={
              selectedCustomer
                ? "No history for this customer"
                : selectedRoute
                ? "Select a customer to see history"
                : "Select a route to begin"
            }
            suggestions={suggestionsMap}
            heightStyle="100%"
          />

          <CustomerInfoPanel customer={customerDetail} />
        </div>
      </div>
    </div>
  );
}

function CustomerInfoPanel({ customer }: { customer: CustomerDetail | null }) {
  if (!customer) {
    return (
      <div className="bg-white rounded border shadow-sm flex flex-col items-center justify-center text-gray-400 p-6 h-full">
        <FileText className="w-6 h-6 mb-1 text-gray-300" />
        <p className="text-xs">Select a customer to see details</p>
      </div>
    );
  }

  return (
    <div className="bg-white rounded border shadow-sm flex flex-col overflow-hidden h-full">
      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50">
        <div className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Customer</div>
        <div className="text-sm text-gray-900 font-medium">{customer.account} · {customer.name}</div>
      </div>

      <div className="flex-1 overflow-auto">
        <div className="px-2 py-2 border-b border-gray-200">
          <CreditStatus customer={customer} currentOrderIncl={0} compact />
        </div>

        {customer.credit_limit > 0 && (
          <div className="px-3 py-2 border-b border-gray-200 text-xs">
            <div className="font-semibold text-gray-700 mb-1 uppercase tracking-wide text-[11px]">Available credit</div>
            <InfoRow label="Available" value={fmtCurrency(customer.available_credit)} emphasize />
          </div>
        )}

        {customer.check_terms && (
          <div className="px-3 py-2 border-b border-gray-200 text-xs">
            <div className="font-semibold text-gray-700 mb-1 uppercase tracking-wide text-[11px]">Terms</div>
            <InfoRow label="Terms" value={customer.terms_code || "—"} />
            <InfoRow
              label="Days"
              value={customer.terms_days > 0 ? `${customer.terms_days}` : "current period"}
            />
          </div>
        )}

        <div className="px-3 py-2 border-b border-gray-200 text-xs">
          <div className="font-semibold text-gray-700 mb-1 uppercase tracking-wide text-[11px]">Delivery Address</div>
          <pre className="whitespace-pre-wrap text-gray-700 text-xs leading-snug font-sans">
            {customer.delivery_address || "—"}
          </pre>
        </div>

        <div className="px-3 py-2 border-b border-gray-200 text-xs">
          <div className="font-semibold text-gray-700 mb-1 uppercase tracking-wide text-[11px]">Contact</div>
          <InfoRow label="Name" value={customer.contact_name || "—"} />
          <InfoRow label="Tel" value={customer.contact_tel || "—"} />
          <InfoRow label="Email" value={customer.contact_email || "—"} />
        </div>
      </div>
    </div>
  );
}

function InfoRow({
  label,
  value,
  emphasize,
}: {
  label: string;
  value: string;
  emphasize?: boolean;
}) {
  return (
    <div className="flex justify-between py-0.5">
      <span className="text-gray-500">{label}</span>
      <span className={emphasize ? "font-semibold text-gray-900" : "text-gray-800"}>{value}</span>
    </div>
  );
}
