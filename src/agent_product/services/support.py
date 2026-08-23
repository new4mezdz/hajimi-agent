from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_product.db.models import (
    AfterSalesCase,
    CommerceOrder,
    CommerceOrderItem,
    Customer,
    Inventory,
    InventoryMovement,
    PaymentTransaction,
    Product,
    Shipment,
)

ISSUE_TYPES = {"damaged", "delivery", "missing", "other"}
RESOLUTIONS = {"refund", "replacement", "manual_review"}


class SupportError(ValueError):
    pass


class SupportOrderNotFoundError(SupportError):
    pass


class SupportCaseNotFoundError(SupportError):
    pass


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.01")))


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


async def seed_support_demo_data(
    session_factory: async_sessionmaker[AsyncSession],
    tenant_id: str,
    customer_id: str = "customer-demo-a",
) -> None:
    """Insert one idempotent relational commerce scenario for a demo tenant."""
    now = datetime.now(UTC)
    async with session_factory() as session:
        existing = await session.scalar(
            select(CommerceOrder.id).where(
                CommerceOrder.id == "order-demo-1001",
                CommerceOrder.tenant_id == tenant_id,
            )
        )
        if existing is not None:
            return

        customer = Customer(
            id=customer_id,
            tenant_id=tenant_id,
            customer_number="CUST-00010001",
            display_name="李晓晴（演示）",
            email_masked="l***@example.com",
            phone_masked="138****5678",
            status="active",
            created_at=now - timedelta(days=400),
        )
        other_customer = Customer(
            id="customer-demo-b",
            tenant_id=tenant_id,
            customer_number="CUST-00010002",
            display_name="王诚（演示）",
            email_masked="w***@example.com",
            phone_masked="139****2468",
            status="active",
            created_at=now - timedelta(days=250),
        )
        products = (
            Product(
                id="product-demo-keyboard",
                tenant_id=tenant_id,
                sku="KEYBOARD-01",
                name="无线键盘",
                price=Decimal("299.00"),
                category="computer-accessories",
                status="active",
            ),
            Product(
                id="product-demo-accessory",
                tenant_id=tenant_id,
                sku="ACCESSORY-02",
                name="限时促销配件",
                price=Decimal("89.00"),
                category="computer-accessories",
                status="active",
            ),
            Product(
                id="product-demo-headphones",
                tenant_id=tenant_id,
                sku="HEADPHONES-03",
                name="降噪耳机",
                price=Decimal("459.00"),
                category="audio",
                status="active",
            ),
        )
        inventory = (
            Inventory(
                id="inventory-demo-keyboard",
                tenant_id=tenant_id,
                product_id="product-demo-keyboard",
                warehouse_id="tokyo-01",
                on_hand=9,
                reserved=2,
                updated_at=now,
            ),
            Inventory(
                id="inventory-demo-accessory",
                tenant_id=tenant_id,
                product_id="product-demo-accessory",
                warehouse_id="tokyo-01",
                on_hand=0,
                reserved=0,
                updated_at=now,
            ),
            Inventory(
                id="inventory-demo-headphones",
                tenant_id=tenant_id,
                product_id="product-demo-headphones",
                warehouse_id="osaka-01",
                on_hand=12,
                reserved=3,
                updated_at=now,
            ),
        )
        orders = (
            CommerceOrder(
                id="order-demo-1001",
                tenant_id=tenant_id,
                order_number=f"EC{(now - timedelta(days=12)):%Y%m%d}0001",
                customer_id=customer.id,
                customer_ref=customer.display_name,
                status="delivered",
                sales_channel="web",
                currency="CNY",
                total_amount=Decimal("299.00"),
                shipping_address_snapshot_json=json.dumps(
                    {
                        "recipient": "李**",
                        "phone": customer.phone_masked,
                        "country": "CN",
                        "region": "东京都",
                        "city": "新宿区",
                        "detail": "西新宿***",
                    },
                    ensure_ascii=False,
                ),
                created_at=now - timedelta(days=12),
                paid_at=now - timedelta(days=12),
                updated_at=now - timedelta(days=8),
            ),
            CommerceOrder(
                id="order-demo-1002",
                tenant_id=tenant_id,
                order_number=f"EC{(now - timedelta(days=20)):%Y%m%d}0002",
                customer_id=customer.id,
                customer_ref=customer.display_name,
                status="delivered",
                sales_channel="mobile",
                currency="CNY",
                total_amount=Decimal("89.00"),
                shipping_address_snapshot_json=json.dumps(
                    {
                        "recipient": "李**",
                        "phone": customer.phone_masked,
                        "country": "CN",
                        "region": "东京都",
                        "city": "新宿区",
                        "detail": "西新宿***",
                    },
                    ensure_ascii=False,
                ),
                created_at=now - timedelta(days=20),
                paid_at=now - timedelta(days=20),
                updated_at=now - timedelta(days=15),
            ),
            CommerceOrder(
                id="order-demo-1003",
                tenant_id=tenant_id,
                order_number=f"EC{(now - timedelta(days=1)):%Y%m%d}0003",
                customer_id=customer.id,
                customer_ref=customer.display_name,
                status="in_transit",
                sales_channel="web",
                currency="CNY",
                total_amount=Decimal("459.00"),
                shipping_address_snapshot_json=json.dumps(
                    {
                        "recipient": "李**",
                        "phone": customer.phone_masked,
                        "country": "CN",
                        "region": "东京都",
                        "city": "新宿区",
                        "detail": "西新宿***",
                    },
                    ensure_ascii=False,
                ),
                created_at=now - timedelta(days=1),
                paid_at=now - timedelta(days=1),
                updated_at=now - timedelta(hours=8),
            ),
            CommerceOrder(
                id="order-demo-2001",
                tenant_id=tenant_id,
                order_number=f"EC{(now - timedelta(days=5)):%Y%m%d}0004",
                customer_id=other_customer.id,
                customer_ref=other_customer.display_name,
                status="delivered",
                sales_channel="web",
                currency="CNY",
                total_amount=Decimal("299.00"),
                shipping_address_snapshot_json=json.dumps(
                    {
                        "recipient": "王**",
                        "phone": other_customer.phone_masked,
                        "country": "CN",
                        "region": "大阪府",
                        "city": "大阪市",
                        "detail": "北区***",
                    },
                    ensure_ascii=False,
                ),
                created_at=now - timedelta(days=5),
                paid_at=now - timedelta(days=5),
                updated_at=now - timedelta(days=2),
            ),
        )
        items = (
            CommerceOrderItem(
                id="item-demo-1001",
                tenant_id=tenant_id,
                order_id="order-demo-1001",
                line_number=1,
                product_id="product-demo-keyboard",
                sku_snapshot="KEYBOARD-01",
                product_name_snapshot="无线键盘",
                quantity=1,
                unit_price=Decimal("299.00"),
                fulfillment_status="delivered",
                refund_window_days=30,
                final_sale=False,
            ),
            CommerceOrderItem(
                id="item-demo-1002",
                tenant_id=tenant_id,
                order_id="order-demo-1002",
                line_number=1,
                product_id="product-demo-accessory",
                sku_snapshot="ACCESSORY-02",
                product_name_snapshot="限时促销配件",
                quantity=1,
                unit_price=Decimal("89.00"),
                fulfillment_status="delivered",
                refund_window_days=7,
                final_sale=False,
            ),
            CommerceOrderItem(
                id="item-demo-1003",
                tenant_id=tenant_id,
                order_id="order-demo-1003",
                line_number=1,
                product_id="product-demo-headphones",
                sku_snapshot="HEADPHONES-03",
                product_name_snapshot="降噪耳机",
                quantity=1,
                unit_price=Decimal("459.00"),
                fulfillment_status="in_transit",
                refund_window_days=30,
                final_sale=False,
            ),
            CommerceOrderItem(
                id="item-demo-2001",
                tenant_id=tenant_id,
                order_id="order-demo-2001",
                line_number=1,
                product_id="product-demo-keyboard",
                sku_snapshot="KEYBOARD-01",
                product_name_snapshot="无线键盘",
                quantity=1,
                unit_price=Decimal("299.00"),
                fulfillment_status="delivered",
                refund_window_days=30,
                final_sale=False,
            ),
        )
        shipments = (
            Shipment(
                id="shipment-demo-1001",
                tenant_id=tenant_id,
                order_id="order-demo-1001",
                carrier="Demo Express",
                tracking_number="DEMO-TRACK-1001",
                status="delivered",
                shipped_at=now - timedelta(days=10),
                delivered_at=now - timedelta(days=8),
                estimated_delivery_at=now - timedelta(days=8),
            ),
            Shipment(
                id="shipment-demo-1002",
                tenant_id=tenant_id,
                order_id="order-demo-1002",
                carrier="Demo Express",
                tracking_number="DEMO-TRACK-1002",
                status="delivered",
                shipped_at=now - timedelta(days=17),
                delivered_at=now - timedelta(days=15),
                estimated_delivery_at=now - timedelta(days=15),
            ),
            Shipment(
                id="shipment-demo-1003",
                tenant_id=tenant_id,
                order_id="order-demo-1003",
                carrier="Demo Express",
                tracking_number="DEMO-TRACK-1003",
                status="in_transit",
                shipped_at=now - timedelta(days=2),
                delivered_at=None,
                estimated_delivery_at=now + timedelta(days=1),
            ),
            Shipment(
                id="shipment-demo-2001",
                tenant_id=tenant_id,
                order_id="order-demo-2001",
                carrier="Demo Express",
                tracking_number="DEMO-TRACK-2001",
                status="delivered",
                shipped_at=now - timedelta(days=4),
                delivered_at=now - timedelta(days=2),
                estimated_delivery_at=now - timedelta(days=2),
            ),
        )
        movements = (
            InventoryMovement(
                id="movement-demo-keyboard-receipt",
                tenant_id=tenant_id,
                product_id="product-demo-keyboard",
                warehouse_id="tokyo-01",
                movement_type="receipt",
                quantity=9,
                reference_type="demo-seed",
                reference_id="support-demo",
                created_at=now - timedelta(days=30),
            ),
            InventoryMovement(
                id="movement-demo-keyboard-reserve",
                tenant_id=tenant_id,
                product_id="product-demo-keyboard",
                warehouse_id="tokyo-01",
                movement_type="reservation",
                quantity=-2,
                reference_type="open-orders",
                reference_id="demo-reservations",
                created_at=now - timedelta(days=1),
            ),
            InventoryMovement(
                id="movement-demo-headphones-receipt",
                tenant_id=tenant_id,
                product_id="product-demo-headphones",
                warehouse_id="osaka-01",
                movement_type="receipt",
                quantity=12,
                reference_type="demo-seed",
                reference_id="support-demo",
                created_at=now - timedelta(days=20),
            ),
        )
        payments = tuple(
            PaymentTransaction(
                id=f"payment-demo-{index}",
                tenant_id=tenant_id,
                order_id=order.id,
                payment_reference=f"PAY-DEMO-{index:04d}",
                provider="demo-pay",
                method_masked="Visa •••• 4242",
                status="captured",
                amount=order.total_amount,
                currency=order.currency,
                paid_at=order.paid_at,
            )
            for index, order in enumerate(orders, start=1)
        )
        session.add_all(
            (
                customer,
                other_customer,
                *products,
                *inventory,
                *orders,
                *items,
                *shipments,
                *movements,
                *payments,
            )
        )
        await session.commit()


