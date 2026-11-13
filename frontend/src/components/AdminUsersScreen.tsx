import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Warehouse } from "../api/types";
import { shortWarehouse } from "../utils/warehouse";
import { ArrowLeft, Loader2, Plus, Shield, UserCheck, UserX, XCircle } from "lucide-react";
import { adminApi, type AdminUserRow, type UserPresence } from "../api/client";
import type { AppUserRole } from "../api/client";
import { useAuth } from "../context/AuthContext";

/** Analytics → Users & Roles.
 *  ADMIN: every active Evolution agent with their role (if any); add,
 *  change role / warehouse, deactivate.
 *  SUPERVISOR: registered agents and supervisors only (no admins), and the
 *  only change they can make is an AGENT's warehouse.
 *  Both see who is signed in right now (Status column, refreshed every
 *  60 s). The backend enforces all of this independently. */

type Props = {
  onBack?: () => void;
};

const ROLES: AppUserRole[] = ["AGENT", "SUPERVISOR", "ADMIN"];

const ROLE_LABEL: Record<AppUserRole, string> = {
  AGENT: "Agent",
  SUPERVISOR: "Supervisor",
  ADMIN: "Admin",
};

const ROLE_CHIP_CLASS: Record<AppUserRole, string> = {
  AGENT: "bg-gray-100 text-gray-700 border-gray-200",
  SUPERVISOR: "bg-blue-100 text-blue-700 border-blue-200",
  ADMIN: "bg-purple-100 text-purple-700 border-purple-200",
};

/** dd/mm/yyyy — avoids the ambiguous US-style m/d/yyyy that
 *  toLocaleDateString() defaults to on en-US locales. Parses from an
 *  ISO string so timezone drift on the client doesn't shift the day. */
function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const dd = String(d.getDate()).padStart(2, "0");
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const yyyy = d.getFullYear();
  return `${dd}/${mm}/${yyyy}`;
}

type Filter = "all" | "registered" | "unregistered" | "inactive";

