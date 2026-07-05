"""M12 Platform — PHI & LGPD Hardening.

Provides:
- ErasureManager: cascading erasure by fhir_patient_id (ADR-0002)
- RetentionManager: audit retention policy (ADR-0007)
"""

from maezo.platform.erasure import ErasureManager
from maezo.platform.retention import RetentionManager

__all__ = [
    "ErasureManager",
    "RetentionManager",
]
