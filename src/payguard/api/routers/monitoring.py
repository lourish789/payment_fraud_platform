"""Model monitoring: PSI drift of live serving-time features and scores against the training reference."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request

from payguard.api.deps import container, require
from payguard.api.errors import ApiError
from payguard.monitoring import drift_report

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


@router.get("/drift")
def drift(request: Request, since: Optional[datetime] = None, _=Depends(require("analyst"))):
    """Status is ok | warn | alert, or insufficient_data below 500 decisions by the champion."""
    c = container(request)
    if c.models.champion is None:
        raise ApiError(503, "unavailable", "no model")
    return drift_report(c.session_factory, c.models.champion, since=since)
