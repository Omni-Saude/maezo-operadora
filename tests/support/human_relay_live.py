"""Explicit disposable D6/CIB fixture; no service starts, engine mocks or production adapters.

ROOT owns the pinned image and PostgreSQL. Raw REST is used only to deploy/start
the existing nonclinical BPMN and observe it; authority is published through the
real signed/mTLS plugin endpoint. D4 session/admission DTOs are not exercised.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import create_async_engine

from maezo.gateway.audit_postgres import _row_to_record, verify_chain
from maezo.gateway.human.credentials import DedicatedHumanCredential, HumanCommandCredentialPartition
from maezo.gateway.human.models import Scope
from maezo.gateway.human.outbox import PostgresHumanOutbox
from maezo.gateway.human.projection import intent_reference, verify_engine_receipt
from maezo.gateway.human.relay import HumanCommandRelay, RelaySettings
from maezo.gateway.human.transport import (
    EngineUnavailableError,
    HumanEngineTransport,
    HumanTLSIdentity,
    MTLSHumanEngineTransport,
    PartitionedEd25519Signer,
)
from maezo.portal.contracts.models import HumanPrincipal, MembershipBinding
from maezo.portal.engine.profile import HumanCommand, SigningContext, canonicalize, strict_loads

ROOT = Path(__file__).resolve().parents[2]
BPMN = ROOT / "src/maezo/portal/engine/java/src/test/resources/synthetic-human.bpmn"
FORM = "maezo.synthetic-ack.v1"
GROUP = "synthetic-reviewers"
CRASH_EXIT = 73


def write_json(path: Path, value) -> None:
    """Only synthetic commands/observations, never keys/configuration, reach evidence."""
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def private_file(path: Path) -> Path:
    assert path.is_absolute() and path.resolve(strict=True) == path
    info = path.stat()
    assert stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600
    assert info.st_uid == os.geteuid()
    return path


@dataclass(repr=False)
class RelayConfig:
    directory: Path
    data: dict = field(repr=False)
    trust: dict = field(repr=False)

    @classmethod
    def load(cls) -> RelayConfig:
        raw = os.environ.get("MAEZO_HUMAN_RELAY_PRIVATE_DIR")
        assert raw, "ROOT must explicitly provide MAEZO_HUMAN_RELAY_PRIVATE_DIR; no skip/default"
        directory = Path(raw)
        assert directory.is_absolute() and directory.resolve(strict=True) == directory
        assert not directory.is_relative_to(ROOT) and not ROOT.is_relative_to(directory)
        info = directory.stat()
        assert stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.geteuid()
        data = json.loads(private_file(directory / "relay-fixture.json").read_text())
        trust = json.loads(private_file(directory / "trust.json").read_text())
        public = json.loads(private_file(directory / "public-receipt.json").read_text())
        assert data["schema"] == "human-relay-fixture.v1" and data["synthetic_opt_in"] is True
        assert trust["enable_synthetic_fixture"] is True and public["relay_synthetic"] is True
        assert re.fullmatch(r"relay_[0-9a-f]{24}", data["tenant"])
        assert data["tenant"] == trust["tenant"]
        assert len(data["tenant"] + "_alembic_version") <= 63
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        assert data["source_sha"] == public["source_sha"] == sha, "fixture must match executed source"
        for key, scheme in (("rest_url", "http"), ("human_url", "https")):
            url = urlsplit(data[key])
            assert url.scheme == scheme and url.hostname in {"localhost", "127.0.0.1"}
            assert url.port and not (url.username or url.password or url.query or url.fragment)
        database = data["database"]
        assert set(database) == {"host", "port", "user", "database", "password_file"}
        assert database["host"] == "127.0.0.1" and database["port"] == 15433
        assert database["user"] == database["database"] == "maezo"
        assert Path(database["password_file"]) == directory / "postgres-password"
        private_file(Path(database["password_file"]))
        return cls(directory, data, trust)

    @property
    def scope(self) -> Scope:
        return Scope(
            tenant=self.data["tenant"], environment="disposable-test", workload_ref=self.key["workload"]
        )

    @property
    def key(self) -> dict:
        return next(k for k in self.trust["keys"] if k["purpose"] == "human-command")

    def db_parameters(self) -> dict:
        values = self.data["database"].copy()
        values["password"] = private_file(Path(values.pop("password_file"))).read_text()
        return values

    def identity(self, *, scope: Scope | None = None, peer: str = "command") -> HumanTLSIdentity:
        assert peer in {"command", "authority", "untrusted-client"}
        return HumanTLSIdentity(
            scope=scope or self.scope,
            ca_file=private_file(self.directory / "ca.crt"),
            certificate_file=private_file(self.directory / f"{peer}.crt"),
            private_key_file=private_file(self.directory / f"{peer}.key"),
        )

    def signing_key(self, purpose: str) -> Ed25519PrivateKey:
        assert purpose in {"command", "authority"}
        key = serialization.load_pem_private_key(
            private_file(self.directory / f"{purpose}-signing.key").read_bytes(), password=None
        )
        assert isinstance(key, Ed25519PrivateKey)
        expected = next(k for k in self.trust["keys"] if k["purpose"] == "human-" + purpose)
        public = key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        assert base64.b64encode(public).decode() == expected["public_key_spki_base64"]
        return key

    def transport(self, *, scope: Scope | None = None, peer: str = "command") -> MTLSHumanEngineTransport:
        scope = scope or self.scope
        credential = DedicatedHumanCredential(scope=scope, key_id=self.key["id"], purpose="human-command")
        partition = HumanCommandCredentialPartition(scope)
        partition.install(credential)
        signer = PartitionedEd25519Signer(
            partition=partition,
            credential=credential,
            context=SigningContext(
                tenant=scope.tenant,
                workload_ref=scope.workload_ref,
                key_id=credential.key_id,
                audience=self.trust["audience"],
                max_lifetime_seconds=int(self.trust["max_lifetime_seconds"]),
            ),
            private_key=self.signing_key("command"),
            valid_from=datetime.fromtimestamp(int(self.key["not_before"]), UTC),
            valid_until=datetime.fromtimestamp(int(self.key["not_after"]), UTC),
            envelope_seconds=30,
        )
        return MTLSHumanEngineTransport(
            scope=scope,
            endpoint=self.data["human_url"],
            identity=self.identity(scope=scope, peer=peer),
            signer=signer,
            timeout_seconds=10,
        )

    async def pool(self):
        return await asyncpg.create_pool(**self.db_parameters(), min_size=1, max_size=4)


class ObservedTransport(HumanEngineTransport):
    """Observe a real transport; faults happen only AFTER the actual response returns.

    No response/receipt is fabricated. Barriers coordinate live relay races. Raw
    server receipts are retained before any deliberate response-loss injection.
    """

    def __init__(self, real: MTLSHumanEngineTransport):
        assert isinstance(real, MTLSHumanEngineTransport)
        self.scope = real.scope
        self.real = real
        self.events: list[str] = []
        self.receipts: list[bytes] = []
        self.commands: list[bytes] = []
        self.drop_response = False
        self.pause_after_missing = False
        self.pause_after_commit = False
        self.reached = asyncio.Event()
        self.resume = asyncio.Event()

    async def receipt(self, command: HumanCommand) -> bytes | None:
        self.events.append("GET.begin")
        result = await self.real.receipt(command)
        self.events.append("GET.missing" if result is None else "GET.committed")
        if result is not None:
            self.receipts.append(result)
        if result is None and self.pause_after_missing:
            self.reached.set()
            await asyncio.wait_for(self.resume.wait(), 30)
        return result

    async def dispatch(self, command: HumanCommand) -> bytes:
        self.events.append("POST.begin")
        self.commands.append(command.canonical)
        result = await self.real.dispatch(command)
        self.receipts.append(result)
        self.events.append("POST.committed")
        if self.pause_after_commit:
            self.reached.set()
            await asyncio.wait_for(self.resume.wait(), 30)
        if self.drop_response:
            self.events.append("response.dropped.after.actual.commit")
            raise EngineUnavailableError()
        return result

    def save(self, directory: Path) -> None:
        write_json(directory / "transport-events.json", {"pid": os.getpid(), "events": self.events})
        for i, receipt in enumerate(self.receipts):
            (directory / f"engine-receipt-{i}.json").write_bytes(receipt)
        for i, command in enumerate(self.commands):
            (directory / f"dispatched-command-{i}.json").write_bytes(command)


def relay(store: PostgresHumanOutbox, transport: HumanEngineTransport, *, lease_seconds: int = 30):
    return HumanCommandRelay(
        outbox=store,
        transport=transport,
        settings=RelaySettings(lease_seconds=lease_seconds, retry_seconds=1, poll_seconds=1),
    )


class LiveRelayFixture:
    def __init__(self, config: RelayConfig, artifacts: Path):
        self.config, self.artifacts = config, artifacts
        self.scope = config.scope
        self.principal_ref = "synthetic-human-" + uuid4().hex
        self.subject = "synthetic-subject-" + uuid4().hex
        self.issuer = "https://synthetic-identity.example.invalid"
        self.valid_until = datetime.now(UTC) + timedelta(minutes=20)
        self.deployment_id: str | None = None
        self.schema_owned = False
        self.observers: list[ObservedTransport] = []

    async def open(self) -> None:
        self.admin = await asyncpg.connect(**self.config.db_parameters())
        assert await self.admin.fetchval("SELECT current_schema()") == "public"
        assert await self.admin.fetchval("SELECT count(*) FROM public.mzo_human_tenant") == 1
        assert await self.admin.fetchval("SELECT tenant_ FROM public.mzo_human_tenant") == self.scope.tenant
        # Never adopt/drop a pre-existing schema. This suite is deliberately serial.
        await self.admin.execute(f'CREATE SCHEMA "{self.scope.tenant}"')
        self.schema_owned = True
        parameters = self.config.db_parameters()
        parameters["username"] = parameters.pop("user")
        url = URL.create("postgresql+asyncpg", **parameters)
        engine = create_async_engine(
            url,
            echo=False,
            hide_parameters=True,
            connect_args={"server_settings": {"search_path": self.scope.tenant}},
        )
        try:
            async with engine.begin() as connection:

                def migrate(sync):
                    with Operations.context(MigrationContext.configure(sync)):
                        for name in (
                            "0002_audit_chain",
                            "0005_audit_emit_dedup",
                            "0013_human_command_outbox",
                        ):
                            importlib.import_module("maezo.platform.migrations.versions." + name).upgrade()

                await connection.run_sync(migrate)
        finally:
            await engine.dispose()
        self.pool = await self.config.pool()
        self.store = PostgresHumanOutbox(scope=self.scope, pool=self.pool)
        self.rest = httpx.AsyncClient(
            base_url=self.config.data["rest_url"], timeout=20, trust_env=False, follow_redirects=False
        )
        deployed = await self.rest.post(
            "/deployment/create",
            data={"deployment-name": "synthetic-relay-" + uuid4().hex, "tenant-id": self.scope.tenant},
            files={"data": ("synthetic-human.bpmn", BPMN.read_bytes(), "application/octet-stream")},
        )
        assert deployed.status_code == 200, "actual CIB synthetic deployment failed"
        self.deployment_id = deployed.json()["id"]
        definitions = await self.rest.get("/process-definition", params={"deploymentId": self.deployment_id})
        assert definitions.status_code == 200 and len(definitions.json()) == 1
        definition = definitions.json()[0]
        self.process_id = definition["id"]
        assert definition["tenantId"] == self.scope.tenant
        resources = await self.rest.get(f"/deployment/{self.deployment_id}/resources")
        assert resources.status_code == 200
        resource = next(r for r in resources.json() if r["name"] == "synthetic-human.bpmn")
        model = await self.rest.get(f"/deployment/{self.deployment_id}/resources/{resource['id']}/data")
        assert model.status_code == 200 and model.content == BPMN.read_bytes()
        self.process_digest = hashlib.sha256(model.content).hexdigest()
        started = await self.rest.post(
            f"/process-definition/{self.process_id}/start", json={"businessKey": "synthetic-" + uuid4().hex}
        )
        assert started.status_code == 200
        self.instance_id = started.json()["id"]
        tasks = await self.rest.get("/task", params={"processInstanceId": self.instance_id})
        assert tasks.status_code == 200 and len(tasks.json()) == 1
        self.task_id = tasks.json()[0]["id"]
        await self.publish_principal()
        await self.publish_evidence()
        write_json(
            self.artifacts / "bootstrap.json",
            {
                "source_sha": self.config.data["source_sha"],
                "tenant": self.scope.tenant,
                "deployment_id": self.deployment_id,
                "process_id": self.process_id,
                "process_digest": self.process_digest,
                "task_id": self.task_id,
                "synthetic_only": True,
                "d4_admission_or_d7_enforcement": False,
            },
        )

    async def publish(self, operation: str, **fields) -> None:
        key = next(k for k in self.config.trust["keys"] if k["purpose"] == "human-authority")
        revision = await self.admin.fetchval(
            "SELECT rev_ FROM public.mzo_human_tenant WHERE tenant_=$1", self.scope.tenant
        )
        command = dict(
            schema="human-authority.v1",
            tenant=self.scope.tenant,
            workload_ref=key["workload"],
            operation=operation,
            expected_revision=str(revision),
            **fields,
        )
        now = int(datetime.now(UTC).timestamp())
        envelope = dict(
            schema="human-envelope.v1",
            purpose="human-authority",
            algorithm="Ed25519",
            audience=self.config.trust["audience"],
            issuer=key["workload"],
            tenant=self.scope.tenant,
            key_id=key["id"],
            issued_at=str(now),
            expires_at=str(now + 30),
            digest=hashlib.sha256(canonicalize(command)).hexdigest(),
            command=command,
        )
        signature = self.config.signing_key("authority").sign(canonicalize(envelope))
        envelope["signature"] = base64.urlsafe_b64encode(signature).rstrip(b"=").decode()
        async with httpx.AsyncClient(
            verify=self.config.identity(peer="authority").context(),
            timeout=10,
            trust_env=False,
            follow_redirects=False,
        ) as client:
            response = await client.post(
                self.config.data["human_url"] + "/v1/authority",
                content=canonicalize(envelope),
                headers={"Content-Type": "application/json"},
            )
        assert response.status_code == 200, "actual signed synthetic authority publication failed"
        assert strict_loads(response.content) == {
            "schema": "human-authority-receipt.v1",
            "tenant": self.scope.tenant,
            "revision": str(revision + 1),
            "digest": envelope["digest"],
        }
        assert (
            await self.admin.fetchval(
                "SELECT rev_ FROM public.mzo_human_tenant WHERE tenant_=$1", self.scope.tenant
            )
            == revision + 1
        )
        (self.artifacts / f"authority-receipt-{revision + 1}.json").write_bytes(response.content)

    async def publish_principal(self, *, active: bool = True) -> None:
        await self.publish(
            "principal",
            principal_ref=self.principal_ref,
            issuer=self.issuer,
            subject=self.subject,
            active=active,
            valid_until=str(int(self.valid_until.timestamp())),
            groups=[GROUP],
        )

    async def publish_evidence(self) -> None:
        reference = "synthetic-evidence-" + uuid4().hex
        await self.publish(
            "evidence",
            task_id=self.task_id,
            process_definition_id=self.process_id,
            evidence_ref=reference,
            evidence_digest=hashlib.sha256(reference.encode()).hexdigest(),
            valid_until=str(int(self.valid_until.timestamp())),
        )

    async def command(self, operation: str = "claim") -> HumanCommand:
        task = await self.task()
        principal = await self.admin.fetchrow(
            "SELECT * FROM public.mzo_human_principal WHERE tenant_=$1 AND principal_=$2",
            self.scope.tenant,
            self.principal_ref,
        )
        evidence = await self.admin.fetchrow(
            "SELECT * FROM public.mzo_human_evidence WHERE tenant_=$1 AND task_=$2",
            self.scope.tenant,
            self.task_id,
        )
        definition = await self.admin.fetchrow(
            "SELECT * FROM public.act_re_procdef WHERE id_=$1", task["proc_def_id_"]
        )
        authority = await self.admin.fetchval(
            "SELECT rev_ FROM public.mzo_human_tenant WHERE tenant_=$1", self.scope.tenant
        )
        assert principal["issuer_"] == self.issuer and principal["subject_"] == self.subject
        assert principal["active_"] is True and json.loads(principal["groups_"]) == [GROUP]
        assert evidence["process_"] == task["proc_def_id_"] == self.process_id
        assert definition["key_"] == "MZO-HUMAN-SYNTHETIC" and task["task_def_key_"] == "UT_Acknowledge"
        command_id = "synthetic-command-" + uuid4().hex
        result = HumanCommand(
            tenant=self.scope.tenant,
            task_id=self.task_id,
            command_id=command_id,
            principal_ref=self.principal_ref,
            principal_issuer=principal["issuer_"],
            principal_subject=principal["subject_"],
            workload_ref=self.scope.workload_ref,
            operation=operation,
            process_definition_id=self.process_id,
            process_definition_key=definition["key_"],
            process_definition_version=str(definition["version_"]),
            process_definition_digest=self.process_digest,
            task_definition_key=task["task_def_key_"],
            form_key=FORM,
            form_version="1",
            form_digest=hashlib.sha256(FORM.encode()).hexdigest(),
            task_revision=str(task["rev_"]),
            authority_revision=str(authority),
            membership_revision=str(principal["rev_"]),
            evidence_revision=str(evidence["rev_"]),
            evidence_ref=evidence["ref_"],
            evidence_digest=evidence["digest_"],
            assignee_ref=task["assignee_"],
            audit_intent_ref=intent_reference(self.scope, self.task_id, command_id),
            outcome=None,
        )
        (self.artifacts / f"command-{command_id}.json").write_bytes(result.canonical)
        return result

    async def principal(self) -> HumanPrincipal:
        revision = await self.admin.fetchval(
            "SELECT rev_ FROM public.mzo_human_principal WHERE tenant_=$1 AND principal_=$2",
            self.scope.tenant,
            self.principal_ref,
        )
        return HumanPrincipal(
            schema_version=1,
            principal_ref=self.principal_ref,
            issuer=self.issuer,
            subject=self.subject,
            tenant=self.scope.tenant,
            membership_revision=revision,
            memberships=(
                MembershipBinding(
                    membership_ref="synthetic-membership", roles=("test-reviewer",), groups=(GROUP,)
                ),
            ),
            session_ref="synthetic-not-an-oidc-session",
            authenticated_at=datetime.now(UTC),
            subject_bindings=(),
        )

    async def task(self):
        row = await self.admin.fetchrow(
            "SELECT * FROM public.act_ru_task WHERE id_=$1 AND tenant_id_=$2", self.task_id, self.scope.tenant
        )
        assert row is not None, "synthetic task must still be active"
        return row

    async def persist(self, command):
        return await self.store.persist(command, evidence_valid_until=self.valid_until)

    def transport(self, **kwargs) -> ObservedTransport:
        observed = ObservedTransport(self.config.transport(**kwargs))
        self.observers.append(observed)
        return observed

    async def rows(self, table: str):
        assert table in {"audit_chain", "audit_emit_dedup", "human_command_outbox", "human_command_delivery"}
        return await self.admin.fetch(f'SELECT * FROM "{self.scope.tenant}".{table}')

    async def delivery(self, command):
        rows = await self.rows("human_command_delivery")
        return next(r for r in rows if r["command_id"] == command.command_id)

    async def engine_receipts(self):
        return await self.admin.fetch(
            "SELECT * FROM public.mzo_human_receipt WHERE tenant_=$1 AND task_=$2",
            self.scope.tenant,
            self.task_id,
        )

    async def allow_retry(self, *, expire: bool = False) -> None:
        # Explicit local technical-clock fault/control, never a business deadline.
        clause = ",lease_until=clock_timestamp()-interval '1 second'" if expire else ""
        await self.admin.execute(
            f'UPDATE "{self.scope.tenant}".human_command_delivery '
            f"SET next_attempt_at=clock_timestamp()-interval '1 second'{clause} WHERE status='pending'"
        )

    async def assert_pending(self, command, *, engine_committed: bool = False):
        delivery = await self.delivery(command)
        assert delivery["status"] == "pending"
        assert (
            delivery["engine_receipt"] is delivery["audit_result_hash"] is delivery["technical_code"] is None
        )
        public = await self.store.read_owned(await self.principal(), command.task_id, command.command_id)
        assert public.status == "pending" and public.engine_receipt_ref is public.audit_result_ref is None
        records = [_row_to_record(r) for r in await self.rows("audit_chain")]
        assert [r.action for r in records] == ["human_command.intent"]
        assert len(await self.rows("audit_emit_dedup")) == 1
        assert len(await self.engine_receipts()) == int(engine_committed)

    async def assert_result(self, command, *, conflict: str | None = None):
        public = await self.store.read_owned(await self.principal(), command.task_id, command.command_id)
        delivery = await self.delivery(command)
        rows = await self.rows("audit_chain")
        records = [_row_to_record(r) for r in rows]
        matching = [r for r in records if r.details["command_id"] == command.command_id]
        assert {r.action for r in matching} == {"human_command.intent", "human_command.result"}
        assert len(matching) == 2
        base = dict(
            principal_kind="human",
            principal_ref=command.principal_ref,
            workload_ref=command.workload_ref,
            task_id=command.task_id,
            command_id=command.command_id,
            payload_digest=command.digest,
            audit_intent_ref=command.audit_intent_ref,
            operation=command.operation,
        )
        intent = next(r for r in matching if r.action == "human_command.intent")
        result = next(r for r in matching if r.action == "human_command.result")
        assert intent.details == base
        assert public.audit_result_ref == result.record_hash == delivery["audit_result_hash"]
        outbox = next(
            r for r in await self.rows("human_command_outbox") if r["command_id"] == command.command_id
        )
        assert outbox["canonical_payload"] == command.canonical and outbox["payload_digest"] == command.digest
        assert public.audit_intent_hash == intent.record_hash == outbox["audit_intent_hash"]
        if conflict:
            assert public.status == delivery["status"] == "conflict" and public.technical_code == conflict
            assert public.engine_receipt_ref is delivery["engine_receipt"] is None
            expected = {**base, "status": "conflict", "technical_code": conflict}
        else:
            assert public.status == delivery["status"] == "committed"
            raw = bytes(delivery["engine_receipt"])
            receipt = verify_engine_receipt(raw, command)
            engine_row = next(r for r in await self.engine_receipts() if r["command_"] == command.command_id)
            assert engine_row["receipt_"].encode() == raw
            assert (
                engine_row["digest_"] == command.digest and engine_row["principal_"] == command.principal_ref
            )
            assert engine_row["workload_"] == command.workload_ref
            assert public.engine_receipt_ref == receipt.engine_receipt_ref
            assert int(receipt.resulting_task_revision) == int(command.task_revision) + 1
            expected = {
                **base,
                "status": "committed",
                "engine_receipt_ref": receipt.engine_receipt_ref,
                "engine_recorded_at": receipt.recorded_at,
                "engine_receipt_digest": hashlib.sha256(raw).hexdigest(),
            }
        assert result.details == expected
        for record in matching:
            assert record.agent_id == command.workload_ref and record.tenant_id == command.tenant
            assert record.agent_version == "human-command.v1" and record.decision == "REQUIRE_HUMAN"
        parameters = self.config.db_parameters()
        parameters["username"] = parameters.pop("user")
        dsn = URL.create("postgresql", **parameters).render_as_string(hide_password=False)
        assert (await verify_chain(dsn, self.scope.tenant)).valid
        write_json(
            self.artifacts / f"verified-result-{command.command_id}.json", public.model_dump(mode="json")
        )
        return public

    async def child(self, mode: str, *, command: HumanCommand | None = None):
        directory = self.artifacts / (mode + "-" + uuid4().hex)
        directory.mkdir()
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.support.human_relay_live",
            mode,
            str(directory),
            cwd=ROOT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(command.canonical if command else b""), 45
            )
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            (directory / "stdout-timeout.txt").write_bytes(stdout)
            (directory / "stderr-timeout.txt").write_bytes(stderr)
            write_json(
                directory / "process-timeout.json", {"pid": process.pid, "returncode": process.returncode}
            )
            raise
        (directory / "stdout.txt").write_bytes(stdout)
        (directory / "stderr.txt").write_bytes(stderr)
        write_json(
            directory / "process.json", {"pid": process.pid, "returncode": process.returncode, "mode": mode}
        )
        assert process.pid != os.getpid()
        assert process.returncode == (CRASH_EXIT if mode == "persist-then-crash" else 0)
        return directory

    async def close(self) -> None:
        for i, observer in enumerate(self.observers):
            directory = self.artifacts / f"transport-{i}"
            directory.mkdir()
            observer.save(directory)
        if hasattr(self, "task_id"):
            write_json(
                self.artifacts / "final-engine-receipts.json", [dict(r) for r in await self.engine_receipts()]
            )
        if self.schema_owned:
            for table in (
                "audit_chain",
                "audit_emit_dedup",
                "human_command_outbox",
                "human_command_delivery",
            ):
                if await self.admin.fetchval("SELECT to_regclass($1)", f"{self.scope.tenant}.{table}"):
                    values = []
                    for row in await self.rows(table):
                        values.append(
                            {k: bytes(v).decode() if isinstance(v, bytes) else v for k, v in row.items()}
                        )
                    write_json(self.artifacts / f"final-{table}.json", values)
        if hasattr(self, "pool"):
            await self.pool.close()
        if hasattr(self, "rest"):
            try:
                if self.deployment_id:
                    response = await self.rest.delete(
                        f"/deployment/{self.deployment_id}", params={"cascade": "true"}
                    )
                    assert response.status_code == 204, "owned synthetic deployment cleanup failed"
            finally:
                await self.rest.aclose()
        if hasattr(self, "admin"):
            try:
                if self.schema_owned:
                    await self.admin.execute(f'DROP SCHEMA "{self.scope.tenant}" CASCADE')
            finally:
                await self.admin.close()


async def child_main(mode: str, directory: Path) -> None:
    config = RelayConfig.load()
    pool = await config.pool()
    store = PostgresHumanOutbox(scope=config.scope, pool=pool)
    if mode == "persist-then-crash":
        command = HumanCommand(**strict_loads(sys.stdin.buffer.read()))
        await store.persist(command, evidence_valid_until=datetime.now(UTC) + timedelta(minutes=1))
        # Actual process termination after acknowledged database COMMIT, before
        # a transport is constructed; no close/finally can fabricate recovery.
        os._exit(CRASH_EXIT)
    assert mode == "run-once"
    observer = ObservedTransport(config.transport())
    try:
        assert await relay(store, observer).run_once()
    finally:
        observer.save(directory)
        await pool.close()


if __name__ == "__main__":
    assert len(sys.argv) == 3
    asyncio.run(child_main(sys.argv[1], Path(sys.argv[2])))
