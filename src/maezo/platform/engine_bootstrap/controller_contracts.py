"""D7-D stage1 parsed representations; no authority or effect adapter.

Derived from CONTRACT ecf12c074d461198c10c1428c55b419c51c77d2a030dc5fb7e6d7f70464f035d
and SCHEMA 3d81bce2fc1b27ec2e7ff21a7b06ba36a73e7faeb1cc3c22477c3a758850b7d7.
The closed catalogue below is mechanically rendered from the allocated shared spec;
wire and relational predicates are implemented here independently. This module does
not verify signatures, allocate owner resources, or construct runtime admission.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, fields
from typing import Any, Literal, NoReturn, cast

from maezo.platform.engine_bootstrap.secured_plan import compile_boundary_plan

MAX_BYTES = 1048576
MAX_DEPTH = 32
MAX_INTEGER = 9007199254740991
DEFERRED_OBLIGATIONS = (
    "authenticated_owner_and_expected_context",
    "observation_signature_and_trusted_clock_freshness",
    "qualified_complete_current_inventory_and_positive_readiness",
    "foreign_unassigned_session_role_route_harmlessness",
    "owner_allocated_resource_identity_and_nonreuse_tombstones",
    "issuer_state_and_journal_transition_order",
    "native_auth_fence_permit_receipt_before_absent_only_effect",
    "python_java_signature_vector_parity",
)

type Id = str
type Text = str
type EmptyOrId = Literal[""] | Id
type Sha256 = str
type GitOid = str
type Uuid = str
type UInt = int
type Positive = int
type Oid = int
type Port = int
type Version = int
type Bool = bool
type Bytes = str
type Challenge = str
type PublicKey = str
type Signature = str
type ResourceName = str
type Arn = str
type GenerationPurpose = Literal["candidate", "runtime"]
type GenerationStatus = Literal["PREPARED", "OPEN", "RETIRED"]
type JournalState = Literal[
    "INTENT",
    "RESERVED",
    "DISPATCHED_UNKNOWN",
    "ACKNOWLEDGED",
    "RECONCILING",
    "SETTLED",
    "REJECTED",
    "CANCELLED_UNSENT",
]
type SettlementClass = Literal["CURRENT_LIVE_TRACKED", "RETIRED_RESOURCES_TERMINAL", "NO_EFFECT"]
type ControllerState = Literal[
    "REVIEWED",
    "LEASED",
    "START_FENCED",
    "DB_FENCED",
    "MIGRATION_VERIFIED",
    "DEPLOY_ADMITTED",
    "DEPLOY_COMMITTED",
    "BOUNDARY_FROZEN",
    "GRANT_ADMITTED",
    "GRANT_COMMITTED",
    "PROVISIONER_DISPOSED",
    "CANDIDATE_ADMITTED",
    "CANDIDATE_VALIDATED",
    "CANDIDATE_RETIRED",
    "ACTIVATION_DECIDED",
    "RESTORING",
    "RESTORED",
    "READINESS_VERIFIED",
    "ACTIVE",
    "RECOVERY_REQUIRED",
]
type RefusalCode = Literal[
    "AUTH_REFUSED",
    "SCOPE_REFUSED",
    "PURPOSE_REFUSED",
    "STALE_EPOCH",
    "STALE_GENERATION",
    "REQUEST_CONFLICT",
    "PRECONDITION_MISMATCH",
    "INVALID_BODY",
    "UNAVAILABLE",
    "PENDING_UNKNOWN",
    "UNSUPPORTED_ADAPTER",
]


class ContractError(ValueError):
    """Only the eleven reviewed public refusals can escape this module."""

    def __init__(self, code: RefusalCode = "INVALID_BODY") -> None:
        safe = code if type(code) is str and code in _REFUSALS else "INVALID_BODY"
        self.code: RefusalCode = cast(RefusalCode, safe)
        super().__init__(safe)


def _fail(code: RefusalCode = "INVALID_BODY") -> NoReturn:
    raise ContractError(code)


def _need(condition: bool, code: RefusalCode = "INVALID_BODY") -> None:
    if not condition:
        _fail(code)


class _RecordType(type):
    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        try:
            return super().__call__(*args, **kwargs)
        except ContractError:
            raise
        except Exception:
            raise ContractError() from None


@dataclass(frozen=True, slots=True)
class Record(metaclass=_RecordType):
    """Immutable parsed data. All returned mappings are detached defensive copies."""

    def __post_init__(self) -> None:
        document = _plain(self)
        name = type(self).__name__
        canonical_json(document)
        _validate(name, document)
        normalized = _materialize({"$ref": "#/$defs/" + name}, document)
        for field in fields(self):
            object.__setattr__(self, field.name, getattr(normalized, field.name))

    def to_wire(self) -> dict[str, Any]:
        return cast(dict[str, Any], _plain(self))

    def canonical_bytes(self) -> bytes:
        return canonical_json(self)

    def digest(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def unestablished_obligations(self) -> tuple[str, ...]:
        return DEFERRED_OBLIGATIONS


@dataclass(frozen=True, slots=True)
class DatabaseBinding(Record):
    endpoint_host: Id
    endpoint_port: Port
    ca_sha256: Sha256
    server_identity: Id
    database_name: Id
    database_oid: Oid
    schema_name: Id
    schema_oid: Oid
    database_incarnation: Uuid


@dataclass(frozen=True, slots=True)
class Scope(Record):
    account: str
    region: str
    tenant: Id
    environment: Id
    engine_name: Id
    database_binding: DatabaseBinding


@dataclass(frozen=True, slots=True)
class BlobRef(Record):
    sha256: Sha256
    byte_length: Positive


@dataclass(frozen=True, slots=True)
class ReceiptKey(Record):
    tenant: Id
    environment: Id
    engine_name: Id
    operation: Literal["deploy", "grant"]
    run_id: Uuid


@dataclass(frozen=True, slots=True)
class ReceiptRef(Record):
    receipt_key: ReceiptKey
    request_sha256: Sha256
    result_sha256: Sha256


@dataclass(frozen=True, slots=True)
class EngineIdentity(Record):
    tenant: Id
    environment: Id
    workload: Id
    workload_version: Id
    issuer: Id
    subject: Id
    origin: Literal["verified_mtls"]


@dataclass(frozen=True, slots=True)
class EngineTarget(Record):
    process_key: Id
    process_version: Version
    definition_id: Id
    topic: EmptyOrId
    message: EmptyOrId


@dataclass(frozen=True, slots=True)
class CertificateBinding(Record):
    certificate_sha256: Sha256
    spki_sha256: Sha256
    issuer_dn: Id
    subject_dn: Text
    uri_san: Id
    not_before_ms: UInt
    expires_at_ms: Positive


@dataclass(frozen=True, slots=True)
class ContentReleaseRef(Record):
    release_id: Id
    reviewed_source: GitOid
    review_artifact_sha256: Sha256


@dataclass(frozen=True, slots=True)
class ParsedDefinition(Record):
    kind: Literal["process", "decision", "drd"]
    key: Id


@dataclass(frozen=True, slots=True)
class DeployResource(Record):
    name: ResourceName
    kind: Literal["BPMN", "DMN"]
    sha256: Sha256
    byte_length: Positive
    expected_definitions: tuple[ParsedDefinition, ...]
    content_release_refs: tuple[ContentReleaseRef, ...]


@dataclass(frozen=True, slots=True)
class DeployPayload(Record):
    tenant: Id
    deployment_name: Id
    resources: tuple[DeployResource, ...]
    expected_deployment_inventory_sha256: Sha256


@dataclass(frozen=True, slots=True)
class Grant(Record):
    engine_user: Id
    resource: Literal["PROCESS_DEFINITION", "PROCESS_INSTANCE"]
    resource_id: str
    permissions: tuple[
        Literal["READ", "CREATE_INSTANCE", "READ_INSTANCE", "READ_HISTORY", "UPDATE_INSTANCE", "CREATE"], ...
    ]


@dataclass(frozen=True, slots=True)
class GrantSource(Record):
    engine_user: Id
    resource: Literal["PROCESS_DEFINITION", "PROCESS_INSTANCE"]
    resource_id: str
    capability_digests: tuple[Sha256, ...]


@dataclass(frozen=True, slots=True)
class AttestationMapping(Record):
    name: Id
    source_variable: Id
    human_task_definition: EmptyOrId


@dataclass(frozen=True, slots=True)
class NoSource(Record):
    kind: Literal["none"]
    source_target: None
    source_worker_id: Literal[""]
    attestations: tuple[AttestationMapping, ...]


@dataclass(frozen=True, slots=True)
class LockedSource(Record):
    kind: Literal["locked_external"]
    source_target: EngineTarget
    source_worker_id: Id
    attestations: tuple[AttestationMapping, ...]


@dataclass(frozen=True, slots=True)
class HumanSource(Record):
    kind: Literal["completed_human"]
    source_target: EngineTarget
    source_worker_id: Literal[""]
    attestations: tuple[AttestationMapping, ...]


@dataclass(frozen=True, slots=True)
class CapabilityMapping(Record):
    capability_sha256: Sha256
    registered_schema_id: Id
    registered_schema_sha256: Sha256
    target: EngineTarget
    worker_id: EmptyOrId
    source: SourceMapping


@dataclass(frozen=True, slots=True)
class WorkloadPlan(Record):
    engine_user: Id
    identity: EngineIdentity
    capabilities: tuple[CapabilityMapping, ...]


@dataclass(frozen=True, slots=True)
class Operators(Record):
    bootstrap: Id
    deployment: Id


@dataclass(frozen=True, slots=True)
class CompilerPlan(Record):
    protocol: Literal["maezo.engine-provisioning-plan.v1"]
    execution_authorized: Literal[False]
    boundary_sha256: Sha256
    tenant: Id
    environment: Id
    engine_name: Id
    operators: Operators
    preserve_existing_users: tuple[Id, ...]
    definitions: tuple[EngineTarget, ...]
    grants: tuple[Grant, ...]
    grant_sources: tuple[GrantSource, ...]
    required_readbacks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GrantPayload(Record):
    deployment_receipt: ReceiptRef
    final_boundary_manifest: BlobRef
    compiler_plan: CompilerPlan
    compiler_plan_sha256: Sha256
    workload_plans: tuple[WorkloadPlan, ...]
    expected_identity_authorization_inventory_sha256: Sha256


@dataclass(frozen=True, slots=True)
class DeployRequest(Record):
    protocol: Literal["maezo.native-provisioning-request.v1"]
    scope: Scope
    run_id: Uuid
    source: GitOid
    tree: GitOid
    native_image_sha256: Sha256
    native_jar_sha256: Sha256
    trust_profile_sha256: Sha256
    artifact_set_sha256: Sha256
    expected_before_state_sha256: Sha256
    operation: Literal["deploy"]
    payload: DeployPayload


@dataclass(frozen=True, slots=True)
class GrantRequest(Record):
    protocol: Literal["maezo.native-provisioning-request.v1"]
    scope: Scope
    run_id: Uuid
    source: GitOid
    tree: GitOid
    native_image_sha256: Sha256
    native_jar_sha256: Sha256
    trust_profile_sha256: Sha256
    artifact_set_sha256: Sha256
    expected_before_state_sha256: Sha256
    operation: Literal["grant"]
    payload: GrantPayload


@dataclass(frozen=True, slots=True)
class StoredResource(Record):
    name: ResourceName
    kind: Literal["BPMN", "DMN"]
    sha256: Sha256
    byte_length: Positive
    native_resource_id: Id


@dataclass(frozen=True, slots=True)
class GeneratedDefinition(Record):
    kind: Literal["process", "decision", "drd"]
    key: Id
    definition_id: Id
    version: Version
    deployment_id: Id
    resource_name: ResourceName
    tenant: Id


@dataclass(frozen=True, slots=True)
class DeployResultBody(Record):
    protocol: Literal["maezo.native-provisioning-result.v1"]
    operation: Literal["deploy"]
    tenant: Id
    deployment_id: Id
    deployment_name: Id
    resources: tuple[StoredResource, ...]
    definitions: tuple[GeneratedDefinition, ...]
    before_inventory_sha256: Sha256
    after_inventory_sha256: Sha256
    allowed_native_delta_sha256: Sha256
    protected_state_sha256: Sha256


@dataclass(frozen=True, slots=True)
class IdentityResult(Record):
    engine_user: Id
    identity_sha256: Sha256
    disposition: Literal["CREATED", "PRESERVED"]


@dataclass(frozen=True, slots=True)
class GrantResultRow(Record):
    authorization_id: Id
    grant: Grant
    disposition: Literal["CREATED", "PRESERVED"]


@dataclass(frozen=True, slots=True)
class GrantResultBody(Record):
    protocol: Literal["maezo.native-provisioning-result.v1"]
    operation: Literal["grant"]
    deployment_receipt: ReceiptRef
    boundary_sha256: Sha256
    compiler_plan_sha256: Sha256
    identities: tuple[IdentityResult, ...]
    grants: tuple[GrantResultRow, ...]
    before_inventory_sha256: Sha256
    after_inventory_sha256: Sha256
    allowed_native_delta_sha256: Sha256
    protected_state_sha256: Sha256


@dataclass(frozen=True, slots=True)
class NativeActor(Record):
    login_name: Id
    login_oid: Oid
    session_user: Id
    current_user: Id
    task_arn: Arn
    task_definition_revision: Arn
    purpose: Literal["deploy", "grant", "receipt"]


@dataclass(frozen=True, slots=True)
class NativeReceipt(Record):
    protocol: Literal["maezo.native-provisioning-receipt.v1"]
    receipt_key: ReceiptKey
    scope: Scope
    request_sha256: Sha256
    result_sha256: Sha256
    canonical_result_bytes: Bytes
    source: GitOid
    tree: GitOid
    image_sha256: Sha256
    jar_sha256: Sha256
    trust_profile_sha256: Sha256
    permit_id: Uuid
    commit_epoch: Positive
    native_actor: NativeActor
    recorded_before_commit_at_ms: UInt
    provisioning_schema_version: Version


@dataclass(frozen=True, slots=True)
class ReceiptQuery(Record):
    protocol: Literal["maezo.native-provisioning-receipt-query.v1"]
    scope: Scope
    receipt_key: ReceiptKey
    request_sha256: Sha256


@dataclass(frozen=True, slots=True)
class Limits(Record):
    max_observation_age_ms: Literal[15000]
    max_clock_disagreement_ms: Literal[2000]
    lease_term_ms: Literal[120000]
    heartbeat_ms: Literal[10000]
    permit_admission_validity_ms: Literal[60000]
    native_tx_deadline_ms: Literal[30000]
    observer_request_deadline_ms: Literal[5000]


@dataclass(frozen=True, slots=True)
class ObserverKey(Record):
    key_id: Id
    issuer: Id
    purpose: Literal["control-observation", "database-observation"]
    subject: Id
    audience: Id
    algorithm: Literal["Ed25519"]
    public_key: PublicKey
    not_before_ms: UInt
    expires_at_ms: Positive
    lifecycle: Literal["ACTIVE", "RETIRED", "REVOKED"]
    predecessor_key_id: Id | None
    revoked_at_ms: UInt | None


@dataclass(frozen=True, slots=True)
class TrustProfile(Record):
    protocol: Literal["maezo.provisioning-trust-profile.v1"]
    profile_id: Id
    scope: Scope
    release_source: GitOid
    release_tree: GitOid
    verifier_image_sha256: Sha256
    verifier_jar_sha256: Sha256
    limits: Limits
    control_keys: tuple[ObserverKey, ...]
    database_keys: tuple[ObserverKey, ...]
    inventory_bounds_sha256: Sha256


INVENTORY_FAMILIES = (
    "control.payload.credential_sessions",
    "control.payload.journal",
    "control.payload.journal.provider_outcome.failures",
    "control.payload.journal.provider_outcome.returned_resource_ids",
    "control.payload.journal.settlement.resource_proofs",
    "control.payload.live_dispatchers",
    "control.payload.managed_resources",
    "control.payload.managed_resources.task_definition_revision.network_binding.security_group_ids",
    "control.payload.managed_resources.task_definition_revision.network_binding.subnet_ids",
    "control.payload.services",
    "control.payload.services.deployments",
    "control.payload.services.deployments.task_definition.network_binding.security_group_ids",
    "control.payload.services.deployments.task_definition.network_binding.subnet_ids",
    "control.payload.services.task_sets",
    "control.payload.services.task_sets.task_definition.network_binding.security_group_ids",
    "control.payload.services.task_sets.task_definition.network_binding.subnet_ids",
    "control.payload.start_paths",
    "control.payload.task_definitions",
    "control.payload.task_definitions.network_binding.security_group_ids",
    "control.payload.task_definitions.network_binding.subnet_ids",
    "control.payload.tasks",
    "control.payload.tasks.credential_session_ids",
    "control.payload.tasks.task_definition.network_binding.security_group_ids",
    "control.payload.tasks.task_definition.network_binding.subnet_ids",
    "database.payload.generations",
    "database.payload.generations.binding.network_binding.security_group_ids",
    "database.payload.generations.binding.network_binding.subnet_ids",
    "database.payload.generations.binding.task_definition_revision.network_binding.security_group_ids",
    "database.payload.generations.binding.task_definition_revision.network_binding.subnet_ids",
    "database.payload.memberships",
    "database.payload.prepared_transactions",
    "database.payload.roles",
    "database.payload.routes",
    "database.payload.routes.backend_login_oids",
    "database.payload.sessions",
)


@dataclass(frozen=True, slots=True)
class InventoryBound(Record):
    collection_path: Id
    max_entries: int


@dataclass(frozen=True, slots=True)
class InventoryBounds(Record):
    protocol: Literal["maezo.provisioning-inventory-bounds.v2"]
    scope: Scope
    bounds: tuple[InventoryBound, ...]


@dataclass(frozen=True, slots=True)
class NetworkBinding(Record):
    vpc_id: Id
    subnet_ids: tuple[Id, ...]
    security_group_ids: tuple[Id, ...]
    assign_public_ip: Literal["DISABLED"]
    network_policy_sha256: Sha256


@dataclass(frozen=True, slots=True)
class SecretVersion(Record):
    resource_arn: Arn
    version_id: Id


@dataclass(frozen=True, slots=True)
class TaskDefinitionBinding(Record):
    arn: Arn
    revision: Version
    registration_sha256: Sha256
    image_sha256: Sha256
    config_set_sha256: Sha256
    execution_role: Arn
    task_role: Arn
    network_binding: NetworkBinding


@dataclass(frozen=True, slots=True)
class GenerationCore(Record):
    generation_id: Positive
    scope: Scope
    epoch: Positive
    purpose: GenerationPurpose
    image_sha256: Sha256
    task_definition_revision: TaskDefinitionBinding
    config_set_sha256: Sha256
    task_role: Arn
    execution_role: Arn
    network_binding: NetworkBinding
    immutable_secret_resource_version: SecretVersion
    credential_binding_sha256: Sha256
    login_oid: Oid
    login_name: Id


@dataclass(frozen=True, slots=True)
class RuntimeAdmissionGeneration(Record):
    binding: GenerationCore
    activation_decision_sha256: Sha256
    status: GenerationStatus
    revision: Positive


@dataclass(frozen=True, slots=True)
class CredentialSession(Record):
    generation_id: Positive
    principal_arn: Arn
    session_arn: Arn
    session_id: Id
    issued_at_ms: UInt
    expires_at_ms: Positive
    effective_policy_sha256: Sha256
    resource_scope_sha256: Sha256
    broker_operation_id: Uuid


@dataclass(frozen=True, slots=True)
class DispatcherReservation(Record):
    invocation_id: Uuid
    worker_subject: Id
    credential_session: CredentialSession
    reserved_at_ms: UInt
    intent_sha256: Sha256


@dataclass(frozen=True, slots=True)
class ProviderFailure(Record):
    resource_id: Id | None
    code: Literal["CAPACITY", "PLACEMENT", "PERMISSION", "INVALID_REQUEST", "UNCLASSIFIED"]
    private_diagnostic_sha256: Sha256


@dataclass(frozen=True, slots=True)
class ProviderOutcome(Record):
    provider_request_id: Id
    response_sha256: Sha256
    returned_resource_ids: tuple[Arn, ...]
    failures: tuple[ProviderFailure, ...]
    observed_at_ms: UInt


@dataclass(frozen=True, slots=True)
class DispatchTerminalProof(Record):
    invocation_id: Uuid
    intent_sha256: Sha256
    status: Literal["COMPLETED", "CANCELLED_BEFORE_SEND", "TERMINATED_AND_AUTHORITY_RETIRED"]
    observed_at_ms: UInt
    authenticated_observer_subject: Id
    proof_sha256: Sha256


@dataclass(frozen=True, slots=True)
class ResourceTerminalProof(Record):
    resource_id: Id
    generation_id: Positive
    terminal_kind: Literal[
        "TASK_STOPPED",
        "SERVICE_DELETED",
        "TASK_SET_DELETED",
        "ROLE_RETIRED",
        "SECRET_DELIVERY_DISABLED",
        "PRIVATE_MOUNT_DISPOSED",
        "IMMUTABLE_OBJECT_PUBLISHED",
        "NO_EFFECT",
    ]
    observation_sha256: Sha256


@dataclass(frozen=True, slots=True)
class Settlement(Record):
    settlement_class: SettlementClass
    dispatch_terminal: DispatchTerminalProof
    resource_proofs: tuple[ResourceTerminalProof, ...]
    managed_resource_registry_sha256: Sha256
    settled_at_ms: UInt


@dataclass(frozen=True, slots=True)
class JournalObservation(Record):
    external_operation_id: Uuid
    logical_run_id: Uuid
    epoch: Positive
    generation_id: Positive
    intent_sha256: Sha256
    action: Literal[
        "RunTask",
        "StopTask",
        "PrepareGeneration",
        "AdmitCandidateLogin",
        "OpenRuntimeGeneration",
        "RetireGeneration",
        "RevokeNativePermit",
        "PublishConfigSet",
        "DisposeOwnedMount",
        "StartTask",
        "UpdateService",
        "CreateTaskSet",
        "UpdateTaskSet",
        "DeleteTaskSet",
        "SchedulingChange",
        "AutomaticRollback",
    ]
    state: JournalState
    reservation: DispatcherReservation | None
    provider_outcome: ProviderOutcome | None
    settlement: Settlement | None
    cancellation_unsent_proof: DispatchTerminalProof | None


@dataclass(frozen=True, slots=True)
class ManagedResource(Record):
    scope: Scope
    generation_id: Positive
    external_operation_id: Uuid
    provider_resource_id: Arn
    task_definition_revision: TaskDefinitionBinding
    image_sha256: Sha256
    config_set_sha256: Sha256
    desired_state: Literal["READY", "STOPPED", "ABSENT"]
    current_observed_state: Literal["PENDING", "RUNNING", "READY", "STOPPING", "STOPPED", "ABSENT"]
    observation_sha256: Sha256
    retired_terminal_proof_sha256: Sha256 | None


@dataclass(frozen=True, slots=True)
class TaskObservation(Record):
    task_arn: Arn
    generation_id: Positive
    task_definition: TaskDefinitionBinding
    image_sha256: Sha256
    last_status: Literal[
        "PROVISIONING",
        "PENDING",
        "ACTIVATING",
        "RUNNING",
        "DEACTIVATING",
        "STOPPING",
        "DEPROVISIONING",
        "STOPPED",
    ]
    desired_status: Literal["RUNNING", "STOPPED"]
    standalone: Bool
    task_set_arn: Arn | None
    credential_session_ids: tuple[Id, ...]
    config_set_sha256: Sha256


@dataclass(frozen=True, slots=True)
class ServiceDeployment(Record):
    deployment_id: Id
    task_definition: TaskDefinitionBinding
    desired_count: UInt
    running_count: UInt
    pending_count: UInt
    rollout_state: Literal["IN_PROGRESS", "COMPLETED", "FAILED"]


@dataclass(frozen=True, slots=True)
class TaskSetObservation(Record):
    task_set_arn: Arn
    generation_id: Positive
    task_definition: TaskDefinitionBinding
    status: Literal["PRIMARY", "ACTIVE", "DRAINING"]
    desired_count: UInt
    running_count: UInt
    pending_count: UInt


@dataclass(frozen=True, slots=True)
class ServiceObservation(Record):
    service_arn: Arn
    generation_id: Positive
    desired_count: UInt
    running_count: UInt
    pending_count: UInt
    deployments: tuple[ServiceDeployment, ...]
    task_sets: tuple[TaskSetObservation, ...]


@dataclass(frozen=True, slots=True)
class StartPath(Record):
    path_id: Id
    actor_arn: Arn
    kind: Literal["DISPATCHER", "CI", "OPERATOR", "SCHEDULER", "AUTOSCALING", "ROLLBACK"]
    enabled: Bool
    qualified_adapter_sha256: Sha256 | None
    generation_id: Positive | None
    enforced_policy_sha256: Sha256


@dataclass(frozen=True, slots=True)
class ControlPayload(Record):
    cluster_arn: Arn
    services: tuple[ServiceObservation, ...]
    task_definitions: tuple[TaskDefinitionBinding, ...]
    tasks: tuple[TaskObservation, ...]
    desired_count: UInt
    running_count: UInt
    pending_count: UInt
    start_paths: tuple[StartPath, ...]
    operation_record_revision: Positive
    controller_state: ControllerState
    principal_policy_inventory_sha256: Sha256
    journal_revision: UInt
    pending_index_sha256: Sha256
    journal: tuple[JournalObservation, ...]
    live_dispatchers: tuple[DispatcherReservation, ...]
    credential_sessions: tuple[CredentialSession, ...]
    managed_resources: tuple[ManagedResource, ...]
    complete: Literal[True]
    pagination_sha256: Sha256
    complete_read_proof_sha256: Sha256


@dataclass(frozen=True, slots=True)
class RoleObservation(Record):
    oid: Oid
    name: Id
    owned: Bool
    can_login: Bool
    superuser: Bool
    create_role: Bool
    create_db: Bool
    replication: Bool
    bypass_rls: Bool
    inherit: Bool
    generation_id: Positive | None
    effective_privileges_sha256: Sha256


@dataclass(frozen=True, slots=True)
class MembershipObservation(Record):
    member_oid: Oid
    role_oid: Oid
    grantor_oid: Oid
    admin_option: Bool
    inherit_option: Bool
    set_option: Bool


@dataclass(frozen=True, slots=True)
class SessionObservation(Record):
    pid: Positive
    backend_start_unix_us: UInt
    login_oid: Oid
    current_role_oid: Oid
    database_oid: Oid
    generation_id: Positive | None
    owned: Bool
    state: Literal[
        "ACTIVE",
        "IDLE",
        "IDLE_IN_TRANSACTION",
        "IDLE_IN_TRANSACTION_ABORTED",
        "FASTPATH_FUNCTION_CALL",
        "DISABLED",
    ]
    transaction_start_ms: UInt | None
    route_id: Id


@dataclass(frozen=True, slots=True)
class PreparedTransaction(Record):
    transaction_gid_sha256: Sha256
    login_oid: Oid
    database_oid: Oid
    prepared_at_ms: UInt
    generation_id: Positive | None
    owned: Bool


@dataclass(frozen=True, slots=True)
class RouteObservation(Record):
    route_id: Id
    kind: Literal[
        "DIRECT_PASSWORD",
        "IAM",
        "CERTIFICATE",
        "POOLER",
        "SET_ROLE",
        "PUBLIC",
        "SECURITY_DEFINER",
        "MAINTENANCE",
    ]
    enabled: Bool
    generation_id: Positive | None
    frontend_identity_sha256: Sha256
    backend_login_oids: tuple[Oid, ...]
    network_binding_sha256: Sha256
    qualified_adapter_sha256: Sha256 | None
    in_flight_admissions: UInt
    admission_barrier_sha256: Sha256


@dataclass(frozen=True, slots=True)
class FenceObservation(Record):
    epoch: Positive
    state: Literal["CLOSED", "CANDIDATE", "RESTORING", "OPEN", "RECOVERY_REQUIRED"]
    revision: Positive
    owner_run_id: Uuid
    candidate_sha256: Sha256
    trust_profile_sha256: Sha256


@dataclass(frozen=True, slots=True)
class DatabasePayload(Record):
    database_binding: DatabaseBinding
    server_version_num: Positive
    session_user: Id
    current_user: Id
    fence: FenceObservation
    role_membership_acl_sha256: Sha256
    roles: tuple[RoleObservation, ...]
    memberships: tuple[MembershipObservation, ...]
    sessions: tuple[SessionObservation, ...]
    prepared_transactions: tuple[PreparedTransaction, ...]
    routes: tuple[RouteObservation, ...]
    generations: tuple[RuntimeAdmissionGeneration, ...]
    migration_receipt_sha256: Sha256
    protected_state_sha256: Sha256
    complete: Literal[True]
    visibility_qualification_sha256: Sha256
    in_flight_admission_barrier_sha256: Sha256
    issuer_receipts_sha256: Sha256


@dataclass(frozen=True, slots=True)
class ControlObservation(Record):
    protocol: Literal["maezo.provisioning-observation.v1"]
    issuer: Id
    key_id: Id
    audience: Id
    challenge: Challenge
    scope: Scope
    run_id: Uuid
    epoch: Positive
    candidate_sha256: Sha256
    request_sha256: Sha256
    trust_profile_sha256: Sha256
    issued_at_ms: UInt
    expires_at_ms: Positive
    observed_revision: Positive
    signature: Signature
    kind: Literal["control"]
    payload: ControlPayload


@dataclass(frozen=True, slots=True)
class DatabaseObservation(Record):
    protocol: Literal["maezo.provisioning-observation.v1"]
    issuer: Id
    key_id: Id
    audience: Id
    challenge: Challenge
    scope: Scope
    run_id: Uuid
    epoch: Positive
    candidate_sha256: Sha256
    request_sha256: Sha256
    trust_profile_sha256: Sha256
    issued_at_ms: UInt
    expires_at_ms: Positive
    observed_revision: Positive
    signature: Signature
    kind: Literal["database"]
    payload: DatabasePayload


@dataclass(frozen=True, slots=True)
class AwsvpcConfiguration(Record):
    subnets: tuple[Id, ...]
    securityGroups: tuple[Id, ...]  # noqa: N815 - exact reviewed provider wire name
    assignPublicIp: Literal["DISABLED"]  # noqa: N815 - exact reviewed provider wire name


@dataclass(frozen=True, slots=True)
class ProviderNetworkConfiguration(Record):
    awsvpcConfiguration: AwsvpcConfiguration  # noqa: N815 - exact reviewed provider wire name


@dataclass(frozen=True, slots=True)
class RunTaskProviderRequest(Record):
    cluster: Arn
    taskDefinition: Arn  # noqa: N815 - exact reviewed provider wire name
    launchType: Literal["FARGATE"]  # noqa: N815 - exact reviewed provider wire name
    platformVersion: str  # noqa: N815 - exact reviewed provider wire name
    count: int
    clientToken: str  # noqa: N815 - exact reviewed provider wire name
    networkConfiguration: ProviderNetworkConfiguration  # noqa: N815 - exact reviewed provider wire name
    startedBy: Uuid  # noqa: N815 - exact reviewed provider wire name
    enableExecuteCommand: Literal[False]  # noqa: N815 - exact reviewed provider wire name


@dataclass(frozen=True, slots=True)
class StopTaskProviderRequest(Record):
    cluster: Arn
    task: Arn
    reason: Literal["maezo-controller-owned-stop"]


@dataclass(frozen=True, slots=True)
class ExpectedPrecondition(Record):
    controller_revision: Positive
    db_fence_revision: Positive
    journal_revision: UInt
    pending_index_sha256: Sha256
    target_inventory_sha256: Sha256
    target_generation_status: GenerationStatus


@dataclass(frozen=True, slots=True)
class RunTaskIntent(Record):
    protocol: Literal["maezo.provisioning-intent.v1"]
    scope: Scope
    external_operation_id: Uuid
    logical_run_id: Uuid
    owner_subject: Id
    epoch: Positive
    purpose: GenerationPurpose
    target_arn: Arn
    target_generation: Positive
    task_definition_revision: TaskDefinitionBinding
    image_sha256: Sha256
    config_set_sha256: Sha256
    execution_role: Arn
    task_role: Arn
    network_binding: NetworkBinding
    admission_generation: Positive
    credential_binding: CredentialSession
    request_bytes: Bytes
    request_sha256: Sha256
    expected_precondition: ExpectedPrecondition
    action: Literal["RunTask"]
    idempotency_class: Literal["PROVIDER_TOKEN_BOUNDED"]
    client_token: str
    request: RunTaskProviderRequest


@dataclass(frozen=True, slots=True)
class StopTaskIntent(Record):
    protocol: Literal["maezo.provisioning-intent.v1"]
    scope: Scope
    external_operation_id: Uuid
    logical_run_id: Uuid
    owner_subject: Id
    epoch: Positive
    purpose: GenerationPurpose
    target_arn: Arn
    target_generation: Positive
    task_definition_revision: TaskDefinitionBinding
    image_sha256: Sha256
    config_set_sha256: Sha256
    execution_role: Arn
    task_role: Arn
    network_binding: NetworkBinding
    admission_generation: Positive
    credential_binding: CredentialSession
    request_bytes: Bytes
    request_sha256: Sha256
    expected_precondition: ExpectedPrecondition
    action: Literal["StopTask"]
    idempotency_class: Literal["SINGLE_DISPATCH_RECONCILE"]
    client_token: None
    request: StopTaskProviderRequest


@dataclass(frozen=True, slots=True)
class IssuerPrepare(Record):
    operation: Literal["prepare_generation"]
    generation: RuntimeAdmissionGeneration


@dataclass(frozen=True, slots=True)
class IssuerCandidate(Record):
    operation: Literal["admit_candidate_login"]
    generation_id: Positive
    candidate_decision_sha256: Sha256
    disposal_sha256: Sha256
    deployment_receipt: ReceiptRef
    grant_receipt: ReceiptRef
    expected_db_revision: Positive


@dataclass(frozen=True, slots=True)
class IssuerOpen(Record):
    operation: Literal["open_runtime_generation"]
    generation_id: Positive
    activation_decision_sha256: Sha256
    expected_db_revision: Positive


@dataclass(frozen=True, slots=True)
class IssuerRetire(Record):
    operation: Literal["retire_generation"]
    generation_id: Positive
    expected_db_revision: Positive


@dataclass(frozen=True, slots=True)
class IssuerRequest(Record):
    protocol: Literal["maezo.provisioning-issuer-request.v1"]
    scope: Scope
    run_id: Uuid
    epoch: Positive
    issuer_operation_id: Uuid
    body: IssuerRequestBody


@dataclass(frozen=True, slots=True)
class IssuerReceipt(Record):
    protocol: Literal["maezo.provisioning-issuer-receipt.v1"]
    scope: Scope
    issuer_operation_id: Uuid
    request_sha256: Sha256
    epoch: Positive
    generation_id: Positive
    purpose: GenerationPurpose
    binding_sha256: Sha256
    activation_decision_sha256: Sha256
    status: GenerationStatus
    revision: Positive
    issuer_login_oid: Oid
    issuer_session_user: Id
    issuer_current_user: Id
    recorded_before_commit_at_ms: UInt
    provisioning_schema_version: Version


@dataclass(frozen=True, slots=True)
class UnsupportedObservedIntent(Record):
    protocol: Literal["maezo.provisioning-unsupported-intent.v1"]
    scope: Scope
    external_operation_id: Uuid
    action: Literal[
        "StartTask",
        "UpdateService",
        "CreateTaskSet",
        "UpdateTaskSet",
        "DeleteTaskSet",
        "SchedulingChange",
        "AutomaticRollback",
        "RevokeNativePermit",
        "PublishConfigSet",
        "DisposeOwnedMount",
    ]
    intent_sha256: Sha256
    request_sha256: Sha256
    state: Literal["DISPATCHED_UNKNOWN"]
    code: Literal["UNSUPPORTED_ADAPTER"]


@dataclass(frozen=True, slots=True)
class Topology(Record):
    cluster_arn: Arn
    task_count: int
    service_arns: tuple[Arn, ...]
    task_set_arns: tuple[Arn, ...]
    permitted_startup_plan_sha256: Sha256


@dataclass(frozen=True, slots=True)
class CandidateDecision(Record):
    scope: Scope
    run_id: Uuid
    epoch: Positive
    source: GitOid
    tree: GitOid
    image_sha256: Sha256
    trust_profile_sha256: Sha256
    deployment_receipt: ReceiptRef
    grant_receipt: ReceiptRef
    boundary_sha256: Sha256
    config_set_sha256: Sha256
    generation_core: GenerationCore
    topology: Topology
    settled_journal_sha256: Sha256
    protocol: Literal["maezo.candidate-admission-decision.v1"]
    purpose: Literal["candidate"]
    provisioner_disposal_sha256: Sha256


@dataclass(frozen=True, slots=True)
class ActivationDecision(Record):
    scope: Scope
    run_id: Uuid
    epoch: Positive
    source: GitOid
    tree: GitOid
    image_sha256: Sha256
    trust_profile_sha256: Sha256
    deployment_receipt: ReceiptRef
    grant_receipt: ReceiptRef
    boundary_sha256: Sha256
    config_set_sha256: Sha256
    generation_core: GenerationCore
    topology: Topology
    settled_journal_sha256: Sha256
    protocol: Literal["maezo.runtime-activation-decision.v1"]
    purpose: Literal["runtime"]
    candidate_validation_sha256: Sha256
    candidate_disposal_sha256: Sha256


@dataclass(frozen=True, slots=True)
class RuntimeActivationBinding(Record):
    scope: Scope
    database_binding: DatabaseBinding
    controller_epoch: Positive
    source: GitOid
    tree: GitOid
    image_sha256: Sha256
    trust_profile_sha256: Sha256
    deployment_receipt_key: ReceiptKey
    deployment_result_sha256: Sha256
    grant_receipt_key: ReceiptKey
    grant_result_sha256: Sha256
    boundary_sha256: Sha256
    config_set_sha256: Sha256
    caller_identity: EngineIdentity
    caller_certificate_binding: CertificateBinding
    runtime_admission_generation: Positive
    activation_decision_sha256: Sha256
    runtime_task_identity_sha256: Sha256
    task_definition_binding_sha256: Sha256
    issuer_restore_receipt_sha256: Sha256
    readiness_result_sha256: Sha256
    journal_revision: UInt
    settled_journal_sha256: Sha256


@dataclass(frozen=True, slots=True)
class NativeCommitted(Record):
    kind: Literal["COMMITTED"]
    receipt_key: ReceiptKey
    request_sha256: Sha256
    canonical_result_bytes: Bytes
    result_sha256: Sha256


@dataclass(frozen=True, slots=True)
class NativeReplay(Record):
    kind: Literal["REPLAY"]
    receipt_key: ReceiptKey
    request_sha256: Sha256
    canonical_result_bytes: Bytes
    result_sha256: Sha256


@dataclass(frozen=True, slots=True)
class ReceiptFound(Record):
    kind: Literal["FOUND"]
    receipt_key: ReceiptKey
    request_sha256: Sha256
    canonical_result_bytes: Bytes
    result_sha256: Sha256


@dataclass(frozen=True, slots=True)
class Refused(Record):
    kind: Literal["REFUSED"]
    code: RefusalCode


@dataclass(frozen=True, slots=True)
class ReceiptNotFound(Record):
    kind: Literal["NOT_FOUND"]
    receipt_key: ReceiptKey


@dataclass(frozen=True, slots=True)
class ExternalAcknowledged(Record):
    kind: Literal["ACKNOWLEDGED"]
    operation_id: Uuid
    intent_sha256: Sha256
    provider_result_sha256: Sha256
    returned_resource_ids: tuple[Arn, ...]


@dataclass(frozen=True, slots=True)
class ExternalPending(Record):
    kind: Literal["PENDING"]
    operation_id: Uuid
    intent_sha256: Sha256
    state: Literal["INTENT", "RESERVED", "DISPATCHED_UNKNOWN", "ACKNOWLEDGED", "RECONCILING"]


@dataclass(frozen=True, slots=True)
class ExternalSettled(Record):
    kind: Literal["SETTLED"]
    operation_id: Uuid
    intent_sha256: Sha256
    terminal_proof_sha256: Sha256


@dataclass(frozen=True, slots=True)
class GenerationObserved(Record):
    kind: Literal["OBSERVED"]
    generation_id: Positive
    epoch: Positive
    status: GenerationStatus
    revision: Positive
    binding_sha256: Sha256
    issuer_receipt_sha256: Sha256
    signed_observation: DatabaseObservation


@dataclass(frozen=True, slots=True)
class ActivationActive(Record):
    kind: Literal["ACTIVE"]
    runtime_activation_binding: RuntimeActivationBinding
    controller_observation: ControlObservation
    database_admission_observation: DatabaseObservation


@dataclass(frozen=True, slots=True)
class ActivationUnavailable(Record):
    kind: Literal["UNAVAILABLE"]
    code: RefusalCode


@dataclass(frozen=True, slots=True)
class TypePacket(Record):
    protocol: Literal["maezo.d7d-stage1-types.proposed.v1"]
    native_request: NativeRequest
    native_receipt: NativeReceipt
    trust_profile: TrustProfile
    control_observation: ControlObservation
    database_observation: DatabaseObservation
    generation: RuntimeAdmissionGeneration
    intent: AdmittedExternalIntent
    activation: ActivationRead


type SourceMapping = NoSource | LockedSource | HumanSource

type NativeRequest = DeployRequest | GrantRequest

type NativeResultBody = DeployResultBody | GrantResultBody

type SignedObservation = ControlObservation | DatabaseObservation

type IssuerRequestBody = IssuerPrepare | IssuerCandidate | IssuerOpen | IssuerRetire

type AdmittedExternalIntent = RunTaskIntent | StopTaskIntent

type NativeResult = NativeCommitted | NativeReplay | Refused

type ReceiptRead = ReceiptFound | ReceiptNotFound | Refused

type ExternalOutcome = ExternalAcknowledged | ExternalPending | ExternalSettled | Refused

type GenerationRead = GenerationObserved | Refused

type ActivationRead = ActivationActive | ActivationUnavailable

# Closed schema constants, mechanically rendered; no file or network read on import.
_DEFINITIONS: dict[str, Any] = {
    "Id": {"type": "string", "minLength": 1, "maxLength": 255, "pattern": "^[^\\x00-\\x20\\x7f-\\x9f*]+$"},
    "Text": {"type": "string", "minLength": 1, "maxLength": 255, "pattern": "^[^\\x00-\\x1f\\x7f-\\x9f]+$"},
    "EmptyOrId": {"oneOf": [{"const": ""}, {"$ref": "#/$defs/Id"}]},
    "Sha256": {"type": "string", "minLength": 64, "maxLength": 64, "pattern": "^[0-9a-f]{64}$"},
    "GitOid": {"type": "string", "minLength": 40, "maxLength": 40, "pattern": "^[0-9a-f]{40}$"},
    "Uuid": {
        "type": "string",
        "minLength": 36,
        "maxLength": 36,
        "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    },
    "UInt": {"type": "integer", "minimum": 0, "maximum": 9007199254740991},
    "Positive": {"type": "integer", "minimum": 1, "maximum": 9007199254740991},
    "Oid": {"type": "integer", "minimum": 1, "maximum": 4294967295},
    "Port": {"type": "integer", "minimum": 1, "maximum": 65535},
    "Version": {"type": "integer", "minimum": 1, "maximum": 2147483647},
    "Bool": {"type": "boolean"},
    "Bytes": {"type": "string", "minLength": 1, "maxLength": 1398102, "pattern": "^[A-Za-z0-9_-]+$"},
    "Challenge": {"type": "string", "minLength": 43, "maxLength": 43, "pattern": "^[A-Za-z0-9_-]{43}$"},
    "PublicKey": {"type": "string", "minLength": 43, "maxLength": 43, "pattern": "^[A-Za-z0-9_-]{43}$"},
    "Signature": {"type": "string", "minLength": 86, "maxLength": 86, "pattern": "^[A-Za-z0-9_-]{86}$"},
    "ResourceName": {
        "type": "string",
        "minLength": 1,
        "maxLength": 255,
        "pattern": "^(?!/)(?!.*(?:^|/)\\.\\.?/)(?!.*//)[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\\.(?:bpmn|dmn)$",
    },
    "Arn": {
        "type": "string",
        "minLength": 1,
        "maxLength": 255,
        "pattern": "^arn:[a-z0-9-]+:[a-z0-9-]+:[a-z0-9-]*:[0-9]*:[^\\s*]+$",
    },
    "GenerationPurpose": {"enum": ["candidate", "runtime"]},
    "GenerationStatus": {"enum": ["PREPARED", "OPEN", "RETIRED"]},
    "JournalState": {
        "enum": [
            "INTENT",
            "RESERVED",
            "DISPATCHED_UNKNOWN",
            "ACKNOWLEDGED",
            "RECONCILING",
            "SETTLED",
            "REJECTED",
            "CANCELLED_UNSENT",
        ]
    },
    "SettlementClass": {"enum": ["CURRENT_LIVE_TRACKED", "RETIRED_RESOURCES_TERMINAL", "NO_EFFECT"]},
    "ControllerState": {
        "enum": [
            "REVIEWED",
            "LEASED",
            "START_FENCED",
            "DB_FENCED",
            "MIGRATION_VERIFIED",
            "DEPLOY_ADMITTED",
            "DEPLOY_COMMITTED",
            "BOUNDARY_FROZEN",
            "GRANT_ADMITTED",
            "GRANT_COMMITTED",
            "PROVISIONER_DISPOSED",
            "CANDIDATE_ADMITTED",
            "CANDIDATE_VALIDATED",
            "CANDIDATE_RETIRED",
            "ACTIVATION_DECIDED",
            "RESTORING",
            "RESTORED",
            "READINESS_VERIFIED",
            "ACTIVE",
            "RECOVERY_REQUIRED",
        ]
    },
    "RefusalCode": {
        "enum": [
            "AUTH_REFUSED",
            "SCOPE_REFUSED",
            "PURPOSE_REFUSED",
            "STALE_EPOCH",
            "STALE_GENERATION",
            "REQUEST_CONFLICT",
            "PRECONDITION_MISMATCH",
            "INVALID_BODY",
            "UNAVAILABLE",
            "PENDING_UNKNOWN",
            "UNSUPPORTED_ADAPTER",
        ]
    },
    "DatabaseBinding": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "endpoint_host",
            "endpoint_port",
            "ca_sha256",
            "server_identity",
            "database_name",
            "database_oid",
            "schema_name",
            "schema_oid",
            "database_incarnation",
        ],
        "properties": {
            "endpoint_host": {"$ref": "#/$defs/Id"},
            "endpoint_port": {"$ref": "#/$defs/Port"},
            "ca_sha256": {"$ref": "#/$defs/Sha256"},
            "server_identity": {"$ref": "#/$defs/Id"},
            "database_name": {"$ref": "#/$defs/Id"},
            "database_oid": {"$ref": "#/$defs/Oid"},
            "schema_name": {"$ref": "#/$defs/Id"},
            "schema_oid": {"$ref": "#/$defs/Oid"},
            "database_incarnation": {"$ref": "#/$defs/Uuid"},
        },
    },
    "Scope": {
        "type": "object",
        "additionalProperties": False,
        "required": ["account", "region", "tenant", "environment", "engine_name", "database_binding"],
        "properties": {
            "account": {"type": "string", "minLength": 12, "maxLength": 12, "pattern": "^[0-9]{12}$"},
            "region": {
                "type": "string",
                "minLength": 1,
                "maxLength": 255,
                "pattern": "^[a-z]{2}(?:-[a-z]+)+-[0-9]+$",
            },
            "tenant": {"$ref": "#/$defs/Id"},
            "environment": {"$ref": "#/$defs/Id"},
            "engine_name": {"$ref": "#/$defs/Id"},
            "database_binding": {"$ref": "#/$defs/DatabaseBinding"},
        },
    },
    "BlobRef": {
        "type": "object",
        "additionalProperties": False,
        "required": ["sha256", "byte_length"],
        "properties": {"sha256": {"$ref": "#/$defs/Sha256"}, "byte_length": {"$ref": "#/$defs/Positive"}},
    },
    "ReceiptKey": {
        "type": "object",
        "additionalProperties": False,
        "required": ["tenant", "environment", "engine_name", "operation", "run_id"],
        "properties": {
            "tenant": {"$ref": "#/$defs/Id"},
            "environment": {"$ref": "#/$defs/Id"},
            "engine_name": {"$ref": "#/$defs/Id"},
            "operation": {"enum": ["deploy", "grant"]},
            "run_id": {"$ref": "#/$defs/Uuid"},
        },
    },
    "ReceiptRef": {
        "type": "object",
        "additionalProperties": False,
        "required": ["receipt_key", "request_sha256", "result_sha256"],
        "properties": {
            "receipt_key": {"$ref": "#/$defs/ReceiptKey"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "result_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "EngineIdentity": {
        "type": "object",
        "additionalProperties": False,
        "required": ["tenant", "environment", "workload", "workload_version", "issuer", "subject", "origin"],
        "properties": {
            "tenant": {"$ref": "#/$defs/Id"},
            "environment": {"$ref": "#/$defs/Id"},
            "workload": {"$ref": "#/$defs/Id"},
            "workload_version": {"$ref": "#/$defs/Id"},
            "issuer": {"$ref": "#/$defs/Id"},
            "subject": {"$ref": "#/$defs/Id"},
            "origin": {"const": "verified_mtls"},
        },
    },
    "EngineTarget": {
        "type": "object",
        "additionalProperties": False,
        "required": ["process_key", "process_version", "definition_id", "topic", "message"],
        "properties": {
            "process_key": {"$ref": "#/$defs/Id"},
            "process_version": {"$ref": "#/$defs/Version"},
            "definition_id": {"$ref": "#/$defs/Id"},
            "topic": {"$ref": "#/$defs/EmptyOrId"},
            "message": {"$ref": "#/$defs/EmptyOrId"},
        },
    },
    "CertificateBinding": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "certificate_sha256",
            "spki_sha256",
            "issuer_dn",
            "subject_dn",
            "uri_san",
            "not_before_ms",
            "expires_at_ms",
        ],
        "properties": {
            "certificate_sha256": {"$ref": "#/$defs/Sha256"},
            "spki_sha256": {"$ref": "#/$defs/Sha256"},
            "issuer_dn": {"$ref": "#/$defs/Id"},
            "subject_dn": {"$ref": "#/$defs/Text"},
            "uri_san": {"$ref": "#/$defs/Id"},
            "not_before_ms": {"$ref": "#/$defs/UInt"},
            "expires_at_ms": {"$ref": "#/$defs/Positive"},
        },
    },
    "ContentReleaseRef": {
        "type": "object",
        "additionalProperties": False,
        "required": ["release_id", "reviewed_source", "review_artifact_sha256"],
        "properties": {
            "release_id": {"$ref": "#/$defs/Id"},
            "reviewed_source": {"$ref": "#/$defs/GitOid"},
            "review_artifact_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "ParsedDefinition": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "key"],
        "properties": {"kind": {"enum": ["process", "decision", "drd"]}, "key": {"$ref": "#/$defs/Id"}},
    },
    "DeployResource": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "kind", "sha256", "byte_length", "expected_definitions", "content_release_refs"],
        "properties": {
            "name": {"$ref": "#/$defs/ResourceName"},
            "kind": {"enum": ["BPMN", "DMN"]},
            "sha256": {"$ref": "#/$defs/Sha256"},
            "byte_length": {"$ref": "#/$defs/Positive"},
            "expected_definitions": {
                "type": "array",
                "items": {"$ref": "#/$defs/ParsedDefinition"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "content_release_refs": {
                "type": "array",
                "items": {"$ref": "#/$defs/ContentReleaseRef"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
        },
    },
    "DeployPayload": {
        "type": "object",
        "additionalProperties": False,
        "required": ["tenant", "deployment_name", "resources", "expected_deployment_inventory_sha256"],
        "properties": {
            "tenant": {"$ref": "#/$defs/Id"},
            "deployment_name": {"$ref": "#/$defs/Id"},
            "resources": {
                "type": "array",
                "items": {"$ref": "#/$defs/DeployResource"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "expected_deployment_inventory_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "Grant": {
        "type": "object",
        "additionalProperties": False,
        "required": ["engine_user", "resource", "resource_id", "permissions"],
        "properties": {
            "engine_user": {"$ref": "#/$defs/Id"},
            "resource": {"enum": ["PROCESS_DEFINITION", "PROCESS_INSTANCE"]},
            "resource_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 255,
                "pattern": "^(?:\\*|[^\\x00-\\x20\\x7f-\\x9f*]+)$",
            },
            "permissions": {
                "type": "array",
                "items": {
                    "enum": [
                        "READ",
                        "CREATE_INSTANCE",
                        "READ_INSTANCE",
                        "READ_HISTORY",
                        "UPDATE_INSTANCE",
                        "CREATE",
                    ]
                },
                "minItems": 1,
                "maxItems": 6,
                "uniqueItems": True,
            },
        },
    },
    "GrantSource": {
        "type": "object",
        "additionalProperties": False,
        "required": ["engine_user", "resource", "resource_id", "capability_digests"],
        "properties": {
            "engine_user": {"$ref": "#/$defs/Id"},
            "resource": {"enum": ["PROCESS_DEFINITION", "PROCESS_INSTANCE"]},
            "resource_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 255,
                "pattern": "^(?:\\*|[^\\x00-\\x20\\x7f-\\x9f*]+)$",
            },
            "capability_digests": {
                "type": "array",
                "items": {"$ref": "#/$defs/Sha256"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
        },
    },
    "AttestationMapping": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "source_variable", "human_task_definition"],
        "properties": {
            "name": {"$ref": "#/$defs/Id"},
            "source_variable": {"$ref": "#/$defs/Id"},
            "human_task_definition": {"$ref": "#/$defs/EmptyOrId"},
        },
    },
    "NoSource": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "source_target", "source_worker_id", "attestations"],
        "properties": {
            "kind": {"const": "none"},
            "source_target": {"type": "null"},
            "source_worker_id": {"const": ""},
            "attestations": {
                "type": "array",
                "items": {"$ref": "#/$defs/AttestationMapping"},
                "minItems": 0,
                "maxItems": 0,
                "uniqueItems": True,
            },
        },
    },
    "LockedSource": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "source_target", "source_worker_id", "attestations"],
        "properties": {
            "kind": {"const": "locked_external"},
            "source_target": {"$ref": "#/$defs/EngineTarget"},
            "source_worker_id": {"$ref": "#/$defs/Id"},
            "attestations": {
                "type": "array",
                "items": {"$ref": "#/$defs/AttestationMapping"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
        },
    },
    "HumanSource": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "source_target", "source_worker_id", "attestations"],
        "properties": {
            "kind": {"const": "completed_human"},
            "source_target": {"$ref": "#/$defs/EngineTarget"},
            "source_worker_id": {"const": ""},
            "attestations": {
                "type": "array",
                "items": {"$ref": "#/$defs/AttestationMapping"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
        },
    },
    "SourceMapping": {
        "oneOf": [
            {"$ref": "#/$defs/NoSource"},
            {"$ref": "#/$defs/LockedSource"},
            {"$ref": "#/$defs/HumanSource"},
        ]
    },
    "CapabilityMapping": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "capability_sha256",
            "registered_schema_id",
            "registered_schema_sha256",
            "target",
            "worker_id",
            "source",
        ],
        "properties": {
            "capability_sha256": {"$ref": "#/$defs/Sha256"},
            "registered_schema_id": {"$ref": "#/$defs/Id"},
            "registered_schema_sha256": {"$ref": "#/$defs/Sha256"},
            "target": {"$ref": "#/$defs/EngineTarget"},
            "worker_id": {"$ref": "#/$defs/EmptyOrId"},
            "source": {"$ref": "#/$defs/SourceMapping"},
        },
    },
    "WorkloadPlan": {
        "type": "object",
        "additionalProperties": False,
        "required": ["engine_user", "identity", "capabilities"],
        "properties": {
            "engine_user": {"$ref": "#/$defs/Id"},
            "identity": {"$ref": "#/$defs/EngineIdentity"},
            "capabilities": {
                "type": "array",
                "items": {"$ref": "#/$defs/CapabilityMapping"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
        },
    },
    "Operators": {
        "type": "object",
        "additionalProperties": False,
        "required": ["bootstrap", "deployment"],
        "properties": {"bootstrap": {"$ref": "#/$defs/Id"}, "deployment": {"$ref": "#/$defs/Id"}},
    },
    "CompilerPlan": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "execution_authorized",
            "boundary_sha256",
            "tenant",
            "environment",
            "engine_name",
            "operators",
            "preserve_existing_users",
            "definitions",
            "grants",
            "grant_sources",
            "required_readbacks",
        ],
        "properties": {
            "protocol": {"const": "maezo.engine-provisioning-plan.v1"},
            "execution_authorized": {"const": False},
            "boundary_sha256": {"$ref": "#/$defs/Sha256"},
            "tenant": {"$ref": "#/$defs/Id"},
            "environment": {"$ref": "#/$defs/Id"},
            "engine_name": {"$ref": "#/$defs/Id"},
            "operators": {"$ref": "#/$defs/Operators"},
            "preserve_existing_users": {
                "type": "array",
                "items": {"$ref": "#/$defs/Id"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "definitions": {
                "type": "array",
                "items": {"$ref": "#/$defs/EngineTarget"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "grants": {
                "type": "array",
                "items": {"$ref": "#/$defs/Grant"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "grant_sources": {
                "type": "array",
                "items": {"$ref": "#/$defs/GrantSource"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "required_readbacks": {
                "const": [
                    "exclusive_runtime_fence",
                    "separate_privileged_database_role",
                    "native_schema_and_artifacts",
                    "all_existing_authorizations_preserved",
                    "actual_certificate_and_mount_validation",
                    "one_transaction_receipt",
                    "bootstrap_credential_disposal",
                    "secured_engine_readiness",
                ]
            },
        },
    },
    "GrantPayload": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "deployment_receipt",
            "final_boundary_manifest",
            "compiler_plan",
            "compiler_plan_sha256",
            "workload_plans",
            "expected_identity_authorization_inventory_sha256",
        ],
        "properties": {
            "deployment_receipt": {"$ref": "#/$defs/ReceiptRef"},
            "final_boundary_manifest": {"$ref": "#/$defs/BlobRef"},
            "compiler_plan": {"$ref": "#/$defs/CompilerPlan"},
            "compiler_plan_sha256": {"$ref": "#/$defs/Sha256"},
            "workload_plans": {
                "type": "array",
                "items": {"$ref": "#/$defs/WorkloadPlan"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "expected_identity_authorization_inventory_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "DeployRequest": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "scope",
            "run_id",
            "source",
            "tree",
            "native_image_sha256",
            "native_jar_sha256",
            "trust_profile_sha256",
            "artifact_set_sha256",
            "expected_before_state_sha256",
            "operation",
            "payload",
        ],
        "properties": {
            "protocol": {"const": "maezo.native-provisioning-request.v1"},
            "scope": {"$ref": "#/$defs/Scope"},
            "run_id": {"$ref": "#/$defs/Uuid"},
            "source": {"$ref": "#/$defs/GitOid"},
            "tree": {"$ref": "#/$defs/GitOid"},
            "native_image_sha256": {"$ref": "#/$defs/Sha256"},
            "native_jar_sha256": {"$ref": "#/$defs/Sha256"},
            "trust_profile_sha256": {"$ref": "#/$defs/Sha256"},
            "artifact_set_sha256": {"$ref": "#/$defs/Sha256"},
            "expected_before_state_sha256": {"$ref": "#/$defs/Sha256"},
            "operation": {"const": "deploy"},
            "payload": {"$ref": "#/$defs/DeployPayload"},
        },
    },
    "GrantRequest": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "scope",
            "run_id",
            "source",
            "tree",
            "native_image_sha256",
            "native_jar_sha256",
            "trust_profile_sha256",
            "artifact_set_sha256",
            "expected_before_state_sha256",
            "operation",
            "payload",
        ],
        "properties": {
            "protocol": {"const": "maezo.native-provisioning-request.v1"},
            "scope": {"$ref": "#/$defs/Scope"},
            "run_id": {"$ref": "#/$defs/Uuid"},
            "source": {"$ref": "#/$defs/GitOid"},
            "tree": {"$ref": "#/$defs/GitOid"},
            "native_image_sha256": {"$ref": "#/$defs/Sha256"},
            "native_jar_sha256": {"$ref": "#/$defs/Sha256"},
            "trust_profile_sha256": {"$ref": "#/$defs/Sha256"},
            "artifact_set_sha256": {"$ref": "#/$defs/Sha256"},
            "expected_before_state_sha256": {"$ref": "#/$defs/Sha256"},
            "operation": {"const": "grant"},
            "payload": {"$ref": "#/$defs/GrantPayload"},
        },
    },
    "NativeRequest": {"oneOf": [{"$ref": "#/$defs/DeployRequest"}, {"$ref": "#/$defs/GrantRequest"}]},
    "StoredResource": {
        "type": "object",
        "additionalProperties": False,
        "required": ["name", "kind", "sha256", "byte_length", "native_resource_id"],
        "properties": {
            "name": {"$ref": "#/$defs/ResourceName"},
            "kind": {"enum": ["BPMN", "DMN"]},
            "sha256": {"$ref": "#/$defs/Sha256"},
            "byte_length": {"$ref": "#/$defs/Positive"},
            "native_resource_id": {"$ref": "#/$defs/Id"},
        },
    },
    "GeneratedDefinition": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "key", "definition_id", "version", "deployment_id", "resource_name", "tenant"],
        "properties": {
            "kind": {"enum": ["process", "decision", "drd"]},
            "key": {"$ref": "#/$defs/Id"},
            "definition_id": {"$ref": "#/$defs/Id"},
            "version": {"$ref": "#/$defs/Version"},
            "deployment_id": {"$ref": "#/$defs/Id"},
            "resource_name": {"$ref": "#/$defs/ResourceName"},
            "tenant": {"$ref": "#/$defs/Id"},
        },
    },
    "DeployResultBody": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "operation",
            "tenant",
            "deployment_id",
            "deployment_name",
            "resources",
            "definitions",
            "before_inventory_sha256",
            "after_inventory_sha256",
            "allowed_native_delta_sha256",
            "protected_state_sha256",
        ],
        "properties": {
            "protocol": {"const": "maezo.native-provisioning-result.v1"},
            "operation": {"const": "deploy"},
            "tenant": {"$ref": "#/$defs/Id"},
            "deployment_id": {"$ref": "#/$defs/Id"},
            "deployment_name": {"$ref": "#/$defs/Id"},
            "resources": {
                "type": "array",
                "items": {"$ref": "#/$defs/StoredResource"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "definitions": {
                "type": "array",
                "items": {"$ref": "#/$defs/GeneratedDefinition"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "before_inventory_sha256": {"$ref": "#/$defs/Sha256"},
            "after_inventory_sha256": {"$ref": "#/$defs/Sha256"},
            "allowed_native_delta_sha256": {"$ref": "#/$defs/Sha256"},
            "protected_state_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "IdentityResult": {
        "type": "object",
        "additionalProperties": False,
        "required": ["engine_user", "identity_sha256", "disposition"],
        "properties": {
            "engine_user": {"$ref": "#/$defs/Id"},
            "identity_sha256": {"$ref": "#/$defs/Sha256"},
            "disposition": {"enum": ["CREATED", "PRESERVED"]},
        },
    },
    "GrantResultRow": {
        "type": "object",
        "additionalProperties": False,
        "required": ["authorization_id", "grant", "disposition"],
        "properties": {
            "authorization_id": {"$ref": "#/$defs/Id"},
            "grant": {"$ref": "#/$defs/Grant"},
            "disposition": {"enum": ["CREATED", "PRESERVED"]},
        },
    },
    "GrantResultBody": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "operation",
            "deployment_receipt",
            "boundary_sha256",
            "compiler_plan_sha256",
            "identities",
            "grants",
            "before_inventory_sha256",
            "after_inventory_sha256",
            "allowed_native_delta_sha256",
            "protected_state_sha256",
        ],
        "properties": {
            "protocol": {"const": "maezo.native-provisioning-result.v1"},
            "operation": {"const": "grant"},
            "deployment_receipt": {"$ref": "#/$defs/ReceiptRef"},
            "boundary_sha256": {"$ref": "#/$defs/Sha256"},
            "compiler_plan_sha256": {"$ref": "#/$defs/Sha256"},
            "identities": {
                "type": "array",
                "items": {"$ref": "#/$defs/IdentityResult"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "grants": {
                "type": "array",
                "items": {"$ref": "#/$defs/GrantResultRow"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "before_inventory_sha256": {"$ref": "#/$defs/Sha256"},
            "after_inventory_sha256": {"$ref": "#/$defs/Sha256"},
            "allowed_native_delta_sha256": {"$ref": "#/$defs/Sha256"},
            "protected_state_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "NativeResultBody": {
        "oneOf": [{"$ref": "#/$defs/DeployResultBody"}, {"$ref": "#/$defs/GrantResultBody"}]
    },
    "NativeActor": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "login_name",
            "login_oid",
            "session_user",
            "current_user",
            "task_arn",
            "task_definition_revision",
            "purpose",
        ],
        "properties": {
            "login_name": {"$ref": "#/$defs/Id"},
            "login_oid": {"$ref": "#/$defs/Oid"},
            "session_user": {"$ref": "#/$defs/Id"},
            "current_user": {"$ref": "#/$defs/Id"},
            "task_arn": {"$ref": "#/$defs/Arn"},
            "task_definition_revision": {"$ref": "#/$defs/Arn"},
            "purpose": {"enum": ["deploy", "grant", "receipt"]},
        },
    },
    "NativeReceipt": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "receipt_key",
            "scope",
            "request_sha256",
            "result_sha256",
            "canonical_result_bytes",
            "source",
            "tree",
            "image_sha256",
            "jar_sha256",
            "trust_profile_sha256",
            "permit_id",
            "commit_epoch",
            "native_actor",
            "recorded_before_commit_at_ms",
            "provisioning_schema_version",
        ],
        "properties": {
            "protocol": {"const": "maezo.native-provisioning-receipt.v1"},
            "receipt_key": {"$ref": "#/$defs/ReceiptKey"},
            "scope": {"$ref": "#/$defs/Scope"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "result_sha256": {"$ref": "#/$defs/Sha256"},
            "canonical_result_bytes": {"$ref": "#/$defs/Bytes"},
            "source": {"$ref": "#/$defs/GitOid"},
            "tree": {"$ref": "#/$defs/GitOid"},
            "image_sha256": {"$ref": "#/$defs/Sha256"},
            "jar_sha256": {"$ref": "#/$defs/Sha256"},
            "trust_profile_sha256": {"$ref": "#/$defs/Sha256"},
            "permit_id": {"$ref": "#/$defs/Uuid"},
            "commit_epoch": {"$ref": "#/$defs/Positive"},
            "native_actor": {"$ref": "#/$defs/NativeActor"},
            "recorded_before_commit_at_ms": {"$ref": "#/$defs/UInt"},
            "provisioning_schema_version": {"$ref": "#/$defs/Version"},
        },
    },
    "ReceiptQuery": {
        "type": "object",
        "additionalProperties": False,
        "required": ["protocol", "scope", "receipt_key", "request_sha256"],
        "properties": {
            "protocol": {"const": "maezo.native-provisioning-receipt-query.v1"},
            "scope": {"$ref": "#/$defs/Scope"},
            "receipt_key": {"$ref": "#/$defs/ReceiptKey"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "Limits": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "max_observation_age_ms",
            "max_clock_disagreement_ms",
            "lease_term_ms",
            "heartbeat_ms",
            "permit_admission_validity_ms",
            "native_tx_deadline_ms",
            "observer_request_deadline_ms",
        ],
        "properties": {
            "max_observation_age_ms": {"const": 15000},
            "max_clock_disagreement_ms": {"const": 2000},
            "lease_term_ms": {"const": 120000},
            "heartbeat_ms": {"const": 10000},
            "permit_admission_validity_ms": {"const": 60000},
            "native_tx_deadline_ms": {"const": 30000},
            "observer_request_deadline_ms": {"const": 5000},
        },
    },
    "ObserverKey": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "key_id",
            "issuer",
            "purpose",
            "subject",
            "audience",
            "algorithm",
            "public_key",
            "not_before_ms",
            "expires_at_ms",
            "lifecycle",
            "predecessor_key_id",
            "revoked_at_ms",
        ],
        "properties": {
            "key_id": {"$ref": "#/$defs/Id"},
            "issuer": {"$ref": "#/$defs/Id"},
            "purpose": {"enum": ["control-observation", "database-observation"]},
            "subject": {"$ref": "#/$defs/Id"},
            "audience": {"$ref": "#/$defs/Id"},
            "algorithm": {"const": "Ed25519"},
            "public_key": {"$ref": "#/$defs/PublicKey"},
            "not_before_ms": {"$ref": "#/$defs/UInt"},
            "expires_at_ms": {"$ref": "#/$defs/Positive"},
            "lifecycle": {"enum": ["ACTIVE", "RETIRED", "REVOKED"]},
            "predecessor_key_id": {"oneOf": [{"$ref": "#/$defs/Id"}, {"type": "null"}]},
            "revoked_at_ms": {"oneOf": [{"$ref": "#/$defs/UInt"}, {"type": "null"}]},
        },
    },
    "TrustProfile": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "profile_id",
            "scope",
            "release_source",
            "release_tree",
            "verifier_image_sha256",
            "verifier_jar_sha256",
            "limits",
            "control_keys",
            "database_keys",
            "inventory_bounds_sha256",
        ],
        "properties": {
            "protocol": {"const": "maezo.provisioning-trust-profile.v1"},
            "profile_id": {"$ref": "#/$defs/Id"},
            "scope": {"$ref": "#/$defs/Scope"},
            "release_source": {"$ref": "#/$defs/GitOid"},
            "release_tree": {"$ref": "#/$defs/GitOid"},
            "verifier_image_sha256": {"$ref": "#/$defs/Sha256"},
            "verifier_jar_sha256": {"$ref": "#/$defs/Sha256"},
            "limits": {"$ref": "#/$defs/Limits"},
            "control_keys": {
                "type": "array",
                "items": {"$ref": "#/$defs/ObserverKey"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "database_keys": {
                "type": "array",
                "items": {"$ref": "#/$defs/ObserverKey"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "inventory_bounds_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "InventoryBound": {
        "type": "object",
        "additionalProperties": False,
        "required": ["collection_path", "max_entries"],
        "properties": {
            "collection_path": {"enum": list(INVENTORY_FAMILIES)},
            "max_entries": {"type": "integer", "minimum": 0, "maximum": 1024},
        },
    },
    "InventoryBounds": {
        "type": "object",
        "additionalProperties": False,
        "required": ["protocol", "scope", "bounds"],
        "properties": {
            "protocol": {"const": "maezo.provisioning-inventory-bounds.v2"},
            "scope": {"$ref": "#/$defs/Scope"},
            "bounds": {
                "type": "array",
                "items": {"$ref": "#/$defs/InventoryBound"},
                "minItems": 35,
                "maxItems": 35,
                "uniqueItems": True,
            },
        },
    },
    "NetworkBinding": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "vpc_id",
            "subnet_ids",
            "security_group_ids",
            "assign_public_ip",
            "network_policy_sha256",
        ],
        "properties": {
            "vpc_id": {"$ref": "#/$defs/Id"},
            "subnet_ids": {
                "type": "array",
                "items": {"$ref": "#/$defs/Id"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "security_group_ids": {
                "type": "array",
                "items": {"$ref": "#/$defs/Id"},
                "minItems": 1,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "assign_public_ip": {"const": "DISABLED"},
            "network_policy_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "SecretVersion": {
        "type": "object",
        "additionalProperties": False,
        "required": ["resource_arn", "version_id"],
        "properties": {"resource_arn": {"$ref": "#/$defs/Arn"}, "version_id": {"$ref": "#/$defs/Id"}},
    },
    "TaskDefinitionBinding": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "arn",
            "revision",
            "registration_sha256",
            "image_sha256",
            "config_set_sha256",
            "execution_role",
            "task_role",
            "network_binding",
        ],
        "properties": {
            "arn": {"$ref": "#/$defs/Arn"},
            "revision": {"$ref": "#/$defs/Version"},
            "registration_sha256": {"$ref": "#/$defs/Sha256"},
            "image_sha256": {"$ref": "#/$defs/Sha256"},
            "config_set_sha256": {"$ref": "#/$defs/Sha256"},
            "execution_role": {"$ref": "#/$defs/Arn"},
            "task_role": {"$ref": "#/$defs/Arn"},
            "network_binding": {"$ref": "#/$defs/NetworkBinding"},
        },
    },
    "GenerationCore": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "generation_id",
            "scope",
            "epoch",
            "purpose",
            "image_sha256",
            "task_definition_revision",
            "config_set_sha256",
            "task_role",
            "execution_role",
            "network_binding",
            "immutable_secret_resource_version",
            "credential_binding_sha256",
            "login_oid",
            "login_name",
        ],
        "properties": {
            "generation_id": {"$ref": "#/$defs/Positive"},
            "scope": {"$ref": "#/$defs/Scope"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "purpose": {"$ref": "#/$defs/GenerationPurpose"},
            "image_sha256": {"$ref": "#/$defs/Sha256"},
            "task_definition_revision": {"$ref": "#/$defs/TaskDefinitionBinding"},
            "config_set_sha256": {"$ref": "#/$defs/Sha256"},
            "task_role": {"$ref": "#/$defs/Arn"},
            "execution_role": {"$ref": "#/$defs/Arn"},
            "network_binding": {"$ref": "#/$defs/NetworkBinding"},
            "immutable_secret_resource_version": {"$ref": "#/$defs/SecretVersion"},
            "credential_binding_sha256": {"$ref": "#/$defs/Sha256"},
            "login_oid": {"$ref": "#/$defs/Oid"},
            "login_name": {"$ref": "#/$defs/Id"},
        },
    },
    "RuntimeAdmissionGeneration": {
        "type": "object",
        "additionalProperties": False,
        "required": ["binding", "activation_decision_sha256", "status", "revision"],
        "properties": {
            "binding": {"$ref": "#/$defs/GenerationCore"},
            "activation_decision_sha256": {"$ref": "#/$defs/Sha256"},
            "status": {"$ref": "#/$defs/GenerationStatus"},
            "revision": {"$ref": "#/$defs/Positive"},
        },
    },
    "CredentialSession": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "generation_id",
            "principal_arn",
            "session_arn",
            "session_id",
            "issued_at_ms",
            "expires_at_ms",
            "effective_policy_sha256",
            "resource_scope_sha256",
            "broker_operation_id",
        ],
        "properties": {
            "generation_id": {"$ref": "#/$defs/Positive"},
            "principal_arn": {"$ref": "#/$defs/Arn"},
            "session_arn": {"$ref": "#/$defs/Arn"},
            "session_id": {"$ref": "#/$defs/Id"},
            "issued_at_ms": {"$ref": "#/$defs/UInt"},
            "expires_at_ms": {"$ref": "#/$defs/Positive"},
            "effective_policy_sha256": {"$ref": "#/$defs/Sha256"},
            "resource_scope_sha256": {"$ref": "#/$defs/Sha256"},
            "broker_operation_id": {"$ref": "#/$defs/Uuid"},
        },
    },
    "DispatcherReservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "invocation_id",
            "worker_subject",
            "credential_session",
            "reserved_at_ms",
            "intent_sha256",
        ],
        "properties": {
            "invocation_id": {"$ref": "#/$defs/Uuid"},
            "worker_subject": {"$ref": "#/$defs/Id"},
            "credential_session": {"$ref": "#/$defs/CredentialSession"},
            "reserved_at_ms": {"$ref": "#/$defs/UInt"},
            "intent_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "ProviderFailure": {
        "type": "object",
        "additionalProperties": False,
        "required": ["resource_id", "code", "private_diagnostic_sha256"],
        "properties": {
            "resource_id": {"oneOf": [{"$ref": "#/$defs/Id"}, {"type": "null"}]},
            "code": {"enum": ["CAPACITY", "PLACEMENT", "PERMISSION", "INVALID_REQUEST", "UNCLASSIFIED"]},
            "private_diagnostic_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "ProviderOutcome": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "provider_request_id",
            "response_sha256",
            "returned_resource_ids",
            "failures",
            "observed_at_ms",
        ],
        "properties": {
            "provider_request_id": {"$ref": "#/$defs/Id"},
            "response_sha256": {"$ref": "#/$defs/Sha256"},
            "returned_resource_ids": {
                "type": "array",
                "items": {"$ref": "#/$defs/Arn"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "failures": {
                "type": "array",
                "items": {"$ref": "#/$defs/ProviderFailure"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "observed_at_ms": {"$ref": "#/$defs/UInt"},
        },
    },
    "DispatchTerminalProof": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "invocation_id",
            "intent_sha256",
            "status",
            "observed_at_ms",
            "authenticated_observer_subject",
            "proof_sha256",
        ],
        "properties": {
            "invocation_id": {"$ref": "#/$defs/Uuid"},
            "intent_sha256": {"$ref": "#/$defs/Sha256"},
            "status": {"enum": ["COMPLETED", "CANCELLED_BEFORE_SEND", "TERMINATED_AND_AUTHORITY_RETIRED"]},
            "observed_at_ms": {"$ref": "#/$defs/UInt"},
            "authenticated_observer_subject": {"$ref": "#/$defs/Id"},
            "proof_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "ResourceTerminalProof": {
        "type": "object",
        "additionalProperties": False,
        "required": ["resource_id", "generation_id", "terminal_kind", "observation_sha256"],
        "properties": {
            "resource_id": {"$ref": "#/$defs/Id"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "terminal_kind": {
                "enum": [
                    "TASK_STOPPED",
                    "SERVICE_DELETED",
                    "TASK_SET_DELETED",
                    "ROLE_RETIRED",
                    "SECRET_DELIVERY_DISABLED",
                    "PRIVATE_MOUNT_DISPOSED",
                    "IMMUTABLE_OBJECT_PUBLISHED",
                    "NO_EFFECT",
                ]
            },
            "observation_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "Settlement": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "settlement_class",
            "dispatch_terminal",
            "resource_proofs",
            "managed_resource_registry_sha256",
            "settled_at_ms",
        ],
        "properties": {
            "settlement_class": {"$ref": "#/$defs/SettlementClass"},
            "dispatch_terminal": {"$ref": "#/$defs/DispatchTerminalProof"},
            "resource_proofs": {
                "type": "array",
                "items": {"$ref": "#/$defs/ResourceTerminalProof"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "managed_resource_registry_sha256": {"$ref": "#/$defs/Sha256"},
            "settled_at_ms": {"$ref": "#/$defs/UInt"},
        },
    },
    "JournalObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "external_operation_id",
            "logical_run_id",
            "epoch",
            "generation_id",
            "intent_sha256",
            "action",
            "state",
            "reservation",
            "provider_outcome",
            "settlement",
            "cancellation_unsent_proof",
        ],
        "properties": {
            "external_operation_id": {"$ref": "#/$defs/Uuid"},
            "logical_run_id": {"$ref": "#/$defs/Uuid"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "intent_sha256": {"$ref": "#/$defs/Sha256"},
            "action": {
                "enum": [
                    "RunTask",
                    "StopTask",
                    "PrepareGeneration",
                    "AdmitCandidateLogin",
                    "OpenRuntimeGeneration",
                    "RetireGeneration",
                    "RevokeNativePermit",
                    "PublishConfigSet",
                    "DisposeOwnedMount",
                    "StartTask",
                    "UpdateService",
                    "CreateTaskSet",
                    "UpdateTaskSet",
                    "DeleteTaskSet",
                    "SchedulingChange",
                    "AutomaticRollback",
                ]
            },
            "state": {"$ref": "#/$defs/JournalState"},
            "reservation": {"oneOf": [{"$ref": "#/$defs/DispatcherReservation"}, {"type": "null"}]},
            "provider_outcome": {"oneOf": [{"$ref": "#/$defs/ProviderOutcome"}, {"type": "null"}]},
            "settlement": {"oneOf": [{"$ref": "#/$defs/Settlement"}, {"type": "null"}]},
            "cancellation_unsent_proof": {
                "oneOf": [{"$ref": "#/$defs/DispatchTerminalProof"}, {"type": "null"}]
            },
        },
    },
    "ManagedResource": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "scope",
            "generation_id",
            "external_operation_id",
            "provider_resource_id",
            "task_definition_revision",
            "image_sha256",
            "config_set_sha256",
            "desired_state",
            "current_observed_state",
            "observation_sha256",
            "retired_terminal_proof_sha256",
        ],
        "properties": {
            "scope": {"$ref": "#/$defs/Scope"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "external_operation_id": {"$ref": "#/$defs/Uuid"},
            "provider_resource_id": {"$ref": "#/$defs/Arn"},
            "task_definition_revision": {"$ref": "#/$defs/TaskDefinitionBinding"},
            "image_sha256": {"$ref": "#/$defs/Sha256"},
            "config_set_sha256": {"$ref": "#/$defs/Sha256"},
            "desired_state": {"enum": ["READY", "STOPPED", "ABSENT"]},
            "current_observed_state": {
                "enum": ["PENDING", "RUNNING", "READY", "STOPPING", "STOPPED", "ABSENT"]
            },
            "observation_sha256": {"$ref": "#/$defs/Sha256"},
            "retired_terminal_proof_sha256": {"oneOf": [{"$ref": "#/$defs/Sha256"}, {"type": "null"}]},
        },
    },
    "TaskObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "task_arn",
            "generation_id",
            "task_definition",
            "image_sha256",
            "last_status",
            "desired_status",
            "standalone",
            "task_set_arn",
            "credential_session_ids",
            "config_set_sha256",
        ],
        "properties": {
            "task_arn": {"$ref": "#/$defs/Arn"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "task_definition": {"$ref": "#/$defs/TaskDefinitionBinding"},
            "image_sha256": {"$ref": "#/$defs/Sha256"},
            "last_status": {
                "enum": [
                    "PROVISIONING",
                    "PENDING",
                    "ACTIVATING",
                    "RUNNING",
                    "DEACTIVATING",
                    "STOPPING",
                    "DEPROVISIONING",
                    "STOPPED",
                ]
            },
            "desired_status": {"enum": ["RUNNING", "STOPPED"]},
            "standalone": {"$ref": "#/$defs/Bool"},
            "task_set_arn": {"oneOf": [{"$ref": "#/$defs/Arn"}, {"type": "null"}]},
            "credential_session_ids": {
                "type": "array",
                "items": {"$ref": "#/$defs/Id"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "config_set_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "ServiceDeployment": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "deployment_id",
            "task_definition",
            "desired_count",
            "running_count",
            "pending_count",
            "rollout_state",
        ],
        "properties": {
            "deployment_id": {"$ref": "#/$defs/Id"},
            "task_definition": {"$ref": "#/$defs/TaskDefinitionBinding"},
            "desired_count": {"$ref": "#/$defs/UInt"},
            "running_count": {"$ref": "#/$defs/UInt"},
            "pending_count": {"$ref": "#/$defs/UInt"},
            "rollout_state": {"enum": ["IN_PROGRESS", "COMPLETED", "FAILED"]},
        },
    },
    "TaskSetObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "task_set_arn",
            "generation_id",
            "task_definition",
            "status",
            "desired_count",
            "running_count",
            "pending_count",
        ],
        "properties": {
            "task_set_arn": {"$ref": "#/$defs/Arn"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "task_definition": {"$ref": "#/$defs/TaskDefinitionBinding"},
            "status": {"enum": ["PRIMARY", "ACTIVE", "DRAINING"]},
            "desired_count": {"$ref": "#/$defs/UInt"},
            "running_count": {"$ref": "#/$defs/UInt"},
            "pending_count": {"$ref": "#/$defs/UInt"},
        },
    },
    "ServiceObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "service_arn",
            "generation_id",
            "desired_count",
            "running_count",
            "pending_count",
            "deployments",
            "task_sets",
        ],
        "properties": {
            "service_arn": {"$ref": "#/$defs/Arn"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "desired_count": {"$ref": "#/$defs/UInt"},
            "running_count": {"$ref": "#/$defs/UInt"},
            "pending_count": {"$ref": "#/$defs/UInt"},
            "deployments": {
                "type": "array",
                "items": {"$ref": "#/$defs/ServiceDeployment"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "task_sets": {
                "type": "array",
                "items": {"$ref": "#/$defs/TaskSetObservation"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
        },
    },
    "StartPath": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "path_id",
            "actor_arn",
            "kind",
            "enabled",
            "qualified_adapter_sha256",
            "generation_id",
            "enforced_policy_sha256",
        ],
        "properties": {
            "path_id": {"$ref": "#/$defs/Id"},
            "actor_arn": {"$ref": "#/$defs/Arn"},
            "kind": {"enum": ["DISPATCHER", "CI", "OPERATOR", "SCHEDULER", "AUTOSCALING", "ROLLBACK"]},
            "enabled": {"$ref": "#/$defs/Bool"},
            "qualified_adapter_sha256": {"oneOf": [{"$ref": "#/$defs/Sha256"}, {"type": "null"}]},
            "generation_id": {"oneOf": [{"$ref": "#/$defs/Positive"}, {"type": "null"}]},
            "enforced_policy_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "ControlPayload": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "cluster_arn",
            "services",
            "task_definitions",
            "tasks",
            "desired_count",
            "running_count",
            "pending_count",
            "start_paths",
            "operation_record_revision",
            "controller_state",
            "principal_policy_inventory_sha256",
            "journal_revision",
            "pending_index_sha256",
            "journal",
            "live_dispatchers",
            "credential_sessions",
            "managed_resources",
            "complete",
            "pagination_sha256",
            "complete_read_proof_sha256",
        ],
        "properties": {
            "cluster_arn": {"$ref": "#/$defs/Arn"},
            "services": {
                "type": "array",
                "items": {"$ref": "#/$defs/ServiceObservation"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "task_definitions": {
                "type": "array",
                "items": {"$ref": "#/$defs/TaskDefinitionBinding"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "tasks": {
                "type": "array",
                "items": {"$ref": "#/$defs/TaskObservation"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "desired_count": {"$ref": "#/$defs/UInt"},
            "running_count": {"$ref": "#/$defs/UInt"},
            "pending_count": {"$ref": "#/$defs/UInt"},
            "start_paths": {
                "type": "array",
                "items": {"$ref": "#/$defs/StartPath"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "operation_record_revision": {"$ref": "#/$defs/Positive"},
            "controller_state": {"$ref": "#/$defs/ControllerState"},
            "principal_policy_inventory_sha256": {"$ref": "#/$defs/Sha256"},
            "journal_revision": {"$ref": "#/$defs/UInt"},
            "pending_index_sha256": {"$ref": "#/$defs/Sha256"},
            "journal": {
                "type": "array",
                "items": {"$ref": "#/$defs/JournalObservation"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "live_dispatchers": {
                "type": "array",
                "items": {"$ref": "#/$defs/DispatcherReservation"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "credential_sessions": {
                "type": "array",
                "items": {"$ref": "#/$defs/CredentialSession"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "managed_resources": {
                "type": "array",
                "items": {"$ref": "#/$defs/ManagedResource"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "complete": {"const": True},
            "pagination_sha256": {"$ref": "#/$defs/Sha256"},
            "complete_read_proof_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "RoleObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "oid",
            "name",
            "owned",
            "can_login",
            "superuser",
            "create_role",
            "create_db",
            "replication",
            "bypass_rls",
            "inherit",
            "generation_id",
            "effective_privileges_sha256",
        ],
        "properties": {
            "oid": {"$ref": "#/$defs/Oid"},
            "name": {"$ref": "#/$defs/Id"},
            "owned": {"$ref": "#/$defs/Bool"},
            "can_login": {"$ref": "#/$defs/Bool"},
            "superuser": {"$ref": "#/$defs/Bool"},
            "create_role": {"$ref": "#/$defs/Bool"},
            "create_db": {"$ref": "#/$defs/Bool"},
            "replication": {"$ref": "#/$defs/Bool"},
            "bypass_rls": {"$ref": "#/$defs/Bool"},
            "inherit": {"$ref": "#/$defs/Bool"},
            "generation_id": {"oneOf": [{"$ref": "#/$defs/Positive"}, {"type": "null"}]},
            "effective_privileges_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "MembershipObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": ["member_oid", "role_oid", "grantor_oid", "admin_option", "inherit_option", "set_option"],
        "properties": {
            "member_oid": {"$ref": "#/$defs/Oid"},
            "role_oid": {"$ref": "#/$defs/Oid"},
            "grantor_oid": {"$ref": "#/$defs/Oid"},
            "admin_option": {"$ref": "#/$defs/Bool"},
            "inherit_option": {"$ref": "#/$defs/Bool"},
            "set_option": {"$ref": "#/$defs/Bool"},
        },
    },
    "SessionObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "pid",
            "backend_start_unix_us",
            "login_oid",
            "current_role_oid",
            "database_oid",
            "generation_id",
            "owned",
            "state",
            "transaction_start_ms",
            "route_id",
        ],
        "properties": {
            "pid": {"$ref": "#/$defs/Positive"},
            "backend_start_unix_us": {"$ref": "#/$defs/UInt"},
            "login_oid": {"$ref": "#/$defs/Oid"},
            "current_role_oid": {"$ref": "#/$defs/Oid"},
            "database_oid": {"$ref": "#/$defs/Oid"},
            "generation_id": {"oneOf": [{"$ref": "#/$defs/Positive"}, {"type": "null"}]},
            "owned": {"$ref": "#/$defs/Bool"},
            "state": {
                "enum": [
                    "ACTIVE",
                    "IDLE",
                    "IDLE_IN_TRANSACTION",
                    "IDLE_IN_TRANSACTION_ABORTED",
                    "FASTPATH_FUNCTION_CALL",
                    "DISABLED",
                ]
            },
            "transaction_start_ms": {"oneOf": [{"$ref": "#/$defs/UInt"}, {"type": "null"}]},
            "route_id": {"$ref": "#/$defs/Id"},
        },
    },
    "PreparedTransaction": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "transaction_gid_sha256",
            "login_oid",
            "database_oid",
            "prepared_at_ms",
            "generation_id",
            "owned",
        ],
        "properties": {
            "transaction_gid_sha256": {"$ref": "#/$defs/Sha256"},
            "login_oid": {"$ref": "#/$defs/Oid"},
            "database_oid": {"$ref": "#/$defs/Oid"},
            "prepared_at_ms": {"$ref": "#/$defs/UInt"},
            "generation_id": {"oneOf": [{"$ref": "#/$defs/Positive"}, {"type": "null"}]},
            "owned": {"$ref": "#/$defs/Bool"},
        },
    },
    "RouteObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "route_id",
            "kind",
            "enabled",
            "generation_id",
            "frontend_identity_sha256",
            "backend_login_oids",
            "network_binding_sha256",
            "qualified_adapter_sha256",
            "in_flight_admissions",
            "admission_barrier_sha256",
        ],
        "properties": {
            "route_id": {"$ref": "#/$defs/Id"},
            "kind": {
                "enum": [
                    "DIRECT_PASSWORD",
                    "IAM",
                    "CERTIFICATE",
                    "POOLER",
                    "SET_ROLE",
                    "PUBLIC",
                    "SECURITY_DEFINER",
                    "MAINTENANCE",
                ]
            },
            "enabled": {"$ref": "#/$defs/Bool"},
            "generation_id": {"oneOf": [{"$ref": "#/$defs/Positive"}, {"type": "null"}]},
            "frontend_identity_sha256": {"$ref": "#/$defs/Sha256"},
            "backend_login_oids": {
                "type": "array",
                "items": {"$ref": "#/$defs/Oid"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "network_binding_sha256": {"$ref": "#/$defs/Sha256"},
            "qualified_adapter_sha256": {"oneOf": [{"$ref": "#/$defs/Sha256"}, {"type": "null"}]},
            "in_flight_admissions": {"$ref": "#/$defs/UInt"},
            "admission_barrier_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "FenceObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "epoch",
            "state",
            "revision",
            "owner_run_id",
            "candidate_sha256",
            "trust_profile_sha256",
        ],
        "properties": {
            "epoch": {"$ref": "#/$defs/Positive"},
            "state": {"enum": ["CLOSED", "CANDIDATE", "RESTORING", "OPEN", "RECOVERY_REQUIRED"]},
            "revision": {"$ref": "#/$defs/Positive"},
            "owner_run_id": {"$ref": "#/$defs/Uuid"},
            "candidate_sha256": {"$ref": "#/$defs/Sha256"},
            "trust_profile_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "DatabasePayload": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "database_binding",
            "server_version_num",
            "session_user",
            "current_user",
            "fence",
            "role_membership_acl_sha256",
            "roles",
            "memberships",
            "sessions",
            "prepared_transactions",
            "routes",
            "generations",
            "migration_receipt_sha256",
            "protected_state_sha256",
            "complete",
            "visibility_qualification_sha256",
            "in_flight_admission_barrier_sha256",
            "issuer_receipts_sha256",
        ],
        "properties": {
            "database_binding": {"$ref": "#/$defs/DatabaseBinding"},
            "server_version_num": {"$ref": "#/$defs/Positive"},
            "session_user": {"$ref": "#/$defs/Id"},
            "current_user": {"$ref": "#/$defs/Id"},
            "fence": {"$ref": "#/$defs/FenceObservation"},
            "role_membership_acl_sha256": {"$ref": "#/$defs/Sha256"},
            "roles": {
                "type": "array",
                "items": {"$ref": "#/$defs/RoleObservation"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "memberships": {
                "type": "array",
                "items": {"$ref": "#/$defs/MembershipObservation"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "sessions": {
                "type": "array",
                "items": {"$ref": "#/$defs/SessionObservation"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "prepared_transactions": {
                "type": "array",
                "items": {"$ref": "#/$defs/PreparedTransaction"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "routes": {
                "type": "array",
                "items": {"$ref": "#/$defs/RouteObservation"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "generations": {
                "type": "array",
                "items": {"$ref": "#/$defs/RuntimeAdmissionGeneration"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "migration_receipt_sha256": {"$ref": "#/$defs/Sha256"},
            "protected_state_sha256": {"$ref": "#/$defs/Sha256"},
            "complete": {"const": True},
            "visibility_qualification_sha256": {"$ref": "#/$defs/Sha256"},
            "in_flight_admission_barrier_sha256": {"$ref": "#/$defs/Sha256"},
            "issuer_receipts_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "ControlObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "issuer",
            "key_id",
            "audience",
            "challenge",
            "scope",
            "run_id",
            "epoch",
            "candidate_sha256",
            "request_sha256",
            "trust_profile_sha256",
            "issued_at_ms",
            "expires_at_ms",
            "observed_revision",
            "signature",
            "kind",
            "payload",
        ],
        "properties": {
            "protocol": {"const": "maezo.provisioning-observation.v1"},
            "issuer": {"$ref": "#/$defs/Id"},
            "key_id": {"$ref": "#/$defs/Id"},
            "audience": {"$ref": "#/$defs/Id"},
            "challenge": {"$ref": "#/$defs/Challenge"},
            "scope": {"$ref": "#/$defs/Scope"},
            "run_id": {"$ref": "#/$defs/Uuid"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "candidate_sha256": {"$ref": "#/$defs/Sha256"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "trust_profile_sha256": {"$ref": "#/$defs/Sha256"},
            "issued_at_ms": {"$ref": "#/$defs/UInt"},
            "expires_at_ms": {"$ref": "#/$defs/Positive"},
            "observed_revision": {"$ref": "#/$defs/Positive"},
            "signature": {"$ref": "#/$defs/Signature"},
            "kind": {"const": "control"},
            "payload": {"$ref": "#/$defs/ControlPayload"},
        },
    },
    "DatabaseObservation": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "issuer",
            "key_id",
            "audience",
            "challenge",
            "scope",
            "run_id",
            "epoch",
            "candidate_sha256",
            "request_sha256",
            "trust_profile_sha256",
            "issued_at_ms",
            "expires_at_ms",
            "observed_revision",
            "signature",
            "kind",
            "payload",
        ],
        "properties": {
            "protocol": {"const": "maezo.provisioning-observation.v1"},
            "issuer": {"$ref": "#/$defs/Id"},
            "key_id": {"$ref": "#/$defs/Id"},
            "audience": {"$ref": "#/$defs/Id"},
            "challenge": {"$ref": "#/$defs/Challenge"},
            "scope": {"$ref": "#/$defs/Scope"},
            "run_id": {"$ref": "#/$defs/Uuid"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "candidate_sha256": {"$ref": "#/$defs/Sha256"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "trust_profile_sha256": {"$ref": "#/$defs/Sha256"},
            "issued_at_ms": {"$ref": "#/$defs/UInt"},
            "expires_at_ms": {"$ref": "#/$defs/Positive"},
            "observed_revision": {"$ref": "#/$defs/Positive"},
            "signature": {"$ref": "#/$defs/Signature"},
            "kind": {"const": "database"},
            "payload": {"$ref": "#/$defs/DatabasePayload"},
        },
    },
    "SignedObservation": {
        "oneOf": [{"$ref": "#/$defs/ControlObservation"}, {"$ref": "#/$defs/DatabaseObservation"}]
    },
    "AwsvpcConfiguration": {
        "type": "object",
        "additionalProperties": False,
        "required": ["subnets", "securityGroups", "assignPublicIp"],
        "properties": {
            "subnets": {
                "type": "array",
                "items": {"$ref": "#/$defs/Id"},
                "minItems": 1,
                "maxItems": 16,
                "uniqueItems": True,
            },
            "securityGroups": {
                "type": "array",
                "items": {"$ref": "#/$defs/Id"},
                "minItems": 1,
                "maxItems": 5,
                "uniqueItems": True,
            },
            "assignPublicIp": {"const": "DISABLED"},
        },
    },
    "ProviderNetworkConfiguration": {
        "type": "object",
        "additionalProperties": False,
        "required": ["awsvpcConfiguration"],
        "properties": {"awsvpcConfiguration": {"$ref": "#/$defs/AwsvpcConfiguration"}},
    },
    "RunTaskProviderRequest": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "cluster",
            "taskDefinition",
            "launchType",
            "platformVersion",
            "count",
            "clientToken",
            "networkConfiguration",
            "startedBy",
            "enableExecuteCommand",
        ],
        "properties": {
            "cluster": {"$ref": "#/$defs/Arn"},
            "taskDefinition": {"$ref": "#/$defs/Arn"},
            "launchType": {"const": "FARGATE"},
            "platformVersion": {
                "type": "string",
                "minLength": 1,
                "maxLength": 255,
                "pattern": "^[0-9]+\\.[0-9]+\\.[0-9]+$",
            },
            "count": {"type": "integer", "minimum": 1, "maximum": 10},
            "clientToken": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[!-~]+$"},
            "networkConfiguration": {"$ref": "#/$defs/ProviderNetworkConfiguration"},
            "startedBy": {"$ref": "#/$defs/Uuid"},
            "enableExecuteCommand": {"const": False},
        },
    },
    "StopTaskProviderRequest": {
        "type": "object",
        "additionalProperties": False,
        "required": ["cluster", "task", "reason"],
        "properties": {
            "cluster": {"$ref": "#/$defs/Arn"},
            "task": {"$ref": "#/$defs/Arn"},
            "reason": {"const": "maezo-controller-owned-stop"},
        },
    },
    "ExpectedPrecondition": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "controller_revision",
            "db_fence_revision",
            "journal_revision",
            "pending_index_sha256",
            "target_inventory_sha256",
            "target_generation_status",
        ],
        "properties": {
            "controller_revision": {"$ref": "#/$defs/Positive"},
            "db_fence_revision": {"$ref": "#/$defs/Positive"},
            "journal_revision": {"$ref": "#/$defs/UInt"},
            "pending_index_sha256": {"$ref": "#/$defs/Sha256"},
            "target_inventory_sha256": {"$ref": "#/$defs/Sha256"},
            "target_generation_status": {"$ref": "#/$defs/GenerationStatus"},
        },
    },
    "RunTaskIntent": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "scope",
            "external_operation_id",
            "logical_run_id",
            "owner_subject",
            "epoch",
            "purpose",
            "target_arn",
            "target_generation",
            "task_definition_revision",
            "image_sha256",
            "config_set_sha256",
            "execution_role",
            "task_role",
            "network_binding",
            "admission_generation",
            "credential_binding",
            "request_bytes",
            "request_sha256",
            "expected_precondition",
            "action",
            "idempotency_class",
            "client_token",
            "request",
        ],
        "properties": {
            "protocol": {"const": "maezo.provisioning-intent.v1"},
            "scope": {"$ref": "#/$defs/Scope"},
            "external_operation_id": {"$ref": "#/$defs/Uuid"},
            "logical_run_id": {"$ref": "#/$defs/Uuid"},
            "owner_subject": {"$ref": "#/$defs/Id"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "purpose": {"$ref": "#/$defs/GenerationPurpose"},
            "target_arn": {"$ref": "#/$defs/Arn"},
            "target_generation": {"$ref": "#/$defs/Positive"},
            "task_definition_revision": {"$ref": "#/$defs/TaskDefinitionBinding"},
            "image_sha256": {"$ref": "#/$defs/Sha256"},
            "config_set_sha256": {"$ref": "#/$defs/Sha256"},
            "execution_role": {"$ref": "#/$defs/Arn"},
            "task_role": {"$ref": "#/$defs/Arn"},
            "network_binding": {"$ref": "#/$defs/NetworkBinding"},
            "admission_generation": {"$ref": "#/$defs/Positive"},
            "credential_binding": {"$ref": "#/$defs/CredentialSession"},
            "request_bytes": {"$ref": "#/$defs/Bytes"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "expected_precondition": {"$ref": "#/$defs/ExpectedPrecondition"},
            "action": {"const": "RunTask"},
            "idempotency_class": {"const": "PROVIDER_TOKEN_BOUNDED"},
            "client_token": {"type": "string", "minLength": 1, "maxLength": 64, "pattern": "^[!-~]+$"},
            "request": {"$ref": "#/$defs/RunTaskProviderRequest"},
        },
    },
    "StopTaskIntent": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "scope",
            "external_operation_id",
            "logical_run_id",
            "owner_subject",
            "epoch",
            "purpose",
            "target_arn",
            "target_generation",
            "task_definition_revision",
            "image_sha256",
            "config_set_sha256",
            "execution_role",
            "task_role",
            "network_binding",
            "admission_generation",
            "credential_binding",
            "request_bytes",
            "request_sha256",
            "expected_precondition",
            "action",
            "idempotency_class",
            "client_token",
            "request",
        ],
        "properties": {
            "protocol": {"const": "maezo.provisioning-intent.v1"},
            "scope": {"$ref": "#/$defs/Scope"},
            "external_operation_id": {"$ref": "#/$defs/Uuid"},
            "logical_run_id": {"$ref": "#/$defs/Uuid"},
            "owner_subject": {"$ref": "#/$defs/Id"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "purpose": {"$ref": "#/$defs/GenerationPurpose"},
            "target_arn": {"$ref": "#/$defs/Arn"},
            "target_generation": {"$ref": "#/$defs/Positive"},
            "task_definition_revision": {"$ref": "#/$defs/TaskDefinitionBinding"},
            "image_sha256": {"$ref": "#/$defs/Sha256"},
            "config_set_sha256": {"$ref": "#/$defs/Sha256"},
            "execution_role": {"$ref": "#/$defs/Arn"},
            "task_role": {"$ref": "#/$defs/Arn"},
            "network_binding": {"$ref": "#/$defs/NetworkBinding"},
            "admission_generation": {"$ref": "#/$defs/Positive"},
            "credential_binding": {"$ref": "#/$defs/CredentialSession"},
            "request_bytes": {"$ref": "#/$defs/Bytes"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "expected_precondition": {"$ref": "#/$defs/ExpectedPrecondition"},
            "action": {"const": "StopTask"},
            "idempotency_class": {"const": "SINGLE_DISPATCH_RECONCILE"},
            "client_token": {"type": "null"},
            "request": {"$ref": "#/$defs/StopTaskProviderRequest"},
        },
    },
    "IssuerPrepare": {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "generation"],
        "properties": {
            "operation": {"const": "prepare_generation"},
            "generation": {"$ref": "#/$defs/RuntimeAdmissionGeneration"},
        },
    },
    "IssuerCandidate": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "operation",
            "generation_id",
            "candidate_decision_sha256",
            "disposal_sha256",
            "deployment_receipt",
            "grant_receipt",
            "expected_db_revision",
        ],
        "properties": {
            "operation": {"const": "admit_candidate_login"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "candidate_decision_sha256": {"$ref": "#/$defs/Sha256"},
            "disposal_sha256": {"$ref": "#/$defs/Sha256"},
            "deployment_receipt": {"$ref": "#/$defs/ReceiptRef"},
            "grant_receipt": {"$ref": "#/$defs/ReceiptRef"},
            "expected_db_revision": {"$ref": "#/$defs/Positive"},
        },
    },
    "IssuerOpen": {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "generation_id", "activation_decision_sha256", "expected_db_revision"],
        "properties": {
            "operation": {"const": "open_runtime_generation"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "activation_decision_sha256": {"$ref": "#/$defs/Sha256"},
            "expected_db_revision": {"$ref": "#/$defs/Positive"},
        },
    },
    "IssuerRetire": {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "generation_id", "expected_db_revision"],
        "properties": {
            "operation": {"const": "retire_generation"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "expected_db_revision": {"$ref": "#/$defs/Positive"},
        },
    },
    "IssuerRequestBody": {
        "oneOf": [
            {"$ref": "#/$defs/IssuerPrepare"},
            {"$ref": "#/$defs/IssuerCandidate"},
            {"$ref": "#/$defs/IssuerOpen"},
            {"$ref": "#/$defs/IssuerRetire"},
        ]
    },
    "IssuerRequest": {
        "type": "object",
        "additionalProperties": False,
        "required": ["protocol", "scope", "run_id", "epoch", "issuer_operation_id", "body"],
        "properties": {
            "protocol": {"const": "maezo.provisioning-issuer-request.v1"},
            "scope": {"$ref": "#/$defs/Scope"},
            "run_id": {"$ref": "#/$defs/Uuid"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "issuer_operation_id": {"$ref": "#/$defs/Uuid"},
            "body": {"$ref": "#/$defs/IssuerRequestBody"},
        },
    },
    "IssuerReceipt": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "scope",
            "issuer_operation_id",
            "request_sha256",
            "epoch",
            "generation_id",
            "purpose",
            "binding_sha256",
            "activation_decision_sha256",
            "status",
            "revision",
            "issuer_login_oid",
            "issuer_session_user",
            "issuer_current_user",
            "recorded_before_commit_at_ms",
            "provisioning_schema_version",
        ],
        "properties": {
            "protocol": {"const": "maezo.provisioning-issuer-receipt.v1"},
            "scope": {"$ref": "#/$defs/Scope"},
            "issuer_operation_id": {"$ref": "#/$defs/Uuid"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "purpose": {"$ref": "#/$defs/GenerationPurpose"},
            "binding_sha256": {"$ref": "#/$defs/Sha256"},
            "activation_decision_sha256": {"$ref": "#/$defs/Sha256"},
            "status": {"$ref": "#/$defs/GenerationStatus"},
            "revision": {"$ref": "#/$defs/Positive"},
            "issuer_login_oid": {"$ref": "#/$defs/Oid"},
            "issuer_session_user": {"$ref": "#/$defs/Id"},
            "issuer_current_user": {"$ref": "#/$defs/Id"},
            "recorded_before_commit_at_ms": {"$ref": "#/$defs/UInt"},
            "provisioning_schema_version": {"$ref": "#/$defs/Version"},
        },
    },
    "AdmittedExternalIntent": {
        "oneOf": [{"$ref": "#/$defs/RunTaskIntent"}, {"$ref": "#/$defs/StopTaskIntent"}]
    },
    "UnsupportedObservedIntent": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "scope",
            "external_operation_id",
            "action",
            "intent_sha256",
            "request_sha256",
            "state",
            "code",
        ],
        "properties": {
            "protocol": {"const": "maezo.provisioning-unsupported-intent.v1"},
            "scope": {"$ref": "#/$defs/Scope"},
            "external_operation_id": {"$ref": "#/$defs/Uuid"},
            "action": {
                "enum": [
                    "StartTask",
                    "UpdateService",
                    "CreateTaskSet",
                    "UpdateTaskSet",
                    "DeleteTaskSet",
                    "SchedulingChange",
                    "AutomaticRollback",
                    "RevokeNativePermit",
                    "PublishConfigSet",
                    "DisposeOwnedMount",
                ]
            },
            "intent_sha256": {"$ref": "#/$defs/Sha256"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "state": {"const": "DISPATCHED_UNKNOWN"},
            "code": {"const": "UNSUPPORTED_ADAPTER"},
        },
    },
    "Topology": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "cluster_arn",
            "task_count",
            "service_arns",
            "task_set_arns",
            "permitted_startup_plan_sha256",
        ],
        "properties": {
            "cluster_arn": {"$ref": "#/$defs/Arn"},
            "task_count": {"type": "integer", "minimum": 1, "maximum": 1024},
            "service_arns": {
                "type": "array",
                "items": {"$ref": "#/$defs/Arn"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "task_set_arns": {
                "type": "array",
                "items": {"$ref": "#/$defs/Arn"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
            "permitted_startup_plan_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "CandidateDecision": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "scope",
            "run_id",
            "epoch",
            "source",
            "tree",
            "image_sha256",
            "trust_profile_sha256",
            "deployment_receipt",
            "grant_receipt",
            "boundary_sha256",
            "config_set_sha256",
            "generation_core",
            "topology",
            "settled_journal_sha256",
            "protocol",
            "purpose",
            "provisioner_disposal_sha256",
        ],
        "properties": {
            "scope": {"$ref": "#/$defs/Scope"},
            "run_id": {"$ref": "#/$defs/Uuid"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "source": {"$ref": "#/$defs/GitOid"},
            "tree": {"$ref": "#/$defs/GitOid"},
            "image_sha256": {"$ref": "#/$defs/Sha256"},
            "trust_profile_sha256": {"$ref": "#/$defs/Sha256"},
            "deployment_receipt": {"$ref": "#/$defs/ReceiptRef"},
            "grant_receipt": {"$ref": "#/$defs/ReceiptRef"},
            "boundary_sha256": {"$ref": "#/$defs/Sha256"},
            "config_set_sha256": {"$ref": "#/$defs/Sha256"},
            "generation_core": {"$ref": "#/$defs/GenerationCore"},
            "topology": {"$ref": "#/$defs/Topology"},
            "settled_journal_sha256": {"$ref": "#/$defs/Sha256"},
            "protocol": {"const": "maezo.candidate-admission-decision.v1"},
            "purpose": {"const": "candidate"},
            "provisioner_disposal_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "ActivationDecision": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "scope",
            "run_id",
            "epoch",
            "source",
            "tree",
            "image_sha256",
            "trust_profile_sha256",
            "deployment_receipt",
            "grant_receipt",
            "boundary_sha256",
            "config_set_sha256",
            "generation_core",
            "topology",
            "settled_journal_sha256",
            "protocol",
            "purpose",
            "candidate_validation_sha256",
            "candidate_disposal_sha256",
        ],
        "properties": {
            "scope": {"$ref": "#/$defs/Scope"},
            "run_id": {"$ref": "#/$defs/Uuid"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "source": {"$ref": "#/$defs/GitOid"},
            "tree": {"$ref": "#/$defs/GitOid"},
            "image_sha256": {"$ref": "#/$defs/Sha256"},
            "trust_profile_sha256": {"$ref": "#/$defs/Sha256"},
            "deployment_receipt": {"$ref": "#/$defs/ReceiptRef"},
            "grant_receipt": {"$ref": "#/$defs/ReceiptRef"},
            "boundary_sha256": {"$ref": "#/$defs/Sha256"},
            "config_set_sha256": {"$ref": "#/$defs/Sha256"},
            "generation_core": {"$ref": "#/$defs/GenerationCore"},
            "topology": {"$ref": "#/$defs/Topology"},
            "settled_journal_sha256": {"$ref": "#/$defs/Sha256"},
            "protocol": {"const": "maezo.runtime-activation-decision.v1"},
            "purpose": {"const": "runtime"},
            "candidate_validation_sha256": {"$ref": "#/$defs/Sha256"},
            "candidate_disposal_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "RuntimeActivationBinding": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "scope",
            "database_binding",
            "controller_epoch",
            "source",
            "tree",
            "image_sha256",
            "trust_profile_sha256",
            "deployment_receipt_key",
            "deployment_result_sha256",
            "grant_receipt_key",
            "grant_result_sha256",
            "boundary_sha256",
            "config_set_sha256",
            "caller_identity",
            "caller_certificate_binding",
            "runtime_admission_generation",
            "activation_decision_sha256",
            "runtime_task_identity_sha256",
            "task_definition_binding_sha256",
            "issuer_restore_receipt_sha256",
            "readiness_result_sha256",
            "journal_revision",
            "settled_journal_sha256",
        ],
        "properties": {
            "scope": {"$ref": "#/$defs/Scope"},
            "database_binding": {"$ref": "#/$defs/DatabaseBinding"},
            "controller_epoch": {"$ref": "#/$defs/Positive"},
            "source": {"$ref": "#/$defs/GitOid"},
            "tree": {"$ref": "#/$defs/GitOid"},
            "image_sha256": {"$ref": "#/$defs/Sha256"},
            "trust_profile_sha256": {"$ref": "#/$defs/Sha256"},
            "deployment_receipt_key": {"$ref": "#/$defs/ReceiptKey"},
            "deployment_result_sha256": {"$ref": "#/$defs/Sha256"},
            "grant_receipt_key": {"$ref": "#/$defs/ReceiptKey"},
            "grant_result_sha256": {"$ref": "#/$defs/Sha256"},
            "boundary_sha256": {"$ref": "#/$defs/Sha256"},
            "config_set_sha256": {"$ref": "#/$defs/Sha256"},
            "caller_identity": {"$ref": "#/$defs/EngineIdentity"},
            "caller_certificate_binding": {"$ref": "#/$defs/CertificateBinding"},
            "runtime_admission_generation": {"$ref": "#/$defs/Positive"},
            "activation_decision_sha256": {"$ref": "#/$defs/Sha256"},
            "runtime_task_identity_sha256": {"$ref": "#/$defs/Sha256"},
            "task_definition_binding_sha256": {"$ref": "#/$defs/Sha256"},
            "issuer_restore_receipt_sha256": {"$ref": "#/$defs/Sha256"},
            "readiness_result_sha256": {"$ref": "#/$defs/Sha256"},
            "journal_revision": {"$ref": "#/$defs/UInt"},
            "settled_journal_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "NativeCommitted": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "receipt_key", "request_sha256", "canonical_result_bytes", "result_sha256"],
        "properties": {
            "kind": {"const": "COMMITTED"},
            "receipt_key": {"$ref": "#/$defs/ReceiptKey"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "canonical_result_bytes": {"$ref": "#/$defs/Bytes"},
            "result_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "NativeReplay": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "receipt_key", "request_sha256", "canonical_result_bytes", "result_sha256"],
        "properties": {
            "kind": {"const": "REPLAY"},
            "receipt_key": {"$ref": "#/$defs/ReceiptKey"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "canonical_result_bytes": {"$ref": "#/$defs/Bytes"},
            "result_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "ReceiptFound": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "receipt_key", "request_sha256", "canonical_result_bytes", "result_sha256"],
        "properties": {
            "kind": {"const": "FOUND"},
            "receipt_key": {"$ref": "#/$defs/ReceiptKey"},
            "request_sha256": {"$ref": "#/$defs/Sha256"},
            "canonical_result_bytes": {"$ref": "#/$defs/Bytes"},
            "result_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "Refused": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "code"],
        "properties": {"kind": {"const": "REFUSED"}, "code": {"$ref": "#/$defs/RefusalCode"}},
    },
    "ReceiptNotFound": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "receipt_key"],
        "properties": {"kind": {"const": "NOT_FOUND"}, "receipt_key": {"$ref": "#/$defs/ReceiptKey"}},
    },
    "NativeResult": {
        "oneOf": [
            {"$ref": "#/$defs/NativeCommitted"},
            {"$ref": "#/$defs/NativeReplay"},
            {"$ref": "#/$defs/Refused"},
        ]
    },
    "ReceiptRead": {
        "oneOf": [
            {"$ref": "#/$defs/ReceiptFound"},
            {"$ref": "#/$defs/ReceiptNotFound"},
            {"$ref": "#/$defs/Refused"},
        ]
    },
    "ExternalAcknowledged": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "kind",
            "operation_id",
            "intent_sha256",
            "provider_result_sha256",
            "returned_resource_ids",
        ],
        "properties": {
            "kind": {"const": "ACKNOWLEDGED"},
            "operation_id": {"$ref": "#/$defs/Uuid"},
            "intent_sha256": {"$ref": "#/$defs/Sha256"},
            "provider_result_sha256": {"$ref": "#/$defs/Sha256"},
            "returned_resource_ids": {
                "type": "array",
                "items": {"$ref": "#/$defs/Arn"},
                "minItems": 0,
                "maxItems": 1024,
                "uniqueItems": True,
            },
        },
    },
    "ExternalPending": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "operation_id", "intent_sha256", "state"],
        "properties": {
            "kind": {"const": "PENDING"},
            "operation_id": {"$ref": "#/$defs/Uuid"},
            "intent_sha256": {"$ref": "#/$defs/Sha256"},
            "state": {"enum": ["INTENT", "RESERVED", "DISPATCHED_UNKNOWN", "ACKNOWLEDGED", "RECONCILING"]},
        },
    },
    "ExternalSettled": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "operation_id", "intent_sha256", "terminal_proof_sha256"],
        "properties": {
            "kind": {"const": "SETTLED"},
            "operation_id": {"$ref": "#/$defs/Uuid"},
            "intent_sha256": {"$ref": "#/$defs/Sha256"},
            "terminal_proof_sha256": {"$ref": "#/$defs/Sha256"},
        },
    },
    "ExternalOutcome": {
        "oneOf": [
            {"$ref": "#/$defs/ExternalAcknowledged"},
            {"$ref": "#/$defs/ExternalPending"},
            {"$ref": "#/$defs/ExternalSettled"},
            {"$ref": "#/$defs/Refused"},
        ]
    },
    "GenerationObserved": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "kind",
            "generation_id",
            "epoch",
            "status",
            "revision",
            "binding_sha256",
            "issuer_receipt_sha256",
            "signed_observation",
        ],
        "properties": {
            "kind": {"const": "OBSERVED"},
            "generation_id": {"$ref": "#/$defs/Positive"},
            "epoch": {"$ref": "#/$defs/Positive"},
            "status": {"$ref": "#/$defs/GenerationStatus"},
            "revision": {"$ref": "#/$defs/Positive"},
            "binding_sha256": {"$ref": "#/$defs/Sha256"},
            "issuer_receipt_sha256": {"$ref": "#/$defs/Sha256"},
            "signed_observation": {"$ref": "#/$defs/DatabaseObservation"},
        },
    },
    "GenerationRead": {"oneOf": [{"$ref": "#/$defs/GenerationObserved"}, {"$ref": "#/$defs/Refused"}]},
    "ActivationActive": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "kind",
            "runtime_activation_binding",
            "controller_observation",
            "database_admission_observation",
        ],
        "properties": {
            "kind": {"const": "ACTIVE"},
            "runtime_activation_binding": {"$ref": "#/$defs/RuntimeActivationBinding"},
            "controller_observation": {"$ref": "#/$defs/ControlObservation"},
            "database_admission_observation": {"$ref": "#/$defs/DatabaseObservation"},
        },
    },
    "ActivationUnavailable": {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind", "code"],
        "properties": {"kind": {"const": "UNAVAILABLE"}, "code": {"$ref": "#/$defs/RefusalCode"}},
    },
    "ActivationRead": {
        "oneOf": [{"$ref": "#/$defs/ActivationActive"}, {"$ref": "#/$defs/ActivationUnavailable"}]
    },
    "TypePacket": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "protocol",
            "native_request",
            "native_receipt",
            "trust_profile",
            "control_observation",
            "database_observation",
            "generation",
            "intent",
            "activation",
        ],
        "properties": {
            "protocol": {"const": "maezo.d7d-stage1-types.proposed.v1"},
            "native_request": {"$ref": "#/$defs/NativeRequest"},
            "native_receipt": {"$ref": "#/$defs/NativeReceipt"},
            "trust_profile": {"$ref": "#/$defs/TrustProfile"},
            "control_observation": {"$ref": "#/$defs/ControlObservation"},
            "database_observation": {"$ref": "#/$defs/DatabaseObservation"},
            "generation": {"$ref": "#/$defs/RuntimeAdmissionGeneration"},
            "intent": {"$ref": "#/$defs/AdmittedExternalIntent"},
            "activation": {"$ref": "#/$defs/ActivationRead"},
        },
    },
}
_RECORD_TYPES: dict[str, type[Record]] = {
    "DatabaseBinding": DatabaseBinding,
    "Scope": Scope,
    "BlobRef": BlobRef,
    "ReceiptKey": ReceiptKey,
    "ReceiptRef": ReceiptRef,
    "EngineIdentity": EngineIdentity,
    "EngineTarget": EngineTarget,
    "CertificateBinding": CertificateBinding,
    "ContentReleaseRef": ContentReleaseRef,
    "ParsedDefinition": ParsedDefinition,
    "DeployResource": DeployResource,
    "DeployPayload": DeployPayload,
    "Grant": Grant,
    "GrantSource": GrantSource,
    "AttestationMapping": AttestationMapping,
    "NoSource": NoSource,
    "LockedSource": LockedSource,
    "HumanSource": HumanSource,
    "CapabilityMapping": CapabilityMapping,
    "WorkloadPlan": WorkloadPlan,
    "Operators": Operators,
    "CompilerPlan": CompilerPlan,
    "GrantPayload": GrantPayload,
    "DeployRequest": DeployRequest,
    "GrantRequest": GrantRequest,
    "StoredResource": StoredResource,
    "GeneratedDefinition": GeneratedDefinition,
    "DeployResultBody": DeployResultBody,
    "IdentityResult": IdentityResult,
    "GrantResultRow": GrantResultRow,
    "GrantResultBody": GrantResultBody,
    "NativeActor": NativeActor,
    "NativeReceipt": NativeReceipt,
    "ReceiptQuery": ReceiptQuery,
    "Limits": Limits,
    "ObserverKey": ObserverKey,
    "TrustProfile": TrustProfile,
    "InventoryBound": InventoryBound,
    "InventoryBounds": InventoryBounds,
    "NetworkBinding": NetworkBinding,
    "SecretVersion": SecretVersion,
    "TaskDefinitionBinding": TaskDefinitionBinding,
    "GenerationCore": GenerationCore,
    "RuntimeAdmissionGeneration": RuntimeAdmissionGeneration,
    "CredentialSession": CredentialSession,
    "DispatcherReservation": DispatcherReservation,
    "ProviderFailure": ProviderFailure,
    "ProviderOutcome": ProviderOutcome,
    "DispatchTerminalProof": DispatchTerminalProof,
    "ResourceTerminalProof": ResourceTerminalProof,
    "Settlement": Settlement,
    "JournalObservation": JournalObservation,
    "ManagedResource": ManagedResource,
    "TaskObservation": TaskObservation,
    "ServiceDeployment": ServiceDeployment,
    "TaskSetObservation": TaskSetObservation,
    "ServiceObservation": ServiceObservation,
    "StartPath": StartPath,
    "ControlPayload": ControlPayload,
    "RoleObservation": RoleObservation,
    "MembershipObservation": MembershipObservation,
    "SessionObservation": SessionObservation,
    "PreparedTransaction": PreparedTransaction,
    "RouteObservation": RouteObservation,
    "FenceObservation": FenceObservation,
    "DatabasePayload": DatabasePayload,
    "ControlObservation": ControlObservation,
    "DatabaseObservation": DatabaseObservation,
    "AwsvpcConfiguration": AwsvpcConfiguration,
    "ProviderNetworkConfiguration": ProviderNetworkConfiguration,
    "RunTaskProviderRequest": RunTaskProviderRequest,
    "StopTaskProviderRequest": StopTaskProviderRequest,
    "ExpectedPrecondition": ExpectedPrecondition,
    "RunTaskIntent": RunTaskIntent,
    "StopTaskIntent": StopTaskIntent,
    "IssuerPrepare": IssuerPrepare,
    "IssuerCandidate": IssuerCandidate,
    "IssuerOpen": IssuerOpen,
    "IssuerRetire": IssuerRetire,
    "IssuerRequest": IssuerRequest,
    "IssuerReceipt": IssuerReceipt,
    "UnsupportedObservedIntent": UnsupportedObservedIntent,
    "Topology": Topology,
    "CandidateDecision": CandidateDecision,
    "ActivationDecision": ActivationDecision,
    "RuntimeActivationBinding": RuntimeActivationBinding,
    "NativeCommitted": NativeCommitted,
    "NativeReplay": NativeReplay,
    "ReceiptFound": ReceiptFound,
    "Refused": Refused,
    "ReceiptNotFound": ReceiptNotFound,
    "ExternalAcknowledged": ExternalAcknowledged,
    "ExternalPending": ExternalPending,
    "ExternalSettled": ExternalSettled,
    "GenerationObserved": GenerationObserved,
    "ActivationActive": ActivationActive,
    "ActivationUnavailable": ActivationUnavailable,
    "TypePacket": TypePacket,
}
_REFUSALS = frozenset(_DEFINITIONS["RefusalCode"]["enum"])


def _plain(value: Any, depth: int = 0) -> Any:
    _need(depth <= MAX_DEPTH)
    if isinstance(value, Record):
        _need(type(value).__name__ in _RECORD_TYPES)
        return {f.name: _plain(getattr(value, f.name), depth + 1) for f in fields(value)}
    if type(value) is dict:
        _need(all(type(k) is str and k.isascii() for k in value))
        return {k: _plain(v, depth + 1) for k, v in value.items()}
    if type(value) in (tuple, list):
        _need(len(value) <= 1024)
        return [_plain(v, depth + 1) for v in value]
    if type(value) is str:
        _need(not any(0xD800 <= ord(c) <= 0xDFFF for c in value))
        return value
    if type(value) is int:
        _need(0 <= value <= MAX_INTEGER)
        return value
    _need(value is None or type(value) is bool)
    return value


def canonical_json(value: Any) -> bytes:
    """Encode the D safe-integer profile, independently of existing wire protocols.

    This low-level codec checks W1/W2 value grammar. Closed names and field byte
    bounds are enforced by parse/record constructors, never by the codec alone.
    """
    try:
        raw = json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        _need(len(raw) <= MAX_BYTES)
        return raw
    except Exception:
        raise ContractError() from None


def _integer(token: str) -> int:
    _need(bool(re.fullmatch(r"0|[1-9][0-9]{0,15}", token)))
    value = int(token)
    _need(value <= MAX_INTEGER)
    return value


def _not_integer(token: str) -> NoReturn:
    _fail()


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _need(key.isascii() and key not in result)
        result[key] = value
    return result


def decode_json(raw: bytes) -> Any:
    """Decode canonical D bytes without repairing lexical defects."""
    try:
        _need(type(raw) is bytes and 0 < len(raw) <= MAX_BYTES)
        # Bound parser recursion before json.loads, including deeply nested junk.
        depth, quoted, escaped = 0, False, False
        for byte in raw:
            if quoted:
                if escaped:
                    escaped = False
                elif byte == 92:
                    escaped = True
                elif byte == 34:
                    quoted = False
            elif byte == 34:
                quoted = True
            elif byte in (91, 123):
                depth += 1
                _need(depth <= MAX_DEPTH)
            elif byte in (93, 125):
                depth -= 1
                _need(depth >= 0)
        result = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object,
            parse_int=_integer,
            parse_float=_not_integer,
            parse_constant=_not_integer,
        )
        _need(canonical_json(result) == raw)
        return result
    except Exception:
        raise ContractError() from None


def decode_base64(value: str, *, length: int | None = None) -> bytes:
    try:
        _need(type(value) is str and 0 < len(value) <= 1398102)
        _need(bool(re.fullmatch(r"[A-Za-z0-9_-]+", value)))
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        _need(0 < len(decoded) <= MAX_BYTES)
        _need(base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") == value)
        _need(length is None or (type(length) is int and len(decoded) == length))
        return decoded
    except Exception:
        raise ContractError() from None


def _shape(rule: dict[str, Any], value: Any) -> None:
    if "$ref" in rule:
        name = rule["$ref"].split("/")[-1]
        _shape(_DEFINITIONS[name], value)
        if name in {"Bytes", "Challenge", "PublicKey", "Signature"}:
            decode_base64(value, length={"Challenge": 32, "PublicKey": 32, "Signature": 64}.get(name))
        if name in {"Id", "EmptyOrId", "Arn"}:
            _need(not any(c.isspace() for c in value))
        return
    if "oneOf" in rule:
        count = 0
        for branch in rule["oneOf"]:
            try:
                _shape(branch, value)
                count += 1
            except ContractError:
                pass
        _need(count == 1)
        return
    if "const" in rule:
        _need(type(value) is type(rule["const"]) and value == rule["const"])
        return
    if "enum" in rule:
        _need(any(type(value) is type(x) and value == x for x in rule["enum"]))
        return
    kind = rule["type"]
    if kind == "object":
        _need(type(value) is dict and set(value) == set(rule["required"]))
        for key, child in rule["properties"].items():
            _shape(child, value[key])
    elif kind == "array":
        _need(type(value) is list and rule["minItems"] <= len(value) <= rule["maxItems"])
        for item in value:
            _shape(rule["items"], item)
        if rule.get("uniqueItems"):
            _need(len({canonical_json(x) for x in value}) == len(value))
    elif kind == "string":
        _need(type(value) is str)
        # W1 applies to every typed identifier/text, including ARN resources
        # whose structural pattern does not exclude non-whitespace controls.
        _need(not any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in value))
        size = len(value.encode("utf-8"))
        _need(rule["minLength"] <= size <= rule["maxLength"])
        _need(not rule.get("pattern") or re.fullmatch(rule["pattern"], value) is not None)
    elif kind == "integer":
        _need(type(value) is int and rule["minimum"] <= value <= rule["maximum"])
    elif kind == "boolean":
        _need(type(value) is bool)
    elif kind == "null":
        _need(value is None)
    else:
        _fail()


def _branch(rule: dict[str, Any], value: Any) -> dict[str, Any]:
    for child in rule["oneOf"]:
        try:
            _shape(child, value)
            return cast(dict[str, Any], child)
        except ContractError:
            pass
    _fail()


def _materialize(rule: dict[str, Any], value: Any) -> Any:
    if "$ref" in rule:
        name = rule["$ref"].split("/")[-1]
        definition = _DEFINITIONS[name]
        if name in _RECORD_TYPES:
            record = object.__new__(_RECORD_TYPES[name])
            for key, child in definition["properties"].items():
                object.__setattr__(record, key, _materialize(child, value[key]))
            return record
        return _materialize(definition, value)
    if "oneOf" in rule:
        return _materialize(_branch(rule, value), value)
    if rule.get("type") == "array":
        return tuple(_materialize(rule["items"], x) for x in value)
    if type(value) is list:
        return tuple(value)
    return value


def _walk_relations(rule: dict[str, Any], value: Any) -> None:
    if "$ref" in rule:
        name = rule["$ref"].split("/")[-1]
        _walk_relations(_DEFINITIONS[name], value)
        _relations(name, value)
    elif "oneOf" in rule:
        _walk_relations(_branch(rule, value), value)
    elif rule.get("type") == "object":
        for key, child in rule["properties"].items():
            _walk_relations(child, value[key])
    elif rule.get("type") == "array":
        for item in value:
            _walk_relations(rule["items"], item)


def _validate(name: str, value: Any) -> None:
    _need(type(name) is str and name in _DEFINITIONS)
    rule = {"$ref": "#/$defs/" + name}
    _shape(rule, value)
    _walk_relations(rule, value)


def parse(name: str, raw: bytes) -> Any:
    """Parse a closed record/union/scalar; this never returns authenticated authority."""
    try:
        value = decode_json(raw)
        _validate(name, value)
        return _materialize({"$ref": "#/$defs/" + name}, value)
    except ContractError:
        raise
    except Exception:
        raise ContractError() from None


def parse_record[R: Record](record_type: type[R], raw: bytes) -> R:
    _need(record_type in _RECORD_TYPES.values())
    result = parse(record_type.__name__, raw)
    _need(type(result) is record_type)
    return cast(R, result)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _same(
    a: dict[str, Any], b: dict[str, Any], names: tuple[str, ...], code: RefusalCode = "INVALID_BODY"
) -> None:
    _need(all(a[n] == b[n] for n in names), code)


def _unique(rows: list[Any], names: tuple[str, ...], *, ordered: bool = False) -> None:
    keys = [tuple(x[n] for n in names) for x in rows]
    _need(len(set(keys)) == len(keys))
    if ordered:
        _need(keys == sorted(keys))


def _scope_key(scope: dict[str, Any], key: dict[str, Any], operation: str | None = None) -> None:
    _same(scope, key, ("tenant", "environment", "engine_name"), "SCOPE_REFUSED")
    _need(operation is None or key["operation"] == operation, "PURPOSE_REFUSED")


def _task_binding(parent: dict[str, Any], key: str = "task_definition_revision") -> None:
    task = parent[key]
    _same(
        parent,
        task,
        tuple(
            n
            for n in ("image_sha256", "config_set_sha256", "task_role", "execution_role", "network_binding")
            if n in parent
        ),
    )


def _relations(name: str, v: Any) -> None:
    """Deterministic predicates whose inputs are wholly present in this record."""
    if type(v) is dict and "scope" in v:
        _nested_scope(v, v["scope"])
    if name == "TypePacket":
        request = v["native_request"]
        _nested_scope(v, request["scope"])
        _need(v["native_receipt"]["request_sha256"] == _sha(request))
        for observation in (v["control_observation"], v["database_observation"]):
            _need(observation["request_sha256"] == _sha(request))
            _same(request, observation, ("run_id", "trust_profile_sha256"))
        _need(request["trust_profile_sha256"] == _sha(v["trust_profile"]))
        _same(request, v["native_receipt"], ("source", "tree", "trust_profile_sha256"))
    if name in {"TaskDefinitionBinding", "NativeActor"}:
        arn = v["arn"] if name == "TaskDefinitionBinding" else v["task_definition_revision"]
        match = re.fullmatch(r"arn:[^:]+:ecs:[^:]+:[0-9]{12}:task-definition/[^:/]+:([1-9][0-9]*)", arn)
        _need(match is not None)
        if name == "TaskDefinitionBinding":
            _need(int(cast(re.Match[str], match)[1]) == v["revision"])
    if name == "SecretVersion":
        _need(v["version_id"] not in {"AWSCURRENT", "AWSPREVIOUS", "AWSPENDING"})
        _need(":secretsmanager:" in v["resource_arn"] and ":secret:" in v["resource_arn"])
    if name in {"DeployResource", "StoredResource"}:
        _need(all(x not in {".", "..", ""} for x in v["name"].split("/")))
        _need(v["name"].endswith(".bpmn" if v["kind"] == "BPMN" else ".dmn"))
    if name == "DeployResource":
        _unique(v["expected_definitions"], ("kind", "key"), ordered=True)
        _unique(
            v["content_release_refs"],
            ("release_id", "reviewed_source", "review_artifact_sha256"),
            ordered=True,
        )
        _need(
            all(
                d["kind"] in ({"process"} if v["kind"] == "BPMN" else {"decision", "drd"})
                for d in v["expected_definitions"]
            )
        )
    if name in {"DeployPayload", "DeployResultBody"}:
        _unique(v["resources"], ("name",), ordered=True)
        if name == "DeployPayload":
            _unique([d for r in v["resources"] for d in r["expected_definitions"]], ("kind", "key"))
        else:
            _unique(v["definitions"], ("kind", "key"), ordered=True)
            _unique(v["definitions"], ("definition_id",))
            _unique(v["resources"], ("native_resource_id",))
            resources = {r["name"]: r for r in v["resources"]}
            _need(set(resources) == {d["resource_name"] for d in v["definitions"]})
            for d in v["definitions"]:
                _same(v, d, ("tenant", "deployment_id"))
                _need(
                    d["kind"]
                    in (
                        {"process"}
                        if resources[d["resource_name"]]["kind"] == "BPMN"
                        else {"decision", "drd"}
                    )
                )
    if name == "Grant":
        _need(v["permissions"] == sorted(v["permissions"]))
        _need(
            (
                v["resource"] == "PROCESS_INSTANCE"
                and v["resource_id"] == "*"
                and v["permissions"] == ["CREATE"]
            )
            or (
                v["resource"] == "PROCESS_DEFINITION"
                and v["resource_id"] != "*"
                and "CREATE" not in v["permissions"]
            )
        )
    if name == "CompilerPlan":
        _need(v["operators"]["bootstrap"] != v["operators"]["deployment"])
        _need(v["preserve_existing_users"] == sorted(v["preserve_existing_users"]))
        _need(v["definitions"] == sorted(v["definitions"], key=canonical_json))
        for rows in (v["grants"], v["grant_sources"]):
            _unique(rows, ("engine_user", "resource", "resource_id"), ordered=True)
        _need(
            [(r["engine_user"], r["resource"], r["resource_id"]) for r in v["grants"]]
            == [(r["engine_user"], r["resource"], r["resource_id"]) for r in v["grant_sources"]]
        )
        _need(
            not set(v["operators"].values())
            & ({r["engine_user"] for r in v["grants"]} | set(v["preserve_existing_users"]))
        )
        _need(all(r["capability_digests"] == sorted(r["capability_digests"]) for r in v["grant_sources"]))
    if name == "GrantPayload":
        _need(v["compiler_plan_sha256"] == _sha(v["compiler_plan"]))
        _need(v["final_boundary_manifest"]["sha256"] == v["compiler_plan"]["boundary_sha256"])
        _scope_key(v["compiler_plan"], v["deployment_receipt"]["receipt_key"], "deploy")
        _unique(v["workload_plans"], ("engine_user",))
        for w in v["workload_plans"]:
            _same(w["identity"], v["compiler_plan"], ("tenant", "environment"))
        _need(
            {w["engine_user"] for w in v["workload_plans"]}
            == {r["engine_user"] for r in v["compiler_plan"]["grants"]}
        )
        by_user = {
            w["engine_user"]: {c["capability_sha256"] for c in w["capabilities"]} for w in v["workload_plans"]
        }
        for row in v["compiler_plan"]["grant_sources"]:
            _need(set(row["capability_digests"]) <= by_user[row["engine_user"]])
        for user, digests in by_user.items():
            _need(
                digests
                == {
                    d
                    for r in v["compiler_plan"]["grant_sources"]
                    if r["engine_user"] == user
                    for d in r["capability_digests"]
                }
            )
    if name == "WorkloadPlan":
        _unique(v["capabilities"], ("capability_sha256",))
    if name in {"LockedSource", "HumanSource"}:
        _unique(v["attestations"], ("name",))
    if name in {"DeployRequest", "GrantRequest"}:
        if name == "DeployRequest":
            _same(v["scope"], v["payload"], ("tenant",), "SCOPE_REFUSED")
        else:
            _same(
                v["scope"],
                v["payload"]["compiler_plan"],
                ("tenant", "environment", "engine_name"),
                "SCOPE_REFUSED",
            )
            _need(v["run_id"] == v["payload"]["deployment_receipt"]["receipt_key"]["run_id"])
    if name in {"NativeReceipt", "ReceiptQuery"}:
        _scope_key(v["scope"], v["receipt_key"])
    if name in {"NativeReceipt", "NativeCommitted", "NativeReplay", "ReceiptFound"}:
        body = _result_body(v)
        if name == "NativeReceipt":
            _need(v["native_actor"]["purpose"] == v["receipt_key"]["operation"], "PURPOSE_REFUSED")
        if body["operation"] == "grant":
            _scope_key(v["receipt_key"], body["deployment_receipt"]["receipt_key"], "deploy")
            _same(v["receipt_key"], body["deployment_receipt"]["receipt_key"], ("run_id",))
    if name in {"GrantResultBody"}:
        _unique(v["identities"], ("engine_user",))
        _unique(v["grants"], ("authorization_id",))
        _unique([r["grant"] for r in v["grants"]], ("engine_user", "resource", "resource_id"))
        _need({r["grant"]["engine_user"] for r in v["grants"]} <= {r["engine_user"] for r in v["identities"]})
    if name in {"CertificateBinding", "ObserverKey"}:
        _need(v["expires_at_ms"] > v["not_before_ms"])
    if name == "ObserverKey":
        if v["lifecycle"] == "REVOKED":
            _need(
                v["revoked_at_ms"] is not None
                and v["not_before_ms"] <= v["revoked_at_ms"] <= v["expires_at_ms"]
            )
        else:
            _need(v["revoked_at_ms"] is None)
    if name == "TrustProfile":
        _profile_relations(v)
    if name == "InventoryBounds":
        _unique(v["bounds"], ("collection_path",))
        _need(tuple(b["collection_path"] for b in v["bounds"]) == INVENTORY_FAMILIES)
    if name in {"ControlObservation", "DatabaseObservation", "CredentialSession"}:
        _need(v["expires_at_ms"] > v["issued_at_ms"])
        if name != "CredentialSession":
            _need(v["expires_at_ms"] - v["issued_at_ms"] <= 15000)
            _observation_relations(v)
    if name in {"GenerationCore", "ManagedResource", "RunTaskIntent", "StopTaskIntent"}:
        _task_binding(v)
    if name == "TaskObservation":
        _task_binding(v, "task_definition")
        _need(v["standalone"] == (v["task_set_arn"] is None))
    if name in {"RoleObservation", "SessionObservation", "PreparedTransaction"}:
        _need(not v["owned"] or v["generation_id"] is not None)
    if name == "DatabasePayload":
        _database_relations(v)
    if name == "ControlPayload":
        _control_relations(v)
    if name == "JournalObservation":
        _journal_relations(v)
    if name in {"RunTaskIntent", "StopTaskIntent"}:
        _intent_relations(v)
    if name in {"CandidateDecision", "ActivationDecision"}:
        _decision_relations(v)
    if name == "RuntimeActivationBinding":
        _need(v["scope"]["database_binding"] == v["database_binding"], "SCOPE_REFUSED")
        _same(v["scope"], v["caller_identity"], ("tenant", "environment"), "SCOPE_REFUSED")
        _need(v["caller_identity"]["issuer"] == v["caller_certificate_binding"]["issuer_dn"])
        _need(v["caller_identity"]["subject"] == v["caller_certificate_binding"]["uri_san"])
        for op in ("deployment", "grant"):
            _scope_key(v["scope"], v[op + "_receipt_key"], "deploy" if op == "deployment" else op)
        _same(v["deployment_receipt_key"], v["grant_receipt_key"], ("run_id",))
    if name == "GenerationObserved":
        _generation_read_relations(v)
    if name == "ActivationActive":
        _active_relations(v)
    if name == "IssuerRequest" and v["body"]["operation"] == "prepare_generation":
        core = v["body"]["generation"]["binding"]
        _same(v, core, ("scope", "epoch"))
        _need(v["body"]["generation"]["status"] == "PREPARED")
    if name == "IssuerCandidate":
        _need(v["deployment_receipt"]["receipt_key"]["operation"] == "deploy")
        _need(v["grant_receipt"]["receipt_key"]["operation"] == "grant")
        _same(
            v["deployment_receipt"]["receipt_key"],
            v["grant_receipt"]["receipt_key"],
            ("tenant", "environment", "engine_name", "run_id"),
        )


def _nested_scope(value: Any, scope: dict[str, Any]) -> None:
    if type(value) is dict:
        if "scope" in value:
            _need(value["scope"] == scope, "SCOPE_REFUSED")
        for key in ("tenant", "environment", "engine_name", "database_binding"):
            if key in value:
                _need(value[key] == scope[key], "SCOPE_REFUSED")
        for child in value.values():
            _nested_scope(child, scope)
    elif type(value) is list:
        for child in value:
            _nested_scope(child, scope)


def _result_body(v: dict[str, Any]) -> dict[str, Any]:
    raw = decode_base64(v["canonical_result_bytes"])
    _need(hashlib.sha256(raw).hexdigest() == v["result_sha256"])
    body = cast(Record, parse("NativeResultBody", raw)).to_wire()
    _need(body["operation"] == v["receipt_key"]["operation"], "PURPOSE_REFUSED")
    if body["operation"] == "deploy":
        _same(body, v["receipt_key"], ("tenant",), "SCOPE_REFUSED")
    return body


def _profile_relations(v: dict[str, Any]) -> None:
    keys = v["control_keys"] + v["database_keys"]
    _unique(keys, ("key_id",))
    public_bindings: dict[str, tuple[str, str, str]] = {}
    for key in keys:
        binding = (key["purpose"], key["subject"], key["audience"])
        previous_binding = public_bindings.setdefault(key["public_key"], binding)
        _need(previous_binding == binding)
    by_id = {k["key_id"]: k for k in keys}
    for purpose, field in (
        ("control-observation", "control_keys"),
        ("database-observation", "database_keys"),
    ):
        for key in v[field]:
            _need(key["purpose"] == purpose, "PURPOSE_REFUSED")
            seen = {key["key_id"]}
            predecessor = key["predecessor_key_id"]
            while predecessor is not None:
                _need(predecessor in by_id and predecessor not in seen)
                seen.add(predecessor)
                previous = by_id[predecessor]
                _need(previous["purpose"] == purpose, "PURPOSE_REFUSED")
                predecessor = previous["predecessor_key_id"]


def _observation_relations(v: dict[str, Any]) -> None:
    payload = v["payload"]
    if v["kind"] == "database":
        _need(v["scope"]["database_binding"] == payload["database_binding"], "SCOPE_REFUSED")
        _same(v, payload["fence"], ("epoch", "candidate_sha256", "trust_profile_sha256"))
        _need(v["run_id"] == payload["fence"]["owner_run_id"])
        _need(v["observed_revision"] == payload["fence"]["revision"])
        for generation in payload["generations"]:
            _need(generation["binding"]["scope"] == v["scope"], "SCOPE_REFUSED")
            _need(generation["binding"]["epoch"] <= v["epoch"], "STALE_EPOCH")
    else:
        _need(v["observed_revision"] == payload["operation_record_revision"])
        for resource in payload["managed_resources"]:
            _need(resource["scope"] == v["scope"], "SCOPE_REFUSED")
        for entry in payload["journal"]:
            _need(entry["epoch"] <= v["epoch"], "STALE_EPOCH")
            if entry["epoch"] == v["epoch"]:
                _need(entry["logical_run_id"] == v["run_id"])


def _database_relations(v: dict[str, Any]) -> None:
    for field, names in (
        ("roles", ("oid",)),
        ("roles", ("name",)),
        ("sessions", ("pid", "backend_start_unix_us")),
        ("routes", ("route_id",)),
        ("prepared_transactions", ("transaction_gid_sha256",)),
        ("memberships", ("member_oid", "role_oid", "grantor_oid")),
    ):
        _unique(v[field], names)
    generations = {g["binding"]["generation_id"]: g for g in v["generations"]}
    _need(len(generations) == len(v["generations"]))
    roles = {r["oid"]: r for r in v["roles"]}
    routes = {r["route_id"]: r for r in v["routes"]}
    for row in v["memberships"]:
        _need(all(row[k] in roles for k in ("member_oid", "role_oid", "grantor_oid")))
    for row in v["routes"]:
        _need(all(oid in roles for oid in row["backend_login_oids"]))
    for row in v["sessions"] + v["prepared_transactions"]:
        _need(row["database_oid"] == v["database_binding"]["database_oid"], "SCOPE_REFUSED")
        _need(row["login_oid"] in roles)
        if "route_id" in row:
            _need(row["current_role_oid"] in roles and row["route_id"] in routes)
        if row["owned"]:
            _need(row["generation_id"] in generations)
    for row in v["roles"]:
        if row["owned"]:
            _need(row["generation_id"] in generations)
    for g in generations.values():
        core = g["binding"]
        _need(core["scope"]["database_binding"] == v["database_binding"], "SCOPE_REFUSED")
        _need(core["login_oid"] in roles)
        role = roles[core["login_oid"]]
        _need(
            role["name"] == core["login_name"]
            and role["owned"]
            and role["generation_id"] == core["generation_id"]
        )
        _need(role["can_login"] == (g["status"] == "OPEN"))


def _journal_relations(v: dict[str, Any]) -> None:
    state = v["state"]
    reservation, outcome, settlement, cancel = (
        v[k] for k in ("reservation", "provider_outcome", "settlement", "cancellation_unsent_proof")
    )
    if state == "INTENT":
        _need(all(x is None for x in (reservation, outcome, settlement, cancel)))
    elif state == "RESERVED":
        _need(reservation is not None and all(x is None for x in (outcome, settlement, cancel)))
    elif state in {"DISPATCHED_UNKNOWN", "RECONCILING", "ACKNOWLEDGED"}:
        _need(reservation is not None and settlement is None and cancel is None)
        _need(state != "ACKNOWLEDGED" or outcome is not None)
    elif state == "SETTLED":
        _need(reservation is not None and settlement is not None and cancel is None)
        if outcome is None:
            _need(
                settlement["settlement_class"] == "NO_EFFECT"
                and len(settlement["resource_proofs"]) > 0
                and all(p["terminal_kind"] == "NO_EFFECT" for p in settlement["resource_proofs"])
            )
        _need(settlement["dispatch_terminal"]["invocation_id"] == reservation["invocation_id"])
    elif state == "REJECTED":
        _need(outcome is not None and settlement is None and cancel is None)
        _need(not outcome["returned_resource_ids"] and bool(outcome["failures"]))
        _need(
            all(
                f["code"] in {"CAPACITY", "PLACEMENT", "PERMISSION", "INVALID_REQUEST"}
                for f in outcome["failures"]
            )
        )
    elif state == "CANCELLED_UNSENT":
        _need(cancel is not None and outcome is None and settlement is None)
        _need(cancel["status"] == "CANCELLED_BEFORE_SEND")
        if reservation is not None:
            _need(cancel["invocation_id"] == reservation["invocation_id"])
    else:
        _fail()
    if reservation is not None:
        _need(reservation["intent_sha256"] == v["intent_sha256"])
        _need(reservation["credential_session"]["generation_id"] == v["generation_id"])
    for proof in (cancel, settlement["dispatch_terminal"] if settlement else None):
        if proof is not None:
            _need(proof["intent_sha256"] == v["intent_sha256"])
    if settlement is not None:
        _need(all(p["generation_id"] == v["generation_id"] for p in settlement["resource_proofs"]))
        _unique(settlement["resource_proofs"], ("resource_id",))
        if settlement["settlement_class"] == "RETIRED_RESOURCES_TERMINAL":
            _need(bool(settlement["resource_proofs"]))
            if outcome is not None:
                _need(
                    set(outcome["returned_resource_ids"])
                    <= {p["resource_id"] for p in settlement["resource_proofs"]}
                )


def _task_identity(resource: dict[str, Any], task: dict[str, Any]) -> bool:
    return all(resource[k] == task[k] for k in ("generation_id", "image_sha256", "config_set_sha256")) and (
        resource["task_definition_revision"] == task["task_definition"]
    )


def _live_ready(payload: dict[str, Any], resource: dict[str, Any]) -> bool:
    """Structural current readiness; qualified positive readiness is also mandatory."""
    tasks = [t for t in payload["tasks"] if t["task_arn"] == resource["provider_resource_id"]]
    return (
        resource["desired_state"] == resource["current_observed_state"] == "READY"
        and resource["retired_terminal_proof_sha256"] is None
        and len(tasks) == 1
        and tasks[0]["last_status"] == "RUNNING"
        and _task_identity(resource, tasks[0])
    )


def _terminal_witness(payload: dict[str, Any], resource: dict[str, Any]) -> bool:
    """Structural retained StopTask witness; matching bytes never confer authority."""
    if (
        resource["desired_state"] not in {"STOPPED", "ABSENT"}
        or resource["current_observed_state"] not in {"STOPPED", "ABSENT"}
        or resource["retired_terminal_proof_sha256"] is None
    ):
        return False
    creators = [
        j for j in payload["journal"] if j["external_operation_id"] == resource["external_operation_id"]
    ]
    if len(creators) != 1:
        return False
    creator = creators[0]
    if (
        creator["state"] != "SETTLED"
        or creator["settlement"] is None
        or creator["settlement"]["settlement_class"] != "CURRENT_LIVE_TRACKED"
        or creator["action"] != "RunTask"
        or creator["generation_id"] != resource["generation_id"]
    ):
        return False
    for task in payload["tasks"]:
        if task["task_arn"] == resource["provider_resource_id"] and (
            task["last_status"] != "STOPPED" or not _task_identity(resource, task)
        ):
            return False
    witnesses = []
    for journal in payload["journal"]:
        settlement = journal["settlement"]
        if (
            journal["external_operation_id"] == creator["external_operation_id"]
            or journal["action"] != "StopTask"
            or journal["state"] != "SETTLED"
            or journal["generation_id"] != resource["generation_id"]
            or journal["epoch"] < creator["epoch"]
            or settlement is None
            or settlement["settlement_class"] != "RETIRED_RESOURCES_TERMINAL"
        ):
            continue
        proofs = settlement["resource_proofs"]
        if len(proofs) == 1 and proofs[0]["resource_id"] == resource["provider_resource_id"]:
            witnesses.append(proofs[0])
    return (
        len(witnesses) == 1
        and witnesses[0]["generation_id"] == resource["generation_id"]
        and witnesses[0]["terminal_kind"] == "TASK_STOPPED"
        and witnesses[0]["observation_sha256"] == resource["observation_sha256"]
        and _sha(witnesses[0]) == resource["retired_terminal_proof_sha256"]
    )


def live_ready(payload: ControlPayload, resource: ManagedResource) -> bool:
    return _live_ready(payload.to_wire(), resource.to_wire())


def task_terminal_witness(payload: ControlPayload, resource: ManagedResource) -> bool:
    return _terminal_witness(payload.to_wire(), resource.to_wire())


def _control_relations(v: dict[str, Any]) -> None:
    for field, names in (
        ("services", ("service_arn",)),
        ("task_definitions", ("arn",)),
        ("tasks", ("task_arn",)),
        ("start_paths", ("path_id",)),
        ("journal", ("external_operation_id",)),
        ("live_dispatchers", ("invocation_id",)),
        ("credential_sessions", ("session_id",)),
        ("managed_resources", ("provider_resource_id",)),
    ):
        _unique(v[field], names)
    tasks = {t["task_arn"]: t for t in v["tasks"]}
    definitions = {t["arn"]: t for t in v["task_definitions"]}
    sessions = {s["session_id"] for s in v["credential_sessions"]}
    for task in v["tasks"]:
        _need(definitions.get(task["task_definition"]["arn"]) == task["task_definition"])
        _need(set(task["credential_session_ids"]) <= sessions)
    for resource in v["managed_resources"]:
        task = tasks.get(resource["provider_resource_id"])
        if task is not None:
            _need(_task_identity(resource, task))
        if resource["retired_terminal_proof_sha256"] is not None:
            _need(_terminal_witness(v, resource), "PENDING_UNKNOWN")
    for journal in v["journal"]:
        settlement = journal["settlement"]
        if settlement and settlement["settlement_class"] == "CURRENT_LIVE_TRACKED":
            resources = [
                r
                for r in v["managed_resources"]
                if r["external_operation_id"] == journal["external_operation_id"]
            ]
            _need(bool(resources))
            for r in resources:
                _need(r["generation_id"] == journal["generation_id"])
            if journal["provider_outcome"]:
                _need(
                    set(journal["provider_outcome"]["returned_resource_ids"])
                    == {r["provider_resource_id"] for r in resources}
                )


def _intent_relations(v: dict[str, Any]) -> None:
    raw = decode_base64(v["request_bytes"])
    _need(raw == canonical_json(v["request"]) and hashlib.sha256(raw).hexdigest() == v["request_sha256"])
    _need(
        v["target_generation"] == v["admission_generation"] == v["credential_binding"]["generation_id"],
        "STALE_GENERATION",
    )
    if v["action"] == "RunTask":
        request = v["request"]
        _need(request["cluster"] == v["target_arn"])
        _need(request["taskDefinition"] == v["task_definition_revision"]["arn"])
        _need(request["clientToken"] == v["client_token"])
        _need(request["startedBy"] == v["logical_run_id"])
        network = v["network_binding"]
        _need(
            request["networkConfiguration"]["awsvpcConfiguration"]
            == {
                "subnets": network["subnet_ids"],
                "securityGroups": network["security_group_ids"],
                "assignPublicIp": network["assign_public_ip"],
            }
        )
    else:
        _need(v["request"]["task"] == v["target_arn"])


def _decision_relations(v: dict[str, Any]) -> None:
    core = v["generation_core"]
    _same(v, core, ("scope", "epoch", "purpose", "image_sha256", "config_set_sha256"))
    for op in ("deployment", "grant"):
        key = v[op + "_receipt"]["receipt_key"]
        _scope_key(v["scope"], key, "deploy" if op == "deployment" else op)
        _need(key["run_id"] == v["run_id"])


def _generation_read_relations(v: dict[str, Any]) -> None:
    obs = v["signed_observation"]
    matches = [
        g for g in obs["payload"]["generations"] if g["binding"]["generation_id"] == v["generation_id"]
    ]
    _need(len(matches) == 1)
    g = matches[0]
    _same(v, g, ("status", "revision"))
    _need(v["epoch"] == g["binding"]["epoch"] and v["binding_sha256"] == _sha(g["binding"]))


def _active_relations(v: dict[str, Any]) -> None:
    b = v["runtime_activation_binding"]
    c, d = v["controller_observation"], v["database_admission_observation"]
    _same(c, d, ("scope", "run_id", "epoch", "candidate_sha256", "request_sha256", "trust_profile_sha256"))
    _need(b["scope"] == c["scope"] and b["controller_epoch"] == c["epoch"])
    _need(b["trust_profile_sha256"] == c["trust_profile_sha256"])
    _need(c["run_id"] == b["deployment_receipt_key"]["run_id"])
    cp, dp = c["payload"], d["payload"]
    _need(cp["controller_state"] == "ACTIVE" and dp["fence"]["state"] == "OPEN")
    _need(cp["journal_revision"] == b["journal_revision"])
    _need(not cp["live_dispatchers"] and cp["pending_count"] == 0, "PENDING_UNKNOWN")
    generations = [
        g for g in dp["generations"] if g["binding"]["generation_id"] == b["runtime_admission_generation"]
    ]
    _need(len(generations) == 1)
    g = generations[0]
    _need(g["status"] == "OPEN" and g["binding"]["purpose"] == "runtime")
    _need(g["activation_decision_sha256"] == b["activation_decision_sha256"])
    _same(g["binding"], b, ("scope", "image_sha256", "config_set_sha256"))
    _need(g["binding"]["epoch"] == b["controller_epoch"])
    _need(_sha(g["binding"]["task_definition_revision"]) == b["task_definition_binding_sha256"])
    _need(bool(cp["managed_resources"]))
    for resource in cp["managed_resources"]:
        if resource["generation_id"] == b["runtime_admission_generation"]:
            _need(_live_ready(cp, resource), "PENDING_UNKNOWN")
            _need(resource["task_definition_revision"] == g["binding"]["task_definition_revision"])
        else:
            _need(
                _terminal_witness(cp, resource),
                "PENDING_UNKNOWN",
            )
    _need(any(r["generation_id"] == b["runtime_admission_generation"] for r in cp["managed_resources"]))
    for journal in cp["journal"]:
        _need(journal["state"] in {"SETTLED", "REJECTED", "CANCELLED_UNSENT"}, "PENDING_UNKNOWN")
        if (
            journal["state"] == "SETTLED"
            and journal["generation_id"] != b["runtime_admission_generation"]
            and journal["settlement"]["settlement_class"] == "CURRENT_LIVE_TRACKED"
        ):
            linked = [
                r
                for r in cp["managed_resources"]
                if r["external_operation_id"] == journal["external_operation_id"]
            ]
            _need(bool(linked) and all(_terminal_witness(cp, r) for r in linked), "PENDING_UNKNOWN")


def signature_input(observation: ControlObservation | DatabaseObservation) -> bytes:
    """Prepare detached bytes only; no signing, key acceptance or signature verification."""
    _need(type(observation) in {ControlObservation, DatabaseObservation})
    body = observation.to_wire()
    _validate(type(observation).__name__, body)
    del body["signature"]
    return (
        b"maezo.provisioning-observation.v1\x00"
        + cast(str, body["kind"]).encode("ascii")
        + b"\x00"
        + canonical_json(body)
    )


def check_generation_decision(
    generation: RuntimeAdmissionGeneration, decision: CandidateDecision | ActivationDecision
) -> None:
    """Compare immutable identity/digests; does not approve allocation or transition."""
    _need(
        type(generation) is RuntimeAdmissionGeneration
        and type(decision) in {CandidateDecision, ActivationDecision}
    )
    _need(generation.binding == decision.generation_core)
    _need(generation.binding.purpose == decision.purpose, "PURPOSE_REFUSED")
    _need(generation.activation_decision_sha256 == decision.digest())


def check_native_receipt(request: DeployRequest | GrantRequest, receipt: NativeReceipt) -> None:
    """Compare historical logical request/result/provenance, never current retry permits."""
    _need(type(request) in {DeployRequest, GrantRequest} and type(receipt) is NativeReceipt)
    req, got = request.to_wire(), receipt.to_wire()
    _same(req, got, ("scope", "source", "tree", "trust_profile_sha256"))
    _need(req["native_image_sha256"] == got["image_sha256"] and req["native_jar_sha256"] == got["jar_sha256"])
    _need(got["request_sha256"] == request.digest())
    _need(
        req["operation"] == got["receipt_key"]["operation"] and req["run_id"] == got["receipt_key"]["run_id"]
    )
    body = _result_body(got)
    payload = req["payload"]
    _need(body["before_inventory_sha256"] == req["expected_before_state_sha256"])
    if req["operation"] == "deploy":
        _need(body["deployment_name"] == payload["deployment_name"])
        expected = [
            {k: r[k] for k in ("name", "kind", "sha256", "byte_length")} for r in payload["resources"]
        ]
        actual = [{k: r[k] for k in ("name", "kind", "sha256", "byte_length")} for r in body["resources"]]
        _need(actual == expected)
        definitions = sorted(
            (d["kind"], d["key"], r["name"]) for r in payload["resources"] for d in r["expected_definitions"]
        )
        _need(definitions == sorted((d["kind"], d["key"], d["resource_name"]) for d in body["definitions"]))
    else:
        _same(payload, body, ("deployment_receipt", "compiler_plan_sha256"))
        _need(body["boundary_sha256"] == payload["final_boundary_manifest"]["sha256"])
        _need([r["grant"] for r in body["grants"]] == payload["compiler_plan"]["grants"])
        _need(
            sorted((r["engine_user"], r["identity_sha256"]) for r in body["identities"])
            == sorted((r["engine_user"], _sha(r["identity"])) for r in payload["workload_plans"])
        )


def check_intent_binding(
    intent: RunTaskIntent | StopTaskIntent,
    *,
    generation: RuntimeAdmissionGeneration,
    decision: CandidateDecision | ActivationDecision,
    managed_resource: ManagedResource | None,
) -> None:
    """Compare the two unqualified request grammars with their complete immutable bindings.

    The owned StopTask target must be supplied explicitly. Parsed managed resources
    and a matching decision do not establish actual ownership or dispatch authority.
    """
    _need(type(intent) in {RunTaskIntent, StopTaskIntent})
    check_generation_decision(generation, decision)
    value, core = intent.to_wire(), generation.binding.to_wire()
    _same(
        value,
        core,
        (
            "scope",
            "epoch",
            "purpose",
            "image_sha256",
            "config_set_sha256",
            "task_definition_revision",
            "execution_role",
            "task_role",
            "network_binding",
        ),
    )
    _need(intent.admission_generation == generation.binding.generation_id)
    _need(intent.credential_binding.digest() == generation.binding.credential_binding_sha256)
    _need(intent.logical_run_id == decision.run_id)
    _need(intent.expected_precondition.target_generation_status == generation.status)
    _need(intent.request.cluster == decision.topology.cluster_arn)
    if type(intent) is StopTaskIntent:
        _need(type(managed_resource) is ManagedResource)
        assert managed_resource is not None
        _need(intent.target_arn == managed_resource.provider_resource_id)
        _need(intent.admission_generation == managed_resource.generation_id)
        _same(
            value,
            managed_resource.to_wire(),
            ("scope", "task_definition_revision", "image_sha256", "config_set_sha256"),
        )
    else:
        _need(managed_resource is None)


def check_generation_read(read: GenerationObserved, issuer: IssuerReceipt) -> None:
    """Bind the exact selected database generation to its supplied issuer receipt."""
    _need(type(read) is GenerationObserved and type(issuer) is IssuerReceipt)
    _need(read.issuer_receipt_sha256 == issuer.digest())
    _same(
        read.to_wire(), issuer.to_wire(), ("generation_id", "epoch", "status", "revision", "binding_sha256")
    )
    _need(read.signed_observation.scope == issuer.scope)
    generation = next(
        g
        for g in read.signed_observation.payload.generations
        if g.binding.generation_id == read.generation_id
    )
    _need(generation.activation_decision_sha256 == issuer.activation_decision_sha256)
    _need(generation.binding.purpose == issuer.purpose)


def check_issuer_request(
    request: IssuerRequest,
    *,
    generation: RuntimeAdmissionGeneration,
    decision: CandidateDecision | ActivationDecision,
) -> None:
    """Compare a complete corresponding immutable decision, without issuer authority or state changes."""
    _need(type(request) is IssuerRequest)
    check_generation_decision(generation, decision)
    _need(request.scope == generation.binding.scope and request.epoch == generation.binding.epoch)
    _need(request.run_id == decision.run_id)
    body = request.body
    if type(body) is IssuerPrepare:
        _need(body.generation == generation and generation.status == "PREPARED")
    else:
        assert isinstance(body, (IssuerCandidate, IssuerOpen, IssuerRetire))
        _need(body.generation_id == generation.binding.generation_id)
        if type(body) is IssuerCandidate:
            _need(type(decision) is CandidateDecision and generation.binding.purpose == "candidate")
            _need(body.candidate_decision_sha256 == decision.digest())
            assert isinstance(decision, CandidateDecision)
            _need(body.disposal_sha256 == decision.provisioner_disposal_sha256)
            _need(
                body.deployment_receipt == decision.deployment_receipt
                and body.grant_receipt == decision.grant_receipt
            )
        elif type(body) is IssuerOpen:
            _need(type(decision) is ActivationDecision and generation.binding.purpose == "runtime")
            _need(body.activation_decision_sha256 == decision.digest())
        else:
            _need(type(body) is IssuerRetire)


def check_observation_profile(
    observation: ControlObservation | DatabaseObservation, profile: TrustProfile, bounds: InventoryBounds
) -> None:
    """Pure key/profile/window/inventory binding; trusted time and crypto remain unestablished."""
    _need(type(observation) in {ControlObservation, DatabaseObservation})
    _need(type(profile) is TrustProfile and type(bounds) is InventoryBounds)
    _need(observation.scope == profile.scope == bounds.scope, "SCOPE_REFUSED")
    _need(
        observation.trust_profile_sha256 == profile.digest()
        and profile.inventory_bounds_sha256 == bounds.digest()
    )
    keys = profile.control_keys if observation.kind == "control" else profile.database_keys
    selected = [k for k in keys if k.key_id == observation.key_id]
    _need(len(selected) == 1, "AUTH_REFUSED")
    key = selected[0]
    _need(key.lifecycle == "ACTIVE", "AUTH_REFUSED")
    _need(key.issuer == observation.issuer and key.audience == observation.audience, "AUTH_REFUSED")
    _need(key.not_before_ms <= observation.issued_at_ms < observation.expires_at_ms <= key.expires_at_ms)
    entries = {b.collection_path: b for b in bounds.bounds}

    def collect(value: Any, family: str) -> None:
        if type(value) is dict:
            for field, child in value.items():
                collect(child, family + "." + field)
        elif type(value) is list:
            _need(family in entries, "UNAVAILABLE")
            _need(len(value) <= entries[family].max_entries)
            # Every concrete occurrence is checked independently. Entering an
            # array element preserves its schema family, never a numeric alias.
            for child in value:
                collect(child, family)

    collect(observation.payload.to_wire(), observation.kind + ".payload")


def check_activation_read(
    active: ActivationActive,
    *,
    current_binding: RuntimeActivationBinding,
    decision: ActivationDecision,
    generation: RuntimeAdmissionGeneration,
    issuer: IssuerReceipt,
) -> None:
    """Pure P9 cross-record equality, including all 23 explicitly supplied binding fields.

    The caller must obtain the expected binding from trusted current composition.
    Passing caller-created records satisfies no authority/freshness/completeness
    obligation. This function returns no admission object and creates no C capability.
    """
    _need(type(active) is ActivationActive and type(current_binding) is RuntimeActivationBinding)
    _need(type(decision) is ActivationDecision and type(issuer) is IssuerReceipt)
    _need(active.runtime_activation_binding == current_binding)
    check_generation_decision(generation, decision)
    b = current_binding.to_wire()
    d = decision.to_wire()
    _same(
        b,
        d,
        (
            "scope",
            "source",
            "tree",
            "image_sha256",
            "trust_profile_sha256",
            "boundary_sha256",
            "config_set_sha256",
            "settled_journal_sha256",
        ),
    )
    _need(b["controller_epoch"] == d["epoch"])
    for label in ("deployment", "grant"):
        _need(
            b[label + "_receipt_key"] == d[label + "_receipt"]["receipt_key"]
            and b[label + "_result_sha256"] == d[label + "_receipt"]["result_sha256"]
        )
    _need(generation in active.database_admission_observation.payload.generations)
    _need(issuer.scope == generation.binding.scope and issuer.epoch == generation.binding.epoch)
    _need(issuer.generation_id == generation.binding.generation_id and issuer.purpose == "runtime")
    _need(issuer.status == generation.status == "OPEN" and issuer.revision == generation.revision)
    _need(
        issuer.binding_sha256 == generation.binding.digest()
        and issuer.activation_decision_sha256 == decision.digest()
    )
    _need(b["issuer_restore_receipt_sha256"] == issuer.digest())


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """Retained read bytes and stable descriptor metadata, not trusted release authority."""

    data: bytes
    sha256: str
    device: int
    inode: int
    owner: int
    size: int
    mtime_ns: int
    ctime_ns: int


def _file_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def read_protected_file(
    path: str,
    *,
    expected_sha256: str,
    expected_owner: int,
    max_size: int = MAX_BYTES,
    sensitivity: Literal["public", "private"],
) -> FileSnapshot:
    """One no-follow descriptor acquisition, bounded read, retained-byte hash and metadata checks.

    Filesystem integrity is not authentication of caller-supplied hashes or owners.
    Paths and operating-system error text never appear in the public refusal.
    """
    descriptors: list[int] = []
    parents: list[tuple[int, str, int, int]] = []
    try:
        _shape({"$ref": "#/$defs/Sha256"}, expected_sha256)
        _need(type(expected_owner) is int and expected_owner >= 0)
        _need(type(max_size) is int and 0 < max_size <= MAX_BYTES)
        _need(sensitivity in {"public", "private"})
        _need(type(path) is str and path.startswith("/") and "\x00" not in path)
        components = path.split("/")[1:]
        _need(bool(components) and all(c not in {"", ".", ".."} for c in components))
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        parent = os.open("/", directory_flags)
        descriptors.append(parent)
        for component in components[:-1]:
            child = os.open(component, directory_flags, dir_fd=parent)
            descriptors.append(child)
            metadata = os.fstat(child)
            _need(stat.S_ISDIR(metadata.st_mode) and metadata.st_uid in {0, expected_owner})
            # Root-owned sticky scratch directories cannot replace owned entries.
            safe_sticky = metadata.st_uid == 0 and bool(metadata.st_mode & stat.S_ISVTX)
            _need(not metadata.st_mode & 0o022 or safe_sticky)
            parents.append((parent, component, metadata.st_dev, metadata.st_ino))
            parent = child
        leaf = components[-1]
        descriptor = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        _need(stat.S_ISREG(before.st_mode) and before.st_uid == expected_owner)
        _need(before.st_nlink == 1 and not before.st_mode & 0o022)
        _need(sensitivity != "private" or not before.st_mode & 0o077)
        _need(0 < before.st_size <= max_size)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65536, max_size - total + 1))
            if not chunk:
                break
            total += len(chunk)
            _need(total <= max_size)
            chunks.append(chunk)
        after = os.fstat(descriptor)
        current = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        _need(_file_identity(before) == _file_identity(after) == _file_identity(current))
        _need(total == after.st_size)
        for fd, name, device, inode in parents:
            current_parent = os.stat(name, dir_fd=fd, follow_symlinks=False)
            _need(
                stat.S_ISDIR(current_parent.st_mode)
                and (current_parent.st_dev, current_parent.st_ino) == (device, inode)
            )
        data = b"".join(chunks)
        digest = hashlib.sha256(data).hexdigest()
        _need(digest == expected_sha256)
        return FileSnapshot(
            data,
            digest,
            after.st_dev,
            after.st_ino,
            after.st_uid,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
    except Exception:
        raise ContractError("UNAVAILABLE") from None
    finally:
        close_failed = False
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                close_failed = True
        if close_failed:
            raise ContractError("UNAVAILABLE") from None


def parse_protected_file(
    name: str,
    path: str,
    *,
    expected_sha256: str,
    expected_owner: int,
    max_size: int = MAX_BYTES,
    sensitivity: Literal["public", "private"],
) -> Any:
    snapshot = read_protected_file(
        path,
        expected_sha256=expected_sha256,
        expected_owner=expected_owner,
        max_size=max_size,
        sensitivity=sensitivity,
    )
    return parse(name, snapshot.data)


def check_grant_boundary_file(payload: GrantPayload, path: str, *, expected_owner: int) -> FileSnapshot:
    """P3 exact pinned existing B compiler parity through actual protected file acquisition.

    No BlobRef or FileSnapshot supplied by a caller is accepted as file custody.
    Existing B parsing/schema registry selects the definitions; D cannot add one.
    """
    _need(type(payload) is GrantPayload)
    snapshot = read_protected_file(
        path,
        expected_sha256=payload.final_boundary_manifest.sha256,
        expected_owner=expected_owner,
        sensitivity="public",
    )
    try:
        _need(snapshot.size == payload.final_boundary_manifest.byte_length)
        plan = compile_boundary_plan(snapshot.data, expected_sha256=snapshot.sha256)
        _need(canonical_json(plan) == payload.compiler_plan.canonical_bytes())
        _need(_sha(plan) == payload.compiler_plan_sha256 and plan["execution_authorized"] is False)
        # B uses its own pinned parser above. D only projects its already-validated
        # complete nonhuman binding; no B float/clock/identity rules are changed.
        boundary = json.loads(snapshot.data)
        expected: dict[str, dict[str, Any]] = {}
        for peer in boundary["peers"]:
            if peer["purpose"] != "nonhuman":
                continue
            capabilities = []
            for cap in peer["capabilities"]:
                doc = cap["document"]
                capabilities.append(
                    {
                        "capability_sha256": cap["digest"],
                        "registered_schema_id": doc["schema"]["schema_id"],
                        "registered_schema_sha256": _sha(doc["schema"]),
                        "target": doc["target"],
                        "worker_id": doc["worker_id"],
                        "source": {
                            "kind": cap["source_kind"] or "none",
                            "source_target": doc["source_target"],
                            "source_worker_id": cap["source_worker_id"],
                            "attestations": cap["attestations"],
                        },
                    }
                )
            row = {
                "engine_user": peer["engine_user"],
                "identity": peer["identity"],
                "capabilities": capabilities,
            }
            if peer["engine_user"] in expected:
                _need(expected[peer["engine_user"]] == row)
            expected[peer["engine_user"]] = row
        actual = {w.engine_user: w.to_wire() for w in payload.workload_plans}
        _need(actual == expected)
        return snapshot
    except Exception:
        raise ContractError() from None
