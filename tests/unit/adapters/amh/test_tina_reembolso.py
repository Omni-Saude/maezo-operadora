"""Condicoes CONTRATUAIS de reembolso do proprio sujeito (contrato AMH PROPOSTO `tina-reembolso`; decisao do
dono de 30/10/2026; desenho em `docs/design/condicoes-reembolso-contratuais.md`).

O contrato NAO esta' publicado: a fixture de contrato e' a PROPOSTA OpenAPI do Maezo
(`docs/design/condicoes-reembolso-contratuais.openapi.proposta.yaml`), e o digest dela entra so' num pin DE
TESTE (`monkeypatch` sobre `tina.load_contract_pin`; o lock real nao e' tocado). O que fica preso:

  * com o lock REAL, os bytes da proposta fazem o construtor RECUSAR (nenhum bloco do pin traz o artefato);
  * a composicao e' dark: flag desligada = `None` sem tocar nos bytes; ligada sem pin = `None`;
  * a flag `MAEZO_REEMBOLSO_CONDICOES_CONTRATUAIS` nasce desligada;
  * a operacao fica FORA de `OPERATIONS` (nenhum executor do gateway a serve);
  * o pedido leva finalidade + data na query e o id opaco no cabecalho, na rota do proprio sujeito;
  * a conversao do corpo validado (campos ausentes = `None`, nunca completados);
  * outro sujeito, outra data, fora do schema ou incoerente = recusa fechada; 403/404/429 mapeados.

Dados 100% sinteticos; nenhum valor e' valor real de contrato.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

import pytest

from maezo.adapters.amh import tina
from maezo.adapters.amh.contract import load_contract_pin
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.tina import (
    CondicoesReembolsoView,
    LimiteReembolso,
    RegraValorReembolso,
    TinaReembolsoPort,
    VinculoReembolso,
)
from maezo.runtime.worker_runtime.settings import WorkerRuntimeSettings
from tests.unit.adapters.amh.test_tina import Executor

pytestmark = pytest.mark.anyio

_ROOT = Path(__file__).resolve().parents[4]
TINA_BYTES = (_ROOT / "config" / "integrations" / "amh" / "openapi" / "tina.openapi.yaml").read_bytes()
PROPOSTA = _ROOT / "docs" / "design" / "condicoes-reembolso-contratuais.openapi.proposta.yaml"
PROPOSTA_BYTES = PROPOSTA.read_bytes()
REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
OUTRO = "amh:psr:v1:2b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
DATA = "2026-10-01"
REQ = "reemb-req-0001-sintetico"
_PATH_PEDIDO = f"/interop/tina/v1/subjects/{quote(REF, safe='')}/condicoes-reembolso"
_BASE_LEGAL = "base-legal:execucao-de-contrato"


class ExecutorReembolso(Executor):
    """So' o teste registra a operacao: em producao nenhum executor do gateway a serve (nao publicada)."""

    registered_operations = tina.OPERATIONS | {tina.OP_REEMBOLSO}


def _corpo(**sobre: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "portable_subject_ref": REF,
        "data_atendimento": DATA,
        "vinculo": {
            "produto_ref": "amh:produto:sintetico-001",
            "contrato_ref": "amh:contrato:sintetico-001",
            "registro_ans_plano": "000000000",
            "tipo_contratacao": "coletivo_empresarial",
            "vigencia_inicio": "2025-01-01",
            "vigencia_fim": None,
        },
        "livre_escolha": True,
        "regra_valor": {
            "tipo": "multiplo_tabela_referencia",
            "tabela_referencia": "TABELA-SINTETICA",
            "multiplo": 1.0,
        },
        "limites": [
            {
                "categoria": "consulta",
                "tipo_limite": "valor_por_evento",
                "valor_cents": 10000,
                "quantidade": None,
                "periodo": "evento",
            },
            {
                "categoria": "terapia",
                "tipo_limite": "quantidade_por_periodo",
                "valor_cents": None,
                "quantidade": 12,
                "periodo": "ano",
            },
        ],
        "limites_truncados": False,
        "coparticipacao": {"aplica": False, "regras": []},
        "prazos": {"pagamento_dias": 30, "pagamento_contagem": "corridos", "solicitacao_dias": 365},
        "documentos_exigidos": ["nota_fiscal", "pedido_medico"],
        "natureza": "informacao_contratual_nao_e_decisao",
        "avisos": [],
        "qualidade": {"campos_ausentes": []},
        "fonte": {"origem": "tasy_via_lake", "atualizada_em": "2026-10-30T06:00:00Z"},
    }
    corpo.update(sobre)
    return corpo


