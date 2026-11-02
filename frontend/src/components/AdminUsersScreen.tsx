import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, Loader2, Plus, Shield, UserCheck, UserX, XCircle } from "lucide-react";
import { adminApi, type AdminUserRow } from "../api/client";
import type { AppUserRole } from "../api/client";
import { useAuth } from "../context/AuthContext";

/** Analytics → Users & Roles. ADMIN only. Lists every active
 *  Evolution agent with their current role (if any) and lets the
 *  admin add + change + deactivate. The backend enforces ADMIN role
 *  separately so a non-admin hitting these endpoints 403s even if the
 *  frontend hides the entrypoint. */

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
  const { agentId: myAgentId } = useAuth();
  const [rows, setRows] = useState<AdminUserRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
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
          <div className="flex-1" />
          <div className="inline-flex rounded border border-gray-300 overflow-hidden text-xs">
            {(["all", "registered", "unregistered", "inactive"] as Filter[]).map((k, i) => (
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
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Description</th>
                    <th className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">Role</th>
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
                      isPending={pending.has(r.agent_id)}
                      isAdding={addingAgent === r.agent_id}
                      onStartAdd={() => setAddingAgent(r.agent_id)}
                      onCancelAdd={() => setAddingAgent(null)}
                      onAdd={(role) => handleAdd(r.agent_id, role)}
                      onRoleChange={(role) => handleRoleChange(r, role)}
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
  isPending,
  isAdding,
  onStartAdd,
  onCancelAdd,
  onAdd,
  onRoleChange,
  onActiveToggle,
}: {
  row: AdminUserRow;
  isSelf: boolean;
  isPending: boolean;
  isAdding: boolean;
  onStartAdd: () => void;
  onCancelAdd: () => void;
  onAdd: (role: AppUserRole) => void;
  onRoleChange: (role: AppUserRole) => void;
  onActiveToggle: (active: boolean) => void;
}) {
  const registered = row.app_user_id != null;
  const active = row.is_active ?? false;
  const roleCellLocked = isSelf && registered;

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
            title={roleCellLocked ? "You can't change your own role. Ask another admin." : undefined}
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
          <button
            onClick={() => onActiveToggle(!active)}
            disabled={isPending || (isSelf && active)}
            className={`inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded border ${
              active
                ? "bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100"
                : "bg-gray-100 text-gray-600 border-gray-200 hover:bg-gray-200"
            } ${isPending || (isSelf && active) ? "opacity-60 cursor-not-allowed" : ""}`}
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
        {!registered &&
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
