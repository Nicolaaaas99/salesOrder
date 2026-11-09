/**
 * Forecasting & scenario management API (Phase 5). All endpoints are
 * SUPERVISOR+ on the backend. Shapes mirror backend/schemas.py.
 */
import { http } from "./client";

// ---------- scenarios ----------

export type LeverType = "PERCENT" | "ABSOLUTE_DELTA" | "FIXED_QTY";
/** What a scenario's lever value is in. */
export type TargetMeasure = "UNITS" | "LITRES";
/** Targets-screen level a target was set on. */
export type AnchorLevel = "company" | "dist_centre" | "route" | "customer" | "category" | "sku";
export type ScenarioStatus = "DRAFT" | "PREVIEW" | "ACTIVE" | "COMPLETED" | "CANCELLED";

export type ScenarioScope = {
  distCentre: string[];
  route: string[];
  customer: string[];
  category: string[];
  sku: string[];
  salesRep: string[];
  telesales: string[];
  excludeLeaves: { customer: string; sku: string }[];
};

export type ScopeDim = Exclude<keyof ScenarioScope, "excludeLeaves">;

export const SCOPE_DIMS: { key: ScopeDim; label: string }[] = [
  { key: "distCentre", label: "DC" },
  { key: "route", label: "Route" },
  { key: "customer", label: "Customer" },
  { key: "category", label: "Category" },
  { key: "sku", label: "Product" },
  { key: "salesRep", label: "Sales rep" },
  { key: "telesales", label: "Telesales" },
];

export const emptyScope = (): ScenarioScope => ({
  distCentre: [], route: [], customer: [], category: [], sku: [],
  salesRep: [], telesales: [], excludeLeaves: [],
});

export type Scenario = {
  id: number;
  name: string;
  description: string | null;
  scope: ScenarioScope;
  lever_type: LeverType;
  lever_value: number;
  period_from: string;
  period_to: string;
  status: ScenarioStatus;
  created_by: string;
  created_at: string;
  approved_by: string | null;
  approved_at: string | null;
  cancel_reason: string | null;
  cancelled_at: string | null;
  measure: TargetMeasure;
  anchor_level: AnchorLevel | null;
};

export type ScenarioInput = {
  name: string;
  description?: string | null;
  scope: ScenarioScope;
  lever_type: LeverType;
  lever_value: number;
  period_from: string;
  period_to: string;
  measure?: TargetMeasure;
  anchor_level?: AnchorLevel | null;
};

export type ReasonablenessFlag = {
  code: string;
  message: string;
  metric_value: number;
  threshold_value: number;
};

export type CascadedTarget = {
  customer_account: string;
  stock_code: string;
  week_start: string;
  route: string | null;
  dist_centre: string | null;
  category: string | null;
  forecast_qty: number;
  target_qty: number;
  override_qty: number | null;
  effective_target_qty: number;
  before_qty: number | null;
  in_scope: boolean;
  scenario_ids: number[];
  flags: ReasonablenessFlag[];
};

export type TargetConflict = {
  scenario_id: number | null;
  week: string;
  goal: number;
  inner_total: number;
  measure: TargetMeasure;
  message: string;
};

export type FlaggedLeaf = {
  customer_account: string;
  stock_code: string;
  flag_codes: string[];
  weeks: string[];
  messages: string[];
};

export type ScenarioCascade = {
  scenario: Scenario;
  as_of_week: string;
  weeks: string[];
  leaf_count: number;
  leaf_week_count: number;
  total_forecast_qty: number;
  total_target_qty: number;
  total_effective_target_qty: number;
  total_before_qty: number;
  rebalanced_count: number;
  rebalanced_delta_qty: number;
  conflicts: TargetConflict[];
  locked_skipped: number;
  warnings: string[];
  flagged_leaves: FlaggedLeaf[];
  targets: CascadedTarget[];
  targets_offset: number;
  rows_written: number | null;
  rollup_level: string | null;
  rollup_deltas: Record<string, number>;
  rollup_total_delta: number;
  rollup_scope_delta: number;
  rollup_outside_delta: number;
};

export type FlagReview = {
  customer_account: string;
  stock_code: string;
  decision: "ACCEPT" | "EXCLUDE";
  note?: string | null;
};

/** Error body for approve when flags are unreviewed (400) or an exclusion
 *  re-split the volume (409). */
export type UnreviewedError = {
  message: string;
  unreviewed: { customer_account: string; stock_code: string }[];
};

export const FLAG_LABELS: Record<string, string> = {
  above_historical_p95: "Above usual max (P95)",
  above_yoy_growth_limit: "> 25% vs last year",
  new_product_requires_review: "New product",
  below_pack_size_minimum: "Below 1 pack",
};

/** Plain-language warning groups for the Targets screen review. */
export const FLAG_GROUPS: Record<string, { title: string; explain: string }> = {
  above_historical_p95: {
    title: "Higher than they normally buy",
    explain: "The target is above what this customer buys in almost every week (their usual maximum).",
  },
  above_yoy_growth_limit: {
    title: "Big jump on last year",
    explain: "The target is more than 25% above the same week last year.",
  },
  new_product_requires_review: {
    title: "New product",
    explain: "The product was first sold less than 13 weeks ago, so there's little history to go on.",
  },
  below_pack_size_minimum: {
    title: "Less than one pack",
    explain: "The target is smaller than one full pack, so the rep can't order exactly that.",
  },
};

