"""PROPOSED v2.1 capability contracts and mechanics; production bindings absent."""

from .models import (
    CANDIDATE_SCHEMA_VERSION,
    CONTRACT_STATE,
    CandidateDTO,
    CapabilityContractError,
    CapabilityEnvelope,
    CapabilityOutcome,
    CapabilityRefusalReason,
    DeclaredMemberships,
    parse_envelope,
    parse_request,
    parse_result,
)
from .service import CapabilityService, CapabilitySourcePort

__all__ = [
    "CANDIDATE_SCHEMA_VERSION",
    "CONTRACT_STATE",
    "CapabilityContractError",
    "CapabilityEnvelope",
    "CapabilityOutcome",
    "CapabilityRefusalReason",
    "CapabilityService",
    "CapabilitySourcePort",
    "CandidateDTO",
    "DeclaredMemberships",
    "parse_envelope",
    "parse_request",
    "parse_result",
]
