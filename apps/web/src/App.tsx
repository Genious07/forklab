import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Experiment, OrderOutcome, Policy, ScenarioSummary } from "./api";
import { api, clock } from "./api";
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
    return raw ? (JSON.parse(raw) as Watching) : null;
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
  const streamRef = useRef<EventSource | null>(null);
  const watchRef = useRef<((id: string, hasCandidate: boolean) => void) | null>(null);

  const baseline = useMemo(
    () => scenarios.find((s) => s.id === "demo-baseline") ?? scenarios[0] ?? null,
    [scenarios],
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
    return () => streamRef.current?.close();
  }, [refreshScenarios]);

  /* Load the per order outcomes that the process map reads. */
  const loadOrders = useCallback(async (experimentId: string, hasCandidate: boolean) => {
    const next: Record<Lane, OrderOutcome[]> = { baseline: [], candidate: [] };
    next.baseline = await api.orders(experimentId, "baseline", false);
    if (hasCandidate) next.candidate = await api.orders(experimentId, "candidate", false);
    setOrders(next);
  }, []);

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
        setCandidateId(saved.candidateId);
        if (saved.candidateId) setActiveLane("candidate");
        if (body.state === "succeeded") {
          void loadOrders(body.id, Boolean(body.candidate_scenario_id));
        } else if (["queued", "running"].includes(body.state)) {
          setBusy(true);
          watchRef.current?.(body.id, Boolean(body.candidate_scenario_id));
        }
      })
      .catch(() => writeWatching(null));
    return () => {
      cancelled = true;
    };
  }, [loadOrders]);

  const watch = useCallback(
    (experimentId: string, hasCandidate: boolean) => {
      streamRef.current?.close();
      const source = new EventSource(`/api/experiments/${experimentId}/stream`);
      streamRef.current = source;
      source.onmessage = (message) => {
        const body = JSON.parse(message.data) as Experiment;
        setExperiment(body);
        if (["succeeded", "failed", "cancelled"].includes(body.state)) {
          source.close();
          setBusy(false);
          if (body.state === "succeeded") void loadOrders(experimentId, hasCandidate);
        }
      };
      source.onerror = () => {
        /* Recover by polling from the persisted record rather than losing the run. */
        source.close();
        void api
          .experiment(experimentId)
          .then((body) => {
            setExperiment(body);
            if (body.state === "succeeded") void loadOrders(experimentId, hasCandidate);
            setBusy(!["succeeded", "failed", "cancelled"].includes(body.state));
          })
          .catch((err: Error) => setError(err.message));
      };
    },
    [loadOrders],
  );

  useEffect(() => {
    watchRef.current = watch;
  }, [watch]);

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
      const created = await api.createExperiment(baseline.id, candidateId, REPLICATIONS);
      setExperiment(created);
      writeWatching({ experimentId: created.id, candidateId });
      watch(created.id, Boolean(candidateId));
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

  const report = experiment?.state === "succeeded" ? experiment.report : null;
  const stale = Boolean(experiment && candidate && experiment.candidate_scenario_id !== candidate.id);
  const shiftEnd = baseline?.facility.shift.shift_end_minute ?? 540;
  const maxMinute = Math.round(shiftEnd + 120);

  if (!baseline) {
    return (
      <div className="app">
        <p className="banner warning">
          {error ?? "Loading the sample warehouse. Start the API with `make dev`."}
        </p>
      </div>
    );
  }

  return (
    <>
      <a className="skip-link" href="#comparison">Skip to the comparison table</a>
      <div className="app">
        <header className="masthead">
          <h1>ForkLab</h1>
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
          <p id="export-reason" className="btn-reason" style={{ marginTop: -16 }}>
            Export becomes available once a two lane comparison has completed.
          </p>
        )}

        <p className="banner">
          Demonstration data. This synthetic warehouse describes no real facility,
          customer, or supplier. Simulated differences are evidence about the model,
          not a guarantee about a site.
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
            The candidate changed after this result was produced. Run the comparison
            again before reading the numbers.
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
              {experiment.replications * (experiment.candidate_scenario_id ? 2 : 1)} replications
            </span>
            {experiment.attempts > 1 && (
              <span className="footnote">attempt {experiment.attempts}, resumed</span>
            )}
            {["queued", "running"].includes(experiment.state) && (
              <button className="btn" onClick={handleCancel}>Stop</button>
            )}
            {experiment.error && <span className="verdict regression">{experiment.error}</span>}
          </div>
        )}

        <div className="workspace">
          <div className="panel">
            <h2>Process at {clock(minute)}</h2>
            {orders.baseline.length === 0 ? (
              <p className="empty">
                Run the baseline to populate the process map. Counts come from a stored
                replication, not from a live animation.
              </p>
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

          {candidate && activeLane === "candidate" ? (
            <AssumptionsPanel scenario={candidate} baseline={baseline} />
          ) : (
            <AssumptionsPanel scenario={active ?? baseline} baseline={null} />
          )}
        </div>

        <TimeScrubber
          minute={minute}
          max={maxMinute}
          onChange={setMinute}
          disabled={orders.baseline.length === 0}
          cutoffMinute={(active ?? baseline).policy.dispatch_cutoff_minute}
        />

        <section id="comparison" className="panel">
          <h2>Comparison</h2>
          <ComparisonTable report={report} />
        </section>

        <div className="workspace">
          <OrderInspector
            experimentId={report ? experiment!.id : null}
            arm={activeLane === "candidate" && candidate ? "candidate" : "baseline"}
          />
          <ForkForm baseline={baseline} onFork={handleFork} busy={busy} />
        </div>

        <footer className="footnote">
          Engine {experiment?.engine_version || "not run"}. The table is the numerical
          authority; the map explains mechanics. Every reported number comes from a
          stored replication that can be replayed with the exported manifest.
        </footer>
      </div>
    </>
  );
}
