"""CSV import and export with an explicit repair report.

An import never silently drops a row. Every rejected row is returned with its
line number and the reason, so the caller can write a repair file the operator
can fix and resubmit.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, time
from typing import Any

from pydantic import BaseModel, ValidationError

from .models import InventoryItem, OrderLine, Replenishment, Scenario, ServiceClass

ORDER_COLUMNS = ["order_id", "sku", "quantity", "arrival", "deadline", "service_class"]
INVENTORY_COLUMNS = ["sku", "on_hand"]
REPLENISHMENT_COLUMNS = ["sku", "quantity", "arrival"]


class RowIssue(BaseModel):
    line_number: int
    column: str | None
    reason: str
    raw: dict[str, str]


class ImportReport(BaseModel):
    """What the importer accepted, what it rejected, and why."""

    accepted_rows: int
    rejected_rows: int
    issues: list[RowIssue]
    missing_skus: list[str] = []

    @property
    def is_clean(self) -> bool:
        return self.rejected_rows == 0 and not self.missing_skus

    def repair_csv(self, columns: list[str]) -> str:
        """A CSV of only the rejected rows, with the reason appended."""
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=[*columns, "rejection_reason"])
        writer.writeheader()
        for issue in self.issues:
            row = {column: issue.raw.get(column, "") for column in columns}
            row["rejection_reason"] = issue.reason
            writer.writerow(row)
        return buffer.getvalue()


def parse_minute(value: str, day_start: time = time(8, 0)) -> float:
    """Accept a plain minute offset, an HH:MM clock time, or an ISO timestamp.

    Clock times are interpreted against the facility day start, so 08:00 with a
    default day start is minute 0 and 17:00 is minute 540.
    """
    text = value.strip()
    if not text:
        raise ValueError("empty time value")

    try:
        return float(text)
    except ValueError:
        pass

    if ":" in text and "T" not in text and "-" not in text:
        parts = text.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(f"cannot read clock time {text!r}")
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(f"clock time out of range: {text!r}")
        base = day_start.hour * 60 + day_start.minute
        return float(hour * 60 + minute - base)

    try:
        stamp = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"cannot read time {text!r}, expected minutes, HH:MM, or ISO") from exc
    base = day_start.hour * 60 + day_start.minute
    return float(stamp.hour * 60 + stamp.minute - base)


def _first_error(exc: ValidationError) -> tuple[str | None, str]:
    error = exc.errors()[0]
    location = error.get("loc") or (None,)
    column = str(location[0]) if location[0] is not None else None
    return column, error.get("msg", "invalid value")


def read_orders(text: str, day_start: time = time(8, 0)) -> tuple[list[OrderLine], list[RowIssue]]:
    orders: list[OrderLine] = []
    issues: list[RowIssue] = []
    reader = csv.DictReader(io.StringIO(text))

    for index, raw in enumerate(reader, start=2):
        cleaned = {key: (value or "").strip() for key, value in raw.items() if key}
        try:
            service = cleaned.get("service_class") or "standard"
            orders.append(
                OrderLine(
                    order_id=cleaned["order_id"],
                    sku=cleaned["sku"],
                    quantity=int(cleaned["quantity"]),
                    arrival_minute=parse_minute(cleaned["arrival"], day_start),
                    deadline_minute=parse_minute(cleaned["deadline"], day_start),
                    service_class=ServiceClass(service),
                )
            )
        except ValidationError as exc:
            column, reason = _first_error(exc)
            issues.append(RowIssue(line_number=index, column=column, reason=reason, raw=cleaned))
        except (KeyError, ValueError) as exc:
            issues.append(RowIssue(line_number=index, column=None, reason=str(exc), raw=cleaned))

    return orders, issues


def read_inventory(text: str) -> tuple[list[InventoryItem], list[RowIssue]]:
    items: list[InventoryItem] = []
    issues: list[RowIssue] = []
    for index, raw in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        cleaned = {key: (value or "").strip() for key, value in raw.items() if key}
        try:
            items.append(InventoryItem(sku=cleaned["sku"], on_hand=int(cleaned["on_hand"])))
        except ValidationError as exc:
            column, reason = _first_error(exc)
            issues.append(RowIssue(line_number=index, column=column, reason=reason, raw=cleaned))
        except (KeyError, ValueError) as exc:
            issues.append(RowIssue(line_number=index, column=None, reason=str(exc), raw=cleaned))
    return items, issues


def read_replenishments(
    text: str, day_start: time = time(8, 0)
) -> tuple[list[Replenishment], list[RowIssue]]:
    items: list[Replenishment] = []
    issues: list[RowIssue] = []
    for index, raw in enumerate(csv.DictReader(io.StringIO(text)), start=2):
        cleaned = {key: (value or "").strip() for key, value in raw.items() if key}
        try:
            items.append(
                Replenishment(
                    sku=cleaned["sku"],
                    quantity=int(cleaned["quantity"]),
                    arrival_minute=parse_minute(cleaned["arrival"], day_start),
                )
            )
        except ValidationError as exc:
            column, reason = _first_error(exc)
            issues.append(RowIssue(line_number=index, column=column, reason=reason, raw=cleaned))
        except (KeyError, ValueError) as exc:
            issues.append(RowIssue(line_number=index, column=None, reason=str(exc), raw=cleaned))
    return items, issues


def build_report(
    orders: list[OrderLine],
    inventory: list[InventoryItem],
    issues: list[RowIssue],
) -> ImportReport:
    known = {item.sku for item in inventory}
    missing = sorted({order.sku for order in orders if order.sku not in known})
    return ImportReport(
        accepted_rows=len(orders) + len(inventory),
        rejected_rows=len(issues),
        issues=issues,
        missing_skus=missing,
    )


def _write(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def write_orders(orders: list[OrderLine]) -> str:
    return _write(
        [
            {
                "order_id": o.order_id,
                "sku": o.sku,
                "quantity": o.quantity,
                "arrival": round(o.arrival_minute, 2),
                "deadline": round(o.deadline_minute, 2),
                "service_class": o.service_class.value,
            }
            for o in orders
        ],
        ORDER_COLUMNS,
    )


def write_inventory(items: list[InventoryItem]) -> str:
    return _write([{"sku": i.sku, "on_hand": i.on_hand} for i in items], INVENTORY_COLUMNS)


def write_replenishments(items: list[Replenishment]) -> str:
    return _write(
        [
            {"sku": r.sku, "quantity": r.quantity, "arrival": round(r.arrival_minute, 2)}
            for r in items
        ],
        REPLENISHMENT_COLUMNS,
    )


def scenario_to_csvs(scenario: Scenario) -> dict[str, str]:
    return {
        "orders.csv": write_orders(scenario.orders),
        "inventory.csv": write_inventory(scenario.inventory),
        "replenishments.csv": write_replenishments(scenario.replenishments),
    }
