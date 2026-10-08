"""Executor real das leituras TINA (fatos do plano) e a composicao com `incluir_tina` — sem rede.

Prende a decisao do dono de 07/10/2026: mesmo caminho governado do billing-status/subject-resolution
(gate em sombra, Zona Geral, auditoria duravel ANTES do despacho, base legal so' como hash, recusa
fechada), escopo `interop/tina.read` exigido na composicao e o adaptador TINA fail-closed sem o pin v1.2.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

from maezo.adapters.amh import tina as tina_mod
from maezo.adapters.amh.contract import load_contract_pin
from maezo.adapters.amh.tina import GovernedTinaRequest
from maezo.gateway.amh_interop import (
    SCOPE_BY_OPERATION,
    SCOPE_TINA,
    AmhInteropCompositionError,
    AmhTinaExecutor,
)
from maezo.gateway.tool_registry import build_amh_interop
from maezo.ports.errors import PortFailureReason as Reason
from tests.unit.adapters.amh.test_tina import _eleg, _openapi
from tests.unit.gateway.test_amh_interop import (
    ORIGIN,
    PURPOSE,
    SECRET,
    Audit,
    Servidor,
    _comum,
    _seam,
    _settings,
)
from tests.unit.gateway.test_amh_interop import relogio as relogio  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop import servidor as servidor  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop import tokens as tokens  # noqa: F401  (fixture)

pytestmark = pytest.mark.anyio

ELEG_PATH = "/interop/tina/v1/subjects/subject1/elegibilidade"
_BASE_LEGAL = "base-legal:execucao-de-contrato"


def _pedido(**sobre: Any) -> GovernedTinaRequest:
    base: dict[str, Any] = {
        "operation": tina_mod.OP_ELEGIBILIDADE,
        "path": ELEG_PATH,
        "query": (("purpose_of_use", PURPOSE),),
        "consent_decision_ref": _BASE_LEGAL,
        "timeout_seconds": 5.0,
    }
    base.update(sobre)
    return GovernedTinaRequest(**base)


def test_as_tres_operacoes_pedem_o_escopo_tina() -> None:
    assert SCOPE_TINA == "interop/tina.read"
    for op in tina_mod.OPERATIONS:
        assert SCOPE_BY_OPERATION[op] == SCOPE_TINA
    assert AmhTinaExecutor.registered_operations == tina_mod.OPERATIONS


async def test_leitura_tina_audita_antes_e_despacha_na_rota_fixa(servidor: Servidor, tokens: Any) -> None:
    audit = Audit(servidor)
    executor = AmhTinaExecutor(**_comum(servidor, tokens, audit))
    resultado = await executor.execute(_pedido())
    assert resultado.succeeded, resultado.failure
    [pedido] = servidor.ao_servico()
    assert str(pedido.url) == ORIGIN + ELEG_PATH + "?purpose_of_use=sharing_amh_internal"
    assert audit.pedidos_no_emit == [0]  # auditoria ANTES do despacho
    [registro] = audit.registros
    assert registro.action == tina_mod.OP_ELEGIBILIDADE
    assert "base-legal" not in json.dumps(registro.details)  # so' o hash da base legal


async def test_requisicoes_com_limite_e_carencias_com_pendentes(servidor: Servidor, tokens: Any) -> None:
    executor = AmhTinaExecutor(**_comum(servidor, tokens, Audit()))
    r1 = await executor.execute(
        _pedido(
            operation=tina_mod.OP_REQUISICOES,
            path="/interop/tina/v1/subjects/subject1/requisicoes",
            query=(("purpose_of_use", PURPOSE), ("limite", 5)),
        )
    )
    r2 = await executor.execute(
        _pedido(
            operation=tina_mod.OP_CARENCIAS,
            path="/interop/tina/v1/subjects/subject1/carencias",
            query=(("purpose_of_use", PURPOSE), ("pendentes", "true")),
        )
    )
    assert r1.succeeded and r2.succeeded
    urls = [str(p.url) for p in servidor.ao_servico()]
    assert urls[0].endswith("/requisicoes?purpose_of_use=sharing_amh_internal&limite=5")
    assert urls[1].endswith("/carencias?purpose_of_use=sharing_amh_internal&pendentes=true")


@pytest.mark.parametrize(
    "sobre",
    [
        {"path": "/interop/tina/v1/subjects/subject1/carencias"},  # rota de outra operacao
        {"path": "/interop/tina/v1/subjects/../admin/elegibilidade"},
        {"query": (("purpose_of_use", "outro"),)},
        {"query": (("purpose_of_use", PURPOSE), ("limite", 5))},  # limite nao existe nesta rota
        {"operation": "amh.get_billing_status"},
        {"consent_decision_ref": " "},
        {"timeout_seconds": 999.0},
    ],
)
async def test_pedido_fora_da_forma_nunca_e_despachado(
    servidor: Servidor, tokens: Any, sobre: dict[str, Any]
) -> None:
    audit = Audit()
    executor = AmhTinaExecutor(**_comum(servidor, tokens, audit))
    resultado = await executor.execute(_pedido(**sobre))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor.ao_servico() == [] and audit.registros == []


@pytest.mark.parametrize(("status", "reason"), [(404, Reason.NOT_FOUND), (500, Reason.UPSTREAM_UNAVAILABLE)])
async def test_status_do_servico_vira_recusa_fechada(
    servidor: Servidor, tokens: Any, status: int, reason: Reason
) -> None:
    servidor.respostas[ELEG_PATH] = httpx.Response(status, json={"detalhe": "nao vaza"})
    executor = AmhTinaExecutor(**_comum(servidor, tokens, Audit()))
    resultado = await executor.execute(_pedido())
    assert resultado.failure is not None and resultado.failure.reason is reason


# --- composicao ----------------------------------------------------------------------------------


def _settings_tina(tmp_path: Path, **sobre: Any) -> Any:
    tina_path = tmp_path / "tina.yaml"
    tina_path.write_bytes(b"z")
    base = {
        "amh_interop_scopes": (
            "interop/billing.read interop/subject.resolve interop/profile.read interop/tina.read"
        ),
        "amh_tina_openapi_path": str(tina_path),
    }
    base.update(sobre)
    return _settings(tmp_path, **base)


def test_composicao_com_tina_sem_o_caminho_do_openapi_recusa(tmp_path: Path) -> None:
    with pytest.raises(AmhInteropCompositionError, match="amh_tina_openapi_path") as exc:
        build_amh_interop(
            settings=_settings_tina(tmp_path, amh_tina_openapi_path=None),
            seam=_seam(),
            audit=Audit(),
            agent_version="helena@v0",
            incluir_tina=True,
        )
    assert SECRET not in str(exc.value)


def test_composicao_com_tina_sem_o_escopo_recusa(tmp_path: Path) -> None:
    with pytest.raises(AmhInteropCompositionError, match="escopos"):
        build_amh_interop(
            settings=_settings_tina(
                tmp_path,
                amh_interop_scopes="interop/billing.read interop/subject.resolve interop/profile.read",
            ),
            seam=_seam(),
            audit=Audit(),
            agent_version="helena@v0",
            incluir_tina=True,
        )


def test_composicao_sem_tina_nao_le_nada_da_tina(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`incluir_tina=False` (o default): nem o caminho nem o escopo TINA sao exigidos."""
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr

    class _Adapter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(bs, "AmhBillingStatusAdapter", _Adapter)
    monkeypatch.setattr(sr, "AmhSubjectResolutionAdapter", _Adapter)
    interop = build_amh_interop(
        settings=_settings(tmp_path), seam=_seam(), audit=Audit(), agent_version="lucas@v0"
    )
    assert interop.tina is None
    assert len(interop._executors) == 2


