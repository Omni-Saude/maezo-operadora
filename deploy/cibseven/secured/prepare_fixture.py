#!/usr/bin/env python3
"""ROOT-only disposable fixture preparation. No Docker lifecycle, no production defaults.

prepare generates secrets only in the established private root. seed is a separate explicit
ROOT command against the loopback-only legacy bootstrap fixture before secured cutover.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from maezo.gateway.engine_contracts import EngineCapabilityProfile, EngineIdentity, EngineTarget
from maezo.gateway.engine_contracts import EngineOperation as Operation
from maezo.gateway.engine_schemas import (
    registered_schema,
    schema_by_id,
    start_read_schema,
    worker_lifecycle_schema,
)


def dump(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(args) -> None:
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.secured_image):
        raise ValueError("actual immutable secured image digest required")
    if args.bootstrap_image != "sha256:9f0ba266d1c3f5712da455560883340451bb59c30bae0d10abc4f2d5f0116c5b":
        raise ValueError("this fixture requires the qualified pinned bootstrap image")
    spec = importlib.util.spec_from_file_location(
        "human_fixture", args.checkout / "deploy/cibseven/package-test/prepare.py"
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.prepare(
        args.checkout,
        args.sha,
        args.base_files / "conf/server.xml",
        args.private_root,
        args.evidence_root,
        args.output,
        args.bootstrap_image,
        relay_synthetic=True,
    )
    root = args.output
    old_umask = os.umask(0o077)
    try:
        trust = json.loads((root / "trust.json").read_text())
        tenant = trust["tenant"]
        ca = x509.load_pem_x509_certificate((root / "ca.crt").read_bytes())
        ca_key = serialization.load_pem_private_key((root / "ca.key").read_bytes(), password=None)
        now = datetime.now(UTC)
        peers = []
        purposes = [
            ("command", "human-relay", "package-command-workload"),
            ("authority", "human-relay", "package-authority-workload"),
            ("agent", "nonhuman", "helena"),
            ("worker", "nonhuman", "worker_runtime"),
            ("observer", "observer", "observer"),
            ("bridge", "nonhuman", "consent_revocation_bridge"),
            ("bootstrap", "bootstrap", "bootstrap"),
            ("deployment", "deployment", "deployment"),
        ]
        for name, purpose, workload in purposes:
            key_path = root / f"{name}.key"
            key = (
                serialization.load_pem_private_key(key_path.read_bytes(), password=None)
                if key_path.exists()
                else ec.generate_private_key(ec.SECP256R1())
            )
            san = f"spiffe://maezo.test/{tenant}/{workload}"
            cert = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
                .issuer_name(ca.subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(seconds=10))
                .not_valid_after(ca.not_valid_after_utc)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), True)
                .add_extension(
                    x509.KeyUsage(True, False, False, False, False, False, False, False, False), True
                )
                .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(san)]), False)
                .sign(ca_key, hashes.SHA256())
            )
            key_path.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            (root / f"{name}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            peers.append(
                {
                    "certificate_sha256": hashlib.sha256(
                        cert.public_bytes(serialization.Encoding.DER)
                    ).hexdigest(),
                    "spki_sha256": hashlib.sha256(
                        key.public_key().public_bytes(
                            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                        )
                    ).hexdigest(),
                    "issuer_dn": cert.issuer.rfc4514_string(),
                    "subject_dn": cert.subject.rfc4514_string(),
                    "uri_san": san,
                    "purpose": purpose,
                    "engine_user": f"d7-{name}",
                    "identity": {
                        "tenant": tenant,
                        "environment": "isolated-test",
                        "workload": workload,
                        "workload_version": "fixture-v1",
                        "issuer": cert.issuer.rfc4514_string(),
                        "subject": san,
                        "origin": "verified_mtls",
                    },
                    "not_before": int(now.timestamp()),
                    "expires_at": int(ca.not_valid_after_utc.timestamp()),
                    "capabilities": [],
                }
            )
        (root / "compose.json").rename(root / "bootstrap-compose.json")
        tree = ET.parse(root / "server.xml")
        for service in tree.findall(".//Service"):
            for connector in list(service.findall("Connector")):
                if connector.get("SSLEnabled") != "true":
                    service.remove(connector)
        for host in tree.findall(".//Host"):
            host.set("autoDeploy", "false")
        tree.write(root / "secured-server.xml", encoding="utf-8", xml_declaration=True)
        files = []
        for source, remote in [
            (root / "secured-server.xml", "/camunda/conf/server.xml"),
            (args.checkout / "deploy/cibseven/secured/descriptors/global-web.xml", "/camunda/conf/web.xml"),
            (
                args.checkout / "deploy/cibseven/secured/descriptors/bpm-platform.xml",
                "/camunda/conf/bpm-platform.xml",
            ),
            (
                args.checkout / "deploy/cibseven/secured/descriptors/engine-rest-web.xml",
                "/camunda/webapps/engine-rest/WEB-INF/web.xml",
            ),
            (
                args.checkout / "deploy/cibseven/human-webapp/WEB-INF/web.xml",
                "/camunda/webapps/maezo-human/WEB-INF/web.xml",
            ),
            (
                args.checkout / "deploy/cibseven/secured/descriptors/camunda-web.xml",
                "/camunda/webapps/camunda/WEB-INF/web.xml",
            ),
        ]:
            files.append({"path": remote, "sha256": sha(source)})
        policy = {
            "protocol": "maezo.engine-boundary.v1",
            "tenant": tenant,
            "environment": "isolated-test",
            "engine_name": "default",
            "not_before": int(now.timestamp()),
            "expires_at": int(ca.not_valid_after_utc.timestamp()),
            "roots": [{"path": "/run/maezo/ca.crt", "sha256": sha(root / "ca.crt")}],
            "files": files,
            "listener_port": 8443,
            "max_tasks": 20,
            "max_lock_millis": 60000,
            "max_poll_millis": 10000,
            "peers": peers,
        }
        # A second real leaf has the same immutable workload authority; only its key changes.
        agent = next(p for p in peers if p["engine_user"] == "d7-agent")
        variants = {}
        for name in ("agent-next", "unknown-leaf", "wrong-san", "expired", "no-eku"):
            key = ec.generate_private_key(ec.SECP256R1())
            builder = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agent")]))
                .issuer_name(ca.subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(
                    now - timedelta(days=2) if name == "expired" else now - timedelta(seconds=10)
                )
                .not_valid_after(now - timedelta(days=1) if name == "expired" else ca.not_valid_after_utc)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
                .add_extension(
                    x509.KeyUsage(True, False, False, False, False, False, False, False, False), True
                )
                .add_extension(
                    x509.SubjectAlternativeName(
                        [
                            x509.UniformResourceIdentifier(
                                agent["uri_san"] + "-wrong" if name == "wrong-san" else agent["uri_san"]
                            )
                        ]
                    ),
                    False,
                )
            )
            if name != "no-eku":
                builder = builder.add_extension(
                    x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), True
                )
            cert = builder.sign(ca_key, hashes.SHA256())
            (root / f"{name}.key").write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            (root / f"{name}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            variants[name] = {
                "certificate_sha256": hashlib.sha256(
                    cert.public_bytes(serialization.Encoding.DER)
                ).hexdigest(),
                "spki_sha256": hashlib.sha256(
                    key.public_key().public_bytes(
                        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                ).hexdigest(),
            }
        dump(root / "certificate-variants.json", variants)
        dump(root / "boundary-template.json", policy)
        dump(
            root / "d7-fixture.json",
            {
                "fixture_only": True,
                "source_sha": args.sha,
                "secured_image": args.secured_image,
                "bootstrap_image": args.bootstrap_image,
                "tenant": tenant,
                "checkout": str(args.checkout),
                "base_files": str(args.base_files),
            },
        )
        print("Prepared private D7 fixture; policy binding requires explicit seed phase.")
    finally:
        os.umask(old_umask)


def seed(root: Path) -> None:
    metadata = json.loads((root / "d7-fixture.json").read_text())
    if metadata.get("fixture_only") is not True or root.resolve() != root or root.stat().st_mode & 0o077:
        raise ValueError("explicit canonical mode-0700 disposable fixture required")
    if (root / "boundary.json").exists():
        raise ValueError("seed is one-shot; preserve existing fixture instead of redeploying")
    tenant = metadata["tenant"]
    context = ssl.create_default_context(cafile=root / "ca.crt")
    context.load_cert_chain(root / "command.crt", root / "command.key")
    with httpx.Client(verify=context, trust_env=False, follow_redirects=False, timeout=10) as probe:
        ready = probe.post("https://localhost:18443/maezo-human/v1/commands", json={})
        assert ready.status_code == 400 and ready.json() == {"error": "INVALID_COMMAND"}, (
            "bootstrap must be the qualified original D5 human image; native vendor image is insufficient"
        )
    # Deliberately local, setup-only existing bootstrap engine. Never used by the secured workload transport.
    with httpx.Client(
        base_url="http://127.0.0.1:18080/engine-rest", trust_env=False, follow_redirects=False, timeout=10
    ) as client:

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code not in (200, 201, 204):
                raise ValueError("isolated engine setup request refused")
            return response.json() if response.content else None

        request("GET", "/version")
        definitions = {}
        for family, topic in [
            ("CONTAS", "operadora.contas.calculate_impact"),
            ("ESCALATION", "operadora.escalation.notify_team"),
        ]:
            key = f"SP-OP-{family}-001"
            error = (
                '<error id="failure" errorCode="ERR_ESC_NOTIFY_FAILED"/>' if family == "ESCALATION" else ""
            )
            handler = (
                (
                    '<boundaryEvent id="failureBoundary" attachedToRef="external">'
                    '<errorEventDefinition errorRef="failure"/></boundaryEvent>'
                    '<sequenceFlow id="errorFlow" sourceRef="failureBoundary" targetRef="human"/>'
                )
                if error
                else ""
            )
            xml = (
                '<definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" '
                'xmlns:camunda="http://camunda.org/schema/1.0/bpmn" '
                f'targetNamespace="urn:maezo:isolated-d7-fixture">{error}'
                f'<process id="{key}" isExecutable="true" camunda:historyTimeToLive="1">'
                '<startEvent id="start"/>'
                '<sequenceFlow id="first" sourceRef="start" targetRef="external"/>'
                f'<serviceTask id="external" camunda:type="external" camunda:topic="{topic}"/>'
                '<sequenceFlow id="second" sourceRef="external" targetRef="human"/>'
                f'<userTask id="human"/>{handler}</process></definitions>'
            )
            request(
                "POST",
                "/deployment/create",
                data={"deployment-name": "d7-isolated-fixture", "tenant-id": tenant},
                files={"data": (f"{key}.bpmn", xml.encode(), "application/octet-stream")},
            )
            found = request("GET", "/process-definition", params={"key": key, "tenantIdIn": tenant})
            if len(found) != 1 or found[0]["tenantId"] != tenant:
                raise ValueError("fixture definition identity ambiguous")
            definitions[key] = found[0]
        policy = json.loads((root / "boundary-template.json").read_text())
        for peer in policy["peers"]:
            workload = peer["identity"]["workload"]
            rows = []
            if workload == "helena":
                start = schema_by_id("helena.escalation.start.v1")
                rows = [
                    start,
                    start_read_schema(start, Operation.READ_ACTIVE),
                    start_read_schema(start, Operation.READ_HISTORY),
                ]
            elif workload == "worker_runtime":
                for schema_id in (
                    "contas.calculate_impact.complete.v1",
                    "escalation.notify_team.bpmn_error.v1",
                ):
                    base = schema_by_id(schema_id)
                    rows.append(base)
                    rows.extend(
                        worker_lifecycle_schema(base, operation)
                        for operation in (
                            Operation.FETCH_LOCK,
                            Operation.FAILURE,
                            Operation.EXTEND_LOCK,
                            Operation.UNLOCK,
                        )
                    )
            for row in rows:
                assert registered_schema(row)
                target = definitions[row.process_key]
                identity = dict(peer["identity"])
                identity.pop("origin")
                profile = EngineCapabilityProfile(
                    EngineIdentity(**identity),
                    EngineTarget(row.process_key, target["version"], target["id"], row.topic, row.message),
                    row,
                    "fixture-worker" if row.topic else "",
                )
                peer["capabilities"].append(
                    {
                        "document": profile.document(),
                        "digest": profile.digest,
                        "source_kind": "",
                        "source_worker_id": "",
                        "attestations": [],
                    }
                )
            # Narrow actual native grants. Workload operations still pass the Java typed command fence.
            keys = {row.process_key for row in rows}
            for key in keys:
                permissions = (
                    ["READ", "READ_INSTANCE", "READ_HISTORY", "CREATE_INSTANCE"]
                    if workload == "helena"
                    else ["READ", "READ_INSTANCE", "UPDATE_INSTANCE"]
                )
                request(
                    "POST",
                    "/authorization/create",
                    json={
                        "type": 1,
                        "userId": peer["engine_user"],
                        "resourceType": 6,
                        "resourceId": key,
                        "permissions": permissions,
                    },
                )
            if workload == "helena":
                request(
                    "POST",
                    "/authorization/create",
                    json={
                        "type": 1,
                        "userId": peer["engine_user"],
                        "resourceType": 8,
                        "resourceId": "*",
                        "permissions": ["CREATE"],
                    },
                )
        # Separate real resources for refusal and legitimate lifecycle controls. Raw attempts
        # share an immutable target only while every predecessor's no-effect assertion passed.
        external_instance = request(
            "POST",
            f"/process-definition/{definitions['SP-OP-CONTAS-001']['id']}/start",
            json={
                "businessKey": "d7-raw-existing-external",
                "variables": {"fixture_value": {"value": "synthetic", "type": "String"}},
            },
        )
        external = request("GET", "/external-task", params={"processInstanceId": external_instance["id"]})
        assert len(external) == 1
        request(
            "POST",
            f"/external-task/{external[0]['id']}/lock",
            json={"workerId": "fixture-worker", "lockDuration": 86400000},
        )
        for n in range(3):
            request(
                "POST",
                f"/process-definition/{definitions['SP-OP-CONTAS-001']['id']}/start",
                json={"businessKey": f"d7-lifecycle-{n}", "variables": {}},
            )
        resources = {"external": external[0], "external_instance": external_instance}
    # Existing D5/D6 fixture performs real migrations, signed claim and receipt, then
    # leaves a legitimate release pending. No receipt/table is fabricated for D7.
    resources.update(asyncio.run(seed_relay(root)))
    with httpx.Client(
        base_url="http://127.0.0.1:18080/engine-rest", trust_env=False, follow_redirects=False, timeout=10
    ) as connection:
        human = resources["human"]
        for label, path in (
            ("human", f"/task/{human['id']}"),
            ("human_definition", f"/process-definition/{human['processDefinitionId']}"),
            ("external", f"/external-task/{external[0]['id']}"),
        ):
            r = connection.get(path)
            assert r.status_code == 200
            resources[label] = r.json()
            assert resources[label]["tenantId"] == tenant
        assert resources["human_definition"]["key"] == "MZO-HUMAN-SYNTHETIC"
    dump(root / "resources.json", resources)
    dump(root / "definitions.json", definitions)
    dump(root / "boundary.json", policy)
    compose = json.loads((root / "bootstrap-compose.json").read_text())
    engine = compose["services"]["engine"]
    engine["image"] = metadata["secured_image"]
    engine["ports"] = ["127.0.0.1:18443:8443"]
    engine["environment"].update(
        {
            "MAEZO_ENGINE_BOUNDARY_FILE": "/run/maezo/boundary.json",
            "MAEZO_ENGINE_BOUNDARY_SHA256": sha(root / "boundary.json"),
            "CATALINA_BASE": "/camunda",
        }
    )
    engine["volumes"] = [v for v in engine["volumes"] if v.get("target") != "/camunda/conf/server.xml"]
    for name, target in [
        ("secured-server.xml", "/camunda/conf/server.xml"),
        ("boundary.json", "/run/maezo/boundary.json"),
        ("ca.crt", "/run/maezo/ca.crt"),
    ]:
        engine["volumes"].append(
            {
                "type": "bind",
                "source": str(root / name),
                "target": target,
                "read_only": True,
                "bind": {"create_host_path": False},
            }
        )
    dump(root / "secured-compose.json", compose)
    print("Seeded isolated setup and bound exact policy; stop bootstrap engine before secured restart.")


async def seed_relay(root: Path) -> dict:
    from tests.support.human_relay_live import LiveRelayFixture, RelayConfig, relay

    previous = os.environ.get("MAEZO_HUMAN_RELAY_PRIVATE_DIR")
    os.environ["MAEZO_HUMAN_RELAY_PRIVATE_DIR"] = str(root)
    try:
        config = RelayConfig.load()
    finally:
        if previous is None:
            os.environ.pop("MAEZO_HUMAN_RELAY_PRIVATE_DIR", None)
        else:
            os.environ["MAEZO_HUMAN_RELAY_PRIVATE_DIR"] = previous
    artifacts = root / "synthetic-relay-bootstrap"
    artifacts.mkdir(mode=0o700)
    live = LiveRelayFixture(config, artifacts)
    try:
        await live.open()
        claim = await live.command()
        await live.persist(claim)
        assert await relay(live.store, live.transport()).run_once()
        await live.assert_result(claim)
        assert len(await live.engine_receipts()) == 1
        # All setup mutations precede the immutable pending command revision.
        for path in (
            f"/task/{live.task_id}/variables/fixture_value",
            f"/task/{live.task_id}/localVariables/fixture_local",
        ):
            r = await live.rest.put(path, json={"value": "synthetic", "type": "String"})
            assert r.status_code == 204
        r = await live.rest.post(
            f"/task/{live.task_id}/localVariables/fixture_binary/data",
            files={"data": ("synthetic.bin", b"nonclinical-fixture", "application/octet-stream")},
        )
        assert r.status_code == 204
        r = await live.rest.post(
            f"/task/{live.task_id}/identity-links",
            json={"groupId": "synthetic-reviewers", "type": "candidate"},
        )
        assert r.status_code == 204
        pending = await live.command("release")
        await live.persist(pending)
        delivery = await live.delivery(pending)
        assert delivery["status"] == "pending" and delivery["engine_receipt"] is None
        assert len(await live.rows("audit_chain")) == 3
        assert len(await live.engine_receipts()) == 1
        dump(root / "pending-release.json", json.loads(pending.canonical))
        return {
            "human": {"id": live.task_id, "processDefinitionId": live.process_id},
            "human_instance": live.instance_id,
            "human_execution": (await live.task())["execution_id_"],
            "tenant_schema": live.scope.tenant,
            "decision_receipt_fabricated": False,
            "receipt_command_id": claim.command_id,
            "pending_command_id": pending.command_id,
        }
    finally:
        # Keep only this owned disposable fixture's durable rows for secure cutover.
        # ROOT removes its Compose project/data at final teardown; no schema adoption.
        if hasattr(live, "rest"):
            await live.rest.aclose()
        if hasattr(live, "pool"):
            await live.pool.close()
        if hasattr(live, "admin"):
            await live.admin.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("prepare")
    for name in ("checkout", "base-files", "private-root", "evidence-root", "output"):
        p.add_argument("--" + name, type=Path, required=True)
    for name in ("sha", "bootstrap-image", "secured-image"):
        p.add_argument("--" + name, required=True)
    s = commands.add_parser("seed")
    s.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args)
    else:
        seed(args.fixture)
