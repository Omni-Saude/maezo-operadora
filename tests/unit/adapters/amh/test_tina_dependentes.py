"""Leitura de dependentes do titular (contrato `tina-dependentes`, manifest v1.5) com os BYTES REAIS pinados.

Os dois OpenAPI vendorizados (`tina` do v1.2 e `tina-dependentes` do v1.5) batem com o digest do lock real:
o adaptador valida cada resposta contra o schema publicado pela AMH, nao contra um sintetico. O que o
adaptador promete: dependente ANONIMO (so' relacao, situacao e recem-nascido), total exato, teto de 20,
coerencia entre os campos, e recusa fechada para qualquer desvio.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import fields, replace
from pathlib import Path
from urllib.parse import quote

import pytest

from maezo.adapters.amh import tina
from maezo.adapters.amh.contract import load_contract_pin
from maezo.ports.errors import PortFailureReason as Reason
from maezo.ports.tina import (
    MAX_DEPENDENTES,
    DependenteAnonimo,
    DependentesView,
    TinaDependentesPort,
)
from tests.unit.adapters.amh.test_tina import Executor

REPO_ROOT = Path(__file__).resolve().parents[4]
OPENAPI = REPO_ROOT / "config" / "integrations" / "amh" / "openapi"
TINA_BYTES = (OPENAPI / "tina.openapi.yaml").read_bytes()
DEPENDENTES_BYTES = (OPENAPI / "tina-dependentes.openapi.yaml").read_bytes()
REF = "amh:psr:v1:1b2f3a4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"
OUTRO = "amh:psr:v1:9c8d7e6f-5a4b-4c3d-8e2f-1a0b9c8d7e6f"
_ARGS = {"purpose_of_use": "sharing_amh_internal", "consent_decision_ref": "base-legal:execucao-de-contrato"}


def _dep(
    relacao: str | None = "Filho(a) até 23 anos", situacao: str | None = "Apto", rn: bool | None = False
):
    return {"relacao_dependencia": relacao, "situacao_beneficiario": situacao, "recem_nascido_no_plano": rn}


def _corpo(**sobre):
    corpo = {
        "portable_subject_ref": REF,
        "eh_titular": True,
        "total_ativos": 2,
        "lista_truncada": False,
        "dependentes": [_dep(), _dep("Cônjuge", "Apto", None)],
        "fonte_atualizada_em": "2026-10-13T10:00:00Z",
    }
    corpo.update(sobre)
    return corpo


def _montar(corpo: dict | None = None) -> tuple[tina.AmhTinaAdapter, Executor]:
    executor = Executor()
    executor.bodies[tina.OP_DEPENDENTES] = corpo if corpo is not None else _corpo()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor, dependentes_openapi_bytes=DEPENDENTES_BYTES)
    return adapter, executor


def test_os_bytes_vendorizados_batem_com_o_pin_real() -> None:
    pin = load_contract_pin()
    import hashlib

    assert pin.artifact_digests[tina.ARTIFACT_DEPENDENTES] == hashlib.sha256(DEPENDENTES_BYTES).hexdigest()
    assert pin.artifact_digests[tina.ARTIFACT] == hashlib.sha256(TINA_BYTES).hexdigest()
    assert pin.manifest_v1_5_digest is not None


async def test_titular_com_dependentes_vira_a_visao_anonima() -> None:
    adapter, executor = _montar()
    assert isinstance(adapter, TinaDependentesPort)
    resultado = await adapter.get_dependentes(REF, **_ARGS)
    assert resultado.succeeded, resultado.failure
    visao = resultado.value
    assert isinstance(visao, DependentesView)
    assert visao.portable_subject_ref == REF and visao.eh_titular is True
    assert visao.total_ativos == 2 and visao.lista_truncada is False
    assert visao.dependentes == (
        DependenteAnonimo("Filho(a) até 23 anos", "Apto", False),
        DependenteAnonimo("Cônjuge", "Apto", None),
    )
    [pedido] = executor.calls
    assert pedido.operation == tina.OP_DEPENDENTES
    assert pedido.path == f"/interop/tina/v1/subjects/{quote(REF, safe='')}/dependentes"
    assert pedido.query == (("purpose_of_use", "sharing_amh_internal"),)


def test_dependente_anonimo_nao_tem_onde_guardar_identificador() -> None:
    nomes = {campo.name for campo in fields(DependenteAnonimo)}
    assert nomes == {"relacao_dependencia", "situacao_vinculo", "rn_no_plano"}
    assert {campo.name for campo in fields(DependentesView)} == {
        "portable_subject_ref",
        "eh_titular",
        "total_ativos",
        "lista_truncada",
        "dependentes",
        "fonte_atualizada_em",
    }


async def test_nao_titular_recebe_lista_vazia() -> None:
    adapter, _ = _montar(_corpo(eh_titular=False, total_ativos=0, dependentes=[]))
    resultado = await adapter.get_dependentes(REF, **_ARGS)
    assert resultado.succeeded, resultado.failure
    assert resultado.value is not None
    assert resultado.value.eh_titular is False and resultado.value.total_ativos == 0
    assert resultado.value.dependentes == ()


async def test_titular_sem_dependentes_e_valido() -> None:
    adapter, _ = _montar(_corpo(total_ativos=0, dependentes=[]))
    resultado = await adapter.get_dependentes(REF, **_ARGS)
    assert resultado.succeeded and resultado.value is not None and resultado.value.total_ativos == 0


async def test_lista_truncada_mantem_o_total_exato() -> None:
    adapter, _ = _montar(
        _corpo(total_ativos=25, lista_truncada=True, dependentes=[_dep() for _ in range(MAX_DEPENDENTES)])
    )
    resultado = await adapter.get_dependentes(REF, **_ARGS)
    assert resultado.succeeded, resultado.failure
    assert resultado.value is not None
    assert resultado.value.total_ativos == 25 and len(resultado.value.dependentes) == 20
    assert resultado.value.lista_truncada is True


@pytest.mark.parametrize(
    "corpo",
    [
        # o dependente carrega a ref (campo fora do contrato fechado)
        _corpo(dependentes=[{**_dep(), "portable_subject_ref": OUTRO}, _dep()]),
        # o dependente carrega nome
        _corpo(dependentes=[{**_dep(), "nome": "FULANO"}, _dep()]),
        # campo obrigatorio ausente
        _corpo(dependentes=[{"relacao_dependencia": "Filho(a)"}, _dep()]),
        # campo de topo desconhecido
        _corpo(cpf="00000000000"),
        # lista maior que o teto de 20
        _corpo(total_ativos=21, lista_truncada=True, dependentes=[_dep() for _ in range(21)]),
        # total menor que a lista
        _corpo(total_ativos=1),
        # lista_truncada incoerente com o total
        _corpo(total_ativos=5, lista_truncada=False),
        _corpo(lista_truncada=True),
        # nao-titular com dependentes
        _corpo(eh_titular=False),
        _corpo(eh_titular=False, total_ativos=0, dependentes=[_dep()]),
        # tipos errados
        _corpo(total_ativos="2"),
        _corpo(total_ativos=-1, dependentes=[]),
        _corpo(eh_titular="sim"),
        # frescor invalido
        _corpo(fonte_atualizada_em="ontem"),
    ],
)
async def test_resposta_fora_do_contrato_ou_incoerente_e_recusada(corpo: dict) -> None:
    adapter, _ = _montar(corpo)
    resultado = await adapter.get_dependentes(REF, **_ARGS)
    assert not resultado.succeeded
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


async def test_resposta_de_outro_sujeito_e_violacao_de_contrato() -> None:
    adapter, _ = _montar(_corpo(portable_subject_ref=OUTRO))
    resultado = await adapter.get_dependentes(REF, **_ARGS)
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONTRACT_VIOLATION


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (400, Reason.INVALID_REQUEST),
        (401, Reason.NOT_AUTHENTICATED),
        (404, Reason.NOT_FOUND),
        (500, Reason.UPSTREAM_UNAVAILABLE),
        (418, Reason.CONTRACT_VIOLATION),
    ],
)
async def test_status_do_servico_vira_recusa_fechada(status: int, reason: Reason) -> None:
    adapter, executor = _montar()
    executor.status = status
    resultado = await adapter.get_dependentes(REF, **_ARGS)
    assert resultado.failure is not None and resultado.failure.reason is reason


async def test_403_mapeia_o_motivo_do_servico() -> None:
    adapter, executor = _montar()
    executor.status = 403
    executor.bodies[tina.OP_DEPENDENTES] = {"error_code": "forbidden", "reason": "consent_denied"}
    resultado = await adapter.get_dependentes(REF, **_ARGS)
    assert resultado.failure is not None and resultado.failure.reason is Reason.CONSENT_REQUIRED


@pytest.mark.parametrize(
    "sobre",
    [
        {"purpose_of_use": "outro"},  # fora do enum do contrato
        {"purpose_of_use": "atendimento_whatsapp"},  # so' o billing-status 0.2.0 declara essa finalidade
        {"consent_decision_ref": " "},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("nan")},
    ],
)
async def test_pedido_invalido_nunca_chega_ao_executor(sobre: dict) -> None:
    adapter, executor = _montar()
    resultado = await adapter.get_dependentes(REF, **{**_ARGS, **sobre})
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


async def test_ref_fora_do_padrao_do_contrato_nunca_chega_ao_executor() -> None:
    adapter, executor = _montar()
    resultado = await adapter.get_dependentes("nao-e-ref", **_ARGS)
    assert resultado.failure is not None and resultado.failure.reason is Reason.INVALID_REQUEST
    assert executor.calls == []


async def test_executor_que_levanta_ou_demora_vira_recusa_fechada() -> None:
    adapter, executor = _montar()
    executor.failure = RuntimeError("cpf 123 vazou")
    resultado = await adapter.get_dependentes(REF, **_ARGS)
    assert resultado.failure is not None and resultado.failure.reason is Reason.UPSTREAM_UNAVAILABLE
    assert "cpf" not in repr(resultado.failure)
    executor.failure = None
    executor.delay = 0.2
    resultado = await adapter.get_dependentes(REF, **{**_ARGS, "timeout_seconds": 0.01})
    assert resultado.failure is not None and resultado.failure.reason is Reason.TIMEOUT
    await asyncio.sleep(0)


async def test_sem_o_artefato_de_dependentes_recusa_sem_chamar_o_executor() -> None:
    executor = Executor()
    adapter = tina.AmhTinaAdapter(TINA_BYTES, executor=executor)
    resultado = await adapter.get_dependentes(REF, **_ARGS)
    assert resultado.failure is not None and resultado.failure.reason is Reason.SCOPE_NOT_SUPPORTED
    assert executor.calls == []


def test_bytes_de_dependentes_adulterados_ou_de_outro_artefato_sao_recusados() -> None:
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(
            TINA_BYTES, executor=Executor(), dependentes_openapi_bytes=DEPENDENTES_BYTES + b"\n# x\n"
        )
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), dependentes_openapi_bytes=TINA_BYTES)
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), dependentes_openapi_bytes="texto")  # type: ignore[arg-type]


def test_sem_o_bloco_v1_5_no_pin_o_construtor_com_dependentes_recusa(monkeypatch: pytest.MonkeyPatch) -> None:
    real = load_contract_pin()
    sem = {k: v for k, v in real.artifact_digests.items() if k != tina.ARTIFACT_DEPENDENTES}
    monkeypatch.setattr(tina, "load_contract_pin", lambda path=None: replace(real, artifact_digests=sem))
    with pytest.raises(tina.TinaContractError):
        tina.AmhTinaAdapter(TINA_BYTES, executor=Executor(), dependentes_openapi_bytes=DEPENDENTES_BYTES)
    # os fatos do plano seguem subindo sem o artefato de dependentes
    assert tina.AmhTinaAdapter(TINA_BYTES, executor=Executor()) is not None


def test_resposta_do_servico_real_segue_o_schema_publicado() -> None:
    """O corpo montado pelo servico da AMH (`contratos.tina_dependentes`) valida no schema pinado."""
    import yaml
    from jsonschema import Draft4Validator

    from maezo.adapters.amh.subject_context import _json_schema

    documento = yaml.safe_load(DEPENDENTES_BYTES)
    esquema = {
        "$ref": "#/definitions/Dependentes",
        "definitions": _json_schema(documento["components"]["schemas"]),
    }
    assert Draft4Validator(esquema).is_valid(json.loads(json.dumps(_corpo())))
