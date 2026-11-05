# commerce-erp-sync

[![ci](https://github.com/itsoumya-d/commerce-erp-sync/actions/workflows/ci.yml/badge.svg)](https://github.com/itsoumya-d/commerce-erp-sync/actions/workflows/ci.yml)

A small, tested reference for ecommerce-to-ERP integration:

- Normalize product, price and stock records from commerce platforms.
- Idempotent product sync with create/update/unchanged decisions.
- Order ingestion with line validation, stock reservation and idempotent
  duplicate delivery.
- Reject unknown SKUs and insufficient stock into a dead-letter queue.
- Cancel orders and release reserved stock exactly once.
- Reconcile commerce prices and stock against an ERP extract.
- Audit every accepted, changed, rejected and cancelled record.
- FastAPI endpoints and a pytest suite.

This is a **reference implementation**, not a claim of production Shopify,
WooCommerce, Accredo, NetSuite, Dynamics 365 Business Central or Odoo
integration. Live connectors, webhook verification and credentials are out of
scope; the mapping, order, inventory, reconciliation and audit core is tested.

## Run

```bash
uv venv --python 3.12
source .venv/bin/activate
pip install -e ".[dev]"
pytest
uvicorn commerceerp.api:app --reload
```

## License

MIT
