"""DL-0082 (decisao do dono de 08/10/2026): o Lucas decide pelos FATOS da fonte, nunca pela pergunta.

INCIDENTE (dev, 08/10/2026 01:13 UTC). "minha mensalidade esta em aberto?" -> Helena passou ao Lucas
com `cobranca_subtipo=cobranca_recebida` -> a fonte AMH disse `status_conciliado=True`, zero ciclos
sem conciliacao -> mesmo assim o Lucas gravou `intencao=inadimplencia`, pulou a DMN de
admissibilidade (`_is_escalation_intent`), abriu SP-OP-ESCALATION-001 e o ACK REDIGIDO PELO MODELO
disse "Recebemos a informacao sobre a inadimplencia detectada".

O que este arquivo prova, contra o grafo real e as DMN DRAFT lidas do XML (`DmnDraftLocal`):
  - fato conciliado -> resposta FIXA "em dia", sem processo, sem modelo, sem "inadimplencia";
  - ciclos > 0 -> a escalacao de sempre (regra de atraso da DMN), com ACK FIXO que informa a
    mensalidade em aberto sem acusar;
  - fato ausente (`Indisponivel`) -> ACK FIXO que diz que nao deu para consultar, nunca um fato;
  - o ACK de qualquer escalacao e' texto fixo e passa pela cerca de saida.
"""

from __future__ import annotations

import unicodedata
from typing import Any

import pytest

from maezo.agents.lucas.graph import (
    ACK_ESCALACAO,
    RESPOSTA_INFORMATIVA_RECUSADA,
    LucasGraph,
    LucasState,
    texto_ack_escalacao,
    texto_mensalidade_em_dia,
)
from maezo.agents.lucas.prompts import RECUSA_ROTULO_DE_INADIMPLENCIA, motivo_de_recusa
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink

#: A frase que o modelo escreveu no incidente. O fake devolve SEMPRE ela: se qualquer texto de
#: modelo chegasse ao beneficiario, os testes abaixo a veriam.
_RASCUNHO_DO_INCIDENTE = (
    "Olá! Recebemos a informação sobre a inadimplência detectada e um de nossos atendentes "
    "especializados entrará em contato."
)

_FATOS_AMH: dict[str, Any] = {"numero_boleto": "****7869", "cnab_ref": "amh-billing:2026-07-28"}


class _Inferencia:
    def __init__(self) -> None:
        self.task_kinds: list[str | None] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        del prompt, phi, agent_id, tenant_id
        self.task_kinds.append(task_kind)
        return _RASCUNHO_DO_INCIDENTE


class _Envio:
    def __init__(self) -> None:
        self.textos: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash, idempotency_key
        self.textos.append(text)
        return {"ok": True}


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).lower()


def _estado(**fatos: Any) -> LucasState:
    estado: dict[str, Any] = {
        "tenant_id": "amh",
        "conversation_id": "wa:amh:hk1_dl0082",
        "canal": "whatsapp",
        "beneficiario_pseudo_id": "pseudo-dl0082",
        "to_hash": "hash-dl0082",
        # A linha de `cobranca_recebida` em `lucas_turno.ENTRADA_POR_SUBTIPO`.
        "intencao": "inadimplencia",
        "tipo_solicitacao": "status_pagamento",
        "competencia": "2026-07",
        **fatos,
    }
    return estado  # type: ignore[return-value]


async def _turno(estado: LucasState) -> tuple[dict[str, Any], _Envio, _Inferencia]:
    envio = _Envio()
    inferencia = _Inferencia()
    grafo = LucasGraph(
        inference=inferencia,  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=envio,
    )
    final = dict(await grafo.compile_graph().compile().ainvoke(estado))
    return final, envio, inferencia


