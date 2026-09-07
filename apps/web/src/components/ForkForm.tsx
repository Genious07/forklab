import { useState } from "react";
import type { Policy, ScenarioSummary } from "../api";

interface Props {
  baseline: ScenarioSummary;
  onFork: (label: string, policy: Partial<Policy>) => Promise<void>;
  busy: boolean;
}

/* Forking changes the decision only. The workload, inventory, and process model
   are carried over unchanged so a paired comparison isolates the policy. */
export function ForkForm({ baseline, onFork, busy }: Props) {
  const [strategy, setStrategy] = useState<Policy["priority_strategy"]>("edf");
  const [cutoff, setCutoff] = useState(baseline.policy.dispatch_cutoff_minute);
  const [overtime, setOvertime] = useState(baseline.policy.allowed_overtime_minutes);
  const [ties, setTies] = useState<Policy["tie_breaking_rule"]>(
    baseline.policy.tie_breaking_rule,
  );

  const unchanged =
    strategy === baseline.policy.priority_strategy &&
    cutoff === baseline.policy.dispatch_cutoff_minute &&
    overtime === baseline.policy.allowed_overtime_minutes &&
    ties === baseline.policy.tie_breaking_rule;

  const label = `${strategy}, cutoff ${Math.round(cutoff)} min`;

  return (
    <form
      className="panel inspector"
      onSubmit={(event) => {
        event.preventDefault();
        void onFork(label, {
          name: "candidate",
          priority_strategy: strategy,
          dispatch_cutoff_minute: cutoff,
          allowed_overtime_minutes: overtime,
          tie_breaking_rule: ties,
        });
      }}
    >
      <h2>Fork a scenario</h2>
      <p className="footnote">
        This changes the decision, not the workload. The same orders, inventory, and
        process model carry over so the comparison isolates the policy.
      </p>

      <div className="field">
        <label htmlFor="strategy">Priority strategy</label>
        <select
          id="strategy"
          value={strategy}
          onChange={(e) => setStrategy(e.target.value as Policy["priority_strategy"])}
        >
          <option value="fifo">First in first out</option>
          <option value="edf">Earliest deadline first</option>
          <option value="batch_by_sku">Batch by SKU</option>
        </select>
      </div>

      <div className="field">
        <label htmlFor="cutoff">Dispatch cutoff, minutes from day start</label>
        <input
          id="cutoff"
          type="number"
          min={0}
          max={1440}
          step={15}
          value={cutoff}
          onChange={(e) => setCutoff(Number(e.target.value))}
        />
        <span className="hint">Baseline is {baseline.policy.dispatch_cutoff_minute}.</span>
      </div>

      <div className="field">
        <label htmlFor="overtime">Allowed overtime, minutes</label>
        <input
          id="overtime"
          type="number"
          min={0}
          max={480}
          step={15}
          value={overtime}
          onChange={(e) => setOvertime(Number(e.target.value))}
        />
      </div>

      <div className="field">
        <label htmlFor="ties">Tie breaking</label>
        <select
          id="ties"
          value={ties}
          onChange={(e) => setTies(e.target.value as Policy["tie_breaking_rule"])}
        >
          <option value="order_id">Order id</option>
          <option value="smallest_quantity">Smallest quantity</option>
          <option value="largest_quantity">Largest quantity</option>
        </select>
      </div>

      <button className="btn primary" type="submit" disabled={busy || unchanged}>
        Fork scenario
      </button>
      {unchanged && (
        <p className="btn-reason">
          Change at least one parameter. A fork identical to the baseline cannot
          produce a difference.
        </p>
      )}
    </form>
  );
}