class SupportService:
    """Tenant-scoped commerce queries and deterministic after-sales decisions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def find_orders(
        self,
        tenant_id: str,
        customer_id: str,
        *,
        days: int = 30,
        product_hint: str | None = None,
        status_hint: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if days < 1 or days > 365:
            raise SupportError("days must be between 1 and 365")
        if limit < 1 or limit > 20:
            raise SupportError("limit must be between 1 and 20")
        product_hint = product_hint.strip() if product_hint else None
        status_hint = status_hint.casefold().strip() if status_hint else None
        cutoff = datetime.now(UTC) - timedelta(days=days)
        statement = (
            select(CommerceOrder)
            .where(
                CommerceOrder.tenant_id == tenant_id,
                CommerceOrder.customer_id == customer_id,
                CommerceOrder.created_at >= cutoff,
            )
            .order_by(CommerceOrder.created_at.desc())
            .limit(limit)
        )
        if status_hint:
            statement = statement.where(CommerceOrder.status == status_hint)
        if product_hint:
            statement = statement.join(
                CommerceOrderItem,
                CommerceOrderItem.order_id == CommerceOrder.id,
            ).where(
                CommerceOrderItem.tenant_id == tenant_id,
                CommerceOrderItem.product_name_snapshot.contains(product_hint),
            ).distinct()
        async with self.session_factory() as session:
            orders = list(await session.scalars(statement))
        results: list[dict[str, Any]] = []
        for order in orders:
            detail = await self.lookup_order(tenant_id, customer_id, order.order_number)
            results.append(
                {
                    "order_number": detail["order_number"],
                    "created_at": detail["created_at"],
                    "status": detail["status"],
                    "total_amount": detail["total_amount"],
                    "currency": detail["currency"],
                    "items": [
                        {
                            "line_number": item["line_number"],
                            "name": item["name"],
                            "quantity": item["quantity"],
                        }
                        for item in detail["items"]
                    ],
                    "shipment": detail["shipments"][0] if detail["shipments"] else None,
                }
            )
        return results

    async def _load_order_graph(
        self,
        tenant_id: str,
        customer_id: str,
        order_number: str,
    ) -> tuple[
        CommerceOrder,
        list[CommerceOrderItem],
        dict[str, Product],
        list[Shipment],
        dict[str, list[Inventory]],
        list[PaymentTransaction],
    ]:
        async with self.session_factory() as session:
            order = await session.scalar(
                select(CommerceOrder).where(
                    CommerceOrder.order_number == order_number,
                    CommerceOrder.tenant_id == tenant_id,
                    CommerceOrder.customer_id == customer_id,
                )
            )
            if order is None:
                raise SupportOrderNotFoundError(
                    f"Order number {order_number!r} was not found for the current customer"
                )
            items = list(
                await session.scalars(
                    select(CommerceOrderItem).where(
                        CommerceOrderItem.order_id == order.id,
                        CommerceOrderItem.tenant_id == tenant_id,
                    )
                )
            )
            product_ids = {item.product_id for item in items}
            products = {
                product.id: product
                for product in await session.scalars(
                    select(Product).where(
                        Product.tenant_id == tenant_id,
                        Product.id.in_(product_ids),
                    )
                )
            }
            shipments = list(
                await session.scalars(
                    select(Shipment).where(
                        Shipment.order_id == order.id,
                        Shipment.tenant_id == tenant_id,
                    )
                )
            )
            inventories: dict[str, list[Inventory]] = {}
            for row in await session.scalars(
                select(Inventory).where(
                    Inventory.tenant_id == tenant_id,
                    Inventory.product_id.in_(product_ids),
                )
            ):
                inventories.setdefault(row.product_id, []).append(row)
            payments = list(
                await session.scalars(
                    select(PaymentTransaction).where(
                        PaymentTransaction.order_id == order.id,
                        PaymentTransaction.tenant_id == tenant_id,
                    )
                )
            )
        return order, items, products, shipments, inventories, payments

    async def lookup_order(
        self,
        tenant_id: str,
        customer_id: str,
        order_number: str,
    ) -> dict[str, Any]:
        order, items, products, shipments, inventories, payments = (
            await self._load_order_graph(
            tenant_id,
                customer_id,
                order_number.strip(),
            )
        )
        return {
            "order_number": order.order_number,
            "customer_id": order.customer_id,
            "customer": order.customer_ref,
            "status": order.status,
            "sales_channel": order.sales_channel,
            "currency": order.currency,
            "total_amount": _money(order.total_amount),
            "shipping_address": json.loads(order.shipping_address_snapshot_json),
            "created_at": _iso(order.created_at),
            "paid_at": _iso(order.paid_at),
            "items": [
                {
                    "line_number": item.line_number,
                    "product_id": item.product_id,
                    "sku": item.sku_snapshot,
                    "name": item.product_name_snapshot,
                    "quantity": item.quantity,
                    "unit_price": _money(item.unit_price),
                    "fulfillment_status": item.fulfillment_status,
                    "refund_window_days": item.refund_window_days,
                    "final_sale": item.final_sale,
                    "inventory": [
                        {
                            "warehouse_id": stock.warehouse_id,
                            "on_hand": stock.on_hand,
                            "reserved": stock.reserved,
                            "available": stock.on_hand - stock.reserved,
                        }
                        for stock in inventories.get(item.product_id, ())
                    ],
                }
                for item in items
            ],
            "shipments": [
                {
                    "shipment_id": shipment.id,
                    "carrier": shipment.carrier,
                    "tracking_number": shipment.tracking_number,
                    "status": shipment.status,
                    "shipped_at": _iso(shipment.shipped_at),
                    "delivered_at": _iso(shipment.delivered_at),
                    "estimated_delivery_at": _iso(shipment.estimated_delivery_at),
                }
                for shipment in shipments
            ],
            "payments": [
                {
                    "payment_reference": payment.payment_reference,
                    "provider": payment.provider,
                    "method": payment.method_masked,
                    "status": payment.status,
                    "amount": _money(payment.amount),
                    "currency": payment.currency,
                    "paid_at": _iso(payment.paid_at),
                }
                for payment in payments
            ],
        }

    async def assess_after_sales_options(
        self,
        tenant_id: str,
        customer_id: str,
        order_number: str,
        line_number: int,
        issue_type: str,
    ) -> dict[str, Any]:
        issue_type = issue_type.casefold().strip()
        if issue_type not in ISSUE_TYPES:
            raise SupportError(f"issue_type must be one of {', '.join(sorted(ISSUE_TYPES))}")
        order, items, products, shipments, inventories, _payments = (
            await self._load_order_graph(
                tenant_id,
                customer_id,
                order_number.strip(),
            )
        )
        item = next(
            (candidate for candidate in items if candidate.line_number == line_number),
            None,
        )
        if item is None:
            raise SupportError(
                f"Line {line_number} does not belong to order {order.order_number!r}"
            )
        product = products[item.product_id]
        shipment = shipments[0] if shipments else None
        delivered_at = shipment.delivered_at if shipment else None
        if delivered_at is not None and delivered_at.tzinfo is None:
            delivered_at = delivered_at.replace(tzinfo=UTC)
        delivered_days_ago = (
            max(0, (datetime.now(UTC) - delivered_at).days)
            if delivered_at is not None
            else None
        )

        refund_reasons: list[str] = []
        if order.status != "delivered" or item.fulfillment_status != "delivered":
            refund_reasons.append("order-not-delivered")
        if item.final_sale:
            refund_reasons.append("final-sale")
        if delivered_days_ago is None:
            refund_reasons.append("delivery-date-unavailable")
        elif delivered_days_ago > item.refund_window_days:
            refund_reasons.append("refund-window-expired")
        refund_available = not refund_reasons

        stock_rows = inventories.get(item.product_id, [])
        available_stock = sum(row.on_hand - row.reserved for row in stock_rows)
        replacement_reasons: list[str] = []
        if order.status != "delivered" or item.fulfillment_status != "delivered":
            replacement_reasons.append("order-not-delivered")
        if available_stock < item.quantity:
            replacement_reasons.append("replacement-out-of-stock")
        replacement_available = not replacement_reasons

        return {
            "order_number": order.order_number,
            "line_number": item.line_number,
            "product": {"id": product.id, "sku": product.sku, "name": product.name},
            "issue_type": issue_type,
            "refund": {
                "available": refund_available,
                "maximum_amount": (
                    _money(item.unit_price * item.quantity) if refund_available else 0.0
                ),
                "currency": order.currency,
                "reasons": refund_reasons,
                "delivered_days_ago": delivered_days_ago,
                "refund_window_days": item.refund_window_days,
            },
            "replacement": {
                "available": replacement_available,
                "available_stock": available_stock,
                "warehouses": [
                    {
                        "warehouse_id": row.warehouse_id,
                        "available": row.on_hand - row.reserved,
                    }
                    for row in stock_rows
                ],
                "reasons": replacement_reasons,
            },
            "manual_review": {
                "required": not refund_available and not replacement_available,
            },
            "shipment": (
                {
                    "status": shipment.status,
                    "tracking_number": shipment.tracking_number,
                    "estimated_delivery_at": _iso(shipment.estimated_delivery_at),
                }
                if shipment
                else None
            ),
        }

    async def create_case(
        self,
        tenant_id: str,
        customer_id: str,
        order_number: str,
        line_number: int,
        issue_type: str,
        requested_resolution: str,
        summary: str,
    ) -> dict[str, Any]:
        order_number = order_number.strip()
        issue_type = issue_type.casefold().strip()
        requested_resolution = requested_resolution.casefold().strip()
        summary = summary.strip()
        if line_number < 1:
            raise SupportError("line_number must be positive")
        if requested_resolution not in RESOLUTIONS:
            raise SupportError(
                f"requested_resolution must be one of {', '.join(sorted(RESOLUTIONS))}"
            )
        if len(summary) < 10 or len(summary) > 1_000:
            raise SupportError("Support case summary must contain 10 to 1,000 characters")
        options = await self.assess_after_sales_options(
            tenant_id,
            customer_id,
            order_number,
            line_number,
            issue_type,
        )
        if requested_resolution == "refund" and not options["refund"]["available"]:
            raise SupportError("Refund is not automatically available; request manual_review")
        if (
            requested_resolution == "replacement"
            and not options["replacement"]["available"]
        ):
            raise SupportError(
                "Replacement is not automatically available; request manual_review"
            )
        eligibility = (
            "manual_review" if requested_resolution == "manual_review" else "automatic"
        )
        order, items, _products, _shipments, _inventories, _payments = (
            await self._load_order_graph(tenant_id, customer_id, order_number)
        )
        item = next(
            (candidate for candidate in items if candidate.line_number == line_number),
            None,
        )
        if item is None:
            raise SupportError(
                f"Line {line_number} does not belong to order {order.order_number!r}"
            )
        case = AfterSalesCase(
            id=f"case-{uuid4()}",
            tenant_id=tenant_id,
            case_number=f"AS{datetime.now(UTC):%Y%m%d}{uuid4().hex[:6].upper()}",
            customer_id=customer_id,
            order_id=order.id,
            order_item_id=item.id,
            issue_type=issue_type,
            requested_resolution=requested_resolution,
            eligibility=eligibility,
            status="open",
            reason=summary,
            eligibility_snapshot_json=json.dumps(options, ensure_ascii=False),
        )
        async with self.session_factory() as session:
            session.add(case)
            await session.commit()
            await session.refresh(case)
        return self._case_dict(case)

    @staticmethod
    def _case_dict(case: AfterSalesCase) -> dict[str, Any]:
        snapshot = json.loads(case.eligibility_snapshot_json)
        return {
            "case_number": case.case_number,
            "tenant_id": case.tenant_id,
            "customer_id": case.customer_id,
            "order_number": snapshot["order_number"],
            "line_number": snapshot["line_number"],
            "issue_type": case.issue_type,
            "requested_resolution": case.requested_resolution,
            "eligibility": case.eligibility,
            "status": case.status,
            "reason": case.reason,
            "eligibility_snapshot": snapshot,
            "created_at": _iso(case.created_at),
            "updated_at": _iso(case.updated_at),
        }

    async def list_cases(
        self,
        tenant_id: str,
        customer_id: str,
    ) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            rows = list(
                await session.scalars(
                    select(AfterSalesCase)
                    .where(
                        AfterSalesCase.tenant_id == tenant_id,
                        AfterSalesCase.customer_id == customer_id,
                    )
                    .order_by(AfterSalesCase.created_at.desc())
                )
            )
        return [self._case_dict(case) for case in rows]

    async def get_case(
        self,
        tenant_id: str,
        customer_id: str,
        case_number: str,
    ) -> dict[str, Any]:
        async with self.session_factory() as session:
            case = await session.scalar(
                select(AfterSalesCase).where(
                    AfterSalesCase.case_number == case_number,
                    AfterSalesCase.tenant_id == tenant_id,
                    AfterSalesCase.customer_id == customer_id,
                )
            )
        if case is None:
            raise SupportCaseNotFoundError(
                f"Support case {case_number!r} was not found for the current customer"
            )
        return self._case_dict(case)

    async def list_inventory(self, tenant_id: str) -> list[dict[str, Any]]:
        async with self.session_factory() as session:
            rows = list(
                await session.execute(
                    select(Inventory, Product)
                    .join(Product, Product.id == Inventory.product_id)
                    .where(
                        Inventory.tenant_id == tenant_id,
                        Product.tenant_id == tenant_id,
                    )
                    .order_by(Product.sku, Inventory.warehouse_id)
                )
            )
        return [
            {
                "product_id": product.id,
                "sku": product.sku,
                "name": product.name,
                "warehouse_id": stock.warehouse_id,
                "on_hand": stock.on_hand,
                "reserved": stock.reserved,
                "available": stock.on_hand - stock.reserved,
                "updated_at": _iso(stock.updated_at),
            }
            for stock, product in rows
        ]
