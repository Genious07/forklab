# Limitations

Read this before showing a ForkLab result to anyone who might act on it.

## The model is not calibrated

Processing times are **estimated**, not measured against facility history. The
interface labels them as estimated everywhere they appear, and the exported
manifest repeats it.

A simulated difference is evidence about the model, not a prediction about a
real site. Calibration against observed queue and service distributions is
milestone C work and has not been done. Until it has, treat every result as
exploratory.

The blueprint proposes a pilot gate of no more than 15 percent error on
selected aggregate operational metrics. **That is a design target, not an
achieved accuracy claim.** Nothing in this repository has been validated
against a real warehouse.

## What the statistics do and do not say

Bootstrap intervals here describe **run to run variability under a fixed
workload**. They do not describe uncertainty about a different day, a different
order mix, or a seasonal peak. Running 30 replications of one synthetic
Tuesday tells you nothing about next Tuesday.

Paired differences assume the replications are exchangeable. They are, because
the workload is held fixed and only the seed changes. Change the workload and
the pairing argument no longer holds.

## Survivorship in the metrics

Timing metrics are computed over orders that reached a terminal state with a
dispatch time. A policy that dispatches fewer orders can therefore show
"better" mean cycle time purely because slow orders left the average.

The earlier cutoff scenario in the README demonstrates this: cycle time appears
to improve by 5.18 minutes while 71 fewer orders ship. ForkLab shows both rows
side by side rather than selecting the flattering one, but **it does not detect
the trap for you**. Always read throughput alongside timing.

## Scope

Not modelled in release one:

- More than one facility, or flow between facilities
- Carry over between days; each run is a single day from an empty start
- Zone travel and pick path routing; picking is setup plus per unit
- Named individuals, shift bidding, or any staffing decision about a person
- Robotics, conveyor mechanics, three dimensional physics
- Partial fulfillment and order splitting; an order is picked whole or waits
- Live writes to a warehouse management system

## Security posture

**Authentication is a stub.** `current_org` reads a header. Ownership is
enforced on every query and there is a test proving cross-organization reads
return 404, but anyone who can reach the API can choose their own organization
id.

Do not expose this to the internet without replacing `current_org` with a
maintained identity provider. The dependency is a single function specifically
so this replacement is small.

Uploaded CSV files are parsed with the standard library reader into validated
Pydantic models. There is no archive extraction and no code execution path from
uploaded data. The API limits each file to 2 MiB and each import to 5,000 orders.
Set total request limits, rate limits and concurrency quotas at the reverse proxy.
Multipart parsing occurs before the per-file application check.

## Determinism boundaries

Identical seeds reproduce identical event logs **on the same engine version**.
`ENGINE_VERSION` is recorded in every run result and manifest, and replay warns
when it differs. A change to the simulator is expected to change results; that
is why the version is pinned into the record rather than assumed stable.

Floating point summation order is fixed by the stable event ordering, so metric
values reproduce exactly rather than approximately. The replay check uses a
`1e-9` tolerance regardless.

## What would change these conclusions

- Calibration against real facility event history
- A workload model that samples days rather than replaying one fixed day
- Modelling partial fulfillment, which is common and currently absent
- A travel model, which would change the relative cost of batching strategies


## Audit follow-ups (September 2026)

This is a deployable internal prototype, not a complete public multi-tenant service.
The blueprint's identity/roles, calibration workflow, policy proposer, workload-day
uncertainty and migration framework still need implementation.

CSV timestamps use a fixed 08:00 display origin. The legacy ISO parser discards the
date and offset, so use numeric minute offsets for imports until a date-aware
facility import contract is implemented. Mixed dates and time zones are not supported.

Wait statistics include orders that started picking; cycle statistics include only
orders that dispatched. Empty samples currently produce zero values. Interpret
these with throughput and unfulfilled counts, never as standalone efficiency wins.

A recorded stock shortage in the process view is historical evidence before picking,
not proof of the exact stock balance at the current scrub time. The map is schematic.
