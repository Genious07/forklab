"""Validation and policy ordering tests."""

from __future__ import annotations

import pytest
from forklab_domain import (
    InventoryItem,
    OrderLine,
    PolicyVersion,
    Scenario,
    compile_policy,
    demo_scenario,
)
from pydantic import ValidationError


def test_deadline_before_arrival_is_rejected():
    with pytest.raises(ValidationError, match="precedes arrival"):
        OrderLine(order_id="X", sku="A", quantity=1, arrival_minute=100.0, deadline_minute=50.0)


def test_zero_quantity_is_rejected():
    with pytest.raises(ValidationError):
        OrderLine(order_id="X", sku="A", quantity=0, arrival_minute=0.0, deadline_minute=10.0)


def test_duplicate_order_ids_are_rejected():
    order = OrderLine(order_id="X", sku="A", quantity=1, arrival_minute=0.0, deadline_minute=10.0)
    with pytest.raises(ValidationError, match="unique"):
        Scenario(
            scenario_id="s",
            label="dup",
            orders=[order, order.model_copy()],
            inventory=[InventoryItem(sku="A", on_hand=5)],
        )


def test_missing_skus_are_reported_not_silently_dropped():
    scenario = Scenario(
        scenario_id="s",
        label="missing sku",
        orders=[
            OrderLine(order_id="X", sku="GHOST", quantity=1, arrival_minute=0, deadline_minute=10)
        ],
        inventory=[InventoryItem(sku="A", on_hand=5)],
    )
    assert scenario.missing_skus() == ["GHOST"]


def test_out_of_range_policy_values_are_rejected():
    with pytest.raises(ValidationError):
        PolicyVersion(dispatch_cutoff_minute=5000.0)
    with pytest.raises(ValidationError):
        PolicyVersion(allowed_overtime_minutes=-1.0)
    with pytest.raises(ValidationError):
        PolicyVersion(priority_strategy="write_arbitrary_python")


def test_policy_digest_changes_with_parameters():
    a = PolicyVersion(priority_strategy="fifo")
    b = PolicyVersion(priority_strategy="edf")
    assert a.digest() != b.digest()
    assert a.digest() == PolicyVersion(priority_strategy="fifo").digest()


@pytest.mark.parametrize("strategy", ["fifo", "edf", "batch_by_sku"])
def test_policy_ordering_is_total(strategy):
    """No two distinct orders may compare equal, or replays could diverge."""
    scenario = demo_scenario(order_count=150)
    key = compile_policy(PolicyVersion(priority_strategy=strategy))
    keys = [key(order) for order in scenario.orders]
    assert len(set(keys)) == len(keys)


def test_edf_orders_by_deadline():
    key = compile_policy(PolicyVersion(priority_strategy="edf"))
    early = OrderLine(order_id="Z", sku="A", quantity=1, arrival_minute=100, deadline_minute=110)
    late = OrderLine(order_id="A", sku="A", quantity=1, arrival_minute=0, deadline_minute=500)
    assert key(early) < key(late)


def test_fifo_orders_by_arrival():
    key = compile_policy(PolicyVersion(priority_strategy="fifo"))
    first = OrderLine(order_id="Z", sku="A", quantity=1, arrival_minute=0, deadline_minute=500)
    second = OrderLine(order_id="A", sku="A", quantity=1, arrival_minute=100, deadline_minute=110)
    assert key(first) < key(second)


def test_input_digest_is_stable_and_sensitive():
    a = demo_scenario(order_count=50)
    b = demo_scenario(order_count=50)
    assert a.input_digest() == b.input_digest()
    c = demo_scenario(order_count=51)
    assert a.input_digest() != c.input_digest()
