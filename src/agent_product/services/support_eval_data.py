from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_product.db.models import (
    CommerceOrder,
    CommerceOrderItem,
    Customer,
    Inventory,
    PaymentTransaction,
    Product,
    Shipment,
)

EVAL_TENANT = "eval"
ORDER_ALIASES = frozenset({"K", "P", "H", "M", "S", "L", "T", "C", "G", "B", "D1", "D2", "V", "X"})


@dataclass(frozen=True, slots=True)
class EvalData:
    orders: dict[str, str]
    tracking: dict[str, str]
    foreign_values: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class _ItemSeed:
    key: str
    name: str
    price: int
    stock: int = 7
    refund_window_days: int = 30
    final_sale: bool = False


@dataclass(frozen=True, slots=True)
class _OrderSeed:
    alias: str
    created_days: int
    items: tuple[_ItemSeed, ...]
    delivered_days: int | None = None
    status: str = "delivered"
    estimated_delivery_days: int | None = None
    has_shipment: bool = True


_ORDERS = (
    _OrderSeed("K", 12, (_ItemSeed("keyboard", "无线键盘", 299),), delivered_days=8),
    _OrderSeed(
        "P",
        20,
        (_ItemSeed("accessory", "限时促销配件", 89, stock=0, refund_window_days=7),),
        delivered_days=15,
    ),
    _OrderSeed(
        "H",
        1,
        (_ItemSeed("headphones", "降噪耳机", 459, stock=9),),
        status="in_transit",
        estimated_delivery_days=1,
    ),
    _OrderSeed(
        "M",
        6,
        (_ItemSeed("mouse", "无线鼠标", 149),),
        status="in_transit",
        estimated_delivery_days=-2,
    ),
    _OrderSeed(
        "S",
        1,
        (_ItemSeed("stand", "笔记本支架", 99),),
        status="processing",
        has_shipment=False,
    ),
    _OrderSeed(
        "L",
        7,
        (_ItemSeed("lamp", "清仓台灯", 129, final_sale=True),),
        delivered_days=5,
    ),
    _OrderSeed(
        "T",
        10,
        (_ItemSeed("toothbrush", "电动牙刷", 199),),
        estimated_delivery_days=-7,
    ),
    _OrderSeed(
        "C",
        22,
        (_ItemSeed("cup", "保温杯", 79, refund_window_days=15),),
        delivered_days=20,
    ),
    _OrderSeed("G", 8, (_ItemSeed("gamepad", "游戏手柄", 199, stock=0),), delivered_days=6),
    _OrderSeed("B", 45, (_ItemSeed("speaker", "蓝牙音箱", 259),), delivered_days=40),
    _OrderSeed("D1", 9, (_ItemSeed("cable-1m", "Type-C 数据线 1 米", 29),), delivered_days=7),
    _OrderSeed("D2", 6, (_ItemSeed("cable-2m", "Type-C 数据线 2 米", 39),), delivered_days=4),
    _OrderSeed(
        "V",
        9,
        (_ItemSeed("monitor", "显示器", 899), _ItemSeed("hdmi", "HDMI 线", 29)),
        delivered_days=6,
    ),
    _OrderSeed("X", 5, (_ItemSeed("air-fryer", "空气炸锅", 399),), delivered_days=3),
)


