"""tools/staff_ops: allowlists, idempotencia do instalador de linhas e cercas do runner SYN."""

from __future__ import annotations

import asyncio
import base64
import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from tools.staff_ops import job, rows, syn
from tools.staff_ops.__main__ import main as dispatch
from tools.staff_ops.common import OpsError, b64

posix = pytest.mark.skipif(sys.platform == "win32", reason="modos POSIX")


def enc(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ------------------------------------------------------------------ common / dispatch
def test_b64_refuses_non_canonical() -> None:
    assert b64(enc(b"x"), "t") == b"x"
    with pytest.raises(OpsError):
        b64("eA", "t")  # sem padding
    with pytest.raises(OpsError):
        b64("", "t")


def test_dispatch_unknown_command() -> None:
    assert dispatch(["nada"]) == 64
    assert dispatch(["rows", "extra"]) == 64


# ------------------------------------------------------------------ job
def _job_document() -> dict[str, str]:
    doc = {f"human/{name}": enc(b"h-" + name.encode()) for name in job.human_files()}
    doc.update({f"job/{name}": enc(b"j-" + name.encode()) for name in job.JOB_FILES})
    return doc


def test_job_decode_exact_allowlist() -> None:
    human, files = job.decode(json.dumps(_job_document()))
    assert set(human) == job.human_files() and set(files) == job.JOB_FILES
    for mutate in (
        lambda d: d.pop("job/config.json"),
        lambda d: d.update({"job/extra.txt": enc(b"x")}),
        lambda d: d.update({"other/config.json": enc(b"x")}),
        lambda d: d.update({"job/../x": enc(b"x")}),
    ):
        doc = _job_document()
        mutate(doc)
        with pytest.raises(OpsError):
            job.decode(json.dumps(doc))
    with pytest.raises(OpsError):
        job.decode('{"job/config.json":"eA==","job/config.json":"eA=="}')


@posix
def test_job_materialize_modes(tmp_path: Path) -> None:
    (tmp_path / "human").mkdir()
    (tmp_path / "job").mkdir()
    human, files = job.decode(json.dumps(_job_document()))
    job.materialize(human, files, root=tmp_path, owner=None)
    current = tmp_path / "human" / "current"
    assert stat.S_IMODE(current.stat().st_mode) == 0o500
    assert {p.name for p in current.iterdir()} == job.human_files()
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o400 for p in current.iterdir())
    assert stat.S_IMODE((tmp_path / "job").stat().st_mode) == 0o500


def test_job_materialize_refuses_dirty_volume(tmp_path: Path) -> None:
    (tmp_path / "human").mkdir()
    (tmp_path / "job").mkdir()
    (tmp_path / "job" / "leftover").write_text("x")
    human, files = job.decode(json.dumps(_job_document()))
    with pytest.raises(OpsError):
        job.materialize(human, files, root=tmp_path, owner=None)


class _S3:
    class exceptions:  # noqa: N801 - forma do boto3
        class NoSuchKey(Exception):  # noqa: N818 - nome do boto3
            pass

    def __init__(self, body: bytes | None) -> None:
        self.body = body
        self.put: list[dict[str, Any]] = []

    def get_object(self, **_: Any) -> dict[str, Any]:
        if self.body is None:
            raise self.exceptions.NoSuchKey()
        data = self.body

        class B:
            def read(self) -> bytes:
                return data

        return {"Body": B(), "VersionId": "v1"}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.put.append(kwargs)
        return {"VersionId": "v2"}


@posix
def test_ledger_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "ledger.json"
    assert job.fetch_ledger(_S3(None), "b", "k", path) == "absent" and not path.exists()
    client = _S3(b'{"tenant":"amh"}')
    assert job.fetch_ledger(client, "b", "k", path) == "v1"
    assert job.store_ledger(client, "b", "k", path) == "v2"
    assert client.put[0]["ServerSideEncryption"] == "AES256"
    with pytest.raises(ValueError):
        job.fetch_ledger(_S3(b"not json"), "b", "k", tmp_path / "other.json")


