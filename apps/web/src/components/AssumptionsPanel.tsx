import type { Facility, Policy, ScenarioSummary } from "../api";
import { clock } from "../api";

/* Assumptions sit next to the result that depends on them, never hidden in a
   settings screen. Provenance is stated in words as well as colour. */

function Row({
  name,
  value,
  provenance,
}: {
  name: string;
  value: string;
  provenance: "confirmed" | "estimated" | "edited";
}) {
  return (
    <div className="assumption">
      <div>
        <div>{name}</div>
        <div className={`provenance ${provenance}`}>
          {provenance === "edited" ? "Edited in this fork" : provenance}
        </div>
      </div>
      <div className="value">{value}</div>
    </div>
  );
}

interface Props {
  scenario: ScenarioSummary;
  baseline: ScenarioSummary | null;
}

export function AssumptionsPanel({ scenario, baseline }: Props) {
  const facility: Facility = scenario.facility;
  const policy: Policy = scenario.policy;
  const basePolicy = baseline?.policy;

  const changed = (field: keyof Policy): "edited" | "confirmed" =>
    basePolicy && basePolicy[field] !== policy[field] ? "edited" : "confirmed";

  return (
    <div className="panel inspector">
      <h2>Model assumptions</h2>
      <Row
        name="Shift calendar"
        value={`${clock(facility.shift.shift_start_minute)} to ${clock(
          facility.shift.shift_end_minute,
        )}`}
        provenance={facility.provenance.shift ?? "estimated"}
      />
      <Row
        name="Pickers"
        value={String(facility.pickers)}
        provenance={facility.provenance.pickers ?? "estimated"}
      />
      <Row
        name="Packing stations"
        value={String(facility.packing_stations)}
        provenance={facility.provenance.packing_stations ?? "estimated"}
      />
      <Row
        name="Processing times"
        value="setup plus per unit"
        provenance={facility.provenance.processing_times ?? "estimated"}
      />

      <h3>Policy</h3>
      <Row
        name="Priority strategy"
        value={policy.priority_strategy}
        provenance={changed("priority_strategy")}
      />
      <Row
        name="Dispatch cutoff"
        value={clock(policy.dispatch_cutoff_minute)}
        provenance={changed("dispatch_cutoff_minute")}
      />
      <Row
        name="Allowed overtime"
        value={`${policy.allowed_overtime_minutes} min`}
        provenance={changed("allowed_overtime_minutes")}
      />
      <Row
        name="Tie breaking"
        value={policy.tie_breaking_rule.replace(/_/g, " ")}
        provenance={changed("tie_breaking_rule")}
      />

      <p className="footnote" style={{ marginTop: 16 }}>
        Workload {scenario.orders} orders across {scenario.skus} SKUs. Input
        digest {scenario.input_digest}, policy digest {scenario.policy_digest}.
      </p>
    </div>
  );
}
