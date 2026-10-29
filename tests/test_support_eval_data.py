from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agent_product.db.base import Base
from agent_product.db.models import CommerceOrder, CommerceOrderItem, Customer, PaymentTransaction
from agent_product.services.support import SupportOrderNotFoundError, SupportService
from agent_product.services.support_eval_data import (
    EVAL_TENANT,
    ORDER_ALIASES,
    EvalData,
    seed_support_eval_data,
)


@pytest_asyncio.fixture
async def scenario(
    tmp_path: Path,
) -> AsyncIterator[tuple[SupportService, EvalData, async_sessionmaker[AsyncSession], datetime]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'eval.db').as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        data = await seed_support_eval_data(factory, now=now)
        yield SupportService(factory), data, factory, now
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("alias", "line", "refund_reasons", "replacement_reasons", "maximum_amount", "stock"),
    [
        ("K", 1, [], [], 299, 7),
        ("P", 1, ["refund-window-expired"], ["replacement-out-of-stock"], 0, 0),
        (
            "H",
            1,
            ["order-not-delivered", "delivery-date-unavailable"],
            ["order-not-delivered"],
            0,
            9,
        ),
        (
            "M",
            1,
            ["order-not-delivered", "delivery-date-unavailable"],
            ["order-not-delivered"],
            0,
            7,
        ),
        (
            "S",
            1,
            ["order-not-delivered", "delivery-date-unavailable"],
            ["order-not-delivered"],
            0,
            7,
        ),
        ("L", 1, ["final-sale"], [], 0, 7),
        ("T", 1, ["delivery-date-unavailable"], [], 0, 7),
        ("C", 1, ["refund-window-expired"], [], 0, 7),
        ("G", 1, [], ["replacement-out-of-stock"], 199, 0),
        ("B", 1, ["refund-window-expired"], [], 0, 7),
        ("D1", 1, [], [], 29, 7),
        ("D2", 1, [], [], 39, 7),
        ("V", 1, [], [], 899, 7),
        ("V", 2, [], [], 29, 7),
        ("X", 1, [], [], 399, 7),
    ],
)
async def test_every_order_matches_planned_after_sales_outcome(
    scenario,
    alias,
    line,
    refund_reasons,
    replacement_reasons,
    maximum_amount,
    stock,
) -> None:
    service, data, _, _ = scenario
    customer = "eval-b" if alias == "X" else "eval-a"

    result = await service.assess_after_sales_options(
        EVAL_TENANT, customer, data.orders[alias], line, "damaged"
    )

    assert result["refund"]["reasons"] == refund_reasons
    assert result["refund"]["available"] is (not refund_reasons)
    assert result["refund"]["maximum_amount"] == maximum_amount
    assert result["replacement"]["reasons"] == replacement_reasons
    assert result["replacement"]["available"] is (not replacement_reasons)
    assert result["replacement"]["available_stock"] == stock
    assert result["manual_review"]["required"] is bool(refund_reasons and replacement_reasons)


@pytest.mark.asyncio
async def test_fixture_contains_complete_relational_data(scenario) -> None:
    service, data, factory, now = scenario
    assert set(data.orders) == ORDER_ALIASES
    assert set(data.tracking) == ORDER_ALIASES - {"S"}
    assert data.created_at == now.isoformat()
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Customer)) == 3
        assert await session.scalar(select(func.count()).select_from(CommerceOrder)) == 14
        assert await session.scalar(select(func.count()).select_from(CommerceOrderItem)) == 15
        assert await session.scalar(select(func.count()).select_from(PaymentTransaction)) == 14
    for alias, order_number in data.orders.items():
        customer = "eval-b" if alias == "X" else "eval-a"
        order = await service.lookup_order(EVAL_TENANT, customer, order_number)
        assert all(item["quantity"] == 1 for item in order["items"])
        assert (
            sum(item["unit_price"] * item["quantity"] for item in order["items"])
            == (order["total_amount"])
        )
        assert len(order["payments"]) == 1
        assert order["payments"][0]["amount"] == order["total_amount"]
        assert "••••" in order["payments"][0]["method"]
        assert "****" in order["shipping_address"]["phone"]
        assert all(item["product_id"].startswith("eval-product-") for item in order["items"])
        assert all(item["inventory"] for item in order["items"])
        if alias != "S":
            assert len(order["shipments"]) == 1
            shipment = order["shipments"][0]
            assert shipment["tracking_number"] == data.tracking[alias]
            assert datetime.fromisoformat(shipment["shipped_at"]) > datetime.fromisoformat(
                order["created_at"]
            )
    multi = await service.lookup_order(EVAL_TENANT, "eval-a", data.orders["V"])
    assert multi["total_amount"] == 928
    assert {(item["line_number"], item["name"]) for item in multi["items"]} == {
        (1, "显示器"),
        (2, "HDMI 线"),
    }


