import { useEffect, useState } from "react";
import { Lock, Warehouse as WarehouseIcon } from "lucide-react";
import { api } from "../api/client";
import type { Warehouse } from "../api/types";
import { useAuth } from "../context/AuthContext";
import { canChooseWarehouse, shortWarehouse } from "../utils/warehouse";
import { extractErrorMsg } from "../utils/format";

/** Top-bar warehouse indicator. Supervisors / admins can switch between the
 *  DCs here (it changes their default warehouse; an order being captured
 *  moves to the new warehouse and is re-priced). Agents see theirs locked —
 *  only an admin can change it on Users & Roles. */
export function WarehouseSwitcher() {
  const { defaultWarehouseCode, role, setDefaultWarehouseCode } = useAuth();
  const [choices, setChoices] = useState<Warehouse[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canChoose = canChooseWarehouse(role);

  useEffect(() => {
    if (!canChoose) return;
    api.listDefaultWarehouseChoices().then(setChoices).catch(() => setChoices([]));
  }, [canChoose]);

  if (!canChoose) {
    return (
      <span
        className="flex items-center gap-1 text-xs text-gray-600"
        title={`Your warehouse: ${defaultWarehouseCode}. Only an admin can change it.`}
      >
        <WarehouseIcon className="w-3.5 h-3.5 text-gray-500" />
        {shortWarehouse(defaultWarehouseCode)}
        <Lock className="w-3 h-3 text-gray-400" />
      </span>
    );
  }

  async function change(code: string) {
    if (!code || code === defaultWarehouseCode) return;
    setSaving(true);
    setError(null);
    try {
      await setDefaultWarehouseCode(code);
    } catch (e) {
      setError(extractErrorMsg(e, "Couldn't switch warehouse"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <label
      className="flex items-center gap-1 text-xs text-gray-600"
      title={error ?? "The warehouse new orders are captured from"}
    >
      <WarehouseIcon className={`w-3.5 h-3.5 ${error ? "text-red-500" : "text-gray-500"}`} />
      <select
        value={defaultWarehouseCode ?? ""}
        onChange={(e) => change(e.target.value)}
        disabled={saving}
        className="border rounded px-1 py-0.5 text-xs bg-white font-semibold text-gray-800"
        aria-label="Warehouse"
      >
        {choices.length === 0 && defaultWarehouseCode && (
          <option value={defaultWarehouseCode}>{shortWarehouse(defaultWarehouseCode)}</option>
        )}
        {choices.map((w) => (
          <option key={w.code} value={w.code}>
            {shortWarehouse(w.code)} — {w.description}
          </option>
        ))}
      </select>
    </label>
  );
}