export function AdminUsersScreen({ onBack }: Props = {}) {
  const { agentId: myAgentId, role: myRole } = useAuth();
  const isAdmin = myRole === "ADMIN";
  const [rows, setRows] = useState<AdminUserRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("registered");
  const [search, setSearch] = useState("");
  // Set of agent_ids currently waiting on a server request — used to
  // disable controls so the user can't double-click into inconsistent
  // state.
  const [pending, setPending] = useState<Set<number>>(new Set());
  // The agent id currently expanded for "add as …" so the inline
  // role picker can be shown without opening a modal.
  const [addingAgent, setAddingAgent] = useState<number | null>(null);

  const refetch = useCallback(() => {
    setLoading(true);
    setError(null);
    adminApi
      .listUsers()
      .then(setRows)
      .catch((e: any) => {
        setError(e?.response?.data?.detail ?? e?.message ?? "Failed to load users.");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refetch();
  }, [refetch]);

  // Who is signed in right now. Memory-only on the server; refreshed every
  // 60 s while this screen is visible, one request at a time.
  const [presence, setPresence] = useState<Map<string, UserPresence>>(new Map());
  useEffect(() => {
    let inFlight = false;
    const load = () => {
      if (inFlight || document.visibilityState !== "visible") return;
      inFlight = true;
      adminApi
        .listPresence()
        .then((list) => setPresence(new Map(list.map((p) => [p.username.trim().toLowerCase(), p]))))
        .catch(() => {})
        .finally(() => {
          inFlight = false;
        });
    };
    load();
    const id = window.setInterval(load, 60_000);
    return () => window.clearInterval(id);
  }, []);
  const onlineCount = useMemo(
    () => (rows ?? []).filter((r) => presence.get(r.agent_name.toLowerCase())?.online).length,
    [rows, presence],
  );

  // The DC warehouses an admin can assign (MAK / POL).
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  useEffect(() => {
    api.listDefaultWarehouseChoices().then(setWarehouses).catch(() => setWarehouses([]));
  }, []);

  const filtered = useMemo(() => {
    if (!rows) return [];
    const q = search.trim().toLowerCase();
    return rows.filter((r) => {
      if (filter === "registered" && r.app_user_id == null) return false;
      if (filter === "unregistered" && r.app_user_id != null) return false;
      if (filter === "inactive" && r.is_active !== false) return false;
      if (
        q &&
        !r.agent_name.toLowerCase().includes(q) &&
        !r.agent_description.toLowerCase().includes(q)
      ) {
        return false;
      }
      return true;
    });
  }, [rows, filter, search]);

  const markPending = (agentId: number, on: boolean) =>
    setPending((prev) => {
      const next = new Set(prev);
      if (on) next.add(agentId);
      else next.delete(agentId);
      return next;
    });

  const handleAdd = async (agentId: number, role: AppUserRole) => {
    markPending(agentId, true);
    setError(null);
    try {
      const updated = await adminApi.createUser({ agent_id: agentId, role });
      setRows((prev) => prev?.map((r) => (r.agent_id === agentId ? updated : r)) ?? null);
      setAddingAgent(null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Create failed.");
    } finally {
      markPending(agentId, false);
    }
  };

  const handleRoleChange = async (row: AdminUserRow, nextRole: AppUserRole) => {
    if (!row.app_user_id || nextRole === row.role) return;
    markPending(row.agent_id, true);
    setError(null);
    try {
      const updated = await adminApi.updateUser(row.app_user_id, { role: nextRole });
      setRows((prev) =>
        prev?.map((r) => (r.agent_id === row.agent_id ? updated : r)) ?? null,
      );
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Role update failed.");
    } finally {
      markPending(row.agent_id, false);
    }
  };

  // Default warehouse lives on the Evolution agent record. The only way an
  // AGENT's warehouse changes (they can't change it themselves).
  const handleWarehouseChange = async (row: AdminUserRow, code: string) => {
    if (!row.app_user_id || !code || code === row.default_warehouse_code) return;
    markPending(row.agent_id, true);
    setError(null);
    try {
      const updated = await adminApi.updateUser(row.app_user_id, { default_warehouse_code: code });
      setRows((prev) => prev?.map((r) => (r.agent_id === row.agent_id ? updated : r)) ?? null);
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Warehouse update failed.");
    } finally {
      markPending(row.agent_id, false);
    }
  };

  const handleActiveToggle = async (row: AdminUserRow, nextActive: boolean) => {
    if (!row.app_user_id) return;
    markPending(row.agent_id, true);
    setError(null);
    try {
      const updated = await adminApi.updateUser(row.app_user_id, { is_active: nextActive });
      setRows((prev) =>
        prev?.map((r) => (r.agent_id === row.agent_id ? updated : r)) ?? null,
      );
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? e?.message ?? "Active toggle failed.");
    } finally {
      markPending(row.agent_id, false);
    }
  };

  return (
    <div className="h-full bg-gray-50 p-2 flex flex-col">
      <div className="max-w-[1200px] mx-auto w-full flex-1 min-h-0 flex flex-col gap-2">
        {onBack && (
          <div className="flex items-center flex-shrink-0">
            <button
              onClick={onBack}
              className="flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800"
            >
              <ArrowLeft className="w-3.5 h-3.5" />
              Back to Analytics
            </button>
          </div>
        )}

        <div className="flex items-center gap-2 flex-wrap flex-shrink-0 bg-white rounded border p-2 shadow-sm">
          <Shield className="w-4 h-4 text-purple-600" />
          <div className="text-sm font-semibold text-gray-900">Users &amp; Roles</div>
          <span className="inline-flex items-center gap-1 text-xs text-gray-600" title="Active in the app in the last ~2.5 minutes">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            {onlineCount} online now
          </span>
          <div className="flex-1" />
          <div className="inline-flex rounded border border-gray-300 overflow-hidden text-xs">
            {((isAdmin ? ["registered", "all", "unregistered", "inactive"] : ["registered", "inactive"]) as Filter[]).map((k, i) => (
              <button
                key={k}
                onClick={() => setFilter(k)}
                className={`px-2 py-1 capitalize ${i > 0 ? "border-l border-gray-300" : ""} ${
                  filter === k
                    ? "bg-blue-600 text-white font-semibold"
                    : "bg-white text-gray-700 hover:bg-gray-50"
                }`}
              >
                {k}
              </button>
            ))}
          </div>
          <input
            type="text"
            placeholder="Search agent name…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="border border-gray-300 rounded px-2 py-1 text-xs focus:border-blue-500 focus:outline-none min-w-[14rem]"
          />
        </div>

        {error && (
          <div className="p-1 bg-red-100 border border-red-300 text-red-800 rounded flex items-center text-xs flex-shrink-0">
            <XCircle className="w-3 h-3 mr-1 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="bg-white rounded border shadow-sm flex-1 min-h-0 flex flex-col overflow-hidden">
          {loading ? (
            <div className="h-full flex items-center justify-center text-gray-500 text-sm">
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
              Loading…
            </div>
          ) : filtered.length === 0 ? (
            <div className="h-full flex items-center justify-center text-gray-400 text-sm italic">
              No agents match this filter.
            </div>
          ) : (
            <div className="flex-1 overflow-auto">
              <table className="w-full text-xs border-collapse">
                <thead className="bg-gray-100 sticky top-0 z-10">
                  <tr className="text-gray-700 border-b border-gray-300">
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Agent</th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Status</th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Description</th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Role</th>
                    <th
                      className="text-left px-2 py-1.5 font-semibold whitespace-nowrap"
                      title="The warehouse their orders are captured from. Agents can't change their own; supervisors and admins can switch theirs in the top bar."
                    >
                      Warehouse
                    </th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Active</th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Registered</th>
                    <th className="w-56"></th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r) => (
                    <UserRow
                      key={r.agent_id}
                      row={r}
                      isSelf={myAgentId != null && r.agent_id === myAgentId}
                      isAdmin={isAdmin}
                      presence={presence.get(r.agent_name.toLowerCase())}
                      isPending={pending.has(r.agent_id)}
                      isAdding={addingAgent === r.agent_id}
                      onStartAdd={() => setAddingAgent(r.agent_id)}
                      onCancelAdd={() => setAddingAgent(null)}
                      onAdd={(role) => handleAdd(r.agent_id, role)}
                      onRoleChange={(role) => handleRoleChange(r, role)}
                      warehouses={warehouses}
                      onWarehouseChange={(code) => handleWarehouseChange(r, code)}
                      onActiveToggle={(active) => handleActiveToggle(r, active)}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function UserRow({
  row,
  isSelf,
  isAdmin,
  presence,
  isPending,
  isAdding,
  onStartAdd,
  onCancelAdd,
  onAdd,
  onRoleChange,
  warehouses,
  onWarehouseChange,
  onActiveToggle,
}: {
  row: AdminUserRow;
  isSelf: boolean;
  /** Viewer is an ADMIN; a SUPERVISOR may only change an AGENT's warehouse. */
  isAdmin: boolean;
  presence?: UserPresence;
  isPending: boolean;
  isAdding: boolean;
  onStartAdd: () => void;
  onCancelAdd: () => void;
  onAdd: (role: AppUserRole) => void;
  onRoleChange: (role: AppUserRole) => void;
  warehouses: Warehouse[];
  onWarehouseChange: (code: string) => void;
  onActiveToggle: (active: boolean) => void;
}) {
  const registered = row.app_user_id != null;
  const active = row.is_active ?? false;
  const roleCellLocked = (isSelf && registered) || !isAdmin;
  const warehouseLocked = !isAdmin && row.role !== "AGENT";

  return (
    <tr
      className={`border-b border-gray-100 ${
        registered ? (active ? "hover:bg-blue-50/40" : "bg-gray-50/60 text-gray-500") : "bg-amber-50/40"
      }`}
    >
      <td className="px-2 py-1.5 font-mono text-gray-900">
        {row.agent_name}
        {isSelf && (
          <span className="ml-1 text-[9px] uppercase tracking-wide text-blue-600 font-semibold">
            you
          </span>
        )}
      </td>
      <td className="px-2 py-1.5 whitespace-nowrap">
        <PresenceCell presence={presence} />
      </td>
      <td className="px-2 py-1.5 text-gray-600 text-[11px]">{row.agent_description || "—"}</td>
      <td className="px-2 py-1.5">
        {registered && row.role ? (
          <select
            value={row.role}
            disabled={isPending || roleCellLocked}
            onChange={(e) => onRoleChange(e.target.value as AppUserRole)}
            className={`text-xs border rounded px-1.5 py-0.5 font-semibold ${
              ROLE_CHIP_CLASS[row.role]
            } ${isPending || roleCellLocked ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}`}
            title={
              !isAdmin
                ? "Only an admin can change roles."
                : roleCellLocked
                  ? "You can't change your own role. Ask another admin."
                  : undefined
            }
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-[11px] italic text-amber-700">Not registered</span>
        )}
      </td>
      <td className="px-2 py-1.5">
        {registered ? (
          <select
            value={row.default_warehouse_code ?? ""}
            disabled={isPending || warehouseLocked}
            title={warehouseLocked ? "Supervisors can only change an agent's warehouse." : undefined}
            onChange={(e) => onWarehouseChange(e.target.value)}
            className={`text-xs border rounded px-1.5 py-0.5 ${
              row.default_warehouse_code ? "bg-white" : "bg-amber-50 border-amber-300 text-amber-800"
            } ${isPending || warehouseLocked ? "opacity-60 cursor-not-allowed" : "cursor-pointer"}`}
          >
            {!row.default_warehouse_code && <option value="">Not set</option>}
            {row.default_warehouse_code &&
              !warehouses.some((w) => w.code === row.default_warehouse_code) && (
                <option value={row.default_warehouse_code}>{row.default_warehouse_code}</option>
              )}
            {warehouses.map((w) => (
              <option key={w.code} value={w.code}>
                {shortWarehouse(w.code)} — {w.description}
              </option>
            ))}
          </select>
        ) : (
          <span className="text-[11px] text-gray-400">{shortWarehouse(row.default_warehouse_code) || "—"}</span>
        )}
      </td>
      <td className="px-2 py-1.5">
        {registered ? (
          <button
            onClick={() => onActiveToggle(!active)}
            disabled={isPending || (isSelf && active) || !isAdmin}
            className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded border ${
              active
                ? "bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100"
                : "bg-gray-100 text-gray-600 border-gray-200 hover:bg-gray-200"
            } ${isPending || (isSelf && active) || !isAdmin ? "opacity-60 cursor-not-allowed" : ""}`}
            title={
              isSelf && active
                ? "You can't deactivate yourself. Ask another admin."
                : active
                  ? "Deactivate — user can no longer log in"
                  : "Reactivate — allow the user to log in again"
            }
          >
            {active ? <UserCheck className="w-3 h-3" /> : <UserX className="w-3 h-3" />}
            {active ? "Active" : "Inactive"}
          </button>
        ) : (
          <span className="text-[11px] text-gray-400">—</span>
        )}
      </td>
      <td className="px-2 py-1.5 text-[10px] text-gray-500">
        {row.registered_at
          ? `${fmtDate(row.registered_at)} by ${row.registered_by ?? "?"}`
          : "—"}
      </td>
      <td className="px-2 py-1.5 text-right">
        {!registered && isAdmin &&
          (isAdding ? (
            <div className="inline-flex items-center gap-1">
              {ROLES.map((r) => (
                <button
                  key={r}
                  onClick={() => onAdd(r)}
                  disabled={isPending}
                  className={`px-2 py-0.5 text-[11px] font-semibold rounded border ${ROLE_CHIP_CLASS[r]} hover:brightness-95 disabled:opacity-50 disabled:cursor-not-allowed`}
                >
                  {ROLE_LABEL[r]}
                </button>
              ))}
              <button
                onClick={onCancelAdd}
                disabled={isPending}
                className="px-1.5 py-0.5 text-[11px] text-gray-500 hover:text-gray-800 disabled:opacity-50"
              >
                Cancel
              </button>
              {isPending && <Loader2 className="w-3 h-3 animate-spin text-gray-500" />}
            </div>
          ) : (
            <button
              onClick={onStartAdd}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-semibold text-blue-700 bg-white border border-dashed border-blue-400 rounded hover:bg-blue-50"
            >
              <Plus className="w-3 h-3" />
              Add
            </button>
          ))}
        {registered && isPending && (
          <Loader2 className="inline-block w-3 h-3 animate-spin text-gray-500" />
        )}
      </td>
    </tr>
  );
}

function hhmm(iso: string): string {
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? ""
    : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Online (active in the last ~2.5 min) / Away (signed in, idle) / blank. */
function PresenceCell({ presence }: { presence?: UserPresence }) {
  if (!presence) return <span className="text-[11px] text-gray-300">Offline</span>;
  const detail = `Signed in ${hhmm(presence.since)} · last active ${hhmm(presence.last_seen)}${
    presence.sessions > 1 ? ` · ${presence.sessions} sessions` : ""
  }`;
  return presence.online ? (
    <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-emerald-700" title={detail}>
      <span className="w-2 h-2 rounded-full bg-emerald-500" />
      Online
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-[11px] text-amber-700" title={detail}>
      <span className="w-2 h-2 rounded-full bg-amber-400" />
      Away · {hhmm(presence.last_seen)}
    </span>
  );
}
