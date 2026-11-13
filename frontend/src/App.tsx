import { useCallback, useMemo, useState } from "react";
import { FileClock, LogOut, UserCircle } from "lucide-react";
import { SalesOrderTab } from "./components/SalesOrderTab";
import { OrderAssistantScreen } from "./components/OrderAssistantScreen";
import { AnalyticsScreen } from "./components/AnalyticsScreen";
import { LoginScreen } from "./components/LoginScreen";
import { SessionDateModal } from "./components/SessionDateModal";
import { DefaultWarehouseModal } from "./components/DefaultWarehouseModal";
import { WarehouseSwitcher } from "./components/WarehouseSwitcher";
import { AuthProvider, useAuth } from "./context/AuthContext";
import { useAutoReloadOnNewVersion } from "./utils/autoReload";
import { useHeartbeat } from "./utils/useHeartbeat";
import { usePendingReviewCount } from "./utils/usePendingReviewCount";
import { CountBadge } from "./components/CountBadge";
import { DraftsModal, StaleDraftsPrompt } from "./components/DraftsDialogs";
import { ConfirmDialogHost } from "./components/ConfirmDialog";
import { api } from "./api/client";
import type { DraftDetail, DraftSummary } from "./api/types";
import { notifyDraftsChanged, useDrafts } from "./utils/drafts";
import { extractErrorMsg } from "./utils/format";

