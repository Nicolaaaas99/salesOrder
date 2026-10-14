import { useCallback, useState } from "react";
import { LogOut, UserCircle } from "lucide-react";
import { SalesOrderTab } from "./components/SalesOrderTab";
import { OrderAssistantScreen } from "./components/OrderAssistantScreen";
import { RouteOverviewScreen } from "./components/RouteOverviewScreen";
import { LoginScreen } from "./components/LoginScreen";
import { SessionDateModal } from "./components/SessionDateModal";
import { AuthProvider, useAuth } from "./context/AuthContext";

type View = "assistant" | "order" | "overview";

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  );
}

function AppShell() {
  const { token, sessionDate } = useAuth();
  if (!token) return <LoginScreen />;
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

  const openOrderInEntryTab = useCallback((orderNo: string) => {
    setPendingOrderNo(orderNo);
    setView("order");
  }, []);
  const clearPendingOrder = useCallback(() => setPendingOrderNo(null), []);

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
          <button
            className={`app-tab ${view === "order" ? "app-tab--active" : ""}`}
            onClick={() => setView("order")}
          >
            Sales Order Entry
          </button>
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
          <OrderAssistantScreen />
        </div>
        <div
          className="h-full overflow-y-auto"
          style={{ display: view === "order" ? "block" : "none" }}
        >
          <SalesOrderTab
            pendingOrderNo={pendingOrderNo}
            clearPendingOrder={clearPendingOrder}
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
