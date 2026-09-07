"""Job claiming and execution.

The worker claims a job by writing its own id and a lease expiry in a single
conditional update. If the worker dies, the lease expires and another worker
may claim the job. Replications are checkpointed as they finish, so a retry
resumes rather than restarting, and a retry can never turn a failed experiment
into an apparent success because the final report is only written once every
replication of both arms is present.
"""

from __future__ import annotations

import time
import uuid
from datetime import timedelta

from forklab_domain import ENGINE_VERSION, Scenario, check_run, simulate
from forklab_eval import compare, run_metrics
from sqlalchemy import select, update

from .db import EventLogRow, ExperimentRow, ReplicationRow, ScenarioRow, session_scope, utcnow

LEASE_SECONDS = 60
MAX_ATTEMPTS = 3


def claim_next(worker_id: str) -> str | None:
    """Atomically claim one eligible job. Returns the experiment id or None."""
    now = utcnow()
    with session_scope() as session:
        candidates = session.scalars(
            select(ExperimentRow)
            .where(ExperimentRow.state.in_(("queued", "running")))
            .order_by(ExperimentRow.created_at)
            .limit(20)
        ).all()

        for row in candidates:
            leased = (
                row.lease_expires_at is not None
                and row.lease_expires_at.replace(tzinfo=row.lease_expires_at.tzinfo or now.tzinfo)
                > now
            )
            if row.state == "running" and leased:
                continue
            if row.attempts >= MAX_ATTEMPTS:
                session.execute(
                    update(ExperimentRow)
                    .where(ExperimentRow.id == row.id, ExperimentRow.state != "failed")
                    .values(
                        state="failed",
                        error=f"exceeded {MAX_ATTEMPTS} attempts",
                        updated_at=now,
                    )
                )
                continue

            result = session.execute(
                update(ExperimentRow)
                .where(
                    ExperimentRow.id == row.id,
                    ExperimentRow.state == row.state,
                    ExperimentRow.attempts == row.attempts,
                )
                .values(
                    state="running",
                    worker_id=worker_id,
                    attempts=row.attempts + 1,
                    lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
                    stage="claimed",
                    updated_at=now,
                )
            )
            if result.rowcount == 1:
                return row.id
    return None


def heartbeat(experiment_id: str, worker_id: str, stage: str, completed: int) -> bool:
    """Extend the lease. Returns False when cancellation has been requested."""
    with session_scope() as session:
        row = session.get(ExperimentRow, experiment_id)
        if row is None:
            return False
        row.lease_expires_at = utcnow() + timedelta(seconds=LEASE_SECONDS)
        row.stage = stage
        row.completed_replications = completed
        row.updated_at = utcnow()
        return not row.cancel_requested


def _load_scenario(session, scenario_id: str) -> Scenario:
    row = session.get(ScenarioRow, scenario_id)
    if row is None:
        raise LookupError(f"scenario {scenario_id} not found")
    return Scenario.model_validate(row.payload)


def _done_seeds(session, experiment_id: str, arm: str) -> set[int]:
    return set(
        session.scalars(
            select(ReplicationRow.seed).where(
                ReplicationRow.experiment_id == experiment_id, ReplicationRow.arm == arm
            )
        ).all()
    )


def execute(experiment_id: str, worker_id: str) -> str:
    """Run one experiment to completion. Returns the terminal state."""
    with session_scope() as session:
        row = session.get(ExperimentRow, experiment_id)
        if row is None:
            return "missing"
        baseline = _load_scenario(session, row.baseline_scenario_id)
        candidate = (
            _load_scenario(session, row.candidate_scenario_id)
            if row.candidate_scenario_id
            else None
        )
        replications = row.replications
        row.engine_version = ENGINE_VERSION

    seeds = list(range(1, replications + 1))
    arms: list[tuple[str, Scenario]] = [("baseline", baseline)]
    if candidate is not None:
        arms.append(("candidate", candidate))

    completed = 0

    for arm, scenario in arms:
        with session_scope() as session:
            already = _done_seeds(session, experiment_id, arm)
        completed += len(already)

        for seed in seeds:
            if seed in already:
                continue
            if not heartbeat(experiment_id, worker_id, f"{arm}:seed {seed}", completed):
                _finish(experiment_id, "cancelled", None, "cancellation requested")
                return "cancelled"

            started = time.perf_counter()
            result = simulate(scenario, seed)
            failures = check_run(scenario, result) if seed == seeds[0] else []
            metrics = run_metrics(scenario, result)
            elapsed = time.perf_counter() - started

            with session_scope() as session:
                # The unique work key is (experiment, arm, seed), so a retry that
                # re-runs a finished seed cannot create a duplicate row.
                if seed in _done_seeds(session, experiment_id, arm):
                    continue
                session.add(
                    ReplicationRow(
                        experiment_id=experiment_id,
                        arm=arm,
                        seed=seed,
                        metrics=metrics.model_dump(mode="json"),
                        invariant_failures=failures,
                        duration_seconds=round(elapsed, 6),
                    )
                )
                if seed == seeds[0]:
                    session.add(
                        EventLogRow(
                            experiment_id=experiment_id,
                            arm=arm,
                            seed=seed,
                            events=[e.model_dump(mode="json") for e in result.events],
                        )
                    )
            completed += 1

            if failures:
                _finish(
                    experiment_id,
                    "failed",
                    None,
                    "invariant failures: " + "; ".join(failures[:5]),
                )
                return "failed"

    # Only assemble the report once every replication of every arm exists.
    with session_scope() as session:
        rows = session.scalars(
            select(ReplicationRow).where(ReplicationRow.experiment_id == experiment_id)
        ).all()
    have = {(r.arm, r.seed) for r in rows}
    expected = {(arm, seed) for arm, _ in arms for seed in seeds}
    if have != expected:
        missing = len(expected - have)
        _finish(experiment_id, "failed", None, f"{missing} replications missing at assembly time")
        return "failed"

    if candidate is None:
        _finish(experiment_id, "succeeded", {"single_arm": True, "replications": len(seeds)}, None)
        return "succeeded"

    baseline_runs = [simulate(baseline, seed) for seed in seeds]
    candidate_runs = [simulate(candidate, seed) for seed in seeds]
    report = compare(baseline, baseline_runs, candidate, candidate_runs)
    _finish(experiment_id, "succeeded", report.model_dump(mode="json"), None)
    return "succeeded"


def _finish(experiment_id: str, state: str, report: dict | None, error: str | None) -> None:
    with session_scope() as session:
        row = session.get(ExperimentRow, experiment_id)
        if row is None:
            return
        row.state = state
        row.stage = state
        row.report = report
        row.error = error
        row.lease_expires_at = None
        row.updated_at = utcnow()
        if state == "succeeded":
            row.completed_replications = row.replications * (2 if row.candidate_scenario_id else 1)


def new_worker_id() -> str:
    return f"worker-{uuid.uuid4().hex[:8]}"
