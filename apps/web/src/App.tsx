import { useCallback, useEffect, useMemo, useState } from "react";
import type { Experiment, OrderOutcome, Policy, ScenarioSummary } from "./api";
import { api, clock } from "./api";
import { ImportForm } from "./components/ImportForm";
import { AssumptionsPanel } from "./components/AssumptionsPanel";
import { ComparisonTable } from "./components/ComparisonTable";
import { ForkForm } from "./components/ForkForm";
import { OrderInspector } from "./components/OrderInspector";
import { ProcessMap } from "./components/ProcessMap";
import { TimeScrubber } from "./components/TimeScrubber";

const REPLICATIONS = 30;

/* The experiment record is durable on the server. This remembers which run the
   tab was watching so a refresh, a sleep, or a closed laptop reattaches to it
   rather than losing the run. The tab observes a job; it never owns it. */
const WATCHING_KEY = "forklab.watching";

interface Watching {
  experimentId: string;
  candidateId: string | null;
}

function readWatching(): Watching | null {
  try {
    const raw = window.localStorage.getItem(WATCHING_KEY);
    const value = raw ? JSON.parse(raw) : null;
    return value && typeof value.experimentId === "string" ? value : null;
  } catch {
    return null;
  }
}

function writeWatching(value: Watching | null): void {
  try {
    if (value === null) window.localStorage.removeItem(WATCHING_KEY);
    else window.localStorage.setItem(WATCHING_KEY, JSON.stringify(value));
  } catch {
    /* Private browsing and blocked storage are fine; the run still completes. */
  }
}

type Lane = "baseline" | "candidate";

