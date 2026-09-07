"""Typed domain models for ForkLab.

All times are minutes measured from the start of the facility day. The facility
timezone is carried on the model so display can convert, while every stored
value stays in a single numeric frame.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "1"


class ServiceClass(StrEnum):
    standard = "standard"
    express = "express"


class Stage(StrEnum):
    receiving = "receiving"
    picking = "picking"
    packing = "packing"
    dispatch = "dispatch"


class OrderLine(BaseModel):
    """One order line. Quantity is in units of the SKU."""

    order_id: str
    sku: str
    quantity: int = Field(gt=0, le=10_000)
    arrival_minute: float = Field(ge=0, le=1440)
    deadline_minute: float = Field(ge=0, le=1440)
    service_class: ServiceClass = ServiceClass.standard

    @model_validator(mode="after")
    def deadline_after_arrival(self) -> OrderLine:
        if self.deadline_minute < self.arrival_minute:
            raise ValueError(
                f"order {self.order_id}: deadline {self.deadline_minute} "
                f"precedes arrival {self.arrival_minute}"
            )
        return self


class InventoryItem(BaseModel):
    sku: str
    on_hand: int = Field(ge=0, le=1_000_000)


class Replenishment(BaseModel):
    """Inbound stock arriving during the day."""

    sku: str
    quantity: int = Field(gt=0, le=1_000_000)
    arrival_minute: float = Field(ge=0, le=1440)


class ProcessingTimes(BaseModel):
    """Aggregate service time model, in minutes.

    A service time is `setup + per_unit * quantity`, multiplied by a
    variability factor drawn per order from a triangular distribution. The
    factor bounds are explicit so a reader can see the spread being assumed.
    """

    pick_setup: float = Field(default=2.0, ge=0, le=240)
    pick_per_unit: float = Field(default=0.5, ge=0, le=60)
    pack_setup: float = Field(default=1.5, ge=0, le=240)
    pack_per_unit: float = Field(default=0.2, ge=0, le=60)
    variability_low: float = Field(default=0.8, gt=0, le=1)
    variability_mode: float = Field(default=1.0, gt=0, le=5)
    variability_high: float = Field(default=1.5, ge=1, le=5)

    @model_validator(mode="after")
    def ordered_bounds(self) -> ProcessingTimes:
        if not self.variability_low <= self.variability_mode <= self.variability_high:
            raise ValueError("variability bounds must satisfy low <= mode <= high")
        return self


class ShiftCalendar(BaseModel):
    """Regular working window, in minutes from day start."""

    shift_start_minute: float = Field(default=0.0, ge=0, le=1440)
    shift_end_minute: float = Field(default=540.0, ge=0, le=1440)

    @model_validator(mode="after")
    def ordered_window(self) -> ShiftCalendar:
        if self.shift_end_minute <= self.shift_start_minute:
            raise ValueError("shift_end_minute must be after shift_start_minute")
        return self


class FacilityModel(BaseModel):
    """The process model. Provenance records which parts were confirmed."""

    facility_id: str = "north-warehouse"
    timezone: str = "Asia/Kolkata"
    pickers: int = Field(default=6, ge=1, le=500)
    packing_stations: int = Field(default=3, ge=1, le=500)
    processing_times: ProcessingTimes = ProcessingTimes()
    shift: ShiftCalendar = ShiftCalendar()
    provenance: dict[str, Literal["confirmed", "estimated"]] = Field(
        default_factory=lambda: {
            "shift": "confirmed",
            "processing_times": "estimated",
            "pickers": "confirmed",
            "packing_stations": "confirmed",
        }
    )


class PolicyVersion(BaseModel):
    """A dispatch policy. Constrained JSON, compiled to a trusted function.

    Model generated Python is deliberately out of scope. A proposer may only
    emit values inside these ranges.
    """

    schema_version: Literal["1"] = "1"
    name: str = "baseline"
    priority_strategy: Literal["fifo", "edf", "batch_by_sku"] = "fifo"
    batching_threshold: int = Field(default=1, ge=1, le=100)
    dispatch_cutoff_minute: float = Field(default=540.0, ge=0, le=1440)
    allowed_overtime_minutes: float = Field(default=0.0, ge=0, le=480)
    tie_breaking_rule: Literal["order_id", "smallest_quantity", "largest_quantity"] = "order_id"

    def digest(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]


class Scenario(BaseModel):
    """A snapshot plus a process model plus a policy. The unit that gets run."""

    scenario_id: str
    label: str
    orders: list[OrderLine]
    inventory: list[InventoryItem]
    replenishments: list[Replenishment] = Field(default_factory=list)
    facility: FacilityModel = FacilityModel()
    policy: PolicyVersion = PolicyVersion()

    @model_validator(mode="after")
    def unique_orders(self) -> Scenario:
        seen = [o.order_id for o in self.orders]
        if len(seen) != len(set(seen)):
            raise ValueError("order_id values must be unique within a scenario")
        skus = [i.sku for i in self.inventory]
        if len(skus) != len(set(skus)):
            raise ValueError("inventory sku values must be unique within a scenario")
        return self

    def missing_skus(self) -> list[str]:
        """Order lines that reference a SKU absent from inventory."""
        known = {i.sku for i in self.inventory}
        return sorted({o.sku for o in self.orders if o.sku not in known})

    def input_digest(self) -> str:
        payload = json.dumps(
            {
                "orders": [o.model_dump(mode="json") for o in self.orders],
                "inventory": [i.model_dump(mode="json") for i in self.inventory],
                "replenishments": [r.model_dump(mode="json") for r in self.replenishments],
                "facility": self.facility.model_dump(mode="json"),
                "policy": self.policy.model_dump(mode="json"),
            },
            sort_keys=True,
        ).encode()
        return hashlib.sha256(payload).hexdigest()[:16]
