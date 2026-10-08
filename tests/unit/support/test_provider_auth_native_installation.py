"""Pure TestOnly admission mechanics; generated signatures are unit data, never independent approval."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import sqlite3
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID
from tests.support import provider_auth_native_installation as helper
from tests.support.provider_auth_native import generate_native_materials
from tests.support.provider_tls_pg import OwnedTlsPostgres, PublishedPostgres

from maezo.gateway.human.auth_profile import Definition, Scope
from maezo.gateway.human.read_profile import digest, parse_model, wire
from maezo.gateway.intake.native_authority import (
    AuthLifecycleConfiguration,
    NativeDatabaseBinding,
    ProtectedDatabaseTlsRoot,
    SourceGrant,
    _database_tls,
)
from maezo.gateway.intake.native_source_lifecycle import IdentitySourceBinding, RelationPin, SourceFunctionPin
from maezo.portal.engine.profile import ProfileError, canonicalize


def artifact(tmp_path: Path, name: str, value: Any, *, media: str = "application/json") -> Any:
    raw = value if isinstance(value, bytes) else canonicalize(value)
    path = tmp_path / name
    path.write_bytes(raw)
    path.chmod(0o400)
    return dict(
        ref="UnitOnly-" + name,
        path=str(path.resolve()),
        sha256=hashlib.sha256(raw).hexdigest(),
        media_type=media,
    )


def jar_bytes(names):
    # Inert archive entries: this checks admission/order/origin, not Java code execution.
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as jar:
        for name in names:
            jar.writestr(name, b"UnitOnly-inert-not-executable")
    return stream.getvalue()


@pytest.fixture
def admitted_unit_case(tmp_path, request):
    """Real unit signatures/files/SQLite, NOT independent receipts or measured installation."""
    now = datetime.now(UTC)
    until = now + timedelta(minutes=1)
    scope = Scope(
        tenant="UnitOnly-tenant",
        environment="TestOnly-unit",
        engine_name="UnitOnly-engine",
        database_incarnation="UnitOnly-incarnation",
        installation_ref="UnitOnly-install",
        installation_revision=1,
    )
    materials = generate_native_materials(
        tmp_path.resolve(),
        owner="provider-unit",
        workload_ref="TestOnly-unit-publisher",
        source_grants=(
            SourceGrant(
                kind="actor",
                source_ref="UnitOnly-source",
                publisher_ref="UnitOnly-publisher",
                resource_ref="UnitOnly-principal",
            ),
        ),
    )
    xml = artifact(tmp_path, "source.xml", b"UnitOnly-not-a-deployed-process", media="application/xml")
    profile = artifact(tmp_path, "profile.json", {"UnitOnly": "not-a-runtime-profile"})
    definition = Definition(
        process_key="SP-OP-AUTH-001",
        definition_id="UnitOnly-not-deployed",
        deployment_id="UnitOnly-not-deployed",
        definition_digest=xml["sha256"],
        input_profile="portal-auth-intake.v1",
        profile_digest=profile["sha256"],
    )
    native_jar = artifact(
        tmp_path,
        "native.jar",
        jar_bytes(["br/com/maezo/human/AuthRuntime.class"]),
        media="application/java-archive",
    )
    support_jar = artifact(
        tmp_path,
        "support.jar",
        jar_bytes(
            [
                "br/com/maezo/human/ProviderAuthTestOwner.class",
                "br/com/maezo/human/ProviderAuthTestComposition.class",
            ]
        ),
        media="application/java-archive",
    )
    jdbc_jar = artifact(
        tmp_path, "jdbc.jar", jar_bytes(["org/postgresql/Driver.class"]), media="application/java-archive"
    )
    signer = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UnitOnly-DB-CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(signer.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(until + timedelta(minutes=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
        )
        .sign(signer, hashes.SHA256())
    )
    root = artifact(
        tmp_path,
        "db-ca.pem",
        ca.public_bytes(serialization.Encoding.PEM),
        media="application/pem-certificate-chain",
    )
    root_record = {"path": root["path"], "sha256": root["sha256"]}
    owned = []
    for kind, ident in (
        ("container", "UnitOnly-not-created-container"),
        ("network", "UnitOnly-not-created-network"),
        ("image", "UnitOnly-phase-b-image"),
    ):
        owned.append(
            dict(
                kind=kind,
                id=ident,
                owner="provider-unit",
                token="UnitOnly-token",
                source_candidate_sha="a" * 40,
                inspect_sha256="f" * 64,
            )
        )
    binding = dict(
        schema="human-auth-native-database.v1",
        database_name="postgres",
        database_oid="1",
        schema_name="maezo_native",
        schema_oid="2",
        owner_role="maezo_native_schema_owner",
        runtime_role="cibseven_app",
    )
    boot_record = dict(
        schema="provider-auth-testonly-boot-switch.v2",
        candidate_sha="a" * 40,
        scope=wire(scope),
        definition=wire(definition),
        phase_a_image_id="UnitOnly-phase-a-image",
        phase_a_descriptor_sha256="c" * 64,
        phase_b_image_id="UnitOnly-phase-b-image",
        phase_b_descriptor_sha256="d" * 64,
        native_jar_sha256=native_jar["sha256"],
        support_jar_sha256=support_jar["sha256"],
        database_binding_sha256=digest(binding),
        observed_at=clock(now),
        valid_until=clock(until),
        outcome="PREPARED_ADMITTED_ONLY",
        phase_b_executed=False,
        evidence=[profile],
    )
    boot = artifact(tmp_path, "boot.json", boot_record)
    measurements = dict(
        schema="provider-auth-testonly-preflight-measurements.v2",
        candidate_sha="a" * 40,
        source_bundle_sha256="b" * 64,
        support_bundle_sha256="c" * 64,
        scope=wire(scope),
        binding=binding,
        definition=wire(definition),
        source_xml=xml,
        retrieved_xml=xml,
        profile_artifact=profile,
        native_jar=native_jar,
        support_jar=support_jar,
        pom={"repository_path": "pom.xml", "git_blob_sha256": "a" * 64},
        maven_compiler_plugin="3.16.0",
        cibseven_version="2.1.0",
        image_id=boot_record["phase_a_image_id"],
        descriptor_sha256="c" * 64,
        database_tls_root=root_record,
        client_ca_sha256=hashlib.sha256(materials.ca_certificate.read_bytes()).hexdigest(),
        server_peer_spki_sha256=materials.proposed_designations[-1].peer_spki_sha256,
        authorization_enabled=True,
        tenant_check_enabled=True,
        schema_update="false",
        role_observations=[
            dict(
                role="unitonly_reader_" + str(i),
                superuser=False,
                bypassrls=False,
                replication=False,
                createdb=False,
                createrole=False,
                membership_digest="a" * 64,
                schema_acl_digest="b" * 64,
                table_column_acl_digest="c" * 64,
            )
            for i in range(3)
        ],
        database_catalog_sha256="a" * 64,
        tls_probe_sha256="b" * 64,
        route_probe_sha256="c" * 64,
        clock_database=clock(now),
        clock_engine=clock(now),
        observed_at=clock(now),
        valid_until=clock(until),
        owned_resources=owned,
        execution_record=profile,
        boot_switch=boot,
    )
    measured_ref = artifact(tmp_path, "measurements.json", measurements)
    q = dict(
        schema="human-auth-installation-qualification.v1",
        definition=wire(definition),
        native_code_digest=native_jar["sha256"],
        source_freeze_contract_digest="b" * 64,
        cutover_ref=boot["ref"],
        review_receipt_ref="UnitOnly-readback.json",
        runtime_qualification_ref=measured_ref["ref"],
        valid_until=clock(until),
    )
    identity = IdentitySourceBinding(
        tenant=scope.tenant,
        installation_ref="UnitOnly-identity-install",
        revision=1,
        database_name="postgres",
        database_oid=1,
        database_incarnation="UnitOnly-source-incarnation",
        source_schema_name="unitonly_identity",
        source_schema_oid=3,
        owner_role="unitonly_owner",
        reader_role="unitonly_reader",
        writer_role="unitonly_writer",
        control_role="unitonly_control",
        receipt_role="unitonly_receipt",
        relations=(),
        functions=tuple(
            SourceFunctionPin(signature=s, oid=i + 1, definition_digest="a" * 64)
            for i, s in enumerate(
                (
                    "portal_auth.source_write_guard()",
                    "portal_auth.apply_change(text,text)",
                    "portal_auth.lock_staff_memberships(text)",
                )
            )
        ),
        scopes=(scope,),
        publisher_ref="UnitOnly-publisher",
        source_ref="UnitOnly-source",
        valid_until=until,
        control_seconds=30,
    )
    native = NativeDatabaseBinding(
        scope=scope,
        database_name="postgres",
        database_oid=1,
        schema_name="maezo_native",
        schema_oid=2,
        owner_role="maezo_native_schema_owner",
        reader_role="unitonly_native_reader",
        relations=tuple(
            RelationPin(schema_name="maezo_native", name=n, oid=i + 4, owner="maezo_native_schema_owner")
            for i, n in enumerate(
                (
                    "mzo_auth_installation",
                    "mzo_auth_trust",
                    "mzo_auth_revoked_key",
                    "mzo_auth_input_head",
                    "mzo_auth_input_version",
                )
            )
        ),
        installed_binding_digest=digest(binding),
        installed_qualification_digest=digest(q),
        valid_until=until,
    )
    config = AuthLifecycleConfiguration(
        schema="human-auth-lifecycle-installation.v1",
        identity=identity,
        native=native,
        client=materials.client_binding(origin="https://localhost:23456", audience="UnitOnly-audience"),
        protected=dict(
            database_name="postgres",
            database_oid=1,
            role="unitonly_protected",
            schema_oid=4,
            relations=(),
            valid_until=until,
        ),
        identity_reader_url="UnitOnly-not-a-used-DSN",
        identity_control_url="UnitOnly-not-a-used-DSN",
        identity_receipt_url="UnitOnly-not-a-used-DSN",
        native_reader_url="UnitOnly-not-a-used-DSN",
        protected_url="UnitOnly-not-a-used-DSN",
        journal_key_id="UnitOnly-journal",
        journal_key_path=materials.journal_key,
        journal_key_digest=hashlib.sha256(materials.journal_key.read_bytes()).hexdigest(),
        intake_key_id="UnitOnly-intake",
        intake_key_path=materials.intake_key,
        intake_key_digest=hashlib.sha256(materials.intake_key.read_bytes()).hexdigest(),
    )
    # The native DTO consumes the number-free Q2 wire, not Pydantic's internal JSON.
    native_wire = wire(native)
    native_raw = canonicalize(native_wire)
    mutation = getattr(request, "param", "valid")
    if mutation == "duplicate":
        native_raw = b'{"database_oid":"1",' + native_raw[1:]
    elif mutation == "noncanonical":
        native_raw += b"\n"
    elif mutation == "numeric":
        native_raw = native_raw.replace(b'"database_oid":"1"', b'"database_oid":1')
    elif mutation == "unknown":
        native_raw = canonicalize({**native_wire, "unknown": "UnitOnly"})
    native_ref = artifact(tmp_path, "native-binding.json", native_raw)
    config_ref = artifact(tmp_path, "lifecycle.json", config.model_dump_json(by_alias=True).encode())

    def secret_ref(p):
        return dict(
            ref="UnitOnly-" + p.name,
            path=str(p),
            sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
            media_type="application/octet-stream",
        )

    owner_jdbc = artifact(
        tmp_path,
        "owner-jdbc.json",
        {"schema": "UnitOnly-no-connection", "password": "UnitOnly-not-a-credential"},
    )
    specs = [dict(action="install", designation=None, revoked_key_id=None)]
    for i, d in enumerate(materials.proposed_designations):
        specs.append(
            dict(
                action="designate",
                designation=artifact(tmp_path, f"designation-{i}.json", wire(d)),
                revoked_key_id=None,
            )
        )
    specs.append(dict(action="readback", designation=None, revoked_key_id=None))
    settings = dict(
        schema="provider-auth-testonly-java-composition.v2",
        scope=wire(scope),
        audience=config.client.audience,
        max_lifetime_seconds="30",
        timeout_seconds="5",
        signing_pkcs12_file=materials.result_pkcs12.name,
        signing_password_file=materials.result_password.name,
        alias="provider-native-result",
        key_id=config.client.native_key_id,
        issuer=materials.proposed_designations[-1].issuer,
        signing_spki_sha256=materials.proposed_designations[-1].peer_spki_sha256,
    )
    runtime = dict(
        schema="provider-auth-testonly-owner-runtime-manifest.v1",
        candidate_sha="a" * 40,
        scope=wire(scope),
        java_settings=settings,
        signing_pkcs12=secret_ref(materials.result_pkcs12),
        signing_password=secret_ref(materials.result_password),
        owner_jdbc_config=owner_jdbc,
        action_specs=specs,
        native_database_binding=native_ref,
        lifecycle_configuration=config_ref,
        origin=config.client.origin,
        owned_resources=owned,
        issued_at=clock(now),
        valid_until=clock(until),
    )
    runtime_ref = artifact(tmp_path, "runtime-manifest.json", runtime)
    cp = dict(
        schema="provider-auth-testonly-owner-classpath-manifest.v1",
        candidate_sha="a" * 40,
        ordered_artifacts=[native_jar, support_jar, jdbc_jar],
    )
    cp_ref = artifact(tmp_path, "classpath.json", cp)
    private_keys = [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]
    entries = []
    for i, role in enumerate(("architecture", "security")):
        entries.append(
            dict(
                verifier_id="/root/unit_" + role,
                verifier_role=role,
                issuer="UnitOnly-issuer-" + role,
                key_id="UnitOnly-key-" + role,
                public_key_spki_base64=base64.b64encode(
                    private_keys[i]
                    .public_key()
                    .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                ).decode(),
                purpose="provider-auth-testonly-preflight",
                allowed_scope="INSTALL_PROVIDER_AUTH_TESTONLY_PRELIMINARY",
                not_before=clock(now),
                not_after=clock(until),
            )
        )
    registry = dict(
        schema="provider-auth-testonly-verifier-registry.v2",
        registry_ref="UnitOnly-registry",
        decision_record=profile,
        candidate_sha="a" * 40,
        installer_author_id="/root/unit_installer",
        helper_author_id="/root/unit_helper",
        repair_author_id="/root/unit_repair",
        installer_support_sha256="c" * 64,
        entries=entries,
        issued_at=clock(now),
        valid_until=clock(until),
    )
    registry_ref = artifact(tmp_path, "registry.json", registry)
    envelopes = []
    readbacks = []
    db_path = tmp_path / "unit-evidence.db"
    with sqlite3.connect(db_path) as db:
        db.execute(
            "CREATE TABLE gateways(id TEXT,verifier TEXT,verdict TEXT,"
            "artifact_sha256 TEXT,evidence_path TEXT)"
        )
        for i, entry in enumerate(entries):
            record = dict(
                schema="provider-auth-testonly-preflight-gate.v2",
                gate_ref="UnitOnly-gate-" + entry["verifier_role"],
                verifier_id=entry["verifier_id"],
                verifier_role=entry["verifier_role"],
                issuer=entry["issuer"],
                checker_candidate_sha="a" * 40,
                checker_source_sha256="d" * 64,
                allowed_scope="INSTALL_PROVIDER_AUTH_TESTONLY_PRELIMINARY",
                verdict="PASS_FOR_TESTONLY_INSTALLATION",
                candidate_sha="a" * 40,
                source_bundle_sha256="b" * 64,
                support_bundle_sha256="c" * 64,
                measurements_sha256=measured_ref["sha256"],
                scope=wire(scope),
                definition=wire(definition),
                binding_sha256=digest(binding),
                database_tls_root_sha256=root["sha256"],
                authority_registry_sha256=registry_ref["sha256"],
                evidence=[cp_ref, runtime_ref],
                issued_at=clock(now),
                valid_until=clock(until),
                not_functional_e04=True,
                not_production_authority=True,
            )
            envelope = dict(
                schema="provider-auth-testonly-preflight-envelope.v2",
                algorithm="Ed25519",
                issuer=entry["issuer"],
                key_id=entry["key_id"],
                record=record,
                record_sha256=digest(record),
            )
            envelope["signature"] = (
                base64.urlsafe_b64encode(private_keys[i].sign(canonicalize(envelope))).decode().rstrip("=")
            )
            ref = artifact(tmp_path, "gate-" + entry["verifier_role"] + ".json", envelope)
            envelopes.append(ref)
            db.execute(
                "INSERT INTO gateways VALUES(?,?,?,?,?)",
                (record["gate_ref"], record["verifier_id"], record["verdict"], ref["sha256"], ref["path"]),
            )
            readbacks.append(
                dict(
                    gate_ref=record["gate_ref"],
                    verifier_id=record["verifier_id"],
                    issuer=entry["issuer"],
                    verdict=record["verdict"],
                    artifact_sha256=ref["sha256"],
                    evidence_path=ref["path"],
                )
            )
    readback = artifact(
        tmp_path,
        "readback.json",
        dict(schema="provider-auth-testonly-preflight-readback.v2", records=readbacks),
    )
    preliminary = dict(
        schema="provider-auth-testonly-preliminary-qualification.v2",
        authority_registry=registry_ref,
        measurements=measured_ref,
        architecture_receipt=envelopes[0],
        security_receipt=envelopes[1],
        readback_record=readback,
        qualification=q,
    )
    prelim_ref = artifact(tmp_path, "preliminary.json", preliminary)
    dispatch = helper.TrustedPreflightDispatch(
        "a" * 40,
        registry_ref["sha256"],
        "b" * 64,
        "c" * 64,
        db_path.resolve(),
        measured_ref["sha256"],
        "d" * 64,
        scope,
        definition,
        cp_ref,
        runtime_ref,
        (),
    )
    output = dict(
        schema="provider-auth-native-test-installation-result.v2",
        owner="provider-unit",
        candidate_sha="a" * 40,
        scope=wire(scope),
        definition=wire(definition),
        native_jar=native_jar,
        support_jar=support_jar,
        image_id=boot_record["phase_b_image_id"],
        origin=runtime["origin"],
        native_database_binding=native_ref,
        lifecycle_configuration=config_ref,
        database_tls_root=root_record,
        preliminary_qualification=prelim_ref,
        owner_action_results=[],
        owned_resources=owned,
        installed_at=clock(now),
        valid_until=clock(until),
        e04_final_qualified=False,
        activation_admitted=False,
    )
    yield SimpleNamespace(
        now=now,
        until=until,
        dispatch=dispatch,
        preliminary=preliminary,
        prelim_ref=prelim_ref,
        measurements=measurements,
        runtime=runtime,
        classpath=cp,
        config=config,
        native=native,
        identity=identity,
        output=output,
        materials=materials,
        tmp_path=tmp_path,
    )
    materials.remove_owned_files()


def clock(value: datetime) -> Any:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@pytest.fixture
def signature_case():
    now = datetime.now(UTC)
    private = Ed25519PrivateKey.generate()
    scope = dict(
        tenant="TestOnly-tenant",
        environment="TestOnly-environment",
        engine_name="TestOnly-engine",
        database_incarnation="TestOnly-incarnation",
        installation_ref="TestOnly-installation",
        installation_revision="1",
    )
    definition = dict(
        process_key="SP-OP-AUTH-001",
        definition_id="Unit-only-not-deployed",
        definition_digest="a" * 64,
        deployment_id="Unit-only-not-deployed",
        input_profile="portal-auth-intake.v1",
        profile_digest="b" * 64,
    )
    artifact = dict(
        ref="UnitOnly-evidence",
        path="/UnitOnly-not-installed.json",
        sha256="a" * 64,
        media_type="application/json",
    )
    record = dict(
        schema="provider-auth-testonly-preflight-gate.v2",
        gate_ref="UnitOnly-not-registered",
        verifier_id="/root/unit_probe",
        verifier_role="architecture",
        issuer="UnitOnly-issuer",
        checker_candidate_sha="a" * 40,
        checker_source_sha256="a" * 64,
        allowed_scope="INSTALL_PROVIDER_AUTH_TESTONLY_PRELIMINARY",
        verdict="PASS_FOR_TESTONLY_INSTALLATION",
        candidate_sha="a" * 40,
        source_bundle_sha256="a" * 64,
        support_bundle_sha256="a" * 64,
        measurements_sha256="a" * 64,
        scope=scope,
        definition=definition,
        binding_sha256="a" * 64,
        database_tls_root_sha256="a" * 64,
        authority_registry_sha256="a" * 64,
        evidence=[artifact],
        issued_at=clock(now),
        valid_until=clock(now + timedelta(minutes=1)),
        not_functional_e04=True,
        not_production_authority=True,
    )
    entry = dict(schema="ignored")
    entry = dict(
        verifier_id=record["verifier_id"],
        verifier_role="architecture",
        issuer=record["issuer"],
        key_id="UnitOnly-key",
        public_key_spki_base64=base64.b64encode(
            private.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).decode(),
        purpose="provider-auth-testonly-preflight",
        allowed_scope="INSTALL_PROVIDER_AUTH_TESTONLY_PRELIMINARY",
        not_before=clock(now - timedelta(minutes=1)),
        not_after=clock(now + timedelta(minutes=2)),
    )
    envelope = dict(
        schema="provider-auth-testonly-preflight-envelope.v2",
        algorithm="Ed25519",
        issuer=entry["issuer"],
        key_id=entry["key_id"],
        record=record,
        record_sha256=digest(record),
    )
    envelope["signature"] = (
        base64.urlsafe_b64encode(private.sign(canonicalize(envelope))).decode().rstrip("=")
    )
    return envelope, entry


def test_actual_ed25519_signature_matches_independently_supplied_public_key(signature_case):
    helper.verify_gate_signature(*signature_case)


@pytest.mark.parametrize("field", ["signature", "issuer", "key_id", "record_sha256", "record", "entry"])
def test_signature_and_issuer_substitution_refuses_before_owner_install(signature_case, field):
    envelope, entry = copy.deepcopy(signature_case)
    if field == "record":
        envelope["record"]["candidate_sha"] = "b" * 40
        envelope["record_sha256"] = digest(envelope["record"])
    elif field == "entry":
        entry["public_key_spki_base64"] = base64.b64encode(
            Ed25519PrivateKey.generate()
            .public_key()
            .public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        ).decode()
    else:
        envelope[field] = {
            "signature": "a" * 86,
            "issuer": "UnitOnly-other",
            "key_id": "UnitOnly-other",
            "record_sha256": "b" * 64,
        }[field]
    with pytest.raises(helper.NativeInstallationError):
        helper.verify_gate_signature(envelope, entry)


@pytest.mark.parametrize(
    "kind",
    [
        "GateEnvelope",
        "GateRecord",
        "VerifierRegistry",
        "PreliminaryQualification",
        "ProtectedJavaConfiguration",
        "OwnerActionInput",
        "OwnerActionResult",
        "InstallerInput",
        "InstallerOutput",
    ],
)
def test_missing_closed_prerequisites_are_not_defaulted_to_approval(kind):
    with pytest.raises(helper.NativeInstallationError):
        helper.validate_record(kind, {})


def test_extra_fields_and_final_e04_claim_are_refused(signature_case):
    envelope, entry = signature_case
    with pytest.raises(helper.NativeInstallationError):
        helper.validate_record("GateEnvelope", {**envelope, "approved": True})
    with pytest.raises(helper.NativeInstallationError):
        helper.validate_record("GateRecord", {**envelope["record"], "not_functional_e04": False})


def test_definition_requires_canonical_six_fields_and_exact_profile(signature_case):
    d = signature_case[0]["record"]["definition"]
    helper.validate_record("Definition", d)
    for field in d:
        with pytest.raises(helper.NativeInstallationError):
            helper.validate_record("Definition", {k: v for k, v in d.items() if k != field})
    with pytest.raises(helper.NativeInstallationError):
        helper.validate_record("Definition", {**d, "key": "SP-OP-AUTH-001"})


def test_public_jar_byte_hash_read_is_not_limited_by_private_secret_file_size(tmp_path):
    raw = b"unit-public-artifact-bytes" * 15000
    path = tmp_path / "unit.jar"
    path.write_bytes(raw)
    path.chmod(0o644)
    ref = dict(
        ref="UnitOnly-opaque-artifact",
        path=str(path.resolve()),
        sha256=hashlib.sha256(raw).hexdigest(),
        media_type="application/java-archive",
    )
    assert helper._artifact_bytes(ref) == raw
    path.chmod(0o666)
    with pytest.raises(helper.NativeInstallationError):
        helper._artifact_bytes(ref)


def test_actual_byte_drift_and_symlink_artifacts_refuse(tmp_path):
    path = tmp_path / "evidence.json"
    path.write_bytes(b"{}")
    path.chmod(0o400)
    ref = dict(
        ref="UnitOnly-evidence",
        path=str(path.resolve()),
        sha256=hashlib.sha256(b"{}").hexdigest(),
        media_type="application/json",
    )
    target = tmp_path / "different"
    target.write_bytes(b"different")
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(helper.NativeInstallationError):
        helper._artifact_bytes(ref)


def unit_action_readbacks(case: Any) -> Any:
    """Synthetic chronology only; no connection, commit or DB receipt is claimed."""
    inputs = []
    results = []
    for i, spec in enumerate(case.runtime["action_specs"]):
        action = dict(
            schema="provider-auth-testonly-owner-action.v2",
            **spec,
            owner="provider-unit",
            candidate_sha=case.dispatch.candidate_sha,
            scope=wire(case.dispatch.expected_scope),
            binding=case.measurements["binding"],
            preliminary_qualification=case.prelim_ref,
            owner_jdbc_config=case.runtime["owner_jdbc_config"],
            expected_installation_revision="1",
            issued_at=clock(case.now),
            valid_until=clock(case.until),
        )
        ref = artifact(case.tmp_path, f"unit-action-{i}.json", action)
        inputs.append(ref)
        observed = case.now + timedelta(microseconds=i * 2)
        record = dict(
            schema="provider-auth-testonly-owner-action-result.v2",
            action=action["action"],
            request_sha256=ref["sha256"],
            candidate_sha=action["candidate_sha"],
            scope=action["scope"],
            binding_sha256=digest(action["binding"]),
            preliminary_qualification_sha256=case.prelim_ref["sha256"],
            outcome="COMMITTED_AND_READ_BACK",
            installation_revision="1",
            installation_binding_sha256=digest(action["binding"]),
            installation_qualification_sha256=digest(case.preliminary["qualification"]),
            designation_sha256=None
            if action["designation"] is None
            else digest(helper._read(action["designation"])),
            revoked_key_id=None,
            catalog_sha256="e" * 64,
            committed_at=clock(observed),
            read_back_at=clock(observed + timedelta(microseconds=1)),
            database_observed_at=clock(observed),
        )
        results.append(artifact(case.tmp_path, f"unit-result-{i}.json", record))
    return replace(case.dispatch, runtime_action_inputs=tuple(inputs)), {
        **case.output,
        "owner_action_results": results,
    }


def test_exact_initial_action_set_accepts_unit_correspondence_only(admitted_unit_case):
    case = admitted_unit_case
    dispatch, output = unit_action_readbacks(case)
    helper.verify_installation_action_set(
        output,
        preliminary=case.preliminary,
        dispatch=dispatch,
        runtime=case.runtime,
        materials=case.materials,
        now=datetime.now(UTC),
    )


@pytest.mark.parametrize(
    "mutation", ["missing", "duplicate", "qualification", "target", "chronology", "expired"]
)
def test_incomplete_or_wrong_owner_readbacks_refuse(admitted_unit_case, mutation):
    case = admitted_unit_case
    dispatch, output = unit_action_readbacks(case)
    if mutation == "missing":
        output["owner_action_results"].pop()
    elif mutation == "duplicate":
        output["owner_action_results"][-1] = output["owner_action_results"][0]
    else:
        result = helper._read(output["owner_action_results"][-1])
        if mutation == "qualification":
            result["installation_qualification_sha256"] = "f" * 64
        elif mutation == "target":
            result["request_sha256"] = "f" * 64
        elif mutation == "chronology":
            result["committed_at"] = clock(case.now + timedelta(seconds=3))
        else:
            result["read_back_at"] = clock(case.until + timedelta(seconds=1))
        output["owner_action_results"][-1] = artifact(case.tmp_path, "wrong-owner-readback.json", result)
    with pytest.raises(helper.NativeInstallationError):
        helper.verify_installation_action_set(
            output,
            preliminary=case.preliminary,
            dispatch=dispatch,
            runtime=case.runtime,
            materials=case.materials,
            now=datetime.now(UTC),
        )


def test_replacement_profile_not_named_by_both_signed_unit_receipts_refuses(admitted_unit_case):
    case = admitted_unit_case
    replacement = artifact(case.tmp_path, "substituted-runtime-profile.json", case.runtime)
    changed = replace(case.dispatch, owner_runtime_manifest=replacement)
    with pytest.raises(helper.NativeInstallationError, match="Both independent receipts"):
        helper.verify_preliminary_qualification(case.prelim_ref, dispatch=changed)


def test_complete_signed_unit_preliminary_and_additive_profiles_are_accepted(admitted_unit_case):
    case = admitted_unit_case
    assert (
        helper.verify_preliminary_qualification(case.prelim_ref, dispatch=case.dispatch) == case.preliminary
    )
    helper.verify_owner_classpath(tuple(case.classpath["ordered_artifacts"]), case.classpath)
    helper.verify_installed_bindings(
        case.output,
        case.native,
        case.config,
        dispatch=case.dispatch,
        preliminary=case.preliminary,
        runtime=case.runtime,
        expected_identity=case.identity,
        now=datetime.now(UTC),
    )


@pytest.mark.parametrize("mutation", ["extra", "missing", "order", "duplicate", "conflicting_origin"])
def test_entire_classpath_counterexamples_refuse(admitted_unit_case, mutation):
    case = admitted_unit_case
    supplied = list(case.classpath["ordered_artifacts"])
    admitted = copy.deepcopy(case.classpath)
    if mutation == "extra":
        supplied.append(
            artifact(
                case.tmp_path,
                "extra.jar",
                jar_bytes(["UnitOnly/Extra.class"]),
                media="application/java-archive",
            )
        )
    elif mutation == "missing":
        supplied.pop()
    elif mutation == "order":
        supplied.reverse()
    elif mutation == "duplicate":
        supplied.append(supplied[-1])
    else:
        supplied[-1] = artifact(
            case.tmp_path,
            "conflict.jar",
            jar_bytes(["br/com/maezo/human/ProviderAuthTestOwner.class"]),
            media="application/java-archive",
        )
        admitted["ordered_artifacts"] = supplied
    with pytest.raises(helper.NativeInstallationError):
        helper.verify_owner_classpath(tuple(supplied), admitted)


@pytest.mark.parametrize(
    "field",
    [
        "native_scope",
        "native_binding",
        "native_qualification",
        "output_deadline",
        "output_jar",
        "image",
        "origin",
        "resources",
    ],
)
def test_original_native_scope_and_admission_binding_counterexamples_refuse(admitted_unit_case, field):
    case = admitted_unit_case
    native, config, output = case.native, case.config, copy.deepcopy(case.output)
    if field == "native_scope":
        scope = Scope.model_validate(
            {**native.scope.model_dump(), "installation_ref": "UnitOnly-other"}, strict=True
        )
        native = NativeDatabaseBinding.model_validate({**native.model_dump(), "scope": scope}, strict=True)
        config = AuthLifecycleConfiguration.model_validate(
            {**config.model_dump(), "native": native}, strict=True
        )
    elif field.startswith("native_"):
        key = {
            "native_binding": "installed_binding_digest",
            "native_qualification": "installed_qualification_digest",
        }[field]
        native = NativeDatabaseBinding.model_validate({**native.model_dump(), key: "f" * 64}, strict=True)
        config = AuthLifecycleConfiguration.model_validate(
            {**config.model_dump(), "native": native}, strict=True
        )
    elif field == "output_deadline":
        output["valid_until"] = clock(case.now - timedelta(seconds=1))
    elif field == "output_jar":
        output["native_jar"] = {**output["native_jar"], "sha256": "f" * 64}
    elif field == "image":
        output["image_id"] = "UnitOnly-other-image"
    elif field == "origin":
        output["origin"] = "https://localhost:23457"
    else:
        output["owned_resources"][0]["token"] = "UnitOnly-other-token"
    with pytest.raises(helper.NativeInstallationError):
        helper.verify_installed_bindings(
            output,
            native,
            config,
            dispatch=case.dispatch,
            preliminary=case.preliminary,
            runtime=case.runtime,
            expected_identity=case.identity,
            now=datetime.now(UTC),
        )


def test_fixed_initial_now_cannot_hide_expiry_after_artifact_io(admitted_unit_case, monkeypatch):
    case = admitted_unit_case
    real_read = helper._read

    class UnitClock(datetime):
        advanced = False

        @classmethod
        def now(cls, tz=None):
            return case.now + timedelta(minutes=2) if cls.advanced else case.now

    def advancing_read(ref, **kwargs):
        result = real_read(ref, **kwargs)
        if ref == case.dispatch.owner_runtime_manifest:
            UnitClock.advanced = True
        return result

    monkeypatch.setattr(helper, "datetime", UnitClock)
    monkeypatch.setattr(helper, "_read", advancing_read)
    with pytest.raises(helper.NativeInstallationError, match="expired"):
        helper.verify_preliminary_qualification(case.prelim_ref, dispatch=case.dispatch, now=case.now)


@pytest.mark.parametrize("mutation", ["mode", "pin", "symlink", "parent_mode", "owner"])
def test_protected_secret_custody_refuses_without_reading_owner_credentials(tmp_path, monkeypatch, mutation):
    path = tmp_path / "UnitOnly-password.txt"
    ref = artifact(tmp_path, path.name, b"UnitOnly-not-a-password", media="text/plain")
    assert helper._protected_material(ref) == b"UnitOnly-not-a-password"
    if mutation == "mode":
        path.chmod(0o644)
    elif mutation == "pin":
        ref["sha256"] = "f" * 64
    elif mutation == "symlink":
        path.unlink()
        path.symlink_to(tmp_path / "not-present")
    elif mutation == "parent_mode":
        tmp_path.chmod(0o755)
    else:
        monkeypatch.setattr(helper.os, "geteuid", lambda: 987654321)
    try:
        with pytest.raises(helper.NativeInstallationError):
            helper._protected_material(ref)
    finally:
        tmp_path.chmod(0o700)


def test_runtime_action_custody_does_not_override_signed_specs_or_ceilings(admitted_unit_case):
    case = admitted_unit_case
    action = dict(
        schema="provider-auth-testonly-owner-action.v2",
        action="install",
        owner="provider-unit",
        candidate_sha=case.dispatch.candidate_sha,
        scope=wire(case.dispatch.expected_scope),
        binding=case.measurements["binding"],
        preliminary_qualification=case.prelim_ref,
        owner_jdbc_config=case.runtime["owner_jdbc_config"],
        designation=None,
        revoked_key_id=None,
        expected_installation_revision="1",
        issued_at=clock(case.now),
        valid_until=clock(case.until),
    )
    helper._verify_action_binding(
        action, case.preliminary, case.dispatch, case.runtime, now=datetime.now(UTC)
    )
    for changes in (
        {"valid_until": clock(case.until + timedelta(seconds=1))},
        {"scope": {**action["scope"], "tenant": "UnitOnly-other"}},
        {"action": "revoke", "revoked_key_id": "UnitOnly-not-admitted"},
        {"owner_jdbc_config": {**action["owner_jdbc_config"], "sha256": "f" * 64}},
    ):
        with pytest.raises(helper.NativeInstallationError):
            helper._verify_action_binding(
                {**action, **changes}, case.preliminary, case.dispatch, case.runtime, now=datetime.now(UTC)
            )


@pytest.fixture
def unit_realm_call(admitted_unit_case: Any, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Exercise the real factory/checkers with inert loader I/O; never a PG/engine test."""
    case = admitted_unit_case
    dispatch, output = unit_action_readbacks(case)
    output_ref = artifact(case.tmp_path, "unit-installer-output.json", output)
    root = ProtectedDatabaseTlsRoot.model_validate(output["database_tls_root"], strict=False)
    resource = next(r for r in case.runtime["owned_resources"] if r["kind"] == "container")
    pg = OwnedTlsPostgres(
        PublishedPostgres(
            resource["id"],
            "UnitOnly-not-created",
            resource["owner"],
            resource["token"],
            "localhost",
            23456,
            "UnitOnly-not-an-image",
        ),
        root.path,
        _database_tls(root),
        "UnitOnly-not-a-credential",
        None,
    )
    state = SimpleNamespace(qualified=0, closed=0, loaded=0, on_qualify=None, parsed=[])
    real_parse = parse_model

    def observed_parse(model: Any, value: Any) -> Any:
        state.parsed.append(type(value))
        return real_parse(model, value)

    class UnitOnlyComposition:
        # Mirror parsing of the inert persisted config (its SecretStrs are UnitOnly masks).
        config = AuthLifecycleConfiguration.model_validate_json(
            helper._artifact_bytes(output["lifecycle_configuration"]), strict=True
        )
        native = SimpleNamespace(binding=case.native)

        async def qualify(self) -> None:
            state.qualified += 1
            if state.on_qualify is not None:
                state.on_qualify()

        async def close(self) -> None:
            state.closed += 1

    composition = UnitOnlyComposition()

    def unit_only_load(path: Path, **kwargs: Any) -> UnitOnlyComposition:
        # The effect boundary is inert; all artifact/signature/binding checks stay real.
        assert path == Path(output["lifecycle_configuration"]["path"])
        assert kwargs == dict(
            tenant=case.identity.tenant,
            identity_writer="UnitOnly-inert-writer",
            database_tls_root=root,
        )
        state.loaded += 1
        return composition

    monkeypatch.setattr(helper, "parse_model", observed_parse)
    monkeypatch.setattr(helper, "load_auth_lifecycle", unit_only_load)
    return SimpleNamespace(
        case=case,
        output=output,
        state=state,
        composition=composition,
        enter=lambda: helper.installed_provider_auth_native_realm(
            case.tmp_path,
            pg=pg,
            identity_composition=SimpleNamespace(binding=case.identity, writer="UnitOnly-inert-writer"),
            materials=case.materials,
            approved_qualification=case.prelim_ref,
            database_tls_root=root,
            owner="provider-unit",
            dispatch=dispatch,
            installation_result=output_ref,
        ),
    )