def _sobre(caminho: str, valor: Any) -> dict[str, Any]:
    corpo = _corpo()
    alvo = corpo
    *pais, folha = caminho.split(".")
    for parte in pais:
        alvo = alvo[int(parte)] if isinstance(alvo, list) else alvo[parte]
    alvo[folha] = valor
    return corpo


@pytest.fixture
def pin_de_teste(monkeypatch: pytest.MonkeyPatch) -> None:
    """O pin REAL + o digest da proposta. Simula SO' o dia em que a AMH publicar; o lock nao muda."""
    real = load_contract_pin()
    digests = {**real.artifact_digests, tina.ARTIFACT_REEMBOLSO: hashlib.sha256(PROPOSTA_BYTES).hexdigest()}
    falso = dataclasses.replace(real, artifact_digests=MappingProxyType(digests))
    monkeypatch.setattr(tina, "load_contract_pin", lambda path=None: falso)


def _montar(corpo: dict[str, Any] | None = None) -> tuple[tina.AmhTinaAdapter, ExecutorReembolso]:
    executor = ExecutorReembolso()
    executor.bodies[tina.OP_REEMBOLSO] = corpo if corpo is not None else _corpo()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor, reembolso_openapi_bytes=PROPOSTA_BYTES)
    return adapter, executor


async def _ler(adapter: tina.AmhTinaAdapter, **sobre: Any) -> PortResult[CondicoesReembolsoView]:
    args: dict[str, Any] = {
        "purpose_of_use": tina.PURPOSE_REEMBOLSO,
        "consent_decision_ref": _BASE_LEGAL,
        "data_atendimento": DATA,
        "request_ref": REQ,
    }
    args.update(sobre)
    return await adapter.get_condicoes_reembolso(REF, **args)


# --- fail-closed e dark com o lock REAL -------------------------------------------------------------


def test_o_lock_real_nao_tem_o_artefato_e_os_bytes_da_proposta_recusam() -> None:
    pin = load_contract_pin()
    assert tina.ARTIFACT_REEMBOLSO == "schemas/openapi/maezo/v1/tina-reembolso.openapi.yaml"
    assert tina.ARTIFACT_REEMBOLSO not in pin.artifact_digests
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), reembolso_openapi_bytes=PROPOSTA_BYTES)
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), reembolso_executor=object())  # type: ignore[arg-type]


def test_a_proposta_nao_esta_vendorizada_como_contrato_publicado() -> None:
    vendorizados = _ROOT / "config" / "integrations" / "amh" / "openapi"
    assert not (vendorizados / "tina-reembolso.openapi.yaml").exists()
    assert "NÃO PUBLICADA, NÃO VENDORIZADA, NÃO PINADA" in PROPOSTA_BYTES.decode("utf-8")


def test_a_operacao_fica_fora_de_operations() -> None:
    assert tina.OP_REEMBOLSO == "amh.tina_get_condicoes_reembolso"
    assert tina.OP_REEMBOLSO not in tina.OPERATIONS


def test_a_flag_nasce_desligada() -> None:
    assert WorkerRuntimeSettings().reembolso_condicoes_contratuais is False
    campo = WorkerRuntimeSettings.model_fields["reembolso_condicoes_contratuais"]
    assert campo.alias == "MAEZO_REEMBOLSO_CONDICOES_CONTRATUAIS"


def test_composicao_desligada_nao_toca_nos_bytes() -> None:
    class Bomba(bytes):
        def __len__(self) -> int:
            raise AssertionError("bytes lidos com a flag desligada")

    assert (
        tina.compor_condicoes_reembolso(
            ligado=False,
            tina_openapi_bytes=TINA_BYTES,
            executor=Executor(),
            reembolso_openapi_bytes=Bomba(b"x"),
        )
        is None
    )


@pytest.mark.usefixtures("pin_de_teste")
def test_composicao_desligada_com_pin_publicado_continua_none() -> None:
    """Com o contrato JA pinado (pin de teste), so' a flag segura o leitor: desligada = `None`."""
    assert (
        tina.compor_condicoes_reembolso(
            ligado=False,
            tina_openapi_bytes=TINA_BYTES,
            executor=ExecutorReembolso(),
            reembolso_openapi_bytes=PROPOSTA_BYTES,
        )
        is None
    )


