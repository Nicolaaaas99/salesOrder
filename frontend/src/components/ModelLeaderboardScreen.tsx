import { useEffect, useState } from "react";
import { BarChart3, CheckCircle2, Circle, Loader2 } from "lucide-react";
import { forecastingApi, type ForecastModelRow } from "../api/forecasting";
import { extractErrorMsg } from "../utils/format";
import { ErrorBanner, ScreenFrame, fmtDate, fmtQty } from "./forecasting/ui";

/** Analytics → Model Leaderboard (SUPERVISOR+). Read-only accuracy per
 *  forecast model, from _vw_SOModelLeaderboard. Models never auto-promote. */
export function ModelLeaderboardScreen({ onBack }: { onBack?: () => void }) {
  const [rows, setRows] = useState<ForecastModelRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    forecastingApi.listModels().then(setRows).catch((e) => setError(extractErrorMsg(e, "Failed to load models.")));
  }, []);

  const pct = (v: number | null) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

  return (
    <ScreenFrame icon={<BarChart3 className="w-4 h-4 text-indigo-600" />} title="Model Leaderboard" onBack={onBack}>
      <ErrorBanner message={error} />
      <div className="bg-white rounded border shadow-sm overflow-auto">
        <table className="w-full text-xs">
          <thead className="bg-gray-50 text-gray-600 border-b">
            <tr>
              <th className="text-left px-3 py-2 font-medium">Model</th>
              <th className="text-left px-3 py-2 font-medium">Status</th>
              <th className="text-right px-3 py-2 font-medium" title="Weeks of customer-product forecasts that have actual sales to compare against">Weeks scored</th>
              <th className="text-right px-3 py-2 font-medium" title="Σ|error| ÷ Σ true demand — lower is better">Weighted MAPE</th>
              <th className="text-right px-3 py-2 font-medium" title="Average miss per customer-product per week, in units">MAE</th>
              <th className="text-right px-3 py-2 font-medium" title="Σ error ÷ Σ true demand. + over-forecasts, − under-forecasts">Bias</th>
              <th className="text-left px-3 py-2 font-medium">Scored weeks</th>
            </tr>
          </thead>
          <tbody>
            {rows === null && !error && (
              <tr><td colSpan={7} className="px-3 py-6 text-center text-gray-400"><Loader2 className="w-4 h-4 animate-spin inline" /></td></tr>
            )}
            {rows?.map((m) => (
              <tr key={m.model_code} className="border-b last:border-0">
                <td className="px-3 py-2">
                  <div className="font-medium text-gray-900">{m.model_name}</div>
                  <div className="text-[11px] text-gray-500 font-mono">{m.model_code} · {m.family}</div>
                </td>
                <td className="px-3 py-2">
                  {m.is_enabled ? (
                    <span className="inline-flex items-center gap-1 text-emerald-700"><CheckCircle2 className="w-3.5 h-3.5" /> Enabled</span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-gray-400"><Circle className="w-3.5 h-3.5" /> Disabled</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono">{fmtQty(m.leaves_scored)}</td>
                <td className="px-3 py-2 text-right font-mono">{pct(m.weighted_mape)}</td>
                <td className="px-3 py-2 text-right font-mono">{fmtQty(m.mae)}</td>
                <td className={`px-3 py-2 text-right font-mono ${m.bias != null && Math.abs(m.bias) > 0.1 ? "text-amber-700" : ""}`}>
                  {m.bias == null ? "—" : `${m.bias > 0 ? "+" : ""}${(m.bias * 100).toFixed(1)}%`}
                </td>
                <td className="px-3 py-2 text-gray-600">
                  {m.first_scored_week ? `${fmtDate(m.first_scored_week)} – ${fmtDate(m.last_scored_week)}` : "Not scored yet"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="bg-white rounded border shadow-sm p-3 text-xs text-gray-600 leading-relaxed">
        <div className="font-semibold text-gray-800 mb-1">Promotion gate</div>
        Models never switch on by themselves. A disabled model can only be promoted after{" "}
        <strong>90 days</strong> of shadow forecasts, at least <strong>50 scored customer-product weeks</strong>, a
        statistically significant improvement over the current model (paired t-test, p &lt; 0.05), and a
        manager's explicit approval. Accuracy fills in once weekly actuals are scored (Phase 6 nightly job).
      </div>
    </ScreenFrame>
  );
}
