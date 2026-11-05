from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .pipeline import CommerceErpPipeline


class ProductSyncIn(BaseModel):
    source_system: str
    records: list[dict[str, Any]]


class OrderIn(BaseModel):
    source_system: str = "shopify"
    order: dict[str, Any]


class ReconcileIn(BaseModel):
    erp: list[dict[str, Any]] = Field(default_factory=list)


def _summary(summary) -> dict[str, Any]:
    return {
        "source_system": summary.source_system,
        "received": summary.received,
        "created": summary.created,
        "updated": summary.updated,
        "unchanged": summary.unchanged,
        "rejected": summary.rejected,
        "dead_letters": [letter.__dict__ for letter in summary.dead_letters],
        "audit": [entry.__dict__ | {"at": entry.at.isoformat()} for entry in summary.audit],
    }


def create_app(pipeline: CommerceErpPipeline | None = None) -> FastAPI:
    state = pipeline or CommerceErpPipeline()
    app = FastAPI(title="Commerce ERP sync", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/products/sync")
    def sync_products(payload: ProductSyncIn) -> dict[str, Any]:
        return _summary(state.sync_products(payload.records, payload.source_system))

    @app.post("/orders")
    def create_order(payload: OrderIn) -> dict[str, Any]:
        order, errors = state.ingest_order(payload.order, payload.source_system)
        if order is None:
            raise HTTPException(status_code=422, detail={"errors": errors})
        return {"order_id": order.order_id, "status": order.status, "total": str(order.total())}

    @app.post("/orders/{order_id}/cancel")
    def cancel_order(order_id: str) -> dict[str, Any]:
        if not state.cancel_order(order_id):
            raise HTTPException(status_code=404, detail="order not found")
        return {"order_id": order_id, "status": "cancelled"}

    @app.post("/reconcile")
    def reconcile(payload: ReconcileIn) -> dict[str, Any]:
        return {"discrepancies": [item.__dict__ for item in state.reconcile_erp(payload.erp)]}

    @app.get("/audit")
    def audit() -> dict[str, Any]:
        return {"audit": [entry.__dict__ | {"at": entry.at.isoformat()} for entry in state.audit]}

    return app


app = create_app()
