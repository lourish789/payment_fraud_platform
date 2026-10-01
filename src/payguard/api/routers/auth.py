"""Who am I: the console exchanges an API key for the caller's identity and capabilities."""

from fastapi import APIRouter, Depends

from payguard.api.deps import principal
from payguard.api.dto import Me
from payguard.api.security import Principal

router = APIRouter(prefix="/auth", tags=["auth"])

PERMISSIONS = {
    "merchant": ["payments:score", "receipts:verify"],
    "analyst": ["transactions:read", "cases:read", "cases:resolve", "investigations:run", "labels:write",
                "receipts:read", "monitoring:read"],
}
PERMISSIONS["admin"] = sorted({*PERMISSIONS["merchant"], *PERMISSIONS["analyst"], "models:read", "models:promote",
                               "admin:read", "clients:manage", "audit:read"})


@router.get("/me", response_model=Me)
def me(p: Principal = Depends(principal)):
    return Me(client_id=p.client_id, name=p.name, role=p.role, permissions=PERMISSIONS[p.role])
