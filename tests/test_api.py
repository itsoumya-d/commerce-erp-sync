from fastapi.testclient import TestClient

from commerceerp.api import create_app


def test_api_product_order_cancel_and_reconcile():
    client = TestClient(create_app())
    products = client.post(
        "/products/sync",
        json={"source_system": "shopify", "records": [{"sku": "TSHIRT-001", "name": "Tee", "price": "89.90", "stock": 4}]},
    )
    assert products.status_code == 200
    assert products.json()["created"] == 1

    order = client.post(
        "/orders",
        json={"source_system": "shopify", "order": {"order_id": "WEB-1", "lines": [{"sku": "TSHIRT-001", "quantity": 1, "unit_price": "89.90"}]}},
    )
    assert order.status_code == 200
    assert order.json()["total"] == "89.90"

    cancel = client.post("/orders/WEB-1/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"

    reconcile = client.post("/reconcile", json={"erp": [{"sku": "TSHIRT-001", "price": "79.00", "stock": 4}]})
    assert reconcile.status_code == 200
    assert reconcile.json()["discrepancies"][0]["kind"] == "price_mismatch"


def test_api_rejects_bad_order_and_audits():
    client = TestClient(create_app())
    response = client.post("/orders", json={"order": {"order_id": "WEB-2", "lines": []}})
    assert response.status_code == 422
    audit = client.get("/audit")
    assert audit.json()["audit"][0]["action"] == "order_rejected"
