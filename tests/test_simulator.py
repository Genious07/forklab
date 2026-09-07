"""Correctness tests for the simulator. These run before any optimization claim."""

from __future__ import annotations

import pytest
from forklab_domain import (
    EventType,
    InventoryItem,
    OrderLine,
    PolicyVersion,
    Scenario,
    check_reproducible,
    check_run,
    demo_scenario,
    fork,
    hand_solvable_scenario,
    simulate,
    simulate_replications,
)
from forklab_domain.fixtures import deterministic_facility


def dispatch_times(result) -> dict[str, float]:
    return {
        event.order_id: event.minute
        for event in result.events
        if event.event_type is EventType.dispatched
    }


def test_hand_solvable_case_matches_arithmetic():
    """One picker, one packer, ten identical orders.

    Pick takes 3.0 minutes and pack takes 2.0 minutes, so the picker is the
    bottleneck and order k dispatches at 3k + 2.
    """
    scenario = hand_solvable_scenario()
    result = simulate(scenario, seed=1)
    times = dispatch_times(result)

    assert len(times) == 10
    for k in range(1, 11):
        assert times[f"H{k:02d}"] == pytest.approx(3 * k + 2)


def test_hand_solvable_case_is_seed_independent():
    """Variability bounds are all 1.0, so the seed cannot change the result."""
    scenario = hand_solvable_scenario()
    first = dispatch_times(simulate(scenario, seed=1))
    second = dispatch_times(simulate(scenario, seed=999_999))
    assert first == second


def test_invariants_hold_on_hand_case():
    scenario = hand_solvable_scenario()
    assert check_run(scenario, simulate(scenario, seed=1)) == []


def test_invariants_hold_on_demo_scenario():
    scenario = demo_scenario()
    assert check_run(scenario, simulate(scenario, seed=1)) == []


@pytest.mark.parametrize("seed", [1, 2, 17, 4242])
def test_identical_seed_reproduces_event_log(seed):
    assert check_reproducible(demo_scenario(order_count=120), seed) == []


def test_capacity_is_never_exceeded():
    scenario = demo_scenario(order_count=200)
    result = simulate(scenario, seed=3)
    for start, end, limit in (
        (EventType.pick_start, EventType.pick_end, scenario.facility.pickers),
        (EventType.pack_start, EventType.pack_end, scenario.facility.packing_stations),
    ):
        active = 0
        for event in result.events:
            if event.event_type is start:
                active += 1
            elif event.event_type is end:
                active -= 1
            assert 0 <= active <= limit


def test_work_never_starts_before_arrival():
    scenario = demo_scenario(order_count=200)
    result = simulate(scenario, seed=5)
    arrivals = {o.order_id: o.arrival_minute for o in scenario.orders}
    for event in result.events:
        if event.event_type is EventType.pick_start:
            assert event.minute >= arrivals[event.order_id]


def test_every_order_reaches_exactly_one_terminal_state():
    scenario = demo_scenario(order_count=200)
    result = simulate(scenario, seed=7)
    counts: dict[str, int] = {}
    for event in result.events:
        if event.event_type in {
            EventType.dispatched,
            EventType.missed_cutoff,
            EventType.unfulfilled,
        }:
            counts[event.order_id] = counts.get(event.order_id, 0) + 1
    assert set(counts) == {o.order_id for o in scenario.orders}
    assert set(counts.values()) == {1}


def test_stock_shortage_blocks_until_replenishment_arrives():
    """An order that cannot be served waits for the inbound delivery, not forever."""
    from forklab_domain import Replenishment

    scenario = Scenario(
        scenario_id="shortage",
        label="Single short order",
        orders=[
            OrderLine(order_id="S1", sku="X", quantity=5, arrival_minute=0.0, deadline_minute=600.0)
        ],
        inventory=[InventoryItem(sku="X", on_hand=0)],
        replenishments=[Replenishment(sku="X", quantity=5, arrival_minute=100.0)],
        facility=deterministic_facility(),
        policy=PolicyVersion(dispatch_cutoff_minute=600.0),
    )
    result = simulate(scenario, seed=1)
    kinds = [e.event_type for e in result.events]
    assert EventType.blocked_no_stock in kinds
    assert EventType.stock_received in kinds

    pick_start = next(e for e in result.events if e.event_type is EventType.pick_start)
    assert pick_start.minute == pytest.approx(100.0)
    assert check_run(scenario, result) == []


def test_stock_is_never_oversold():
    """Two orders, only enough stock for one, and no replenishment."""
    scenario = Scenario(
        scenario_id="oversell",
        label="Contention for one unit of stock",
        orders=[
            OrderLine(order_id="A", sku="X", quantity=3, arrival_minute=0.0, deadline_minute=600.0),
            OrderLine(order_id="B", sku="X", quantity=3, arrival_minute=0.0, deadline_minute=600.0),
        ],
        inventory=[InventoryItem(sku="X", on_hand=3)],
        facility=deterministic_facility(),
        policy=PolicyVersion(dispatch_cutoff_minute=600.0),
    )
    result = simulate(scenario, seed=1)
    assert check_run(scenario, result) == []
    dispatched = [e.order_id for e in result.events if e.event_type is EventType.dispatched]
    unfulfilled = [e.order_id for e in result.events if e.event_type is EventType.unfulfilled]
    assert dispatched == ["A"]
    assert unfulfilled == ["B"]


def test_earlier_dispatch_cutoff_can_only_reduce_dispatches():
    scenario = demo_scenario(order_count=300)
    seeds = [1, 2, 3]
    baseline = simulate_replications(scenario, seeds)
    early = fork(
        scenario,
        "early-cutoff",
        "Earlier cutoff",
        PolicyVersion(priority_strategy="fifo", dispatch_cutoff_minute=420.0),
    )
    candidate = simulate_replications(early, seeds)
    for base_run, cand_run in zip(baseline, candidate, strict=True):
        base_count = sum(1 for e in base_run.events if e.event_type is EventType.dispatched)
        cand_count = sum(1 for e in cand_run.events if e.event_type is EventType.dispatched)
        assert cand_count <= base_count


def test_forking_preserves_workload():
    scenario = demo_scenario(order_count=100)
    candidate = fork(scenario, "c", "Candidate", PolicyVersion(priority_strategy="edf"))
    assert [o.model_dump() for o in candidate.orders] == [o.model_dump() for o in scenario.orders]
    assert candidate.policy.digest() != scenario.policy.digest()
