"""CPT vigente do proprio sujeito (contrato `tina-cpt`, manifest aditivo v1.15; caso C1 do dono e DPO de
20/10/2026, DL-0130).

O artefato esta' PINADO (bloco `manifest_v1_15`, PUBLISHED em 20/10/2026): estes testes usam os BYTES REAIS
vendorizados em `config/integrations/amh/openapi/` contra o lock real. O que fica preso:

  * o digest do lock real = o dos bytes vendorizados; bytes fora do digest = o construtor RECUSA;
  * sem os bytes, `get_cpt_vigente` recusa `SCOPE_NOT_SUPPORTED`;
  * o pedido leva SO' `purpose_of_use` na query, na rota do proprio sujeito;
  * a conversao do corpo validado para `CptVigenteView` (com e sem data, sem CPT);
  * resposta de outro sujeito, fora do schema, incoerente, 404 ou 429 vira recusa fechada;
  * o executor PROPRIO do gateway (`AmhTinaCptExecutor`) so' com a finalidade fixa e o escopo TINA;
  * a composicao: com o caminho a rota entra; arquivo ilegivel ou fora do digest deixa SO' a CPT de fora
    (a cobertura continua).

Dados 100% sinteticos.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest
import structlog

from maezo.adapters.amh import tina
from maezo.adapters.amh.contract import load_contract_pin
from maezo.gateway.amh_interop import (
    SCOPE_BY_OPERATION,
    AmhInteropCompositionError,
    AmhTinaCoberturaExecutor,
    AmhTinaCptExecutor,
    AmhTinaExecutor,
    CognitoClientCredentials,
)
from maezo.gateway.tool_registry import build_amh_interop
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.tina import CptVigenteView, TinaCptPort
from tests.unit.adapters.amh.test_tina import Executor
from tests.unit.gateway.test_amh_interop import (
    ORIGIN,
    SECRET,
    TOKEN_URL,
    Audit,
    Servidor,
    _comum,
    _seam,
    _settings,
)
from tests.unit.gateway.test_amh_interop import relogio as relogio  # noqa: F401  (fixture)
from tests.unit.gateway.test_amh_interop import servidor as servidor  # noqa: F401  (fixture)

pytestmark = pytest.mark.anyio

_REAL = Path(__file__).resolve().parents[4] / "config" / "integrations" / "amh" / "openapi"
TINA_BYTES = (_REAL / "tina.openapi.yaml").read_bytes()
CPT_BYTES = (_REAL / "tina-cpt.openapi.yaml").read_bytes()
REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
OUTRO = "amh:psr:v1:2b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
_PATH = f"/interop/tina/v1/subjects/{REF}/cpt"
_PATH_PEDIDO = f"/interop/tina/v1/subjects/{quote(REF, safe='')}/cpt"
_BASE_LEGAL = "base-legal:execucao-de-contrato"


def _corpo(**sobre: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "portable_subject_ref": REF,
        "tem_cpt_vigente": True,
        "cpt_vigentes": 1,
        "termina_em": "2027-03-01",
        "natureza": "informacao_cadastral_nao_e_autorizacao",
        "fonte": {"tabela": "tina_carencia_tipo", "atualizada_em": "2026-10-20T12:00:00Z"},
    }
    corpo.update(sobre)
    return corpo


def _montar(corpo: dict[str, Any] | None = None) -> tuple[tina.AmhTinaAdapter, Executor]:
    executor = Executor()
    executor.bodies[tina.OP_CPT] = corpo if corpo is not None else _corpo()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor, cpt_openapi_bytes=CPT_BYTES)
    return adapter, executor


async def _ler(adapter: tina.AmhTinaAdapter, **sobre: Any) -> PortResult[CptVigenteView]:
    args: dict[str, Any] = {"purpose_of_use": tina.PURPOSE_CPT, "consent_decision_ref": _BASE_LEGAL}
    args.update(sobre)
    return await adapter.get_cpt_vigente(REF, **args)


# --- adaptador ---------------------------------------------------------------------------------------


def test_o_lock_real_tem_o_digest_do_v1_15_e_bytes_fora_dele_recusam() -> None:
    pin = load_contract_pin()
    assert tina.ARTIFACT_CPT == "schemas/openapi/maezo/v1/tina-cpt.openapi.yaml"
    assert pin.manifest_v1_15_digest is not None
    assert pin.artifact_digests[tina.ARTIFACT_CPT] == hashlib.sha256(CPT_BYTES).hexdigest()
    tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), cpt_openapi_bytes=CPT_BYTES)
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), cpt_openapi_bytes=CPT_BYTES + b"\n")
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), cpt_executor=object())  # type: ignore[arg-type]


async def test_sem_os_bytes_a_cpt_recusa_e_nada_sai() -> None:
    executor = Executor()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor)
    assert isinstance(adapter, TinaCptPort)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert executor.calls == []


async def test_o_pedido_leva_so_a_finalidade_na_rota_do_sujeito() -> None:
    adapter, executor = _montar()
    resultado = await _ler(adapter)
    assert resultado.succeeded and resultado.value is not None, resultado.failure
    [pedido] = executor.calls
    assert pedido.operation == tina.OP_CPT == "amh.tina_get_cpt_vigente"
    assert pedido.path == _PATH_PEDIDO
    assert pedido.query == (("purpose_of_use", "atendimento_whatsapp"),)
    assert pedido.request_ref is None


async def test_conversao_do_corpo_validado() -> None:
    adapter, _ = _montar()
    resultado = await _ler(adapter)
    assert resultado.value == CptVigenteView(
        portable_subject_ref=REF,
        tem_cpt_vigente=True,
        cpt_vigentes=1,
        termina_em="2027-03-01",
        fonte_atualizada_em="2026-10-20T12:00:00Z",
    )


@pytest.mark.parametrize(
    ("corpo", "tem", "termina"),
    [
        (_corpo(tem_cpt_vigente=False, cpt_vigentes=0, termina_em=None), False, None),
        (_corpo(cpt_vigentes=2, termina_em=None), True, None),
        (_corpo(fonte={"tabela": "tina_carencia_tipo", "atualizada_em": None}), True, "2027-03-01"),
    ],
)
async def test_sem_cpt_ou_sem_data_sao_estados_validos(
    corpo: dict[str, Any], tem: bool, termina: Any
) -> None:
    adapter, _ = _montar(corpo)
    resultado = await _ler(adapter)
    assert resultado.value is not None, resultado.failure
    assert resultado.value.tem_cpt_vigente is tem and resultado.value.termina_em == termina


@pytest.mark.parametrize(
    "corpo",
    [
        _corpo(portable_subject_ref=OUTRO),  # OUTRO sujeito: nunca um fato
        _corpo(natureza="autorizacao"),
        _corpo(cid="X00"),  # campo fora do contrato fechado
        _corpo(termina_em="01/03/2027"),
        _corpo(cpt_vigentes=-1),
        _corpo(tem_cpt_vigente="sim"),
        _corpo(fonte={"tabela": "outra", "atualizada_em": None}),
        # incoerentes (o schema nao expressa; o adaptador recusa):
        _corpo(tem_cpt_vigente=False, cpt_vigentes=1, termina_em=None),
        _corpo(tem_cpt_vigente=False, cpt_vigentes=0, termina_em="2027-03-01"),
        _corpo(tem_cpt_vigente=True, cpt_vigentes=0),
    ],
)
async def test_resposta_fora_do_contrato_vira_recusa_fechada(corpo: dict[str, Any]) -> None:
    adapter, _ = _montar(corpo)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize(("status", "motivo"), [(404, Reason.NOT_FOUND), (429, Reason.RATE_LIMITED)])
async def test_404_e_429_viram_recusa(status: int, motivo: Reason) -> None:
    adapter, executor = _montar()
    executor.status = status
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is motivo


async def test_finalidade_fora_do_enum_nunca_sai() -> None:
    adapter, executor = _montar()
    resultado = await _ler(adapter, purpose_of_use="sharing_amh_internal")
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


async def test_um_executor_proprio_carrega_so_a_cpt() -> None:
    principal = Executor()
    proprio = Executor()
    proprio.bodies[tina.OP_CPT] = _corpo()
    adapter = tina.AmhTinaAdapter(
        TINA_BYTES, executor=principal, cpt_openapi_bytes=CPT_BYTES, cpt_executor=proprio
    )
    assert (await _ler(adapter)).succeeded
    assert [c.operation for c in proprio.calls] == [tina.OP_CPT]
    assert principal.calls == []


# --- executor real do gateway (sem rede) -------------------------------------------------------------


def _tokens(servidor: Servidor, relogio: list[float], *escopos: str) -> CognitoClientCredentials:
    return CognitoClientCredentials(
        token_url=TOKEN_URL,
        client_id="maezo-operadora-interop",
        client_secret=SECRET,
        scopes=escopos or ("interop/tina.read",),
        transport_factory=servidor.factory,
        clock=lambda: relogio[0],
    )


def _pedido(**sobre: Any) -> tina.GovernedTinaRequest:
    base: dict[str, Any] = {
        "operation": tina.OP_CPT,
        "path": _PATH_PEDIDO,
        "query": (("purpose_of_use", "atendimento_whatsapp"),),
        "consent_decision_ref": _BASE_LEGAL,
        "timeout_seconds": 5.0,
    }
    base.update(sobre)
    return tina.GovernedTinaRequest(**base)


def _executor(servidor: Servidor, relogio: list[float], audit: Audit) -> AmhTinaCptExecutor:
    return AmhTinaCptExecutor(
        **_comum(servidor, _tokens(servidor, relogio), audit, purpose_of_use="atendimento_whatsapp")
    )


def test_escopo_registro_e_composicao_do_executor_proprio(servidor: Servidor, relogio: list[float]) -> None:
    assert SCOPE_BY_OPERATION[tina.OP_CPT] == "interop/tina.read"
    assert tina.OP_CPT not in AmhTinaExecutor.registered_operations
    assert tina.OP_CPT not in AmhTinaCoberturaExecutor.registered_operations
    assert AmhTinaCptExecutor.registered_operations == frozenset({tina.OP_CPT})
    with pytest.raises(AmhInteropCompositionError):  # finalidade de sempre: recusado
        AmhTinaCptExecutor(**_comum(servidor, _tokens(servidor, relogio), Audit()))
    with pytest.raises(AmhInteropCompositionError):  # token sem `interop/tina.read`: recusado
        AmhTinaCptExecutor(
            **_comum(
                servidor,
                _tokens(servidor, relogio, "interop/billing.read"),
                Audit(),
                purpose_of_use="atendimento_whatsapp",
            )
        )
    _executor(servidor, relogio, Audit())


async def test_executor_audita_antes_e_despacha_na_rota_fixa(
    servidor: Servidor, relogio: list[float]
) -> None:
    servidor.respostas[_PATH] = httpx.Response(200, json=_corpo())
    audit = Audit(servidor)
    resultado = await _executor(servidor, relogio, audit).execute(_pedido())
    assert resultado.succeeded, resultado.failure
    [pedido] = servidor.ao_servico()
    assert pedido.url.path == _PATH
    assert dict(pedido.url.params) == {"purpose_of_use": "atendimento_whatsapp"}
    assert str(pedido.url).startswith(ORIGIN)
    assert audit.pedidos_no_emit == [0]  # auditoria ANTES do despacho
    [registro] = audit.registros
    assert registro.action == tina.OP_CPT


@pytest.mark.parametrize(
    "sobre",
    [
        {"query": (("purpose_of_use", "atendimento_whatsapp"), ("limite", 5))},
        {"query": (("purpose_of_use", "sharing_amh_internal"),)},
        {"query": ()},
        {"path": f"/interop/tina/v1/subjects/{quote(REF, safe='')}/cobertura"},
        {"operation": tina.OP_COBERTURA},
        {"request_ref": "hk1_msg_cpt_000001"},
    ],
)
async def test_executor_nunca_despacha_pedido_fora_da_forma(
    servidor: Servidor, relogio: list[float], sobre: dict[str, Any]
) -> None:
    audit = Audit()
    resultado = await _executor(servidor, relogio, audit).execute(_pedido(**sobre))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor.ao_servico() == [] and audit.registros == []


# --- composicao (`tool_registry.build_amh_interop`) ----------------------------------------------------


def _settings_reais(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **sobre: Any) -> Any:
    from maezo.adapters.amh import billing_status as bs
    from maezo.adapters.amh import subject_resolution as sr

    class _Adapter:
        def __init__(self, *_: Any, **__: Any) -> None:
            pass

    monkeypatch.setattr(bs, "AmhBillingStatusAdapter", _Adapter)
    monkeypatch.setattr(sr, "AmhSubjectResolutionAdapter", _Adapter)
    base: dict[str, Any] = {
        "amh_interop_scopes": (
            "interop/billing.read interop/subject.resolve interop/profile.read interop/tina.read"
        ),
        "amh_tina_openapi_path": str(_REAL / "tina.openapi.yaml"),
        "amh_tina_cobertura_openapi_path": str(_REAL / "tina-cobertura.openapi.yaml"),
    }
    base.update(sobre)
    return _settings(tmp_path, **base)


async def test_composicao_com_o_caminho_liga_a_rota_ponta_a_ponta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, servidor: Servidor
) -> None:
    servidor.respostas[_PATH] = httpx.Response(200, json=_corpo())
    audit = Audit()
    interop = build_amh_interop(
        settings=_settings_reais(
            tmp_path, monkeypatch, amh_tina_cpt_openapi_path=str(_REAL / "tina-cpt.openapi.yaml")
        ),
        seam=_seam(),
        audit=audit,
        agent_version="helena@v0",
        transport_factory=servidor.factory,
        incluir_tina=True,
    )
    # billing + subjects + TINA + cobertura + CPT
    assert interop.tina is not None and len(interop._executors) == 5
    resultado = await interop.tina.get_cpt_vigente(
        REF, purpose_of_use="atendimento_whatsapp", consent_decision_ref=_BASE_LEGAL
    )
    assert resultado.succeeded and resultado.value is not None, resultado.failure
    assert resultado.value.tem_cpt_vigente is True
    assert [r.action for r in audit.registros] == [tina.OP_CPT]
    await interop.aclose()


async def test_composicao_sem_o_caminho_nao_tem_a_rota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interop = build_amh_interop(
        settings=_settings_reais(tmp_path, monkeypatch, amh_tina_cpt_openapi_path=""),
        seam=_seam(),
        audit=Audit(),
        agent_version="helena@v0",
        incluir_tina=True,
    )
    assert interop.tina is not None and len(interop._executors) == 4
    resultado = await interop.tina.get_cpt_vigente(
        REF, purpose_of_use="atendimento_whatsapp", consent_decision_ref=_BASE_LEGAL
    )
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED


@pytest.mark.parametrize("caso", ["ilegivel", "fora_do_digest"])
async def test_falha_de_leitura_ou_digest_deixa_so_a_cpt_de_fora(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caso: str
) -> None:
    caminho = tmp_path / "cpt.yaml"
    if caso == "fora_do_digest":
        caminho.write_bytes(CPT_BYTES + b"\n")
    with structlog.testing.capture_logs() as logs:
        interop = build_amh_interop(
            settings=_settings_reais(tmp_path, monkeypatch, amh_tina_cpt_openapi_path=str(caminho)),
            seam=_seam(),
            audit=Audit(),
            agent_version="helena@v0",
            incluir_tina=True,
        )
    # a cobertura CONTINUA (billing + subjects + TINA + cobertura); so' a CPT fica de fora
    assert interop.tina is not None and len(interop._executors) == 4
    assert [e for e in logs if e["event"] == "amh_interop_cobertura_fora"] == []
    resultado = await interop.tina.get_cpt_vigente(
        REF, purpose_of_use="atendimento_whatsapp", consent_decision_ref=_BASE_LEGAL
    )
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    [erro] = [e for e in logs if e["event"] == "amh_interop_cpt_fora"]
    assert erro["log_level"] == "error"
    assert str(tmp_path) not in repr(erro)  # o caminho nunca vai a log
