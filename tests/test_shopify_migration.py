from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from commerceerp.api import create_app
from commerceerp.pipeline import CommerceErpPipeline
from commerceerp.shopify_migration import (
    MultiCurrencyConfig,
    ShopifyMigrationService,
)


def test_multi_currency_pricing_and_fixed_overrides():
    config = MultiCurrencyConfig(
        base_currency="NZD",
        exchange_rates={
            "NZD": Decimal("1.00"),
            "USD": Decimal("0.60"),
            "CAD": Decimal("0.82"),
        },
        market_fixed_overrides={
            "USD": {"COFFEE-CHEW-6PK": Decimal("24.99")}
        },
    )

    base_price = Decimal("39.95")
    # Dynamic conversion for CAD: 39.95 * 0.82 = 32.759 -> 32.76
    assert config.get_price("GENERIC-SKU", base_price, "CAD") == Decimal("32.76")

    # Fixed override for US market:
    assert config.get_price("COFFEE-CHEW-6PK", base_price, "USD") == Decimal("24.99")

    # Dynamic conversion for USD on non-override sku: 39.95 * 0.60 = 23.97
    assert config.get_price("OTHER-SKU", base_price, "USD") == Decimal("23.97")

    with pytest.raises(ValueError, match="unsupported currency"):
        config.get_price("GENERIC-SKU", base_price, "EUR")


def test_shopify_product_migration_ingestion():
    pipeline = CommerceErpPipeline()
    config = MultiCurrencyConfig()
    service = ShopifyMigrationService(pipeline, config)

    shopify_payload = {
        "products": [
            {
                "id": "prod_101",
                "title": "Energy Coffee Chews",
                "handle": "energy-coffee-chews",
                "variants": [
                    {
                        "id": "var_201",
                        "title": "Mocha / 12-Pack",
                        "sku": "CHW-MOCHA-12",
                        "price": "29.90",
                        "compare_at_price": "34.90",
                        "inventory_quantity": 450,
                    },
                    {
                        "id": "var_202",
                        "title": "Caramel / 12-Pack",
                        "sku": "CHW-CARM-12",
                        "price": "29.90",
                        "inventory_quantity": 320,
                    },
                ],
            },
            {
                "id": "prod_102",
                "title": "Corrupt Item",
                "handle": "corrupt-item",
                "variants": [
                    {"id": "var_bad", "title": "Missing SKU", "price": "10.00"},
                ],
            },
        ]
    }

    report = service.import_products(shopify_payload)

    assert report.total_received == 2
    assert report.products_imported == 1
    assert report.variants_mapped == 2
    assert len(report.errors) == 1
    assert "missing required sku" in report.errors[0]

    # Verify pipeline state
    assert "CHW-MOCHA-12" in pipeline.products
    mocha = pipeline.products["CHW-MOCHA-12"]
    assert mocha.name == "Energy Coffee Chews - Mocha / 12-Pack"
    assert mocha.price == Decimal("29.90")
    assert mocha.stock == 450
    assert mocha.source_system == "shopify_migration"

    # Verify multi-currency summary
    summary = report.multi_currency_summary["CHW-MOCHA-12"]
    assert summary["NZD"] == "29.90"
    assert summary["USD"] == "17.94"  # 29.90 * 0.60 = 17.94


def test_api_shopify_migration_and_catalogue_lookup():
    pipeline = CommerceErpPipeline()
    config = MultiCurrencyConfig()
    app = create_app(pipeline=pipeline, currency_config=config)
    client = TestClient(app)

    migration_payload = {
        "products": [
            {
                "id": "prod_999",
                "title": "Classic Chew Variety Pack",
                "variants": [
                    {
                        "id": "var_555",
                        "title": "Default",
                        "sku": "CHEW-VARIETY-24",
                        "price": "49.99",
                        "inventory_quantity": 180,
                    }
                ],
            }
        ]
    }

    resp = client.post("/api/v1/migration/shopify/products", json=migration_payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["products_imported"] == 1
    assert data["variants_mapped"] == 1
    assert "CHEW-VARIETY-24" in data["multi_currency_summary"]

    # Test catalogue lookup
    get_resp = client.get("/api/v1/catalogue/multi-currency/CHEW-VARIETY-24")
    assert get_resp.status_code == 200
    cat_data = get_resp.json()
    assert cat_data["sku"] == "CHEW-VARIETY-24"
    assert cat_data["stock"] == 180
    assert cat_data["base_price_nzd"] == "49.99"
    assert "USD" in cat_data["multi_currency_prices"]
    assert "CAD" in cat_data["multi_currency_prices"]

    # Test 404 for unknown sku
    not_found_resp = client.get("/api/v1/catalogue/multi-currency/NON-EXISTENT")
    assert not_found_resp.status_code == 404
