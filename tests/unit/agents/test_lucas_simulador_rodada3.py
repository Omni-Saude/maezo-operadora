"""Simulador de beneficiario no dev (rodada 3, 13/10/2026): valores certos, comportamento errado.

Cada teste reproduz uma conversa da bateria contra o grafo REAL do Lucas, a DMN DRAFT (`DmnDraftLocal`) e o
motor em memoria compartilhado entre os turnos. A memoria curta do Lucas (migration 0025: referencia do
ultimo texto e avisos de caso aberto) passa de um turno ao outro como o roteador a passa.

  1. "A mensalidade de julho foi paga?" -> "Esta' tudo em dia! Sua ultima mensalidade (julho de 2026) ja'
     foi paga." — com setembro sem pagamento e outubro em aberto.
  2. "de qual ano?" -> o mesmo texto, identico.
  3. "Novembro ta' lancado?" em outubro -> "Sua mensalidade de novembro de 2025 foi de R$ 541,38...".
  4. "Essa e' a ultima? Nao e' novembro?" -> o mesmo texto de outubro, identico, varias vezes.
  5. O aviso de caso aberto repetido ate' a terceira vez (checagem D6).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.graph import (
    ACK_ESCALACAO,
    AVISOS_DE_CASO_ABERTO,
    NAO_CONSIGO_COM_OFERTA,
    LucasGraph,
    LucasState,
    new_lucas_state,
    ref_de_resposta,
)
from maezo.agents.lucas.prompts import motivo_de_recusa
from maezo.platform.webhooks.whatsapp.dispatch import _handoff_com_mes_do_texto
from maezo.runtime.cobranca_foco import decidir_foco_da_cobranca
from maezo.runtime.competencia import mes_unico_do_texto, resolver_ano_da_competencia
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink

_HOJE = date(2026, 10, 13)
_FECHO = "(dados de 12/10)"


def _mes(competencia: str, situacao: str, **extra: str) -> dict[str, str]:
    fato = {
        "competencia": competencia,
        "situacao": situacao,
        "vencimento": f"{competencia}-20",
        "valor_total": "552.54",
        "valor_coparticipacao": "0.00",
        "valor_saldo": "0.00" if situacao == "paga" else "552.54",
        "boleto": "****0042",
    }
    return {**fato, **extra}


_OUTUBRO = _mes("2026-10", "em_aberto")
#: Setembro em aberto com o vencimento ja' passado: a fonte ainda nao o marcou `vencida` (sem ciclo).
_SETEMBRO = _mes("2026-09", "em_aberto", boleto="****0041")
_AGOSTO = _mes("2026-08", "paga", liquidado_em="2026-08-19", boleto="****0040")
_JULHO = _mes("2026-07", "paga", liquidado_em="2026-08-18", boleto="****0039")
_FATOS = (_OUTUBRO, _SETEMBRO, _AGOSTO, _JULHO)


class _Inferencia:
    async def generate(self, prompt: str, **_: Any) -> str:
        del prompt
        return "Narrativa para o humano."


class _Envio:
    def __init__(self) -> None:
        self.textos: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash, idempotency_key
        self.textos.append(text)
        return {"ok": True}


class _Conversa:
    """Uma conversa: o mesmo motor e a memoria curta do Lucas de um turno para o outro."""

    def __init__(self) -> None:
        self.motor = FakeCibSevenTransport()
        self.turno = 0
        self.ref: str | None = None
        self.avisos = 0

    async def mensagem(self, **entrada: Any) -> tuple[dict[str, Any], str]:
        envio = _Envio()
        grafo = LucasGraph(
            inference=_Inferencia(),  # type: ignore[arg-type]
            dmn=DmnDraftLocal(),
            cibseven=self.motor,
            audit_sink=FakeStartAuditSink(),
            whatsapp=envio,
            hoje=lambda: _HOJE,
        )
        memoria: dict[str, Any] = {"avisos_anteriores": self.avisos, "variante_de_redacao": self.turno % 2}
        if self.ref is not None:
            memoria["resposta_anterior_ref"] = self.ref
        final = dict(await grafo.compile_graph().compile().ainvoke(_estado(**memoria, **entrada)))
        self.turno += 1
        [texto] = envio.textos
        # A referencia que o roteador guarda: o degrau da escada (rodada 4) ou, sem ele, o texto enviado.
        self.ref = final.get("resposta_memoria_ref") or ref_de_resposta(texto)
        self.avisos = final.get("avisos_de_caso_aberto") or 0
        return final, texto


def _estado(
    *, tipo: str = "status_pagamento", intencao: str = "confirmacao_pagamento", **extra: Any
) -> LucasState:
    return new_lucas_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_1313",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "pseudo-1313",
            "to_hash": "hash-1313",
            "intencao": intencao,
            "tipo_solicitacao": tipo,
            "status_conciliado": True,
            "ciclos_sem_conciliacao": 0,
            "numero_boleto": "****0042",
            "cnab_ref": "amh-billing:2026-10-12",
            "valor_em_aberto": "1105.08",
            "dias_atraso_max": 0,
            "competencias_cobranca": list(_FATOS),
            **extra,
        }
    )


def _sem_recusa(final: dict[str, Any], texto: str) -> None:
    fatos = final["mensagem"]["fatos"] | {"dados_de": "2026-10-12"}
    assert motivo_de_recusa(texto, "mensagem", fatos) is None, texto


# --- 1. "A mensalidade de julho foi paga?" --------------------------------------------------------------


def test_foi_paga_e_pergunta_de_pagamento_e_o_mes_vai_ao_lucas() -> None:
    pergunta = "A mensalidade de julho foi paga?"
    assert decidir_foco_da_cobranca(pergunta) == "pagamento"
    assert mes_unico_do_texto(pergunta) == 7
    handoff = {"cobranca_subtipo": "confirmacao_pagamento", "competencia": None, "cobranca_foco": "pagamento"}
    assert _handoff_com_mes_do_texto(pergunta, handoff)["competencia"] == "2026-07"


async def test_mes_perguntado_recebe_o_fato_daquele_mes() -> None:
    final, texto = await _Conversa().mensagem(competencia="2026-07", cobranca_foco="pagamento")
    assert final["route"] == "respond_member"
    assert texto == f"Sim, a mensalidade de julho de 2026 foi paga em 18/08. {_FECHO}"
    _sem_recusa(final, texto)


async def test_em_dia_so_sem_mes_em_aberto_e_ultima_e_a_mais_recente() -> None:
    final, texto = await _Conversa().mensagem()
    assert "tudo em dia" not in texto.lower() and "última" not in texto
    assert texto == (
        "A mensalidade de setembro de 2026 ainda não consta como paga. A mensalidade de outubro de 2026 "
        "(boleto final 0042) vence em 20/10 e ainda não consta como paga. A de agosto de 2026 já foi paga. "
        f"{_FECHO}"
    )
    _sem_recusa(final, texto)


# --- 2. "de qual ano?" ----------------------------------------------------------------------------------


async def test_de_qual_ano_responde_curto_com_o_ano_e_nunca_igual() -> None:
    conversa = _Conversa()
    _, primeiro = await conversa.mensagem(competencia="2026-07", cobranca_foco="pagamento")
    final, segundo = await conversa.mensagem(competencia="2026-07", cobranca_foco="esclarecimento")
    assert segundo == "É julho de 2026: paga em 18/08."
    assert segundo != primeiro
    _sem_recusa(final, segundo)
    _, terceiro = await conversa.mensagem(competencia="2026-07", cobranca_foco="esclarecimento")
    # Rodada 4: a mesma pergunta de novo nao vira "Confirmando: ..." — o Lucas diz que e' o que tem e oferece
    # alguem da equipe.
    assert terceiro == NAO_CONSIGO_COM_OFERTA


# --- 3. "Novembro ta' lancado?" -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pergunta", "esperado"),
    [
        ("Novembro tá lançado?", "2026-11"),
        ("E novembro? Já tá no sistema?", "2026-11"),
        ("quando vence a de novembro?", "2026-11"),
        ("Essa é a última? Não é novembro?", "2026-11"),
        # Sem pista de mensalidade que vem: a ocorrencia passada (a regra de antes).
        ("quanto paguei em novembro?", "2025-11"),
    ],
)
def test_ano_do_mes_pela_pista_da_pergunta(pergunta: str, esperado: str) -> None:
    assert resolver_ano_da_competencia("2025-11", pergunta, _HOJE) == esperado


def test_janeiro_lancado_em_dezembro_e_do_ano_que_vem() -> None:
    assert resolver_ano_da_competencia("2026-01", "janeiro já tá lançado?", date(2026, 12, 10)) == "2027-01"


@pytest.mark.parametrize(
    ("tipo", "intencao"), [("consulta_valores", "cobranca_info"), ("vencimento", "cobranca_info")]
)
async def test_mes_que_ainda_nao_existe_nao_foi_gerado(tipo: str, intencao: str) -> None:
    final, texto = await _Conversa().mensagem(tipo=tipo, intencao=intencao, competencia="2026-11")
    assert texto.startswith("A mensalidade de novembro de 2026 ainda não foi gerada.")
    assert "2025" not in texto
    _sem_recusa(final, texto)


# --- 4. "Essa e' a ultima? Nao e' novembro?" ------------------------------------------------------------


async def test_confirmacao_da_ultima_responde_sim_e_nao_repete() -> None:
    conversa = _Conversa()
    pergunta = "Essa é a última? Não é novembro?"
    assert decidir_foco_da_cobranca(pergunta) == "ultima"
    entrada = {"tipo": "consulta_valores", "intencao": "cobranca_info", "competencia": "2026-11"}
    final, primeiro = await conversa.mensagem(**entrada, cobranca_foco="ultima")
    assert primeiro == (
        "Sim, a mais recente é a de outubro de 2026 (R$ 552,54, vence em 20/10). A de novembro de 2026 ainda "
        f"não foi gerada. {_FECHO}"
    )
    _sem_recusa(final, primeiro)
    _, segundo = await conversa.mensagem(**entrada, cobranca_foco="ultima")
    # Rodada 4: a repeticao sai CURTA e responde o que foi perguntado (novembro).
    assert segundo == "A mensalidade de novembro de 2026 ainda não foi gerada."


# --- 5. Avisos de caso aberto: no maximo dois ----------------------------------------------------------


async def test_depois_de_dois_avisos_so_o_fato() -> None:
    conversa = _Conversa()
    contestacao = {"tipo": "", "intencao": "cobranca_info", "contesta_cobranca": True}
    _, primeiro = await conversa.mensagem(**contestacao)
    assert primeiro == ACK_ESCALACAO
    textos = [primeiro]
    for _ in range(2):
        _, texto = await conversa.mensagem(**contestacao)
        # Rodada 4: a reclamacao com o caso aberto e' reconhecida (nunca "outra duvida").
        assert "nossa equipe" in texto and "outra dúvida" not in texto
        textos.append(texto)
    assert conversa.avisos == 2
    for _ in range(3):
        _, texto = await conversa.mensagem(**contestacao)
        assert not any(aviso in texto for aviso in AVISOS_DE_CASO_ABERTO)
        assert "outra dúvida" not in texto
        textos.append(texto)
    # Pergunta de fato com o caso aberto e os avisos esgotados: so' o fato.
    _, fato = await conversa.mensagem(competencia="2026-07", cobranca_foco="pagamento")
    assert fato == f"Sim, a mensalidade de julho de 2026 foi paga em 18/08. {_FECHO}"
    textos.append(fato)
    assert all(a != b for a, b in zip(textos, textos[1:], strict=False)), textos
