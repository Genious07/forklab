/* Typed API client. Mirrors the FastAPI schemas in services/api. */

export interface Policy {
  name: string;
  priority_strategy: "fifo" | "edf" | "batch_by_sku";
  batching_threshold: number;
  dispatch_cutoff_minute: number;
  allowed_overtime_minutes: number;
  tie_breaking_rule: "order_id" | "smallest_quantity" | "largest_quantity";
}

export interface Facility {
  facility_id: string;
  timezone: string;
  pickers: number;
  packing_stations: number;
  shift: { shift_start_minute: number; shift_end_minute: number };
  provenance: Record<string, "confirmed" | "estimated">;
  processing_times: Record<string, number>;
}

export interface ScenarioSummary {
  id: string;
  label: string;
  parent_id: string | null;
  input_digest: string;
  policy_digest: string;
  policy: Policy;
  policy_description: string;
  orders: number;
  skus: number;
  facility: Facility;
}

export type Verdict = "improvement" | "regression" | "inconclusive";

export interface MetricComparison {
  metric: string;
  label: string;
  direction: "lower_is_better" | "higher_is_better" | "neutral";
  baseline_mean: number;
  candidate_mean: number;
  mean_difference: number;
  ci_low: number;
  ci_high: number;
  verdict: Verdict;
}

export interface ComparisonReport {
  replications: number;
  seeds: number[];
  confidence: number;
  engine_version: string;
  metrics: MetricComparison[];
  notes: string[];
}

export interface Experiment {
  id: string;
  state: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  stage: string;
  replications: number;
  completed_replications: number;
  progress: number;
  baseline_scenario_id: string;
  candidate_scenario_id: string | null;
  engine_version: string;
  attempts: number;
  error: string | null;
  cancel_requested: boolean;
  report: ComparisonReport | null;
}

export interface OrderOutcome {
  order_id: string;
  service_class: "standard" | "express";
  arrival_minute: number;
  deadline_minute: number;
  pick_start_minute: number | null;
  pick_end_minute: number | null;
  pack_start_minute: number | null;
  pack_end_minute: number | null;
  dispatched_minute: number | null;
  terminal: string;
  blocked_on_stock: boolean;
  is_late: boolean;
  wait_minutes: number | null;
  cycle_minutes: number | null;
}

export interface OrderEvent {
  sequence: number;
  minute: number;
  order_id: string;
  event_type: string;
  resource_id: string | null;
  detail: Record<string, string | number>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* keep the status line */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string; engine_version: string }>("/health"),
  scenarios: () => request<ScenarioSummary[]>("/scenarios"),
  resetDemo: (orders: number) =>
    request<ScenarioSummary>(`/scenarios/demo?orders=${orders}`, { method: "POST" }),
  fork: (parentId: string, label: string, policy: Partial<Policy>) =>
    request<ScenarioSummary>(`/scenarios/${parentId}/fork`, {
      method: "POST",
      body: JSON.stringify({ label, policy }),
    }),
  createExperiment: (
    baseline: string,
    candidate: string | null,
    replications: number,
  ) =>
    request<Experiment>("/experiments", {
      method: "POST",
      body: JSON.stringify({
        baseline_scenario_id: baseline,
        candidate_scenario_id: candidate,
        replications,
      }),
    }),
  experiment: (id: string) => request<Experiment>(`/experiments/${id}`),
  cancel: (id: string) =>
    request<Experiment>(`/experiments/${id}/cancel`, { method: "POST" }),
  orders: (id: string, arm: "baseline" | "candidate", onlyLate: boolean) =>
    request<OrderOutcome[]>(
      `/experiments/${id}/orders?arm=${arm}&only_late=${onlyLate}&limit=500`,
    ),
  orderTimeline: (id: string, orderId: string, arm: "baseline" | "candidate") =>
    request<{ order_id: string; arm: string; seed: number; events: OrderEvent[] }>(
      `/experiments/${id}/orders/${orderId}?arm=${arm}`,
    ),
  manifest: (id: string) => request<Record<string, unknown>>(`/experiments/${id}/manifest`),
};

/** Minute 0 is the start of the facility day, taken here as 08:00 local time. */
export function clock(minute: number, dayStartMinutes = 8 * 60): string {
  const total = Math.max(0, Math.round(dayStartMinutes + minute));
  const hh = Math.floor(total / 60) % 24;
  const mm = total % 60;
  return `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

export function signed(value: number, digits = 4): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}
