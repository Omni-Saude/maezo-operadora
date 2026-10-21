"""Cobertura por procedimento (contrato `tina-cobertura`, manifest aditivo v1.9; decisao do dono D1 de
18/10/2026).

O artefato esta' PINADO (bloco `manifest_v1_9`, PUBLISHED em 16/10/2026): estes testes usam os BYTES REAIS
vendorizados em `config/integrations/amh/openapi/` contra o lock real. O que fica preso:

  * o digest do lock real = o dos bytes vendorizados; bytes fora do digest = o construtor RECUSA;
  * sem os bytes, `get_cobertura` recusa `SCOPE_NOT_SUPPORTED` e o adaptador de sempre nao muda;
  * o pedido leva `purpose_of_use`, `procedimento` e `limite` na query, na rota do proprio sujeito; termo
    fora do padrao do contrato, `limite` fora de 1..10 ou finalidade outra nunca sai;
  * a conversao do corpo validado para `CoberturaView` (sem codigo, origem nem descricao de carencia);
  * resposta de outro sujeito, fora do schema ou 404 vira recusa fechada;
  * o executor PROPRIO do gateway (`AmhTinaCoberturaExecutor`) so' com a finalidade fixa e o escopo TINA;
    rota, query permitida e `limite` 1..10 conferidos de novo;
  * a composicao: com o caminho a rota entra; arquivo ilegivel ou fora do digest deixa SO' a cobertura de
    fora (o resto sobe).

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
    AmhTinaExecutor,
    CognitoClientCredentials,
)
from maezo.gateway.tool_registry import build_amh_interop
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.tina import CoberturaView, TinaCoberturaPort
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
COBERTURA_BYTES = (_REAL / "tina-cobertura.openapi.yaml").read_bytes()
REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
OUTRO = "amh:psr:v1:2b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
_PATH = f"/interop/tina/v1/subjects/{REF}/cobertura"
#: O caminho como o adaptador o monta (sujeito percent-codificado): e' o que o executor confere.
_PATH_PEDIDO = f"/interop/tina/v1/subjects/{quote(REF, safe='')}/cobertura"
_BASE_LEGAL = "base-legal:execucao-de-contrato"


def _item(**sobre: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "codigo": 40101010,
        "origem": "tuss",
        "descricao": "Procedimento Exemplo",
        "situacao": "coberto_rol",
        "coberto": True,
        "no_rol": True,
        "na_segmentacao": True,
        "rol_edicao": "ROL DE PROCEDIMENTOS 2021 - RN Nº 465/2021",
        "cobertura_extra": False,
        "cobertura_extra_descricao": None,
        "diretriz_de_utilizacao": True,
        "carencias": [
            {
                "carencia": "Carencia Exemplo",
                "regra": "produto",
                "dias": 180,
                "termina_em": "2026-12-01",
                "cumprida": False,
            }
        ],
    }
    item.update(sobre)
    return item


def _corpo(**sobre: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "portable_subject_ref": REF,
        "natureza": "informacao_cadastral_nao_e_autorizacao",
        "plano": {
            "descricao": "Plano Exemplo",
            "segmentacao": "Ambulatorial + Hospitalar com Obstetrícia",
            "regulamentado": True,
        },
        "itens": [_item()],
        "truncada": False,
        "avisos": ["sujeito_a_autorizacao", "sujeito_a_diretriz_de_utilizacao"],
        "fonte": {"tabela": "tina_cobertura_procedimento", "atualizada_em": "2026-10-15T12:00:00Z"},
        "qualidade": {"campos_ausentes": []},
    }
    corpo.update(sobre)
    return corpo


def _montar(corpo: dict[str, Any] | None = None) -> tuple[tina.AmhTinaAdapter, Executor]:
    executor = Executor()
    executor.bodies[tina.OP_COBERTURA] = corpo if corpo is not None else _corpo()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor, cobertura_openapi_bytes=COBERTURA_BYTES)
    return adapter, executor


async def _ler(adapter: tina.AmhTinaAdapter, **sobre: Any) -> PortResult[CoberturaView]:
    args: dict[str, Any] = {
        "purpose_of_use": tina.PURPOSE_COBERTURA,
        "consent_decision_ref": _BASE_LEGAL,
        "procedimento": "ressonancia magnetica",
    }
    args.update(sobre)
    return await adapter.get_cobertura(REF, **args)


# --- adaptador ---------------------------------------------------------------------------------------


def test_o_lock_real_tem_o_digest_do_v1_9_e_bytes_fora_dele_recusam() -> None:
    pin = load_contract_pin()
    assert tina.ARTIFACT_COBERTURA == "schemas/openapi/maezo/v1/tina-cobertura.openapi.yaml"
    assert pin.manifest_v1_9_digest is not None
    assert pin.artifact_digests[tina.ARTIFACT_COBERTURA] == hashlib.sha256(COBERTURA_BYTES).hexdigest()
    tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), cobertura_openapi_bytes=COBERTURA_BYTES)
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), cobertura_openapi_bytes=COBERTURA_BYTES + b"\n")
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), cobertura_executor=object())  # type: ignore[arg-type]


async def test_sem_os_bytes_a_cobertura_recusa_e_nada_sai() -> None:
    executor = Executor()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor)
    assert isinstance(adapter, TinaCoberturaPort)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert executor.calls == []


async def test_o_pedido_leva_finalidade_termo_e_limite_na_rota_do_sujeito() -> None:
    adapter, executor = _montar()
    resultado = await _ler(adapter, limite=3)
    assert resultado.succeeded and resultado.value is not None, resultado.failure
    [pedido] = executor.calls
    assert pedido.operation == tina.OP_COBERTURA
    assert pedido.path == _PATH_PEDIDO
    assert pedido.query == (
        ("purpose_of_use", "atendimento_whatsapp"),
        ("procedimento", "ressonancia magnetica"),
        ("limite", 3),
    )
    assert pedido.request_ref is None


async def test_conversao_do_corpo_validado() -> None:
    adapter, _ = _montar(
        _corpo(
            itens=[
                _item(),
                _item(
                    situacao=None,
                    coberto=None,
                    no_rol=None,
                    na_segmentacao=None,
                    rol_edicao=None,
                    diretriz_de_utilizacao=None,
                    carencias=[],
                    codigo=None,
                    origem=None,
                ),
            ],
            truncada=True,
        )
    )
    resultado = await _ler(adapter, procedimento="40101010")
    assert resultado.succeeded and resultado.value is not None, resultado.failure
    view = resultado.value
    assert view.portable_subject_ref == REF
    assert view.segmentacao == "Ambulatorial + Hospitalar com Obstetrícia"
    assert view.regulamentado is True
    assert view.truncada is True
    assert view.avisos == ("sujeito_a_autorizacao", "sujeito_a_diretriz_de_utilizacao")
    assert view.fonte_atualizada_em == "2026-10-15T12:00:00Z"
    primeiro, segundo = view.itens
    assert (primeiro.situacao, primeiro.coberto, primeiro.no_rol, primeiro.na_segmentacao) == (
        "coberto_rol",
        True,
        True,
        True,
    )
    assert primeiro.rol_edicao == "ROL DE PROCEDIMENTOS 2021 - RN Nº 465/2021"
    assert primeiro.tem_diretriz is True
    [carencia] = primeiro.carencias
    assert (carencia.cumprida, carencia.dias, carencia.termina_em) == (False, 180, "2026-12-01")
    # `situacao` nula = a fonte nao sabe: `indeterminado`, nunca "nao cobre".
    assert segundo.situacao == "indeterminado" and segundo.coberto is None and segundo.tem_diretriz is False
    assert segundo.carencias == ()


async def test_lista_vazia_e_fonte_sem_data_sao_estado_valido() -> None:
    adapter, _ = _montar(
        _corpo(itens=[], fonte={"tabela": "tina_cobertura_procedimento", "atualizada_em": None})
    )
    resultado = await _ler(adapter)
    assert resultado.succeeded and resultado.value is not None
    assert resultado.value.itens == () and resultado.value.fonte_atualizada_em is None


@pytest.mark.parametrize(
    "sobre",
    [
        {"procedimento": "ab"},  # termo curto demais
        {"procedimento": "x" * 81},
        {"procedimento": "ressonancia; drop"},  # fora do alfabeto do contrato
        {"procedimento": ""},
        {"procedimento": 40101010},
        {"limite": 0},
        {"limite": 11},
        {"limite": True},
        {"purpose_of_use": "sharing_amh_internal"},  # a rota so' aceita `atendimento_whatsapp`
        {"consent_decision_ref": " "},
    ],
)
async def test_pedido_fora_do_contrato_nunca_sai(sobre: dict[str, Any]) -> None:
    adapter, executor = _montar()
    resultado = await _ler(adapter, **sobre)
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


async def test_termo_com_acento_e_codigo_curto_sao_aceitos() -> None:
    adapter, executor = _montar()
    assert (await _ler(adapter, procedimento="ressonância magnética")).succeeded
    assert (await _ler(adapter, procedimento="4")).succeeded
    assert len(executor.calls) == 2


@pytest.mark.parametrize(
    "corpo",
    [
        _corpo(portable_subject_ref=OUTRO),  # OUTRO sujeito: nunca um fato
        _corpo(itens=[_item()] * 11),  # contrato: no maximo 10
        _corpo(itens=[_item(situacao="coberto")]),  # fora do enum
        _corpo(natureza="autorizacao"),
        _corpo(avisos=["cobre_tudo"]),
        _corpo(extra="x"),
    ],
)
async def test_resposta_fora_do_contrato_vira_recusa_fechada(corpo: dict[str, Any]) -> None:
    adapter, _ = _montar(corpo)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


async def test_404_vira_not_found() -> None:
    adapter, executor = _montar()
    executor.status = 404
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.NOT_FOUND


async def test_um_executor_proprio_carrega_so_a_cobertura() -> None:
    principal = Executor()
    proprio = Executor()
    proprio.bodies[tina.OP_COBERTURA] = _corpo()
    adapter = tina.AmhTinaAdapter(
        TINA_BYTES,
        executor=principal,
        cobertura_openapi_bytes=COBERTURA_BYTES,
        cobertura_executor=proprio,
    )
    assert (await _ler(adapter)).succeeded
    assert [c.operation for c in proprio.calls] == [tina.OP_COBERTURA]
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
        "operation": tina.OP_COBERTURA,
        "path": _PATH_PEDIDO,
        "query": (
            ("purpose_of_use", "atendimento_whatsapp"),
            ("procedimento", "ressonancia magnetica"),
            ("limite", 5),
        ),
        "consent_decision_ref": _BASE_LEGAL,
        "timeout_seconds": 5.0,
    }
    base.update(sobre)
    return tina.GovernedTinaRequest(**base)


def _executor(servidor: Servidor, relogio: list[float], audit: Audit) -> AmhTinaCoberturaExecutor:
    return AmhTinaCoberturaExecutor(
        **_comum(servidor, _tokens(servidor, relogio), audit, purpose_of_use="atendimento_whatsapp")
    )


def test_escopo_registro_e_composicao_do_executor_proprio(servidor: Servidor, relogio: list[float]) -> None:
    assert SCOPE_BY_OPERATION[tina.OP_COBERTURA] == "interop/tina.read"
    assert tina.OP_COBERTURA not in AmhTinaExecutor.registered_operations
    assert AmhTinaCoberturaExecutor.registered_operations == frozenset({tina.OP_COBERTURA})
    with pytest.raises(AmhInteropCompositionError):  # finalidade de sempre: recusado
        AmhTinaCoberturaExecutor(**_comum(servidor, _tokens(servidor, relogio), Audit()))
    with pytest.raises(AmhInteropCompositionError):  # token sem `interop/tina.read`: recusado
        AmhTinaCoberturaExecutor(
            **_comum(
                servidor,
                _tokens(servidor, relogio, "interop/billing.read"),
                Audit(),
                purpose_of_use="atendimento_whatsapp",
            )
        )
    _executor(servidor, relogio, Audit())


async def test_executor_audita_antes_e_despacha_na_rota_fixa_com_a_query(
    servidor: Servidor, relogio: list[float]
) -> None:
    servidor.respostas[_PATH] = httpx.Response(200, json=_corpo())
    audit = Audit(servidor)
    resultado = await _executor(servidor, relogio, audit).execute(_pedido())
    assert resultado.succeeded, resultado.failure
    [pedido] = servidor.ao_servico()
    assert pedido.url.path == _PATH
    assert dict(pedido.url.params) == {
        "purpose_of_use": "atendimento_whatsapp",
        "procedimento": "ressonancia magnetica",
        "limite": "5",
    }
    assert str(pedido.url).startswith(ORIGIN)
    assert audit.pedidos_no_emit == [0]  # auditoria ANTES do despacho
    [registro] = audit.registros
    assert registro.action == tina.OP_COBERTURA
    assert "ressonancia" not in repr(registro.details)  # o termo nunca vai cru a auditoria


@pytest.mark.parametrize(
    "sobre",
    [
        {
            "query": (
                ("purpose_of_use", "atendimento_whatsapp"),
                ("procedimento", "ressonancia"),
                ("limite", 11),
            )
        },
        {"query": (("purpose_of_use", "atendimento_whatsapp"), ("procedimento", "ab"))},
        {"query": (("purpose_of_use", "atendimento_whatsapp"),)},  # sem o termo
        {"query": (("purpose_of_use", "atendimento_whatsapp"), ("procedimento", "x"), ("pendentes", "true"))},
        {"query": (("purpose_of_use", "sharing_amh_internal"), ("procedimento", "ressonancia"))},
        {"path": f"/interop/tina/v1/subjects/{quote(REF, safe='')}/elegibilidade"},
        {"operation": tina.OP_ELEGIBILIDADE},
        {"request_ref": "hk1_msg_cobertura_01"},
    ],
)
async def test_executor_nunca_despacha_pedido_fora_da_forma(
    servidor: Servidor, relogio: list[float], sobre: dict[str, Any]
) -> None:
    audit = Audit()
    resultado = await _executor(servidor, relogio, audit).execute(_pedido(**sobre))
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor.ao_servico() == [] and audit.registros == []


async def test_o_executor_tina_de_sempre_nao_registra_a_cobertura(
    servidor: Servidor, relogio: list[float]
) -> None:
    executor = AmhTinaExecutor(**_comum(servidor, _tokens(servidor, relogio), Audit()))
    resultado = await executor.execute(_pedido())
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert servidor.ao_servico() == []


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
            tmp_path, monkeypatch, amh_tina_cobertura_openapi_path=str(_REAL / "tina-cobertura.openapi.yaml")
        ),
        seam=_seam(),
        audit=audit,
        agent_version="helena@v0",
        transport_factory=servidor.factory,
        incluir_tina=True,
    )
    assert interop.tina is not None and len(interop._executors) == 4
    resultado = await interop.tina.get_cobertura(
        REF,
        purpose_of_use="atendimento_whatsapp",
        consent_decision_ref=_BASE_LEGAL,
        procedimento="ressonancia magnetica",
    )
    assert resultado.succeeded and resultado.value is not None, resultado.failure
    assert resultado.value.itens[0].situacao == "coberto_rol"
    assert [r.action for r in audit.registros] == [tina.OP_COBERTURA]
    await interop.aclose()


async def test_composicao_sem_o_caminho_nao_tem_a_rota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    interop = build_amh_interop(
        settings=_settings_reais(tmp_path, monkeypatch, amh_tina_cobertura_openapi_path=""),
        seam=_seam(),
        audit=Audit(),
        agent_version="helena@v0",
        incluir_tina=True,
    )
    assert interop.tina is not None and len(interop._executors) == 3
    resultado = await interop.tina.get_cobertura(
        REF, purpose_of_use="atendimento_whatsapp", consent_decision_ref=_BASE_LEGAL, procedimento="exame"
    )
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED


@pytest.mark.parametrize("caso", ["ilegivel", "fora_do_digest"])
async def test_falha_de_leitura_ou_digest_deixa_so_a_cobertura_de_fora(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caso: str
) -> None:
    caminho = tmp_path / "cobertura.yaml"
    if caso == "fora_do_digest":
        caminho.write_bytes(COBERTURA_BYTES + b"\n")
    with structlog.testing.capture_logs() as logs:
        interop = build_amh_interop(
            settings=_settings_reais(tmp_path, monkeypatch, amh_tina_cobertura_openapi_path=str(caminho)),
            seam=_seam(),
            audit=Audit(),
            agent_version="helena@v0",
            incluir_tina=True,
        )
    assert interop.tina is not None and len(interop._executors) == 3
    resultado = await interop.tina.get_cobertura(
        REF, purpose_of_use="atendimento_whatsapp", consent_decision_ref=_BASE_LEGAL, procedimento="exame"
    )
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    [erro] = [e for e in logs if e["event"] == "amh_interop_cobertura_fora"]
    assert erro["log_level"] == "error"
    assert str(tmp_path) not in repr(erro)  # o caminho nunca vai a log
