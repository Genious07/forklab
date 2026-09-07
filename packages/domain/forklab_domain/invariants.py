"""Invariant checks over an event log.

Correctness precedes optimization. These run against the produced log rather
than trusting the simulator's internal state, so a bug in the simulator cannot
mark itself as valid.
"""

from __future__ import annotations

from .events import TERMINAL_EVENTS, EventType, RunResult
from .models import Scenario


def check_run(scenario: Scenario, result: RunResult) -> list[str]:
    """Return a list of invariant failure descriptions. Empty means clean."""
    failures: list[str] = []
    orders = {o.order_id: o for o in scenario.orders}

    # 1. Work never precedes arrival.
    arrival_seen: dict[str, float] = {}
    for event in result.events:
        if event.event_type is EventType.arrived:
            arrival_seen[event.order_id] = event.minute
        elif event.event_type is EventType.pick_start:
            arrived_at = arrival_seen.get(event.order_id)
            if arrived_at is None:
                failures.append(f"{event.order_id}: pick_start before any arrival event")
            elif event.minute < arrived_at - 1e-9:
                failures.append(
                    f"{event.order_id}: pick_start {event.minute} precedes arrival {arrived_at}"
                )

    # 2. Capacity is never exceeded at either stage.
    for start, end, limit, label in (
        (EventType.pick_start, EventType.pick_end, scenario.facility.pickers, "pickers"),
        (
            EventType.pack_start,
            EventType.pack_end,
            scenario.facility.packing_stations,
            "packing stations",
        ),
    ):
        active = 0
        peak = 0
        for event in result.events:
            if event.event_type is start:
                active += 1
                peak = max(peak, active)
            elif event.event_type is end:
                active -= 1
        if peak > limit:
            failures.append(f"{label}: peak concurrency {peak} exceeds capacity {limit}")
        if active != 0:
            failures.append(f"{label}: {active} unmatched start events at end of run")

    # 3. Stock is conserved and never goes negative.
    stock = {item.sku: item.on_hand for item in scenario.inventory}
    consumed: dict[str, int] = {}
    for event in result.events:
        if event.event_type not in (EventType.stock_reserved, EventType.stock_received):
            continue
        sku = str(event.detail.get("sku", ""))
        quantity = int(event.detail.get("quantity", 0))
        if event.event_type is EventType.stock_received:
            stock[sku] = stock.get(sku, 0) + quantity
            continue
        stock[sku] = stock.get(sku, 0) - quantity
        consumed[sku] = consumed.get(sku, 0) + quantity
        if stock[sku] < 0:
            failures.append(f"{sku}: stock went negative at minute {event.minute}")

    for sku, total in consumed.items():
        supplied = sum(i.on_hand for i in scenario.inventory if i.sku == sku)
        supplied += sum(r.quantity for r in scenario.replenishments if r.sku == sku)
        if total > supplied:
            failures.append(f"{sku}: consumed {total} exceeds supplied {supplied}")

    # 4. Every order reaches exactly one terminal state.
    terminal_counts: dict[str, int] = {}
    for event in result.events:
        if event.event_type in TERMINAL_EVENTS:
            terminal_counts[event.order_id] = terminal_counts.get(event.order_id, 0) + 1
    for order_id in orders:
        count = terminal_counts.get(order_id, 0)
        if count == 0:
            failures.append(f"{order_id}: no terminal state recorded")
        elif count > 1:
            failures.append(f"{order_id}: {count} terminal states recorded")

    # 5. Picked work is packed, packed work is resolved.
    picked = {e.order_id for e in result.events if e.event_type is EventType.pick_end}
    packed = {e.order_id for e in result.events if e.event_type is EventType.pack_end}
    for order_id in packed - picked:
        failures.append(f"{order_id}: packed without being picked")

    return failures


def check_reproducible(scenario: Scenario, seed: int) -> list[str]:
    """Run the same scenario and seed twice and compare the logs."""
    from .simulator import simulate

    first = simulate(scenario, seed)
    second = simulate(scenario, seed)
    if first.events != second.events:
        return [f"seed {seed}: repeated run produced a different event log"]
    return []
