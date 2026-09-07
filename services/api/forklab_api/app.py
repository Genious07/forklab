"""ForkLab HTTP API.

Every query is scoped by organization. Authentication is deliberately a single
pluggable dependency rather than an invented session scheme: the demo resolves
one organization, and a team deployment replaces `current_org` with a real
identity provider. See docs/limitations.md.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from forklab_domain import (
    ENGINE_VERSION,
    PolicyVersion,
    Scenario,
    build_report,
    demo_scenario,
    describe_policy,
    read_inventory,
    read_orders,
    read_replenishments,
)
from forklab_eval import outcomes as order_outcomes
from forklab_eval.compare import ComparisonReport
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .db import EventLogRow, ExperimentRow, ReplicationRow, ScenarioRow, init_db, session_scope
from .jobs import execute, new_worker_id

DEMO_ORG = "demo-org"
API_PREFIX = "/api"


def current_org(x_forklab_org: Annotated[str | None, Header()] = None) -> str:
    """Resolve the caller's organization.

    Replace this dependency with a real identity provider for a deployment that
    serves more than one organization.
    """
    return x_forklab_org or DEMO_ORG


OrgDep = Annotated[str, Depends(current_org)]


# ------------------------------------------------------------------- schemas


class PolicyIn(BaseModel):
    name: str = "candidate"
    priority_strategy: Literal["fifo", "edf", "batch_by_sku"] = "edf"
    batching_threshold: int = Field(default=1, ge=1, le=100)
    dispatch_cutoff_minute: float = Field(default=540.0, ge=0, le=1440)
    allowed_overtime_minutes: float = Field(default=0.0, ge=0, le=480)
    tie_breaking_rule: Literal["order_id", "smallest_quantity", "largest_quantity"] = "order_id"

    def to_policy(self) -> PolicyVersion:
        return PolicyVersion(**self.model_dump())


class ScenarioSummary(BaseModel):
    id: str
    label: str
    parent_id: str | None
    input_digest: str
    policy_digest: str
    policy: dict[str, Any]
    policy_description: str
    orders: int
    skus: int
    facility: dict[str, Any]


class ExperimentSummary(BaseModel):
    id: str
    state: str
    stage: str
    replications: int
    completed_replications: int
    progress: float
    baseline_scenario_id: str
    candidate_scenario_id: str | None
    engine_version: str
    attempts: int
    error: str | None
    cancel_requested: bool
    report: dict[str, Any] | None


class CreateExperiment(BaseModel):
    baseline_scenario_id: str
    candidate_scenario_id: str | None = None
    replications: int = Field(default=30, ge=1, le=200)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ForkRequest(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    policy: PolicyIn


# ------------------------------------------------------------------- helpers


def _summary(row: ScenarioRow) -> ScenarioSummary:
    scenario = Scenario.model_validate(row.payload)
    return ScenarioSummary(
        id=row.id,
        label=row.label,
        parent_id=row.parent_id,
        input_digest=row.input_digest,
        policy_digest=row.policy_digest,
        policy=scenario.policy.model_dump(mode="json"),
        policy_description=describe_policy(scenario.policy),
        orders=len(scenario.orders),
        skus=len(scenario.inventory),
        facility=scenario.facility.model_dump(mode="json"),
    )


def _experiment_summary(row: ExperimentRow) -> ExperimentSummary:
    arms = 2 if row.candidate_scenario_id else 1
    total = row.replications * arms
    return ExperimentSummary(
        id=row.id,
        state=row.state,
        stage=row.stage,
        replications=row.replications,
        completed_replications=row.completed_replications,
        progress=round(min(1.0, row.completed_replications / total), 4) if total else 0.0,
        baseline_scenario_id=row.baseline_scenario_id,
        candidate_scenario_id=row.candidate_scenario_id,
        engine_version=row.engine_version,
        attempts=row.attempts,
        error=row.error,
        cancel_requested=bool(row.cancel_requested),
        report=row.report,
    )


def _store_scenario(
    session, org: str, scenario: Scenario, parent_id: str | None = None
) -> ScenarioRow:
    row = ScenarioRow(
        id=scenario.scenario_id,
        organization_id=org,
        label=scenario.label,
        parent_id=parent_id,
        input_digest=scenario.input_digest(),
        policy_digest=scenario.policy.digest(),
        payload=scenario.model_dump(mode="json"),
    )
    existing = session.get(ScenarioRow, row.id)
    if existing is not None:
        if existing.organization_id == org and existing.payload == row.payload:
            return existing
        raise HTTPException(
            status_code=409, detail="scenario snapshots are immutable; create a fork"
        )
    session.add(row)
    return row


def _owned_scenario(session, org: str, scenario_id: str) -> ScenarioRow:
    row = session.get(ScenarioRow, scenario_id)
    if row is None or row.organization_id != org:
        raise HTTPException(status_code=404, detail="scenario not found")
    return row


def _owned_experiment(session, org: str, experiment_id: str) -> ExperimentRow:
    row = session.get(ExperimentRow, experiment_id)
    if row is None or row.organization_id != org:
        raise HTTPException(status_code=404, detail="experiment not found")
    return row


async def _read_upload(upload: UploadFile) -> str:
    content = await upload.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Each CSV must be at most 2 MiB")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="Save the CSV as UTF-8 and try again") from exc


# --------------------------------------------------------------- background


class InProcessWorker:
    """Runs jobs inside the API process so the demo needs one command.

    A team deployment runs `forklab-worker` as its own process instead. The
    claim protocol is identical either way, so both can run at once.
    """

    def __init__(self) -> None:
        self.worker_id = new_worker_id()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="forklab-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        from .jobs import claim_next

        while not self._stop.is_set():
            try:
                experiment_id = claim_next(self.worker_id)
                if experiment_id is None:
                    self._stop.wait(0.4)
                    continue
                execute(experiment_id, self.worker_id)
            except Exception:  # keep the loop alive; the job records its own error
                self._stop.wait(1.0)


# ------------------------------------------------------------------- the app


def create_app(start_worker: bool = True) -> FastAPI:
    worker = InProcessWorker()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        init_db()
        with session_scope() as session:
            if session.get(ScenarioRow, "demo-baseline") is None:
                _store_scenario(session, DEMO_ORG, demo_scenario())
        if start_worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(
        title="ForkLab",
        version="0.1.0",
        description="Rehearse a warehouse decision before changing the warehouse.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(f"{API_PREFIX}/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "engine_version": ENGINE_VERSION,
            "worker_id": worker.worker_id if start_worker else None,
        }

    # ----------------------------------------------------------- scenarios

    @app.get(f"{API_PREFIX}/scenarios", response_model=list[ScenarioSummary])
    def list_scenarios(org: OrgDep) -> list[ScenarioSummary]:
        with session_scope() as session:
            rows = session.scalars(
                select(ScenarioRow)
                .where(ScenarioRow.organization_id == org)
                .order_by(ScenarioRow.created_at)
            ).all()
            return [_summary(row) for row in rows]

    @app.get(f"{API_PREFIX}/scenarios/{{scenario_id}}", response_model=ScenarioSummary)
    def get_scenario(scenario_id: str, org: OrgDep) -> ScenarioSummary:
        with session_scope() as session:
            return _summary(_owned_scenario(session, org, scenario_id))

    @app.post(f"{API_PREFIX}/scenarios/demo", response_model=ScenarioSummary)
    def reset_demo(
        org: OrgDep, orders: int = Query(default=650, ge=10, le=5000)
    ) -> ScenarioSummary:
        scenario = demo_scenario(order_count=orders).model_copy(
            update={"scenario_id": f"demo-{uuid.uuid4().hex[:12]}"}
        )
        with session_scope() as session:
            row = _store_scenario(session, org, scenario)
            session.flush()
            return _summary(row)

    @app.post(f"{API_PREFIX}/scenarios/{{scenario_id}}/fork", response_model=ScenarioSummary)
    def fork_scenario(scenario_id: str, body: ForkRequest, org: OrgDep) -> ScenarioSummary:
        with session_scope() as session:
            parent = _owned_scenario(session, org, scenario_id)
            scenario = Scenario.model_validate(parent.payload)
            forked = Scenario(
                scenario_id=f"fork-{uuid.uuid4().hex[:8]}",
                label=body.label,
                orders=scenario.orders,
                inventory=scenario.inventory,
                replenishments=scenario.replenishments,
                facility=scenario.facility,
                policy=body.policy.to_policy(),
            )
            row = _store_scenario(session, org, forked, parent_id=parent.id)
            session.flush()
            return _summary(row)

    # -------------------------------------------------------------- imports

    @app.post(f"{API_PREFIX}/imports")
    async def create_import(
        org: OrgDep,
        label: Annotated[str, Query()] = "Imported warehouse",
        orders_file: UploadFile = File(...),
        inventory_file: UploadFile = File(...),
        replenishments_file: UploadFile | None = File(default=None),
    ) -> dict[str, Any]:
        orders_text = await _read_upload(orders_file)
        inventory_text = await _read_upload(inventory_file)

        orders, order_issues = read_orders(orders_text)
        inventory, inventory_issues = read_inventory(inventory_text)
        replenishments: list = []
        replen_issues: list = []
        if replenishments_file is not None:
            text = await _read_upload(replenishments_file)
            replenishments, replen_issues = read_replenishments(text)

        if len(orders) > 5000:
            raise HTTPException(status_code=413, detail="Limit imports to 5,000 orders per study")
        report = build_report(orders, inventory, order_issues + inventory_issues + replen_issues)
        payload: dict[str, Any] = {
            "accepted_rows": report.accepted_rows,
            "rejected_rows": report.rejected_rows,
            "missing_skus": report.missing_skus,
            "issues": [issue.model_dump(mode="json") for issue in report.issues[:200]],
            "scenario_id": None,
        }
        if not report.is_clean or not orders:
            payload["message"] = (
                "Import blocked. Download the repair file, fix the listed rows, and resubmit."
            )
            return payload

        try:
            from forklab_domain.models import FacilityModel

            scenario = Scenario(
                scenario_id=f"import-{uuid.uuid4().hex[:12]}",
                label=label,
                orders=orders,
                inventory=inventory,
                replenishments=replenishments,
                facility=FacilityModel(
                    provenance={
                        "shift": "estimated",
                        "processing_times": "estimated",
                        "pickers": "estimated",
                        "packing_stations": "estimated",
                    }
                ),
            )
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"]) from exc
        with session_scope() as session:
            _store_scenario(session, org, scenario)
        payload["scenario_id"] = scenario.scenario_id
        payload["message"] = f"Imported {len(orders)} orders across {len(inventory)} SKUs."
        return payload

    @app.post(f"{API_PREFIX}/imports/repair-file", response_class=PlainTextResponse)
    async def repair_file(orders_file: UploadFile = File(...)) -> str:
        """Return only the rejected rows, with the reason appended."""
        from forklab_domain.csv_io import ORDER_COLUMNS

        text = await _read_upload(orders_file)
        orders, issues = read_orders(text)
        report = build_report(orders, [], issues)
        return report.repair_csv(ORDER_COLUMNS)

    # ---------------------------------------------------------- experiments

    @app.post(f"{API_PREFIX}/experiments", response_model=ExperimentSummary)
    def create_experiment(body: CreateExperiment, org: OrgDep) -> ExperimentSummary:
        def existing_request(session):
            existing = (
                session.scalars(
                    select(ExperimentRow).where(
                        ExperimentRow.organization_id == org,
                        ExperimentRow.idempotency_key == body.idempotency_key,
                    )
                ).first()
                if body.idempotency_key
                else None
            )
            if existing is not None:
                if (
                    existing.baseline_scenario_id,
                    existing.candidate_scenario_id,
                    existing.replications,
                ) != (
                    body.baseline_scenario_id,
                    body.candidate_scenario_id,
                    body.replications,
                ):
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency key already used for a different request",
                    )
                return _experiment_summary(existing)
            return None

        try:
            with session_scope() as session:
                baseline = _owned_scenario(session, org, body.baseline_scenario_id)
                if body.candidate_scenario_id:
                    candidate = _owned_scenario(session, org, body.candidate_scenario_id)
                    exclude = {"scenario_id", "label", "policy"}
                    if Scenario.model_validate(baseline.payload).model_dump(
                        exclude=exclude
                    ) != Scenario.model_validate(candidate.payload).model_dump(exclude=exclude):
                        raise HTTPException(
                            status_code=422,
                            detail="Compare policies on the same workload and facility",
                        )
                existing = existing_request(session)
                if existing is not None:
                    return existing
                row = ExperimentRow(
                    id=f"exp-{uuid.uuid4().hex[:12]}",
                    organization_id=org,
                    baseline_scenario_id=body.baseline_scenario_id,
                    candidate_scenario_id=body.candidate_scenario_id,
                    replications=body.replications,
                    idempotency_key=body.idempotency_key,
                    state="queued",
                    stage="queued",
                )
                session.add(row)
                session.flush()
                return _experiment_summary(row)
        except IntegrityError:
            with session_scope() as session:
                existing = existing_request(session)
                if existing is not None:
                    return existing
            raise

    @app.get(f"{API_PREFIX}/experiments", response_model=list[ExperimentSummary])
    def list_experiments(org: OrgDep) -> list[ExperimentSummary]:
        with session_scope() as session:
            rows = session.scalars(
                select(ExperimentRow)
                .where(ExperimentRow.organization_id == org)
                .order_by(ExperimentRow.created_at.desc())
                .limit(50)
            ).all()
            return [_experiment_summary(row) for row in rows]

    @app.get(f"{API_PREFIX}/experiments/{{experiment_id}}", response_model=ExperimentSummary)
    def get_experiment(experiment_id: str, org: OrgDep) -> ExperimentSummary:
        with session_scope() as session:
            return _experiment_summary(_owned_experiment(session, org, experiment_id))

    @app.post(
        f"{API_PREFIX}/experiments/{{experiment_id}}/cancel", response_model=ExperimentSummary
    )
    def cancel_experiment(experiment_id: str, org: OrgDep) -> ExperimentSummary:
        with session_scope() as session:
            row = _owned_experiment(session, org, experiment_id)
            if row.state in ("succeeded", "failed", "cancelled"):
                return _experiment_summary(row)
            session.execute(
                update(ExperimentRow)
                .where(
                    ExperimentRow.id == experiment_id,
                    ExperimentRow.organization_id == org,
                    ExperimentRow.state.in_(("queued", "running")),
                )
                .values(cancel_requested=1)
                .execution_options(synchronize_session=False)
            )
            session.refresh(row)
            return _experiment_summary(row)

    @app.get(f"{API_PREFIX}/experiments/{{experiment_id}}/stream")
    async def stream_experiment(experiment_id: str, org: OrgDep) -> StreamingResponse:
        """Progress snapshots; reconnecting reads the durable record."""
        with session_scope() as session:
            _owned_experiment(session, org, experiment_id)

        async def generator():
            last = None
            for _ in range(3600):
                with session_scope() as session:
                    row = session.get(ExperimentRow, experiment_id)
                    if row is None or row.organization_id != org:
                        yield 'event: error\ndata: {"detail":"not found"}\n\n'
                        return
                    payload = _experiment_summary(row).model_dump(mode="json")
                frame = json.dumps(payload)
                if frame != last:
                    last = frame
                    yield f"data: {frame}\n\n"
                if payload["state"] in ("succeeded", "failed", "cancelled"):
                    return
                await asyncio.sleep(0.4)

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(f"{API_PREFIX}/experiments/{{experiment_id}}/replications")
    def experiment_replications(experiment_id: str, org: OrgDep) -> list[dict[str, Any]]:
        with session_scope() as session:
            _owned_experiment(session, org, experiment_id)
            rows = session.scalars(
                select(ReplicationRow)
                .where(ReplicationRow.experiment_id == experiment_id)
                .order_by(ReplicationRow.arm, ReplicationRow.seed)
            ).all()
            return [
                {
                    "arm": row.arm,
                    "seed": row.seed,
                    "metrics": row.metrics,
                    "invariant_failures": row.invariant_failures,
                    "duration_seconds": row.duration_seconds,
                }
                for row in rows
            ]

    @app.get(f"{API_PREFIX}/experiments/{{experiment_id}}/orders")
    def experiment_orders(
        experiment_id: str,
        org: OrgDep,
        arm: Literal["baseline", "candidate"] = "baseline",
        only_late: bool = False,
        limit: int = Query(default=200, ge=1, le=2000),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict[str, Any]]:
        """Per order outcomes for the stored replication, for the explanation view."""
        with session_scope() as session:
            experiment = _owned_experiment(session, org, experiment_id)
            scenario_id = (
                experiment.baseline_scenario_id
                if arm == "baseline"
                else experiment.candidate_scenario_id
            )
            if scenario_id is None:
                raise HTTPException(status_code=404, detail="arm not present on this experiment")
            scenario = Scenario.model_validate(_owned_scenario(session, org, scenario_id).payload)
            log = session.scalars(
                select(EventLogRow).where(
                    EventLogRow.experiment_id == experiment_id, EventLogRow.arm == arm
                )
            ).first()

        if log is None:
            return []

        from forklab_domain.events import Event, RunResult

        result = RunResult(
            scenario_id=scenario.scenario_id,
            policy_digest=scenario.policy.digest(),
            input_digest=scenario.input_digest(),
            seed=log.seed,
            engine_version=ENGINE_VERSION,
            events=[Event.model_validate(e) for e in log.events],
            overtime_minutes=0.0,
        )
        blocked_times: dict[str, list[float]] = {}
        for event in log.events:
            if event["event_type"] == "blocked_no_stock":
                blocked_times.setdefault(event["order_id"], []).append(event["minute"])
        rows = order_outcomes(scenario, result)
        if only_late:
            rows = [row for row in rows if row.is_late]
        return [
            {
                **row.model_dump(mode="json"),
                "is_late": row.is_late,
                "wait_minutes": row.wait_minutes,
                "cycle_minutes": row.cycle_minutes,
                "blocked_minutes": blocked_times.get(row.order_id, []),
            }
            for row in rows[offset : offset + limit]
        ]

    @app.get(f"{API_PREFIX}/experiments/{{experiment_id}}/orders/{{order_id}}")
    def order_timeline(
        experiment_id: str,
        order_id: str,
        org: OrgDep,
        arm: Literal["baseline", "candidate"] = "baseline",
    ) -> dict[str, Any]:
        """Why was this order late: its own events, in order."""
        with session_scope() as session:
            _owned_experiment(session, org, experiment_id)
            log = session.scalars(
                select(EventLogRow).where(
                    EventLogRow.experiment_id == experiment_id, EventLogRow.arm == arm
                )
            ).first()
        if log is None:
            raise HTTPException(status_code=404, detail="no stored event log for this arm")
        events = [e for e in log.events if e["order_id"] == order_id]
        if not events:
            raise HTTPException(status_code=404, detail="order not found in this event log")
        return {"order_id": order_id, "arm": arm, "seed": log.seed, "events": events}

    @app.get(f"{API_PREFIX}/experiments/{{experiment_id}}/manifest")
    def experiment_manifest(experiment_id: str, org: OrgDep) -> dict[str, Any]:
        """The reproducibility record for a completed comparison."""
        from forklab_cli.manifest import build_manifest

        with session_scope() as session:
            row = _owned_experiment(session, org, experiment_id)
            if row.state != "succeeded" or not row.report or row.candidate_scenario_id is None:
                raise HTTPException(
                    status_code=409,
                    detail="a manifest exists only for a completed two arm comparison",
                )
            baseline = Scenario.model_validate(
                _owned_scenario(session, org, row.baseline_scenario_id).payload
            )
            candidate = Scenario.model_validate(
                _owned_scenario(session, org, row.candidate_scenario_id).payload
            )
            report = ComparisonReport.model_validate(row.report)
        return build_manifest(baseline, candidate, report)

    # -------------------------------------------------------- static bundle

    bundle = Path(__file__).resolve().parents[3] / "apps" / "web" / "dist"
    if bundle.is_dir():
        app.mount("/assets", StaticFiles(directory=bundle / "assets"), name="assets")
        if (bundle / "brand").is_dir():
            app.mount("/brand", StaticFiles(directory=bundle / "brand"), name="brand")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(bundle / "index.html")

    return app


def _worker_enabled() -> bool:
    """The in process worker is on by default so the demo needs one command.

    A Compose deployment sets FORKLAB_START_WORKER=0 and runs the standalone
    worker instead.
    """
    return os.environ.get("FORKLAB_START_WORKER", "1").strip().lower() not in {
        "0",
        "false",
        "no",
    }


app = create_app(start_worker=_worker_enabled())
