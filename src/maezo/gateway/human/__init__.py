"""ADR-0049 D4 human gateway foundation. Production activation is closed pending D5/D6."""

from .credentials import DedicatedHumanCredential, HumanCommandCredentialPartition
from .errors import GatewayRefusalError
from .gateway import HumanGateway, create_production_gateway
from .models import (
    AssignmentCommand,
    AuthoritativeTask,
    AuthorizedAssignment,
    CurrentTaskAuthority,
    PendingAdmission,
    Scope,
)
from .ports import AuthorityProjection, BoundHumanPorts, DurableAdmission, HumanTaskTransport

__all__ = [
    "AssignmentCommand",
    "AuthoritativeTask",
    "AuthorizedAssignment",
    "CurrentTaskAuthority",
    "PendingAdmission",
    "Scope",
    "AuthorityProjection",
    "BoundHumanPorts",
    "DurableAdmission",
    "HumanTaskTransport",
    "DedicatedHumanCredential",
    "HumanCommandCredentialPartition",
    "GatewayRefusalError",
    "HumanGateway",
    "create_production_gateway",
]
