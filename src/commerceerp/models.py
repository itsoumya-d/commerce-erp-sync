from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: Decimal
    stock: int
    source_system: str
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def fingerprint(self) -> str:
        payload = {"name": self.name, "price": str(self.price), "stock": self.stock}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:24]


@dataclass(frozen=True)
class OrderLine:
    sku: str
    quantity: int
    unit_price: Decimal


@dataclass(frozen=True)
class Order:
    order_id: str
    lines: tuple[OrderLine, ...]
    status: str
    source_system: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def total(self) -> Decimal:
        return sum((line.unit_price * line.quantity for line in self.lines), Decimal("0.00"))


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    action: str
    reference: str
    detail: str


@dataclass
class DeadLetter:
    letter_id: str
    reference: str
    payload: dict[str, Any]
    errors: list[str]
    attempts: int = 1


@dataclass(frozen=True)
class Discrepancy:
    kind: str
    sku: str
    detail: str
    commerce_value: str = ""
    erp_value: str = ""


@dataclass
class SyncSummary:
    source_system: str
    received: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: int = 0
    dead_letters: list[DeadLetter] = field(default_factory=list)
    audit: list[AuditEntry] = field(default_factory=list)