# ------------------------------------------------------------------ syn
def _syn(guide: str = "SYN-DEVGUIA1") -> dict[str, Any]:
    return {
        "schema": syn.SCHEMA,
        "config": {
            "schema": "dev-syn-fixture.v1",
            "guide_number": guide,
            "native": {"origin": "https://engine-native.x.internal", "audience": "a"},
            "database": {"native_schema": "maezo_native", "runtime_role": "cibseven_app"},
        },
        "files": {"client_certificate": enc(b"c"), "client_key": enc(b"k"), "native_ca": enc(b"ca")},
    }


@pytest.mark.parametrize("guide", ["AUTH-123", "SYN-", "syn-abc", "SYN-ABC/1", "123"])
def test_syn_refuses_non_synthetic_guide(tmp_path: Path, guide: str) -> None:
    with pytest.raises(OpsError):
        syn.build(_syn(guide), "p" * 20, "h", "5432", "maezo", run=tmp_path)
    assert not any(tmp_path.iterdir())  # nada escrito antes da cerca


@posix
def test_syn_builds_paths_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ca = tmp_path / "rds"
    ca.mkdir()
    for n in range(3):
        (ca / f"{n}.crt").write_bytes(b"-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----\n")
    monkeypatch.setattr(syn, "RDS_CA", str(ca))
    run = tmp_path / "run"
    run.mkdir()
    path = syn.build(_syn(), "senha@/:muito-longa", "db.host", "5432", "maezo", run=run)
    config = json.loads(path.read_text())
    assert config["database"]["owner_dsn_file"] == str(run / "owner-dsn")
    assert "senha" not in path.read_text()
    assert (run / "owner-dsn").read_text().startswith("postgresql://maezo_native_schema_owner:senha%40%2F%3A")


# ------------------------------------------------------------------ rows
def _rows_doc(**over: Any) -> dict[str, Any]:
    doc = {
        "schema": rows.SCHEMA,
        "scope": {
            "tenant": "amh",
            "environment": "dev",
            "engine_name": "default",
            "database_incarnation": "i:1",
        },
        "designation_revision": 1,
        "designation_b64": enc(b'{"d":1}'),
        "designation_sha256": "0" * 64,
        "installation_proof_b64": enc(b'{"p":1}'),
        "root_public_key_b64": enc(b"r"),
        "admission_ref": "ref:1",
        "admission_revision": 1,
        "admission_record_b64": enc(b'{"a":1}'),
        "admission_signature_b64": enc(b"s" * 64),
        "admission_sha256": "1" * 64,
        "identity_login": "portal_read_source_amh",
        "identity_search_path": "amh",
        "auth": {
            "auth_scope": {
                "tenant": "amh",
                "environment": "dev",
                "engine_name": "default",
                "database_incarnation": "i:1",
                "installation_ref": "ref:auth:1",
                "installation_revision": "1",
            },
            "native_code_digest": "a" * 64,
            "runtime_role": "cibseven_app",
            "valid_days": 12,
        },
    }
    doc.update(over)
    return doc


def test_rows_parse_fences() -> None:
    assert rows.parse(_rows_doc())["signature"] == b"s" * 64
    for bad in (
        _rows_doc(schema="x"),
        _rows_doc(
            scope={"tenant": "amh", "environment": "prod", "engine_name": "d", "database_incarnation": "i"}
        ),
        _rows_doc(identity_login="x; DROP ROLE y"),
        _rows_doc(identity_search_path="amh,public"),
        _rows_doc(designation_revision=0),
        {**_rows_doc(), "extra": 1},
        _rows_doc(auth={**_rows_doc()["auth"], "valid_days": 30}),
        _rows_doc(
            auth={**_rows_doc()["auth"], "auth_scope": {**_rows_doc()["auth"]["auth_scope"], "tenant": "x"}}
        ),
    ):
        with pytest.raises(OpsError):
            rows.parse(bad)


class _Tx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_: Any) -> bool:
        return False


class _Owner:
    def __init__(self, event: Any = None, current: Any = None, admission: Any = None) -> None:
        self.rows = {"event": event, "current": current, "admission": admission}
        self.writes: list[str] = []

    def transaction(self) -> _Tx:
        return _Tx()

    async def fetchval(self, query: str, *args: Any) -> Any:
        return rows.OWNER

    async def execute(self, query: str, *args: Any) -> str:
        if not query.startswith("SET LOCAL"):
            self.writes.append(query.split()[0] + " " + query.split()[2])
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> Any:
        if "designation_event" in query:
            return self.rows["event"]
        if "designation_current" in query:
            return self.rows["current"]
        return self.rows["admission"]


