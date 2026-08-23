# Support Agent relational demo

Application startup calls `seed_support_demo_data()` after database tables are
available. The seed is idempotent and inserts a masked customer, products,
inventory, public order numbers, order-item snapshots, shipments, payments and
inventory movements for `SUPPORT_DEMO_TENANT_ID` and
`SUPPORT_DEMO_CUSTOMER_ID`.

[`demo-data.json`](demo-data.json) is a human-readable manifest; the actual
runtime source of truth is the SQLAlchemy database. Delivery timestamps are
calculated relative to seed time so the three scenarios remain meaningful in a
fresh database.

The Agent never receives arbitrary SQL or a caller-supplied customer id. It
calls domain tools that always add the authenticated tenant and customer
boundaries and return masked, purpose-specific projections. Internal database
ids stay behind the service; customers and tools use public order/case numbers.