// Stale drafts the agent chose to "Finish" this session: not asked again
// until the next sign-in (they're still deleted after 3 days).
const FINISH_LATER_KEY = "drafts:finishLater";
function readFinishLater(): Set<number> {
  try {
    return new Set(JSON.parse(sessionStorage.getItem(FINISH_LATER_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

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
  // Any mounted session polls /version.json and auto-reloads on a new
  // build — removes the need for users to manually hard-refresh after a
  // deploy. See utils/autoReload.ts.
  useAutoReloadOnNewVersion();
  const { token, sessionDate, defaultWarehouseCode } = useAuth();
  if (!token) return <LoginScreen />;
  if (!defaultWarehouseCode) return <DefaultWarehouseModal />;
  if (!sessionDate) return <SessionDateModal />;
  return <AuthenticatedApp />;
}

function AuthenticatedApp() {
  const { username, role, logout } = useAuth();
  // Keeps this user "online" on Users & Roles while the app is open.
  useHeartbeat(true);
  const [view, setView] = useState<View>("assistant");
  // Imported orders waiting for a supervisor: badge on the tab + card.
  const pendingReviews = usePendingReviewCount(role === "SUPERVISOR" || role === "ADMIN");
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

  // -- Drafts ----------------------------------------------------------------
  const { drafts, error: draftsError } = useDrafts();
  const [draftsOpen, setDraftsOpen] = useState(false);
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  // A draft being reopened, handed to the screen it was saved from.
  const [pendingAssistantDraft, setPendingAssistantDraft] = useState<DraftDetail | null>(null);
  const [pendingEntryDraft, setPendingEntryDraft] = useState<DraftDetail | null>(null);
  const [finishLater, setFinishLater] = useState<Set<number>>(readFinishLater);
  const staleDrafts = useMemo(
    () => drafts.filter((d) => d.stale && !finishLater.has(d.id)),
    [drafts, finishLater],
  );

  const openDraft = useCallback(async (d: DraftSummary) => {
    setDraftBusy(true);
    setDraftError(null);
    try {
      const detail = await api.getDraft(d.id);
      setDraftsOpen(false);
      if (detail.source === "ORDER_ASSISTANT") {
        setPendingAssistantDraft(detail);
        setView("assistant");
      } else {
        setPendingEntryDraft(detail);
        setView("order");
      }
    } catch (e) {
      setDraftError(extractErrorMsg(e, "Could not open the draft"));
      notifyDraftsChanged();
    } finally {
      setDraftBusy(false);
    }
  }, []);

  const deleteDraft = useCallback(async (d: DraftSummary) => {
    setDraftBusy(true);
    setDraftError(null);
    try {
      await api.deleteDraft(d.id);
    } catch (e) {
      setDraftError(extractErrorMsg(e, "Could not delete the draft"));
    } finally {
      notifyDraftsChanged();
      setDraftBusy(false);
    }
  }, []);

  const resolveStale = useCallback(
    async (finish: DraftSummary[], remove: DraftSummary[]) => {
      const later = new Set(finishLater);
      finish.forEach((d) => later.add(d.id));
      sessionStorage.setItem(FINISH_LATER_KEY, JSON.stringify([...later]));
      setFinishLater(later);
      setDraftBusy(true);
      try {
        await Promise.all(remove.map((d) => api.deleteDraft(d.id)));
      } catch (e) {
        setDraftError(extractErrorMsg(e, "Could not delete a draft"));
      } finally {
        setDraftBusy(false);
        notifyDraftsChanged();
      }
      if (finish.length > 0) await openDraft(finish[0]);
    },
    [finishLater, openDraft],
  );

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
              <CountBadge count={pendingReviews} title="orders waiting for price review" />
            </button>
          )}
          <button
            className={`app-tab ${view === "overview" ? "app-tab--active" : ""}`}
            onClick={() => setView("overview")}
          >
            Analytics
          </button>
        </div>

        <div className="ml-auto flex items-center gap-3 pr-3">
          <button
            onClick={() => setDraftsOpen(true)}
            className="flex items-center gap-1 text-xs text-gray-700 hover:text-blue-700"
            title="Orders you saved as a draft"
          >
            <FileClock className="w-3.5 h-3.5" />
            Drafts
            <CountBadge count={drafts.length} title="draft orders" />
          </button>
          <WarehouseSwitcher />
          <span className="flex items-center gap-1 text-xs text-gray-600">
            <UserCircle className="w-4 h-4 text-gray-500" />
            {username}
            {role && (
              <span
                className={`ml-1 inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${
                  role === "ADMIN"
                    ? "bg-purple-100 text-purple-700 border border-purple-200"
                    : role === "SUPERVISOR"
                      ? "bg-blue-100 text-blue-700 border border-blue-200"
                      : "bg-gray-100 text-gray-600 border border-gray-200"
                }`}
                title={`Signed in as ${role.toLowerCase()}`}
              >
                {role}
              </span>
            )}
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
          <OrderAssistantScreen
            onOpenOrder={openOrderInEntryTab}
            drafts={drafts}
            onOpenDraft={openDraft}
            pendingDraft={pendingAssistantDraft}
            clearPendingDraft={() => setPendingAssistantDraft(null)}
          />
        </div>
        <div
          className="h-full overflow-y-auto"
          style={{ display: view === "order" ? "block" : "none" }}
        >
          <SalesOrderTab
            pendingOrderNo={pendingOrderNo}
            clearPendingOrder={clearPendingOrder}
            onEditDone={originAfterEdit ? handleEditDone : undefined}
            pendingReviews={pendingReviews}
            pendingDraft={pendingEntryDraft}
            clearPendingDraft={() => setPendingEntryDraft(null)}
          />
        </div>
        <div
          className="h-full"
          style={{ display: view === "overview" ? "block" : "none" }}
        >
          <AnalyticsScreen onOpenOrder={openOrderInEntryTab} />
        </div>
      </main>

      {draftsOpen && (
        <DraftsModal
          drafts={drafts}
          error={draftError ?? draftsError}
          busy={draftBusy}
          onOpen={openDraft}
          onDelete={deleteDraft}
          onClose={() => {
            setDraftsOpen(false);
            setDraftError(null);
          }}
        />
      )}
      {staleDrafts.length > 0 && (
        <StaleDraftsPrompt
          key={staleDrafts.map((d) => d.id).join(",")}
          drafts={staleDrafts}
          busy={draftBusy}
          onDone={resolveStale}
        />
      )}
      <ConfirmDialogHost />
      {draftError && !draftsOpen && (
        <div className="modal-backdrop" onClick={() => setDraftError(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2 className="modal-title">Draft</h2>
            <p className="modal-body">{draftError}</p>
            <button className="btn btn-primary" onClick={() => setDraftError(null)}>OK</button>
          </div>
        </div>
      )}
    </div>
  );
}