@pytest.mark.asyncio
async def test_missing_shipment_missing_delivery_date_and_eta_are_distinct(scenario) -> None:
    service, data, _, now = scenario
    stand = await service.lookup_order(EVAL_TENANT, "eval-a", data.orders["S"])
    assert stand["status"] == "processing"
    assert stand["shipments"] == []
    found = await service.find_orders(EVAL_TENANT, "eval-a", product_hint="支架")
    assert found[0]["shipment"] is None
    toothbrush = await service.lookup_order(EVAL_TENANT, "eval-a", data.orders["T"])
    assert toothbrush["shipments"][0]["status"] == "delivered"
    assert toothbrush["shipments"][0]["delivered_at"] is None
    for alias, offset in (("H", 1), ("M", -2)):
        order = await service.lookup_order(EVAL_TENANT, "eval-a", data.orders[alias])
        assert datetime.fromisoformat(order["shipments"][0]["estimated_delivery_at"]) == (
            now + timedelta(days=offset)
        )


@pytest.mark.asyncio
async def test_customer_and_tenant_boundaries_and_foreign_values(scenario) -> None:
    service, data, _, _ = scenario
    main = await service.find_orders(EVAL_TENANT, "eval-a", days=365, limit=20)
    other = await service.find_orders(EVAL_TENANT, "eval-b", days=365, limit=20)
    assert len(main) == 13
    assert [order["order_number"] for order in other] == [data.orders["X"]]
    assert await service.find_orders(EVAL_TENANT, "eval-c", days=365) == []
    assert await service.find_orders("different-tenant", "eval-a", days=365) == []
    for customer, tenant in (("eval-a", EVAL_TENANT), ("eval-b", "different-tenant")):
        with pytest.raises(SupportOrderNotFoundError):
            await service.lookup_order(tenant, customer, data.orders["X"])
    foreign = await service.lookup_order(EVAL_TENANT, "eval-b", data.orders["X"])
    assert set(data.foreign_values) == {
        foreign["items"][0]["name"],
        foreign["customer"],
        foreign["shipping_address"]["phone"],
        foreign["shipments"][0]["tracking_number"],
    }
    assert all(value not in str(main) for value in data.foreign_values)


@pytest.mark.asyncio
async def test_order_search_preserves_ambiguity_and_time_range(scenario) -> None:
    service, data, _, _ = scenario
    keywords = {
        "键盘": "K",
        "耳机": "H",
        "鼠标": "M",
        "支架": "S",
        "台灯": "L",
        "牙刷": "T",
        "保温杯": "C",
        "手柄": "G",
        "音箱": "B",
        "显示器": "V",
        "HDMI": "V",
    }
    for keyword, alias in keywords.items():
        orders = await service.find_orders(
            EVAL_TENANT, "eval-a", days=365, product_hint=keyword, limit=20
        )
        assert [order["order_number"] for order in orders] == [data.orders[alias]]
    cables = await service.find_orders(EVAL_TENANT, "eval-a", product_hint="数据线")
    assert [order["order_number"] for order in cables] == [data.orders["D2"], data.orders["D1"]]
    assert await service.find_orders(EVAL_TENANT, "eval-a", product_hint="蓝牙耳机") == []
    assert await service.find_orders(EVAL_TENANT, "eval-a", product_hint="音箱") == []
    default_range = await service.find_orders(EVAL_TENANT, "eval-a", limit=20)
    assert {order["order_number"] for order in default_range} == {
        number for alias, number in data.orders.items() if alias not in {"B", "X"}
    }


@pytest.mark.asyncio
async def test_separate_evaluation_databases_do_not_share_cases(scenario, tmp_path) -> None:
    service, data, _, now = scenario
    await service.create_case(
        EVAL_TENANT,
        "eval-a",
        data.orders["K"],
        1,
        "damaged",
        "refund",
        "评测客户反馈键盘按键失灵，申请退货退款。",
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'second.db').as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        second = await seed_support_eval_data(factory, now=now)
        assert second.orders == data.orders
        assert await SupportService(factory).list_cases(EVAL_TENANT, "eval-a") == []
        assert len(await service.list_cases(EVAL_TENANT, "eval-a")) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_seed_requires_timezone_and_normalizes_to_utc(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'dates.db').as_posix()}")
    # Returned fixture metadata must also work with SQLAlchemy's expiration default.
    factory = async_sessionmaker(engine)
    try:
        with pytest.raises(ValueError, match="timezone-aware"):
            await seed_support_eval_data(factory, now=datetime(2026, 10, 29))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        now = datetime(2026, 10, 29, 2, tzinfo=timezone(timedelta(hours=8)))
        data = await seed_support_eval_data(factory, now=now)
        assert data.created_at == "2026-10-28T18:00:00+00:00"
        assert data.orders["H"].startswith("EC20261027")
    finally:
        await engine.dispose()