async def test_fato_conciliado_responde_em_dia_sem_processo_sem_modelo_e_sem_inadimplencia() -> None:
    final, envio, inferencia = await _turno(
        _estado(status_conciliado=True, ciclos_sem_conciliacao=0, **_FATOS_AMH)
    )

    assert final["route"] == "respond_member"
    assert final["admissibilidade"] == "RESPONDER"
    assert final["process_started"] is False
    assert final["motivo_humano"] == ""
    assert final["dossier"] == {}
    assert envio.textos == [
        "Consultei aqui: o pagamento da sua mensalidade (boleto final 7869) consta como conciliado, então "
        "ela está em dia. Essa informação é conforme os dados de 28/07/2026."
    ]
    # 09/10/2026: o WhatsApp come asterisco (negrito) — o boleto vai uma vez so', sem asterisco.
    assert "*" not in envio.textos[0] and envio.textos[0].count("boleto") == 1
    assert "inadimpl" not in _sem_acento(envio.textos[0])
    assert inferencia.task_kinds == [], "com o fato dizendo tudo, nenhum modelo redige a resposta"
    assert final["mensagem"]["recusa_de_saida"] is False


async def test_ciclos_em_aberto_seguem_a_escalacao_de_sempre_com_ack_fixo_sem_acusacao() -> None:
    final, envio, inferencia = await _turno(
        _estado(status_conciliado=False, ciclos_sem_conciliacao=1, **_FATOS_AMH)
    )

    assert final["route"] == "escalate_human"
    assert final["motivo_humano"] == "inadimplencia_detectada"
    assert final["roteamento_escalacao"] == "COBRANCA_HUMANO"
    assert final["process_started"] is True
    assert list(final["dmn_refs"]) == ["lucas_billing_admissibility", "lucas_escalation_routing"]
    assert envio.textos == [
        "Consultei aqui e consta uma mensalidade em aberto, ainda sem pagamento conciliado (boleto final "
        "7869). Essa informação é conforme os dados de 28/07/2026. " + ACK_ESCALACAO
    ]
    assert "*" not in envio.textos[0]
    assert "inadimpl" not in _sem_acento(envio.textos[0])
    # So' a narrativa do dossie (lida pelo humano) chama o modelo; o ACK nao.
    assert inferencia.task_kinds == ["reasoning"]


async def test_fonte_indisponivel_diz_que_nao_conseguiu_consultar_e_escala_sem_afirmar_nada() -> None:
    # `Indisponivel` = os campos de conciliacao AUSENTES na entrada (lucas_turno.entrada_do_lucas).
    final, envio, _ = await _turno(_estado())

    assert final["route"] == "escalate_human"
    assert final["motivo_humano"] == "ambiguidade"
    assert final["process_started"] is True
    assert envio.textos == [
        "No momento não consegui consultar a situação da sua mensalidade. " + ACK_ESCALACAO
    ]


async def test_em_aberto_sem_ciclo_vencido_nunca_vira_inadimplencia() -> None:
    final, envio, _ = await _turno(_estado(status_conciliado=False, ciclos_sem_conciliacao=0, **_FATOS_AMH))

    assert final["route"] == "escalate_human"
    assert final["motivo_humano"] == "ambiguidade"
    assert envio.textos == [ACK_ESCALACAO]


async def test_ack_de_cancelamento_e_o_texto_fixo_neutro() -> None:
    final, envio, _ = await _turno(
        _estado(
            intencao="cancelamento", tipo_solicitacao="", pedido_cancelamento=True, status_conciliado=True
        )
    )
    assert final["motivo_humano"] == "pedido_cancelamento"
    assert envio.textos == [ACK_ESCALACAO]


def test_o_ack_neutro_e_o_da_decisao_do_dono() -> None:
    assert ACK_ESCALACAO.startswith(
        "Vou encaminhar sua solicitação de cobrança para um atendente, que vai entrar em contato."
    )
    assert "inadimpl" not in _sem_acento(ACK_ESCALACAO)
    assert "detectad" not in _sem_acento(ACK_ESCALACAO)


@pytest.mark.parametrize(
    "estado",
    [
        {"motivo_humano": "inadimplencia_detectada", "status_conciliado": False, **_FATOS_AMH},
        {"motivo_humano": "inadimplencia_detectada", "status_conciliado": False},
        {"motivo_humano": "ambiguidade"},
        {"motivo_humano": "contestacao_cobranca"},
        {"motivo_humano": "pedido_cancelamento", "status_conciliado": True},
        {"motivo_humano": "falha_tecnica"},
    ],
)
def test_todo_ack_fixo_passa_na_cerca_e_nao_fala_em_inadimplencia(estado: dict[str, Any]) -> None:
    texto = texto_ack_escalacao(estado)  # type: ignore[arg-type]
    assert motivo_de_recusa(texto, "ack_escalacao", None) is None
    assert "inadimpl" not in _sem_acento(texto)
    assert texto.endswith(ACK_ESCALACAO)


