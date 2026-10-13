"""Simulador de beneficiario no dev (rodada 4, 13/10/2026): valores certos, comportamento errado.

Cada teste reproduz uma conversa da bateria 3 contra o grafo REAL do Lucas, a DMN DRAFT (`DmnDraftLocal`) e o
motor em memoria compartilhado entre os turnos; a memoria curta do Lucas passa de um turno ao outro como o
roteador a passa (migration 0025).

  1. Contrato de empresa: "Sua mensalidade e' paga pela empresa contratante." e "Confirmando: ..." alternados
     9 vezes para "de qual ano?", "como assim?", "foi paga ou nao?".
  2. O "Confirmando:" saiu: a repeticao e' curta e responde; insistencia -> o que ele nao consegue + pessoa.
  3. "Quanto foi agosto e setembro?" -> so' outubro, 8 vezes.
  4. "FUI COBRADO DUAS VEZES NO MES PASSADO" + mensagens irritadas -> "Entendi. Para outra duvida...".
  5. Depois da escalacao por agosto e setembro sem pagamento, "quais os valores?" -> outubro.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.graph import (
    ACK_ESCALACAO,
    NAO_CONSIGO,
    NAO_CONSIGO_COM_OFERTA,
    TEXTO_PAGADOR_EMPRESA,
    LucasGraph,
    LucasState,
    new_lucas_state,
    ref_de_resposta,
)
from maezo.agents.lucas.prompts import motivo_de_recusa
from maezo.platform.webhooks.whatsapp.dispatch import _handoff_com_mes_do_texto
from maezo.runtime.cobranca_lista import PedidoDeLista, decidir_lista_da_cobranca
from maezo.runtime.cobranca_reclamacao import decidir_reclamacao
from maezo.runtime.competencia import mes_relativo_do_texto
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink

_HOJE = date(2026, 10, 13)
_FECHO = "(dados de 12/10)"


def _mes(competencia: str, situacao: str, **extra: str) -> dict[str, str]:
    fato = {
        "competencia": competencia,
        "situacao": situacao,
        "vencimento": f"{competencia}-15",
        "valor_total": "966.52",
        "valor_coparticipacao": "0.00",
        "valor_saldo": "0.00" if situacao == "paga" else "966.52",
        "boleto": "****0042",
    }
    return {**fato, **extra}


_OUTUBRO = _mes("2026-10", "em_aberto")
_SETEMBRO_SEM_PAGAMENTO = _mes("2026-09", "vencida", boleto="****0041")
_AGOSTO_SEM_PAGAMENTO = _mes("2026-08", "vencida", boleto="****0040")
_JULHO = _mes("2026-07", "paga", liquidado_em="2026-07-14", boleto="****0039", valor_coparticipacao="35.20")

#: Pessoa com agosto e setembro sem pagamento confirmado (a DMN escala) e outubro em aberto no prazo.
_COM_ATRASO: dict[str, Any] = {
    "status_conciliado": False,
    "ciclos_sem_conciliacao": 2,
    "numero_boleto": "****0042",
    "valor_em_aberto": "2899.56",
    "dias_atraso_max": 59,
    "competencias_cobranca": [_OUTUBRO, _SETEMBRO_SEM_PAGAMENTO, _AGOSTO_SEM_PAGAMENTO, _JULHO],
}
#: Contrato de EMPRESA (a fatura e' da contratante).
_EMPRESA: dict[str, Any] = {
    "status_conciliado": True,
    "ciclos_sem_conciliacao": 0,
    "numero_boleto": "",
    "valor_em_aberto": "0.00",
    "dias_atraso_max": 0,
    "competencias_cobranca": [_JULHO],
    "pagador_tipo": "pessoa_juridica",
}
_EM_DIA: dict[str, Any] = {
    "status_conciliado": True,
    "ciclos_sem_conciliacao": 0,
    "numero_boleto": "****0042",
    "valor_em_aberto": "0.00",
    "dias_atraso_max": 0,
    "competencias_cobranca": [
        _mes("2026-09", "paga", liquidado_em="2026-09-14"),
        _mes("2026-08", "paga", liquidado_em="2026-08-14", valor_total="941.10"),
        _JULHO,
    ],
}


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

    def __init__(self, fatos: dict[str, Any]) -> None:
        self.fatos = fatos
        self.motor = FakeCibSevenTransport()
        self.turno = 0
        self.ref: str | None = None
        self.avisos = 0
        self.textos: list[str] = []

    async def mensagem(
        self, *, tipo: str = "consulta_valores", intencao: str = "cobranca_info", **entrada: Any
    ) -> tuple[dict[str, Any], str]:
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
        estado: LucasState = new_lucas_state(
            {
                "tenant_id": "amh",
                "conversation_id": "wa:amh:hk1_1314",
                "canal": "whatsapp",
                "beneficiario_pseudo_id": "pseudo-1314",
                "to_hash": "hash-1314",
                "intencao": intencao,
                "tipo_solicitacao": tipo,
                "cnab_ref": "amh-billing:2026-10-12",
                **self.fatos,
                **memoria,
                **entrada,
            }
        )
        final = dict(await grafo.compile_graph().compile().ainvoke(estado))
        self.turno += 1
        [texto] = envio.textos
        # A referencia que o roteador guarda: o degrau da escada (rodada 4) ou, sem ele, o texto enviado.
        self.ref = final.get("resposta_memoria_ref") or ref_de_resposta(texto)
        self.avisos = final.get("avisos_de_caso_aberto") or 0
        self.textos.append(texto)
        return final, texto


def _sem_repeticao(textos: list[str]) -> None:
    assert all(a != b for a, b in zip(textos, textos[1:], strict=False)), textos
    assert not any("Confirmando" in t for t in textos), textos


# --- 1 e 2. Contrato de empresa: a consequencia uma vez, o ano, e nada repetido ---------------------------


async def test_empresa_explica_uma_vez_responde_o_ano_e_nunca_repete() -> None:
    conversa = _Conversa(_EMPRESA)
    julho: dict[str, Any] = {
        "tipo": "status_pagamento",
        "intencao": "confirmacao_pagamento",
        "competencia": "2026-07",
    }
    # "A mensalidade de julho foi paga?"
    _, primeiro = await conversa.mensagem(**julho, cobranca_foco="pagamento")
    assert primeiro == (
        f"{TEXTO_PAGADOR_EMPRESA} Por isso, a situação do pagamento de julho de 2026 fica com a empresa "
        "(normalmente o RH). Se quiser, posso te passar para alguém da nossa equipe."
    )
    # "de qual ano?"
    _, ano = await conversa.mensagem(**julho, cobranca_foco="esclarecimento")
    assert ano == "É julho de 2026. O pagamento dela é feito pela empresa contratante."
    # "como assim?"
    _, de_novo = await conversa.mensagem(**julho, cobranca_foco="esclarecimento")
    assert (
        de_novo
        == "A confirmação do pagamento de julho de 2026 é com a empresa contratante, normalmente pelo RH."
    )
    # "julho 2026 foi paga ou nao?", "foi paga pela empresa ou nao?", ...
    for _ in range(4):
        _, texto = await conversa.mensagem(**julho, cobranca_foco="pagamento")
        assert texto in NAO_CONSIGO
    _sem_repeticao(conversa.textos)
    # A coparticipacao e' da pessoa: continua respondida.
    _, copart = await conversa.mensagem(competencia="2026-07", cobranca_foco="coparticipacao")
    # Rodada 5: a coparticipacao e' da pessoa — so' o fato dela, sem a frase da empresa.
    assert copart == f"Em julho de 2026 sua coparticipação foi de R$ 35,20. {_FECHO}"


async def test_pessoa_fisica_que_insiste_recebe_a_curta_e_depois_a_oferta_uma_vez() -> None:
    conversa = _Conversa(_EM_DIA)
    pergunta: dict[str, Any] = {
        "tipo": "status_pagamento",
        "intencao": "confirmacao_pagamento",
        "competencia": "2026-07",
    }
    textos = [(await conversa.mensagem(**pergunta, cobranca_foco="pagamento"))[1] for _ in range(5)]
    assert textos[0] == f"Sim, a mensalidade de julho de 2026 foi paga em 14/07. {_FECHO}"
    assert textos[1] == "É julho de 2026: paga em 14/07."
    assert textos[2] == NAO_CONSIGO_COM_OFERTA
    assert textos[3:] == list(NAO_CONSIGO)
    assert sum(t == NAO_CONSIGO_COM_OFERTA for t in textos) == 1
    _sem_repeticao(textos)


# --- 3. Dois meses nomeados ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pergunta",
    [
        "Quanto foi agosto e setembro?",
        "os valores de agosto e setembro",
        "agosto e setembro tão sem pagar mesmo?",
    ],
)
def test_dois_meses_nomeados_viram_lista(pergunta: str) -> None:
    lista = decidir_lista_da_cobranca(pergunta, _HOJE)
    assert lista is not None and lista.meses == ("2026-08", "2026-09")
    assert PedidoDeLista(2, meses=("2026-08", "2026-09")).como_handoff()["meses"] == ["2026-08", "2026-09"]


async def test_dois_meses_com_dado_listam_os_dois() -> None:
    conversa = _Conversa(_EM_DIA)
    lista = PedidoDeLista(2, meses=("2026-08", "2026-09")).como_handoff()
    final, texto = await conversa.mensagem(cobranca_lista=lista)
    assert texto == (
        "Suas mensalidades de agosto de 2026 e setembro de 2026:\n"
        "• setembro de 2026: R$ 966,52, paga em 14/09.\n"
        "• agosto de 2026: R$ 941,10, paga em 14/08.\n"
        f"{_FECHO}"
    )
    fatos = final["mensagem"]["fatos"] | {"dados_de": "2026-10-12"}
    assert motivo_de_recusa(texto, "mensagem", fatos) is None


# --- 5. Escalacao por agosto e setembro e a pergunta dos valores deles --------------------------------


async def test_meses_sem_pagamento_tem_valores_na_escalacao_e_no_caso_aberto() -> None:
    conversa = _Conversa(_COM_ATRASO)
    lista = PedidoDeLista(2, meses=("2026-08", "2026-09")).como_handoff()
    final, primeiro = await conversa.mensagem(cobranca_lista=lista)
    assert final["route"] == "escalate_human"
    linhas = (
        "• setembro de 2026: R$ 966,52, vencimento em 15/09, sem pagamento confirmado.",
        "• agosto de 2026: R$ 966,52, vencimento em 15/08, sem pagamento confirmado.",
    )
    assert all(linha in primeiro for linha in linhas) and "outubro" not in primeiro
    assert "Vou passar para alguém da nossa equipe" in primeiro
    # "agosto e setembro tao sem pagar mesmo? quais os valores?"
    final, seguinte = await conversa.mensagem(cobranca_lista=lista)
    assert final["caso_aberto_cobranca"] is True
    assert all(linha in seguinte for linha in linhas) and "outubro" not in seguinte
    assert "Vou passar" not in seguinte
    # Um mes so', com o caso aberto: o fato DAQUELE mes, com valor e vencimento.
    _, agosto = await conversa.mensagem(competencia="2026-08")
    assert agosto.startswith(
        "A mensalidade de agosto de 2026 (R$ 966,52, vencimento em 15/08) ainda está sem pagamento "
        "confirmado."
    )
    _sem_repeticao(conversa.textos)


# --- 4. Reclamacao com o caso aberto ------------------------------------------------------------------


def test_reclamacao_e_mes_passado_saem_do_texto() -> None:
    texto = "FUI COBRADO DUAS VEZES NO MES PASSADO"
    assert decidir_reclamacao(texto) == "em_dobro"
    assert decidir_reclamacao("o valor tá errado") == "valor_errado"
    assert decidir_reclamacao("não reconheço essa cobrança") == "nao_reconhece"
    assert decidir_reclamacao("quando vence?") is None
    assert mes_relativo_do_texto(texto, _HOJE) == "2026-09"
    assert mes_relativo_do_texto("o boleto deste mês", _HOJE) == "2026-10"
    handoff = {"cobranca_subtipo": "contestacao", "competencia": None, "cobranca_foco": "geral"}
    ajustado = _handoff_com_mes_do_texto(texto, handoff)
    assert ajustado["competencia"] == "2026-09" and ajustado["cobranca_reclamacao"] == "em_dobro"


async def test_reclamacao_com_caso_aberto_e_reconhecida_sem_outra_duvida() -> None:
    conversa = _Conversa(_EM_DIA)
    reclamacao: dict[str, Any] = {
        "tipo": "",
        "contesta_cobranca": True,
        "competencia": "2026-09",
        "cobranca_reclamacao": "em_dobro",
    }
    _, primeiro = await conversa.mensagem(**reclamacao)
    assert primeiro == ACK_ESCALACAO
    seguintes = [(await conversa.mensagem(**reclamacao))[1] for _ in range(5)]
    # Rodada 5: a primeira reconhece a reclamacao e da' os fatos do mes UMA vez; as seguintes sao curtas.
    assert seguintes[0].startswith(
        "Sua reclamação sobre a cobrança em dobro de setembro de 2026 já está com a nossa equipe."
    )
    assert any(t.startswith("Entendo a sua chateação") for t in seguintes)
    for texto in seguintes:
        assert "outra dúvida" not in texto and "prazo" not in texto and "Vou passar" not in texto
    _sem_repeticao(conversa.textos)
    assert len(conversa.motor._history["ESC-amh-wa:amh:hk1_1314"]) == 1
