from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from agent_product.db.base import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_tenant_updated", "tenant_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    history_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ConversationProfile(Base):
    """Immutable Agent composition selected when a conversation is created.

    This is a separate table so existing SQLite installations can adopt Profile
    binding through ``create_all`` without altering the legacy conversations table.
    """

    __tablename__ = "conversation_profiles"
    __table_args__ = (
        Index("ix_conversation_profiles_tenant", "tenant_id", "conversation_id"),
    )

    conversation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ConversationEvent(Base):
    """Append-only execution fact used for audit, replay work and evaluations."""

    __tablename__ = "conversation_events"
    __table_args__ = (
        Index("ix_conversation_events_stream", "tenant_id", "conversation_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "customer_number",
            name="uq_customers_tenant_number",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    customer_number: Mapped[str] = mapped_column(String(40), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email_masked: Mapped[str] = mapped_column(String(160), nullable=False)
    phone_masked: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "sku", name="uq_products_tenant_sku"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="general")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")


class Inventory(Base):
    __tablename__ = "inventory"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "product_id",
            "warehouse_id",
            name="uq_inventory_tenant_product_warehouse",
        ),
        CheckConstraint("on_hand >= 0", name="ck_inventory_on_hand_nonnegative"),
        CheckConstraint("reserved >= 0", name="ck_inventory_reserved_nonnegative"),
        CheckConstraint("reserved <= on_hand", name="ck_inventory_reserved_within_stock"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False)
    on_hand: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class CommerceOrder(Base):
    __tablename__ = "commerce_orders"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "order_number",
            name="uq_commerce_orders_tenant_number",
        ),
        Index("ix_commerce_orders_tenant_customer", "tenant_id", "customer_id"),
        Index("ix_commerce_orders_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    order_number: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    customer_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    sales_channel: Mapped[str] = mapped_column(String(30), nullable=False, default="web")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shipping_address_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class CommerceOrderItem(Base):
    __tablename__ = "commerce_order_items"
    __table_args__ = (Index("ix_order_items_tenant_order", "tenant_id", "order_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    sku_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    product_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    fulfillment_status: Mapped[str] = mapped_column(String(30), nullable=False)
    refund_window_days: Mapped[int] = mapped_column(Integer, nullable=False)
    final_sale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Shipment(Base):
    __tablename__ = "shipments"
    __table_args__ = (Index("ix_shipments_tenant_order", "tenant_id", "order_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    carrier: Mapped[str] = mapped_column(String(80), nullable=False)
    tracking_number: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    shipped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estimated_delivery_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        Index("ix_inventory_movements_tenant_product", "tenant_id", "product_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    product_id: Mapped[str] = mapped_column(String(64), nullable=False)
    warehouse_id: Mapped[str] = mapped_column(String(64), nullable=False)
    movement_type: Mapped[str] = mapped_column(String(30), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_type: Mapped[str] = mapped_column(String(40), nullable=False)
    reference_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        Index("ix_payments_tenant_order", "tenant_id", "order_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payment_reference: Mapped[str] = mapped_column(String(80), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    method_masked: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AfterSalesCase(Base):
    __tablename__ = "after_sales_cases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "case_number",
            name="uq_after_sales_cases_tenant_number",
        ),
        Index("ix_after_sales_cases_tenant_created", "tenant_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    case_number: Mapped[str] = mapped_column(String(40), nullable=False)
    customer_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    order_item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(30), nullable=False)
    requested_resolution: Mapped[str] = mapped_column(String(30), nullable=False)
    eligibility: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    eligibility_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