@pytest.mark.parametrize("fatos", [_FATOS_AMH, {}, {"numero_boleto": "SIM-ABC", "cnab_ref": "cnab-sim-1"}])
def test_a_resposta_em_dia_passa_na_cerca_da_rota_informativa(fatos: dict[str, Any]) -> None:
    texto = texto_mensalidade_em_dia(_estado(status_conciliado=True, **fatos))
    # Rota `mensagem`: nenhuma promessa de humano e nenhum valor monetario. Desde DL-0086 a cerca
    # tambem confere data e boleto com os fatos: o grafo entrega a ela o boleto e a data da fonte
    # (`dados_de`, de `cnab_ref`) que a frase de fechamento cita — os mesmos fatos daqui.
    fatos_da_cerca: dict[str, Any] = {"numero_boleto": fatos.get("numero_boleto")}
    if str(fatos.get("cnab_ref", "")).startswith("amh-billing:"):
        fatos_da_cerca["dados_de"] = fatos["cnab_ref"].split(":", 1)[1]
    assert motivo_de_recusa(texto, "mensagem", fatos_da_cerca) is None
    assert "inadimpl" not in _sem_acento(texto)


@pytest.mark.parametrize(
    ("fatos", "esperado"),
    [
        ({"numero_boleto": "SIM-ABCDEF", "cnab_ref": "cnab-sim-abc"}, ""),  # rotulos sinteticos
        (
            {"numero_boleto": "1234567890", "cnab_ref": "amh-billing:2026-13-40"},
            "",
        ),  # nao mascarado / data invalida
        (
            {"numero_boleto": "****1234", "cnab_ref": ""},
            "Consultei aqui: o pagamento da sua mensalidade (boleto final 1234) consta como conciliado, "
            "então ela está em dia.",
        ),
        (
            {"cnab_ref": "amh-billing:2026-07-28"},
            "Consultei aqui: o pagamento da sua mensalidade consta como conciliado, então ela está em dia. "
            "Essa informação é conforme os dados de 28/07/2026.",
        ),
    ],
)
def test_so_boleto_mascarado_e_data_valida_da_fonte_entram_no_texto(
    fatos: dict[str, Any], esperado: str
) -> None:
    base = "Consultei aqui: o pagamento da sua mensalidade consta como conciliado, então ela está em dia."
    texto = texto_mensalidade_em_dia(_estado(status_conciliado=True, **fatos))
    assert texto == (esperado or base)


async def test_fato_contraditorio_conciliado_com_ciclos_nao_afirma_em_dia() -> None:
    """Conciliado mas com ciclos > 0: a DMN ainda manda RESPONDER (regra de status), mas o texto fixo
    "em dia" so' vale com zero ciclos — aqui segue a redacao de antes."""
    final, envio, inferencia = await _turno(_estado(status_conciliado=True, ciclos_sem_conciliacao=2))
    assert final["route"] == "respond_member"
    assert inferencia.task_kinds == ["task_default"]
    assert "em dia" not in _sem_acento(envio.textos[0])
    # O rascunho do modelo (a frase do incidente) cai na cerca: o rotulo nunca chega ao beneficiario.
    assert envio.textos == [RESPOSTA_INFORMATIVA_RECUSADA]
    assert final["mensagem"]["recusa_de_saida"] is True


@pytest.mark.parametrize("rota", ["mensagem", "ack_escalacao"])
@pytest.mark.parametrize(
    "texto",
    [
        _RASCUNHO_DO_INCIDENTE,
        "Identificamos que voce esta INADIMPLENTE com a mensalidade.",
        "Recebemos a informacao sobre a inadimplência.",
    ],
)
def test_a_cerca_barra_o_rotulo_de_inadimplencia_em_toda_rota(texto: str, rota: str) -> None:
    assert motivo_de_recusa(texto, rota, {}) == (RECUSA_ROTULO_DE_INADIMPLENCIA, "(?<![a-z])inadimpl")
