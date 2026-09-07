"""Discrete event simulator for a single facility day.

Design notes that matter for reproducibility:

1. Service time variability is drawn once per order per stage from a seeded
   RNG, before the simulation starts, in sorted order_id order. Nothing is
   drawn inside a running process, so the event log does not depend on the
   order SimPy happens to schedule concurrent processes.
2. Work assignment is event driven. There are no polling loops and no
   persistent worker processes racing each other. After any state change that
   could free capacity or make an order eligible, the assigner runs to a fixed
   point using the compiled policy ordering.
3. The policy ordering is a total order, so ties never resolve by scheduling
   accident.
"""

from __future__ import annotations

import random

import simpy

from .events import Event, EventType, RunResult
from .models import OrderLine, PolicyVersion, Scenario
from .policies import compile_policy

ENGINE_VERSION = "forklab-sim-0.1.0"


def _triangular_factors(scenario: Scenario, seed: int) -> dict[tuple[str, str], float]:
    """Pre-sample one variability factor per order per stage."""
    rng = random.Random(seed)
    times = scenario.facility.processing_times
    factors: dict[tuple[str, str], float] = {}
    for order in sorted(scenario.orders, key=lambda o: o.order_id):
        for stage in ("pick", "pack"):
            factors[(order.order_id, stage)] = rng.triangular(
                times.variability_low, times.variability_high, times.variability_mode
            )
    return factors


def _select_next(
    waiting: list[OrderLine],
    policy: PolicyVersion,
    key,
    stock: dict[str, int],
    now: float,
    require_stock: bool,
) -> OrderLine | None:
    """Pick the next order to start, or None when nothing is eligible."""
    eligible = [
        order
        for order in waiting
        if order.arrival_minute <= now
        and (not require_stock or stock.get(order.sku, 0) >= order.quantity)
    ]
    if not eligible:
        return None

    if policy.priority_strategy == "batch_by_sku" and policy.batching_threshold > 1:
        counts: dict[str, int] = {}
        for order in eligible:
            counts[order.sku] = counts.get(order.sku, 0) + 1
        qualifying = {sku for sku, n in counts.items() if n >= policy.batching_threshold}
        if qualifying:
            eligible = [order for order in eligible if order.sku in qualifying]

    return min(eligible, key=key)