export default function App() {
  const [scenarios, setScenarios] = useState<ScenarioSummary[]>([]);
  const [baselineId, setBaselineId] = useState("demo-baseline");
  const [candidateId, setCandidateId] = useState<string | null>(null);
  const [activeLane, setActiveLane] = useState<Lane>("baseline");
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [minute, setMinute] = useState(240);
  const [orders, setOrders] = useState<Record<Lane, OrderOutcome[]>>({
    baseline: [],
    candidate: [],
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const baseline = useMemo(
    () => scenarios.find((s) => s.id === baselineId) ?? scenarios[0] ?? null,
    [scenarios, baselineId],
  );
  const candidate = useMemo(
    () => scenarios.find((s) => s.id === candidateId) ?? null,
    [scenarios, candidateId],
  );
  const active = activeLane === "candidate" && candidate ? candidate : baseline;

  const refreshScenarios = useCallback(async () => {
    try {
      setScenarios(await api.scenarios());
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    void refreshScenarios();
  }, [refreshScenarios]);

  useEffect(() => {
    if (experiment?.state !== "succeeded") return;
    let cancelled = false;
    void Promise.all([
      api.orders(experiment.id, "baseline", false),
      experiment.candidate_scenario_id
        ? api.orders(experiment.id, "candidate", false)
        : Promise.resolve([]),
    ])
      .then(([baseline, candidate]) => {
        if (!cancelled) setOrders({ baseline, candidate });
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [experiment?.id, experiment?.state]);

  /* Reattach to whatever this tab was last watching. */
  useEffect(() => {
    const saved = readWatching();
    if (!saved) return;
    let cancelled = false;
    void api
      .experiment(saved.experimentId)
      .then((body) => {
        if (cancelled) return;
        setExperiment(body);
        setBaselineId(body.baseline_scenario_id);
        setCandidateId(body.candidate_scenario_id);
        if (saved.candidateId) setActiveLane("candidate");
        if (body.state === "succeeded") {
          setBusy(false);
        } else if (["queued", "running"].includes(body.state)) {
          setBusy(true);
        }
      })
      .catch(() => writeWatching(null));
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!experiment || !["queued", "running"].includes(experiment.state))
      return;
    let cancelled = false;
    let pending = false;
    const id = experiment.id;
    const poll = async () => {
      if (pending) return;
      pending = true;
      try {
        const body = await api.experiment(id);
        if (cancelled) return;
        setExperiment(body);
        if (!["queued", "running"].includes(body.state)) {
          setBusy(false);
        }
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
      } finally {
        pending = false;
      }
    };
    const timer = window.setInterval(() => {
      void poll();
    }, 1500);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [experiment?.id, experiment?.state]);

  const handleFork = async (label: string, policy: Partial<Policy>) => {
    if (!baseline) return;
    setBusy(true);
    try {
      const forked = await api.fork(baseline.id, label, policy);
      await refreshScenarios();
      setCandidateId(forked.id);
      setActiveLane("candidate");
      setExperiment(null);
      writeWatching(null);
      setOrders({ baseline: [], candidate: [] });
      setError(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleRun = async () => {
    if (!baseline) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createExperiment(
        baseline.id,
        candidateId,
        REPLICATIONS,
      );
      setExperiment(created);
      writeWatching({ experimentId: created.id, candidateId });
      setOrders({ baseline: [], candidate: [] });
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (!experiment) return;
    try {
      setExperiment(await api.cancel(experiment.id));
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const handleExport = async () => {
    if (!experiment) return;
    try {
      const manifest = await api.manifest(experiment.id);
      const blob = new Blob([JSON.stringify(manifest, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `forklab-decision-${experiment.id}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const completedReport =
    experiment?.state === "succeeded" ? experiment.report : null;
  const report =
    completedReport && "metrics" in completedReport ? completedReport : null;
  const stale = Boolean(
    experiment &&
      candidate &&
      experiment.candidate_scenario_id !== candidate.id,
  );
  const shiftEnd = baseline?.facility.shift.shift_end_minute ?? 540;
  const maxMinute = Math.ceil(
    Math.max(
      shiftEnd +
        Math.max(
          baseline?.policy.allowed_overtime_minutes ?? 0,
          candidate?.policy.allowed_overtime_minutes ?? 0,
        ),
      ...Object.values(orders)
        .flat()
        .flatMap((o) => [
          o.arrival_minute,
          o.pick_end_minute ?? 0,
          o.pack_end_minute ?? 0,
          o.dispatched_minute ?? 0,
        ]),
    ),
  );

  if (!baseline) {
    return (
      <div className="app">
        <p className="banner warning">
          {error ?? "Loading warehouse studies."}
        </p>
      </div>
    );
  }

  return (
    <>
      <a className="skip-link" href="#comparison">
        Skip to the comparison table
      </a>
      <div className="app">
        <header className="masthead">
          <div className="brand">
            <img src="/brand/forklab-mark.svg" alt="" width="42" height="42" />
            <h1>
              ForkLab<span>Decision workbench</span>
            </h1>
          </div>
          <span className="context">
            {baseline.facility.facility_id.replace(/-/g, " ")} / dispatch study
          </span>
          <span className="spacer" />
          <button
            className="btn dark"
            onClick={handleExport}
            disabled={!report}
            aria-describedby="export-reason"
          >
            Export decision
          </button>
        </header>

        {!report && (
          <p
            id="export-reason"
            className="btn-reason"
            style={{ marginTop: -16 }}
          >
            Export becomes available once a two lane comparison has completed.
          </p>
        )}

        <section className="study-heading">
          <div>
            <p className="eyebrow">Warehouse policy lab</p>
            <h2>
              One warehouse.
              <br />
              Two possible days.
            </h2>
            <p>
              Change a dispatch decision. Trace its consequences.
              <br />
              Keep the evidence that lets you choose.
            </p>
          </div>
          <dl className="study-facts">
            <div>
              <dt>Orders in this study</dt>
              <dd>{baseline.orders.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Inventory lines</dt>
              <dd>{baseline.skus}</dd>
            </div>
            <div>
              <dt>Paired seeds</dt>
              <dd>{REPLICATIONS}</dd>
            </div>
          </dl>
        </section>
        <label className="dataset-picker">
          Study dataset{" "}
          <select
            disabled={busy}
            value={baseline.id}
            onChange={(e) => {
              setBaselineId(e.target.value);
              setCandidateId(null);
              setExperiment(null);
              setOrders({ baseline: [], candidate: [] });
              setActiveLane("baseline");
              writeWatching(null);
            }}
          >
            {scenarios
              .filter((s) => !s.parent_id)
              .map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
          </select>
        </label>
        <ImportForm
          disabled={busy}
          onImported={async (id) => {
            await refreshScenarios();
            setBaselineId(id);
            setCandidateId(null);
            setActiveLane("baseline");
            setExperiment(null);
            setOrders({ baseline: [], candidate: [] });
            writeWatching(null);
          }}
        />
        <p className="banner">
          {baseline.id.startsWith("demo")
            ? "Demonstration data."
            : "Imported data with estimated facility settings."}{" "}
          Simulated differences are evidence about the model, not a guarantee
          about a site.
        </p>

        {error && <p className="banner warning">{error}</p>}

        <div className="scenario-bar">
          <button
            className="tab"
            aria-pressed={activeLane === "baseline"}
            onClick={() => setActiveLane("baseline")}
          >
            <span className="role">baseline</span>
            {baseline.policy.priority_strategy}
          </button>
          {candidate && (
            <button
              className="tab"
              aria-pressed={activeLane === "candidate"}
              onClick={() => setActiveLane("candidate")}
            >
              <span className="role">candidate</span>
              {candidate.label}
            </button>
          )}
          <span className="spacer" style={{ flex: 1 }} />
          <button className="btn primary" onClick={handleRun} disabled={busy}>
            {busy ? "Running" : candidate ? "Run comparison" : "Run baseline"}
          </button>
        </div>

        {stale && (
          <p className="banner warning">
            The candidate changed after this result was produced. Run the
            comparison again before reading the numbers.
          </p>
        )}

        {experiment && (
          <div className="progress-strip" role="status" aria-live="polite">
            <span className="state-word">{experiment.state}</span>
            <span>{experiment.stage}</span>
            <div className="progress-track">
              <div
                className="progress-fill"
                style={{ width: `${Math.round(experiment.progress * 100)}%` }}
              />
            </div>
            <span>
              {experiment.completed_replications} of{" "}
              {experiment.replications *
                (experiment.candidate_scenario_id ? 2 : 1)}{" "}
              replications
            </span>
            {experiment.attempts > 1 && (
              <span className="footnote">
                attempt {experiment.attempts}, resumed
              </span>
            )}
            {["queued", "running"].includes(experiment.state) && (
              <button className="btn" onClick={handleCancel}>
                Stop
              </button>
            )}
            {experiment.error && (
              <span className="verdict regression">{experiment.error}</span>
            )}
          </div>
        )}

        <div className="workspace">
          <div className="panel">
            <div className="section-heading">
              <div>
                <p className="eyebrow">01 / Follow the flow</p>
                <h2>Warehouse at {clock(minute)}</h2>
              </div>
              <span className="footnote">
                Stored seed 1 • {orders.baseline.length} orders loaded
              </span>
            </div>
            {orders.baseline.length === 0 ? (
              <div className="process-preview">
                <img
                  src="/brand/warehouse-flow.svg"
                  alt="Orders pass through receiving, picking, packing and dispatch."
                />
                <p className="empty">
                  Run the baseline to populate the process map. Counts come from
                  a stored replication, not from a live animation.
                </p>
              </div>
            ) : (
              <>
                <ProcessMap
                  label="Baseline"
                  variant="baseline"
                  orders={orders.baseline}
                  minute={minute}
                  pickers={baseline.facility.pickers}
                  packingStations={baseline.facility.packing_stations}
                />
                {orders.candidate.length > 0 && candidate && (
                  <ProcessMap
                    label={candidate.label}
                    variant="candidate"
                    orders={orders.candidate}
                    minute={minute}
                    pickers={candidate.facility.pickers}
                    packingStations={candidate.facility.packing_stations}
                  />
                )}
              </>
            )}
          </div>

          <aside>
            <ForkForm
              key={baseline.id}
              baseline={baseline}
              onFork={handleFork}
              busy={busy}
            />
            {candidate && activeLane === "candidate" ? (
              <AssumptionsPanel scenario={candidate} baseline={baseline} />
            ) : (
              <AssumptionsPanel scenario={active ?? baseline} baseline={null} />
            )}
          </aside>
        </div>

        <TimeScrubber
          minute={minute}
          max={maxMinute}
          onChange={setMinute}
          disabled={orders.baseline.length === 0}
          cutoffMinute={(active ?? baseline).policy.dispatch_cutoff_minute}
        />

        <section id="comparison" className="panel">
          <p className="eyebrow">02 / Weigh the evidence</p>
          <h2>What changed, and how certain are we?</h2>
          {completedReport && !report && (
            <p className="banner">
              Baseline complete. Inspect its process below, then fork a policy
              to measure the difference.
            </p>
          )}
          <ComparisonTable report={report} />
        </section>

        <div>
          <OrderInspector
            experimentId={
              experiment?.state === "succeeded" ? experiment.id : null
            }
            arm={
              activeLane === "candidate" && candidate ? "candidate" : "baseline"
            }
          />
        </div>

        <footer className="footnote">
          Engine {experiment?.engine_version || "not run"}. The table is the
          numerical authority; the map explains mechanics. Every reported number
          comes from a stored replication that can be replayed with the exported
          manifest.
        </footer>
      </div>
    </>
  );
}