class _Identity:
    """Conexao do PROPRIO identity_login (nunca a credencial mestre)."""

    def __init__(self, login: str = "portal_read_source_amh") -> None:
        self.login = login
        self.statements: list[str] = []

    async def fetchval(self, query: str, *args: Any) -> Any:
        return self.login

    async def execute(self, query: str, *args: Any) -> str:
        self.statements.append(query)
        return "OK"


def test_rows_identity_login_and_search_path_pass_the_name_fence() -> None:
    parsed = rows.parse(_rows_doc())
    assert rows._NAME.fullmatch(parsed["identity_login"]) and rows._NAME.fullmatch(parsed["identity_search_path"])


def test_rows_search_path_uses_own_credential_and_refuses_other_login() -> None:
    parsed = rows.parse(_rows_doc())
    identity = _Identity()
    actions = asyncio.run(rows.install(_Owner(), identity, parsed))
    assert identity.statements == ["ALTER ROLE CURRENT_USER SET search_path = amh"]
    assert actions["identity_search_path"] == "portal_read_source_amh=amh"
    wrong = _Identity(login="postgres")
    with pytest.raises(OpsError):
        asyncio.run(rows.install(_Owner(), wrong, parsed))
    assert wrong.statements == []


def test_rows_install_is_idempotent_and_refuses_divergence() -> None:
    parsed = rows.parse(_rows_doc())
    fresh = _Owner()
    actions = asyncio.run(rows.install(fresh, _Identity(), parsed))
    assert (
        actions["designation_event"] == actions["designation_current"] == actions["admission"] == "inserida"
    )
    assert [w for w in fresh.writes if w.startswith(("DELETE", "UPDATE"))] == []
    same = _Owner(
        event={
            "designation_digest": "0" * 64,
            "canonical_designation": '{"d":1}',
            "installation_proof": '{"p":1}',
        },
        current={"designation_revision": 1, "designation_digest": "0" * 64},
        admission={"record_": b'{"a":1}', "signature_": b"s" * 64, "revoked_": False},
    )
    again = asyncio.run(rows.install(same, _Identity(), parsed))
    assert again["designation_event"] == again["admission"] == "igual" and same.writes == []
    other = _Owner(
        event={"designation_digest": "f" * 64, "canonical_designation": "x", "installation_proof": "y"}
    )
    with pytest.raises(OpsError):
        asyncio.run(rows.install(other, _Identity(), parsed))
    revoked = _Owner(
        event=same.rows["event"],
        current=same.rows["current"],
        admission={"record_": b'{"a":1}', "signature_": b"s" * 64, "revoked_": True},
    )
    with pytest.raises(OpsError):
        asyncio.run(rows.install(revoked, _Identity(), parsed))


def test_rows_verify_rejects_wrong_digest() -> None:
    with pytest.raises(OpsError):
        rows.verify(rows.parse(_rows_doc()))


class _BootOwner(_Owner):
    def __init__(self, auth_row: Any = None) -> None:
        super().__init__()
        self.auth_row = auth_row

    async def execute(self, query: str, *args: Any) -> str:
        await super().execute(query, *args)
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *args: Any) -> Any:
        if "mzo_auth_installation" in query:
            return self.auth_row
        return {"db": 1, "ns": 2, "name": "maezo"}


def test_bootstrap_inserts_tenant_and_auth_only_when_absent() -> None:
    parsed = rows.parse(_rows_doc())
    fresh = _BootOwner()
    assert asyncio.run(rows.bootstrap(fresh, parsed)) == {
        "human_tenant": "inserida",
        "auth_installation": "inserida",
    }
    same = _BootOwner({"scope_": json.dumps(parsed["auth"]["auth_scope"])})
    assert asyncio.run(rows.bootstrap(same, parsed))["auth_installation"] == "igual"
    with pytest.raises(OpsError):
        asyncio.run(rows.bootstrap(_BootOwner({"scope_": '{"tenant":"outro"}'}), parsed))
