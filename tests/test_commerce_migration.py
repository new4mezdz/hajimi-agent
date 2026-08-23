import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from agent_product.core.config import Settings
from agent_product.db.base import Base
from agent_product.db.commerce_migrations import migrate_sqlite_commerce_demo_schema
from agent_product.db.session import build_engine


def create_legacy_demo_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE products (
            id VARCHAR(64) PRIMARY KEY,
            tenant_id VARCHAR(100) NOT NULL,
            sku VARCHAR(80) NOT NULL,
            name VARCHAR(200) NOT NULL,
            price NUMERIC(12,2) NOT NULL,
            status VARCHAR(30) NOT NULL
        );
        CREATE TABLE commerce_orders (
            id VARCHAR(64) PRIMARY KEY,
            tenant_id VARCHAR(100) NOT NULL,
            customer_ref VARCHAR(100) NOT NULL,
            status VARCHAR(30) NOT NULL,
            currency VARCHAR(3) NOT NULL,
            total_amount NUMERIC(12,2) NOT NULL,
            created_at DATETIME NOT NULL,
            paid_at DATETIME
        );
        CREATE TABLE commerce_order_items (
            id VARCHAR(64) PRIMARY KEY,
            tenant_id VARCHAR(100) NOT NULL,
            order_id VARCHAR(64) NOT NULL,
            product_id VARCHAR(64) NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price NUMERIC(12,2) NOT NULL,
            fulfillment_status VARCHAR(30) NOT NULL,
            refund_window_days INTEGER NOT NULL,
            final_sale BOOLEAN NOT NULL
        );
        CREATE TABLE after_sales_cases (
            id VARCHAR(64) PRIMARY KEY,
            tenant_id VARCHAR(100) NOT NULL,
            order_id VARCHAR(64) NOT NULL,
            order_item_id VARCHAR(64) NOT NULL,
            issue_type VARCHAR(30) NOT NULL,
            requested_resolution VARCHAR(30) NOT NULL,
            eligibility VARCHAR(30) NOT NULL,
            status VARCHAR(30) NOT NULL,
            reason TEXT NOT NULL,
            eligibility_snapshot_json TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
        INSERT INTO products VALUES (
            'product-demo-keyboard', 'local', 'KEYBOARD-01', '无线键盘', 299, 'active'
        );
        INSERT INTO commerce_orders VALUES (
            'order-demo-1001', 'local', '顾客 A（演示）', 'delivered', 'CNY', 299,
            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
        );
        INSERT INTO commerce_order_items VALUES (
            'item-demo-1001', 'local', 'order-demo-1001', 'product-demo-keyboard',
            1, 299, 'delivered', 30, 0
        );
        """
    )
    connection.commit()
    connection.close()


@pytest.mark.asyncio
async def test_legacy_sqlite_demo_schema_is_upgraded_without_deleting_rows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"
    create_legacy_demo_database(database_path)
    settings = Settings(database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}")
    engine = build_engine(settings)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await migrate_sqlite_commerce_demo_schema(
            engine,
            tenant_id="local",
            customer_id="customer-demo-a",
        )
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT order_number, customer_id, sales_channel "
                        "FROM commerce_orders WHERE id = 'order-demo-1001'"
                    )
                )
            ).one()
            item = (
                await connection.execute(
                    text(
                        "SELECT line_number, sku_snapshot, product_name_snapshot "
                        "FROM commerce_order_items WHERE id = 'item-demo-1001'"
                    )
                )
            ).one()

        assert tuple(row) == ("EC-LEGACY-1001", "customer-demo-a", "web")
        assert tuple(item) == (1, "KEYBOARD-01", "无线键盘")
    finally:
        await engine.dispose()
