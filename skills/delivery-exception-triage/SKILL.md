---
name: delivery-exception-triage
description: Triage an in-transit, delayed, or apparently missing shipment and decide whether information or a human delivery case is appropriate. Use for logistics exceptions, not delivered-item refund calculations.
version: 1
status: published
profiles: [support]
tags: [commerce, shipment, delivery, exception]
user-invocable: true
---

# Delivery exception triage

Ground the response in the customer's shipment record and avoid premature after-sales promises.

1. Resolve the order with `find_my_orders`, using time and product clues. Ask the customer to choose
   when multiple public order numbers match.
2. Call `lookup_my_order` and report carrier, shipment status, masked tracking number, and estimated
   delivery time.
3. If the shipment is still within its estimate, explain that it is in transit and avoid creating a
   case unless the customer reports a concrete exception.
4. If the estimate has passed, tracking is missing, or the customer reports loss, summarize the
   verified discrepancy and ask whether they want a human delivery investigation.
5. After explicit confirmation, call `create_support_case` with issue type `delivery` and
   `requested_resolution="manual_review"`. Keep the approval card as the final authorization step.

Do not claim a package is lost solely because it is in transit. Do not offer delivered-item refund
or replacement options until the order facts and applicable policy support them.
