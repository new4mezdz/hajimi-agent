from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_product.db.base import Base
from agent_product.db.models import CommerceOrder, CommerceOrderItem, Product, Shipment
from agent_product.services.support import ORDER_SEARCH_STATUSES, SupportError, SupportService


@pytest_asyncio.fixture
async def search_service(tmp_path: Path) -> AsyncIterator[SupportService]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'search.db').as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    # The cancelled row distinguishes the aggregate from status != delivered.
    rows = (
        ("processing", "tenant-a", "customer-a", "processing", 1, "笔记本支架"),
        ("transit", "tenant-a", "customer-a", "in_transit", 2, "降噪耳机"),
        ("delivered", "tenant-a", "customer-a", "delivered", 3, "无线键盘"),
        ("cancelled", "tenant-a", "customer-a", "cancelled", 0, "已取消商品"),
        ("old-transit", "tenant-a", "customer-a", "in_transit", 45, "旧款降噪耳机"),
        ("other-customer", "tenant-a", "customer-b", "processing", 0, "其他客户耳机"),
        ("other-tenant", "tenant-b", "customer-a", "in_transit", 0, "其他租户耳机"),
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as session:
            for index, (key, tenant, customer, status, age, name) in enumerate(rows, 1):
                product_id = f"product-{key}"
                order_id = f"order-{key}"
                session.add_all(
                    [
                        Product(
                            id=product_id,
                            tenant_id=tenant,
                            sku=f"SKU-{index}",
                            name=name,
                            price=Decimal("99.00"),
                        ),
                        CommerceOrder(
                            id=order_id,
                            tenant_id=tenant,
                            order_number=f"EC20261001{index:04d}",
                            customer_id=customer,
                            customer_ref="测试客户",
                            status=status,
                            currency="CNY",
                            total_amount=Decimal("99.00"),
                            shipping_address_snapshot_json="{}",
                            created_at=now - timedelta(days=age),
                        ),
                        CommerceOrderItem(
                            id=f"item-{key}",
                            tenant_id=tenant,
                            order_id=order_id,
                            line_number=1,
                            product_id=product_id,
                            sku_snapshot=f"SKU-{index}",
                            product_name_snapshot=name,
                            quantity=1,
                            unit_price=Decimal("99.00"),
                            fulfillment_status=status,
                            refund_window_days=30,
                        ),
                    ]
                )
                if status in {"in_transit", "delivered"}:
                    session.add(
                        Shipment(
                            id=f"shipment-{key}",
                            tenant_id=tenant,
                            order_id=order_id,
                            carrier="测试承运商",
                            tracking_number=f"SEARCH-TRACK-{index:04d}",
                            status=status,
                            # Recent delivery dates must not make a 45-day-old order recent.
                            shipped_at=now - timedelta(hours=12),
                            delivered_at=now - timedelta(hours=1)
                            if status == "delivered"
                            else None,
                        )
                    )
            await session.commit()
        yield SupportService(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_not_delivered_includes_processing_and_transit_only(search_service) -> None:
    orders = await search_service.find_orders(
        "tenant-a",
        "customer-a",
        status_hint="not_delivered",
        limit=20,
    )

    assert [(row["order_number"], row["status"]) for row in orders] == [
        ("EC202610010001", "processing"),
        ("EC202610010002", "in_transit"),
    ]
    assert orders[0]["shipment"] is None
    assert orders[1]["shipment"]["tracking_number"] == "SEARCH-TRACK-0002"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("processing", "EC202610010001"),
        ("in_transit", "EC202610010002"),
        ("delivered", "EC202610010003"),
    ],
)
async def test_exact_status_filters_remain_exact(search_service, status, expected) -> None:
    orders = await search_service.find_orders("tenant-a", "customer-a", status_hint=status)
    assert [row["order_number"] for row in orders] == [expected]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [" NOT_DELIVERED ", "In_Transit", " DELIVERED "])
async def test_service_normalizes_documented_status_values(search_service, status) -> None:
    expected = await search_service.find_orders(
        "tenant-a",
        "customer-a",
        status_hint=status.strip().lower(),
    )
    assert (
        await search_service.find_orders(
            "tenant-a",
            "customer-a",
            status_hint=status,
        )
        == expected
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "", "   "])
async def test_missing_or_blank_filter_preserves_all_recent_statuses(
    search_service, status
) -> None:
    orders = await search_service.find_orders("tenant-a", "customer-a", status_hint=status)
    assert [row["status"] for row in orders] == [
        "cancelled",
        "processing",
        "in_transit",
        "delivered",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["未送达", "未发货", "pending", "cancelled", "in-transit"])
async def test_unsupported_status_fails_instead_of_silently_returning_empty(
    search_service,
    status,
) -> None:
    with pytest.raises(SupportError, match="status_hint must be one of") as error:
        await search_service.find_orders("tenant-a", "customer-a", status_hint=status)
    assert all(option in str(error.value) for option in ORDER_SEARCH_STATUSES)


@pytest.mark.asyncio
async def test_aggregate_respects_customer_tenant_and_order_date(search_service) -> None:
    own = await search_service.find_orders(
        "tenant-a",
        "customer-a",
        days=60,
        status_hint="not_delivered",
        limit=20,
    )
    assert [row["order_number"] for row in own] == [
        "EC202610010001",
        "EC202610010002",
        "EC202610010005",
    ]
    other_customer = await search_service.find_orders(
        "tenant-a",
        "customer-b",
        status_hint="not_delivered",
    )
    assert [row["order_number"] for row in other_customer] == ["EC202610010006"]
    other_tenant = await search_service.find_orders(
        "tenant-b",
        "customer-a",
        status_hint="not_delivered",
    )
    assert [row["order_number"] for row in other_tenant] == ["EC202610010007"]
    assert (
        await search_service.find_orders(
            "tenant-b",
            "customer-b",
            status_hint="not_delivered",
        )
        == []
    )


@pytest.mark.asyncio
async def test_status_aggregate_composes_with_literal_product_filter_and_limit(
    search_service,
) -> None:
    # Product lookup remains literal: the service must not silently substitute a product.
    assert (
        await search_service.find_orders(
            "tenant-a",
            "customer-a",
            status_hint="not_delivered",
            product_hint="蓝牙耳机",
        )
        == []
    )
    broader = await search_service.find_orders(
        "tenant-a",
        "customer-a",
        days=60,
        status_hint="not_delivered",
        product_hint=" 耳机 ",
        limit=1,
    )
    assert [row["order_number"] for row in broader] == ["EC202610010002"]


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [{"days": 0}, {"days": 366}, {"limit": 0}, {"limit": 21}])
async def test_aggregate_keeps_existing_bounds(search_service, overrides) -> None:
    with pytest.raises(SupportError):
        await search_service.find_orders(
            "tenant-a",
            "customer-a",
            status_hint="not_delivered",
            **overrides,
        )
