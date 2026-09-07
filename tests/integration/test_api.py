"""Integration tests: API, durable jobs, worker, and ownership."""

from __future__ import annotations

import os
import tempfile
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    handle.close()
    os.environ["DATABASE_URL"] = f"sqlite:///{handle.name}"

    import forklab_api.db as db

    db._engine = None
    db._SessionLocal = None
    from forklab_api.app import create_app

    with TestClient(create_app(start_worker=True)) as test_client:
        yield test_client

    os.unlink(handle.name)


def wait_for(client, experiment_id: str, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/experiments/{experiment_id}").json()
        if body["state"] in ("succeeded", "failed", "cancelled"):
            return body
        time.sleep(0.2)
    raise AssertionError(f"experiment {experiment_id} did not finish within {timeout}s")


def test_health_reports_the_engine_version(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["engine_version"].startswith("forklab-sim")


def test_demo_scenario_is_seeded_on_startup(client):
    scenarios = client.get("/api/scenarios").json()
    assert any(s["id"] == "demo-baseline" for s in scenarios)
    demo = next(s for s in scenarios if s["id"] == "demo-baseline")
    assert demo["orders"] > 0
    assert demo["policy"]["priority_strategy"] == "fifo"


def test_fork_preserves_workload_and_changes_the_policy(client):
    parent = client.get("/api/scenarios/demo-baseline").json()
    forked = client.post(
        "/api/scenarios/demo-baseline/fork",
        json={
            "label": "Earliest deadline first",
            "policy": {"name": "edf", "priority_strategy": "edf"},
        },
    ).json()

    assert forked["parent_id"] == "demo-baseline"
    assert forked["input_digest"] != parent["input_digest"]
    assert forked["policy_digest"] != parent["policy_digest"]
    assert forked["orders"] == parent["orders"]


def test_full_comparison_runs_and_reports_intervals(client):
    forked = client.post(
        "/api/scenarios/demo-baseline/fork",
        json={"label": "EDF", "policy": {"name": "edf", "priority_strategy": "edf"}},
    ).json()

    created = client.post(
        "/api/experiments",
        json={
            "baseline_scenario_id": "demo-baseline",
            "candidate_scenario_id": forked["id"],
            "replications": 5,
        },
    ).json()
    assert created["state"] == "queued"

    finished = wait_for(client, created["id"])
    assert finished["state"] == "succeeded", finished["error"]
    assert finished["progress"] == 1.0

    report = finished["report"]
    assert report["replications"] == 5
    late = next(m for m in report["metrics"] if m["metric"] == "late_proportion")
    assert late["ci_low"] <= late["mean_difference"] <= late["ci_high"]
    assert late["verdict"] in ("improvement", "regression", "inconclusive")

    replications = client.get(f"/api/experiments/{created['id']}/replications").json()
    assert len(replications) == 10
    assert {r["arm"] for r in replications} == {"baseline", "candidate"}
    assert all(r["invariant_failures"] == [] for r in replications)


def test_idempotency_key_does_not_create_a_second_experiment(client):
    body = {
        "baseline_scenario_id": "demo-baseline",
        "replications": 2,
        "idempotency_key": "same-key-twice",
    }
    first = client.post("/api/experiments", json=body).json()
    second = client.post("/api/experiments", json=body).json()
    assert first["id"] == second["id"]


def test_order_explanation_returns_the_event_path(client):
    created = client.post(
        "/api/experiments",
        json={"baseline_scenario_id": "demo-baseline", "replications": 1},
    ).json()
    wait_for(client, created["id"])

    orders = client.get(f"/api/experiments/{created['id']}/orders?limit=5").json()
    assert orders
    assert {"order_id", "is_late", "wait_minutes", "terminal"} <= set(orders[0])

    timeline = client.get(f"/api/experiments/{created['id']}/orders/{orders[0]['order_id']}").json()
    kinds = [event["event_type"] for event in timeline["events"]]
    assert kinds[0] == "arrived"
    assert "pick_start" in kinds


def test_manifest_is_refused_until_a_comparison_completes(client):
    single = client.post(
        "/api/experiments",
        json={"baseline_scenario_id": "demo-baseline", "replications": 1},
    ).json()
    wait_for(client, single["id"])
    assert client.get(f"/api/experiments/{single['id']}/manifest").status_code == 409


def test_manifest_records_seeds_digests_and_limitations(client):
    forked = client.post(
        "/api/scenarios/demo-baseline/fork",
        json={"label": "Cutoff 480", "policy": {"name": "c480", "dispatch_cutoff_minute": 480.0}},
    ).json()
    created = client.post(
        "/api/experiments",
        json={
            "baseline_scenario_id": "demo-baseline",
            "candidate_scenario_id": forked["id"],
            "replications": 3,
        },
    ).json()
    wait_for(client, created["id"])

    manifest = client.get(f"/api/experiments/{created['id']}/manifest").json()
    assert manifest["seeds"] == [1, 2, 3]
    assert manifest["baseline"]["policy_digest"] != manifest["candidate"]["policy_digest"]
    assert manifest["limitations"]
    assert manifest["engine_version"].startswith("forklab-sim")


def test_another_organization_cannot_read_the_demo_scenario(client):
    assert client.get("/api/scenarios/demo-baseline").status_code == 200
    other = client.get("/api/scenarios/demo-baseline", headers={"x-forklab-org": "someone-else"})
    assert other.status_code == 404
    assert client.get("/api/scenarios", headers={"x-forklab-org": "someone-else"}).json() == []


def test_import_rejects_bad_rows_and_returns_a_repair_file(client):
    orders = (
        "order_id,sku,quantity,arrival,deadline,service_class\n"
        "A1,SKU-A,2,08:00,10:00,standard\n"
        "A2,SKU-A,0,08:30,09:00,standard\n"
        "A3,SKU-A,1,09:00,08:30,express\n"
    )
    inventory = "sku,on_hand\nSKU-A,50\n"

    response = client.post(
        "/api/imports",
        files={
            "orders_file": ("orders.csv", orders, "text/csv"),
            "inventory_file": ("inventory.csv", inventory, "text/csv"),
        },
    ).json()
    assert response["rejected_rows"] == 2
    assert response["scenario_id"] is None
    assert "repair" in response["message"].lower()

    repair = client.post(
        "/api/imports/repair-file",
        files={"orders_file": ("orders.csv", orders, "text/csv")},
    ).text
    assert "rejection_reason" in repair
    assert "A2" in repair and "A3" in repair
    assert "A1" not in repair


def test_import_accepts_a_clean_file_and_creates_a_runnable_scenario(client):
    orders = "order_id,sku,quantity,arrival,deadline,service_class\n" + "".join(
        f"C{i},SKU-A,2,{i * 2},{i * 2 + 200},standard\n" for i in range(1, 21)
    )
    inventory = "sku,on_hand\nSKU-A,500\n"

    response = client.post(
        "/api/imports?label=Imported%20day",
        files={
            "orders_file": ("orders.csv", orders, "text/csv"),
            "inventory_file": ("inventory.csv", inventory, "text/csv"),
        },
    ).json()
    assert response["rejected_rows"] == 0
    scenario_id = response["scenario_id"]
    assert scenario_id

    created = client.post(
        "/api/experiments",
        json={"baseline_scenario_id": scenario_id, "replications": 2},
    ).json()
    assert wait_for(client, created["id"])["state"] == "succeeded"


def test_missing_skus_block_the_import(client):
    orders = (
        "order_id,sku,quantity,arrival,deadline,service_class\nA1,GHOST,2,08:00,10:00,standard\n"
    )
    response = client.post(
        "/api/imports",
        files={
            "orders_file": ("orders.csv", orders, "text/csv"),
            "inventory_file": ("inventory.csv", "sku,on_hand\nSKU-A,50\n", "text/csv"),
        },
    ).json()
    assert response["missing_skus"] == ["GHOST"]
    assert response["scenario_id"] is None


def test_unknown_scenario_is_rejected_when_creating_an_experiment(client):
    response = client.post(
        "/api/experiments", json={"baseline_scenario_id": "does-not-exist", "replications": 1}
    )
    assert response.status_code == 404


def test_demo_reset_preserves_existing_snapshot(client):
    before = client.get("/api/scenarios/demo-baseline").json()
    fresh = client.post("/api/scenarios/demo?orders=75").json()
    assert fresh["id"] != before["id"]
    assert client.get("/api/scenarios/demo-baseline").json() == before


def test_order_pagination_covers_every_order(client):
    job = client.post(
        "/api/experiments",
        json={
            "baseline_scenario_id": "demo-baseline",
            "replications": 1,
        },
    ).json()
    assert wait_for(client, job["id"])["state"] == "succeeded"
    rows = []
    for offset in range(0, 750, 250):
        rows.extend(
            client.get(f"/api/experiments/{job['id']}/orders?limit=250&offset={offset}").json()
        )
    assert len(rows) == 650
    assert len({r["order_id"] for r in rows}) == 650
    assert all("blocked_minutes" in row for row in rows)


def test_idempotency_key_cannot_refer_to_different_request(client):
    body = {
        "baseline_scenario_id": "demo-baseline",
        "replications": 1,
        "idempotency_key": "same-key-different-input",
    }
    assert client.post("/api/experiments", json=body).status_code == 200
    body["replications"] = 2
    assert client.post("/api/experiments", json=body).status_code == 409


def test_imported_dataset_runs_with_estimated_facility_settings(client):
    response = client.post(
        "/api/imports",
        files={
            "orders_file": ("orders.csv", "order_id,sku,quantity,arrival,deadline\nA,X,1,0,60\n"),
            "inventory_file": ("inventory.csv", "sku,on_hand\nX,10\n"),
        },
    )
    assert response.status_code == 200
    scenario_id = response.json()["scenario_id"]
    assert scenario_id
    scenario = client.get(f"/api/scenarios/{scenario_id}").json()
    assert set(scenario["facility"]["provenance"].values()) == {"estimated"}
    job = client.post(
        "/api/experiments",
        json={
            "baseline_scenario_id": scenario_id,
            "replications": 1,
        },
    ).json()
    assert wait_for(client, job["id"])["state"] == "succeeded"


def test_duplicate_import_returns_validation_error_not_server_error(client):
    response = client.post(
        "/api/imports",
        files={
            "orders_file": (
                "orders.csv",
                "order_id,sku,quantity,arrival,deadline\nA,X,1,0,60\nA,X,1,0,60\n",
            ),
            "inventory_file": ("inventory.csv", "sku,on_hand\nX,10\n"),
        },
    )
    assert response.status_code == 422
    assert "unique" in response.json()["detail"]


def test_oversized_upload_is_rejected(client):
    response = client.post(
        "/api/imports",
        files={
            "orders_file": ("orders.csv", b"x" * (2 * 1024 * 1024 + 1)),
            "inventory_file": ("inventory.csv", "sku,on_hand\nX,10\n"),
        },
    )
    assert response.status_code == 413
