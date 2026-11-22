# commerce-erp-sync

[![ci](https://github.com/itsoumya-d/commerce-erp-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/itsoumya-d/commerce-erp-sync/actions/workflows/ci.yml)

A tested reference architecture for ecommerce-to-ERP synchronization and social commerce creator attribution:

- **Product & Inventory Sync**: Normalize product, price and stock records across commerce platforms with stock reservation and idempotent duplicate delivery.
- **Social Commerce Creator Attribution**: Ingest affiliate sales events from TikTok Shop, Instagram, YouTube Shopping, Amazon, and Shopify with SHA-256 idempotency hashing.
- **Tiered Commission Settlement**: Calculate net payouts with volume-based bonus tiers (Gold/Platinum) and platform fee reconciliation.
- **Refund & Cancellation Rollbacks**: Transactional reversal handling that decrements creator balances and records audit settlements.
- **Dead-Letter Dispatch**: Fault-tolerant message routing with exponential backoff for failed webhook deliveries.
- **FastAPI Endpoints**: REST API covering product sync, order intake, affiliate sales ingestion, and creator performance summaries.

*Independent study of commerce synchronization and social commerce creator attribution systems; not affiliated with any company.*

## Architecture

```
[TikTok Shop / Meta / Shopify Webhook]
                 │
                 ▼
     [CreatorAttributionEngine]
       ├── SHA-256 Idempotency Guard (order_id + status + GMV)
       ├── Tier Escalation Calculator (Standard / Gold / Platinum)
       ├── Platform Fee Deductions
       └── Reversal / Refund Engine
                 │
                 ▼
       [FastAPI Endpoint Layer]
       ├── POST /api/v1/affiliate/sales
       ├── GET  /api/v1/affiliate/creators/{id}/summary
       └── GET  /api/v1/affiliate/dead-letters
```

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn commerceerp.api:app --reload
```

## Endpoints

- `GET /health` — Service health check.
- `POST /products/sync` — Normalize and synchronize product catalogs.
- `POST /orders` — Ingest commerce orders with stock reservation.
- `POST /api/v1/affiliate/sales` — Ingest creator sales events with idempotent commission settlement.
- `GET /api/v1/affiliate/creators/{creator_id}/summary` — Aggregate creator GMV, settled commissions, and tier status.
- `GET /api/v1/affiliate/dead-letters` — Inspect rejected or failed event records.

## License

MIT
