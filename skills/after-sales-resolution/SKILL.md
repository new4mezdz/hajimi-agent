---
name: after-sales-resolution
description: Determine the available refund, replacement, or manual-review paths for a confirmed delivered order item. Use when a customer reports damage, missing contents, or asks whether an item can be returned or exchanged.
version: 1
status: published
profiles: [support]
tags: [commerce, after-sales, refund, replacement]
user-invocable: true
---

# After-sales resolution

Give the customer options derived from database facts and published support policy.

1. Resolve the customer's natural-language order reference with `find_my_orders`. If multiple
   orders or lines match, ask them to confirm the public order number and item.
2. Call `lookup_my_order` for the confirmed public order number. Use its line number, not an
   internal order-item id.
3. Load the applicable support policy with `search_knowledge`; treat it as policy evidence, not as
   an operational fact.
4. Call `assess_after_sales_options` with the public order number, line number, and issue type. Its
   refund window and `on_hand - reserved` inventory result are authoritative.
5. Present refund, replacement, and manual-review paths separately, including concrete reasons for
   unavailable paths. Do not promise that current stock has been reserved.
6. Ask which available path the customer wants. Only after an explicit choice may you call
   `create_support_case`; that call must remain subject to user approval.

Creating a case starts human processing. It does not execute a refund, replacement shipment, payment
mutation, or inventory reservation.
