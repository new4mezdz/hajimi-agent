---
name: order-delivery-status
description: Resolve a customer's natural-language question about which recent order they mean, its shipment state, and estimated arrival. Use for “昨天买的东西到哪了” or “还有多久送到”, not refund or replacement decisions.
version: 1
status: published
profiles: [support]
tags: [commerce, orders, delivery]
user-invocable: true
---

# Order delivery status

Produce a concise answer grounded in the authenticated customer's order and shipment records.

1. Convert the user's time, product, and status clues into the narrowest useful `find_my_orders`
   query. Do not ask for a customer id; identity comes from the session.
2. If nothing matches, say which clues were used and ask for another non-sensitive clue.
3. If several orders match, show public order number, purchase date, item name, and status, then
   ask the user to choose. Never guess.
4. For one confirmed match, call `lookup_my_order` with its public order number.
5. Report the item, current shipment status, carrier, masked tracking number, and estimated delivery
   time when present. Distinguish an estimate from a guarantee.

Do not expose internal database ids, complete addresses, payment credentials, or another customer's
orders. Do not create a case unless the user explicitly asks for follow-up action.
