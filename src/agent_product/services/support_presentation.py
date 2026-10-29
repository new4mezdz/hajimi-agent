"""Customer-facing projections of support tool results.

The commerce service and its database keep the original tracking numbers. Agent
tools expose a display value only, so masking never depends on model wording.
"""

from __future__ import annotations

from typing import Any


def mask_tracking_number(value: str) -> str:
    """Keep the final four characters behind exactly four asterisks.

    Empty/whitespace-only values stay empty. Values of four characters or fewer
    are hidden completely, because retaining their suffix would reveal the whole
    identifier. Leading and trailing whitespace is not part of the identifier.
    """
    value = value.strip()
    if not value:
        return ""
    return "****" + value[-4:] if len(value) > 4 else "****"


def support_tool_payload(value: Any) -> Any:
    """Return a fresh JSON-compatible projection, including nested case snapshots.

    If a record contains both raw and display fields, recompute the display field
    from the raw one. Null or malformed raw values fail closed to a null display.
    The source objects are never modified.
    """
    if isinstance(value, dict):
        projected = {
            key: support_tool_payload(child)
            for key, child in value.items()
            if key != "tracking_number"
        }
        if "tracking_number" in value:
            raw = value["tracking_number"]
            projected["tracking_number_display"] = (
                mask_tracking_number(raw) if isinstance(raw, str) else None
            )
        return projected
    if isinstance(value, (list, tuple)):
        return [support_tool_payload(child) for child in value]
    return value
