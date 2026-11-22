from decimal import Decimal

import pytest

from commerceerp.mapping import normalize_product
from commerceerp.pipeline import CommerceErpPipeline


def product(**overrides):
    data = {
        "sku": "TSHIRT-001",
        "title": "Merino T-shirt",
        "price": "$89.90",
        "stock": "10",
    }
    data.update(overrides)
    return data


def order(**overrides):
    data = {
        "order_number": "WEB-1001",
        "financial_status": "paid",
        "line_items": [{"sku": "TSHIRT-001", "quantity": 2, "price": "89.90"}],
    }
    data.update(overrides)
    return data


def test_normalize_product_aliases():
    item = normalize_product(product(), "shopify")
    assert item.sku == "TSHIRT-001"
    assert item.name == "Merino T-shirt"
    assert item.price == Decimal("89.90")
    assert item.stock == 10


def test_normalize_product_rejects_bad_values():
    with pytest.raises(ValueError, match="invalid price"):
        normalize_product(product(price="free"), "shopify")
    with pytest.raises(ValueError, match="negative stock"):
        normalize_product(product(stock="-1"), "shopify")


def test_product_sync_creates_updates_and_skips_unchanged():
    pipeline = CommerceErpPipeline()
    first = pipeline.sync_products([product()], "shopify")
    assert (first.created, first.updated, first.unchanged, first.rejected) == (
        1,
        0,
        0,
        0,
    )
    same = pipeline.sync_products([product()], "shopify")
    assert (same.created, same.updated, same.unchanged) == (0, 0, 1)
    changed = pipeline.sync_products([product(stock="4")], "shopify")
    assert (changed.created, changed.updated) == (0, 1)


def test_order_reserves_stock_and_is_idempotent():
    pipeline = CommerceErpPipeline()
    pipeline.sync_products([product()], "shopify")
    first, errors = pipeline.ingest_order(order(), "shopify")
    assert errors == [] and first is not None
    assert pipeline.products["TSHIRT-001"].stock == 8
    again, errors = pipeline.ingest_order(order(), "shopify")
    assert errors == [] and again.order_id == "WEB-1001"
    assert pipeline.products["TSHIRT-001"].stock == 8


def test_order_rejects_insufficient_stock_into_dead_letter():
    pipeline = CommerceErpPipeline()
    pipeline.sync_products([product(stock="1")], "shopify")
    created, errors = pipeline.ingest_order(order(), "shopify")
    assert created is None
    assert "insufficient stock" in errors[0]
    assert pipeline.products["TSHIRT-001"].stock == 1
    assert len(pipeline.dead_letters) == 1


def test_order_rejects_unknown_sku():
    pipeline = CommerceErpPipeline()
    created, errors = pipeline.ingest_order(order(), "shopify")
    assert created is None
    assert errors == ["unknown sku: TSHIRT-001"]


def test_cancel_releases_stock_and_is_idempotent():
    pipeline = CommerceErpPipeline()
    pipeline.sync_products([product()], "shopify")
    pipeline.ingest_order(order(), "shopify")
    assert pipeline.cancel_order("WEB-1001") is True
    assert pipeline.products["TSHIRT-001"].stock == 10
    assert pipeline.cancel_order("WEB-1001") is True
    assert pipeline.products["TSHIRT-001"].stock == 10


def test_reconcile_reports_price_stock_and_missing_rows():
    pipeline = CommerceErpPipeline()
    pipeline.sync_products([product()], "shopify")
    rows = [
        {"item_code": "TSHIRT-001", "sell_price": "99.00", "on_hand": 7},
        {"item_code": "HAT-9", "price": "20"},
    ]
    kinds = {item.kind for item in pipeline.reconcile_erp(rows)}
    assert {"price_mismatch", "stock_mismatch", "missing_in_commerce"} <= kinds


def test_reconcile_reports_missing_in_erp():
    pipeline = CommerceErpPipeline()
    pipeline.sync_products([product()], "shopify")
    assert [item.kind for item in pipeline.reconcile_erp([])] == ["missing_in_erp"]


def test_duplicate_order_with_different_payload_is_rejected():
    pipeline = CommerceErpPipeline()
    pipeline.sync_products([product()], "shopify")
    pipeline.ingest_order(order(), "shopify")
    changed = order(line_items=[{"sku": "TSHIRT-001", "quantity": 3, "price": "89.90"}])
    created, errors = pipeline.ingest_order(changed, "shopify")
    assert created is None
    assert "already exists" in errors[0]
