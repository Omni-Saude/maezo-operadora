"""Integration-boundary domain values — the vocabulary the AMH boundary is expressed in (ADR-0037).

    maezo.domain.integration.identity   the five portable identity / tenant value objects (XRD-05)

Public surface only: this module re-exports and declares nothing of its own, mirroring
`maezo.ports.__init__`. Purity (stdlib + this package, nothing else) is fenced by
`tests/unit/domain/integration/test_identity.py`.
"""

from maezo.domain.integration.identity import (
    BUSINESS_KEY_SEPARATOR,
    MAX_COMPONENT_LENGTH,
    MIN_COMPONENT_LENGTH,
    PAYER_PROCESS_DEFINITION_KEY_PREFIX,
    PERMITTED_CHARACTERS,
    CompanyTenantRef,
    ForbiddenRawIdentifierError,
    IdentityShapeError,
    LegalEntityRef,
    PortableSubjectRef,
    SourceProvenanceRef,
    WorkflowBusinessRef,
    is_payer_process_definition_key,
    payer_process_definition_key,
    require_payer_process_definition_key,
)

__all__ = [
    "BUSINESS_KEY_SEPARATOR",
    "MAX_COMPONENT_LENGTH",
    "MIN_COMPONENT_LENGTH",
    "PAYER_PROCESS_DEFINITION_KEY_PREFIX",
    "PERMITTED_CHARACTERS",
    "CompanyTenantRef",
    "ForbiddenRawIdentifierError",
    "IdentityShapeError",
    "LegalEntityRef",
    "PortableSubjectRef",
    "SourceProvenanceRef",
    "WorkflowBusinessRef",
    "is_payer_process_definition_key",
    "payer_process_definition_key",
    "require_payer_process_definition_key",
]
