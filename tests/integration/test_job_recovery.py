"""Worker crash and retry behaviour.

The claim these tests check: a lease that expires lets another worker resume,
a resumed job does not redo finished replications, and a retry can never turn a
failed experiment into an apparent success.
"""

from __future__ import annotations

import os
import tempfile
from datetime import timedelta

import pytest


@pytest.fixture()
def db(monkeypatch):
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{handle.name}")

    import forklab_api.db as database

    database._engine = None
    database._SessionLocal = None
    database.init_db()
    yield database
    os.unlink(handle.name)


def seed_experiment(database, replications: int = 3, with_candidate: bool = True) -> str:
    from forklab_api.app import DEMO_ORG, _store_scenario
    from forklab_api.db import ExperimentRow, session_scope
    from forklab_domain import PolicyVersion, demo_scenario, fork

    baseline = demo_scenario(order_count=60)
    candidate = fork(baseline, "cand", "EDF", PolicyVersion(name="edf", priority_strategy="edf"))
    with session_scope() as session:
        _store_scenario(session, DEMO_ORG, baseline)
        if with_candidate:
            _store_scenario(session, DEMO_ORG, candidate)
        row = ExperimentRow(
            id="exp-recovery",
            organization_id=DEMO_ORG,
            baseline_scenario_id=baseline.scenario_id,
            candidate_scenario_id=candidate.scenario_id if with_candidate else None,
            replications=replications,
            state="queued",
            stage="queued",
        )
        session.add(row)
    return "exp-recovery"


def test_a_live_lease_prevents_a_second_worker_from_claiming(db):
    from forklab_api.jobs import claim_next

    seed_experiment(db)
    first = claim_next("worker-a")
    assert first == "exp-recovery"
    assert claim_next("worker-b") is None


def test_an_expired_lease_lets_another_worker_resume(db):
    from forklab_api.db import ExperimentRow, session_scope, utcnow
    from forklab_api.jobs import claim_next

    seed_experiment(db)
    assert claim_next("worker-a") == "exp-recovery"

    # Simulate worker-a dying: its lease lapses without a terminal state.
    with session_scope() as session:
        row = session.get(ExperimentRow, "exp-recovery")
        row.lease_expires_at = utcnow() - timedelta(seconds=120)

    assert claim_next("worker-b") == "exp-recovery"
    with session_scope() as session:
        assert session.get(ExperimentRow, "exp-recovery").worker_id == "worker-b"


def test_resuming_does_not_redo_finished_replications(db):
    from forklab_api.db import ReplicationRow, session_scope
    from forklab_api.jobs import claim_next, execute

    experiment_id = seed_experiment(db, replications=3)
    claim_next("worker-a")
    execute(experiment_id, "worker-a")

    with session_scope() as session:
        before = session.query(ReplicationRow).count()
    assert before == 6

    # Running the same job again must not append duplicate replication rows.
    execute(experiment_id, "worker-a")
    with session_scope() as session:
        after = session.query(ReplicationRow).count()
    assert after == before


def test_a_completed_run_records_every_paired_seed(db):
    from forklab_api.db import ExperimentRow, ReplicationRow, session_scope
    from forklab_api.jobs import claim_next, execute

    experiment_id = seed_experiment(db, replications=4)
    claim_next("worker-a")
    assert execute(experiment_id, "worker-a") == "succeeded"

    with session_scope() as session:
        rows = session.query(ReplicationRow).all()
        experiment = session.get(ExperimentRow, experiment_id)
        pairs = {(row.arm, row.seed) for row in rows}
        report = experiment.report

    assert pairs == {(arm, seed) for arm in ("baseline", "candidate") for seed in (1, 2, 3, 4)}
    assert report["replications"] == 4
    assert report["seeds"] == [1, 2, 3, 4]


def test_cancellation_stops_the_run_and_is_not_reported_as_success(db):
    from forklab_api.db import ExperimentRow, session_scope
    from forklab_api.jobs import claim_next, execute

    experiment_id = seed_experiment(db, replications=8)
    claim_next("worker-a")
    with session_scope() as session:
        session.get(ExperimentRow, experiment_id).cancel_requested = 1

    assert execute(experiment_id, "worker-a") == "cancelled"
    with session_scope() as session:
        row = session.get(ExperimentRow, experiment_id)
    assert row.state == "cancelled"
    assert row.report is None


def test_a_job_that_exhausts_its_attempts_is_marked_failed(db):
    from forklab_api.db import ExperimentRow, session_scope, utcnow
    from forklab_api.jobs import MAX_ATTEMPTS, claim_next

    seed_experiment(db)
    with session_scope() as session:
        row = session.get(ExperimentRow, "exp-recovery")
        row.attempts = MAX_ATTEMPTS
        row.state = "running"
        row.lease_expires_at = utcnow() - timedelta(seconds=120)

    assert claim_next("worker-a") is None
    with session_scope() as session:
        row = session.get(ExperimentRow, "exp-recovery")
    assert row.state == "failed"
    assert "attempts" in (row.error or "")
    assert row.report is None


def test_stale_worker_cannot_heartbeat_finish_or_write(db):
    from forklab_api.db import ExperimentRow, ReplicationRow, session_scope, utcnow
    from forklab_api.jobs import _finish, claim_next, execute, heartbeat

    job = seed_experiment(db)
    claim_next("old")
    with session_scope() as session:
        session.get(ExperimentRow, job).lease_expires_at = utcnow() - timedelta(seconds=1)
    assert claim_next("new") == job
    assert heartbeat(job, "old", "stale", 9) is False
    assert _finish(job, "old", "succeeded", {}, None) == "lease_lost"
    assert execute(job, "old") == "lease_lost"
    with session_scope() as session:
        assert session.query(ReplicationRow).count() == 0
        assert session.get(ExperimentRow, job).worker_id == "new"
    assert execute(job, "new") == "succeeded"


def test_report_uses_checkpoints_without_simulating_twice(db, monkeypatch):
    import forklab_api.jobs as jobs

    job = seed_experiment(db, replications=3)
    real = jobs.simulate
    calls = []

    def counted(scenario, seed):
        calls.append((scenario.scenario_id, seed))
        return real(scenario, seed)

    monkeypatch.setattr(jobs, "simulate", counted)
    jobs.claim_next("worker")
    assert jobs.execute(job, "worker") == "succeeded"
    assert len(calls) == 6


def test_invariants_are_checked_after_first_seed(db, monkeypatch):
    import forklab_api.jobs as jobs

    job = seed_experiment(db, replications=3)
    real = jobs.check_run
    monkeypatch.setattr(
        jobs,
        "check_run",
        lambda scenario, run: ["second seed failure"] if run.seed == 2 else real(scenario, run),
    )
    jobs.claim_next("worker")
    assert jobs.execute(job, "worker") == "failed"


def test_cancellation_during_last_simulation_wins(db, monkeypatch):
    import forklab_api.jobs as jobs
    from forklab_api.db import ExperimentRow, session_scope

    job = seed_experiment(db, replications=1, with_candidate=False)
    real = jobs.simulate

    def cancel_after_run(scenario, seed):
        result = real(scenario, seed)
        with session_scope() as session:
            session.get(ExperimentRow, job).cancel_requested = 1
        return result

    monkeypatch.setattr(jobs, "simulate", cancel_after_run)
    jobs.claim_next("worker")
    assert jobs.execute(job, "worker") == "cancelled"