async def test_factory_decodes_pinned_canonical_native_wire_at_both_reads_unit_only(
    unit_realm_call: SimpleNamespace,
) -> None:
    call = unit_realm_call
    async with call.enter() as realm:
        assert realm.native_binding == call.case.native
        assert realm.scope == call.case.dispatch.expected_scope
        assert realm.config_digest == call.output["lifecycle_configuration"]["sha256"]
        assert call.state.parsed == [dict, dict]
        assert call.state.loaded == call.state.qualified == 1
        assert call.state.closed == 0
    assert call.state.closed == 1


@pytest.mark.parametrize(
    "admitted_unit_case", ["duplicate", "noncanonical", "numeric", "unknown"], indirect=True
)
async def test_factory_refuses_pinned_invalid_native_wire_unit_only(unit_realm_call: SimpleNamespace) -> None:
    call = unit_realm_call
    with pytest.raises((ProfileError, helper.NativeInstallationError)):
        async with call.enter():
            pytest.fail("Invalid closed native wire must not yield a realm")
    assert call.state.loaded == call.state.qualified == call.state.closed == 0


@pytest.mark.parametrize("stage", ["before_load", "after_qualify"])
async def test_factory_refuses_native_artifact_pin_drift_unit_only(
    unit_realm_call: SimpleNamespace,
    stage: str,
) -> None:
    call = unit_realm_call
    path = Path(call.output["native_database_binding"]["path"])

    def change_bytes() -> None:
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b"\n")
        path.chmod(0o400)

    if stage == "before_load":
        change_bytes()
    else:
        call.state.on_qualify = change_bytes
    with pytest.raises(helper.NativeInstallationError, match="independent pin"):
        async with call.enter():
            pytest.fail("Changed pinned bytes must not yield a realm")
    expected = int(stage == "after_qualify")
    assert call.state.loaded == call.state.qualified == call.state.closed == expected


async def test_factory_keeps_original_ceiling_after_await_unit_only(
    unit_realm_call: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call = unit_realm_call

    class UnitClock(datetime):
        advanced = False

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Self:
            value = call.case.until if cls.advanced else call.case.now
            return cls.fromtimestamp(value.timestamp(), tz=UTC)

    monkeypatch.setattr(helper, "datetime", UnitClock)
    call.state.on_qualify = lambda: setattr(UnitClock, "advanced", True)
    with pytest.raises(helper.NativeInstallationError, match="expired"):
        async with call.enter():
            pytest.fail("Original admission expiry crossed by await must refuse")
    assert call.state.loaded == call.state.qualified == call.state.closed == 1