async def seed_support_eval_data(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
) -> EvalData:
    """Seed one fresh evaluation database, without touching the demo scenario.

    Call once per newly created database. Reusing a seeded database raises on duplicate
    keys instead of silently reusing old cases or drifting dates between repetitions.
    """
    now = now if now is not None else datetime.now(UTC)
    if now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    customers = {
        suffix: Customer(
            id=f"eval-{suffix}",
            tenant_id=EVAL_TENANT,
            customer_number=f"EVAL-CUSTOMER-{index:04d}",
            display_name=name,
            email_masked=f"{suffix}***@example.invalid",
            phone_masked=phone,
            status="active",
            created_at=now - timedelta(days=365),
        )
        for index, (suffix, name, phone) in enumerate(
            (
                ("a", "林**（评测虚构）", "130****0101"),
                ("b", "周**（评测虚构）", "131****0202"),
                ("c", "陈**（评测虚构）", "132****0303"),
            ),
            start=1,
        )
    }
    orders: dict[str, str] = {}
    tracking: dict[str, str] = {}
    async with session_factory() as session:
        session.add_all(customers.values())
        for index, spec in enumerate(_ORDERS, start=1):
            customer = customers["b" if spec.alias == "X" else "a"]
            ordered_at = now - timedelta(days=spec.created_days)
            order_id = f"eval-order-{spec.alias.lower()}"
            order_number = f"EC{ordered_at:%Y%m%d}{index:04d}"
            total = sum((Decimal(item.price) for item in spec.items), Decimal("0.00"))
            orders[spec.alias] = order_number
            session.add(
                CommerceOrder(
                    id=order_id,
                    tenant_id=EVAL_TENANT,
                    order_number=order_number,
                    customer_id=customer.id,
                    customer_ref=customer.display_name,
                    status=spec.status,
                    sales_channel="web",
                    currency="CNY",
                    total_amount=total,
                    shipping_address_snapshot_json=json.dumps(
                        {
                            "recipient": customer.display_name,
                            "phone": customer.phone_masked,
                            "country": "CN",
                            "region": "评测省",
                            "city": "虚构市",
                            "detail": f"测试街道***号（{customer.id[-1].upper()}）",
                        },
                        ensure_ascii=False,
                    ),
                    created_at=ordered_at,
                    paid_at=ordered_at + timedelta(minutes=5),
                    updated_at=now,
                )
            )
            for line_number, item in enumerate(spec.items, start=1):
                product_id = f"eval-product-{item.key}"
                sku = f"EVAL-{item.key.upper()}"
                session.add_all(
                    (
                        Product(
                            id=product_id,
                            tenant_id=EVAL_TENANT,
                            sku=sku,
                            name=item.name,
                            price=Decimal(item.price),
                            category="evaluation",
                        ),
                        Inventory(
                            id=f"eval-inventory-{item.key}",
                            tenant_id=EVAL_TENANT,
                            product_id=product_id,
                            warehouse_id="wh-eval-east",
                            on_hand=item.stock + 2,
                            reserved=2,
                            updated_at=now,
                        ),
                        CommerceOrderItem(
                            id=f"eval-item-{item.key}",
                            tenant_id=EVAL_TENANT,
                            order_id=order_id,
                            line_number=line_number,
                            product_id=product_id,
                            sku_snapshot=sku,
                            product_name_snapshot=item.name,
                            quantity=1,
                            unit_price=Decimal(item.price),
                            fulfillment_status=spec.status,
                            refund_window_days=item.refund_window_days,
                            final_sale=item.final_sale,
                        ),
                    )
                )
            if spec.has_shipment:
                delivered_at = (
                    now - timedelta(days=spec.delivered_days)
                    if spec.delivered_days is not None
                    else None
                )
                tracking[spec.alias] = f"EVAL-TRACK-{index:04d}"
                session.add(
                    Shipment(
                        id=f"eval-shipment-{spec.alias.lower()}",
                        tenant_id=EVAL_TENANT,
                        order_id=order_id,
                        carrier="评测速递（虚构）",
                        tracking_number=tracking[spec.alias],
                        status=spec.status,
                        shipped_at=ordered_at + timedelta(hours=12),
                        delivered_at=delivered_at,
                        estimated_delivery_at=(
                            now + timedelta(days=spec.estimated_delivery_days)
                            if spec.estimated_delivery_days is not None
                            else delivered_at
                        ),
                    )
                )
            session.add(
                PaymentTransaction(
                    id=f"eval-payment-{spec.alias.lower()}",
                    tenant_id=EVAL_TENANT,
                    order_id=order_id,
                    payment_reference=f"PAY-EVAL-{index:04d}",
                    provider="eval-pay",
                    method_masked="测试卡 •••• 0000",
                    status="captured",
                    amount=total,
                    currency="CNY",
                    paid_at=ordered_at + timedelta(minutes=5),
                )
            )
        foreign_values = (
            "空气炸锅",
            customers["b"].display_name,
            customers["b"].phone_masked,
            tracking["X"],
        )
        await session.commit()
    return EvalData(
        orders=orders,
        tracking=tracking,
        foreign_values=foreign_values,
        created_at=now.isoformat(),
    )