def test_composicao_ligada_sem_pin_continua_none() -> None:
    assert (
        tina.compor_condicoes_reembolso(
            ligado=True,
            tina_openapi_bytes=TINA_BYTES,
            executor=Executor(),
            reembolso_openapi_bytes=PROPOSTA_BYTES,
        )
        is None
    )
    assert (
        tina.compor_condicoes_reembolso(
            ligado=True, tina_openapi_bytes=TINA_BYTES, executor=Executor(), reembolso_openapi_bytes=None
        )
        is None
    )


@pytest.mark.usefixtures("pin_de_teste")
def test_composicao_ligada_com_pin_monta_o_leitor() -> None:
    leitor = tina.compor_condicoes_reembolso(
        ligado=True,
        tina_openapi_bytes=TINA_BYTES,
        executor=ExecutorReembolso(),
        reembolso_openapi_bytes=PROPOSTA_BYTES,
    )
    assert isinstance(leitor, TinaReembolsoPort)


async def test_sem_os_bytes_recusa_e_nada_sai() -> None:
    executor = ExecutorReembolso()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert executor.calls == []


# --- comportamento contra a fixture de contrato (pin de teste) -----------------------------------------


@pytest.mark.usefixtures("pin_de_teste")
async def test_executor_sem_a_operacao_registrada_recusa() -> None:
    executor = Executor()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor, reembolso_openapi_bytes=PROPOSTA_BYTES)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert executor.calls == []


@pytest.mark.usefixtures("pin_de_teste")
async def test_o_pedido_leva_finalidade_data_e_id_opaco() -> None:
    adapter, executor = _montar()
    resultado = await _ler(adapter)
    assert resultado.succeeded, resultado.failure
    [pedido] = executor.calls
    assert pedido.operation == tina.OP_REEMBOLSO
    assert pedido.path == _PATH_PEDIDO
    assert pedido.query == (("purpose_of_use", "analise_reembolso"), ("data_atendimento", DATA))
    assert pedido.request_ref == REQ
    assert pedido.consent_decision_ref == _BASE_LEGAL


@pytest.mark.usefixtures("pin_de_teste")
async def test_um_executor_proprio_carrega_so_o_reembolso() -> None:
    principal, proprio = Executor(), ExecutorReembolso()
    proprio.bodies[tina.OP_REEMBOLSO] = _corpo()
    adapter = tina.AmhTinaAdapter(
        TINA_BYTES, executor=principal, reembolso_openapi_bytes=PROPOSTA_BYTES, reembolso_executor=proprio
    )
    assert (await _ler(adapter)).succeeded
    assert principal.calls == [] and [c.operation for c in proprio.calls] == [tina.OP_REEMBOLSO]


@pytest.mark.usefixtures("pin_de_teste")
async def test_conversao_do_corpo_validado() -> None:
    adapter, _ = _montar()
    view = (await _ler(adapter)).value
    assert view is not None
    assert view.vinculo == VinculoReembolso(
        produto_ref="amh:produto:sintetico-001",
        contrato_ref="amh:contrato:sintetico-001",
        registro_ans_plano="000000000",
        tipo_contratacao="coletivo_empresarial",
        vigencia_inicio="2025-01-01",
        vigencia_fim=None,
    )
    assert view.livre_escolha is True
    assert view.regra_valor == RegraValorReembolso("multiplo_tabela_referencia", "TABELA-SINTETICA", 1.0)
    assert view.limites[0] == LimiteReembolso("consulta", "valor_por_evento", 10000, None, "evento")
    assert view.limites[1].quantidade == 12 and view.limites[1].valor_cents is None
    assert view.coparticipacao_aplica is False and view.coparticipacao == ()
    assert (view.prazo_pagamento_dias, view.prazo_pagamento_contagem, view.prazo_solicitacao_dias) == (
        30,
        "corridos",
        365,
    )
    assert view.documentos_exigidos == frozenset({"nota_fiscal", "pedido_medico"})
    assert view.fonte_atualizada_em == "2026-10-30T06:00:00Z"


