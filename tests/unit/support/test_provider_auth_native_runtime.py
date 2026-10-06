"""UnitOnly observation extracts/DAG mechanics; no runtime approval or native effects."""

from __future__ import annotations

import base64
import copy
import email.utils
import gzip
import hashlib
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from tests.support import provider_auth_native_runtime as runtime
from tests.support.provider_auth_native_installation import _read
from tests.unit.support.test_provider_auth_native_installation import (
    admitted_unit_case,
    artifact,
)

from maezo.gateway.human.read_profile import digest, wire
from maezo.portal.engine.profile import canonicalize

# Importing the fixture registers it for these UNIT tests; generated envelopes
# in that existing fixture are UnitOnly and are never used as independent gates.
assert admitted_unit_case is not None


def responses(now, *, xml, definition_id="UnitOnly-definition"):
    headers = {"content-type": "application/json", "date": email.utils.format_datetime(now, usegmt=True)}
    return [
        httpx.Response(200, headers=headers, json={"version": "2.1.0"}),
        httpx.Response(
            200,
            headers=headers,
            json={"id": definition_id, "key": "SP-OP-AUTH-001", "deploymentId": "UnitOnly-deployment"},
        ),
        httpx.Response(200, headers=headers, json={"id": definition_id, "bpmn20Xml": xml.decode()}),
    ]


