from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .models import Product
from .pipeline import CommerceErpPipeline


@dataclass(frozen=True)
class ShopifyVariant:
    variant_id: str
    title: str
    sku: str
    price: Decimal
    compare_at_price: Decimal | None
    inventory_quantity: int
    barcode: str = ""


@dataclass(frozen=True)
class ShopifyProduct:
    product_id: str
    title: str
    handle: str
    variants: tuple[ShopifyVariant, ...]
    tags: tuple[str, ...] = ()
    vendor: str = ""


@dataclass
class MultiCurrencyConfig:
    base_currency: str = "NZD"
    exchange_rates: dict[str, Decimal] = field(
        default_factory=lambda: {
            "NZD": Decimal("1.00"),
            "USD": Decimal("0.60"),
            "CAD": Decimal("0.82"),
            "AUD": Decimal("0.92"),
        }
    )
    market_fixed_overrides: dict[str, dict[str, Decimal]] = field(
        default_factory=dict
    )

    def get_price(self, sku: str, base_nzd_price: Decimal, currency: str) -> Decimal:
        curr = currency.upper().strip()
        # 1. Check fixed market override
        if (
            curr in self.market_fixed_overrides
            and sku in self.market_fixed_overrides[curr]
        ):
            return self.market_fixed_overrides[curr][sku]

        # 2. Check exchange rate conversion
        rate = self.exchange_rates.get(curr)
        if rate is None:
            raise ValueError(f"unsupported currency: {currency}")

        converted = (base_nzd_price * rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return converted


@dataclass
class MigrationReport:
    total_received: int = 0
    products_imported: int = 0
    variants_mapped: int = 0
    errors: list[str] = field(default_factory=list)
    multi_currency_summary: dict[str, dict[str, str]] = field(
        default_factory=dict
    )


class ShopifyMigrationService:
    def __init__(
        self,
        pipeline: CommerceErpPipeline,
        currency_config: MultiCurrencyConfig | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.currency_config = currency_config or MultiCurrencyConfig()

    def parse_variant(self, raw: dict[str, Any]) -> ShopifyVariant:
        sku = str(raw.get("sku") or "").strip()
        if not sku:
            raise ValueError("variant missing required sku")

        raw_price = raw.get("price")
        if raw_price is None:
            raise ValueError(f"variant {sku} missing price")
        price = Decimal(str(raw_price)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        if price < 0:
            raise ValueError(f"variant {sku} cannot have negative price: {price}")

        raw_compare = raw.get("compare_at_price")
        compare_price = (
            Decimal(str(raw_compare)).quantize(Decimal("0.01"))
            if raw_compare is not None
            else None
        )

        qty = int(raw.get("inventory_quantity", 0))
        return ShopifyVariant(
            variant_id=str(raw.get("id") or ""),
            title=str(raw.get("title") or "Default"),
            sku=sku,
            price=price,
            compare_at_price=compare_price,
            inventory_quantity=qty,
            barcode=str(raw.get("barcode") or ""),
        )

    def import_products(
        self, payload: dict[str, Any] | list[dict[str, Any]]
    ) -> MigrationReport:
        items = (
            payload.get("products", [])
            if isinstance(payload, dict)
            else payload
        )
        report = MigrationReport(total_received=len(items))

        for raw_p in items:
            p_id = str(raw_p.get("id") or "")
            title = str(raw_p.get("title") or "").strip()

            raw_variants = raw_p.get("variants") or []
            if not raw_variants:
                report.errors.append(f"product {p_id} ({title}) has no variants")
                continue

            parsed_variants: list[ShopifyVariant] = []
            for raw_v in raw_variants:
                try:
                    v = self.parse_variant(raw_v)
                    parsed_variants.append(v)
                except (ValueError, TypeError, KeyError) as exc:
                    report.errors.append(f"product {p_id} variant error: {exc}")

            if not parsed_variants:
                continue

            # Ingest variants into pipeline as standard Products
            for v in parsed_variants:
                p_name = f"{title} - {v.title}" if v.title != "Default" else title
                product = Product(
                    sku=v.sku,
                    name=p_name,
                    price=v.price,
                    stock=v.inventory_quantity,
                    source_system="shopify_migration",
                )
                self.pipeline.products[v.sku] = product
                report.variants_mapped += 1

                # Generate multi-currency matrix
                matrix: dict[str, str] = {}
                for curr in self.currency_config.exchange_rates:
                    m_price = self.currency_config.get_price(v.sku, v.price, curr)
                    matrix[curr] = f"{m_price:.2f}"
                report.multi_currency_summary[v.sku] = matrix

            report.products_imported += 1

        return report