class _Simulation:
    def __init__(self, scenario: Scenario, seed: int) -> None:
        self.scenario = scenario
        self.seed = seed
        self.env = simpy.Environment()
        self.policy = scenario.policy
        self.key = compile_policy(self.policy)
        self.factors = _triangular_factors(scenario, seed)

        self.stock: dict[str, int] = {item.sku: item.on_hand for item in scenario.inventory}
        self.events: list[Event] = []

        self.pick_queue: list[OrderLine] = []
        self.pack_queue: list[OrderLine] = []
        self.idle_pickers = scenario.facility.pickers
        self.idle_packers = scenario.facility.packing_stations

        self.blocked_reported: set[str] = set()
        self.sequence = 0
        self.overtime_minutes = 0.0

        shift = scenario.facility.shift
        self.shift_end = shift.shift_end_minute
        self.work_cutoff = shift.shift_end_minute + self.policy.allowed_overtime_minutes
        self.dispatch_cutoff = self.policy.dispatch_cutoff_minute

    # ------------------------------------------------------------------ log

    def log(
        self,
        order_id: str,
        event_type: EventType,
        resource_id: str | None = None,
        **detail: float | int | str,
    ) -> None:
        self.sequence += 1
        self.events.append(
            Event(
                sequence=self.sequence,
                minute=round(self.env.now, 6),
                order_id=order_id,
                event_type=event_type,
                resource_id=resource_id,
                detail=detail,
            )
        )

    # ------------------------------------------------------------- service

    def pick_minutes(self, order: OrderLine) -> float:
        t = self.scenario.facility.processing_times
        base = t.pick_setup + t.pick_per_unit * order.quantity
        return base * self.factors[(order.order_id, "pick")]

    def pack_minutes(self, order: OrderLine) -> float:
        t = self.scenario.facility.processing_times
        base = t.pack_setup + t.pack_per_unit * order.quantity
        return base * self.factors[(order.order_id, "pack")]

    def accrue_overtime(self, start: float, end: float) -> None:
        self.overtime_minutes += max(0.0, end - max(start, self.shift_end))

    # ----------------------------------------------------------- assignment

    def assign(self) -> None:
        """Run assignment to a fixed point. Safe to call after any change."""
        progressed = True
        while progressed:
            progressed = False
            if self.env.now < self.work_cutoff and self.idle_packers > 0:
                order = _select_next(
                    self.pack_queue, self.policy, self.key, self.stock, self.env.now, False
                )
                if order is not None:
                    self.pack_queue.remove(order)
                    self.idle_packers -= 1
                    self.env.process(self.run_pack(order))
                    progressed = True
                    continue
            if self.env.now < self.work_cutoff and self.idle_pickers > 0:
                order = _select_next(
                    self.pick_queue, self.policy, self.key, self.stock, self.env.now, True
                )
                if order is not None:
                    self.pick_queue.remove(order)
                    self.idle_pickers -= 1
                    self.env.process(self.run_pick(order))
                    progressed = True
                    continue
            self.report_blocked()

    def report_blocked(self) -> None:
        for order in self.pick_queue:
            if order.order_id in self.blocked_reported:
                continue
            if order.arrival_minute > self.env.now:
                continue
            if self.stock.get(order.sku, 0) < order.quantity:
                self.blocked_reported.add(order.order_id)
                self.log(
                    order.order_id,
                    EventType.blocked_no_stock,
                    sku=order.sku,
                    required=order.quantity,
                    on_hand=self.stock.get(order.sku, 0),
                )

    # ------------------------------------------------------------ processes

    def run_arrivals(self):
        for order in sorted(self.scenario.orders, key=lambda o: (o.arrival_minute, o.order_id)):
            if order.arrival_minute > self.env.now:
                yield self.env.timeout(order.arrival_minute - self.env.now)
            self.pick_queue.append(order)
            self.log(order.order_id, EventType.arrived, sku=order.sku, quantity=order.quantity)
            self.assign()

    def run_replenishments(self):
        items = sorted(
            self.scenario.replenishments, key=lambda r: (r.arrival_minute, r.sku, r.quantity)
        )
        for item in items:
            if item.arrival_minute > self.env.now:
                yield self.env.timeout(item.arrival_minute - self.env.now)
            self.stock[item.sku] = self.stock.get(item.sku, 0) + item.quantity
            still_blocked = {order.order_id for order in self.pick_queue if order.sku == item.sku}
            self.blocked_reported -= still_blocked
            self.log(
                f"inbound:{item.sku}",
                EventType.stock_received,
                sku=item.sku,
                quantity=item.quantity,
                on_hand=self.stock[item.sku],
            )
            self.assign()

    def run_pick(self, order: OrderLine):
        start = self.env.now
        self.stock[order.sku] -= order.quantity
        self.log(order.order_id, EventType.stock_reserved, sku=order.sku, quantity=order.quantity)
        self.log(order.order_id, EventType.pick_start, resource_id="picker")
        yield self.env.timeout(self.pick_minutes(order))
        self.log(order.order_id, EventType.pick_end, resource_id="picker")
        self.accrue_overtime(start, self.env.now)
        self.idle_pickers += 1
        self.pack_queue.append(order)
        self.assign()

    def run_pack(self, order: OrderLine):
        start = self.env.now
        self.log(order.order_id, EventType.pack_start, resource_id="packer")
        yield self.env.timeout(self.pack_minutes(order))
        self.log(order.order_id, EventType.pack_end, resource_id="packer")
        self.accrue_overtime(start, self.env.now)
        self.idle_packers += 1
        if self.env.now <= self.dispatch_cutoff:
            self.log(
                order.order_id,
                EventType.dispatched,
                deadline=order.deadline_minute,
                late=int(self.env.now > order.deadline_minute),
            )
        else:
            self.log(
                order.order_id,
                EventType.missed_cutoff,
                cutoff=self.dispatch_cutoff,
                deadline=order.deadline_minute,
            )
        self.assign()

    # ---------------------------------------------------------------- drive

    def run(self) -> RunResult:
        self.env.process(self.run_arrivals())
        if self.scenario.replenishments:
            self.env.process(self.run_replenishments())
        self.env.run()

        # Anything still queued never reached a terminal state during the day.
        for order in self.pick_queue + self.pack_queue:
            self.log(
                order.order_id,
                EventType.unfulfilled,
                reason="work_cutoff" if self.env.now >= self.work_cutoff else "no_stock",
            )

        # Stable sort on minute alone. Python's sort is stable, so events that
        # share a minute keep their emission order and causality is preserved.
        self.events.sort(key=lambda e: e.minute)
        return RunResult(
            scenario_id=self.scenario.scenario_id,
            policy_digest=self.policy.digest(),
            input_digest=self.scenario.input_digest(),
            seed=self.seed,
            engine_version=ENGINE_VERSION,
            events=self.events,
            overtime_minutes=round(self.overtime_minutes, 6),
        )


def simulate(scenario: Scenario, seed: int) -> RunResult:
    """Run one replication of a scenario under a seed."""
    return _Simulation(scenario, seed).run()


def simulate_replications(scenario: Scenario, seeds: list[int]) -> list[RunResult]:
    """Run one replication per seed. Seeds are the pairing key across scenarios."""
    return [simulate(scenario, seed) for seed in seeds]
