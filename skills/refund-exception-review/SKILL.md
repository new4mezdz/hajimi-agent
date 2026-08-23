---
name: refund-exception-review
description: Prepare a manual-review request when normal refund and replacement rules do not provide an automatic resolution. Use for expired windows, no replacement stock, or an explicit exception request; do not use when an automatic option is available and unexamined.
version: 1
status: published
profiles: [support]
tags: [commerce, refund, exception, manual-review]
user-invocable: true
---

# Refund exception review

Create an accurate exception record without implying that an exception will be granted.

1. Confirm the public order number and line through `find_my_orders` and `lookup_my_order`.
2. Call `assess_after_sales_options` again for the stated issue. Do not rely on an earlier prose
   answer or calculate the window and stock yourself.
3. Explain each blocking reason, such as `refund-window-expired` or
   `replacement-out-of-stock`, in customer-friendly language.
4. Ask whether the customer wants a human exception review. Do not create one merely because an
   automatic path is unavailable.
5. After explicit confirmation, call `create_support_case` with
   `requested_resolution="manual_review"` and a factual summary containing the verified issue and
   blocking reasons. The approval card is the final authorization boundary.

Say that the case is queued for review, not approved. Never promise refund value, timing, or outcome.
