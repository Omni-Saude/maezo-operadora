"""Simulador de beneficiario no dev (rodada 2, 13/10/2026): Lucas certo nos valores, errado no comportamento.

Cada teste reproduz uma conversa da bateria (persona, mensagens e fatos da AMH no formato do contrato),
contra o grafo REAL do Lucas, a DMN DRAFT lida do XML (`DmnDraftLocal`) e o motor de processos em memoria
(`FakeCibSevenTransport`, compartilhado entre os turnos da mesma conversa, como o motor real).

  1. Idosa: "nao sei se ja' paguei a mensalidade desse mes e quando vence a proxima" -> "Vi aqui uma
     mensalidade ainda sem pagamento confirmado (boleto final X)" + escalacao, repetidos 10 vezes.
  2. Contestacao: "vao corrigir?", "quanto tempo demora?" -> o mesmo ACK de escalacao a cada mensagem.
  3. "paguei dia 5, aparece 15/09" -> "Esta' tudo em dia!" tres vezes, ignorando a pergunta.
  4. 2a via sem acesso ao aplicativo -> os mesmos canais quatro vezes.
  5. "Quanto e' o TOTAL em aberto, tudo junto?" -> ora o total, ora o valor de um mes.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from maezo.agents.lucas.graph import (
    ACK_ESCALACAO,
    AVISOS_DE_CASO_ABERTO,
    SEGUNDA_VIA_CANAIS,
    LucasGraph,
    LucasState,
    new_lucas_state,
)
from maezo.agents.lucas.prompts import motivo_de_recusa
from maezo.runtime.cobranca_foco import decidir_foco_da_cobranca
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink

_HOJE = date(2026, 10, 13)
_OUTUBRO_NO_PRAZO = (
    "A mensalidade de outubro de 2026 (boleto final 0042) vence em 15/10 e ainda não consta como paga."
)
_OUTUBRO_COM_VALOR = (
    "A mensalidade de outubro de 2026 (boleto final 0042) vence em 15/10, no valor de R$ 966,52."
)
_FECHO = "(dados de 12/10)"
_CNAB = "amh-billing:2026-10-12"

_OUTUBRO = {
    "competencia": "2026-10",
    "situacao": "em_aberto",
    "vencimento": "2026-10-15",
    "valor_total": "966.52",
    "valor_coparticipacao": "0.00",
    "valor_saldo": "966.52",
    "boleto": "****0042",
}
_SETEMBRO = {
    "competencia": "2026-09",
    "situacao": "paga",
    "vencimento": "2026-09-15",
    "valor_total": "966.52",
    "valor_coparticipacao": "0.00",
    "valor_saldo": "0.00",
    "liquidado_em": "2026-09-15",
    "boleto": "****0037",
}
_AGOSTO_SEM_PAGAMENTO = {
    "competencia": "2026-08",
    "situacao": "vencida",
    "vencimento": "2026-08-15",
    "valor_total": "966.52",
    "valor_coparticipacao": "0.00",
    "valor_saldo": "966.52",
    "boleto": "****0031",
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
    """Uma conversa: o MESMO motor de processos em todos os turnos (o caso aberto persiste)."""

    def __init__(self) -> None:
        self.motor = FakeCibSevenTransport()
        self.turno = 0

    async def mensagem(self, **entrada: Any) -> tuple[dict[str, Any], list[str]]:
        envio = _Envio()
        grafo = LucasGraph(
            inference=_Inferencia(),  # type: ignore[arg-type]
            dmn=DmnDraftLocal(),
            cibseven=self.motor,
            audit_sink=FakeStartAuditSink(),
            whatsapp=envio,
            hoje=lambda: _HOJE,
        )
        estado = _estado(variante_de_redacao=self.turno % 2, **entrada)
        self.turno += 1
        final = dict(await grafo.compile_graph().compile().ainvoke(estado))
        return final, envio.textos

    def instancias(self) -> int:
        return len(self.motor._history.get("ESC-amh-wa:amh:hk1_1310", []))


def _estado(
    *,
    tipo: str = "status_pagamento",
    intencao: str = "confirmacao_pagamento",
    competencias: tuple[dict[str, str], ...] = (_OUTUBRO, _SETEMBRO),
    conciliado: bool = True,
    ciclos: int = 0,
    **extra: Any,
) -> LucasState:
    vencida = any(c["situacao"] == "vencida" for c in competencias)
    return new_lucas_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_1310",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "pseudo-1310",
            "to_hash": "hash-1310",
            "intencao": intencao,
            "tipo_solicitacao": tipo,
            "status_conciliado": conciliado,
            "ciclos_sem_conciliacao": ciclos,
            "numero_boleto": "****0042",
            "cnab_ref": _CNAB,
            "valor_em_aberto": "1933.04" if vencida else "966.52",
            "dias_atraso_max": 59 if vencida else 0,
            "competencias_cobranca": list(competencias),
            **extra,
        }
    )


def _sem_repeticao(textos: list[str]) -> None:
    assert all(a != b for a, b in zip(textos, textos[1:], strict=False)), textos


# --- 1. Mensalidade em aberto NO PRAZO: fato, sem escalacao ------------------------------------------


async def test_em_aberto_no_prazo_responde_com_fatos_sem_escalar() -> None:
    """A pergunta da idosa, sem nenhuma mensalidade vencida nos fatos: nada de escalacao."""
    final, textos = await _Conversa().mensagem()
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert textos == [
        "Está tudo em dia! Sua última mensalidade (setembro de 2026) já foi paga. A de outubro de 2026 "
        f"(boleto final 0042) vence em 15/10 e ainda não consta como paga. {_FECHO}"
    ]
    fatos = final["mensagem"]["fatos"] | {"dados_de": "2026-10-12", "competencias": [_OUTUBRO, _SETEMBRO]}
    assert motivo_de_recusa(textos[0], "mensagem", fatos) is None


async def test_quando_vence_com_em_aberto_no_prazo_nao_escala() -> None:
    final, textos = await _Conversa().mensagem(tipo="vencimento", intencao="cobranca_info")
    assert final["route"] == "respond_member" and final["process_started"] is False
    assert textos == [f"A mensalidade de outubro de 2026 vence em 15/10, no valor de R$ 966,52. {_FECHO}"]


# --- 1b. Com uma mensalidade VENCIDA a DMN escala: fato primeiro, e nunca de novo a cada mensagem -----


async def test_com_vencida_a_escalacao_diz_o_fato_e_nao_se_repete() -> None:
    conversa = _Conversa()
    atraso = {
        "competencias": (_OUTUBRO, _SETEMBRO, _AGOSTO_SEM_PAGAMENTO),
        "conciliado": False,
        "ciclos": 1,
    }
    final, primeiro = await conversa.mensagem(**atraso)
    assert final["route"] == "escalate_human" and final["motivo_humano"] == "inadimplencia_detectada"
    assert primeiro == [
        "A mensalidade de agosto de 2026 (boleto final 0031) ainda está sem pagamento confirmado. "
        "A de outubro de 2026 (boleto final 0042) vence em 15/10 e ainda não consta como paga. "
        "Vou passar para alguém da nossa equipe, que vai te responder por aqui. "
        f"Fique tranquilo: nenhuma decisão sobre o seu plano foi tomada. {_FECHO}"
    ]
    # "nao quero falar com outra pessoa, so' me diz se o boleto e' de outubro ou setembro e quando vence"
    textos = list(primeiro)
    for _ in range(3):
        final, enviados = await conversa.mensagem(**atraso)
        assert final["caso_aberto_cobranca"] is True
        [texto] = enviados
        assert texto.startswith(_OUTUBRO_NO_PRAZO)
        assert "Vou passar" not in texto
        textos.append(texto)
    _sem_repeticao(textos)
    assert conversa.instancias() == 1, "o caso aberto nao e' reaberto"


# --- 2. Contestacao com o caso ja' com a equipe -------------------------------------------------------


async def test_contestacao_seguinte_reconhece_o_caso_sem_repetir() -> None:
    conversa = _Conversa()
    contestacao = {"tipo": "", "intencao": "cobranca_info", "contesta_cobranca": True}
    final, primeiro = await conversa.mensagem(
        **contestacao
    )  # "O valor cobrado no boleto desse mes ta' errado"
    assert final["route"] == "escalate_human" and primeiro == [ACK_ESCALACAO]
    textos = list(primeiro)
    for _ in range(3):  # "vao corrigir?", "quanto tempo demora?", ...
        _, enviados = await conversa.mensagem(**contestacao)
        [texto] = enviados
        assert texto in AVISOS_DE_CASO_ABERTO
        assert "Vou passar" not in texto and "prazo" not in texto
        textos.append(texto)
    _sem_repeticao(textos)
    assert conversa.instancias() == 1


# --- 3. Pergunta especifica com o caso aberto: o fato, nunca o "em dia" generico ----------------------


async def test_data_de_pagamento_com_caso_aberto_responde_o_fato_especifico() -> None:
    conversa = _Conversa()
    # "paguei dia 5, aparece 15/09" -> contestacao, escalada.
    await conversa.mensagem(tipo="", intencao="cobranca_info", contesta_cobranca=True, competencia="2026-09")
    textos: list[str] = []
    for _ in range(3):
        final, enviados = await conversa.mensagem(competencia="2026-09")
        assert final["route"] == "respond_member"
        [texto] = enviados
        assert "Está tudo em dia" not in texto
        assert texto.startswith("A mensalidade de setembro de 2026 consta como paga em 15/09.")
        assert texto.endswith(tuple(AVISOS_DE_CASO_ABERTO))
        textos.append(texto)
    _sem_repeticao(textos)


async def test_caso_aberto_pela_helena_nao_muda_a_resposta_do_lucas() -> None:
    from maezo.tools.mcp_cibseven.transport import ProcessInstance

    conversa = _Conversa()
    conversa.motor.seed_instance(
        ProcessInstance("i-1", "SP-OP-ESCALATION-001", "ESC-amh-wa:amh:hk1_1310", "ACTIVE"),
        variables={"source_agent_id": "helena"},
    )
    final, textos = await conversa.mensagem()
    assert final["caso_aberto_cobranca"] is False
    assert textos[0].startswith("Está tudo em dia!")


# --- 4. 2a via pedida de novo -------------------------------------------------------------------------


async def test_segunda_via_repetida_diz_que_nao_envia_e_da_o_que_tem() -> None:
    conversa = _Conversa()
    segunda_via = {"tipo": "2a_via", "intencao": "cobranca_info"}
    _, primeiro = await conversa.mensagem(**segunda_via)
    assert primeiro[0].startswith(SEGUNDA_VIA_CANAIS)
    textos = list(primeiro)
    for _ in range(3):  # "nao consigo acessar o app", "me manda aqui pelo WhatsApp", ...
        final, enviados = await conversa.mensagem(**segunda_via, assunto_anterior="boleto_2via")
        [texto] = enviados
        assert SEGUNDA_VIA_CANAIS not in texto
        assert "boleto" in texto and "central de atendimento" in texto and "nossa equipe" in texto
        assert (
            "A mensalidade de outubro de 2026 (boleto final 0042) vence em 15/10, no valor de R$ 966,52."
            in texto
        )
        fatos = final["mensagem"]["fatos"] | {"dados_de": "2026-10-12"}
        assert motivo_de_recusa(texto, "mensagem", fatos) is None
        textos.append(texto)
    _sem_repeticao(textos)
    assert textos[2] == (
        "Por aqui eu não consigo enviar o boleto. A mensalidade de outubro de 2026 (boleto final 0042) vence "
        "em 15/10, no valor de R$ 966,52. A 2ª via também sai pela central de atendimento. Se quiser, passo "
        "para alguém da nossa equipe."
    )


# --- 5. Total em aberto -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pergunta",
    ["Quanto é o TOTAL em aberto, tudo junto?", "e quanto dá tudo junto?", "quanto estou devendo no total?"],
)
def test_pergunta_de_total_e_saldo(pergunta: str) -> None:
    assert decidir_foco_da_cobranca(pergunta) == "saldo"


async def test_total_em_aberto_diz_os_meses_e_e_o_mesmo_nos_turnos() -> None:
    conversa = _Conversa()
    textos = []
    for _ in range(2):
        _, enviados = await conversa.mensagem(
            tipo="consulta_valores", intencao="cobranca_info", cobranca_foco="saldo"
        )
        textos.extend(enviados)
    assert textos == [f"Seu total em aberto hoje é R$ 966,52: outubro de 2026. {_FECHO}"] * 2
