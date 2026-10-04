"""HTTP routers, one per resource. Each is thin: validate input, check the role, call a service,
return a DTO from payguard.api.dto. Mounted under /v1 by payguard.api.app (ops endpoints at the root)."""

from payguard.api.routers import (admin, auth, cases, investigations, labels, meta, models, monitoring, ops,
                                  receipts, scoring, transactions)

V1 = [auth.router, meta.router, scoring.router, transactions.router, cases.router, investigations.router, labels.router,
      receipts.router, monitoring.router, models.router, admin.router]
ROOT = [ops.router]