def test_composicao_com_tina_sem_pin_v1_2_recusa_pelo_contrato(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O lock real NAO tem `manifest_v1_2` (DRAFT): o adaptador TINA recusa, e com ele o boot."""
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr

    class _Adapter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(bs, "AmhBillingStatusAdapter", _Adapter)
    monkeypatch.setattr(sr, "AmhSubjectResolutionAdapter", _Adapter)
    (tmp_path / "t.yaml").write_bytes(_openapi())
    with pytest.raises(tina_mod.TinaContractError):
        build_amh_interop(
            settings=_settings_tina(tmp_path, amh_tina_openapi_path=str(tmp_path / "t.yaml")),
            seam=_seam(),
            audit=Audit(),
            agent_version="helena@v0",
            incluir_tina=True,
        )


async def test_ponta_a_ponta_tina_pinada_sobre_o_executor_real(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servidor: Servidor
) -> None:
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr

    class _Adapter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(bs, "AmhBillingStatusAdapter", _Adapter)
    monkeypatch.setattr(sr, "AmhSubjectResolutionAdapter", _Adapter)
    raw = _openapi().replace(b"purpose1", PURPOSE.encode())
    real_pin = load_contract_pin()
    monkeypatch.setattr(
        tina_mod,
        "load_contract_pin",
        lambda path=None: replace(
            real_pin, artifact_digests={tina_mod.ARTIFACT: hashlib.sha256(raw).hexdigest()}
        ),
    )
    (tmp_path / "t.yaml").write_bytes(raw)
    servidor.respostas[ELEG_PATH] = httpx.Response(200, json=_eleg())
    audit = Audit()
    interop = build_amh_interop(
        settings=_settings_tina(tmp_path, amh_tina_openapi_path=str(tmp_path / "t.yaml")),
        seam=_seam(),
        audit=audit,
        agent_version="helena@v0",
        transport_factory=servidor.factory,
        incluir_tina=True,
    )
    assert interop.tina is not None and len(interop._executors) == 3
    resultado = await interop.tina.get_elegibilidade(
        "subject1", purpose_of_use=PURPOSE, consent_decision_ref=_BASE_LEGAL
    )
    assert resultado.succeeded, resultado.failure
    assert resultado.value is not None and resultado.value.vinculos[0].carteirinha_mascarada == "******4567"
    assert [r.action for r in audit.registros] == [tina_mod.OP_ELEGIBILIDADE]
    # o token foi pedido com o escopo TINA
    [token_req] = [p for p in servidor.pedidos if p not in servidor.ao_servico()]
    assert "interop%2Ftina.read" in token_req.content.decode()
    await interop.aclose()


# --- governanca: catalogo, acao L0 e agent.yaml da Helena ---------------------------------------


def test_operacoes_tina_no_catalogo_e_acao_l3_nao_hard_declarada_na_helena() -> None:
    import yaml

    from maezo.gateway.effect_classes import OPERATIONS
    from maezo.gateway.pep import HARD_ACTIONS, PEP, Decision, Level, load_matrix

    raiz = Path(__file__).resolve().parents[3]
    for operacao in tina_mod.OPERATIONS:
        spec = OPERATIONS[operacao]
        assert spec.action_class == "leitura_phi_clinica"
        assert spec.autonomy_action == "read_member_plan_facts"
        assert spec.tool_id is None
    matriz = load_matrix(raiz / "spec/policies/autonomy/L0-core.yaml", tenant="helena-tina-unit")
    politica = matriz.policy_for("read_member_plan_facts")
    assert politica is not None and politica.level is Level.L3 and politica.hard is False
    assert "read_member_plan_facts" not in HARD_ACTIONS
    assert PEP(matriz).evaluate("read_member_plan_facts") is Decision.ALLOW
    aprovacoes = yaml.safe_load(
        (raiz / "spec/policies/autonomy/action-approvals.yaml").read_text(encoding="utf-8")
    )
    for operacao in tina_mod.OPERATIONS:
        assert aprovacoes["mapeamento_acoes"][f"agente.{operacao}"] == "leitura_phi_clinica"
    helena = yaml.safe_load((raiz / "spec/agents/helena/agent.yaml").read_text(encoding="utf-8"))
    assert "read_member_plan_facts" in helena["autonomy_actions"]
    assert helena["security_zone"] == "general"