def _observer_unit_pki(directory, now):
    """Genuine test PKI, no authority, listener, engine or network operation."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    directory.mkdir(mode=0o700)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UnitOnly-observer-CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UnitOnly-dedicated-observer")]))
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.UniformResourceIdentifier("spiffe://UnitOnly/provider-native-observer")]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    refs = []
    for name, data in (
        ("observer.pem", cert.public_bytes(serialization.Encoding.PEM)),
        (
            "observer-key.pem",
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ),
        ),
        ("observer-ca.pem", ca.public_bytes(serialization.Encoding.PEM)),
    ):
        path = directory / name
        path.write_bytes(data)
        path.chmod(0o600)
        refs.append(runtime.NativeObservationFile(path, hashlib.sha256(data).hexdigest()))
    cert_hash = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()
    spki_hash = hashlib.sha256(
        cert.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).hexdigest()
    return (
        runtime.NativeObservationTransport("https://127.0.0.1:8443", *refs, "a" * 64, ("b" * 64, "c" * 64)),
        cert_hash,
        spki_hash,
    )


@pytest.fixture
def observation_v3_unit_case(tmp_path):
    """Legitimate v4 SHAPE extracts; never an independent runtime gate."""
    import base64
    import io
    import json
    import zipfile

    wall = datetime.now(UTC)
    now = wall.replace(microsecond=wall.microsecond // 1000 * 1000)
    until = now + timedelta(minutes=5)
    transport, cert_hash, spki_hash = _observer_unit_pki(tmp_path / "observer-private", now)
    private = tmp_path / "admission-private"
    private.mkdir(mode=0o700)

    def pin(name, raw, *, secret=False):
        path = (private if secret else tmp_path) / ("observer-unit-" + name)
        path.write_bytes(raw)
        path.chmod(0o600 if secret else 0o644)
        return runtime.NativeObservationFile(path, hashlib.sha256(raw).hexdigest())

    def encode(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

    xml = (
        b'<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">'
        b'<process id="SP-OP-AUTH-001"/></definitions>'
    )
    profile = canonicalize({"UnitOnly": "mounted-profile-not-engine-extraction"})
    stable = dict(
        database_name="UnitOnly-db",
        database_oid="100",
        system_identifier="123456789",
        engine_schema_name="cibseven",
        engine_schema_oid="101",
        native_schema_name="maezo_native",
        native_schema_oid="102",
        current_user="cibseven_app",
        session_user="cibseven_app",
        catalogue_projection_sha256="d" * 64,
        witness_projection="provider-native-physical-db-join.v2",
    )
    identity = dict(
        tenant="UnitOnly-tenant",
        environment="TestOnly-UnitOnly",
        workload="provider-native-observer",
        workload_version="UnitOnly-v1",
        issuer="UnitOnly-issuer",
        subject="UnitOnly-subject",
        origin="verified_mtls",
    )
    pins = {
        name: hashlib.sha256(name.encode()).hexdigest()
        for name in (
            "descriptor_sha256",
            "native_jar_sha256",
            "support_jar_sha256",
            "observation_schema_sha256",
            "startup_descriptor_inventory_sha256",
            "startup_vendor_inventory_sha256",
            "rest_spi_jar_sha256",
        )
    }
    pins["observation_schema_sha256"] = hashlib.sha256(_UNIT_ONLY_OBSERVATION_SCHEMA).hexdigest()
    # Actual inert UNIT-only JAR bytes prove the class-directory absence shape.
    # Never installed or executed; not bytecode, a receipt or a physical fact.
    support_directory = tmp_path / "unit-only-installed-material"
    support_directory.mkdir(mode=0o700)
    support_path = support_directory / "provider-auth-test-support.jar"
    support_bytes = io.BytesIO()
    with zipfile.ZipFile(support_bytes, "w") as jar:
        jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n\n")
        jar.writestr("br/com/maezo/human/ProviderAuthTestOwner.class", b"UnitOnly-inert-not-bytecode")
        jar.writestr("br/com/maezo/human/ProviderAuthTestComposition.class", b"UnitOnly-inert-not-bytecode")
    support_path.write_bytes(support_bytes.getvalue())
    support_path.chmod(0o644)
    pins["support_jar_sha256"] = hashlib.sha256(support_bytes.getvalue()).hexdigest()
    support_pin = runtime.NativeObservationFile(support_path, pins["support_jar_sha256"])
    admission = dict(
        schema="provider-native-observation-admission.v4",
        identity=identity,
        peer_certificate_sha256=cert_hash,
        peer_spki_sha256=spki_hash,
        engine_name="UnitOnly-engine",
        definition_id="UnitOnly-definition",
        deployment_id="UnitOnly-deployment",
        process_key="SP-OP-AUTH-001",
        process_version=1,
        xml_sha256=hashlib.sha256(xml).hexdigest(),
        profile_sha256=hashlib.sha256(profile).hexdigest(),
        candidate_sha="e" * 40,
        expected_image_id="sha256:" + "f" * 64,
        startup_variant="phase-a",
        nonce="1" * 32,
        issued_at=runtime.clock(now - timedelta(seconds=1)),
        original_deadline=runtime.clock(until),
        expected_database=dict(stable_projection=stable, root_pg_preboot_witness_sha256="2" * 64),
        **pins,
    )
    inputs = runtime.NativeObservationInputs(
        pin("admission.json", encode(admission), secret=True),
        pin("schema.json", _UNIT_ONLY_OBSERVATION_SCHEMA),
        pin("source.xml", xml),
        pin("profile.json", profile),
        "3" * 64,
        "4" * 64,
        installed_support_jar=support_pin,
    )
    request = runtime.native_observation_request(inputs, deadline=until)
    provenance = [
        dict(
            class_name=name,
            jar_relative_path="lib/UnitOnly-engine.jar"
            if "ProcessEngineImpl" in name
            else "lib/UnitOnly-native.jar",
            jar_sha256="5" * 64 if "ProcessEngineImpl" in name else pins["native_jar_sha256"],
            class_sha256=hashlib.sha256(name.encode()).hexdigest(),
            loader_group="engine-common",
        )
        for name in sorted(
            (
                "br.com.maezo.workload.WorkloadPlugin",
                "org.cibseven.bpm.engine.impl.ProcessEngineImpl",
                "br.com.maezo.workload.RuntimeDefinitionObservation",
                "br.com.maezo.human.AuthValues",
            )
        )
    ]
    jvm = dict(
        boot_uuid="11111111-2222-3333-4444-555555555555",
        pid_namespace=1,
        start_time_epoch_ms=1000,
        engine_registry_size=1,
        engine_registry_name="UnitOnly-engine",
    )
    installation = dict(
        engine_name="UnitOnly-engine",
        authorization_enabled=True,
        tenant_check_enabled=True,
        schema_update="false",
        jvm_process=jvm,
        class_provenance=provenance,
        mounted_pins=pins,
    )
    expectations = dict(
        candidate_sha=admission["candidate_sha"],
        image_id=admission["expected_image_id"],
        validation_origin="root-crosslink-required",
    )
    binding = {
        name: admission[name]
        for name in (
            "engine_name",
            "definition_id",
            "deployment_id",
            "process_key",
            "process_version",
            "xml_sha256",
            "profile_sha256",
            "original_deadline",
        )
    }
    binding.update(
        policy_digest=inputs.policy_digest,
        observation_admission_sha256=inputs.admission.sha256,
        capability_digest=inputs.capability_digest,
        tenant=identity["tenant"],
        environment=identity["environment"],
        candidate_expectations=expectations,
    )

    def stage(sequence):
        # Different genuine-shape new backend/TX values are intentional positives.
        tx = now + timedelta(microseconds=sequence * 100)
        session = dict(
            backend_pid=str(50 + sequence),
            backend_start=runtime.clock(now - timedelta(seconds=2)),
            transaction_started_at=runtime.clock(tx),
            transaction_started_at_source="postgresql-transaction_timestamp-on-enlisted-connection",
            transaction_id_if_assigned=None,
            transaction_id_source="pg_current_xact_id_if_assigned",
            connection_source="actual-commandcontext-dbsqlsession-connection",
            context_connection_identity="enlisted-object-identity-checked",
            auto_commit=False,
            transaction_isolation="READ_COMMITTED",
            backend_ssl=True,
            backend_tls_version="TLSv1.2" if sequence else "TLSv1.3",
            observed_at=runtime.clock(tx),
            observed_at_source="postgresql-clock_timestamp-on-enlisted-connection",
        )
        first = dict(
            stable_database_projection=stable,
            binding_projection=binding,
            runtime_installation_projection=installation,
            session_transaction=session,
        )
        last = copy.deepcopy(first)
        last["session_transaction"].update(
            transaction_id_if_assigned=str(101 + sequence),
            observed_at=runtime.clock(tx + timedelta(microseconds=10)),
        )
        return dict(
            stage="CAPTURE" if not sequence else "EMISSION_RECHECK",
            stage_sequence=sequence,
            server_stage_execution_uuid=f"00000000-0000-0000-0000-{sequence + 1:012d}",
            entry=copy.deepcopy(first),
            committing=last,
            native_commit_confirmed=True,
            jvm_after_commit_at=runtime.clock(now + timedelta(milliseconds=1 + sequence)),
        )

    capture, recheck = stage(0), stage(1)
    result = dict(
        schema="provider-native-runtime-snapshot.v4",
        request_sha256=hashlib.sha256(request).hexdigest(),
        observation_admission_sha256=inputs.admission.sha256,
        nonce=admission["nonce"],
        original_deadline=admission["original_deadline"],
        policy_digest=inputs.policy_digest,
        capability_digest=inputs.capability_digest,
        identity=identity,
        peer_certificate_sha256=cert_hash,
        peer_spki_sha256=spki_hash,
        **installation,
        candidate_expectations=expectations,
        cibseven_version="2.1.0",
        definition=dict(
            definition_id=admission["definition_id"],
            deployment_id=admission["deployment_id"],
            process_key="SP-OP-AUTH-001",
            process_version=1,
            tenant=identity["tenant"],
            resource_name="UnitOnly-auth.bpmn",
            xml_sha256=admission["xml_sha256"],
            xml_base64=base64.b64encode(xml).decode(),
            xml_size=len(xml),
            suspended=False,
        ),
        profile=dict(
            input_profile="portal-auth-intake.v1",
            origin="admitted-profile-file",
            sha256=admission["profile_sha256"],
            base64=base64.b64encode(profile).decode(),
            size=len(profile),
        ),
        clock=dict(
            source="jvm-system-currentTimeMillis",
            sample_started_at=runtime.clock(now),
            sample_finished_at=runtime.clock(now + timedelta(milliseconds=1)),
            engine_clock=runtime.clock(now),
            monotonic_elapsed_ns="10000",
            resolution_nominal_ms=1,
            accuracy_status="unqualified_wall_clock",
            absolute_utc_uncertainty_ms=None,
            uncertainty_status="not_independently_calibrated",
        ),
        cutoff="command-transaction-COMMITTING-as-of",
        authority_semantics="technical-observation-only-no-effect-authorization",
        database_witness=capture,
        emission_recheck=dict(
            boundary="new-workloadcommand-read-context-after-capture-commit",
            validation_protocol="finite-private-emission-stage.v1",
            observed_at=recheck["jvm_after_commit_at"],
            observed_at_source="jvm-system-currentTimeMillis-after-new-read-commit",
            original_cutoff_preserved=True,
            authority_semantics="current-read-rechecked-not-future-lease",
            read_stages=[recheck],
        ),
        cutoff_observed_at=runtime.clock(now + timedelta(milliseconds=1)),
        capture_transaction_commit_confirmed=True,
    )
    after = now + timedelta(milliseconds=3)
    response = dict(
        protocol="maezo.engine-result.v1", capability_digest=inputs.capability_digest, result=result
    )
    socket = dict(
        schema="provider-native-observer-http-tls.v1",
        origin=transport.origin,
        server_certificate_sha256="6" * 64,
        server_peer_spki_sha256=transport.expected_server_spki_sha256,
        tls_version="TLSv1.3",
        cipher=["UnitOnly-cipher", "TLSv1.3", "256"],
        before=runtime.clock(now),
        after=runtime.clock(after),
    )
    return SimpleNamespace(
        now=now,
        after=after,
        until=until,
        admission=admission,
        inputs=inputs,
        transport=transport,
        request=request,
        response=response,
        socket=socket,
        encode=encode,
        pin=pin,
    )


def _parse_observer_unit(case):
    return runtime.parse_native_engine_observation(
        raw_response=case.encode(case.response),
        raw_request=case.request,
        inputs=case.inputs,
        transport=case.transport,
        socket_evidence=case.socket,
        before=case.now,
        after=case.after,
        deadline=case.until,
    )


def test_observer_legitimate_unit_shape_accepts_new_backend_and_new_transaction(observation_v3_unit_case):
    case = observation_v3_unit_case
    engine = _parse_observer_unit(case)
    assert engine.definition.definition_id == "UnitOnly-definition"
    assert engine.source_xml == engine.retrieved_xml
    assert engine.route_probe["root_crosslink_status"] == "PENDING_ROOT_READBACK"
    assert (
        engine.route_probe["snapshot"]["database_witness"]["committing"]["session_transaction"][
            "transaction_id_if_assigned"
        ]
        == "101"
    )
    assert (
        engine.route_probe["snapshot"]["emission_recheck"]["read_stages"][0]["committing"][
            "session_transaction"
        ]["transaction_id_if_assigned"]
        == "102"
    )
    assert canonicalize(engine.route_probe)


def test_observer_context_serves_only_genuine_unit_ca_and_dedicated_client(observation_v3_unit_case):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    case = observation_v3_unit_case
    context = case.transport.context(case.until)
    expected = x509.load_pem_x509_certificate(case.transport.ca_certificate.path.read_bytes()).public_bytes(
        serialization.Encoding.DER
    )
    assert context.check_hostname
    assert context.get_ca_certs(binary_form=True) == [expected]


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema",), "provider-native-runtime-snapshot.v2"),
        (("request_sha256",), "0" * 64),
        (("observation_admission_sha256",), "0" * 64),
        (("nonce",), "0" * 32),
        (("policy_digest",), "0" * 64),
        (("peer_certificate_sha256",), "0" * 64),
        (("peer_spki_sha256",), "0" * 64),
        (("identity", "tenant"), "UnitOnly-foreign"),
        (("definition", "process_version"), True),
        (("definition", "deployment_id"), "UnitOnly-other-deployment"),
        (("definition", "xml_size"), 1),
        (("profile", "origin"), "engine-extracted"),
        (("profile", "sha256"), "0" * 64),
        (("clock", "absolute_utc_uncertainty_ms"), 0),
        (("jvm_process", "engine_registry_size"), True),
        (("database_witness", "stage_sequence"), True),
        (("database_witness", "committing", "session_transaction", "backend_pid"), "999"),
        (("database_witness", "committing", "session_transaction", "backend_ssl"), False),
        (("database_witness", "committing", "stable_database_projection", "engine_schema_oid"), "999"),
        (("database_witness", "committing", "binding_projection", "tenant"), "UnitOnly-foreign"),
        (
            (
                "database_witness",
                "committing",
                "runtime_installation_projection",
                "mounted_pins",
                "rest_spi_jar_sha256",
            ),
            "0" * 64,
        ),
        (("emission_recheck", "observed_at"), "2000-01-01T00:00:00.000000Z"),
    ],
)
def test_observer_v3_crossfield_and_closed_type_refusals(observation_v3_unit_case, path, value):
    case = observation_v3_unit_case
    target = case.response["result"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(runtime.NativeObservationError):
        _parse_observer_unit(case)


@pytest.mark.parametrize(
    "mutation",
    [
        "copied-xid",
        "reordered",
        "copied-uuid",
        "too-many",
        "assigned-to-null",
        "changed-tls",
        "backward-pg",
        "backward-jvm",
        "unknown-field",
        "duplicate-class",
    ],
)
def test_observer_stage_lifecycle_refuses_partial_or_copied_evidence(observation_v3_unit_case, mutation):
    case = observation_v3_unit_case
    r = case.response["result"]
    capture = r["database_witness"]
    recheck = r["emission_recheck"]["read_stages"][0]
    if mutation == "copied-xid":
        recheck["committing"]["session_transaction"]["transaction_id_if_assigned"] = "101"
    elif mutation == "reordered":
        recheck["stage_sequence"] = 2
    elif mutation == "copied-uuid":
        recheck["server_stage_execution_uuid"] = capture["server_stage_execution_uuid"]
    elif mutation == "too-many":
        r["emission_recheck"]["read_stages"] *= 9
    elif mutation == "assigned-to-null":
        capture["entry"]["session_transaction"]["transaction_id_if_assigned"] = "100"
        capture["committing"]["session_transaction"]["transaction_id_if_assigned"] = None
    elif mutation == "changed-tls":
        recheck["committing"]["session_transaction"]["backend_tls_version"] = "TLSv1.3"
    elif mutation == "backward-pg":
        recheck["entry"]["session_transaction"]["observed_at"] = runtime.clock(
            case.now - timedelta(seconds=1)
        )
    elif mutation == "backward-jvm":
        recheck["jvm_after_commit_at"] = runtime.clock(case.now - timedelta(seconds=1))
    elif mutation == "unknown-field":
        recheck["committing"]["copied_authority_receipt"] = "UnitOnly-no-authority"
    else:
        r["class_provenance"][1] = copy.deepcopy(r["class_provenance"][0])
    with pytest.raises(runtime.NativeObservationError):
        _parse_observer_unit(case)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}',
        b'{"x":1.0}',
        b'{"x":NaN}',
        b'{"x":"\\ud800"}',
        b'{"x":9223372036854775808}',
        b"\xff",
        b"[" * 18 + b"0" + b"]" * 18,
    ],
)
def test_observer_json_refuses_ambiguous_or_overflow_payload(raw):
    with pytest.raises(runtime.NativeObservationError):
        runtime._observation_json(raw)


def test_observer_primitive_serializer_refuses_foreign_container_without_executing_hooks():
    invoked = []

    class UnitOnlyForeignDict(dict):
        def items(self):
            invoked.append("foreign-items")
            raise AssertionError("foreign hook must not run")

    with pytest.raises(runtime.NativeObservationError):
        runtime._observation_bytes({"socket": UnitOnlyForeignDict()})
    assert invoked == []


def test_observer_schema_fixture_is_exact_independent_contract_bytes():
    assert (
        hashlib.sha256(_UNIT_ONLY_OBSERVATION_SCHEMA).hexdigest()
        == "d0affa2deb9c75e8aa2a52b52abc70e8052e81a9c1d98540af889ec32d27e2a1"
    )


def test_observer_never_reuses_auth_spki(observation_v3_unit_case):
    case = observation_v3_unit_case
    transport = replace(case.transport, forbidden_auth_spki_sha256=(case.admission["peer_spki_sha256"],))
    with pytest.raises(runtime.NativeObservationError, match="PKI"):
        transport.guard(case.until)


@pytest.mark.parametrize("mutation", ["mode", "symlink", "hardlink", "byte-drift", "unprotected-parent"])
def test_observer_private_material_custody_refusals(observation_v3_unit_case, mutation):
    case = observation_v3_unit_case
    ref = case.transport.client_key
    if mutation == "mode":
        ref.path.chmod(0o644)
    elif mutation == "symlink":
        renamed = ref.path.with_suffix(".original")
        ref.path.rename(renamed)
        ref.path.symlink_to(renamed)
    elif mutation == "hardlink":
        os.link(ref.path, ref.path.with_suffix(".duplicate"))
    elif mutation == "byte-drift":
        ref.path.write_bytes(ref.path.read_bytes() + b"\n")
    else:
        ref.path.parent.chmod(0o755)
    with pytest.raises(runtime.NativeObservationError):
        case.transport.guard(case.until)


def _root_readback_unit(case, engine):
    """Inert data fixture, never real ROOT readback provenance or gate approval."""
    result = case.response["result"]
    stages = [result["database_witness"], *result["emission_recheck"]["read_stages"]]
    session_keys = (
        "backend_pid",
        "backend_start",
        "transaction_started_at",
        "transaction_id_if_assigned",
        "backend_ssl",
        "backend_tls_version",
    )
    correlations = []
    for stage in stages:
        for endpoint in ("entry", "committing"):
            observed = stage[endpoint]["session_transaction"]
            correlations.append(
                dict(
                    stage_sequence=stage["stage_sequence"],
                    server_stage_execution_uuid=stage["server_stage_execution_uuid"],
                    sample=endpoint,
                    source="root-pg-stat-activity-and-ssl-readback",
                    root_observed_at=observed["observed_at"],
                    session={key: observed[key] for key in session_keys},
                    full_xid_mapping=dict(
                        source="root-pg-current-snapshot-and-stat-activity",
                        backend_xid32=None
                        if observed["transaction_id_if_assigned"] is None
                        else str(int(observed["transaction_id_if_assigned"]) % 2**32),
                        active_full_xids=[]
                        if observed["transaction_id_if_assigned"] is None
                        else [observed["transaction_id_if_assigned"]],
                    ),
                )
            )
    installation = copy.deepcopy(result["database_witness"]["entry"]["runtime_installation_projection"])
    return dict(
        schema="provider-native-root-observation-crosslink.v1",
        source="root-owned-physical-resource-and-jar-readback",
        before=runtime.clock(case.now),
        after=runtime.clock(case.after),
        origin=engine.origin,
        request_sha256=engine.route_probe["request_sha256"],
        response_sha256=engine.route_probe["response_sha256"],
        root_pg_preboot_witness_sha256=case.admission["expected_database"]["root_pg_preboot_witness_sha256"],
        candidate_sha=case.admission["candidate_sha"],
        image_id=case.admission["expected_image_id"],
        source_xml_sha256=case.admission["xml_sha256"],
        profile_sha256=case.admission["profile_sha256"],
        server_peer_spki_sha256=case.transport.expected_server_spki_sha256,
        server_certificate_sha256=engine.route_probe["transport"]["server_certificate_sha256"],
        stable_database_projection=copy.deepcopy(case.admission["expected_database"]["stable_projection"]),
        runtime_installation_projection=installation,
        stage_session_correlations=correlations,
    )


def test_observer_corroboration_requires_each_stage_independent_artifact_shape(observation_v3_unit_case):
    case = observation_v3_unit_case
    engine = _parse_observer_unit(case)
    root = _root_readback_unit(case, engine)
    pin = case.pin("root-crosslink.json", case.encode(root), secret=True)
    observed = runtime.corroborate_native_engine_observation(
        engine=engine,
        inputs=case.inputs,
        root_crosslink=runtime.NativeObservationRootCrosslink(pin),
        deadline=case.until,
    )
    assert observed.route_probe["root_crosslink_status"] == "ROOT_READBACK_VALUES_MATCHED"
    assert (
        observed.route_probe["snapshot"]["authority_semantics"]
        == "technical-observation-only-no-effect-authorization"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "wrong-xid",
        "wrong-ssl",
        "wrong-image",
        "wrong-boot",
        "wrong-catalogue",
        "copied-endpoint",
        "unknown-field",
    ],
)
def test_observer_root_corroboration_cannot_skip_or_substitute_witness(observation_v3_unit_case, mutation):
    case = observation_v3_unit_case
    engine = _parse_observer_unit(case)
    root = _root_readback_unit(case, engine)
    if mutation == "missing":
        root["stage_session_correlations"].pop()
    elif mutation == "wrong-xid":
        root["stage_session_correlations"][-1]["session"]["transaction_id_if_assigned"] = "999"
    elif mutation == "wrong-ssl":
        root["stage_session_correlations"][-1]["session"]["backend_ssl"] = False
    elif mutation == "wrong-image":
        root["image_id"] = "sha256:" + "0" * 64
    elif mutation == "wrong-boot":
        root["runtime_installation_projection"]["jvm_process"]["boot_uuid"] = (
            "00000000-0000-0000-0000-000000000000"
        )
    elif mutation == "wrong-catalogue":
        root["stable_database_projection"]["catalogue_projection_sha256"] = "0" * 64
    elif mutation == "copied-endpoint":
        root["stage_session_correlations"][-1] = copy.deepcopy(root["stage_session_correlations"][0])
    else:
        root["invented_authority"] = "UnitOnly-no-authority"
    pin = case.pin("root-crosslink.json", case.encode(root), secret=True)
    with pytest.raises(runtime.NativeObservationError):
        runtime.corroborate_native_engine_observation(
            engine=engine,
            inputs=case.inputs,
            root_crosslink=runtime.NativeObservationRootCrosslink(pin),
            deadline=case.until,
        )


def test_observer_pretty_wire_hash_is_raw_not_reserialized(observation_v3_unit_case):
    import json

    case = observation_v3_unit_case
    case.request = json.dumps(json.loads(case.request), indent=2).encode()
    case.response["result"]["request_sha256"] = hashlib.sha256(case.request).hexdigest()
    engine = _parse_observer_unit(case)
    assert engine.route_probe["request_sha256"] == hashlib.sha256(case.request).hexdigest()


def test_observer_root_join_refuses_mutation_after_capture(observation_v3_unit_case):
    case = observation_v3_unit_case
    engine = _parse_observer_unit(case)
    root = _root_readback_unit(case, engine)
    pin = case.pin("root-crosslink.json", case.encode(root), secret=True)
    engine.route_probe["snapshot"]["identity"]["tenant"] = "UnitOnly-mutated"
    with pytest.raises(runtime.NativeObservationError, match="mutated after capture"):
        runtime.corroborate_native_engine_observation(
            engine=engine,
            inputs=case.inputs,
            root_crosslink=runtime.NativeObservationRootCrosslink(pin),
            deadline=case.until,
        )


@pytest.mark.parametrize(
    "raw",
    [
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Encoding: gzip",
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2",
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        b"Content-Length: 2\r\nTransfer-Encoding: chunked",
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n Location: /other",
        b"HTTP/1.1 302 Found\r\nContent-Type: application/json",
        b"HTTP/1.0 200 OK\r\nContent-Type: application/json",
    ],
)
def test_observer_http_closed_framing_refusals(raw):
    with pytest.raises(runtime.NativeObservationError):
        runtime._observation_http_headers(raw)


@pytest.mark.asyncio
@pytest.mark.parametrize("expire_at", [None, "connect", "drain", "read", "close", "wrong-spki"])
async def test_observer_transport_unit_waits_preserve_original_ceiling_and_cleanup(
    observation_v3_unit_case,
    monkeypatch,
    expire_at,
):
    """Inert socket mechanics only: never real TLS, engine or integration proof."""
    import asyncio

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    case = observation_v3_unit_case
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UnitOnly-inert-socket-peer")])
    peer = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(case.now - timedelta(minutes=1))
        .not_valid_after(case.now + timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    spki = hashlib.sha256(
        key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).hexdigest()
    transport = replace(
        case.transport, expected_server_spki_sha256="f" * 64 if expire_at == "wrong-spki" else spki
    )
    state = SimpleNamespace(now=case.now, closed=False, close_completed=False, sent=b"", reads=0)

    def controlled_current(deadline):
        if state.now >= deadline:
            raise runtime.NativeObservationError("Original observation ceiling expired")
        return state.now

    async def wait_point(name):
        await asyncio.sleep(0)
        state.now = case.until if expire_at == name else case.after

    class UnitOnlyTlsSession:
        def getpeercert(self, *, binary_form):
            assert binary_form
            return peer.public_bytes(serialization.Encoding.DER)

        def version(self):
            return "TLSv1.3"

        def cipher(self):
            return ("UnitOnly-cipher", "TLSv1.3", 256)

    class UnitOnlyWriter:
        def get_extra_info(self, name):
            if name == "peername":
                return ("127.0.0.1", 8443)
            assert name == "ssl_object"
            return UnitOnlyTlsSession()

        def write(self, data):
            state.sent += data

        async def drain(self):
            await wait_point("drain")

        def close(self):
            state.closed = True

        async def wait_closed(self):
            await wait_point("close")
            state.close_completed = True

    class UnitOnlyReader:
        async def read(self, bound):
            await wait_point("read")
            state.reads += 1
            if state.reads > 1:
                return b""
            body = case.encode(case.response)
            return (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            )

    async def connect(host, port, *, ssl, server_hostname):
        assert host == server_hostname == "127.0.0.1" and port == 8443
        assert ssl.check_hostname
        await wait_point("connect")
        return UnitOnlyReader(), UnitOnlyWriter()

    monkeypatch.setattr(runtime, "current", controlled_current)
    monkeypatch.setattr(runtime.asyncio, "open_connection", connect)
    if expire_at is None:
        result = await runtime.observe_native_engine_operation(
            inputs=case.inputs,
            transport=transport,
            deadline=case.until,
        )
        assert result.route_probe["root_crosslink_status"] == "PENDING_ROOT_READBACK"
        assert state.sent.startswith(b"POST /engine-rest/maezo/v1/operations HTTP/1.1\r\n")
        assert state.sent.endswith(case.request)
    else:
        with pytest.raises(runtime.NativeObservationError):
            await runtime.observe_native_engine_operation(
                inputs=case.inputs,
                transport=transport,
                deadline=case.until,
            )
        if expire_at in ("connect", "wrong-spki"):
            assert state.sent == b""
    assert state.closed and state.close_completed


@pytest.mark.parametrize("xid_mode", ["new-assigned", "unassigned"])
def test_observer_reused_backend_is_valid_for_genuine_new_transaction_shape(
    observation_v3_unit_case, xid_mode
):
    case = observation_v3_unit_case
    result = case.response["result"]
    capture = result["database_witness"]
    recheck = result["emission_recheck"]["read_stages"][0]
    for endpoint in ("entry", "committing"):
        recheck[endpoint]["session_transaction"]["backend_pid"] = capture[endpoint]["session_transaction"][
            "backend_pid"
        ]
    if xid_mode == "unassigned":
        capture["committing"]["session_transaction"]["transaction_id_if_assigned"] = None
        recheck["committing"]["session_transaction"]["transaction_id_if_assigned"] = None
    assert _parse_observer_unit(case).route_probe["root_crosslink_status"] == "PENDING_ROOT_READBACK"


@pytest.mark.parametrize("mapping_mode", ["legitimate-full", "only-xid32", "ambiguous-epoch"])
def test_observer_root_full_xid_requires_actual_snapshot_epoch_mapping(
    observation_v3_unit_case, mapping_mode
):
    case = observation_v3_unit_case
    full = str(2**32 + 102)
    case.response["result"]["emission_recheck"]["read_stages"][0]["committing"]["session_transaction"][
        "transaction_id_if_assigned"
    ] = full
    engine = _parse_observer_unit(case)
    root = _root_readback_unit(case, engine)
    mapping = root["stage_session_correlations"][-1]["full_xid_mapping"]
    assert mapping["backend_xid32"] == "102"
    if mapping_mode == "only-xid32":
        mapping["active_full_xids"] = ["102"]
    elif mapping_mode == "ambiguous-epoch":
        mapping["active_full_xids"] = ["102", full]
    pin = case.pin("root-crosslink.json", case.encode(root), secret=True)
    crosslink = runtime.NativeObservationRootCrosslink(pin)
    if mapping_mode == "legitimate-full":
        assert crosslink.validate(engine, inputs=case.inputs, deadline=case.until)
    else:
        with pytest.raises(runtime.NativeObservationError, match="full-XID mapping"):
            crosslink.validate(engine, inputs=case.inputs, deadline=case.until)


@pytest.mark.parametrize(
    "clock_path,value",
    [
        (("clock", "engine_clock"), "old-skew"),
        (("clock", "monotonic_elapsed_ns"), "6000000000"),
        (("clock", "sample_finished_at"), "later-than-commit"),
        (("clock", "engine_clock"), "false-micro-resolution"),
    ],
)
def test_observer_jvm_bracket_and_duration_are_not_pg_five_second_skew(
    observation_v3_unit_case, clock_path, value
):
    case = observation_v3_unit_case
    if value == "old-skew":
        value = runtime.clock(case.now - timedelta(seconds=1))
    elif value == "later-than-commit":
        value = runtime.clock(case.now + timedelta(milliseconds=3))
    elif value == "false-micro-resolution":
        value = runtime.clock(case.now + timedelta(microseconds=1))
    case.response["result"][clock_path[0]][clock_path[1]] = value
    with pytest.raises(runtime.NativeObservationError):
        _parse_observer_unit(case)


@pytest.fixture
def unit_observations(admitted_unit_case):
    """Extracts only: no sockets, SQL server, Docker resource or real receipt."""
    case = admitted_unit_case
    now = datetime.now(UTC)
    until = case.until
    source = (
        b'<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">'
        b'<process id="SP-OP-AUTH-001" isExecutable="true"/></definitions>'
    )
    profile = canonicalize({"UnitOnly": "profile-extract-not-runtime-qualification"})
    observed_responses = responses(now, xml=source)
    engine = runtime.parse_engine_observation(
        version_response=observed_responses[0],
        definition_response=observed_responses[1],
        xml_response=observed_responses[2],
        definition_id="UnitOnly-definition",
        source_xml=source,
        profile=profile,
        before=now,
        after=now,
        deadline=until,
        origin=case.runtime["origin"],
    )
    database = runtime.NativeDatabaseObservation(
        binding=copy.deepcopy(case.measurements["binding"]),
        role_observations=tuple(case.measurements["role_observations"]),
        catalog=(),
        database_clock=now,
        before=now,
        after=now,
        server_version="16.6-UnitOnly",
        system_identifier="123456789",
        backend_pid=7,
        role_facts=(),
    )
    tls = runtime.NativeTlsObservation(
        case.runtime["origin"],
        case.runtime["java_settings"]["signing_spki_sha256"],
        hashlib.sha256(case.materials.ca_certificate.read_bytes()).hexdigest(),
        dict(schema="UnitOnly-no-handshake", before=runtime.clock(now), after=runtime.clock(now)),
    )
    measured = copy.deepcopy(case.measurements)
    measured.update(
        source_xml=artifact(case.tmp_path, "measured-source.xml", source, media="application/xml"),
        retrieved_xml=artifact(case.tmp_path, "measured-retrieved.xml", source, media="application/xml"),
        profile_artifact=artifact(case.tmp_path, "measured-profile.json", profile),
        definition=wire(engine.definition),
        valid_until=runtime.clock(until),
    )
    boot = _read(measured["boot_switch"])
    boot.update(definition=wire(engine.definition), valid_until=runtime.clock(until))
    owner = copy.deepcopy(case.runtime)
    owner.update(valid_until=runtime.clock(until), issued_at=runtime.clock(now))
    output = case.tmp_path / "preimages"
    output.mkdir(mode=0o700)
    execution = runtime.write_observation_evidence(
        output / "actual-observation-evidence.json",
        database=database,
        engine=engine,
        tls=tls,
        deadline=until,
    )
    measured["execution_record"] = execution
    descriptors = []
    for phase in ("a", "b"):
        composition = (
            "<plugin><class>br.com.maezo.human.ProviderAuthTestComposition</class></plugin>"
            if phase == "b"
            else ""
        )
        descriptor = (
            f'<bpm-platform><process-engine name="{case.dispatch.expected_scope.engine_name}">'
            '<properties><property name="authorizationEnabled">true</property>'
            '<property name="tenantCheckEnabled">true</property>'
            '<property name="databaseSchemaUpdate">false</property></properties>'
            f"<plugins>{composition}<plugin><class>br.com.maezo.human.HumanCommandPlugin</class>"
            "</plugin></plugins></process-engine></bpm-platform>"
        ).encode()
        ref = artifact(case.tmp_path, "descriptor-" + phase + ".xml", descriptor, media="application/xml")
        descriptors.append(ref)
        boot["phase_" + phase + "_descriptor_sha256"] = ref["sha256"]
    measured["descriptor_sha256"] = descriptors[0]["sha256"]
    pom = artifact(
        case.tmp_path,
        "measured-pom.xml",
        b"<project><build><plugins><plugin><artifactId>maven-compiler-plugin</artifactId>"
        b"<version>3.16.0</version></plugin></plugins></build></project>",
        media="application/xml",
    )
    measured["pom"]["git_blob_sha256"] = pom["sha256"]
    return SimpleNamespace(
        directory=output,
        measurements=measured,
        boot_switch=boot,
        runtime_manifest=owner,
        ordered_classpath=tuple(case.classpath["ordered_artifacts"]),
        database=database,
        engine=engine,
        tls=tls,
        phase_a_descriptor=descriptors[0],
        phase_b_descriptor=descriptors[1],
        pom_path=Path(pom["path"]),
        deadline=until,
        identity_binding=case.identity,
        protected_binding=case.config.protected,
    )


def assemble(case):
    return runtime.assemble_phase_a_preimages(
        case.directory,
        **{
            name: getattr(case, name)
            for name in (
                "measurements",
                "boot_switch",
                "runtime_manifest",
                "ordered_classpath",
                "database",
                "engine",
                "tls",
                "phase_a_descriptor",
                "phase_b_descriptor",
                "pom_path",
                "identity_binding",
                "protected_binding",
                "deadline",
            )
        },
    )


def test_positive_complete_preimages_are_acyclic_and_create_new(unit_observations):
    case = unit_observations
    dag = assemble(case)
    assert dag.order.index("boot-switch.json") < dag.order.index("measurements.json")
    assert len(dag.order) == 4
    assert all(not Path(ref["path"]).exists() for ref in dag.artifacts.values())
    assert dag.records["measurements.json"]["clock_engine"].endswith(".000000Z")
    assert case.engine.route_probe["engine_clock_resolution_seconds"] == "1"
    assert dag.records["measurements.json"]["database_catalog_sha256"] == digest([])
    assert "qualification" not in dag.records
    runtime.write_preimages(case.directory, dag, deadline=case.deadline)
    for name, ref in dag.artifacts.items():
        assert _read(ref) == dag.records[name]
        assert os.stat(ref["path"]).st_mode & 0o777 == 0o400
    with pytest.raises(FileExistsError):
        runtime.write_preimages(case.directory, dag, deadline=case.deadline)


def test_protected_observation_without_root_witnesses_cannot_construct_measurements(
    unit_observations,
    observation_v3_unit_case,
):
    case = unit_observations
    case.engine = _parse_observer_unit(observation_v3_unit_case)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in case.directory.iterdir()}
    with pytest.raises(runtime.NativeObservationError, match="pending ROOT EACH witness"):
        assemble(case)
    assert {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in case.directory.iterdir()
    } == before


@pytest.mark.parametrize(
    "mutation",
    [
        "scope",
        "candidate",
        "binding",
        "definition",
        "deadline",
        "classpath",
        "xml-drift",
        "pom-drift",
        "descriptor-drift",
        "execution-drift",
        "unknown-field",
        "stale-bracket",
        "future-bracket",
        "clock-skew",
        "tls-origin",
        "jar",
        "runtime-scope",
        "wide-deadline",
    ],
)
def test_preimages_refuse_mismatched_observations_before_writing(unit_observations, mutation):
    case = unit_observations
    if mutation == "scope":
        case.boot_switch["scope"]["tenant"] = "UnitOnly-other"
    elif mutation == "candidate":
        case.runtime_manifest["candidate_sha"] = "b" * 40
    elif mutation == "binding":
        case.measurements["binding"]["database_oid"] = "999"
    elif mutation == "definition":
        case.measurements["definition"]["deployment_id"] = "UnitOnly-other"
    elif mutation == "deadline":
        case.runtime_manifest["valid_until"] = runtime.clock(case.deadline + timedelta(seconds=1))
    elif mutation == "classpath":
        case.ordered_classpath = (
            case.ordered_classpath[1],
            case.ordered_classpath[0],
            case.ordered_classpath[2],
        )
    elif mutation in {"xml-drift", "pom-drift", "descriptor-drift", "execution-drift"}:
        path = {
            "xml-drift": case.measurements["source_xml"]["path"],
            "pom-drift": str(case.pom_path),
            "descriptor-drift": case.phase_a_descriptor["path"],
            "execution-drift": case.measurements["execution_record"]["path"],
        }[mutation]
        Path(path).chmod(0o600)
        Path(path).write_bytes(b"UnitOnly-drift")
    elif mutation == "unknown-field":
        case.measurements["unknown"] = True
    elif mutation == "stale-bracket":
        case.database = replace(case.database, before=case.database.before - timedelta(seconds=10))
    elif mutation == "future-bracket":
        case.engine = replace(case.engine, after=case.engine.after + timedelta(seconds=10))
    elif mutation == "clock-skew":
        case.engine = replace(case.engine, engine_clock=case.engine.engine_clock - timedelta(seconds=10))
    elif mutation == "tls-origin":
        case.tls = replace(case.tls, origin="https://127.0.0.1:9999")
    elif mutation == "jar":
        case.boot_switch["native_jar_sha256"] = "9" * 64
    elif mutation == "runtime-scope":
        case.runtime_manifest["scope"]["tenant"] = "UnitOnly-other"
    elif mutation == "wide-deadline":
        case.deadline += timedelta(hours=1)
        for record in (case.measurements, case.boot_switch, case.runtime_manifest):
            record["valid_until"] = runtime.clock(case.deadline)
    with pytest.raises(RuntimeError):
        assemble(case)
    assert not (case.directory / "measurements.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-date",
        "skew-date",
        "redirect",
        "wrong-type",
        "version",
        "xml-mismatch",
        "definition-id",
        "deployment-absent",
        "process-key",
        "profile-noncanonical",
        "reversed-bracket",
    ],
)
def test_rest_extracts_refuse_unknown_or_inconsistent_facts(mutation):
    now = datetime.now(UTC)
    source = (
        b'<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL">'
        b'<process id="SP-OP-AUTH-001"/></definitions>'
    )
    result = responses(now, xml=source)
    before, after = now, now
    profile = canonicalize({"UnitOnly": "profile-extract"})
    if mutation == "missing-date":
        del result[2].headers["date"]
    elif mutation == "skew-date":
        result[2].headers["date"] = email.utils.format_datetime(now - timedelta(seconds=10), usegmt=True)
    elif mutation == "redirect":
        result[0] = httpx.Response(302, headers=result[0].headers, content=result[0].content)
    elif mutation == "wrong-type":
        result[0].headers["content-type"] = "text/html"
    elif mutation == "version":
        result[0] = httpx.Response(200, headers=result[0].headers, json={"version": "2.2.0"})
    elif mutation == "xml-mismatch":
        source += b"\n"
    elif mutation in {"definition-id", "deployment-absent", "process-key"}:
        payload = result[1].json()
        if mutation == "definition-id":
            payload["id"] = "UnitOnly-other"
        elif mutation == "deployment-absent":
            del payload["deploymentId"]
        else:
            payload["key"] = "SP-OP-OTHER"
        result[1] = httpx.Response(200, headers=result[1].headers, json=payload)
    elif mutation == "profile-noncanonical":
        profile = b'{ "UnitOnly": "profile-extract" }'
    elif mutation == "reversed-bracket":
        before += timedelta(seconds=1)
    with pytest.raises(runtime.NativeObservationError):
        runtime.parse_engine_observation(
            version_response=result[0],
            definition_response=result[1],
            xml_response=result[2],
            definition_id="UnitOnly-definition",
            source_xml=source,
            profile=profile,
            before=before,
            after=after,
            deadline=now + timedelta(minutes=1),
            origin="https://127.0.0.1:8443",
        )


@pytest.mark.parametrize("case", ["cycle", "missing", "future-direct", "bad-kind", "unknown-data"])
def test_dag_refuses_cycles_unknown_nodes_and_future_direct_hashes(tmp_path, case):
    tmp_path.chmod(0o700)
    until = datetime.now(UTC) + timedelta(minutes=1)
    value = {
        "schema": "human-auth-native-database.v1",
        "database_name": "UnitOnly-postgres",
        "database_oid": "1",
        "schema_name": "maezo_native",
        "schema_oid": "2",
        "owner_role": "maezo_native_schema_owner",
        "runtime_role": "cibseven_app",
    }
    nodes = {"binding.json": ("Binding", value)}
    if case == "cycle":
        value["database_name"] = runtime.ArtifactLink("binding.json")
    elif case == "missing":
        value["database_name"] = runtime.ArtifactLink("absent.json")
    elif case == "future-direct":
        value["database_name"] = dict(
            ref="UnitOnly-future",
            path=str(tmp_path / "binding.json"),
            sha256="a" * 64,
            media_type="application/json",
        )
    elif case == "bad-kind":
        nodes["binding.json"] = ("InventedQualification", value)
    else:
        value["database_name"] = object()
    with pytest.raises(runtime.NativeObservationError):
        runtime.prepare_canonical_dag(tmp_path, nodes, deadline=until)
    assert not (tmp_path / "binding.json").exists()


@pytest.mark.parametrize("case", ["symlink", "hardlink", "world-write", "secret-world-read", "expired"])
def test_file_pin_refuses_unsafe_custody(tmp_path, case):
    tmp_path.chmod(0o700)
    path = tmp_path / "UnitOnly-artifact"
    path.write_bytes(b"UnitOnly-nonsecret")
    path.chmod(0o400)
    until = datetime.now(UTC) + timedelta(minutes=1)
    secret = False
    if case == "symlink":
        link = tmp_path / "link"
        link.symlink_to(path)
        path = link
    elif case == "hardlink":
        os.link(path, tmp_path / "hardlink")
    elif case == "world-write":
        path.chmod(0o666)
    elif case == "secret-world-read":
        path.chmod(0o644)
        secret = True
    else:
        until = datetime.now(UTC) - timedelta(seconds=1)
    with pytest.raises(runtime.NativeObservationError):
        runtime.pin_file(path, ref="UnitOnly-file", media_type="text/plain", deadline=until, secret=secret)


def test_file_pin_measures_actual_bytes_without_exposing_them(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "UnitOnly-secret"
    path.write_bytes(b"UnitOnly-not-a-credential")
    path.chmod(0o400)
    ref = runtime.pin_file(
        path,
        ref="UnitOnly-file",
        media_type="application/octet-stream",
        deadline=datetime.now(UTC) + timedelta(minutes=1),
        secret=True,
    )
    assert ref["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert b"UnitOnly-not-a-credential" not in canonicalize(ref)


def test_preimage_mutation_refused_before_first_file_write(unit_observations):
    case = unit_observations
    dag = assemble(case)
    dag.records["measurements.json"]["candidate_sha"] = "b" * 40
    with pytest.raises(runtime.NativeObservationError):
        runtime.write_preimages(case.directory, dag, deadline=case.deadline)
    assert not (case.directory / "boot-switch.json").exists()


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:8443",
        "https://evil.example:8443",
        "https://127.0.0.1:65536",
        "https://127.0.0.1:8443/path",
        "https://u:p@127.0.0.1:8443",
        "https://127.0.0.1:8443?secret=x",
    ],
)
def test_only_exact_loopback_tls_origins_are_admissible(origin):
    with pytest.raises(runtime.NativeObservationError):
        runtime._origin(origin)


@pytest.mark.asyncio
async def test_database_measurement_does_not_accept_unit_callback_as_real_connection():
    with pytest.raises(runtime.NativeObservationError):
        await runtime.observe_native_database(
            object(),
            roles=("maezo_native_schema_owner", "cibseven_app", "UnitOnly-reader"),
            deadline=datetime.now(UTC) + timedelta(minutes=1),
        )


@pytest.mark.parametrize(
    "kind",
    [
        "GateRecord",
        "GateEnvelope",
        "VerifierRegistry",
        "OwnerActionResult",
        "InstallerOutput",
        "PreliminaryQualification",
    ],
)
def test_observation_producer_cannot_emit_authority_or_results(tmp_path, kind):
    tmp_path.chmod(0o700)
    with pytest.raises(runtime.NativeObservationError):
        runtime.prepare_canonical_dag(
            tmp_path,
            {"forbidden.json": (kind, {})},
            deadline=datetime.now(UTC) + timedelta(minutes=1),
        )
    assert not (tmp_path / "forbidden.json").exists()


@pytest.fixture
def single_preimage(tmp_path):
    directory = tmp_path.resolve() / "admitted"
    directory.mkdir(mode=0o700)
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    record = dict(
        schema="human-auth-native-database.v1",
        database_name="UnitOnly-database",
        database_oid="1",
        schema_name="maezo_native",
        schema_oid="2",
        owner_role="maezo_native_schema_owner",
        runtime_role="cibseven_app",
    )
    dag = runtime.prepare_canonical_dag(directory, {"binding.json": ("Binding", record)}, deadline=deadline)
    return directory, dag, deadline


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize(
    "bad_name", ["../escaped.json", "/escaped.json", "nested/escaped.json", ".json", "..\\escaped.json"]
)
def test_writer_rejects_coherent_path_escape_without_creating_any_output(single_preimage, direct, bad_name):
    directory, dag, deadline = single_preimage
    if bad_name == "/escaped.json":
        bad_name = str(directory.parent / "escaped.json")
    record, ref, kind = (
        dag.records.pop("binding.json"),
        dag.artifacts.pop("binding.json"),
        dag.kinds.pop("binding.json"),
    )
    ref.update(path=str(directory / bad_name), ref="UnitOnly-escaped")
    if direct:
        forged = runtime.CanonicalPreimages(
            {bad_name: record}, {bad_name: ref}, (bad_name,), {bad_name: kind}
        )
    else:
        dag.records[bad_name], dag.artifacts[bad_name], dag.kinds[bad_name] = record, ref, kind
        forged = replace(dag, order=(bad_name,))
    with pytest.raises(runtime.NativeObservationError):
        runtime.write_preimages(directory, forged, deadline=deadline)
    assert list(directory.iterdir()) == []
    assert list(directory.parent.iterdir()) == [directory]


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate-order",
        "empty",
        "extra-record",
        "extra-ref",
        "extra-kind",
        "missing-kind",
        "unknown-kind",
        "unknown-ref-field",
        "wrong-ref",
        "wrong-media",
        "wrong-path",
        "bad-schema",
        "custom-map",
        "custom-key",
        "custom-value",
        "custom-order",
        "oversize",
        "cycle",
    ],
)
def test_writer_revalidates_complete_closed_plan_before_any_output(single_preimage, mutation):
    directory, dag, deadline = single_preimage
    called = []

    class Custom(str):
        def __eq__(self, other):
            called.append("eq")
            return super().__eq__(other)

        __hash__ = str.__hash__

    class CustomMap(dict):
        def __iter__(self):
            called.append("iter")
            return super().__iter__()

    if mutation == "duplicate-order":
        dag = replace(dag, order=dag.order * 2)
    elif mutation == "empty":
        dag = runtime.CanonicalPreimages({}, {}, (), {})
    elif mutation in {"extra-record", "extra-ref", "extra-kind"}:
        mapping = {"extra-record": dag.records, "extra-ref": dag.artifacts, "extra-kind": dag.kinds}[mutation]
        mapping["extra.json"] = mapping["binding.json"]
    elif mutation == "missing-kind":
        dag.kinds.clear()
    elif mutation == "unknown-kind":
        dag.kinds["binding.json"] = "UnitOnly-Qualification"
    elif mutation == "unknown-ref-field":
        dag.artifacts["binding.json"]["authority"] = "UnitOnly-forged"
    elif mutation == "wrong-ref":
        dag.artifacts["binding.json"]["ref"] = "UnitOnly-forged"
    elif mutation == "wrong-media":
        dag.artifacts["binding.json"]["media_type"] = "text/plain"
    elif mutation == "wrong-path":
        dag.artifacts["binding.json"]["path"] = str(directory / "other.json")
    elif mutation == "bad-schema":
        dag.records["binding.json"]["invented"] = "UnitOnly"
        dag.artifacts["binding.json"]["sha256"] = digest(dag.records["binding.json"])
    elif mutation == "custom-map":
        dag = replace(dag, records=CustomMap(dag.records))
    elif mutation == "custom-key":
        dag = replace(dag, kinds={Custom("binding.json"): "Binding"})
    elif mutation == "custom-value":
        dag.artifacts["binding.json"]["path"] = Custom(str(directory / "binding.json"))
    elif mutation == "custom-order":
        dag = replace(dag, order=(Custom("binding.json"),))
    elif mutation == "oversize":
        dag.records["binding.json"]["database_name"] = "x" * 4194304
    else:
        dag.records["binding.json"]["cycle"] = dag.records["binding.json"]
    with pytest.raises(runtime.NativeObservationError):
        runtime.write_preimages(directory, dag, deadline=deadline)
    assert called == []
    assert list(directory.iterdir()) == []


def test_writer_uses_pinned_snapshot_after_caller_mutation_at_file_open(unit_observations, monkeypatch):
    case = unit_observations
    dag = assemble(case)
    expected = {
        name: (canonicalize(dag.records[name]), copy.deepcopy(dag.artifacts[name])) for name in dag.order
    }
    actual_open = os.open
    mutated = False

    def open_then_mutate(path, flags, *args, **kwargs):
        nonlocal mutated
        if flags & os.O_WRONLY and not mutated:
            mutated = True
            dag.records["measurements.json"]["candidate_sha"] = "f" * 40
            dag.artifacts["measurements.json"]["path"] = str(case.directory.parent / "escaped.json")
            dag.kinds.clear()
        return actual_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(runtime.os, "open", open_then_mutate)
    runtime.write_preimages(case.directory, dag, deadline=case.deadline)
    assert mutated
    for name, (raw, ref) in expected.items():
        assert (case.directory / name).read_bytes() == raw
        assert hashlib.sha256(raw).hexdigest() == ref["sha256"]
    assert not (case.directory.parent / "escaped.json").exists()


def test_writer_preserves_partial_evidence_on_cancellation(unit_observations, monkeypatch):
    case = unit_observations
    dag = assemble(case)
    original = canonicalize(dag.records[dag.order[0]])

    def cancel_after_flush(fd):
        raise KeyboardInterrupt("UnitOnly-cancellation")

    monkeypatch.setattr(runtime.os, "fsync", cancel_after_flush)
    with pytest.raises(KeyboardInterrupt):
        runtime.write_preimages(case.directory, dag, deadline=case.deadline)
    assert (case.directory / dag.order[0]).read_bytes() == original
    assert all(not (case.directory / name).exists() for name in dag.order[1:])


def test_writer_does_not_renew_deadline_after_validation(single_preimage, monkeypatch):
    directory, dag, deadline = single_preimage
    original = runtime._preimage_snapshot

    def expire_after_snapshot(*args):
        result = original(*args)
        monkeypatch.setattr(
            runtime, "current", lambda until: (_ for _ in ()).throw(runtime.NativeObservationError("expired"))
        )
        return result

    monkeypatch.setattr(runtime, "_preimage_snapshot", expire_after_snapshot)
    with pytest.raises(runtime.NativeObservationError):
        runtime.write_preimages(directory, dag, deadline=deadline)
    assert list(directory.iterdir()) == []


def test_writer_refuses_more_than_finite_node_cap(single_preimage):
    directory, dag, deadline = single_preimage
    record = dag.records["binding.json"]
    names = tuple("binding-" + str(index) + ".json" for index in range(33))
    oversized = runtime.CanonicalPreimages(
        {name: record for name in names},
        {
            name: {
                **dag.artifacts["binding.json"],
                "path": str(directory / name),
                "ref": "provider-native-" + name,
            }
            for name in names
        },
        names,
        dict.fromkeys(names, "Binding"),
    )
    with pytest.raises(runtime.NativeObservationError):
        runtime.write_preimages(directory, oversized, deadline=deadline)
    assert list(directory.iterdir()) == []


def test_writer_expiry_after_flush_retains_only_original_partial_bytes(unit_observations, monkeypatch):
    case = unit_observations
    dag = assemble(case)
    expected = canonicalize(dag.records[dag.order[0]])
    real_fsync = os.fsync

    def expire_after_flush(fd):
        real_fsync(fd)
        monkeypatch.setattr(
            runtime, "current", lambda until: (_ for _ in ()).throw(runtime.NativeObservationError("expired"))
        )

    monkeypatch.setattr(runtime.os, "fsync", expire_after_flush)
    with pytest.raises(runtime.NativeObservationError):
        runtime.write_preimages(case.directory, dag, deadline=case.deadline)
    assert (case.directory / dag.order[0]).read_bytes() == expected
    assert all(not (case.directory / name).exists() for name in dag.order[1:])


# Exact externally reviewed contract bytes, inert UnitOnly schema fixture.
# Embedding avoids an unversioned local E path or another worktree overlay.


@pytest.fixture
def protected_composed_unit_case(unit_observations, observation_v3_unit_case, request):
    """Consistent independent UNIT domains; never a physical Root witness."""
    case, observer = unit_observations, observation_v3_unit_case
    catalogue = dict(
        schema="provider-native-act-catalogue.v1",
        schemas=[
            dict(name="cibseven", oid="101", owner="unitonly_engine_owner"),
            dict(name="maezo_native", oid="2", owner="maezo_native_schema_owner"),
        ],
        relations=[
            dict(
                schema="cibseven",
                name=name,
                oid=str(200 + index),
                owner="unitonly_act_owner",
                kind="r",
                rls=False,
                force_rls=False,
                acl=None,
            )
            for index, name in enumerate(("act_ge_bytearray", "act_re_deployment", "act_re_procdef"))
        ],
    )
    # These fixture constants describe a separate ACT domain, with distinct
    # schema owner/table owner/runtime user, and a deliberately empty MZO domain.
    stable = dict(
        database_name="postgres",
        database_oid="1",
        system_identifier="123456789",
        engine_schema_name="cibseven",
        engine_schema_oid="101",
        native_schema_name="maezo_native",
        native_schema_oid="2",
        current_user="cibseven_app",
        session_user="cibseven_app",
        catalogue_projection_sha256=hashlib.sha256(observer.encode(catalogue)).hexdigest(),
        witness_projection="provider-native-physical-db-join.v2",
    )
    split = getattr(request, "param", None)
    if split in ("database-name", "database-oid", "system-id", "native-schema-oid", "engine-schema-oid"):
        field = {
            "database-name": "database_name",
            "database-oid": "database_oid",
            "system-id": "system_identifier",
            "native-schema-oid": "native_schema_oid",
            "engine-schema-oid": "engine_schema_oid",
        }[split]
        stable[field] = "UnitOnly-other-db" if split == "database-name" else "999"
        remote_catalogue = copy.deepcopy(catalogue)
        if split in ("native-schema-oid", "engine-schema-oid"):
            remote_catalogue["schemas"][1 if split == "native-schema-oid" else 0]["oid"] = "999"
            stable["catalogue_projection_sha256"] = hashlib.sha256(
                observer.encode(remote_catalogue)
            ).hexdigest()
    observer.until = case.deadline
    observer.admission["original_deadline"] = runtime.clock(case.deadline)
    observer.admission["identity"]["environment"] = case.measurements["scope"]["environment"]
    observer.admission["expected_database"]["stable_projection"] = stable
    observer.admission["candidate_sha"] = case.measurements["candidate_sha"]
    observer.admission["descriptor_sha256"] = case.phase_a_descriptor["sha256"]
    observer.admission["native_jar_sha256"] = case.measurements["native_jar"]["sha256"]
    observer.admission["support_jar_sha256"] = case.measurements["support_jar"]["sha256"]
    observer.inputs = replace(
        observer.inputs,
        admission=observer.pin("composed-admission.json", observer.encode(observer.admission), secret=True),
    )
    observer.request = runtime.native_observation_request(observer.inputs, deadline=case.deadline)
    result = observer.response["result"]
    result.update(
        request_sha256=hashlib.sha256(observer.request).hexdigest(),
        observation_admission_sha256=observer.inputs.admission.sha256,
        identity=copy.deepcopy(observer.admission["identity"]),
        original_deadline=runtime.clock(case.deadline),
        candidate_expectations=dict(
            candidate_sha=observer.admission["candidate_sha"],
            image_id=observer.admission["expected_image_id"],
            validation_origin="root-crosslink-required",
        ),
    )
    for name in ("descriptor_sha256", "native_jar_sha256", "support_jar_sha256"):
        result["mounted_pins"][name] = observer.admission[name]
    for row in result["class_provenance"]:
        if "ProcessEngineImpl" not in row["class_name"]:
            row["jar_sha256"] = observer.admission["native_jar_sha256"]
    for stage in [result["database_witness"], *result["emission_recheck"]["read_stages"]]:
        for endpoint in ("entry", "committing"):
            sample = stage[endpoint]
            sample["stable_database_projection"] = copy.deepcopy(stable)
            sample["binding_projection"].update(
                environment=observer.admission["identity"]["environment"],
                observation_admission_sha256=observer.inputs.admission.sha256,
                original_deadline=runtime.clock(case.deadline),
                candidate_expectations=copy.deepcopy(result["candidate_expectations"]),
            )
            sample["runtime_installation_projection"].update(
                mounted_pins=copy.deepcopy(result["mounted_pins"]),
                class_provenance=copy.deepcopy(result["class_provenance"]),
            )
    observed = _parse_observer_unit(observer)
    actual = _root_readback_unit(observer, observed)
    root = runtime.NativeObservationRootCrosslink(
        observer.pin("composed-root.json", observer.encode(actual), secret=True)
    )
    case.engine = runtime.corroborate_native_engine_observation(
        engine=observed, inputs=observer.inputs, root_crosslink=root, deadline=case.deadline
    )
    observed_roles = tuple(
        dict(
            role=role,
            superuser=False,
            replication=False,
            bypassrls=False,
            createdb=False,
            createrole=False,
            membership_digest=digest([]),
            schema_acl_digest=digest([]),
            table_column_acl_digest=digest([]),
        )
        for role in ("maezo_native_schema_owner", "cibseven_app", "unitonly_reader")
    )
    database_changes = dict(role_observations=observed_roles)
    if "engine_database" in runtime.NativeDatabaseObservation.__dataclass_fields__:
        database_changes["engine_database"] = dict(
            database_name="postgres", database_oid="1", system_identifier="123456789", catalogue=catalogue
        )
    case.database = replace(case.database, **database_changes)
    case.tls = runtime.NativeTlsObservation(
        case.engine.origin,
        observer.transport.expected_server_spki_sha256,
        case.tls.client_ca_sha256,
        dict(
            schema="provider-native-tls-observation.v1",
            before=runtime.clock(observer.now),
            after=runtime.clock(observer.after),
            certificate_sha256=observer.socket["server_certificate_sha256"],
        ),
    )
    case.measurements.update(
        image_id=observer.admission["expected_image_id"],
        definition=wire(case.engine.definition),
        source_xml=artifact(
            case.directory.parent, "protected-source.xml", case.engine.source_xml, media="application/xml"
        ),
        retrieved_xml=artifact(
            case.directory.parent,
            "protected-retrieved.xml",
            case.engine.retrieved_xml,
            media="application/xml",
        ),
        profile_artifact=artifact(case.directory.parent, "protected-profile.json", case.engine.profile),
    )
    case.boot_switch.update(
        definition=wire(case.engine.definition), phase_a_image_id=case.measurements["image_id"]
    )
    case.runtime_manifest["origin"] = case.engine.origin
    case.runtime_manifest["java_settings"]["signing_spki_sha256"] = case.tls.server_peer_spki_sha256
    case.measurements["execution_record"] = runtime.write_observation_evidence(
        case.directory / "protected-unit-evidence.json",
        database=case.database,
        engine=case.engine,
        tls=case.tls,
        deadline=case.deadline,
    )
    return case


def test_protected_complete_same_database_preimages_use_separate_act_and_mzo_domains(
    protected_composed_unit_case,
):
    case = protected_composed_unit_case
    dag = assemble(case)
    assert len(dag.order) == 4
    assert case.database.catalog_sha256 == digest([])
    assert (
        case.engine.route_probe["snapshot"]["database_witness"]["entry"]["stable_database_projection"][
            "catalogue_projection_sha256"
        ]
        != case.database.catalog_sha256
    )
    assert all(not Path(ref["path"]).exists() for ref in dag.artifacts.values())


@pytest.mark.parametrize("tag", ["legacy", "unknown", "missing"])
def test_protected_origin_cannot_downgrade_via_mutable_public_route(protected_composed_unit_case, tag):
    case = protected_composed_unit_case
    if tag == "missing":
        case.engine.route_probe.pop("schema")
    else:
        case.engine.route_probe["schema"] = (
            "provider-native-engine-rest-observation.v1" if tag == "legacy" else "unknown"
        )
    case.measurements["execution_record"] = runtime.write_observation_evidence(
        case.directory / "caller-mutated-route-evidence.json",
        database=case.database,
        engine=case.engine,
        tls=case.tls,
        deadline=case.deadline,
    )
    before = set(case.directory.iterdir())
    with pytest.raises(runtime.NativeObservationError):
        assemble(case)
    assert set(case.directory.iterdir()) == before


@pytest.mark.parametrize(
    "mutation",
    [
        "database-name",
        "database-oid",
        "system-id",
        "native-schema-name",
        "native-schema-oid",
        "engine-schema-oid",
        "missing-act",
        "act-catalogue",
        "native-owner",
        "runtime-role",
        "missing-runtime-role",
    ],
)
def test_protected_assembler_requires_physical_database_and_role_joins_before_output(
    protected_composed_unit_case, mutation
):
    case = protected_composed_unit_case
    if mutation in (
        "database-name",
        "database-oid",
        "native-schema-name",
        "native-schema-oid",
        "native-owner",
        "runtime-role",
    ):
        field = {
            "database-name": "database_name",
            "database-oid": "database_oid",
            "native-schema-name": "schema_name",
            "native-schema-oid": "schema_oid",
            "native-owner": "owner_role",
            "runtime-role": "runtime_role",
        }[mutation]
        case.database.binding[field] = (
            "other" if field in ("database_name", "schema_name", "owner_role", "runtime_role") else "999"
        )
        case.measurements["binding"] = copy.deepcopy(case.database.binding)
        case.boot_switch["database_binding_sha256"] = digest(case.database.binding)
    elif mutation == "system-id":
        case.database = replace(case.database, system_identifier="987654321")
    elif mutation == "engine-schema-oid":
        case.database.engine_database["catalogue"]["schemas"][0]["oid"] = "999"
    elif mutation == "missing-act":
        case.database = replace(case.database, engine_database=None)
    elif mutation == "act-catalogue":
        case.database.engine_database["catalogue"]["relations"][0]["acl"] = "UnitOnly-drift"
    else:
        case.database = replace(
            case.database,
            role_observations=tuple(
                row for row in case.database.role_observations if row["role"] != "cibseven_app"
            ),
        )
    before = set(case.directory.iterdir())
    with pytest.raises(runtime.NativeObservationError):
        assemble(case)
    assert set(case.directory.iterdir()) == before


def _timing_unit_clock(case, monkeypatch):
    """Virtual original wall/mono; no real waits/network or expiry renewal."""
    state = SimpleNamespace(wall=case.now, ns=0)

    def now(deadline):
        if state.wall >= deadline:
            raise runtime.NativeObservationError("Original observation ceiling expired")
        return state.wall

    monkeypatch.setattr(runtime, "current", now)
    monkeypatch.setattr(runtime.time, "monotonic_ns", lambda: state.ns)

    def expire(mode):
        if mode == "wall":
            state.wall = case.until
        else:
            state.ns = int((case.until - case.now).total_seconds() * 1e9)

    state.expire = expire
    return state


@pytest.mark.parametrize("mode", ["wall", "monotonic"])
@pytest.mark.parametrize("boundary", ["file-close", "xml-parse", "pki-parse", "request-wire", "proof-wire"])
def test_observer_verified_returns_include_last_sync_validation_and_fd_close(
    observation_v3_unit_case, monkeypatch, mode, boundary
):
    case = observation_v3_unit_case
    state = _timing_unit_clock(case, monkeypatch)
    if boundary == "file-close":
        close = runtime.os.close

        def close_then_expire(fd):
            close(fd)
            state.expire(mode)

        monkeypatch.setattr(runtime.os, "close", close_then_expire)

        def operation():
            return case.inputs.source_xml.read(deadline=case.until, limit=524288, private=False)
    elif boundary == "xml-parse":
        parse = runtime.ElementTree.fromstring

        def parse_then_expire(raw):
            result = parse(raw)
            state.expire(mode)
            return result

        monkeypatch.setattr(runtime.ElementTree, "fromstring", parse_then_expire)

        def operation():
            return case.inputs.load(case.until)
    elif boundary == "pki-parse":
        parse = runtime.x509.load_pem_x509_certificate

        def parse_then_expire(raw):
            result = parse(raw)
            state.expire(mode)
            return result

        monkeypatch.setattr(runtime.x509, "load_pem_x509_certificate", parse_then_expire)

        def operation():
            return case.transport.guard(case.until)
    else:
        serialize = runtime._observation_bytes
        state.socket_serializations = 0

        def serialize_then_expire(value):
            raw = serialize(value)
            if boundary == "request-wire" and value.get("operation") == "read_runtime_definition":
                state.expire(mode)
            elif boundary == "proof-wire" and value.get("schema") == "provider-native-observer-http-tls.v1":
                state.socket_serializations += 1
                if state.socket_serializations == 2:
                    state.expire(mode)
            return raw

        monkeypatch.setattr(runtime, "_observation_bytes", serialize_then_expire)
        operation = (
            (lambda: runtime.native_observation_request(case.inputs, deadline=case.until))
            if boundary == "request-wire"
            else (lambda: _parse_observer_unit(case))
        )
    with pytest.raises(runtime.NativeObservationError):
        operation()


@pytest.mark.parametrize("mode", ["wall", "monotonic"])
def test_observer_context_returns_only_after_both_private_fds_close(
    observation_v3_unit_case, monkeypatch, mode
):
    case = observation_v3_unit_case
    state = _timing_unit_clock(case, monkeypatch)
    opened, closed = [], []
    opening, closing = runtime.os.open, runtime.os.close

    def open_selected(path, flags, *args, **kwargs):
        fd = opening(path, flags, *args, **kwargs)
        if (
            path in (case.transport.client_certificate.path, case.transport.client_key.path)
            and not flags & os.O_NONBLOCK
        ):
            opened.append(fd)
        return fd

    def close_selected(fd):
        closing(fd)
        if fd in opened:
            closed.append(fd)
            if len(closed) == 2:
                state.expire(mode)

    monkeypatch.setattr(runtime.os, "open", open_selected)
    monkeypatch.setattr(runtime.os, "close", close_selected)
    with pytest.raises(runtime.NativeObservationError):
        case.transport.context(case.until)
    assert len(opened) == len(closed) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["wall", "monotonic"])
async def test_observer_postawait_validation_cannot_return_after_original_budget(
    observation_v3_unit_case, monkeypatch, mode
):
    import asyncio

    case = observation_v3_unit_case
    state = _timing_unit_clock(case, monkeypatch)
    guard = runtime._ObservationWaitGuard.start(case.until)
    load = runtime.NativeObservationInputs.load

    def load_then_expire(self, deadline):
        data = load(self, deadline)
        state.expire(mode)
        return data

    monkeypatch.setattr(runtime.NativeObservationInputs, "load", load_then_expire)
    with pytest.raises(runtime.NativeObservationError):
        await runtime._observation_wait(
            asyncio.sleep(0, result="UnitOnly-no-effect"),
            guard=guard,
            transport=case.transport,
            inputs=case.inputs,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["prepost-validation", "parsed-return"])
@pytest.mark.parametrize("mode", ["wall", "monotonic"])
async def test_observer_last_effect_and_return_guard_survive_sync_expiry_with_full_close(
    observation_v3_unit_case, monkeypatch, boundary, mode
):
    """Inert HTTP stream only, not TLS/engine runtime evidence."""
    import asyncio

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    case = observation_v3_unit_case
    state = _timing_unit_clock(case, monkeypatch)
    state.connected, state.closed, state.completed, state.sent, state.reads, state.loads = (
        False,
        False,
        False,
        b"",
        0,
        0,
    )
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "UnitOnly-inert-peer")])
    peer = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(case.now - timedelta(minutes=1))
        .not_valid_after(case.now + timedelta(hours=1))
        .sign(key, hashes.SHA256())
    )
    spki = hashlib.sha256(
        key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    ).hexdigest()
    transport = replace(case.transport, expected_server_spki_sha256=spki)

    class UnitOnlyTls:
        def getpeercert(self, *, binary_form):
            assert binary_form
            return peer.public_bytes(serialization.Encoding.DER)

        def version(self):
            return "TLSv1.3"

        def cipher(self):
            return ("UnitOnly-cipher", "TLSv1.3", 256)

    class UnitOnlyWriter:
        def get_extra_info(self, name):
            return ("127.0.0.1", 8443) if name == "peername" else UnitOnlyTls()

        def write(self, data):
            state.sent += data

        async def drain(self):
            await asyncio.sleep(0)

        def close(self):
            state.closed = True

        async def wait_closed(self):
            await asyncio.sleep(0)
            state.completed = True

    class UnitOnlyReader:
        async def read(self, bound):
            await asyncio.sleep(0)
            state.reads += 1
            body = case.encode(case.response)
            return (
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
                if state.reads == 1
                else b""
            )

    async def connect(host, port, *, ssl, server_hostname):
        assert host == server_hostname == "127.0.0.1" and port == 8443
        await asyncio.sleep(0)
        state.connected = True
        state.wall = max(state.wall, case.after)  # Valid remote bracket, never restore an expired clock.
        return UnitOnlyReader(), UnitOnlyWriter()

    monkeypatch.setattr(runtime.asyncio, "open_connection", connect)
    if boundary == "prepost-validation":
        load = runtime.NativeObservationInputs.load

        def load_then_expire(self, deadline):
            result = load(self, deadline)
            if state.connected:
                state.loads += 1
                if state.loads == 2:
                    state.expire(mode)
            return result

        monkeypatch.setattr(runtime.NativeObservationInputs, "load", load_then_expire)
    else:
        parse = runtime.parse_native_engine_observation

        def parse_then_expire(**kwargs):
            result = parse(**kwargs)
            state.expire(mode)
            return result

        monkeypatch.setattr(runtime, "parse_native_engine_observation", parse_then_expire)
    with pytest.raises(runtime.NativeObservationError):
        await runtime.observe_native_engine_operation(
            inputs=case.inputs, transport=transport, deadline=case.until
        )
    if boundary == "prepost-validation":
        assert state.sent == b""  # Expiry after final synchronous validation never initiates POST.
    else:
        assert state.sent.startswith(b"POST ")
    assert state.closed and state.completed


@pytest.mark.parametrize("version", ["v3", "unknown"])
def test_observer_live_protected_parser_refuses_predecessor_or_unknown_snapshot(
    observation_v3_unit_case, version
):
    case = observation_v3_unit_case
    case.response["result"]["schema"] = "provider-native-runtime-snapshot." + version
    with pytest.raises(runtime.NativeObservationError):
        _parse_observer_unit(case)


@pytest.mark.parametrize(
    "protected_composed_unit_case",
    ["database-name", "database-oid", "system-id", "native-schema-oid", "engine-schema-oid"],
    indirect=True,
)
def test_protected_corroborated_engine_and_root_cannot_be_from_a_different_database(
    protected_composed_unit_case,
):
    case = protected_composed_unit_case
    before = set(case.directory.iterdir())
    with pytest.raises(runtime.NativeObservationError):
        assemble(case)
    assert set(case.directory.iterdir()) == before


@pytest.mark.parametrize(
    "field", ["identity-incarnation", "identity-schema-oid", "protected-role", "protected-schema-oid"]
)
def test_protected_assembler_joins_exact_private_lifecycle_source_bindings(
    protected_composed_unit_case, field
):
    case = protected_composed_unit_case
    if field.startswith("identity"):
        key = "database_incarnation" if field == "identity-incarnation" else "source_schema_oid"
        case.identity_binding = case.identity_binding.model_copy(
            update={key: "UnitOnly-other-source-incarnation" if key == "database_incarnation" else 999}
        )
    else:
        key = "role" if field == "protected-role" else "schema_oid"
        case.protected_binding = case.protected_binding.model_copy(
            update={key: "unitonly_other_protected" if key == "role" else 999}
        )
    before = set(case.directory.iterdir())
    with pytest.raises(runtime.NativeObservationError):
        assemble(case)
    assert set(case.directory.iterdir()) == before


@pytest.mark.parametrize("mode", ["wall", "monotonic"])
def test_assembler_verified_return_includes_final_dag_validation_budget(unit_observations, monkeypatch, mode):
    case = unit_observations
    state = _timing_unit_clock(SimpleNamespace(now=case.database.before, until=case.deadline), monkeypatch)
    prepare = runtime.prepare_canonical_dag

    def prepare_then_expire(*args, **kwargs):
        result = prepare(*args, **kwargs)
        state.expire(mode)
        return result

    monkeypatch.setattr(runtime, "prepare_canonical_dag", prepare_then_expire)
    before = set(case.directory.iterdir())
    with pytest.raises(runtime.NativeObservationError):
        assemble(case)
    assert set(case.directory.iterdir()) == before


_UNIT_ONLY_OBSERVATION_SCHEMA = gzip.decompress(
    base64.b64decode(
        b"H4sIAAAAAAACE+19aXfbRpbod/+KGr586B4RBElRmxX3HIqiY3a0NUnZTiceHBAoSohBgAFAyUrG77e/e29VASCJjVqdeck5jiQs"
        b"tdx9q4s/XjFW+y60rvnMrL1mtesomoevdf3X0Pc0cbnhB1e6HZjTSG83202t1dbl83V62bHTL85M/rvfcLwb03VsfR74N47NA80z"
        b"I+eGa8HCi5wZ1/xJyIMbuAazTB3PibjGZ04Y4t83nYacGBchJomcyOU4zRmNw2DciFsRt1lqJCZGYmokFkbmFWfhwrJ4GPoBu+mI"
        b"0XyPn09htJ/hD8b+oP/jTgKOV2v/R//O5tNQH/LfFjyManT/a73s4XDueyGv9vR5suquLZcr34T/fxKAxSfhJTFITa3mdTJqdDcn"
        b"oPiTXwEYtbq6btq2g0Ob7kXgz3kQORwHmppuyOOH5ulbakhxPfIt3126Ctct2B3OXhMY5t6V4wEiYQzaRuOmVYsf/1pPxrPMuTlx"
        b"XCe6M2znankHS7sIo8Dxrmr19L25GUU88PD2f/9satOmdvDpj93O1++y54pXk7v4gJu2IanQAAAjxcSwXx0OQIGUY3zmd7kDji60"
        b"8wutezl+pzWbOSAIeOgvAosbggoetvvtdt7ub8zAMSfuCkLz6aQyrWTQy9elewHQphNwFAQ/f8qmAj8IuJuJm29gdabrGjMzsq4R"
        b"BTmYpjkz3+ZB4AeG5ds8l0qyERb5c8fa8J0ZECRItQ3fuvWDzzwwSFRv8t7cDMwZByJ8DppK3aFBpFw0wmuzvbO79kQx81QVHys7"
        b"ptc837P4o823zLBZ8/mBA8LUdEEgmbYLYvWhc//yi/1H56uGP9rqx1j8eL3045dfGvjL7td/ry7xVc5yl7ipBGOZYC3de+p+Nrfm"
        b"C9Msan61MkD2BhK1V6i6MnVNjsbIUwCZEjtHVObJqGzpsyZaMuRGplDI5vhskL9Ko0ZCtRZbP9+WdQJwX7jRN2GaiKU8uxSNLfs1"
        b"eRJDLM9GDz1zHl77AL5OsfgKhGX6/HI6Zfsbf6mLF1UXK0uc+65j5XDVU6KijKOfcm7gIWCc6K5oygw+34jXy/hdTMY904sy75Xv"
        b"fh0C/6H9309/tOrAU6sQyICCUEnejRP43ow/3irGgMtzz73Tfu5q/za13wEnRuO1RgvrNKsuDDWe65t23qpyZaKQNDzYbBoD3gjX"
        b"PZ7nRAXIxAUs++UWEC4Eyb/cCoS4LEU54MqZOtw2ZpEbZoz8qmSmfMM4zZQl3FJEs1UILZ8CClCTD7OVG5+KZT4HY9JCwTR1LDPi"
        b"z6+IaQXh/LPz/FMLg9PwwHZ+8Kz5JL6m7hyQSzfcKxA0CYG3G61Gs3g8cxFdA+p/FyYVUCz4JnbRoFGw4IUjCrI3wAq1Pj/KgMKg"
        b"NRZzG0iscL+kPIv3mwoAvrTSTpayHqV5Tmlp87nr36Ewetl15Mdeq8dgq4xfUUU7XsSvMsUo+taAudliBs+1Ks394gZaHI/IkVfP"
        b"tY4vMzdfVN9vEfkiu2gREzPku53HWsTf/ut1YqZu6Z/Aufr7f65dbH998+Z/li9tf33z9//6LofIzC8noGWia5hi9+CgudOuDmLn"
        b"d/5YBJ63Nnl/p91p7+9XtAzDOfdsXmqNL0feH8sgWxa4pZKwRERVkDD1TYzCZQ4tZp1imi6iiUKsbGb8Bf7UcfmLq1HHmy8iI381"
        b"K26eH0Smq6HZowEDmJ/5UqzwEbwKDEuBYLA1uSSN1lWNP15aMP7JhOL+3vZ+pxpkn08e7u7sbO8+uuxapvJ86synqnx058FrI3Hw"
        b"683MkPLvxUXCxPcjY7F4RGtWsdL+Vy3+vVPh91a7svXr2CT5w7lp8We1TcPIDCKDyiP43LeujVn4rPNLRzrgVw7g467QdlFS9n4j"
        b"P6ft+0B+T0i4lFYqorQq5KvCcSPpYLkm2EUYY0X7pzh3YgaBuW5eIVkNIk602Vm/aX7Jv+nIO1mB82KxtKFgKhdNMSgKSJEATvzz"
        b"c+ZdpI6gYfmzhsh8qpBg44P85cJdZKuCWFVcNVQcqTGZz2TutOHM5m7jQsjwPl0awJX8cbJXMRTJxOPY2E5VvlUc63oxM71GF2y0"
        b"96a74Bmx2XWKy5UCqJvMwBAJ9htuABNf50O+ghhYEQSuM9ET86VhaJ+2fvmlAVN+V9togYXG371WVmIA5i1GEOg3sxwkKh4YV4G/"
        b"mOcvJza/ZR0AENPM9zJnqRgvKZDOy1xckeTKEF+Oi3LwvCrjkXU1tPCc3xZcCU8Mxq7bZq6bKp5N/5enoyPT8cJy2smVutVEaVVx"
        b"ukwglWRnzjhfM69/zdlACQ2tLv+BQg70Yy+BfK6Xkn6mCif8GVG8oYL7C9n/+/i50Ar5C+H/axBezVT8/wC3G7ljMx/Yg9vGPBvL"
        b"zxutSdf0yVzviwdCbR5agTOP/ODl1yIKkoxSH+E51hIu5hhB/0YWg1GOxdxIIcvxQOXDL3ffzupgQfa3tbKASnfnzgtg8YFRsXxR"
        b"UYmHKzFXNaq/Jzneg04qInCzcJzp2Q6W0xj8yxwkPEH05TVBsizY1KOTZPWiUWdmXvFHLIYRKHp9D1alM7WC3iumHwPfjzQr8MPQ"
        b"dbzPWsxPj86Ly8gqgmKlfW0YTvatzy9OriJfX4qRX29mWngXRnymWYsgAO4eg2dy6riuE1ZUJSa4qQBnlBRgtZmPVkj08GMAJUtG"
        b"7yu8/pOsWaY58ojrG1vtzPf8yPccy+CuOQ8Bxl74aDJTJC/r7eYm9WXughja82d0ViU/jbhZFs+0gG1M6w7pP1qUDlpbeL8tQLxQ"
        b"ZfctnqETCK0214S2wY1FZBkLD2ubweWK7ipsxlu4bqU50sNW3BJgGgwDm1Odjhe5d4YFO5wEIH2fQLJLuVZJEFVj/TJmq0relQmv"
        b"AhltiP1qiNxMiy0ifzotrGjGNAboWS0KTC80Leqe0Ts/PR2Mx4OzHzQz1PxppQJvXCKYy17kWGHhjBG3rgHqprvUscPHgzier/Hp"
        b"FPSqtlQ1XlJwbUYm1pkYt07k5RSHrPbKGGEXjw/y+eIifHUEMOBUcP4NlJ4sPNsMyiuYPX6rqWilwjK2qtAw+sa/AIynII41y5xH"
        b"i0Dks5yoYqGaOLr051C68dFGwQ3GHBiaVl8GwbVzA7kyvRIHrPCdMNQERiRtcRs4INKmC0KIy82Qb4wP4xFsRkkZSECSYKqTBjVD"
        b"oSY5pbZCdhnGailGbrRSPbCf+UB+ScaGAmGTnG7K6cg5VL6OCNmqaB44IAlTLYsIhtmlow+u/JESpISzN+KlimxRjXhL6Koi5O+h"
        b"Ko1iyfbCJ6ClqDZSutoQvAk/vKkTzDY9CHWPjhypPl0rz6fO629yqP5+HT0KD4eXnuDOO2Zd8fhh2RnBgoN8ZaftKp2eq3IgrvCM"
        b"W/6ZtZwa/IJa3LJCvKLMULWoYWZsJtvUrWCblpmPZQZgueC4D9tmd6h5qkYzsnNIZt+VzD5yT92DJbOnSNnZeeE5xAJlqalIuk9K"
        b"TheFp++Uknso76kO7xZ3Sdho1s26I6wupKArwqYdEfKGLjqg/ETgze188FQT5nc6eKoZc5MAlboa3MegyDywVtDBIK97QVnnguyu"
        b"BTkdCzK6FWQ3DqvWoeAxuz6VdCR4zKnyOxBUnyWb9NKzFB1Tf9x58o+hP+Y8j9Xis/g4eeFRmcwjMumxc09JPyqlCuPx6ScqSutu"
        b"Ps+ydlviB7JKQfLl5G8rz1Wcr01PWVab85hgLK29eczJygqgHrWHb8UCsEcFpqq6wH6Ma+ZY9gGc2vwaPBFtzbsWlycV2lhuXLD0"
        b"JFuuVJD0mDNntct7zDbIZDpkBIUqz/GwYNASLZd06nuBJcViUfnSq0tajbb25QsX13chZoKO1Xt5bS5LqskeTEoV/ey12FdW5KhC"
        b"1KgoYpQTLcrtLZHbUyKvcWx+D4nsBhCrajxX7xYqykKVVqyDCpVGJSlfIJY3FJ4bSLs18ZQpTwo5u5DHSjgkM7ajnPrRargltkz/"
        b"yO1gvhL6qJkL24kM04I9U3vk7HbDBniKrh1Sn/TMDsNrt7JfSHqU12o53flzG/Hn2uarZvgSSGEseBp9QzH80nIkfYnvVKg4RkNQ"
        b"diOZuiFDGpjHWW99vLKYjCdUN/eMe+GK9VC7GJ6/Hxz3h1q/qZ11x4P3fW14eTYenPa186NRf/gerp2fab3zs/Gw2xvjhvXjfm8w"
        b"wqvw8vi8d37SmKVCkZ8y+srXVrs9yyhPaTDn6xIh5mqAJw80RhgeX8Zsoa4a0QtqfRfJe9m6Ck/Vz68wMUYH7GVQ+YXV1tqeq684"
        b"U4zkwuSpsRcH6x8UklgPaBYEDdSM/gNiBi3Anah4a9ZbB7nmM6XjDWFSgBQMnng+aWhIUZYF0aRaQaapqgz09ICSBkSVhdMZLkO8"
        b"UGWwp1+8rLYwFuE6gtfgbZjzeQ6xcJGVeuAwYJ+arn+1SEuHp3fXlIzJl8K5OYK5VBiaPdF+9R2vcdPeXCAui5E8bi9kzWI+KmSO"
        b"YkouJM08OsojjIqYLkZNngq44kcO2P/e1TMqgKIm8E8VMSr5DMDjBjKf8cNW314gITNf+pih+fz8aOVZqudF/8qk/JVJ+fNmUgrO"
        b"7T194UTZOb37tVwvL1soOJe30ZTl5/FWZ65yDm/DM3j3ScYXnbnLO29XfNbuHtVMObV8VcsIS76xVRRlW6tFyKlD+HMGaZdYOvub"
        b"WyJ4NgAyM10RwHtG4+55lGSVDxJkl8yumyqFHyEoH6TowwO5Hx1ID5DfE/XpZXRRD9T7yeeH9z5dayFd0vO0tN9obq/RtUqtSj1O"
        b"H2++ij1Nc05Clo321N8cuYdmyutXWtirtEKf0io9Skv7k+Z8oLa4J2nBQZjcXqS5fUhzDrwUn03b4GRa8bm0sh5LRf1G799r9DH6"
        b"jD5Wj9FNm0aV902s1lO0/KDdPXuJZi+ooJnLhisp7FWxPnlpz9Cnnb60R2jF/qBfS796mXNuq6gfaIVeoEV9QIt7gBb0//yUK8aL"
        b"e37m9PvM0l35beIqdFZ+nFbJD+7r+ej9YO8hTEo6wBV2f1sZ7VtH08N7c/6FsG+Arzbtr/kX0l4OaVV6ZP7p8VNq7hf0vHx6b3yT"
        b"HpdP9cHKKj0tn+yD2RV6WD7V3JV6Vj7Z5PfqUfnUq6nek/KpVlKtB+WjzX6PiEbVXpOlfSZLe0yW95e8R2/JDftKlvWU3DxPkBOD"
        b"LzsyX3ZcPveofM6p96IT78s6IbuQQtRrjJPD4R/W+hg9Tbh9YlqfAXWwtqeueVIzEcV8A2UGqYP4jm04UwMQ6Fx56xF83+NVHFW1"
        b"BezQVtvIqttI/OQDu5qBsrLvzE5BqcqrK0PVGH2Bl1YhlR3x9D1PlRYVjw4jLkxXk42pVEsqexL+5soKJi0ZLHcyfMlITZp30j8V"
        b"nHGdEL+dKDhJUy9osgFTbsrIly0b8kZe/qBoLtRD3zUL692G/e6xIRuw9Y9L2Cl0N045qXcjt7QcIoshs4+tjU9GN61Ge1Xii8vb"
        b"5YHy/K47LywdchuifjPrKuViP4yuQPX+5qZb/FFGBAaZzTVgtJgpyjiutM9Y5rzUr2WTGSvq/7T+ylU21cR+qZAslHEVZVKOQCkV"
        b"FTmcX8LUOexVTuiVSa6YMPKrRkfUvvO5TnrEJb1PdeRjIopgNxs/q3Y2+0CJPM7kpEoyNpiquKCjsLI8hfzSDeXasPc9q5KFtWKY"
        b"bwCzks3mU+5zWebU567aifFe92J8Oeyvqt7+6WCEB7uMYb/3rt/7sdLR8StuhNjHrSBVXVIi2VzNVcvr+zmUhgfERFc/g3/hluiy"
        b"m1FTsnmJ48NqSZZLd6O1bqvZLC0FW46xisI+EkcpHzaU9PZLOv+Vm4LozlKnTzXUi1k51WXDFa/l02xF0lrDbTaaKkC8BJjLguSV"
        b"3GDti6jc1OigqxYs3NRZTpAzgWNFbG4GsIXXzF7MXTpEzj7zu7DObD6Prv/R2q2zqeubEVyZme7Ux9Wwy/HbfT0El82/ghfg1sL7"
        b"7Pm3Hr3KgMoWIWcTDk9zlpRIMtf3Py/mDdZ1XSbZmyHe8RWUaWzi+26jpioERaPHBnbFpiOqMC578wYXbM447D9s0GlnvBa3j0su"
        b"DXk4B7rkDdGBTtxopN9eq89cHinz9uqoaw81khFYfPaafc/WR/v+TfLAVmsHRNsh83wAhcdvzQQMA2nU6an6UB1P9etgJl1x+B2U"
        b"DwPlwzg6zwyIAvbImXmFyYWIhbBdhjWswEM2O5IdWS+o8lVPkHPI6NytLk8/6wLmOibJAEt8No/u6ku4dHBsoF6XR7DV1Qbb8fIR"
        b"19dmeA1IHr3rtnd2WWDeMuGTT+6AeGDZFpAajs1EDS1cgcFhaPbPEPAgnrJMz6de3odsuQMojRdwiwPf2Opeg50hJAWuWMjdKa4B"
        b"XvV9WHUEdAnzAlgDfgv/cGXwEJJGxFNr762E2ySEpwAaRnVgDGmqLnfDRB2GJqoYmDLGmfT3FT8AnBzTlUHDQxb6aOuyCTyHs+Gw"
        b"DZYqagDM+kAsgMrFdOpYDgyacAjs/OPpSQIbhobMbgd4F8+7A4XttNo/Okd1ufCAg/CCG3UGoGOmZzOsQDskCAp8f5y5b94EHCQD"
        b"B2jCXw12Icp9U5MAwXm0ZMTL9292OzDFIZPxR0YPp2DmhEDVgFMKocJCwP4h4yfexVtkiwRZKFvisVvNzv7O3i5s33IXaIcxfxEh"
        b"PXo33AWL5hD3r8uKZKDdyERzjlHf4TCG/NLnBog0QFN5FmFAR34JhG0GiERS5HaKAsChZLIZfwuwETpfNKBSB6kgmAFn2z4XG6Qt"
        b"M9V2X3XoP6RvKcCmeizVYp/F3YwBvj/o45OR3hscsUmA3lWEG98B2nLtYXsCQhNgLsNjaBIyZC+SFCNCWY+R14vk48zihatT7yyu"
        b"ydappp6haBhiTb/CF70HIjyu8YdrkS9p+hhGBklzMTjWz0bg/er/7A71JHCvx9WQaQGgKP2Um6AjOAqskKAuVCWxI2r0UBSIx2se"
        b"eeY8vEZawXOYSDtmaPhTJvqe1gnKAb/xBeK00JxyJjqYs7j7KqNe5g3Wp+b+IRE59TQ6YlLTp6mBpeCqyy9tWMuLGmJbcgR5eodA"
        b"WgFHVQlYmCxACiJeyAeAVevUrkIHQp34X3SSTPNI7/Z+rDNQkVySPygElpTqN5jQW5YJNIssw+fXALgAGCP+iMGMz/zgToAhnhSG"
        b"94OEXkW3DRwAkORceZJYpDCfm06gS5clZKS6CUD8C6p+oGkkDHYFDkoUKnAlY3dbr9fkrGRU4je4SvL6ZHA6GBN7sOgaRQG1G2cj"
        b"AB7oCthiANIlOGSi+wYtAAWQ1A4gOmECNg18fN0PldCliaRYF1PT+3K4EDkawzxMyHngrwhUJulCeJkjbsF+mYdwH0a3Sb/ADZL+"
        b"Qj0l+2y/ZuIU7iA+hMtmC9wsl2wB1IBdO9B/RMk9db7AzBjF9sHK811DvP63vzMESwA0CQID2C/iKS2T6GSiCo3ADn/1P/Z7l+M+"
        b"wMd0XKRtQObCM2/gL5wP8Q+3JgHZarYubCpBoeDyuRigATCB0KHVfXFsBsaYkBqsONAOQoUjf8IoPhmC+DKIM1wsoJPJA7uJcASC"
        b"UD2LmexZjKs12Vn/A1PVaj0RdyeAMbKScaBrMCBuBN8ijKUsFvkyPTnbAmxzosslM3FaR9o7HsENVnxrRV90FeVjSTSMAAWiMSQh"
        b"qL48QTEzhEiDnUuLTMoXKWxAXqHdhNL62vSu0roAtyv1qNRVgvpNodUBdNoElDQPEJRXCzOwGdnpyypfJ1UFbg43Z0BOwGQhbZW2"
        b"JCUnGP1hhIOjGS7MRbltGm+CAh+2dQsSj4f61F2E1zptlQZBqgdOA2niOiTk8A4oGhP8fxQoGIBQaBMYvxOsSKer0nKuLgxS8Fz0"
        b"VbO0h1wicr1Y1Cs1NEBDfGWAJUpC8B3SX11ON0WVKHK5LM7lMll2hEreAfmD2AWZ0GBv4Wn5MIyEBHbFUYwiuiV8xToUOlDkAxcA"
        b"paHqB102JTSfdgdnDPTXoZoaDE+xZOINgVpEvo7549HFgGF9K2jGeGtqHpwBDW4fiDeAdd8gytkEZkFRpJMhh8LqFAfDgaQBTCTv"
        b"eELMxm6CrmxylkKx2ADAQsFMeUGoZ1CuzkCL3qSl1rD3Tutu66N+Txu12IVxfCS3hD4ZR6s3L9wFvl+6S0E93ZqgvtaPoL7ehKC+"
        b"1nmgvt5uoL7WY6CebixQT3cTqBe0EKiv9w0QHqWKDIPu4OE1IBXAZEZS5KTlgtAGiaO31vCqsdbGhnXPjpUUYeTK64n/3hAIlW1t"
        b"lJhEnwJolVz4K3RdwSAIAYzJOpPhhazy52BkTJlaDohzsGBWEQyYPRrAYgRus+O7CuW0UTSKSOBSqywUXGRHr5MfOoO60G/KsxR2"
        b"Nf47ZPYdYNGx1PID5fXIZ2OMkX0C5ooLQs+bL8C0yT4WGFsdwHsEP2WI4vvLU5DlKj1SlrIcD8WLS0/FkyUGLUP9Y6KMyQDm4Gw0"
        b"7p6cSHgWBrEzwQrcSB728KKnI/pfS09HF4jX//n+VAiElE+kp32lLRBJkvPFPgT5ggdxLWhXEawiP5I+ynaIiQVHALNXWXJKhIEU"
        b"RbGPpISPkGn5BYNpMCBJGpKNsbQhv0K5y7p1Z7kksFAqosEKI6bMJLXgVBhR2Bok53pC1zaAOKQJIK/87e9v3oDOlqpKsSUTYW3p"
        b"NqObCc80jiej31wZ82+kfu3F7zXY6F8nglW2ZHKK/ClyikL94+CY9DNZH7CJ8bvBaE0kHAr2A6QHC2wypxMrkgkaH/4FZgC3xF+E"
        b"I4ETeON4dCbsLsnxCKE4KIarAo8ZNcTxUcqGYDJwtPoxCLGoNzLIzlSksXnIVj8L0Uh9oAamtEn5iLdXY/HxMGGr0Tirk4p1rhaw"
        b"C4zmfdmvCzJDVbsq1Jj49B1hCeO6TIhvFhcMUAQIFfGWCHr6gRYuLKxYEuK4IC4q4xspJwejgUSTqX5+ymTwmQ2uNBqzV64/Md3x"
        b"R+Akf5qGqWTE87O+wi3YhHm0hYGsVEJXX8ri6tmJSCQpJvOdwtw/JAsgEeGhlCAkDNY1RGyQI0HS7bNLkDpzHszQ2BGP0iVfiT1l"
        b"ltfF8+pP5ZiFNJV4EQaFKchOAHvK/8yVDY+IRCv+mrtzNLxw8Aw7nh31xx/6/TNkExetd4Tf+COTRDb3fVeBDOjmDgkiukYrDqNb"
        b"aCXjqoXlfMjSXAgL01GgEg+e9d/3h7FfKaUamGe+axNTEa0JawcY0MHgCBES3UtBegsrV3R0HYRRDL4cBZRTQR4mGwWA3orhhpB3"
        b"puCrRlKwhvEt8u5sB233OiMsrId1KCAB6jlFIIcKVMrHdZ0pF0JzzXsGIF/8wOLSBOmSh6/ZEvlhUDizeAJvEBk0Vmoc8EaK0Fbu"
        b"HuaMJjxWwTbi819CiuMaSSPERgoM74HQDtVdWoS6e6iQSCgGkrsl+lRfsRR8dguOEH67VOYFQDxgU5tYeYCcFLIapRPgHpXZ+XQK"
        b"ivYYQIqfitP+QfoYPJnLcW9XqCgwACiwRFP/jhFSQYg62RyxI5yCPihitv4FHVSISuYmH6Nk176InM/R4T47H+POaZWSqVkKws5s"
        b"tiB7kYUygNWgyUg0J5JUSZPv34hVwE+h0BurCZ3v3yC0hBmgZP7aMyqfkOQdjol+ASYiHggrvglpIdi8LMQ4hYwv6uFnfoshRmFr"
        b"xeYM8HDyKVYVwGywcZy7YIjGrfhrpoJXyTFEUsA6nFsTYEOu77rNBThYU2VpTNA6QnbSHY1JXQu6zMh2CXM2DlrBFgUzHbLcj8ax"
        b"GTcxaidNqHSKfUs8mw46YExZAFHuEB6/QkGM4BIRRxlmRN+UuRhIA9sRpFJKAkhlCFJSefWw+ygjKiGDEmgyHwp9rGIpyfIJ4u4d"
        b"Ul0anmT+InpZv9t7pwbU1fvEq1spna5a2unS2UIZT6ZTHchFeZjAQpYZeCJYIby1xLrXA98FybjFeoMjMrjD1ZAwGrxxWBiMdqK4"
        b"GSoDKSWFpXrpoar3J75w5ElMYbRCqRngS2FBKBIR0E9Jedw8WQKHjMgK9hfrlmRsEZuKTAdIKx2Qriea5k7OiQYfGWyC8mI4ox9B"
        b"MWThVQvBRXr++CgFq61ub7wKJ1QsRNeHqUC8sKR0GU1Q0QbhJVAQ4U6oNVTv6qUGu8AVdPWjVGRlS8RaZIPpJJKCKlpoMxHaMMOU"
        b"w+AtZqQ2DzHbGlFoP6EGoayFJo+1fBroRL+317A56Sql3KNYkJjkeuHwaAtejt+xdH0MUyFxdIqFigUyx/ge7VAljECALJA2FxRO"
        b"oxjpji5D+VfpUOpyuo6tputg/8QcoLopcSeZD+EQZ9xCMB17fbgGfGqJKCIdzQLC8nQwlNncXYTCoUw6EIkEGqXv4oEO2T/NQPp7"
        b"Mhol1FxCxXRT5AjjFCHu16M8LSb7UrmHuWveydQACFN0v4VIEAvALDDDpNLW6OLHwVbsq7wD520LwX95OTjeEq8LGUSKlmz/VrPd"
        b"QVyiaXDDwbUhHOoq3icl3KGwZ2NxHAquQV9pMZM55gYmxcl4QciGMocxWSCzKjNMZLNgRbgqDNkLw14Y9GTeO0oFxakcQCTsh6Nh"
        b"e5tE0BOHjIItHud2CCoKl+kHaIAENDJoBwd3sRy0lCUFrQ548nEOMQ75CgNe6RBB6UJHpDkEPeM4dyHzheQripgH6AQRVBZkFoeK"
        b"W6eYh1UZSsqZCh8sgS1QOcBW5i6XiihS8eC5iOLG4xI+CMyHqQUn+c147+87MCbQk0UOekRuF6x4cK6B6LQ4aa8I9n6FxSAyoyHN"
        b"WgwIpOj+7bEOVpsSYymrSFeqDPeOQkZfinwLwyAUqSE02WSoWEY5hFaH3dwlBofYvk4GANkWKHJ0C6Sqb9/p6eydWE1iwpF/IKPw"
        b"iS0ispAu2Fbwboj+LFjwtHV0jUDIpLZJiInBd76IwLBMh+0DnorX42LFEnC0BtqSFyd9cJMN/PunGJRzUNaRlMoU6EWPS0JP+dFL"
        b"YNxaBv7WEkS3IvQfYW4BWdqeIMOpa17pljSDdQwQBugBU+TcsUgGxblOFI1Y53EYZw5ssq9JB4ugm0xsaVPgMLIYhDbG9FnMr6nc"
        b"s3TmEWO6THeK3aYnuOJr6Y4liYHwF0lOkT6SXq8UqJhiSmWPABcIaOG3bkkuwKAHODD7NEBixgrXXWROw3Qq/tgJ004MGUNYM4HG"
        b"CRXLIGQVUGMWBR2EaYAI/A+GsYsJd1U4IwmW0e7fDoajsXH007hvvOueHZ+/fYsmGsZv6kJbgAkdOhOKucmUgyysGpz3qUqGAl4f"
        b"MO2zlPWRjBXnrJLkoQlGUBRenv14dv7hTJfCg1jNX6QSWSh4KJukC20hxb8udYGQoqwn8kmwHvT9BZdjRY1wMNYt/nSgSqIEfkMj"
        b"ShdRbD2Jv1LWhgejVVMEa9aUQaL0OdY3edFeq9mpxya9TOLFzliKmHEZd4lHLyMWRJCr3hz5QL43JVJFkwpjZQlfsxSxv5JfLcAi"
        b"O+QOWJqGIcKlcy5YNxgtsOiudnzeuzztn427w5+M0WWv1x+NzofG+44B7qXROz/uGz90x6q6tia2mLTmreFut3emVmebW5a5PbGm"
        b"ze32ZGLut60DPrEmO9PdndbBvnmwv2fv701ak4Ntq73L203zYM882N6NBw58oDJwkQLzNjmsWrOaOzv7Lb7fmjYndtve3bcmrebB"
        b"/nZr17anHXNnYu0etDowacu2rW1796DTbh5Y23u7E6s12dtVB3JqtgN2f2Rdp4betfcOWvt7++bu9h5cgiV3Jge7Owe2ZTf392CF"
        b"29v2dLsDk/Nmp727vb8/2TmA3U06O2azzffU0FRw4xpYxJAuizYD6xoIlwzFlQ7OopNRTb8EBg/1qTkDIwN8Gd/mOnUT0OibHja4"
        b"CbrtW6Eo3Aj1+PMWA5DhWrvZ3tVaTR1/tprNnTEYTp1m59+6qlLR57ctzc76Xmz8hXX1HXQtZUlr6YXrP4AIuzXv3tMHL4VYHorS"
        b"jl/DlTryGLCTA8s6sM3WAZ+2m/sTDmib7m/z5u6eOTU71qRjNvftXd4Bopk2+aS53ZnuTNvT3RbQS3tv10qPCvxvO/T5z9pFdzRa"
        b"PqTqi9Jc0BrJ6thxfzT44cw4Pzv5qbZWVRuifl09HfetI0Qt+l7ImHbabd45mNrtCQdmam43d7dtu73XPtjZnlr2XtvmZnv7YAKE"
        b"D0S+bwInWdO91j78utPab+3nIGPYfz8Y9TPRIS1hVBdxjZASbkmMWEQh/GAdR2iOG8ivvihAeBlc8WY1RA3Pz8faRXcw1I4Ho4vz"
        b"0QA/MVOADwvAPrWmO52dvf3tKUgVPt0/2Ot0Ovu70+1ts9lqT9qAq5Y1sYFX9uGObQLn7HPb5tut1s5Sqfa179BBhRpVLoCx/zv3"
        b"EtAKRxD9JJRNdRZSYbVQIfA7Vvxlf5pGIIu0Q9oUVpoL9aBK1sWGEUwHFotNNo+w+E0H4xhgw5EVoCeBcpkxVaCupcrCw7vZBKtp"
        b"NFEt7IR4CBYUflIennyuSXwrp0bxyCMRKRnBZo+PkiMoMU2mlBwb/XR6dH4y6IFhdkIfBGIoK0SNs4gdMIyb6TN0IGDVE54acDUB"
        b"T0CSuv7NG5X1PFShsuRSMob6GLr03UhiiuXvNHVyWI8wu4Yxso+DYyzT1P6RxMpazdYhi5rfv4mS7ynV5GxZQ7bkkK2iIdswZBuG"
        b"3EaSiVrwW7u2ehKBdgoGl0q4yVQMmSuUnonDhjgu2bJomggTUAQQh9ImS48tzyaB1QcvEq66YINcjI0P7/qYL/zX5WDYR9SNjG5v"
        b"fAkm2U8G8nL/rJam2TWyCDjYwLakC/DUxx/vRRaboQ3zSq1mMWKQIddfBMDSu22d6JkMv+pgAqfD6I0/ylPTleCz8FSWCeYNHwgb"
        b"uT0iEcpjfXHsN0hm9bikkSSIHoehyYqmEG+lXeJ0xofB+B1tVXzbi3Ycb/pQpPEMIJDzHhisBmzLeAuWLLx1BjZtMTREXq8nUHws"
        b"Kkre4urOg55wXcSJyIv1I3T3g1jWXof9t5egU+OzMsqMFzI40T3kBGszEz9Ll5iWNenKy9OnBvryS50Ha+gVG8JZVBWhKwfyyC1Y"
        b"+q6aOl2XOpAf72E0BgY/BbZEHh2c/bB25+j88uwYMNY/HlG4IXngffdkcNzFeIgBAAPP4wL8reQ2Ilm2ATgb9z+OUyP/60S+0cWh"
        b"BSEsj/128LF/bAAV/Diih4b90fnlsJcYK7UkGpM9QrxX47Q7/DG95fSijN7J+Sh9E7T/WHLgqH/y1lCbPD9LngEbtn96dIJTD05P"
        b"L8fdo5M+ed6p3Y/7w9PBWffEANcY1vDDZXd4PFpaXTqEk974qhuf3PswHIyXUPT25HL0bukKbmcZi/0hcpSaLw0FJNRjQy00uSMd"
        b"+uQO3fik7IrYfjCU/YR0vxqTEiEZyjVSuC8V+8JwkK5UiozSLDxiJm4rm4QeimM3yk2bmV+MlOOPRzKVbyhTlYYMKKzwRG7ybpm7"
        b"VGzFSH0LAGNkhojU3K2MKgMoBsaijOW4VyILyAe/uAR66Rk9kGxH3d6Paj8EJYrOyJgKvqbg3z06H47hbQMUwRBxj0Od9ZDQCavU"
        b"VuPrq6+v/h8aFyllqtQAAA=="
    )
)


def test_measurement_config_cannot_be_foreign_map_or_removed_by_public_route(observation_v3_unit_case):
    case = observation_v3_unit_case
    changed = replace(case.inputs, measurement_configuration={"UnitOnly": True})
    with pytest.raises(runtime.NativeObservationError, match="designation/config"):
        changed.load(case.until)
    # Protected-origin dispatch is still frozen raw proof, independent of public tags.
    engine = _parse_observer_unit(case)
    object.__setattr__(engine, "route_probe", {"schema": "provider-native-engine-rest-observation.v1"})
    with pytest.raises(runtime.NativeObservationError, match="mutated after capture"):
        runtime._validate_operation_proof(engine, deadline=case.until)


@pytest.mark.parametrize("mutation", ["missing-jar", "instrument-without-config", "wrong-admission-jar"])
def test_protected_measurement_designation_cannot_downgrade_before_request(
    observation_v3_unit_case, mutation
):
    """Counterproof only: inert class-directory bytes never execute a JVM."""
    import io
    import zipfile

    case = observation_v3_unit_case
    if mutation == "missing-jar":
        changed = replace(case.inputs, installed_support_jar=None)
        message = "support JAR origin required"
    else:
        path = case.inputs.installed_support_jar.path
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as jar:
            jar.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n\n")
            jar.writestr(
                "br/com/maezo/workload/ProviderNativeIndependentMeasurement.class", b"UnitOnly-inert"
            )
        raw = payload.getvalue()
        path.write_bytes(raw)
        pin = runtime.NativeObservationFile(path, hashlib.sha256(raw).hexdigest())
        if mutation == "instrument-without-config":
            case.admission["support_jar_sha256"] = pin.sha256
            case.inputs.admission.path.write_bytes(case.encode(case.admission))
            admission = runtime.NativeObservationFile(
                case.inputs.admission.path, hashlib.sha256(case.encode(case.admission)).hexdigest()
            )
            changed = replace(case.inputs, admission=admission, installed_support_jar=pin)
            message = "designation/config"
        else:
            changed = replace(case.inputs, installed_support_jar=pin)
            message = "differs from admission"
    with pytest.raises(runtime.NativeObservationError, match=message):
        runtime.native_observation_request(changed, deadline=case.until)
