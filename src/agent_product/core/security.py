import re
import secrets
from typing import Annotated

from fastapi import Header, HTTPException, Request, status

TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")


async def require_api_key(
    request: Request,
    supplied_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    expected_key = request.app.state.settings.service_api_key
    if not expected_key:
        return
    if supplied_key is None or not secrets.compare_digest(supplied_key, expected_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


async def require_tenant_id(
    tenant_id: Annotated[str, Header(alias="X-Tenant-ID")] = "local",
) -> str:
    if not TENANT_PATTERN.fullmatch(tenant_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-ID has an invalid format",
        )
    return tenant_id

