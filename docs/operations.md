# Operations

## Deployment paths

**1. Local demo.** Everything on one machine, SQLite, seeded synthetic data, no
API key. `make dev` then <http://localhost:8000>. No new hosting bill.

**2. Self hosted team release.** Docker Compose with Postgres, a persistent
volume, a reverse proxy terminating TLS, and at least one worker. See
[deploy/](../deploy).

**3. Public demonstration.** Read only, synthetic data, tight limits. Not
covered here and not configured in this repository.

## Running with Compose

```bash
cd deploy
cp .env.example .env        # set POSTGRES_PASSWORD
docker compose up --build
```

This starts Postgres, the API on port 8000, and one worker. The API runs with
`start_worker=false` under Compose so the standalone worker owns execution.

## Scaling workers

Workers claim jobs with a leased conditional update, so more than one is safe.
Add replicas:

```bash
docker compose up --scale worker=3
```

Each experiment is still executed by one worker at a time. Scaling adds
throughput across concurrent experiments, not within one.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./forklab.db` | SQLAlchemy URL |
| `FORKLAB_START_WORKER` | `1` | Run the in process worker inside the API |

Lease duration and retry limit are `LEASE_SECONDS` and `MAX_ATTEMPTS` in
`services/api/forklab_api/jobs.py`.

## Backup and restore

State is entirely in Postgres. Scenarios, experiments, replications, and event
logs are all rows.

```bash
docker compose exec db pg_dump -U forklab forklab > backup.sql
docker compose exec -T db psql -U forklab forklab < backup.sql
```

Rehearse the restore before relying on it. An untested backup is not a backup.

## Interruption behaviour

A worker killed mid experiment leaves the job `running` with a lease that
expires after `LEASE_SECONDS`. Another worker then claims it and resumes from
the last checkpointed replication. Finished seeds are skipped.

To verify this on a running system:

```bash
docker compose kill worker      # mid experiment
docker compose up -d worker     # wait out the lease, then watch it resume
```

The experiment's `attempts` counter increments and the UI shows
"attempt 2, resumed".

After `MAX_ATTEMPTS` the job is marked `failed` with no report. A failed
experiment never produces a partial comparison.

## Cancellation

`POST /api/experiments/{id}/cancel` sets a flag. The worker observes it at the
next heartbeat, stops, and marks the run `cancelled` with no report. Partial
replications remain in the database and are visible, clearly attached to a
cancelled experiment.

## Logs

The API and worker log to stdout. The worker prints each claim and terminal
state with the experiment id. There are no credentials in scenario payloads,
event logs, or manifests. Uploaded order data does live in the database, so
treat a database dump as customer data.

## Health

`GET /api/health` returns status and engine version. It does not check the
database, so use it for liveness rather than readiness.

## Upgrades

Tables are created with `create_all` at startup. The audit update also adds unique
indexes for `(experiment_id, arm, seed)` on replications and event logs, and
`(organization_id, idempotency_key)` on experiments. Back up before upgrading.
Check for duplicate non-null keys with GROUP BY/HAVING COUNT(*) > 1 first. Existing
duplicates cause startup to fail; startup never deletes or silently merges records.
Resolve duplicates from a verified backup under operator review, then restart. There is no migration tool
wired yet, which is fine while the schema is additive and unreleased, and is a
gap before any deployment holding data worth keeping. Alembic is the intended
answer and is listed in the blueprint.

Changing the simulator changes `ENGINE_VERSION`, which changes results. Replay
warns rather than failing silently. Keep the manifest alongside any decision
you acted on.


Worker writes and heartbeats are conditional on a live lease, running state and
worker identity. A background renewal runs during long seeds; cancellation wins
before a terminal success is recorded. Reports assemble from metric checkpoints.
A retry under a different engine version fails rather than mixing versions.
The web client polls durable experiment state every 1.5 seconds while active and
reattaches after refresh. No browser connection owns job execution.
