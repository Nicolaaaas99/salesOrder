import { useCallback, useState } from "react";
import { LogOut, UserCircle } from "lucide-react";
import { SalesOrderTab } from "./components/SalesOrderTab";
import { OrderAssistantScreen } from "./components/OrderAssistantScreen";
import { RouteOverviewScreen } from "./components/RouteOverviewScreen";
import { LoginScreen } from "./components/LoginScreen";
import { SessionDateModal } from "./components/SessionDateModal";
import { DefaultWarehouseModal } from "./components/DefaultWarehouseModal";
import { AuthProvider, useAuth } from "./context/AuthContext";

type View = "assistant" | "order" | "overview";

// Toggle the Sales Order Entry tab button on/off without touching the rest
// of the plumbing. The view itself stays mounted so the Edit flow from
// Order Assistant / Route Overview still routes through it; this just hides
// the standalone tab from the navbar.
const SHOW_SALES_ORDER_ENTRY_TAB = true;

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}

function AppShell() {
  const { token, sessionDate, defaultWarehouseCode } = useAuth();
  if (!token) return <LoginScreen />;
  if (!defaultWarehouseCode) return <DefaultWarehouseModal />;
  if (!sessionDate) return <SessionDateModal />;
  return <AuthenticatedApp />;
}

function AuthenticatedApp() {
  const { username, logout } = useAuth();
  const [view, setView] = useState<View>("assistant");
  // When another tab (e.g. Route Overview) wants to open an existing order
  // for editing, it drops the order number here and switches us to the
  // Sales Order Entry tab. SalesOrderTab's effect picks it up and loads it.
  const [pendingOrderNo, setPendingOrderNo] = useState<string | null>(null);
  // Remember which tab the edit was launched from so we can route the user
  // back after they save — otherwise they'd be stranded on the Sales Order
  // Entry landing screen.
  const [originAfterEdit, setOriginAfterEdit] = useState<View | null>(null);

  const openOrderInEntryTab = useCallback(
    (orderNo: string) => {
      setPendingOrderNo(orderNo);
      // Only track as "needs return" when launched from assistant / overview.
      // Opening an order from inside the Sales Order Entry tab itself should
      // stay in that tab.
      if (view !== "order") setOriginAfterEdit(view);
      setView("order");
    },
    [view],
  );
  const clearPendingOrder = useCallback(() => setPendingOrderNo(null), []);

  const handleEditDone = useCallback(() => {
    if (!originAfterEdit) return;
    setView(originAfterEdit);
    setOriginAfterEdit(null);
  }, [originAfterEdit]);

  return (
    <div className="h-screen flex flex-col">
      <nav className="app-tabbar flex-shrink-0 flex items-center">
        <div className="flex gap-1">
          <button
            className={`app-tab ${view === "assistant" ? "app-tab--active" : ""}`}
            onClick={() => setView("assistant")}
          >
            Order Assistant
          </button>
          {SHOW_SALES_ORDER_ENTRY_TAB && (
            <button
              className={`app-tab ${view === "order" ? "app-tab--active" : ""}`}
              onClick={() => setView("order")}
            >
              Sales Order Entry
            </button>
          )}
          <button
            className={`app-tab ${view === "overview" ? "app-tab--active" : ""}`}
            onClick={() => setView("overview")}
          >
            Route Overview
          </button>
        </div>

        <div className="ml-auto flex items-center gap-3 pr-3">
          <span className="flex items-center gap-1 text-xs text-gray-600">
            <UserCircle className="w-4 h-4 text-gray-500" />
            {username}
          </span>
          <button
            onClick={logout}
            className="flex items-center gap-1 text-xs text-gray-600 hover:text-red-600"
            title="Sign out"
          >
            <LogOut className="w-3.5 h-3.5" />
            Sign out
          </button>
        </div>
      </nav>

      {/* Sized views fit the remaining viewport; SalesOrderTab manages its own
          overflow (its content can be tall), the other two fit exactly. */}
      <main className="flex-1 min-h-0">
        <div
          className="h-full"
          style={{ display: view === "assistant" ? "block" : "none" }}
        >
          <OrderAssistantScreen onOpenOrder={openOrderInEntryTab} />
        </div>
        <div
          className="h-full overflow-y-auto"
          style={{ display: view === "order" ? "block" : "none" }}
        >
          <SalesOrderTab
            pendingOrderNo={pendingOrderNo}
            clearPendingOrder={clearPendingOrder}
            onEditDone={originAfterEdit ? handleEditDone : undefined}
          />
        </div>
        <div
          className="h-full"
          style={{ display: view === "overview" ? "block" : "none" }}
        >
          <RouteOverviewScreen onOpenOrder={openOrderInEntryTab} />
        </div>
      </main>
    </div>
  );
}
