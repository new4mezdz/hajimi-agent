from typing import Any

from pydantic import BaseModel


class InventoryPositionResponse(BaseModel):
    warehouse_id: str
    on_hand: int
    reserved: int
    available: int


class SupportOrderItemResponse(BaseModel):
    line_number: int
    product_id: str
    sku: str
    name: str
    quantity: int
    unit_price: float
    fulfillment_status: str
    refund_window_days: int
    final_sale: bool
    inventory: list[InventoryPositionResponse]


class ShipmentResponse(BaseModel):
    shipment_id: str
    carrier: str
    tracking_number: str
    status: str
    shipped_at: str | None
    delivered_at: str | None
    estimated_delivery_at: str | None


class SupportOrderResponse(BaseModel):
    order_number: str
    customer_id: str
    customer: str
    status: str
    sales_channel: str
    currency: str
    total_amount: float
    shipping_address: dict[str, Any]
    created_at: str
    paid_at: str | None
    items: list[SupportOrderItemResponse]
    shipments: list[ShipmentResponse]
    payments: list[dict[str, Any]]


class SupportOrderSummaryResponse(BaseModel):
    order_number: str
    created_at: str
    status: str
    total_amount: float
    currency: str
    items: list[dict[str, Any]]
    shipment: dict[str, Any] | None


class AfterSalesOptionsResponse(BaseModel):
    order_number: str
    line_number: int
    product: dict[str, Any]
    issue_type: str
    refund: dict[str, Any]
    replacement: dict[str, Any]
    manual_review: dict[str, Any]
    shipment: dict[str, Any] | None


class InventorySummaryResponse(BaseModel):
    product_id: str
    sku: str
    name: str
    warehouse_id: str
    on_hand: int
    reserved: int
    available: int
    updated_at: str


class SupportCaseResponse(BaseModel):
    case_number: str
    tenant_id: str
    customer_id: str
    order_number: str
    line_number: int
    issue_type: str
    requested_resolution: str
    eligibility: str
    status: str
    reason: str
    eligibility_snapshot: dict[str, Any]
    created_at: str
    updated_at: str
