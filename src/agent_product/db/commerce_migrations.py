from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncEngine


def _column_names(connection, table: str) -> set[str]:
    return {column["name"] for column in inspect(connection).get_columns(table)}


async def migrate_sqlite_commerce_demo_schema(
    engine: AsyncEngine,
    *,
    tenant_id: str,
    customer_id: str,
) -> None:
    """Upgrade the short-lived pre-relational demo schema without deleting local data.

    Production deployments should use Alembic; this compatibility path exists for
    desktop-development databases created by earlier project revisions.
    """
    if engine.dialect.name != "sqlite":
        return
    async with engine.begin() as connection:
        tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
        if "commerce_orders" not in tables:
            return

        migrations = {
            "products": {
                "category": "VARCHAR(80) NOT NULL DEFAULT 'general'",
            },
            "commerce_orders": {
                "order_number": "VARCHAR(40)",
                "customer_id": "VARCHAR(64)",
                "sales_channel": "VARCHAR(30) NOT NULL DEFAULT 'web'",
                "shipping_address_snapshot_json": "TEXT NOT NULL DEFAULT '{}'",
                "updated_at": "DATETIME",
            },
            "commerce_order_items": {
                "line_number": "INTEGER",
                "sku_snapshot": "VARCHAR(80)",
                "product_name_snapshot": "VARCHAR(200)",
            },
            "after_sales_cases": {
                "case_number": "VARCHAR(40)",
                "customer_id": "VARCHAR(64)",
            },
        }
        for table, columns in migrations.items():
            if table not in tables:
                continue
            existing = await connection.run_sync(lambda sync, name=table: _column_names(sync, name))
            for column, declaration in columns.items():
                if column not in existing:
                    await connection.execute(
                        text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}')
                    )

        demo_exists = await connection.scalar(
            text(
                "SELECT 1 FROM commerce_orders "
                "WHERE id = 'order-demo-1001' AND tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
        if not demo_exists:
            return

        await connection.execute(
            text(
                "INSERT OR IGNORE INTO customers "
                "(id, tenant_id, customer_number, display_name, email_masked, phone_masked, "
                "status, created_at) VALUES "
                "(:customer_id, :tenant_id, 'CUST-00010001', '李晓晴（演示）', "
                "'l***@example.com', '138****5678', 'active', CURRENT_TIMESTAMP)"
            ),
            {"customer_id": customer_id, "tenant_id": tenant_id},
        )
        order_numbers = {
            "order-demo-1001": "EC-LEGACY-1001",
            "order-demo-1002": "EC-LEGACY-1002",
            "order-demo-1003": "EC-LEGACY-1003",
        }
        for order_id, order_number in order_numbers.items():
            await connection.execute(
                text(
                    "UPDATE commerce_orders SET order_number = :order_number, "
                    "customer_id = :customer_id, sales_channel = COALESCE(sales_channel, 'web'), "
                    "shipping_address_snapshot_json = "
                    "COALESCE(shipping_address_snapshot_json, '{}'), "
                    "updated_at = COALESCE(updated_at, created_at) "
                    "WHERE id = :order_id AND tenant_id = :tenant_id"
                ),
                {
                    "order_number": order_number,
                    "customer_id": customer_id,
                    "order_id": order_id,
                    "tenant_id": tenant_id,
                },
            )
        item_snapshots = {
            "item-demo-1001": (1, "KEYBOARD-01", "无线键盘"),
            "item-demo-1002": (1, "ACCESSORY-02", "限时促销配件"),
            "item-demo-1003": (1, "HEADPHONES-03", "降噪耳机"),
        }
        for item_id, (line_number, sku, name) in item_snapshots.items():
            await connection.execute(
                text(
                    "UPDATE commerce_order_items SET line_number = :line_number, "
                    "sku_snapshot = :sku, product_name_snapshot = :name "
                    "WHERE id = :item_id AND tenant_id = :tenant_id"
                ),
                {
                    "line_number": line_number,
                    "sku": sku,
                    "name": name,
                    "item_id": item_id,
                    "tenant_id": tenant_id,
                },
            )
        await connection.execute(
            text(
                "UPDATE products SET category = CASE id "
                "WHEN 'product-demo-headphones' THEN 'audio' "
                "ELSE 'computer-accessories' END "
                "WHERE tenant_id = :tenant_id"
            ),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text(
                "UPDATE after_sales_cases SET "
                "case_number = COALESCE(case_number, 'ASLEGACY-' || substr(id, 1, 12)), "
                "customer_id = COALESCE(customer_id, :customer_id) "
                "WHERE tenant_id = :tenant_id"
            ),
            {"customer_id": customer_id, "tenant_id": tenant_id},
        )
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_commerce_orders_tenant_number "
                "ON commerce_orders(tenant_id, order_number)"
            )
        )
        await connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_commerce_orders_tenant_customer "
                "ON commerce_orders(tenant_id, customer_id)"
            )
        )
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_after_sales_cases_tenant_number "
                "ON after_sales_cases(tenant_id, case_number)"
            )
        )
