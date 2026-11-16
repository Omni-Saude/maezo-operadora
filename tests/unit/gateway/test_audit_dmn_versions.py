"""Proof that `AuditRecord.dmn_versions` can be populated from a real DMN evaluation (T1.5,
ADR-0028 §5) — closes the TODO(T1.5) that used to live on `AuditRecord.dmn_versions`'s
docstring (`gateway/audit.py`).

`DmnTransport.evaluate(...)` (`maezo.tools.workers.dmn_transport`) returns a `DmnVersion`
alongside every result; `DmnVersion.to_audit_dict()` is the exact shape this column expects
(`{"version": ..., "id": ..., "deploymentId": ...}`). This module proves the wire-up with
`FakeDmnTransport` (fast, no engine) and — when the live compose engine is reachable — with a
REAL `CibSevenDmnTransport` evaluation, so at least one example in this repo cites an ACTUAL
engine-verified `dmn_versions` payload, not a fabricated one (constraint 3).

What this does NOT prove (a separate, larger follow-up, explicitly out of T1.5's scope): that any
production code path actually constructs an `AuditRecord` from a worker/agent decision today — it
does not (`grep AuditRecord( src/` finds exactly one call site, `audit_postgres.py`'s
row-to-record decoder, which is a READ path). This module demonstrates the schema is populatable
and gives a template for the caller that eventually does the write-side wiring.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.gateway.audit import AuditRecord
from maezo.platform import rco_manifesto
from maezo.tools.workers import rco_pin
from maezo.tools.workers._audit_ctx import collect_dmn_versions
from maezo.tools.workers.base import reclassify_coded_exception
from maezo.tools.workers.dmn_transport import (
    CibSevenDmnTransport,
    DmnVersion,
    FakeDmnTransport,
    evaluate_sync,
)
from maezo.tools.workers.rco_pin import (
    RCO_RULES_DIR,
    RCO_RULES_PIN_RELATIVE_PATH,
    RcoRulesPinError,
    load_rco_rules_pin,
)
from tests.unit.platform import rco_k1_sintetico as sint


def test_dmn_version_to_audit_dict_shape_matches_column_expectation() -> None:
    """`DmnVersion.to_audit_dict()` is exactly the per-key shape `dmn_versions` expects."""
    version = DmnVersion(key="pagto_alcada", id="pagto_alcada:3:abc", version=3, deployment_id="dep-1")
    assert version.to_audit_dict() == {"version": 3, "id": "pagto_alcada:3:abc", "deploymentId": "dep-1"}


def test_audit_record_populated_from_fake_dmn_evaluation() -> None:
    """End-to-end (fast, no engine): evaluate via FakeDmnTransport, build a real AuditRecord
    with dmn_versions populated from the returned DmnVersion — proves the schema round-trips
    (canonicalize_jsonb + hash) without raising, and the value is queryable afterward."""
    fake = FakeDmnTransport()
    fake.register(
        "pagto_alcada",
        [{"faixa_valor": "ALCADA_L1", "grupo_aprovador": "aprovacao-financeira-l1"}],
        version=7,
        definition_id="pagto_alcada:7:test",
        deployment_id="dep-test-1",
    )

    rows, version = evaluate_sync(fake, "pagto_alcada", {"valor_pagamento_cents": 25_000_000})

    record = AuditRecord(
        agent_id="pagto-worker",
        tenant_id="amh",
        agent_version="1.0.0",
        action="operadora.pagto.calculate_facts",
        decision="ALLOW",
        details={"faixa_valor": rows[0]["faixa_valor"]},
        dmn_versions={"pagto_alcada": version.to_audit_dict()},
        timestamp=datetime.now(UTC),
    )

    assert record.dmn_versions == {
        "pagto_alcada": {"version": 7, "id": "pagto_alcada:7:test", "deploymentId": "dep-test-1"}
    }
    # Never "unknown" (the donor's fail-open gap, ADR-0028 §2) — a real int version + real id.
    assert record.dmn_versions["pagto_alcada"]["version"] == 7
    assert record.record_hash is not None


@pytest.mark.integration
async def test_audit_record_populated_from_live_engine_evaluation() -> None:
    """Same proof, but against the REAL compose engine (ADR-0011, never a mock) — at least one
    example in this repo of a `dmn_versions`-populated audit record citing an ACTUAL engine
    version (constraint 3: no fabricated results). Loudly skips if the engine is unreachable
    (mirrors `tests/integration/conftest.py`'s `_skip_if_engine_unreachable` pattern, applied
    locally here since this file lives under `tests/unit/`, not `tests/integration/`)."""
    from maezo.platform.deploy.engine_deploy import resolve_engine_rest_url

    base_url = resolve_engine_rest_url()
    try:
        async with httpx.AsyncClient() as probe:
            resp = await probe.get(f"{base_url}/version", timeout=3.0)
        reachable = resp.status_code == 200
    except httpx.HTTPError:
        reachable = False
    if not reachable:
        pytest.skip(
            f"COULD NOT VERIFY: CIB Seven engine unreachable at {base_url} (GET /version "
            "failed). Run `docker compose --profile core up -d` and retry."
        )

    transport = CibSevenDmnTransport(base_url, timeout=30.0)  # engine can be slow under suite load
    try:
        rows, version = await transport.evaluate("ans_retry_policy", {"retry_attempt": 1})
    finally:
        await transport.close()

    record = AuditRecord(
        agent_id="ans-submit-worker",
        tenant_id="amh",
        agent_version="1.0.0",
        action="regulatorio.anssubmit.retransmit",
        decision="ALLOW",
        details={"backoff": rows[0]["backoff"], "continue_retry": rows[0]["continue_retry"]},
        dmn_versions={"ans_retry_policy": version.to_audit_dict()},
        timestamp=datetime.now(UTC),
    )

    assert record.dmn_versions["ans_retry_policy"]["version"] == version.version
    assert record.dmn_versions["ans_retry_policy"]["deploymentId"] == version.deployment_id
    assert record.dmn_versions["ans_retry_policy"]["id"] != "unknown"  # the donor's fail-open gap, fixed


# ---------------------------------------------------------------------------
# MZO-5a (ADR-0075 §(f)): rco K1 tables carry idRegra / versaoRegra / shaDmn
# ---------------------------------------------------------------------------

_SINTETICO = "rco_exemplo_sintetico"
_SINTETICO_REL = "spec/processes/dmn/rco/rco_exemplo_sintetico.dmn"
_REPO = Path(__file__).resolve().parents[3]


def _lock_sha(rel: str) -> str:
    lock = json.loads((_REPO / RCO_RULES_PIN_RELATIVE_PATH).read_text(encoding="utf-8"))
    (entry,) = [e for e in lock["exemplos_sinteticos"] if e["path"] == rel]
    return str(entry["sha256"])


def _record(dmn_versions: dict[str, Any]) -> AuditRecord:
    return AuditRecord(
        agent_id="w",
        tenant_id="amh",
        agent_version="1.0.0",
        action="a",
        decision="ALLOW",
        details={},
        dmn_versions=dmn_versions,
        timestamp=datetime(2026, 11, 15, tzinfo=UTC),
    )


def test_pin_path_constants_match_the_ci_gate() -> None:
    from scripts.ci import verify_rco_rules_pin as gate

    assert RCO_RULES_PIN_RELATIVE_PATH == gate.DEFAULT_LOCK_PATH
    assert RCO_RULES_DIR == gate.RULES_DIR
    assert rco_pin.LOCK_VERSION == gate.LOCK_VERSION


def test_maezo_table_entry_has_no_rco_keys() -> None:
    fake = FakeDmnTransport()
    fake.register("pagto_alcada", [{"faixa_valor": "ALCADA_L1", "idRegra": "nao-e-do-rco"}], version=2)
    with collect_dmn_versions() as collected:
        evaluate_sync(fake, "pagto_alcada", {"valor_pagamento_cents": 1})
    expected = {"version": 2, "id": "pagto_alcada:2:test", "deploymentId": "test-deployment"}
    assert collected == {"pagto_alcada": expected}
    assert not {"idRegra", "versaoRegra", "shaDmn"} & set(collected["pagto_alcada"])


def test_rco_table_entry_carries_sha_from_the_pin() -> None:
    sha = _lock_sha(_SINTETICO_REL)
    # notacao do pacote K1 (contrato-k1.md §1): shaDmn = decisoes[].sha256 = `sha256:<hex>`
    assert sha == "sha256:" + hashlib.sha256((_REPO / _SINTETICO_REL).read_bytes()).hexdigest()
    fake = FakeDmnTransport()
    fake.register(_SINTETICO, [{"decisao": "liberar", "classeAdverso": None, "fila": None}], version=1)
    with collect_dmn_versions() as collected:
        evaluate_sync(fake, _SINTETICO, {"rco_exemplo_tipo": "A", "rco_exemplo_quantidade": 3})
    # estado `sem_pacote`: no K1 manifest -> no versaoRegra; the synthetic table has no idRegra output.
    assert collected[_SINTETICO] == {
        "version": 1,
        "id": f"{_SINTETICO}:1:test",
        "deploymentId": "test-deployment",
        "shaDmn": sha,
    }


def test_rco_id_regra_comes_from_the_single_matched_row() -> None:
    version = DmnVersion(key=_SINTETICO, id="x", version=1, deployment_id="d")
    assert version.to_audit_dict({"idRegra": "K1-R-001"})["idRegra"] == "K1-R-001"
    assert version.to_audit_dict({"idRegra": "a.B_9-z"})["idRegra"] == "a.B_9-z"
    assert version.to_audit_dict({"idRegra": "x" * 64})["idRegra"] == "x" * 64
    # `""` e o vazio canonico do K1 (contrato §6): linha sem regra identificada, so shaDmn.
    for row in (None, {}, {"idRegra": None}, {"idRegra": ""}, {"idRegra": "", "decisao": "liberar"}):
        assert "idRegra" not in version.to_audit_dict(row)

    fake = FakeDmnTransport()
    fake.register(_SINTETICO, [{"idRegra": "K1-R-001"}, {"idRegra": "K1-R-002"}])
    with collect_dmn_versions() as collected:
        evaluate_sync(fake, _SINTETICO, {})
    assert "idRegra" not in collected[_SINTETICO]  # two rows: no single authoritative rule


def test_rco_entry_canonicalization_and_hash_are_stable() -> None:
    version = DmnVersion(key=_SINTETICO, id="x", version=1, deployment_id="d")
    entry = version.to_audit_dict({"idRegra": "K1-R-001"})
    first = _record({_SINTETICO: entry})
    reordered = _record({_SINTETICO: dict(reversed(list(entry.items())))})
    reloaded = _record(json.loads(json.dumps(first.dmn_versions)))  # jsonb round trip
    assert first.dmn_versions == {_SINTETICO: entry}
    assert first.record_hash == reordered.record_hash == reloaded.record_hash


def test_unpinned_rco_table_fails_closed_before_evaluation() -> None:
    fake = FakeDmnTransport()
    fake.register("rco_nao_fixada", [{"decisao": "liberar"}])
    with collect_dmn_versions() as collected, pytest.raises(RcoRulesPinError) as excinfo:
        evaluate_sync(fake, "rco_nao_fixada", {})
    assert fake.calls == []  # never evaluated
    assert collected == {}  # nothing fabricated
    assert excinfo.value.code == "ERR_DMN_RCO_PIN"
    with pytest.raises(RcoRulesPinError):
        DmnVersion(key="rco_nao_fixada", id="x", version=1, deployment_id="d").to_audit_dict()


def test_coded_pin_error_becomes_a_non_retried_incident() -> None:
    def _fn() -> dict[str, Any]:
        raise RcoRulesPinError("x")

    with pytest.raises(ValueError, match="ERR_DMN_RCO_PIN"):
        reclassify_coded_exception(_fn)


def _write_pin(tmp_path: Path, *, sha: str, estado: str = "sem_pacote") -> tuple[Path, Path]:
    rules_dir = tmp_path / "rco"
    rules_dir.mkdir()
    (rules_dir / "rco_exemplo_sintetico.dmn").write_bytes((_REPO / _SINTETICO_REL).read_bytes())
    lock = json.loads((_REPO / RCO_RULES_PIN_RELATIVE_PATH).read_text(encoding="utf-8"))
    lock["estado"] = estado
    lock["exemplos_sinteticos"] = [{"path": _SINTETICO_REL, "sha256": sha}]
    lock_path = tmp_path / "rules.lock.json"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return lock_path, rules_dir


def test_pin_loader_maps_decision_to_sha(tmp_path: Path) -> None:
    lock_path, rules_dir = _write_pin(tmp_path, sha=_lock_sha(_SINTETICO_REL))
    pin = load_rco_rules_pin(lock_path, rules_dir=rules_dir)
    assert pin.tables[_SINTETICO].sha_dmn == _lock_sha(_SINTETICO_REL)
    assert pin.tables[_SINTETICO].regras is None  # sem manifesto: versaoRegra nunca inventada


@pytest.mark.parametrize(
    ("sha", "estado", "remove_file"),
    [
        ("sha256:" + "0" * 64, "sem_pacote", False),  # pin sha diverges from the bytes on board
        ("nao-e-sha", "sem_pacote", False),  # sha missing/malformed in the pin
        ("PLACEHOLDER_RAW_HEX", "sem_pacote", False),  # raw hex (no `sha256:` prefix) is refused
        (None, "sem_pacote", True),  # listed file absent
        (None, "fixado", False),  # `fixado` with no K1 manifest pinned
        (None, "outro", False),
    ],
)
def test_pin_loader_fails_closed(tmp_path: Path, sha: str | None, estado: str, remove_file: bool) -> None:
    if sha == "PLACEHOLDER_RAW_HEX":
        sha = _lock_sha(_SINTETICO_REL).removeprefix("sha256:")
    lock_path, rules_dir = _write_pin(tmp_path, sha=sha or _lock_sha(_SINTETICO_REL), estado=estado)
    if remove_file:
        (rules_dir / "rco_exemplo_sintetico.dmn").unlink()
    with pytest.raises(RcoRulesPinError):
        load_rco_rules_pin(lock_path, rules_dir=rules_dir)


@pytest.mark.parametrize(
    "id_regra",
    [
        "x" * 65,  # too long
        "regra 1",  # space
        "regração",  # accents
        "K1-R-001\nnome=Maria",  # injected text
        "123.456.789-00 Joao",  # echoed case data
        7,
    ],
)
def test_rco_id_regra_out_of_pattern_fails_closed_and_is_never_written(id_regra: object) -> None:
    version = DmnVersion(key=_SINTETICO, id="x", version=1, deployment_id="d")
    with pytest.raises(RcoRulesPinError) as excinfo:
        version.to_audit_dict({"idRegra": id_regra})
    assert excinfo.value.code == "ERR_DMN_RCO_PIN"
    if isinstance(id_regra, str) and id_regra:
        assert id_regra not in str(excinfo.value)

    fake = FakeDmnTransport()
    fake.register(_SINTETICO, [{"idRegra": id_regra}])
    with collect_dmn_versions() as collected, pytest.raises(RcoRulesPinError):
        evaluate_sync(fake, _SINTETICO, {})
    assert collected == {}


# ---------------------------------------------------------------------------------------------
# MZO-2a (contrato-k1.md §11): estado `fixado` lido com o manifesto K1 — pacote SINTETICO
# ---------------------------------------------------------------------------------------------


def _fixado(tmp_path: Path) -> tuple[Path, Path]:
    sint.montar(tmp_path)
    return tmp_path / sint.LOCK_REL, tmp_path / sint.RULES_DIR


def test_fixado_loads_the_package_and_versao_regra_comes_from_the_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path, rules_dir = _fixado(tmp_path)
    pin = load_rco_rules_pin(lock_path, rules_dir=rules_dir)
    assert pin.estado == "fixado"
    # formato 2.0.0: os numeros de D1/D2 sao lidos do vocabulario.json pinado e so expostos.
    assert pin.parametros_ativacao == rco_manifesto.ParametrosAtivacao(**sint.PARAMETROS_ATIVACAO)
    assert set(pin.tables) == {"rco_sint_a", "rco_sint_b"}
    manifesto = json.loads((tmp_path / sint.K1_DIR / "manifesto.json").read_bytes())
    sha_a = manifesto["decisoes"][0]["sha256"]
    assert pin.tables["rco_sint_a"].sha_dmn == sha_a  # shaDmn = decisoes[].sha256
    regras = pin.tables["rco_sint_a"].regras
    assert regras is not None
    assert regras["SINT-A-0002"].sha256_yaml == manifesto["decisoes"][0]["regras"][1]["sha256_yaml"]

    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    version = DmnVersion(key="rco_sint_a", id="x", version=3, deployment_id="d")
    assert version.to_audit_dict({"idRegra": "SINT-A-0002"}) == {
        "version": 3,
        "id": "x",
        "deploymentId": "d",
        "shaDmn": sha_a,
        "idRegra": "SINT-A-0002",
        "versaoRegra": "1.0.1",
        # MAJOR 2 (ADR-0077 §4): o pacote sintetico fixado so tem `plena` sem aditivo (o resto e recusado).
        "ativacaoPacote": "plena",
        "exigeAditivo": "false",
        # 5c-2 (ADR-0077 §4): estado efetivo no instante da decisao; o arquivo de estado comitado nao tem
        # entradas (toda regra em sombra) e o indicador de efeito esta desligado.
        "estadoManual": "sombra",
        "ativacaoEfetiva": "plena",
        "modo": "sombra",
    }
    # Sem linha casada unica nao ha idRegra, logo nao ha versaoRegra (ausente, nunca inventada).
    assert set(version.to_audit_dict(None)) == {"version", "id", "deploymentId", "shaDmn"}
    # idRegra que o manifesto nao lista para a decisao: recusado, nada gravado.
    with pytest.raises(RcoRulesPinError, match="regras"):
        version.to_audit_dict({"idRegra": "SINT-B-0001"})


def test_fixado_keeps_maezo_synthetic_examples_without_versao_regra(tmp_path: Path) -> None:
    lock_path, rules_dir = _fixado(tmp_path)
    (rules_dir / "rco_exemplo_sintetico.dmn").write_bytes((_REPO / _SINTETICO_REL).read_bytes())
    lock = sint.lock(tmp_path)
    lock["exemplos_sinteticos"] = [{"path": _SINTETICO_REL, "sha256": _lock_sha(_SINTETICO_REL)}]
    sint.gravar_lock(tmp_path, lock)
    pin = load_rco_rules_pin(lock_path, rules_dir=rules_dir)
    assert pin.tables[_SINTETICO].regras is None
    assert pin.tables[_SINTETICO].audit_fields("K1-R-001") == {"shaDmn": _lock_sha(_SINTETICO_REL)}


def _sem_sujo(m: dict[str, Any]) -> None:
    m["origem"]["sujo"] = True


@pytest.mark.parametrize(
    "estragar",
    [
        # manifesto: digest do pin divergente
        lambda root: (root / sint.K1_DIR / "manifesto.json").write_bytes(b"{}"),
        # manifesto recusado pelo leitor, mesmo repinado
        lambda root: sint.regravar(root, {**_manifesto(root), "campo_novo": 1}),
        lambda root: sint.regravar(root, _mutado(root, _sem_sujo)),
        lambda root: sint.regravar(root, {**_manifesto(root), "formato_manifesto": "3.0.0"}),
        # bloco `ativacao` do vocabulario.json fora do contrato (repinado: so o leitor recusa)
        lambda root: sint.trocar_vocabulario(
            root, _manifesto(root), sint.vocabulario_bytes(2, valores=["plena", "conferida"])
        ),
        # anexo adulterado
        lambda root: (root / sint.K1_DIR / "k6-filas.json").write_bytes(b"{}"),
        # DMN adulterado
        lambda root: (root / sint.RULES_DIR / "sint_a.dmn").write_bytes(b"<x/>"),
        # artifacts diferentes de decisoes[]
        lambda root: sint.gravar_lock(
            root, {**sint.lock(root), "artifacts": sint.lock(root)["artifacts"][:1]}
        ),
        # provenance diferente da origem
        lambda root: sint.gravar_lock(
            root,
            {**sint.lock(root), "provenance": {**sint.lock(root)["provenance"], "rco_commit_sha": "f" * 40}},
        ),
        # manifest_pin fora do caminho fixo
        lambda root: sint.gravar_lock(
            root,
            {
                **sint.lock(root),
                "manifest_pin": {**sint.lock(root)["manifest_pin"], "path": "x/manifesto.json"},
            },
        ),
        # lock no formato antigo
        lambda root: sint.gravar_lock(
            root, {**sint.lock(root), "lock_format": {**sint.lock(root)["lock_format"], "version": "1.0.0"}}
        ),
    ],
)
def test_fixado_fails_closed(tmp_path: Path, estragar: Any) -> None:
    lock_path, rules_dir = _fixado(tmp_path)
    load_rco_rules_pin(lock_path, rules_dir=rules_dir)  # integro: carrega
    estragar(tmp_path)
    with pytest.raises(RcoRulesPinError) as excinfo:
        load_rco_rules_pin(lock_path, rules_dir=rules_dir)
    assert excinfo.value.code == "ERR_DMN_RCO_PIN"


def _manifesto(root: Path) -> dict[str, Any]:
    return dict(json.loads((root / sint.K1_DIR / "manifesto.json").read_bytes()))


def _mutado(root: Path, mutar: Any) -> dict[str, Any]:
    m = _manifesto(root)
    mutar(m)
    return m


def test_fixado_major1_has_no_activation_parameters(tmp_path: Path) -> None:
    sint.montar(tmp_path, major=1)
    pin = load_rco_rules_pin(tmp_path / sint.LOCK_REL, rules_dir=tmp_path / sint.RULES_DIR)
    assert pin.parametros_ativacao is None
    # MAJOR 1: a regra nao tem ativacao/exige_aditivo, logo a auditoria nao os grava (ausentes, nunca null).
    campos = pin.tables["rco_sint_a"].audit_fields("SINT-A-0001")
    assert set(campos) == {"shaDmn", "versaoRegra"}
    assert pin.tables["rco_sint_a"].regras is not None


# ---------------------------------------------------------------------------------------------
# Revisao de 2403c8ed: `""` em idRegra, ativacao nao ligada, runtime tao estrito quanto o gate
# ---------------------------------------------------------------------------------------------


def test_fixado_empty_id_regra_records_only_sha(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """O pacote real emite `""` em idRegra de linha nao adversa (ex.: liberar): ausente, nao erro."""
    lock_path, rules_dir = _fixado(tmp_path)
    pin = load_rco_rules_pin(lock_path, rules_dir=rules_dir)
    monkeypatch.setattr(rco_pin, "rco_rules_pin", lambda: pin)
    fields = rco_pin.rco_audit_fields("rco_sint_b", {"idRegra": "", "decisao": "liberar"})
    assert fields == {"shaDmn": pin.tables["rco_sint_b"].sha_dmn}


@pytest.mark.parametrize("ativacao", [("em_correcao", False), ("conferida", False), ("plena", True)])
def test_fixado_major2_with_unwired_activation_is_refused(tmp_path: Path, ativacao: tuple[str, bool]) -> None:
    sint.montar(tmp_path, ativacoes={"SINT-A-0001": ativacao})
    with pytest.raises(RcoRulesPinError, match="Onda 2 item 5") as excinfo:
        load_rco_rules_pin(tmp_path / sint.LOCK_REL, rules_dir=tmp_path / sint.RULES_DIR)
    assert excinfo.value.code == "ERR_DMN_RCO_PIN"


def _symlink_k1(root: Path) -> None:
    k1 = root / sint.K1_DIR
    real = root / "k1-real"
    k1.rename(real)
    k1.symlink_to(real, target_is_directory=True)


@pytest.mark.parametrize(
    "estragar",
    [
        lambda root: (root / sint.K1_DIR / "notas.txt").write_text("x", encoding="utf-8"),
        lambda root: (root / sint.RULES_DIR / "rco_intruso.dmn").write_bytes(
            (root / sint.RULES_DIR / "sint_a.dmn").read_bytes()
        ),
        lambda root: (root / sint.K1_DIR / "exemplos").mkdir(),
        _symlink_k1,
        lambda root: (root / sint.LOCK_REL).write_text(
            (root / sint.LOCK_REL)
            .read_text(encoding="utf-8")
            .replace('"estado": "fixado"', '"estado": "sem_pacote", "estado": "fixado"'),
            encoding="utf-8",
        ),
        lambda root: (root / sint.LOCK_REL).write_text(
            (root / sint.LOCK_REL).read_text(encoding="utf-8").replace('"PUBLISHED"', "NaN"),
            encoding="utf-8",
        ),
    ],
    ids=[
        "arquivo-solto-k1",
        "dmn-solto-rco",
        "exemplos-vendorizados",
        "k1-link",
        "lock-chave-dupla",
        "lock-nan",
    ],
)
def test_runtime_refuses_what_the_gate_refuses(tmp_path: Path, estragar: Any) -> None:
    from scripts.ci.verify_rco_rules_pin import main as gate

    lock_path, rules_dir = _fixado(tmp_path)
    load_rco_rules_pin(lock_path, rules_dir=rules_dir)
    estragar(tmp_path)
    assert gate(["--repo-root", str(tmp_path)]) == 1
    with pytest.raises(RcoRulesPinError):
        load_rco_rules_pin(lock_path, rules_dir=rules_dir)


def test_sem_pacote_runtime_refuses_stray_dmn(tmp_path: Path) -> None:
    lock_path, rules_dir = _write_pin(tmp_path, sha=_lock_sha(_SINTETICO_REL))
    load_rco_rules_pin(lock_path, rules_dir=rules_dir)
    (rules_dir / "rco_solto.dmn").write_bytes((rules_dir / "rco_exemplo_sintetico.dmn").read_bytes())
    with pytest.raises(RcoRulesPinError, match="rco_solto.dmn"):
        load_rco_rules_pin(lock_path, rules_dir=rules_dir)


@pytest.mark.parametrize("ativacao", [[], {}])
def test_fixado_unhashable_activation_is_coded_error(tmp_path: Path, ativacao: object) -> None:
    """Re-revisao de e70d9b79: `ativacao` lista/objeto -> ERR_DMN_RCO_PIN, nunca TypeError."""
    lock_path, rules_dir = _fixado(tmp_path)
    manifesto = _manifesto(tmp_path)
    manifesto["decisoes"][0]["regras"][0]["ativacao"] = ativacao
    sint.regravar(tmp_path, manifesto)
    with pytest.raises(RcoRulesPinError) as excinfo:
        load_rco_rules_pin(lock_path, rules_dir=rules_dir)
    assert excinfo.value.code == "ERR_DMN_RCO_PIN"


def test_audit_fields_major2_carry_activation_and_aditivo_as_text() -> None:
    """ADR-0077 §4: `ativacaoPacote`/`exigeAditivo` (texto) so quando a regra do manifesto os tem."""
    regras = {
        "R-1": rco_manifesto.RegraK1("R-1", "1.0.0", "a" * 64, "conferida", True),
        "R-2": rco_manifesto.RegraK1("R-2", "1.0.0", "b" * 64, "plena", False),
        "R-3": rco_manifesto.RegraK1("R-3", "1.0.0", "c" * 64),  # MAJOR 1: sem os dois campos
    }
    tabela = rco_pin.RcoPinnedTable("rco_x", "spec/x.dmn", "sha256:" + "0" * 64, regras)
    assert tabela.audit_fields("R-1") == {
        "shaDmn": tabela.sha_dmn,
        "versaoRegra": "1.0.0",
        "ativacaoPacote": "conferida",
        "exigeAditivo": "true",
    }
    assert tabela.audit_fields("R-2")["exigeAditivo"] == "false"
    assert set(tabela.audit_fields("R-3")) == {"shaDmn", "versaoRegra"}
    assert set(tabela.audit_fields()) == {"shaDmn"}