@pytest.mark.usefixtures("pin_de_teste")
async def test_campos_ausentes_sao_estado_valido_e_nunca_completados() -> None:
    corpo = _corpo(
        livre_escolha=None,
        regra_valor={"tipo": None, "tabela_referencia": None, "multiplo": None},
        limites=[],
        coparticipacao={"aplica": None, "regras": []},
        prazos={"pagamento_dias": None, "pagamento_contagem": None, "solicitacao_dias": None},
        documentos_exigidos=[],
        qualidade={"campos_ausentes": ["livre_escolha", "regra_valor", "limites", "prazos"]},
        fonte={"origem": "tasy_via_lake", "atualizada_em": None},
    )
    adapter, _ = _montar(corpo)
    view = (await _ler(adapter)).value
    assert view is not None
    assert view.livre_escolha is None and view.limites == () and view.prazo_pagamento_dias is None
    assert view.regra_valor == RegraValorReembolso(None, None, None)
    assert "limites" in view.campos_ausentes and view.fonte_atualizada_em is None


@pytest.mark.usefixtures("pin_de_teste")
@pytest.mark.parametrize(
    "corpo",
    [
        _corpo(portable_subject_ref=OUTRO),  # OUTRO sujeito: nunca um fato
        _corpo(data_atendimento="2026-09-01"),  # condicoes de OUTRA data: nunca valem para este pedido
        _corpo(natureza="aprovacao"),
        _corpo(valor_devido_cents=100),  # campo fora do contrato fechado
        _corpo(livre_escolha="sim"),
        _sobre("regra_valor.tipo", "inventado"),
        _sobre("limites.0.categoria", "estetica"),
        _sobre("limites.0.valor_cents", -1),
        _sobre("prazos.pagamento_contagem", "semanas"),
        _corpo(documentos_exigidos=["selfie"]),
        _corpo(fonte={"origem": "tasy_direto", "atualizada_em": None}),
        # incoerentes (o schema nao expressa; o adaptador recusa):
        _sobre("limites.0.valor_cents", None),  # limite de valor sem valor
        _sobre("limites.1.quantidade", None),  # limite de quantidade sem quantidade
        _sobre("regra_valor.tipo", "tabela_propria"),  # multiplo com regra que nao e' multiplo
        _corpo(
            coparticipacao={
                "aplica": False,
                "regras": [
                    {"categoria": "consulta", "percentual": 30, "valor_fixo_cents": None, "teto_cents": None}
                ],
            }
        ),
    ],
)
async def test_resposta_fora_do_contrato_vira_recusa_fechada(corpo: dict[str, Any]) -> None:
    adapter, _ = _montar(copy.deepcopy(corpo))
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


@pytest.mark.usefixtures("pin_de_teste")
async def test_limites_truncados_passam_como_fato() -> None:
    corpo = _corpo(limites_truncados=True)
    adapter, _ = _montar(corpo)
    view = (await _ler(adapter)).value
    assert view is not None and view.limites_truncados is True


@pytest.mark.usefixtures("pin_de_teste")
@pytest.mark.parametrize(
    ("status", "motivo"),
    [
        (400, Reason.INVALID_REQUEST),
        (401, Reason.NOT_AUTHENTICATED),
        (404, Reason.NOT_FOUND),
        (429, Reason.RATE_LIMITED),
        (500, Reason.UPSTREAM_UNAVAILABLE),
    ],
)
async def test_status_de_erro_viram_recusa(status: int, motivo: Reason) -> None:
    adapter, executor = _montar()
    executor.status = status
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is motivo


@pytest.mark.usefixtures("pin_de_teste")
@pytest.mark.parametrize(
    ("razao", "motivo"),
    [
        ("consent_denied", Reason.CONSENT_REQUIRED),
        ("purpose_not_permitted", Reason.PURPOSE_DENIED),
        ("scope_not_supported", Reason.SCOPE_NOT_SUPPORTED),
    ],
)
async def test_403_fail_closed(razao: str, motivo: Reason) -> None:
    adapter, executor = _montar({"error_code": "forbidden", "message": "x", "reason": razao, "trace_id": "t"})
    executor.status = 403
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is motivo


@pytest.mark.usefixtures("pin_de_teste")
@pytest.mark.parametrize(
    "sobre",
    [
        {"purpose_of_use": "atendimento_whatsapp"},
        {"data_atendimento": "01/10/2026"},
        {"data_atendimento": 20261001},
        {"request_ref": "curto"},
        {"request_ref": None},
        {"consent_decision_ref": " "},
    ],
)
async def test_pedido_invalido_nunca_sai(sobre: dict[str, Any]) -> None:
    adapter, executor = _montar()
    resultado = await _ler(adapter, **sobre)
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []
