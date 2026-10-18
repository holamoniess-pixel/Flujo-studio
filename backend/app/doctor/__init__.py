"""System Doctor subsystem: read-only dependency matrix and safe guidance.

Hard rules (production gate):
- Doctor takes NO destructive actions and performs NO repairs.
- Doctor modifies NO source code, overrides NO Monitor halt, bypasses NO policy.
- Every recommendation is manual, read-only guidance for a human operator.
"""

from .checks import (
    COMPONENTS,
    build_health_payload,
    list_incidents,
    overall_status,
    recommendations,
    record_incidents,
    run_checks,
)

__all__ = [
    "COMPONENTS",
    "build_health_payload",
    "list_incidents",
    "overall_status",
    "recommendations",
    "record_incidents",
    "run_checks",
]
