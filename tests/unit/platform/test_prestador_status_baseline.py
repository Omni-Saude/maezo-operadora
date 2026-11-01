"""MPTT P3 — baseline AMH: recusa entrada nao atestada/adulterada, nao aceita dado de beneficiario,
nao sobrescreve status ja projetado do CRED. Fixtures SINTETICAS (nenhum prestador real); a
"atestacao" das fixtures e' preenchida SO' aqui, em tmp — nunca num arquivo do repositorio.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from maezo.platform.prestador_status.baseline import (
    FORMATO_BASELINE,
    BaselineRecusadaError,
    ResultadoLinha,
    digest_conteudo,
    importar_baseline,
    validar_baseline,
)
from maezo.platform.prestador_status.ledger import EntradaLedger, SituacaoLida, ler_situacao
from maezo.platform.prestador_status.projecao import Dimensao, Origem
from tests.unit.platform.test_prestador_status_reconciliador import LedgerMemoria

T = "amh"


def _prestadores() -> list[dict[str, Any]]:
    return [
        {
            "prestador_id": "SINT-0001",
            "situacao": "CREDENCIADO",
            "data_efeito": "2019-03-01",
            "tipo_prestador": "clinica",
            "especialidade": "Cardiologia",
            "regiao_saude": "RS-Sintetica-1",
        },
        {
            "prestador_id": "SINT-0002",
            "situacao": "DESCREDENCIADO",
            "data_efeito": "2025-06-30",
            "tipo_prestador": None,
            "especialidade": None,
            "regiao_saude": None,
        },
    ]


def baseline_atestada(**sobrescreve: Any) -> dict[str, Any]:
    prestadores = _prestadores()
    bruta: dict[str, Any] = {
        "formato": FORMATO_BASELINE,
        "tenant": T,
        "data_referencia": "2026-11-01",
        "prestadores": prestadores,
        "atestacao": {
            "atestado": True,
            "emissor": "Fixture sintetica de teste",
            "responsavel": "Signatario ficticio de teste",
            "data_assinatura": "2026-11-01",
            "evidencia_ref": "fixture://baseline-sintetica-1",
            "conteudo_sha256": digest_conteudo(T, "2026-11-01", prestadores),
        },
    }
    bruta.update(sobrescreve)
    return bruta


def test_baseline_atestada_valida() -> None:
    baseline = validar_baseline(baseline_atestada(), tenant=T)
    assert [linha.prestador_id for linha in baseline.linhas] == ["SINT-0001", "SINT-0002"]
    assert baseline.conteudo_sha256 == digest_conteudo(T, "2026-11-01", _prestadores())


def _sem_atestacao() -> dict[str, Any]:
    bruta = baseline_atestada()
    del bruta["atestacao"]
    bruta["atestacao"] = None
    return bruta


def _com_atestacao(**campos: Any) -> dict[str, Any]:
    bruta = baseline_atestada()
    bruta["atestacao"] = dict(bruta["atestacao"], **campos)
    return bruta


@pytest.mark.parametrize(
    ("bruta", "trecho"),
    [
        (_sem_atestacao(), "atestacao ausente"),
        (_com_atestacao(atestado=False), "nao atestada"),
        (_com_atestacao(atestado="true"), "nao atestada"),
        (_com_atestacao(responsavel="PENDENTE"), "'responsavel' vazio ou pendente"),
        (_com_atestacao(responsavel=""), "'responsavel' vazio ou pendente"),
        (_com_atestacao(emissor="TODO"), "'emissor' vazio ou pendente"),
        (_com_atestacao(evidencia_ref="<preencher>"), "'evidencia_ref' vazio ou pendente"),
        (_com_atestacao(data_assinatura="ontem"), "data_assinatura"),
        (_com_atestacao(conteudo_sha256="0" * 64), "nao bate com o conteudo"),
        (_com_atestacao(conteudo_sha256="abc"), "nao e' sha256"),
        (_com_atestacao(assinatura_extra="x"), "chaves exatas"),
    ],
)
def test_baseline_sem_atestacao_valida_e_recusada(bruta: dict[str, Any], trecho: str) -> None:
    with pytest.raises(BaselineRecusadaError, match=trecho):
        validar_baseline(bruta, tenant=T)


def test_conteudo_adulterado_depois_da_assinatura_e_recusado() -> None:
    bruta = baseline_atestada()
    bruta["prestadores"][1]["situacao"] = "CREDENCIADO"  # alguem "corrige" depois de assinado
    with pytest.raises(BaselineRecusadaError, match="nao bate com o conteudo"):
        validar_baseline(bruta, tenant=T)


def _re_atestada(prestadores: list[dict[str, Any]]) -> dict[str, Any]:
    """Uma baseline cujo digest BATE (o atacante recalculou): a forma ainda tem de recusar."""
    bruta = baseline_atestada(prestadores=prestadores)
    bruta["atestacao"]["conteudo_sha256"] = digest_conteudo(T, "2026-11-01", prestadores)
    return bruta


@pytest.mark.parametrize(
    "campo_beneficiario", ["cpf", "beneficiario_pseudo_id", "nome", "carteirinha", "cid10"]
)
def test_phi_dado_de_beneficiario_nao_tem_onde_entrar(campo_beneficiario: str) -> None:
    prestadores = _prestadores()
    prestadores[0][campo_beneficiario] = "12345678900"
    with pytest.raises(BaselineRecusadaError, match="chaves fora do formato"):
        validar_baseline(_re_atestada(prestadores), tenant=T)


@pytest.mark.parametrize(
    ("mutacao", "trecho"),
    [
        (lambda p: p[0].update(situacao="SUSPENSO"), "situacao fora"),
        (lambda p: p[0].update(situacao="DESCONHECIDO"), "situacao fora"),
        (lambda p: p[1].update(prestador_id="SINT-0001"), "repetido"),
        (lambda p: p[0].update(prestador_id="SINT 0001"), "prestador_id fora"),
        (lambda p: p[0].update(data_efeito="01/03/2019"), "data ISO"),
        (lambda p: p[0].update(tipo_prestador="Clinica Grande"), "tipo_prestador fora do formato"),
    ],
)
def test_linha_fora_do_formato_derruba_a_importacao_inteira(mutacao: Any, trecho: str) -> None:
    prestadores = _prestadores()
    mutacao(prestadores)
    with pytest.raises(BaselineRecusadaError, match=trecho):
        validar_baseline(_re_atestada(prestadores), tenant=T)


def test_tenant_e_formato_conferidos() -> None:
    with pytest.raises(BaselineRecusadaError, match="tenant"):
        validar_baseline(baseline_atestada(), tenant="outro")
    with pytest.raises(BaselineRecusadaError, match="formato"):
        validar_baseline(baseline_atestada(formato="v0"), tenant=T)
    with pytest.raises(BaselineRecusadaError, match="lista nao-vazia"):
        validar_baseline(_re_atestada([]), tenant=T)


async def test_importacao_e_idempotente_e_nao_sobrescreve_delta_do_cred() -> None:
    led = LedgerMemoria()
    # SINT-0001 ja foi descredenciado por terminal humano do CRED antes da baseline chegar.
    await led.registrar(
        EntradaLedger(
            tenant=T,
            prestador_id="SINT-0001",
            origem=Origem.CRED,
            dimensao=Dimensao.SITUACAO,
            valor="DESCREDENCIADO",
            chave_evento="CRED:inst-1:End_PrestadorDescredenciado",
            fonte_ref="inst-1",
            evidencia_ref=f"CRED-{T}-SINT-0001",
            versao_projecao="mptt-p3-projecao/v1",
        )
    )
    baseline = validar_baseline(baseline_atestada(), tenant=T)
    primeira = await importar_baseline(baseline, led)
    assert dict(primeira.resultados) == {
        "SINT-0001": ResultadoLinha.CONFLITO,
        "SINT-0002": ResultadoLinha.GRAVADO,
    }
    segunda = await importar_baseline(baseline, led)
    assert dict(segunda.resultados) == {
        "SINT-0001": ResultadoLinha.CONFLITO,
        "SINT-0002": ResultadoLinha.JA_REGISTRADO,
    }
    assert (await ler_situacao(led, T, "SINT-0001")).situacao is SituacaoLida.DESCREDENCIADO
    leitura = await ler_situacao(led, T, "SINT-0002")
    assert leitura.situacao is SituacaoLida.DESCREDENCIADO and leitura.origem is Origem.BASELINE_AMH
    assert (await ler_situacao(led, T, "SINT-0404")).situacao is SituacaoLida.DESCONHECIDO
    assert primeira.contagem() == {"GRAVADO": 1, "JA_REGISTRADO": 0, "CONFLITO": 1}


def test_digest_e_canonico_independe_da_ordem_das_chaves() -> None:
    prestadores = _prestadores()
    invertido = [dict(reversed(list(linha.items()))) for linha in copy.deepcopy(prestadores)]
    assert digest_conteudo(T, "2026-11-01", prestadores) == digest_conteudo(T, "2026-11-01", invertido)
