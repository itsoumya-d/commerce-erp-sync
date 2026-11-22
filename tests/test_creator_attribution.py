from fastapi.testclient import TestClient

from commerceerp.api import create_app
from commerceerp.creator_attribution import (
    CreatorAttributionEngine,
    CreatorSaleEvent,
    OrderStatus,
    PlatformType,
)


def test_standard_sale_ingestion_and_commission():
    engine = CreatorAttributionEngine(platform_fee_rate=0.015)
    event = CreatorSaleEvent(
        event_id="evt_001",
        order_id="ord_101",
        creator_id="creator_alpha",
        platform=PlatformType.TIKTOK_SHOP,
        gross_merchandise_value=100.0,
        base_commission_rate=0.10,
        status=OrderStatus.COMPLETED,
    )

    settlement = engine.ingest_sale_event(event)

    assert settlement.order_id == "ord_101"
    assert settlement.creator_id == "creator_alpha"
    assert settlement.gmv == 100.0
    assert settlement.base_commission == 10.0
    assert settlement.tier_bonus == 0.0
    # Platform fee = 1.5% of $10.00 = $0.15
    assert settlement.platform_fee == 0.15
    # Net commission = 10.00 - 0.15 = 9.85
    assert settlement.net_commission == 9.85
    assert settlement.status == "settled"


def test_idempotency_duplicate_protection():
    engine = CreatorAttributionEngine(platform_fee_rate=0.015)
    event = CreatorSaleEvent(
        event_id="evt_002",
        order_id="ord_102",
        creator_id="creator_alpha",
        platform=PlatformType.TIKTOK_SHOP,
        gross_merchandise_value=250.0,
        base_commission_rate=0.10,
    )

    first = engine.ingest_sale_event(event)
    second = engine.ingest_sale_event(event)

    # Identical settlement returned; duplicate event does not double count
    assert first.settlement_id == second.settlement_id
    assert first.idempotency_hash == second.idempotency_hash

    summary = engine.get_creator_summary("creator_alpha")
    assert summary.total_gmv == 250.0
    assert summary.order_count == 1


def test_tiered_bonus_escalation():
    engine = CreatorAttributionEngine(platform_fee_rate=0.01)

    # 1. High-value sale to push creator past Gold tier ($10,000)
    engine.ingest_sale_event(
        CreatorSaleEvent(
            event_id="evt_bulk",
            order_id="ord_bulk",
            creator_id="creator_top",
            platform=PlatformType.TIKTOK_SHOP,
            gross_merchandise_value=12_000.0,
            base_commission_rate=0.10,
        )
    )

    # 2. Next sale should receive +2% Gold tier bonus
    subsequent_event = CreatorSaleEvent(
        event_id="evt_sub",
        order_id="ord_sub",
        creator_id="creator_top",
        platform=PlatformType.INSTAGRAM,
        gross_merchandise_value=1_000.0,
        base_commission_rate=0.10,
    )
    settlement = engine.ingest_sale_event(subsequent_event)

    assert settlement.effective_rate == 0.12  # 10% base + 2% bonus
    assert settlement.base_commission == 100.0
    assert settlement.tier_bonus == 20.0
    # gross = 120, fee = 1% of 120 = 1.20, net = 118.80
    assert settlement.net_commission == 118.80

    summary = engine.get_creator_summary("creator_top")
    assert summary.active_tier == "Gold (+2% bonus)"


def test_refund_reversal_handling():
    engine = CreatorAttributionEngine(platform_fee_rate=0.0)

    # Initial order completed
    engine.ingest_sale_event(
        CreatorSaleEvent(
            event_id="evt_orig",
            order_id="ord_refund_me",
            creator_id="creator_beta",
            platform=PlatformType.SHOPIFY,
            gross_merchandise_value=500.0,
            base_commission_rate=0.10,
            status=OrderStatus.COMPLETED,
        )
    )

    # Refund event received
    refund_event = CreatorSaleEvent(
        event_id="evt_ref",
        order_id="ord_refund_me",
        creator_id="creator_beta",
        platform=PlatformType.SHOPIFY,
        gross_merchandise_value=500.0,
        base_commission_rate=0.10,
        status=OrderStatus.REFUNDED,
    )
    reversal = engine.ingest_sale_event(refund_event)

    assert reversal.status == "reversed"
    assert reversal.gmv == -500.0
    assert reversal.net_commission == -50.0

    summary = engine.get_creator_summary("creator_beta")
    assert summary.total_gmv == 0.0
    assert summary.settled_commissions == 0.0
    assert summary.refunded_commissions == 50.0


def test_api_creator_attribution_endpoints():
    client = TestClient(create_app())

    # Ingest sale via API
    payload = {
        "event_id": "api_evt_1",
        "order_id": "api_ord_1",
        "creator_id": "creator_gamma",
        "platform": "tiktok_shop",
        "gross_merchandise_value": 300.0,
        "currency": "USD",
        "base_commission_rate": 0.15,
        "status": "completed",
    }
    res = client.post("/api/v1/affiliate/sales", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["order_id"] == "api_ord_1"
    assert data["creator_id"] == "creator_gamma"
    assert data["gmv"] == 300.0
    assert data["base_commission"] == 45.0

    # Query creator summary
    summary_res = client.get("/api/v1/affiliate/creators/creator_gamma/summary")
    assert summary_res.status_code == 200
    summary = summary_res.json()
    assert summary["creator_id"] == "creator_gamma"
    assert summary["total_gmv"] == 300.0
    assert summary["order_count"] == 1
    assert summary["active_tier"] == "Standard"
