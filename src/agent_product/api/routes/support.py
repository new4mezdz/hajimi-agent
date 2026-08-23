from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent_product.core.security import require_api_key, require_customer_id, require_tenant_id
from agent_product.schemas.support import (
    AfterSalesOptionsResponse,
    InventorySummaryResponse,
    SupportCaseResponse,
    SupportOrderResponse,
    SupportOrderSummaryResponse,
)
from agent_product.services.support import SupportError, SupportService

router = APIRouter(
    prefix="/v1/support",
    tags=["support-example"],
    dependencies=[Depends(require_api_key)],
)


def _support(request: Request) -> SupportService:
    service = request.app.state.support_service
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The support example is disabled",
        )
    return service


@router.get("/orders", response_model=list[SupportOrderSummaryResponse])
async def find_support_orders(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    customer_id: Annotated[str, Depends(require_customer_id)],
    days: int = 30,
    product_hint: str | None = None,
    status_hint: str | None = None,
) -> list[SupportOrderSummaryResponse]:
    try:
        rows = await _support(request).find_orders(
            tenant_id,
            customer_id,
            days=days,
            product_hint=product_hint,
            status_hint=status_hint,
        )
        return [SupportOrderSummaryResponse.model_validate(row) for row in rows]
    except SupportError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/orders/{order_number}", response_model=SupportOrderResponse)
async def get_support_order(
    order_number: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    customer_id: Annotated[str, Depends(require_customer_id)],
) -> SupportOrderResponse:
    try:
        result = await _support(request).lookup_order(tenant_id, customer_id, order_number)
        return SupportOrderResponse.model_validate(result)
    except SupportError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/orders/{order_number}/items/{line_number}/after-sales-options",
    response_model=AfterSalesOptionsResponse,
)
async def get_after_sales_options(
    order_number: str,
    line_number: int,
    issue_type: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    customer_id: Annotated[str, Depends(require_customer_id)],
) -> AfterSalesOptionsResponse:
    try:
        result = await _support(request).assess_after_sales_options(
            tenant_id,
            customer_id,
            order_number,
            line_number,
            issue_type,
        )
        return AfterSalesOptionsResponse.model_validate(result)
    except SupportError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@router.get("/inventory", response_model=list[InventorySummaryResponse])
async def list_support_inventory(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    customer_id: Annotated[str, Depends(require_customer_id)],
) -> list[InventorySummaryResponse]:
    del customer_id
    return [
        InventorySummaryResponse.model_validate(row)
        for row in await _support(request).list_inventory(tenant_id)
    ]


@router.get("/cases", response_model=list[SupportCaseResponse])
async def list_support_cases(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    customer_id: Annotated[str, Depends(require_customer_id)],
) -> list[SupportCaseResponse]:
    return [
        SupportCaseResponse.model_validate(case)
        for case in await _support(request).list_cases(tenant_id, customer_id)
    ]


@router.get("/cases/{case_number}", response_model=SupportCaseResponse)
async def get_support_case(
    case_number: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    customer_id: Annotated[str, Depends(require_customer_id)],
) -> SupportCaseResponse:
    try:
        case = await _support(request).get_case(tenant_id, customer_id, case_number)
        return SupportCaseResponse.model_validate(case)
    except SupportError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
