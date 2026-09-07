"""Immutable event log.

The event log is the only output of the simulator. Every metric in the
evaluation package is derived from it, so the simulator cannot report a number
that is not traceable to an event.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class EventType(StrEnum):
    arrived = "arrived"
    blocked_no_stock = "blocked_no_stock"
    stock_reserved = "stock_reserved"
    stock_received = "stock_received"
    pick_start = "pick_start"
    pick_end = "pick_end"
    pack_start = "pack_start"
    pack_end = "pack_end"
    dispatched = "dispatched"
    missed_cutoff = "missed_cutoff"
    unfulfilled = "unfulfilled"


TERMINAL_EVENTS = {EventType.dispatched, EventType.missed_cutoff, EventType.unfulfilled}


class Event(BaseModel):
    """One logged fact.

    `sequence` is the emission index. Events that share a minute must keep
    their causal order, so the log is ordered by (minute, sequence) and never
    re-sorted by a field that could interleave a start before the end that
    released its resource.
    """

    sequence: int
    minute: float
    order_id: str
    event_type: EventType
    resource_id: str | None = None
    detail: dict[str, float | int | str] = Field(default_factory=dict)


class RunResult(BaseModel):
    """One replication. Carries the seed so it can be replayed exactly."""

    scenario_id: str
    policy_digest: str
    input_digest: str
    seed: int
    engine_version: str
    events: list[Event]
    overtime_minutes: float
    invariant_failures: list[str] = Field(default_factory=list)

    def terminal_state(self, order_id: str) -> EventType | None:
        for event in reversed(self.events):
            if event.order_id == order_id and event.event_type in TERMINAL_EVENTS:
                return event.event_type
        return None
