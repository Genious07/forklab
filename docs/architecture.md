# Architecture

## Shape

```
Web workbench  ->  API  ->  validated snapshot + scenario revision
                              |
                              v
                      durable experiment job
                              |
                    simulator and policy runner
                              |
                     event log + metric aggregation
                              |
                     comparison + decision manifest
```

One API and one worker, in a modular monolith. Not microservices. The pieces
are separated by what they are allowed to know, not by deployment boundary.

## Dependency direction

`packages/evaluation` depends on `packages/domain` for types, but never reads
the simulator's internal state. It reconstructs outcomes from the event log the
way an external auditor would. This is the load bearing rule of the codebase:
if a number cannot be derived from logged events, the product cannot report it.

`services/api` depends on both. `packages/domain` depends on nothing in this
repository.

## The simulator

A discrete event model over one facility day, in minutes from day start.
Orders flow receiving to picking to packing to dispatch. Picking consumes
stock; inbound replenishments add it.

Three choices make runs reproducible:

1. **Pre-sampled variability.** A triangular factor is drawn once per order per
   stage from a seeded RNG before the run starts, iterating orders in sorted
   `order_id` order. No RNG call happens inside a running process, so the log
   cannot depend on how SimPy interleaves concurrent work.
2. **Event driven assignment.** There are no polling loops and no persistent
   worker processes competing for the next job. After any state change that
   could free capacity or make an order eligible, an assigner runs to a fixed
   point using the compiled policy ordering.
3. **Total policy ordering.** Every compiled policy key ends in a tie
   component, so no two distinct orders compare equal. A test asserts this for
   all three strategies.

The event log is ordered by minute with a **stable** sort, preserving emission
order within a minute. This is not cosmetic. An earlier version sorted by
`(minute, order_id, event_type)` and broke causality: a `pack_start` could
appear before the `pack_end` that released the station, and order consumption
could sort ahead of the inbound delivery that supplied it. The invariant
checker caught both.

## Policies are data

A `PolicyVersion` is constrained JSON: strategy, batching threshold, dispatch
cutoff, allowed overtime, tie breaking rule. Every field has a range and a
schema version, enforced by Pydantic. `compile_policy` turns it into a sort
key over waiting orders.

A future proposer, model driven or otherwise, can only emit values inside those
ranges. Model generated Python is out of scope for release one. This is a
deliberate boundary, not a missing feature.

## Durable jobs

Job state lives in the database, never in a process.

- A worker **claims** a job with a single conditional update that also writes a
  lease expiry, so two workers cannot hold the same job.
- The worker **heartbeats** to extend the lease and to observe cancellation.
- Each replication is **checkpointed** as it completes, keyed by
  `(experiment, arm, seed)`.
- On resume, finished seeds are skipped rather than redone.
- The final report is assembled **only** when every replication of every arm is
  present. A retry cannot turn a failed experiment into an apparent success.
- After `MAX_ATTEMPTS` the job is marked failed with no report.

`tests/integration/test_job_recovery.py` exercises each of these.

The in process worker exists so the demo is one command. It runs the identical
claim protocol as the standalone `forklab-worker`, so both can run at once.

## Progress and recovery

Progress reaches the browser over server sent events. If the stream drops, the
client falls back to reading the persisted record. The tab stores which
experiment it was watching, so a refresh, a sleep, or a closed laptop reattaches
to the durable job rather than losing the run. A browser tab observes a job; it
never owns one.

## Ownership

Every resource carries an organization id and every query filters on it.
`current_org` is a single FastAPI dependency, which is the seam where a real
identity provider replaces the header stub. There is a test that a second
organization gets 404 on the demo scenario and an empty list from the index.

## Storage

SQLAlchemy over SQLite by default so the demo needs no services. Set
`DATABASE_URL` to a Postgres URL for a team deployment. Scenarios are stored as
validated JSON payloads and re-validated with Pydantic on read, so a schema
change surfaces as a validation error rather than a silent misread.
