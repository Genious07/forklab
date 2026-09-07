"""Durable, lease-fenced execution with checkpoint-based report assembly."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta

from forklab_domain import ENGINE_VERSION, Scenario, check_run, simulate
from forklab_eval import run_metrics
from forklab_eval.compare import compare_metrics
from forklab_eval.metrics import RunMetrics
from sqlalchemy import or_, select, update

from .db import EventLogRow, ExperimentRow, ReplicationRow, ScenarioRow, session_scope, utcnow

LEASE_SECONDS = 60
MAX_ATTEMPTS = 3


def _ownership(experiment_id: str, worker_id: str):
    return (
        ExperimentRow.id == experiment_id,
        ExperimentRow.worker_id == worker_id,
        ExperimentRow.state == "running",
        ExperimentRow.lease_expires_at > utcnow(),
    )


def claim_next(worker_id: str) -> str | None:
    now = utcnow()
    eligible = (
        ExperimentRow.state.in_(("queued", "running")),
        or_(ExperimentRow.lease_expires_at.is_(None), ExperimentRow.lease_expires_at <= now),
    )
    with session_scope() as session:
        candidates = session.scalars(
            select(ExperimentRow).where(*eligible).order_by(ExperimentRow.created_at).limit(20)
        ).all()
        for row in candidates:
            conditions = (
                ExperimentRow.id == row.id,
                ExperimentRow.attempts == row.attempts,
                *eligible,
            )
            if row.attempts >= MAX_ATTEMPTS:
                session.execute(
                    update(ExperimentRow)
                    .execution_options(synchronize_session=False)
                    .where(*conditions)
                    .values(
                        state="failed",
                        stage="failed",
                        report=None,
                        error=f"exceeded {MAX_ATTEMPTS} attempts",
                        updated_at=now,
                    )
                )
                continue
            result = session.execute(
                update(ExperimentRow)
                .execution_options(synchronize_session=False)
                .where(*conditions)
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
    with session_scope() as session:
        result = session.execute(
            update(ExperimentRow)
            .execution_options(synchronize_session=False)
            .where(
                *_ownership(experiment_id, worker_id),
                ExperimentRow.cancel_requested == 0,
            )
            .values(
                lease_expires_at=utcnow() + timedelta(seconds=LEASE_SECONDS),
                stage=stage,
                completed_replications=completed,
                updated_at=utcnow(),
            )
        )
        return result.rowcount == 1


def _finish(
    experiment_id: str, worker_id: str, state: str, report: dict | None, error: str | None
) -> str:
    with session_scope() as session:
        # A write obtains the row lock; cancellation and takeover serialize with it.
        fence = session.execute(
            update(ExperimentRow)
            .execution_options(synchronize_session=False)
            .where(
                *_ownership(experiment_id, worker_id),
            )
            .values(updated_at=utcnow())
        )
        if fence.rowcount != 1:
            return "lease_lost"
        row = session.get(ExperimentRow, experiment_id)
        if row.cancel_requested:
            state, report, error = "cancelled", None, "cancellation requested"
        row.state = row.stage = state
        row.report, row.error, row.lease_expires_at = report, error, None
        if state == "succeeded":
            row.completed_replications = row.replications * (2 if row.candidate_scenario_id else 1)
    return state


def _load_scenario(session, scenario_id: str) -> Scenario:
    row = session.get(ScenarioRow, scenario_id)
    if row is None:
        raise LookupError(f"scenario {scenario_id} not found")
    return Scenario.model_validate(row.payload)


def _done_seeds(session, experiment_id: str, arm: str) -> set[int]:
    return set(
        session.scalars(
            select(ReplicationRow.seed).where(
                ReplicationRow.experiment_id == experiment_id,
                ReplicationRow.arm == arm,
            )
        ).all()
    )


def _renew_until_stopped(stop: threading.Event, experiment_id: str, worker_id: str) -> None:
    while not stop.wait(LEASE_SECONDS / 3):
        try:
            with session_scope() as session:
                result = session.execute(
                    update(ExperimentRow)
                    .execution_options(synchronize_session=False)
                    .where(
                        *_ownership(experiment_id, worker_id),
                        ExperimentRow.cancel_requested == 0,
                    )
                    .values(lease_expires_at=utcnow() + timedelta(seconds=LEASE_SECONDS))
                )
                if result.rowcount != 1:
                    return
        except Exception:
            # A database outage does not grant authority to a stale worker.
            return


def execute(experiment_id: str, worker_id: str) -> str:
    stop = threading.Event()
    renewer = threading.Thread(
        target=_renew_until_stopped, args=(stop, experiment_id, worker_id), daemon=True
    )
    renewer.start()
    try:
        return _execute(experiment_id, worker_id)
    except Exception as exc:
        return _finish(experiment_id, worker_id, "failed", None, f"{type(exc).__name__}: {exc}")
    finally:
        stop.set()
        renewer.join(timeout=2)


def _execute(experiment_id: str, worker_id: str) -> str:
    with session_scope() as session:
        fence = session.execute(
            update(ExperimentRow)
            .execution_options(synchronize_session=False)
            .where(*_ownership(experiment_id, worker_id))
            .values(updated_at=utcnow())
        )
        if fence.rowcount != 1:
            return "lease_lost"
        row = session.scalars(
            select(ExperimentRow).where(*_ownership(experiment_id, worker_id))
        ).first()
        if row is None:
            return "lease_lost"
        baseline = _load_scenario(session, row.baseline_scenario_id)
        candidate = (
            _load_scenario(session, row.candidate_scenario_id)
            if row.candidate_scenario_id
            else None
        )
        replications = row.replications
        if row.engine_version and row.engine_version != ENGINE_VERSION:
            raise ValueError(
                "engine version changed; create a new experiment instead of mixing checkpoints"
            )
        row.engine_version = ENGINE_VERSION

    seeds = list(range(1, replications + 1))
    arms = [("baseline", baseline)] + ([("candidate", candidate)] if candidate else [])
    with session_scope() as session:
        completed = len(
            session.scalars(
                select(ReplicationRow.id).where(
                    ReplicationRow.experiment_id == experiment_id,
                )
            ).all()
        )
    for arm, scenario in arms:
        with session_scope() as session:
            already = _done_seeds(session, experiment_id, arm)
        for seed in seeds:
            if seed in already:
                continue
            if not heartbeat(experiment_id, worker_id, f"{arm}:seed {seed}", completed):
                return _finish(
                    experiment_id, worker_id, "cancelled", None, "cancellation requested"
                )
            started = time.perf_counter()
            result = simulate(scenario, seed)
            failures = check_run(scenario, result)
            metrics = run_metrics(scenario, result)
            with session_scope() as session:
                fence = session.execute(
                    update(ExperimentRow)
                    .execution_options(synchronize_session=False)
                    .where(
                        *_ownership(experiment_id, worker_id),
                    )
                    .values(updated_at=utcnow())
                )
                if fence.rowcount != 1:
                    return "lease_lost"
                if seed in _done_seeds(session, experiment_id, arm):
                    continue
                session.add(
                    ReplicationRow(
                        experiment_id=experiment_id,
                        arm=arm,
                        seed=seed,
                        metrics=metrics.model_dump(mode="json"),
                        invariant_failures=failures,
                        duration_seconds=round(time.perf_counter() - started, 6),
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
                return _finish(
                    experiment_id,
                    worker_id,
                    "failed",
                    None,
                    "invariant failures: " + "; ".join(failures[:5]),
                )

    if not heartbeat(experiment_id, worker_id, "assembling report", completed):
        return _finish(experiment_id, worker_id, "cancelled", None, "cancellation requested")
    with session_scope() as session:
        rows = session.scalars(
            select(ReplicationRow).where(
                ReplicationRow.experiment_id == experiment_id,
            )
        ).all()
    expected = {(arm, seed) for arm, _ in arms for seed in seeds}
    if {(r.arm, r.seed) for r in rows} != expected or len(rows) != len(expected):
        raise ValueError("replication checkpoints are incomplete or duplicated")
    if any(r.invariant_failures for r in rows):
        raise ValueError("stored checkpoint contains invariant failures")
    if candidate is None:
        report = {"single_arm": True, "replications": len(seeds)}
    else:
        report = compare_metrics(
            baseline,
            [RunMetrics.model_validate(r.metrics) for r in rows if r.arm == "baseline"],
            candidate,
            [RunMetrics.model_validate(r.metrics) for r in rows if r.arm == "candidate"],
            ENGINE_VERSION,
        ).model_dump(mode="json")
    return _finish(experiment_id, worker_id, "succeeded", report, None)


def new_worker_id() -> str:
    return f"worker-{uuid.uuid4().hex[:12]}"
