from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .models import Order, OrderLine, Product

ALIASES = {
    "sku": ("sku", "product_sku", "item_code", "item", "code", "variant_sku"),
    "name": ("name", "product_name", "title", "description"),
    "price": ("price", "unit_price", "retail_price", "sell_price", "amount"),
    "stock": ("stock", "inventory", "qty", "quantity", "available", "on_hand"),
    "order_id": ("order_id", "id", "order_number", "number", "reference"),
    "lines": ("lines", "items", "line_items", "order_lines"),
    "status": ("status", "state", "financial_status"),
}


def _pick(raw: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
    return None


def _money(value: Any, field: str) -> Decimal:
    if value in (None, ""):
        raise ValueError(f"{field} is required")
    cleaned = str(value).replace("$", "").replace(",", "").replace(" ", "").strip()
    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if amount < 0:
        raise ValueError(f"negative {field}: {value!r}")
    return amount.quantize(Decimal("0.01"))


def _int(value: Any, field: str) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if number < 0:
        raise ValueError(f"negative {field}: {value!r}")
    return number


def normalize_product(raw: dict[str, Any], source_system: str) -> Product:
    sku = str(_pick(raw, ALIASES["sku"]) or "").strip()
    name = str(_pick(raw, ALIASES["name"]) or "").strip()
    if not sku:
        raise ValueError("sku is required")
    if not name:
        raise ValueError("name is required")
    return Product(
        sku=sku,
        name=name,
        price=_money(_pick(raw, ALIASES["price"]), "price"),
        stock=_int(_pick(raw, ALIASES["stock"]), "stock"),
        source_system=source_system,
    )


def normalize_order(raw: dict[str, Any], source_system: str) -> Order:
    order_id = str(_pick(raw, ALIASES["order_id"]) or "").strip()
    if not order_id:
        raise ValueError("order_id is required")
    lines_raw = _pick(raw, ALIASES["lines"])
    if not isinstance(lines_raw, list) or not lines_raw:
        raise ValueError("order must contain at least one line")
    lines: list[OrderLine] = []
    for line in lines_raw:
        sku = str(_pick(line, ALIASES["sku"]) or "").strip()
        if not sku:
            raise ValueError("order line sku is required")
        quantity = _int(_pick(line, ("quantity", "qty", "count")), "quantity")
        if quantity <= 0:
            raise ValueError(f"quantity must be positive for {sku}")
        lines.append(
            OrderLine(
                sku=sku,
                quantity=quantity,
                unit_price=_money(_pick(line, ALIASES["price"]), "unit_price"),
            )
        )
    status = str(_pick(raw, ALIASES["status"]) or "paid").strip().lower()
    return Order(
        order_id=order_id,
        lines=tuple(lines),
        status=status,
        source_system=source_system,
        created_at=datetime.now(timezone.utc),
    )
