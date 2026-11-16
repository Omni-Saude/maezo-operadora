"""Rede credenciada do plano (contrato `tina-rede-credenciada`, manifest aditivo v1.8; pedido (b) da T-MZO-B,
HEL-3/DL-0167).

O artefato esta' PINADO (bloco `manifest_v1_8`, PUBLISHED em 16/10/2026): estes testes usam os BYTES REAIS
vendorizados em `config/integrations/amh/openapi/` contra o lock real. A excecao da cerca de pureza dos ports
para `nome`/`endereco`/`telefone` do estabelecimento AGUARDA a confirmacao do dono
(`tests/unit/ports/test_ports_purity.py`). O que fica preso aqui:

  * o digest do lock real = o dos bytes vendorizados; bytes fora do digest = o construtor RECUSA;
  * sem os bytes, `get_rede_credenciada` recusa `SCOPE_NOT_SUPPORTED`; executor que nao registra a operacao
    tambem (o gateway ainda nao tem executor para ela: a operacao fica FORA de `OPERATIONS`);
  * o pedido leva finalidade, especialidade, municipio, limite e pagina na rota do proprio sujeito; parametro
    fora do contrato nunca sai;
  * a conversao do corpo validado para `RedeCredenciadaView`, com nome, endereco e telefone fora do `repr`;
  * outro sujeito, campo fora do schema (ex.: CNPJ), outra pagina, contagem de pessoa fisica fora da pagina 1
    ou 404 viram recusa fechada.

Dados 100% sinteticos.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from maezo.adapters.amh import tina
from maezo.adapters.amh.contract import load_contract_pin
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.errors import PortResult
from maezo.ports.tina import EstabelecimentoRede, RedeCredenciadaView, TinaRedeCredenciadaPort
from tests.unit.adapters.amh.test_tina import Executor

_REAL = Path(__file__).resolve().parents[4] / "config" / "integrations" / "amh" / "openapi"
TINA_BYTES = (_REAL / "tina.openapi.yaml").read_bytes()
REDE_BYTES = (_REAL / "tina-rede-credenciada.openapi.yaml").read_bytes()
REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
OUTRO = "amh:psr:v1:2b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
_PATH_PEDIDO = f"/interop/tina/v1/subjects/{quote(REF, safe='')}/rede-credenciada"
_BASE_LEGAL = "base-legal:execucao-de-contrato"


class _ExecutorComRede(Executor):
    """O executor falso do TINA que TAMBEM registra a rede credenciada (o do gateway ainda nao existe)."""

    registered_operations = tina.OPERATIONS | {tina.OP_REDE_CREDENCIADA}


def _estabelecimento(**sobre: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "nome": "Clinica Exemplo Cardio",
        "endereco": "Rua Exemplo, 100",
        "bairro": "Centro",
        "municipio": "Cidade Exemplo",
        "uf": "SP",
        "telefone": "1700000000",
        "especialidades": ["Cardiologia"],
    }
    item.update(sobre)
    return item


def _corpo(**sobre: Any) -> dict[str, Any]:
    corpo: dict[str, Any] = {
        "portable_subject_ref": REF,
        "pagina": 1,
        "limite": 5,
        "tem_mais": False,
        "estabelecimentos": [_estabelecimento()],
        "profissionais_sem_identificacao": 12,
        "fonte_atualizada_em": "2026-10-11T12:00:00Z",
        "qualidade": {"campos_ausentes": []},
    }
    corpo.update(sobre)
    return corpo


def _montar(corpo: dict[str, Any] | None = None) -> tuple[tina.AmhTinaAdapter, Executor]:
    executor = _ExecutorComRede()
    executor.bodies[tina.OP_REDE_CREDENCIADA] = corpo if corpo is not None else _corpo()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor, rede_credenciada_openapi_bytes=REDE_BYTES)
    return adapter, executor


async def _ler(adapter: tina.AmhTinaAdapter, **sobre: Any) -> PortResult[RedeCredenciadaView]:
    args: dict[str, Any] = {
        "purpose_of_use": tina.PURPOSE_REDE_CREDENCIADA,
        "consent_decision_ref": _BASE_LEGAL,
        "especialidade": "cardiologia",
    }
    args.update(sobre)
    return await adapter.get_rede_credenciada(REF, **args)


def test_o_lock_real_tem_o_digest_do_v1_8_e_bytes_fora_dele_recusam() -> None:
    pin = load_contract_pin()
    assert tina.ARTIFACT_REDE_CREDENCIADA == "schemas/openapi/maezo/v1/tina-rede-credenciada.openapi.yaml"
    assert pin.manifest_v1_8_digest is not None
    assert pin.artifact_digests[tina.ARTIFACT_REDE_CREDENCIADA] == hashlib.sha256(REDE_BYTES).hexdigest()
    tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), rede_credenciada_openapi_bytes=REDE_BYTES)
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(
            TINA_BYTES, executor=Executor(), rede_credenciada_openapi_bytes=REDE_BYTES + b"\n"
        )
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(
            TINA_BYTES,
            executor=Executor(),
            rede_credenciada_executor=object(),  # type: ignore[arg-type]
        )


def test_a_operacao_fica_fora_do_gateway_ate_ter_executor_e_autonomia() -> None:
    from maezo.gateway.effect_classes import OPERATIONS as EFEITOS

    assert tina.OP_REDE_CREDENCIADA == "amh.tina_get_rede_credenciada"
    assert tina.OP_REDE_CREDENCIADA not in tina.OPERATIONS
    assert tina.OP_REDE_CREDENCIADA not in EFEITOS


async def test_sem_os_bytes_a_rede_recusa_e_nada_sai() -> None:
    executor = _ExecutorComRede()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor)
    assert isinstance(adapter, TinaRedeCredenciadaPort)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert executor.calls == []


async def test_executor_que_nao_registra_a_rede_recusa_sem_despachar() -> None:
    executor = Executor()  # registra so' `tina.OPERATIONS`
    executor.bodies[tina.OP_REDE_CREDENCIADA] = _corpo()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor, rede_credenciada_openapi_bytes=REDE_BYTES)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert executor.calls == []


async def test_o_pedido_leva_finalidade_especialidade_municipio_limite_e_pagina() -> None:
    adapter, executor = _montar(_corpo(pagina=2, limite=3, profissionais_sem_identificacao=None))
    resultado = await _ler(adapter, municipio="Cidade Exemplo", limite=3, pagina=2)
    assert resultado.succeeded and resultado.value is not None, resultado.failure
    [pedido] = executor.calls
    assert pedido.operation == tina.OP_REDE_CREDENCIADA
    assert pedido.path == _PATH_PEDIDO
    assert pedido.query == (
        ("purpose_of_use", "atendimento_whatsapp"),
        ("especialidade", "cardiologia"),
        ("municipio", "Cidade Exemplo"),
        ("limite", 3),
        ("pagina", 2),
    )
    assert pedido.request_ref is None


async def test_sem_municipio_o_parametro_nao_vai() -> None:
    adapter, executor = _montar()
    assert (await _ler(adapter)).succeeded
    [pedido] = executor.calls
    assert [nome for nome, _ in pedido.query] == ["purpose_of_use", "especialidade", "limite", "pagina"]


async def test_conversao_do_corpo_validado_com_os_campos_fora_do_repr() -> None:
    adapter, _ = _montar(
        _corpo(
            tem_mais=True,
            estabelecimentos=[
                _estabelecimento(),
                _estabelecimento(nome=None, endereco=None, telefone=None, bairro=None, uf=None),
            ],
            qualidade={"campos_ausentes": ["nome", "endereco", "telefone"]},
        )
    )
    resultado = await _ler(adapter)
    assert resultado.succeeded and resultado.value is not None, resultado.failure
    view = resultado.value
    assert (view.portable_subject_ref, view.pagina, view.limite, view.tem_mais) == (REF, 1, 5, True)
    assert view.profissionais_sem_identificacao == 12
    assert view.fonte_atualizada_em == "2026-10-11T12:00:00Z"
    assert view.campos_ausentes == ("nome", "endereco", "telefone")
    primeiro, segundo = view.estabelecimentos
    assert primeiro == EstabelecimentoRede(
        nome="Clinica Exemplo Cardio",
        endereco="Rua Exemplo, 100",
        bairro="Centro",
        municipio="Cidade Exemplo",
        uf="SP",
        telefone="1700000000",
        especialidades=("Cardiologia",),
    )
    # Campo ausente e' estado valido: nunca completado.
    assert (segundo.nome, segundo.endereco, segundo.telefone, segundo.bairro) == (None, None, None, None)
    texto = repr(view)
    assert "Clinica Exemplo" not in texto and "Rua Exemplo" not in texto and "1700000000" not in texto


async def test_pagina_vazia_e_estado_valido() -> None:
    adapter, _ = _montar(_corpo(estabelecimentos=[], profissionais_sem_identificacao=None))
    resultado = await _ler(adapter)
    assert resultado.succeeded and resultado.value is not None
    assert resultado.value.estabelecimentos == () and resultado.value.profissionais_sem_identificacao is None


@pytest.mark.parametrize(
    "sobre",
    [
        {"especialidade": "ab"},  # curta demais
        {"especialidade": "x" * 61},
        {"especialidade": "cardio; drop"},  # fora do alfabeto do contrato
        {"especialidade": None},
        {"municipio": "S"},
        {"municipio": "Cidade/Exemplo"},
        {"municipio": 10},
        {"limite": 0},
        {"limite": 11},
        {"limite": True},
        {"pagina": 0},
        {"pagina": 21},
        {"purpose_of_use": "sharing_amh_internal"},  # a rota so' aceita `atendimento_whatsapp`
        {"consent_decision_ref": " "},
    ],
)
async def test_pedido_fora_do_contrato_nunca_sai(sobre: dict[str, Any]) -> None:
    adapter, executor = _montar()
    resultado = await _ler(adapter, **sobre)
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


async def test_especialidade_e_municipio_com_acento_sao_aceitos() -> None:
    adapter, executor = _montar()
    assert (await _ler(adapter, especialidade="cardiologia pediátrica", municipio="São José")).succeeded
    assert len(executor.calls) == 1


@pytest.mark.parametrize(
    "corpo",
    [
        _corpo(portable_subject_ref=OUTRO),  # OUTRO sujeito: nunca um fato
        _corpo(estabelecimentos=[_estabelecimento(cnpj="00000000000000")]),  # campo fora do schema
        _corpo(pagina=2, profissionais_sem_identificacao=None),  # outra pagina que a pedida
        _corpo(limite=10),  # outro limite que o pedido
        _corpo(limite=5, estabelecimentos=[_estabelecimento()] * 6),  # mais itens que o limite
        _corpo(qualidade={"campos_ausentes": ["cpf"]}),  # codigo fora da lista fechada
    ],
    ids=["outro_sujeito", "cnpj", "outra_pagina", "outro_limite", "itens_demais", "campo_ausente_aberto"],
)
async def test_resposta_fora_do_contrato_vira_recusa_fechada(corpo: dict[str, Any]) -> None:
    adapter, _ = _montar(corpo)
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


async def test_contagem_de_pessoa_fisica_fora_da_pagina_1_e_violacao() -> None:
    adapter, _ = _montar(_corpo(pagina=2, profissionais_sem_identificacao=3))
    resultado = await _ler(adapter, pagina=2)
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


async def test_rota_desligada_e_404() -> None:
    adapter, executor = _montar()
    executor.status = 404
    executor.bodies[tina.OP_REDE_CREDENCIADA] = {"error_code": "nf", "message": "x", "trace_id": "t"}
    resultado = await _ler(adapter)
    assert resultado.failure is not None and resultado.failure.reason is Reason.NOT_FOUND
