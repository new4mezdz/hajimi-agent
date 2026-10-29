---
name: order-delivery-status
description: Resolve a customer's natural-language question about which recent order they mean, its shipment state, and estimated arrival. Use for “昨天买的东西到哪了” or “还有多久送到”, not refund or replacement decisions.
version: 3
status: published
profiles: [support]
tags: [commerce, orders, delivery]
user-invocable: true
---

# Order delivery status

Produce a concise answer grounded in the authenticated customer's order and shipment records.

1. Use `find_my_orders` to resolve the customer's order. Identity comes from the session; do not
   ask for a customer id. `days` filters the order's creation date, not its delivery date. If the
   customer mentions an older purchase, increase the range accordingly (up to 365 days); a delivery
   estimate such as "前天应该到" is not evidence that the order was placed two days ago.
2. `product_hint` is a literal substring search, not semantic matching. If the first search is
   empty, make one meaningfully broader search before asking the customer for more information:
   use a category noun (for example, change "蓝牙耳机" to "耳机"), or omit `product_hint`. Widen
   `days` when an older purchase is plausible. Do not repeat the same effective empty query.
   An empty result is not a tool failure. If the broader search is still empty, explain that no
   matching order was found under the current account and ask for a non-sensitive clue.
3. Use only documented status filters: `processing`, `in_transit`, `delivered`, or
   `not_delivered`. For "还有哪些没送到", use `status_hint="not_delivered"` and `limit=20`;
   this includes processing and in-transit orders, but not cancelled orders. Do not pass Chinese
   status labels to the tool. Inspect all returned orders and their shipment records; if the limit
   is reached, state that the displayed list may be incomplete rather than claiming it is exhaustive.
4. Broader searches are candidate discovery, not permission to substitute a different product.
   If an individual-item request matches several candidates, show public order number, purchase
   date, item name and status, then ask the customer to choose. For a request to list several orders,
   inspect and report each matching order; do not ask the customer to choose only one.
   If a differently named item is only a possible match, ask whether it is
   the intended item. Never silently choose an unrelated item. For one confirmed match, call
   `lookup_my_order` with its public order number. When listing several requested orders, verify each
   with `lookup_my_order` before making detailed shipment claims.
5. Report the item, current shipment status, carrier, and estimated delivery time when present.
   For the tracking number, copy the tool's `tracking_number_display` exactly: the program has
   already masked it. Do not change the mask, guess missing characters, or reconstruct the full
   number. Distinguish an estimate from a guarantee.

Do not expose internal database ids, complete addresses, payment credentials, or another customer's
orders. Do not create a case unless the user explicitly asks for follow-up action.