export type ReviewDecision = "ACCEPT" | "EXCLUDE";

// ---------- targets / workbench ----------

export type WorkbenchLevel = "dist_centre" | "route" | "customer" | "category" | "sku";
export type Measure = "units" | "litres";

export type WorkbenchRow = {
  key: string;
  label: string;
  forecast: number;
  target: number;
  delivered: number;
  on_order: number;
  committed: number;
  leaf_count: number;
  targeted_count: number;
  flagged_count: number;
  override_count: number;
  target_id: number | null;
  override: number | null;
  source: string | null;
  is_locked: boolean;
};

export type WorkbenchResponse = {
  week_start: string;
  level: WorkbenchLevel;
  measure: Measure;
  filters: Partial<Record<WorkbenchLevel, string>>;
  total: WorkbenchRow;
  rows: WorkbenchRow[];
};

export type ScenarioTargetRow = {
  id: number;
  customer_account: string;
  stock_code: string;
  week_start: string;
  forecast_qty: number;
  target_qty: number;
  override_qty: number | null;
  override_reason: string | null;
  override_by: string | null;
  source: string;
  scenario_ids: number[];
  is_flagged: boolean;
  flag_codes: string[];
  is_locked: boolean;
};

// ---------- models / tasks ----------

export type ForecastModelRow = {
  model_code: string;
  model_name: string;
  family: string;
  is_enabled: boolean;
  leaves_scored: number;
  weighted_mape: number | null;
  mae: number | null;
  bias: number | null;
  first_scored_week: string | null;
  last_scored_week: string | null;
};

export type TaskStatus = "OPEN" | "IN_PROGRESS" | "SNOOZED" | "RESOLVED" | "DISMISSED";

export type ForecastTask = {
  id: number;
  task_type: string;
  priority: "HIGH" | "MED" | "LOW";
  assigned_to: string | null;
  customer_account: string | null;
  stock_code: string | null;
  category: string | null;
  route: string | null;
  dist_centre: string | null;
  scenario_id: number | null;
  title: string;
  description: string | null;
  due_date: string;
  status: TaskStatus;
  resolution_notes: string | null;
  resolved_by: string | null;
  created_at: string;
  resolved_at: string | null;
};

export type ScopeOption = { value: string; label: string };

// ---------- calls ----------

export const forecastingApi = {
  listScenarios: (status?: ScenarioStatus) =>
    http.get<Scenario[]>("/forecasting/scenarios", { params: status ? { status } : {} }).then((r) => r.data),
  getScenario: (id: number) =>
    http.get<Scenario>(`/forecasting/scenarios/${id}`).then((r) => r.data),
  createScenario: (body: ScenarioInput) =>
    http.post<Scenario>("/forecasting/scenarios", body).then((r) => r.data),
  updateScenario: (id: number, body: Partial<ScenarioInput>) =>
    http.patch<Scenario>(`/forecasting/scenarios/${id}`, body).then((r) => r.data),
  previewScenario: (id: number, limit = 500, rollup?: {
    rollup_level: WorkbenchLevel;
    rollup_week: string;
    measure: Measure;
    dist_centre?: string;
    route?: string;
    customer?: string;
    category?: string;
  }) =>
    http.post<ScenarioCascade>(`/forecasting/scenarios/${id}/preview`, null, {
      params: { limit, ...(rollup ?? {}) },
    }).then((r) => r.data),
  approveScenario: (id: number, reviews: FlagReview[], groupDecisions: Record<string, ReviewDecision> = {}) =>
    http.post<ScenarioCascade>(`/forecasting/scenarios/${id}/approve`,
      { reviews, group_decisions: groupDecisions }, { params: { limit: 0 } }).then((r) => r.data),
  cancelScenario: (id: number, reason: string) =>
    http.post<{ scenario: Scenario; targets_recomputed: number; targets_deleted: number }>(
      `/forecasting/scenarios/${id}/cancel`, { reason },
    ).then((r) => r.data),

  scopeOptions: (dim: ScopeDim, q?: string, limit = 50) =>
    http.get<ScopeOption[]>("/forecasting/scope-options", {
      params: { dim, limit, ...(q ? { q } : {}) },
    }).then((r) => r.data),

  workbench: (params: {
    week: string;
    level: WorkbenchLevel;
    measure: Measure;
    dist_centre?: string;
    route?: string;
    customer?: string;
    category?: string;
  }) => http.get<WorkbenchResponse>("/forecasting/workbench", { params }).then((r) => r.data),

  overrideTarget: (id: number, body: { override_qty: number | null; reason: string; break_lock?: boolean }) =>
    http.patch<ScenarioTargetRow>(`/forecasting/targets/${id}/override`, body).then((r) => r.data),

  listModels: () => http.get<ForecastModelRow[]>("/forecasting/models").then((r) => r.data),

  listTasks: (params: { status?: "open" | "closed" | "all"; mine?: boolean; task_type?: string } = {}) =>
    http.get<ForecastTask[]>("/forecasting/tasks", { params }).then((r) => r.data),
  updateTask: (id: number, body: { status: TaskStatus; resolution_notes?: string | null }) =>
    http.patch<ForecastTask>(`/forecasting/tasks/${id}`, body).then((r) => r.data),
};
