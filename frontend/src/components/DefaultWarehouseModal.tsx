import { useEffect, useMemo, useState, type FormEvent } from "react";
import Select from "react-select";
import { Warehouse } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { api } from "../api/client";
import type { Warehouse as WarehouseOption } from "../api/types";

type Option = { value: string; label: string };

/**
 * One-time prompt shown after login when the telesales agent has no default
 * warehouse set in _rtblAgents.iDefWhseId. Writing the choice goes through
 * AuthContext → POST /auth/default-warehouse, which also updates the agent
 * row so this modal never appears again for that user.
 */
export function DefaultWarehouseModal() {
  const { setDefaultWarehouseCode } = useAuth();
  const [warehouses, setWarehouses] = useState<WarehouseOption[]>([]);
  const [code, setCode] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const options = useMemo<Option[]>(
    () =>
      warehouses.map((w) => ({
        value: w.code,
        label: `${w.code} — ${w.description}`,
      })),
    [warehouses],
  );

  useEffect(() => {
    let cancelled = false;
    api
      .listDefaultWarehouseChoices()
      .then((list) => {
        if (cancelled) return;
        setWarehouses(list);
        if (list.length === 1) setCode(list[0].code);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || "Failed to load warehouses");
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!code || isSaving) return;
    setIsSaving(true);
    setError(null);
    try {
      await setDefaultWarehouseCode(code);
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || "Failed to save default warehouse");
      setIsSaving(false);
    }
  }

  return (
    <div className="h-screen flex items-center justify-center bg-gray-50 p-4">
      <form
        onSubmit={onSubmit}
        className="bg-white rounded border shadow-sm p-6 w-full max-w-sm flex flex-col gap-3"
      >
        <div className="flex items-center gap-2 mb-2">
          <Warehouse className="w-5 h-5 text-blue-600" />
          <h1 className="text-base font-semibold text-gray-900">
            Pick your default warehouse
          </h1>
        </div>
        <p className="text-xs text-gray-500 -mt-1 mb-1">
          Pricing and stock defaults will use this warehouse. We only ask once —
          it's saved to your agent profile for future sessions.
        </p>

        <label className="flex flex-col gap-1 text-xs text-gray-700">
          Warehouse
          <Select<Option>
            autoFocus
            isDisabled={isLoading || isSaving}
            isLoading={isLoading}
            isSearchable
            options={options}
            value={options.find((o) => o.value === code) ?? null}
            onChange={(opt) => setCode(opt?.value ?? "")}
            placeholder={isLoading ? "Loading…" : "Search warehouses…"}
            noOptionsMessage={() => "No warehouses match"}
            styles={{
              control: (base) => ({
                ...base,
                minHeight: "34px",
                fontSize: "13px",
                borderColor: "#d1d5db",
                boxShadow: "none",
                "&:hover": { borderColor: "#3b82f6" },
              }),
              menu: (base) => ({ ...base, fontSize: "13px", zIndex: 40 }),
              option: (base, state) => ({
                ...base,
                fontSize: "13px",
                padding: "4px 8px",
                backgroundColor: state.isFocused ? "#eff6ff" : "white",
                color: "#1f2937",
              }),
            }}
          />
        </label>

        {error && (
          <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded px-2 py-1">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={!code || isLoading || isSaving}
          className="mt-1 bg-blue-600 text-white rounded px-3 py-2 text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
        >
          {isSaving ? "Saving…" : "Save and continue"}
        </button>
      </form>
    </div>
  );
}
