"""Social commerce creator attribution and commission settlement engine.

Models multi-platform creator sales ingestion (TikTok Shop, Instagram, YouTube Shopping, Amazon, Shopify),
idempotent attribution, tiered commission calculations, refund reversals, and dead-letter routing.
"""

from __future__ import annotations

import datetime as dt
import enum
import hashlib
import threading

from pydantic import BaseModel, Field, field_validator


class PlatformType(str, enum.Enum):
    TIKTOK_SHOP = "tiktok_shop"
    INSTAGRAM = "instagram"
    YOUTUBE_SHOPPING = "youtube_shopping"
    AMAZON = "amazon"
    SHOPIFY = "shopify"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    REFUNDED = "refunded"
    CANCELLED = "cancelled"


class CreatorSaleEvent(BaseModel):
    event_id: str = Field(
        ..., min_length=1, description="Unique upstream webhook event id"
    )
    order_id: str = Field(..., min_length=1, description="External commerce order id")
    creator_id: str = Field(..., min_length=1, description="Unique creator identifier")
    platform: PlatformType
    gross_merchandise_value: float = Field(
        ..., ge=0.0, description="Gross merchandise value (GMV)"
    )
    currency: str = Field(default="USD", min_length=3, max_length=3)
    base_commission_rate: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Agreed commission percentage (0.0-1.0)",
    )
    status: OrderStatus = OrderStatus.COMPLETED
    timestamp: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @field_validator("gross_merchandise_value")
    @classmethod
    def validate_gmv(cls, v: float) -> float:
        return round(float(v), 2)


class CommissionSettlement(BaseModel):
    settlement_id: str
    order_id: str
    creator_id: str
    platform: PlatformType
    gmv: float
    effective_rate: float
    base_commission: float
    tier_bonus: float
    platform_fee: float
    net_commission: float
    status: str  # "settled", "reversed", "pending"
    idempotency_hash: str
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))


class CreatorSummary(BaseModel):
    creator_id: str
    total_gmv: float
    settled_commissions: float
    refunded_commissions: float
    order_count: int
    active_tier: str


class CreatorAttributionEngine:
    """Thread-safe attribution engine providing idempotent settlement and reconciliation."""

    def __init__(self, platform_fee_rate: float = 0.015) -> None:
        self._lock = threading.Lock()
        self.platform_fee_rate = platform_fee_rate
        self.events: dict[str, CreatorSaleEvent] = {}
        self.settlements: dict[str, CommissionSettlement] = {}
        self.order_to_settlement: dict[str, str] = {}
        self.dead_letters: list[dict[str, object]] = []

    def _compute_hash(self, event: CreatorSaleEvent) -> str:
        payload = f"{event.order_id}:{event.status.value}:{event.gross_merchandise_value:.2f}:{event.creator_id}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def ingest_sale_event(self, event: CreatorSaleEvent) -> CommissionSettlement:
        with self._lock:
            idem_hash = self._compute_hash(event)
            existing_settlement_id = self.order_to_settlement.get(event.order_id)

            if existing_settlement_id:
                existing = self.settlements[existing_settlement_id]
                # If identical event replayed, return existing settlement (idempotent duplicate protection)
                if existing.idempotency_hash == idem_hash:
                    return existing

                # Handle Refund or Cancellation Reversals
                if (
                    event.status in (OrderStatus.REFUNDED, OrderStatus.CANCELLED)
                    and existing.status == "settled"
                ):
                    existing.status = "reversed"
                    rev_id = f"rev_{event.order_id}_{len(self.settlements) + 1}"
                    reversal = CommissionSettlement(
                        settlement_id=rev_id,
                        order_id=event.order_id,
                        creator_id=event.creator_id,
                        platform=event.platform,
                        gmv=-existing.gmv,
                        effective_rate=existing.effective_rate,
                        base_commission=-existing.base_commission,
                        tier_bonus=-existing.tier_bonus,
                        platform_fee=0.0,
                        net_commission=-existing.net_commission,
                        status="reversed",
                        idempotency_hash=idem_hash,
                    )
                    self.settlements[rev_id] = reversal
                    return reversal

            # Calculate cumulative creator volume for tier bonuses
            prior_gmv = sum(
                s.gmv
                for s in self.settlements.values()
                if s.creator_id == event.creator_id and s.status == "settled"
            )

            # Tier bonus evaluation
            tier_bonus_rate = 0.0
            if prior_gmv >= 50_000.0:
                tier_bonus_rate = 0.04
            elif prior_gmv >= 10_000.0:
                tier_bonus_rate = 0.02

            effective_rate = event.base_commission_rate + tier_bonus_rate
            base_comm = round(
                event.gross_merchandise_value * event.base_commission_rate, 2
            )
            tier_bonus = round(event.gross_merchandise_value * tier_bonus_rate, 2)
            gross_comm = base_comm + tier_bonus
            platform_fee = round(gross_comm * self.platform_fee_rate, 2)
            net_comm = round(gross_comm - platform_fee, 2)

            settlement_id = f"stl_{event.order_id}_{len(self.settlements) + 1}"
            settlement = CommissionSettlement(
                settlement_id=settlement_id,
                order_id=event.order_id,
                creator_id=event.creator_id,
                platform=event.platform,
                gmv=event.gross_merchandise_value,
                effective_rate=round(effective_rate, 4),
                base_commission=base_comm,
                tier_bonus=tier_bonus,
                platform_fee=platform_fee,
                net_commission=net_comm,
                status="settled"
                if event.status == OrderStatus.COMPLETED
                else "pending",
                idempotency_hash=idem_hash,
            )

            self.events[event.event_id] = event
            self.settlements[settlement_id] = settlement
            self.order_to_settlement[event.order_id] = settlement_id
            return settlement

    def get_creator_summary(self, creator_id: str) -> CreatorSummary:
        with self._lock:
            creator_settlements = [
                s for s in self.settlements.values() if s.creator_id == creator_id
            ]
            settled = [s for s in creator_settlements if s.status == "settled"]
            refunded = [s for s in creator_settlements if s.status == "reversed"]

            total_gmv = round(
                sum(s.gmv for s in settled) + sum(s.gmv for s in refunded), 2
            )
            total_net_comm = round(
                sum(s.net_commission for s in settled)
                + sum(s.net_commission for s in refunded),
                2,
            )
            refund_comm = round(
                sum(
                    abs(s.net_commission)
                    for s in creator_settlements
                    if s.status == "reversed" and s.net_commission < 0
                ),
                2,
            )

            if total_gmv >= 50_000.0:
                tier = "Platinum (+4% bonus)"
            elif total_gmv >= 10_000.0:
                tier = "Gold (+2% bonus)"
            else:
                tier = "Standard"

            return CreatorSummary(
                creator_id=creator_id,
                total_gmv=max(0.0, total_gmv),
                settled_commissions=max(0.0, total_net_comm),
                refunded_commissions=refund_comm,
                order_count=len(settled),
                active_tier=tier,
            )

    def route_dead_letter(
        self, event: CreatorSaleEvent, reason: str, attempts: int
    ) -> None:
        with self._lock:
            self.dead_letters.append(
                {
                    "event_id": event.event_id,
                    "order_id": event.order_id,
                    "creator_id": event.creator_id,
                    "reason": reason,
                    "attempts": attempts,
                    "recorded_at": dt.datetime.now(dt.UTC).isoformat(),
                }
            )
