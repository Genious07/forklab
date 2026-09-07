"""Metrics derived from an event log.

This package deliberately does not import the simulator's internal state. It
reads the event log the same way an external auditor would, so a metric cannot
be produced by anything the log does not record.
"""

from __future__ import annotations

from forklab_domain.events import EventType, RunResult
from forklab_domain.models import Scenario, ServiceClass
from pydantic import BaseModel


class OrderOutcome(BaseModel):
    order_id: str
    service_class: ServiceClass
    arrival_minute: float
    deadline_minute: float
    pick_start_minute: float | None = None
    pick_end_minute: float | None = None
    pack_start_minute: float | None = None
    pack_end_minute: float | None = None
    dispatched_minute: float | None = None
    terminal: str = "unknown"
    blocked_on_stock: bool = False

    @property
    def is_late(self) -> bool:
        if self.dispatched_minute is None:
            return True
        return self.dispatched_minute > self.deadline_minute

    @property
    def wait_minutes(self) -> float | None:
        """Time between arrival and the start of picking."""
        if self.pick_start_minute is None:
            return None
        return self.pick_start_minute - self.arrival_minute

    @property
    def cycle_minutes(self) -> float | None:
        if self.dispatched_minute is None:
            return None
        return self.dispatched_minute - self.arrival_minute


class RunMetrics(BaseModel):
    """Aggregates for one replication. Every field traces to the event log."""

    seed: int
    order_count: int
    dispatched_count: int
    late_count: int
    unfulfilled_count: int
    missed_cutoff_count: int
    blocked_on_stock_count: int
    late_proportion: float
    mean_wait_minutes: float
    p90_wait_minutes: float
    mean_cycle_minutes: float
    throughput_orders: int
    overtime_minutes: float
    late_proportion_by_class: dict[str, float]


def _percentile(values: list[float], fraction: float) -> float:
    """Nearest rank percentile. Explicit so the number is reproducible."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), int(round(fraction * len(ordered) + 0.5))))
    return ordered[rank - 1]


def outcomes(scenario: Scenario, result: RunResult) -> list[OrderOutcome]:
    """Reconstruct a per order outcome table from the event log."""
    by_id = {
        order.order_id: OrderOutcome(
            order_id=order.order_id,
            service_class=order.service_class,
            arrival_minute=order.arrival_minute,
            deadline_minute=order.deadline_minute,
        )
        for order in scenario.orders
    }

    for event in result.events:
        outcome = by_id.get(event.order_id)
        if outcome is None:
            continue
        match event.event_type:
            case EventType.pick_start:
                outcome.pick_start_minute = event.minute
            case EventType.pick_end:
                outcome.pick_end_minute = event.minute
            case EventType.pack_start:
                outcome.pack_start_minute = event.minute
            case EventType.pack_end:
                outcome.pack_end_minute = event.minute
            case EventType.blocked_no_stock:
                outcome.blocked_on_stock = True
            case EventType.dispatched:
                outcome.dispatched_minute = event.minute
                outcome.terminal = "dispatched"
            case EventType.missed_cutoff:
                outcome.terminal = "missed_cutoff"
            case EventType.unfulfilled:
                outcome.terminal = "unfulfilled"
            case _:
                pass

    return [by_id[order.order_id] for order in scenario.orders]


def run_metrics(scenario: Scenario, result: RunResult) -> RunMetrics:
    rows = outcomes(scenario, result)
    waits = [row.wait_minutes for row in rows if row.wait_minutes is not None]
    cycles = [row.cycle_minutes for row in rows if row.cycle_minutes is not None]
    late = [row for row in rows if row.is_late]

    by_class: dict[str, float] = {}
    for service_class in ServiceClass:
        subset = [row for row in rows if row.service_class is service_class]
        if subset:
            by_class[service_class.value] = round(
                sum(1 for row in subset if row.is_late) / len(subset), 6
            )

    return RunMetrics(
        seed=result.seed,
        order_count=len(rows),
        dispatched_count=sum(1 for row in rows if row.terminal == "dispatched"),
        late_count=len(late),
        unfulfilled_count=sum(1 for row in rows if row.terminal == "unfulfilled"),
        missed_cutoff_count=sum(1 for row in rows if row.terminal == "missed_cutoff"),
        blocked_on_stock_count=sum(1 for row in rows if row.blocked_on_stock),
        late_proportion=round(len(late) / len(rows), 6) if rows else 0.0,
        mean_wait_minutes=round(sum(waits) / len(waits), 4) if waits else 0.0,
        p90_wait_minutes=round(_percentile(waits, 0.90), 4),
        mean_cycle_minutes=round(sum(cycles) / len(cycles), 4) if cycles else 0.0,
        throughput_orders=sum(1 for row in rows if row.terminal == "dispatched"),
        overtime_minutes=result.overtime_minutes,
        late_proportion_by_class=by_class,
    )
