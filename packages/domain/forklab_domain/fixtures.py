"""Synthetic fixtures.

Everything here is generated, redistributable, and clearly labelled as
demonstration data. No real facility data is bundled with this repository.
"""

from __future__ import annotations

import random

from .models import (
    FacilityModel,
    InventoryItem,
    OrderLine,
    PolicyVersion,
    ProcessingTimes,
    Replenishment,
    Scenario,
    ServiceClass,
    ShiftCalendar,
)

DAY_START_LABEL = "08:00"


def deterministic_facility(pickers: int = 1, packers: int = 1) -> FacilityModel:
    """A facility with all variability removed, for hand checkable cases.

    pick minutes  = 2.0 + 0.5 * quantity
    pack minutes  = 1.0 + 0.5 * quantity
    """
    return FacilityModel(
        facility_id="hand-check",
        pickers=pickers,
        packing_stations=packers,
        processing_times=ProcessingTimes(
            pick_setup=2.0,
            pick_per_unit=0.5,
            pack_setup=1.0,
            pack_per_unit=0.5,
            variability_low=1.0,
            variability_mode=1.0,
            variability_high=1.0,
        ),
        shift=ShiftCalendar(shift_start_minute=0.0, shift_end_minute=600.0),
    )


def hand_solvable_scenario() -> Scenario:
    """Ten identical orders, one picker, one packer, first in first out.

    With quantity 2 every order takes 3.0 minutes to pick and 2.0 minutes to
    pack. The picker is the bottleneck, so for k = 1..10:

        pick k  runs from 3*(k-1) to 3*k
        pack k  runs from 3*k     to 3*k + 2
        order k dispatches at     3*k + 2

    giving dispatch times 5, 8, 11, 14, 17, 20, 23, 26, 29, 32.
    """
    orders = [
        OrderLine(
            order_id=f"H{index:02d}",
            sku="SKU-A",
            quantity=2,
            arrival_minute=0.0,
            deadline_minute=600.0,
        )
        for index in range(1, 11)
    ]
    return Scenario(
        scenario_id="hand-solvable",
        label="Hand solvable ten order case",
        orders=orders,
        inventory=[InventoryItem(sku="SKU-A", on_hand=100)],
        facility=deterministic_facility(),
        policy=PolicyVersion(name="fifo", priority_strategy="fifo", dispatch_cutoff_minute=600.0),
    )


def demo_scenario(
    seed: int = 20260907,
    order_count: int = 650,
    tight_stock: bool = True,
) -> Scenario:
    """A synthetic single facility day with a deliberate afternoon squeeze.

    The day is built so a dispatch cutoff change has a visible effect: a large
    block of orders arrives late in the shift, and one SKU is deliberately
    short until an inbound delivery lands mid afternoon.
    """
    rng = random.Random(seed)
    skus = ["SKU-A", "SKU-B", "SKU-C", "SKU-D", "SKU-E"]
    orders: list[OrderLine] = []

    for index in range(1, order_count + 1):
        # Two arrival waves: a steady morning and a heavier afternoon block.
        if index <= int(order_count * 0.45):
            arrival = rng.uniform(0.0, 240.0)
        else:
            arrival = rng.uniform(240.0, 450.0)

        sku = rng.choices(skus, weights=[30, 25, 20, 15, 10])[0]
        quantity = rng.choices([1, 2, 3, 5, 8, 12], weights=[30, 25, 18, 14, 8, 5])[0]
        service_class = ServiceClass.express if rng.random() < 0.18 else ServiceClass.standard
        promise = 60.0 if service_class is ServiceClass.express else 120.0

        orders.append(
            OrderLine(
                order_id=f"D{index:04d}",
                sku=sku,
                quantity=quantity,
                arrival_minute=round(arrival, 2),
                deadline_minute=round(min(arrival + promise, 600.0), 2),
                service_class=service_class,
            )
        )

    demand: dict[str, int] = {}
    for order in orders:
        demand[order.sku] = demand.get(order.sku, 0) + order.quantity

    inventory = []
    replenishments = []
    for sku in skus:
        needed = demand.get(sku, 0)
        if tight_stock and sku == "SKU-C":
            # Deliberately short until the inbound delivery lands.
            opening = int(needed * 0.45)
            inventory.append(InventoryItem(sku=sku, on_hand=opening))
            replenishments.append(
                Replenishment(sku=sku, quantity=needed - opening + 20, arrival_minute=300.0)
            )
        else:
            inventory.append(InventoryItem(sku=sku, on_hand=needed + 25))

    facility = FacilityModel(
        facility_id="north-warehouse",
        timezone="Asia/Kolkata",
        pickers=6,
        packing_stations=3,
        processing_times=ProcessingTimes(),
        shift=ShiftCalendar(shift_start_minute=0.0, shift_end_minute=540.0),
    )

    return Scenario(
        scenario_id="demo-baseline",
        label="North warehouse, synthetic day",
        orders=orders,
        inventory=inventory,
        replenishments=replenishments,
        facility=facility,
        policy=PolicyVersion(
            name="baseline fifo",
            priority_strategy="fifo",
            dispatch_cutoff_minute=540.0,
            allowed_overtime_minutes=0.0,
        ),
    )


def fork(scenario: Scenario, scenario_id: str, label: str, policy: PolicyVersion) -> Scenario:
    """Fork a scenario by replacing only the policy.

    The workload, inventory, and process model are carried over unchanged, so a
    paired comparison isolates the decision rather than mixing in a workload
    change.
    """
    return Scenario(
        scenario_id=scenario_id,
        label=label,
        orders=scenario.orders,
        inventory=scenario.inventory,
        replenishments=scenario.replenishments,
        facility=scenario.facility,
        policy=policy,
    )
