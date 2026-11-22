from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from .mapping import _money, _pick, normalize_order, normalize_product
from .models import AuditEntry, DeadLetter, Discrepancy, Order, Product, SyncSummary


class CommerceErpPipeline:
    def __init__(self) -> None:
        self.products: dict[str, Product] = {}
        self.orders: dict[str, Order] = {}
        self.dead_letters: list[DeadLetter] = []
        self.audit: list[AuditEntry] = []

    def _record(self, action: str, reference: str, detail: str) -> None:
        self.audit.append(
            AuditEntry(
                at=datetime.now(UTC),
                action=action,
                reference=reference,
                detail=detail,
            )
        )

    def sync_products(
        self, records: list[dict[str, Any]], source_system: str
    ) -> SyncSummary:
        summary = SyncSummary(source_system=source_system, received=len(records))
        seen: set[str] = set()
        for raw in records:
            sku = str(
                raw.get("sku") or raw.get("item_code") or raw.get("code") or ""
            ).strip()
            try:
                product = normalize_product(raw, source_system)
                if product.sku in seen:
                    raise ValueError(f"duplicate sku in batch: {product.sku}")
                seen.add(product.sku)
                existing = self.products.get(product.sku)
                if existing is None:
                    self.products[product.sku] = product
                    summary.created += 1
                    action = "created"
                elif existing.fingerprint() != product.fingerprint():
                    self.products[product.sku] = product
                    summary.updated += 1
                    action = "updated"
                else:
                    summary.unchanged += 1
                    action = "unchanged"
                self._record(
                    action,
                    product.sku,
                    f"{product.name} price={product.price} stock={product.stock}",
                )
            except ValueError as exc:
                summary.rejected += 1
                letter = DeadLetter(uuid.uuid4().hex[:12], sku, dict(raw), [str(exc)])
                self.dead_letters.append(letter)
                summary.dead_letters.append(letter)
                self._record("rejected", sku, str(exc))
        return summary

    def ingest_order(
        self, raw: dict[str, Any], source_system: str
    ) -> tuple[Order | None, list[str]]:
        reference = str(
            raw.get("order_id") or raw.get("id") or raw.get("order_number") or ""
        ).strip()
        try:
            order = normalize_order(raw, source_system)
        except ValueError as exc:
            self._reject_order(reference, raw, [str(exc)])
            return None, [str(exc)]

        existing = self.orders.get(order.order_id)
        if existing is not None:
            if existing.lines == order.lines and existing.status == order.status:
                self._record("unchanged", order.order_id, "duplicate order delivery")
                return existing, []
            error = f"order {order.order_id} already exists with different lines"
            self._reject_order(order.order_id, raw, [error])
            return None, [error]

        errors: list[str] = []
        for line in order.lines:
            product = self.products.get(line.sku)
            if product is None:
                errors.append(f"unknown sku: {line.sku}")
            elif product.stock < line.quantity:
                errors.append(
                    f"insufficient stock for {line.sku}: need {line.quantity}, have {product.stock}"
                )
        if errors:
            self._reject_order(order.order_id, raw, errors)
            return None, errors

        for line in order.lines:
            product = self.products[line.sku]
            self.products[line.sku] = replace(
                product, stock=product.stock - line.quantity
            )
        self.orders[order.order_id] = order
        self._record(
            "order_accepted",
            order.order_id,
            f"total={order.total()} lines={len(order.lines)}",
        )
        return order, []

    def _reject_order(
        self, reference: str, raw: dict[str, Any], errors: list[str]
    ) -> None:
        letter = DeadLetter(uuid.uuid4().hex[:12], reference, dict(raw), errors)
        self.dead_letters.append(letter)
        self._record("order_rejected", reference, "; ".join(errors))

    def cancel_order(self, order_id: str) -> bool:
        order = self.orders.get(order_id)
        if order is None:
            return False
        if order.status == "cancelled":
            return True
        for line in order.lines:
            product = self.products.get(line.sku)
            if product is not None:
                self.products[line.sku] = replace(
                    product, stock=product.stock + line.quantity
                )
        self.orders[order_id] = replace(order, status="cancelled")
        self._record("order_cancelled", order_id, "stock released")
        return True

    def reconcile_erp(self, erp_rows: list[dict[str, Any]]) -> list[Discrepancy]:
        erp: dict[str, dict[str, Any]] = {}
        for row in erp_rows:
            sku = str(_pick(row, ("sku", "item_code", "code", "item")) or "").strip()
            if sku:
                erp[sku] = row
        discrepancies: list[Discrepancy] = []
        for sku, product in self.products.items():
            row = erp.get(sku)
            if row is None:
                discrepancies.append(
                    Discrepancy(
                        "missing_in_erp", sku, "commerce product is absent from ERP"
                    )
                )
                continue
            try:
                erp_price = _money(
                    _pick(row, ("price", "unit_price", "sell_price")), "erp_price"
                )
            except ValueError as exc:
                discrepancies.append(
                    Discrepancy("invalid_erp_price", sku, str(exc), str(product.price))
                )
                continue
            if abs(product.price - erp_price) > Decimal("0.01"):
                discrepancies.append(
                    Discrepancy(
                        "price_mismatch",
                        sku,
                        "commerce price differs from ERP",
                        str(product.price),
                        str(erp_price),
                    )
                )
            erp_stock_raw = _pick(row, ("stock", "inventory", "on_hand", "available"))
            if erp_stock_raw not in (None, ""):
                try:
                    erp_stock = int(str(erp_stock_raw).strip())
                except ValueError:
                    discrepancies.append(
                        Discrepancy(
                            "invalid_erp_stock",
                            sku,
                            f"invalid stock: {erp_stock_raw!r}",
                            str(product.stock),
                        )
                    )
                    continue
                if erp_stock != product.stock:
                    discrepancies.append(
                        Discrepancy(
                            "stock_mismatch",
                            sku,
                            "commerce stock differs from ERP",
                            str(product.stock),
                            str(erp_stock),
                        )
                    )
        for sku in sorted(set(erp) - set(self.products)):
            discrepancies.append(
                Discrepancy(
                    "missing_in_commerce",
                    sku,
                    "ERP product is absent from commerce catalog",
                )
            )
        return discrepancies
