"""Policy compilation.

A PolicyVersion is data. This module turns it into a sort key over waiting
orders. Nothing here executes model authored code; a proposer can only choose
among the strategies and parameter ranges declared in models.PolicyVersion.
"""

from __future__ import annotations

from collections.abc import Callable

from .models import OrderLine, PolicyVersion

SortKey = Callable[[OrderLine], tuple]


def _tie_component(order: OrderLine, rule: str) -> tuple:
    if rule == "smallest_quantity":
        return (order.quantity, order.order_id)
    if rule == "largest_quantity":
        return (-order.quantity, order.order_id)
    return (order.order_id,)


def compile_policy(policy: PolicyVersion) -> SortKey:
    """Return a total ordering over waiting orders.

    The key is always a tuple ending in the tie component, so the ordering is
    total and the simulation stays reproducible. Express orders are never
    promoted implicitly; a strategy has to say so.
    """

    rule = policy.tie_breaking_rule

    if policy.priority_strategy == "fifo":

        def fifo_key(order: OrderLine) -> tuple:
            return (order.arrival_minute, *_tie_component(order, rule))

        return fifo_key

    if policy.priority_strategy == "edf":

        def edf_key(order: OrderLine) -> tuple:
            return (order.deadline_minute, *_tie_component(order, rule))

        return edf_key

    if policy.priority_strategy == "batch_by_sku":
        # Group same SKU work together to amortise setup, but keep the earliest
        # deadline inside a SKU group leading so batching cannot starve a group
        # indefinitely.
        def batch_key(order: OrderLine) -> tuple:
            return (order.sku, order.deadline_minute, *_tie_component(order, rule))

        return batch_key

    raise ValueError(f"unsupported priority_strategy: {policy.priority_strategy}")


def describe_policy(policy: PolicyVersion) -> str:
    parts = [f"strategy={policy.priority_strategy}"]
    if policy.priority_strategy == "batch_by_sku":
        parts.append(f"batch_threshold={policy.batching_threshold}")
    parts.append(f"cutoff={policy.dispatch_cutoff_minute:.0f}min")
    parts.append(f"overtime_allowed={policy.allowed_overtime_minutes:.0f}min")
    parts.append(f"ties={policy.tie_breaking_rule}")
    return ", ".join(parts)
