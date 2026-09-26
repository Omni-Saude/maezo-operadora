"""Fixture sintetica AUTH do dev: cercas fail-closed e construcao dos payloads. Sem rede, sem banco."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from tools.dev_syn_fixture import __main__ as dev
from tools.dev_syn_fixture import core, guards

from maezo.gateway.human.auth_profile import Definition, Scope
from maezo.gateway.human.read_profile import digest, parse_model

ROOT = Path(__file__).parents[3]
NOW = datetime(2026, 9, 24, 12, tzinfo=UTC)
ENV = {guards.ACCOUNT_ENV: "203312548462", guards.ENVIRONMENT_ENV: "dev"}
SCOPE = dict(
    tenant="amh",
    environment="dev",
    engine_name="default",
    database_incarnation="dev-incarnation-1",
    installation_ref="dev-auth-installation",
    installation_revision="1",
)
DEFINITION = dict(
    process_key="SP-OP-AUTH-001",
    definition_id="SP-OP-AUTH-001:3:abc",
    definition_digest="b" * 64,
    deployment_id="dep-1",
    input_profile="portal-auth-intake.v1",
    profile_digest="c" * 64,
)


def config(**changes: Any) -> dict[str, Any]:
    value: dict[str, Any] = dict(
        schema="dev-syn-fixture.v1",
        guide_number="SYN-DEVGUIA1",
        auth_scope=dict(SCOPE),
        engine_rest_url="http://engine.internal:8080/engine-rest",
        native=dict(
            origin="https://engine-native.maezo-operadora-dev.internal",
            ca_file="/run/ca.pem",
            client_certificate_file="/run/cert.pem",
            client_key_file="/run/key.pem",
            audience="dev-engine-auth",
        ),
        database=dict(
            owner_dsn_file="/run/owner-dsn",
            ca_file="/run/rds.pem",
            native_schema="maezo_native",
            runtime_role="cibseven_app",
        ),
        installation=dict(native_code_digest="d" * 64, valid_days=14),
    )
    value.update(changes)
    return value


def load(value: dict[str, Any], env: dict[str, str] | None = None) -> dev.Config:
    return dev.load_config(json.dumps(value).encode(), ENV if env is None else env)


# --- cercas -------------------------------------------------------------------------------------


def test_dev_target_accepted() -> None:
    loaded = load(config())
    assert loaded.guide_number == "SYN-DEVGUIA1"
    assert loaded.refs().label == "dev-devguia1"


@pytest.mark.parametrize(
    "env",
    [
        {},
        {guards.ENVIRONMENT_ENV: "dev"},
        {guards.ACCOUNT_ENV: "999999999999", guards.ENVIRONMENT_ENV: "dev"},
        {guards.ACCOUNT_ENV: " 203312548462", guards.ENVIRONMENT_ENV: "dev"},
        {guards.ACCOUNT_ENV: "203312548462"},
        {guards.ACCOUNT_ENV: "203312548462", guards.ENVIRONMENT_ENV: "staging"},
        {guards.ACCOUNT_ENV: "203312548462", guards.ENVIRONMENT_ENV: "prod"},
        {guards.ACCOUNT_ENV: "203312548462", guards.ENVIRONMENT_ENV: "DEV"},
    ],
)
def test_env_refused(env: dict[str, str]) -> None:
    with pytest.raises(guards.DevGuardError):
        load(config(), env)


@pytest.mark.parametrize(
    "field,value",
    [("environment", "prod"), ("environment", "staging"), ("tenant", "amh2"), ("tenant", "AMH")],
)
def test_scope_refused(field: str, value: str) -> None:
    with pytest.raises(guards.DevGuardError):
        load(config(auth_scope=dict(SCOPE, **{field: value})))


@pytest.mark.parametrize(
    "guide",
    [
        "123456",
        "SYN-",
        "syn-DEVGUIA1",
        "SYN-devguia1",
        "SYN-DEV GUIA",
        "SYN-DEV-1",
        "XSYN-DEV1",
        "SYN-DEV1\n",
        12345,
        None,
    ],
)
def test_guide_refused(guide: object) -> None:
    with pytest.raises(guards.DevGuardError):
        load(config(guide_number=guide))


def test_guide_pattern_accepts_c1_and_dev() -> None:
    for guide in ("SYN-C1GUIA1", "SYN-DEVGUIA1", "SYN-0001"):
        assert guards.check_guide(guide) == guide


@pytest.mark.parametrize(
    "changes",
    [
        dict(schema="other.v1"),
        dict(installation=dict(native_code_digest="d" * 64, valid_days=30)),
        dict(installation=dict(native_code_digest="xyz", valid_days=14)),
        dict(extra=True),
        dict(database=dict(owner_dsn_file="/x", ca_file="/y", native_schema="maezo_native")),
    ],
)
def test_config_refused(changes: dict[str, Any]) -> None:
    with pytest.raises(dev.ConfigError):
        load(config(**changes))


def test_refuses_non_synthetic_claims() -> None:
    core.refuse_non_synthetic(["SYN-C1GUIA1", "SYN-guide-dev-0001"])
    with pytest.raises(core.SyntheticRefusedError):
        core.refuse_non_synthetic(["SYN-DEVGUIA1", "123456789"])


def test_refs_without_prefix_refused() -> None:
    refs = core.FixtureRefs.for_label("dev", "SYN-DEVGUIA1", audience="a")
    refs.check()
    from dataclasses import replace

    with pytest.raises(core.SyntheticRefusedError):
        replace(refs, provider="provider-real-1").check()
    with pytest.raises(core.SyntheticRefusedError):
        replace(refs, guide_number="2026000001").check()


def test_main_refuses_before_any_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_: object, **__: object) -> None:
        raise AssertionError("rede tocada")

    monkeypatch.setattr(dev, "run", boom)
    path = tmp_path / "c.json"
    path.write_text(json.dumps(config()), encoding="utf-8")
    env = {dev.CONFIG_ENV: str(path), guards.ACCOUNT_ENV: "111111111111", guards.ENVIRONMENT_ENV: "dev"}
    assert dev.main(env) == 3
    assert dev.main({}) == 2


# --- payloads -----------------------------------------------------------------------------------


def test_c1_refs_unchanged() -> None:
    """O harness continua com as mesmas referencias de antes do nucleo compartilhado."""
    refs = core.FixtureRefs.for_label("c1", "SYN-C1GUIA1", audience="c1-engine-auth")
    assert (
        refs.workload,
        refs.publisher,
        refs.source,
        refs.guide_ref,
        refs.intake,
        refs.command,
        refs.intent,
    ) == (
        "SYN-c1-auth-fixture",
        "SYN-c1-fixture-publisher",
        "SYN-c1-fixture-source",
        "SYN-guide-c1-0001",
        "SYN-intake-c1-0001",
        "SYN-command-c1-0001",
        "SYN-intent-c1-0001",
    )
    assert refs.issuer == "https://cognito-idp.sa-east-1.amazonaws.com/sa-east-1_SYNc1Fixture"
    assert refs.keys == {
        "human-auth-input-publication": "SYN-c1-fixture-publication",
        "human-auth-start": "SYN-c1-fixture-start",
    }


def test_inputs_publications_and_start() -> None:
    refs = load(config()).refs()
    until = NOW + timedelta(hours=2)
    inputs = core.build_inputs(refs, NOW, until, cutover="SYN-dev-cutover")
    kinds = [kind for kind, *_ in inputs.items]
    assert kinds == ["actor", "resource_authority", "guide", "start_facts", "document_policy", "audit_intent"]
    assert all(ref.startswith("SYN-") for _, ref, _, _ in inputs.items)
    guide = inputs.items[2][2]
    assert guide.numero_guia_tiss == "SYN-DEVGUIA1"
    scope = parse_model(Scope, SCOPE)
    first = core.publication(refs, scope, *inputs.items[0], until)
    again = core.publication(refs, scope, *inputs.items[0], until, expected_generation=2)
    assert (first.publication_id, first.expected_generation) == (f"SYN-publication-{refs.label}-actor", 0)
    assert (again.publication_id, again.expected_generation) == (f"SYN-publication-{refs.label}-actor-g3", 2)
    other = core.FixtureRefs.for_label("dev-devguia2", "SYN-DEVGUIA2", audience=refs.audience)
    assert core.publication(other, scope, *inputs.items[0], until).publication_id != first.publication_id
    grants = inputs.grants(refs)
    assert len(grants) == 6 and {g["publisher_ref"] for g in grants} == {refs.publisher}
    heads = {kind: (ref, 1, provenance, digest(payload)) for kind, ref, payload, provenance in inputs.items}
    command = core.start_command(refs, scope, parse_model(Definition, DEFINITION), inputs, heads)
    assert command.guide_identity_ref == refs.guide_ref
    assert [pin.kind for pin in command.input_pins] == sorted(k for k in kinds if k != "audit_intent")


def test_designations() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    refs = load(config()).refs()
    key = Ed25519PrivateKey.generate()
    grants = [dict(kind="actor")]
    publication = core.key_designation(refs, core.PUBLICATION_PURPOSE, key, "f" * 64, grants, NOW, NOW)
    start = core.key_designation(refs, core.START_PURPOSE, key, "f" * 64, grants, NOW, NOW)
    assert publication["source_grants"] == grants and start["source_grants"] == []
    assert publication["key_id"].startswith("SYN-") and publication["issuer"] == refs.workload
    result = core.result_designation(b"spki", "k", "i", NOW, NOW)
    assert result["purpose"] == "human-auth-result" and len(result["peer_spki_sha256"]) == 64


def test_qualification() -> None:
    deployed = dict(id="SP-OP-AUTH-001:4:x", deployment="dep-4", digest="a" * 64)
    original = dict(schema="human-auth-installation-qualification.v1", definition=dict(DEFINITION))
    updated = core.qualify_definition(original, deployed)
    assert original["definition"] == DEFINITION  # sem mutacao
    assert updated["definition"]["definition_id"] == "SP-OP-AUTH-001:4:x"
    synthetic = core.synthetic_qualification(
        deployed, native_code_digest="d" * 64, label="dev", valid_until=NOW
    )
    assert all(
        synthetic[k].startswith("SYN-")
        for k in ("cutover_ref", "review_receipt_ref", "runtime_qualification_ref")
    )
    parse_model(Definition, synthetic["definition"])


def test_escalation_routes_to_plantao() -> None:
    """As constantes do executor casam com a regra r1 da DMN (-> plantao-clinico, P1)."""
    tree = ET.parse(ROOT / "spec/processes/dmn/escalation_routing.dmn")
    rule = next(r for r in tree.iter() if r.tag.endswith("rule") and r.get("id") == "r1")
    texts = [e.text for e in rule.iter() if e.tag.endswith("text")]
    assert texts[:4] == [f'"{dev.MOTIVO}"', f'"{dev.SEVERIDADE}"', '"P1"', f'"{dev.EXPECTED_GROUP}"']
    variables = core.escalation_variables(
        "amh", dev.MOTIVO, dev.SEVERIDADE, agent="a", conversation="c", canal="x"
    )
    assert variables["motivo_categoria"]["value"] == "red_flag_clinico"
    assert variables["severidade"]["value"] == "grave"
    assert core.escalation_key("amh", "SYN-DEVGUIA1") == "ESC-amh-sla-auth-SYN-DEVGUIA1"
    assert core.auth_instance_key("amh", "SYN-DEVGUIA1") == "AUTH-amh-SYN-DEVGUIA1"


@pytest.mark.parametrize("existing", [False, True])
def test_open_escalation_idempotent(existing: bool) -> None:
    started: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/process-instance"):
            key = request.url.params["businessKey"]
            return httpx.Response(200, json=[{"id": "x"}] if existing or key.startswith("AUTH-") else [])
        if path.endswith("/start"):
            started.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "new"})
        if path.endswith("/fetchAndLock"):
            return httpx.Response(200, json=[])
        if path.endswith("/task"):
            return httpx.Response(200, json=[{"id": "t1"}])
        if path.endswith("/identity-links"):
            return httpx.Response(200, json=[{"groupId": "plantao-clinico"}])
        return httpx.Response(404)

    with httpx.Client(base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler)) as client:
        outcome = core.open_escalation(
            client,
            "amh",
            "SYN-DEVGUIA1",
            motivo=dev.MOTIVO,
            severidade=dev.SEVERIDADE,
            agent="a",
            conversation="c",
            canal="x",
            worker="w",
        )
    assert outcome.created is not existing and len(started) == (0 if existing else 1)
    assert outcome.groups == ["plantao-clinico"] and outcome.auth_instances == 1
    if started:
        assert started[0]["businessKey"] == "ESC-amh-sla-auth-SYN-DEVGUIA1"


# --- escalacao encerrada -> guia nova (Onda 8) ---------------------------------------------------


def _rest(running: bool, history: bool, tasks: bool) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/history/process-instance"):
            return httpx.Response(200, json=[{"id": "h"}] if history else [])
        if path.endswith("/process-instance"):
            return httpx.Response(200, json=[{"id": "x"}] if running else [])
        if path.endswith("/task"):
            return httpx.Response(200, json=[{"id": "t"}] if tasks else [])
        return httpx.Response(404)

    return httpx.Client(base_url="http://engine/engine-rest", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("running", "history", "tasks", "expected"),
    [
        (False, False, False, "absent"),
        (True, True, True, "live"),
        (True, False, False, "pending"),
        (False, True, False, "closed"),
    ],
)
def test_escalation_state(running: bool, history: bool, tasks: bool, expected: str) -> None:
    with _rest(running, history, tasks) as client:
        assert core.escalation_state(client, "amh", "SYN-DEVGUIA1") == expected


def test_fresh_guide_is_synthetic_and_unique() -> None:
    taken = {"SYN-R260924120000"}
    guide = core.fresh_guide("SYN-DEVGUIA1", NOW, lambda g: g in taken)
    assert guide == "SYN-R260924120000N1"
    assert core.GUIDE_PATTERN.fullmatch(guide) and len(guide) <= core.GUIDE_MAX_LENGTH
    assert core.fresh_guide("SYN-DEVGUIA1", NOW, lambda g: False) == "SYN-R260924120000"
    # O ultimo contador ainda cabe no numero_guia_tiss (20).
    last = core.fresh_guide("SYN-DEVGUIA1", NOW, lambda g: not g.endswith("N99"))
    assert last == "SYN-R260924120000N99" and len(last) == core.GUIDE_MAX_LENGTH
    with pytest.raises(core.SyntheticRefusedError):
        core.fresh_guide("SYN-devguia", NOW, lambda g: False)
    with pytest.raises(core.SyntheticRefusedError):
        core.fresh_guide("SYN-DEVGUIA1", NOW, lambda g: True)


class _StopError(Exception):
    pass


@pytest.mark.parametrize("closed", [True, False])
def test_run_replaces_a_closed_guide_before_any_write(monkeypatch: pytest.MonkeyPatch, closed: bool) -> None:
    seen: list[str] = []

    async def no_claims(config: Any, fn: Any, *args: Any) -> list[str]:
        return []  # nenhuma guia real reivindicada

    monkeypatch.setattr(dev, "_db", no_claims)
    monkeypatch.setattr(
        core,
        "escalation_state",
        lambda rest, tenant, guide: "closed" if closed and guide == "SYN-DEVGUIA1" else "absent",
    )
    monkeypatch.setattr(core, "auth_instance_exists", lambda rest, tenant, guide: False)

    def stop(rest: Any, tenant: str) -> Any:
        raise _StopError

    monkeypatch.setattr(core, "deployed_definition", stop)
    original = dev.Config.refs

    def refs(self: dev.Config) -> core.FixtureRefs:
        seen.append(self.guide_number)
        return original(self)

    monkeypatch.setattr(dev.Config, "refs", refs)
    with pytest.raises(_StopError):
        dev.run(load(config()))
    assert seen[0] == "SYN-DEVGUIA1"
    if closed:
        assert seen[-1].startswith("SYN-R") and core.GUIDE_PATTERN.fullmatch(seen[-1])
        assert len(seen[-1]) <= core.GUIDE_MAX_LENGTH
    else:
        assert set(seen) == {"SYN-DEVGUIA1"}
