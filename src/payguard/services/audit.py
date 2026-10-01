"""Audit trail for operator actions. `record` takes the caller's session so the audit row commits or rolls
back together with the change it describes (no audited action without its record, and vice versa)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from payguard.api.security import Principal
from payguard.db.models import AuditLog, clean_json


def record(s: Session, actor: Principal, action: str, resource: str, details: dict | None = None) -> None:
    s.add(AuditLog(actor_id=actor.client_id, actor_name=actor.name, action=action, resource=resource,
                   details=clean_json(details) if details else None))
