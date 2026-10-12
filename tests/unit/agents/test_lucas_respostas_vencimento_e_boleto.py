"""Teste real do dono (09/10/2026): as cinco perguntas, o boleto sem asterisco e o vencimento em texto fixo.

Transcricao do dono (dev, fonte AMH com os fatos de valor do billing-status):
  1. "Qual o valor da minha ultima mensalidade" -> despejou TRES meses, com "Boleto de referencia" repetido
     no fim; o WhatsApp leu os asteriscos do rotulo mascarado como negrito (`****0037` chegou `**0037`).
  2. "Quando paguei a mensalidade de setembro?" -> so' 09/2026 (certo), mas com o boleto repetido no fim.
  3. "Quanto paguei de coparticipacao este mes?" -> tres meses.
  4. "Minha mensalidade esta em dia?" -> certo, mas com `**0037`.
  5. "Quando vence a proxima mensalidade?" -> o rascunho do MODELO respondeu "Ola! Seu pagamento ... foi
     identificado e conciliado" e nao disse o vencimento.

Decisoes do dono: boleto sempre "boleto final NNNN"; sem mes citado so' a competencia mais recente (mais o
total em aberto quando > 0) e o convite para outro mes; sem "Boleto de referencia" repetido; vencimento em
TEXTO FIXO dos fatos por competencia (sem modelo), com "vence"/"venceu" pela data de hoje; sem os fatos,
o lembrete de antes. Tudo contra o grafo REAL e a DMN DRAFT lida do XML (`DmnDraftLocal`).
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import pytest

from maezo.agents.lucas.graph import (
    RESPOSTA_INFORMATIVA_RECUSADA,
    VENCIMENTO_NAO_ENCONTRADO,
    LucasGraph,
    LucasState,
    boleto_no_texto,
    new_lucas_state,
)
from maezo.agents.lucas.prompts import (
    MESSAGE_PROMPT_VERSION,
    RECUSA_BOLETO_SEM_FATO,
    SEGUNDA_VIA_TEXTO_VERSION,
    VENCIMENTO_PROMPT_VERSION,
    motivo_de_recusa,
)
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from tests.evals.lucas.programa import DmnDraftLocal
from tests.support.audit_fakes import FakeStartAuditSink

_HOJE = date(2026, 10, 9)
_FECHO = "Essa informação é conforme os dados de 08/10/2026."

#: Os fatos do teste real (09/10/2026), na forma em que a fonte AMH os entrega (`como_entrada_lucas`).
_OUTUBRO: dict[str, str] = {
    "competencia": "2026-10",
    "situacao": "paga",
    "vencimento": "2026-10-25",
    "valor_total": "8389.53",
    "valor_coparticipacao": "0.00",
    "valor_saldo": "0.00",
    "liquidado_em": "2026-10-06",
    "boleto": "****0037",
}
_SETEMBRO: dict[str, str] = {
    "competencia": "2026-09",
    "situacao": "paga",
    "vencimento": "2026-09-25",
    "valor_total": "8389.53",
    "valor_coparticipacao": "0.00",
    "valor_saldo": "0.00",
    "liquidado_em": "2026-09-05",
    "boleto": "****2578",
}
_AGOSTO: dict[str, str] = {
    "competencia": "2026-08",
    "situacao": "paga",
    "vencimento": "2026-08-25",
    "valor_total": "8269.53",
    "valor_coparticipacao": "0.00",
    "valor_saldo": "0.00",
    "liquidado_em": "2026-08-06",
    "boleto": "****7869",
}
_FATOS_DO_TESTE_REAL: dict[str, Any] = {
    "status_conciliado": True,
    "ciclos_sem_conciliacao": 0,
    "numero_boleto": "****0037",
    "cnab_ref": "amh-billing:2026-10-08",
    "valor_em_aberto": "0.00",
    "dias_atraso_max": 0,
    "vencimento_referencia": "2026-10-25",
    "competencias_cobranca": [_OUTUBRO, _SETEMBRO, _AGOSTO],
}


class _Inferencia:
    def __init__(self, rascunho: str) -> None:
        self.rascunho = rascunho
        self.prompts: list[tuple[str | None, str]] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        del agent_id, tenant_id
        assert phi is True
        self.prompts.append((task_kind, prompt))
        return "Narrativa para o humano." if task_kind == "reasoning" else self.rascunho


class _Envio:
    def __init__(self) -> None:
        self.textos: list[str] = []

    async def send(self, to_hash: str, text: str, *, idempotency_key: str) -> dict[str, Any]:
        del to_hash, idempotency_key
        self.textos.append(text)
        return {"ok": True}


def _estado(tipo: str, *, intencao: str = "cobranca_info", **fatos: Any) -> LucasState:
    return new_lucas_state(
        {
            "tenant_id": "amh",
            "conversation_id": "wa:amh:hk1_0910",
            "canal": "whatsapp",
            "beneficiario_pseudo_id": "pseudo-0910",
            "to_hash": "hash-0910",
            "intencao": intencao,
            "tipo_solicitacao": tipo,
            **fatos,
        }
    )


def _grafo(inferencia: _Inferencia, envio: _Envio, hoje: date = _HOJE) -> LucasGraph:
    return LucasGraph(
        inference=inferencia,  # type: ignore[arg-type]
        dmn=DmnDraftLocal(),
        cibseven=FakeCibSevenTransport(),
        audit_sink=FakeStartAuditSink(),
        whatsapp=envio,
        hoje=lambda: hoje,
    )


async def _turno(
    estado: LucasState,
    rascunho: str = "Ola! Seu pagamento foi identificado e conciliado.",
    hoje: date = _HOJE,
) -> tuple[dict[str, Any], _Envio, _Inferencia]:
    envio = _Envio()
    inferencia = _Inferencia(rascunho)
    final = dict(await _grafo(inferencia, envio, hoje).compile_graph().compile().ainvoke(estado))
    return final, envio, inferencia


# --- As cinco perguntas do teste real ----------------------------------------------------------------

_RESPOSTA_SEM_MES = (
    "Consultei aqui os valores do seu plano. Competência 10/2026: mensalidade de R$ 8.389,53, "
    "coparticipação de R$ 0,00, saldo de R$ 0,00, vencimento em 25/10/2026, paga em 06/10/2026, "
    f"boleto final 0037. Se quiser outro mês, é só dizer qual (por exemplo, setembro). {_FECHO}"
)


@pytest.mark.parametrize(
    ("pergunta", "estado", "esperado"),
    [
        (
            "Qual o valor da minha última mensalidade",
            _estado("consulta_valores", **_FATOS_DO_TESTE_REAL),
            _RESPOSTA_SEM_MES,
        ),
        (
            "Quando paguei a mensalidade de setembro?",
            _estado("consulta_valores", competencia="2026-09", **_FATOS_DO_TESTE_REAL),
            "Consultei aqui os valores do seu plano. Competência 09/2026: mensalidade de R$ 8.389,53, "
            "coparticipação de R$ 0,00, saldo de R$ 0,00, vencimento em 25/09/2026, paga em 05/09/2026, "
            f"boleto final 2578. {_FECHO}",
        ),
        (
            "Quanto paguei de coparticipação este mês?",
            _estado("consulta_valores", **_FATOS_DO_TESTE_REAL),
            _RESPOSTA_SEM_MES,
        ),
        (
            "Minha mensalidade está em dia?",
            _estado("status_pagamento", intencao="inadimplencia", **_FATOS_DO_TESTE_REAL),
            "Consultei aqui: o pagamento da sua mensalidade (boleto final 0037) consta como conciliado, "
            f"então ela está em dia. {_FECHO}",
        ),
        (
            "Quando vence a próxima mensalidade?",
            _estado("vencimento", **_FATOS_DO_TESTE_REAL),
            "A mensalidade de 10/2026 vence em 25/10/2026 e já está paga (pagamento em 06/10/2026). "
            f"A próxima ainda não consta nos nossos dados. {_FECHO}",
        ),
    ],
    ids=["ultima_mensalidade", "setembro", "coparticipacao_este_mes", "em_dia", "proximo_vencimento"],
)
async def test_as_cinco_perguntas_do_teste_real(pergunta: str, estado: LucasState, esperado: str) -> None:
    del pergunta  # a pergunta e' da Helena; aqui chegam o tipo e a competencia que ela extraiu
    final, envio, inferencia = await _turno(estado)
    assert final["route"] == "respond_member"
    assert final["process_started"] is False
    assert envio.textos == [esperado]
    assert inferencia.prompts == [], "nenhum modelo redige: texto fixo dos fatos"
    [texto] = envio.textos
    assert "*" not in texto, "o WhatsApp le' asterisco como negrito"
    assert texto.count("boleto") <= 1 and "Boleto de referência" not in texto
    assert not texto.startswith("Olá")
    fatos_da_cerca = final["mensagem"]["fatos"] | {"dados_de": "2026-10-08"}
    assert motivo_de_recusa(texto, "mensagem", fatos_da_cerca) is None


# --- Vencimento em texto fixo ------------------------------------------------------------------------


def _em_aberto(**extra: str) -> dict[str, str]:
    return {
        "competencia": "2026-11",
        "situacao": "em_aberto",
        "vencimento": "2026-11-25",
        "valor_total": "8389.53",
        "valor_coparticipacao": "0.00",
        "valor_saldo": "8389.53",
        "boleto": "****0042",
        **extra,
    }


def _fatos(*competencias: dict[str, str], **extra: Any) -> dict[str, Any]:
    return {**_FATOS_DO_TESTE_REAL, "competencias_cobranca": list(competencias), **extra}


async def test_vencimento_com_competencia_em_aberto_diz_data_e_valor() -> None:
    final, envio, inferencia = await _turno(
        _estado("vencimento", **_fatos(_em_aberto(), _OUTUBRO, valor_em_aberto="8389.53"))
    )
    assert final["admissibilidade"] == "LEMBRETE"
    assert final["desfecho"] == "lembrete_enviado"
    assert final["mensagem"]["prompt_version"] == VENCIMENTO_PROMPT_VERSION == "vencimento-v1-texto-fixo"
    assert envio.textos == [
        f"A mensalidade de 11/2026 vence em 25/11/2026, no valor de R$ 8.389,53. {_FECHO}"
    ]
    assert inferencia.prompts == []


async def test_vencimento_com_duas_em_aberto_fala_da_mais_antiga() -> None:
    dezembro = _em_aberto(competencia="2026-12", vencimento="2026-12-25", boleto="****0050")
    _, envio, _ = await _turno(_estado("vencimento", **_fatos(dezembro, _em_aberto(), _OUTUBRO)))
    assert envio.textos == [
        f"A mensalidade de 11/2026 vence em 25/11/2026, no valor de R$ 8.389,53. {_FECHO}"
    ]


async def test_vencimento_em_aberto_sem_valor_nos_fatos_nao_cita_valor() -> None:
    sem_valor = {k: v for k, v in _em_aberto().items() if k != "valor_total"}
    _, envio, _ = await _turno(_estado("vencimento", **_fatos(sem_valor)))
    assert envio.textos == [f"A mensalidade de 11/2026 vence em 25/11/2026. {_FECHO}"]


async def test_vencimento_vencendo_hoje_diz_vence() -> None:
    _, envio, _ = await _turno(_estado("vencimento", **_fatos(_em_aberto())), hoje=date(2026, 11, 25))
    assert envio.textos == [
        f"A mensalidade de 11/2026 vence em 25/11/2026, no valor de R$ 8.389,53. {_FECHO}"
    ]


async def test_vencimento_tudo_pago_e_data_passada_diz_venceu() -> None:
    _, envio, _ = await _turno(_estado("vencimento", **_FATOS_DO_TESTE_REAL), hoje=date(2026, 10, 30))
    assert envio.textos == [
        "A mensalidade de 10/2026 venceu em 25/10/2026 e já está paga (pagamento em 06/10/2026). "
        f"A próxima ainda não consta nos nossos dados. {_FECHO}"
    ]


async def test_vencimento_pago_sem_data_de_pagamento_nao_inventa_a_data() -> None:
    sem_liquidacao = {k: v for k, v in _OUTUBRO.items() if k != "liquidado_em"}
    _, envio, _ = await _turno(_estado("vencimento", **_fatos(sem_liquidacao)))
    assert envio.textos == [
        "A mensalidade de 10/2026 vence em 25/10/2026 e já está paga. "
        f"A próxima ainda não consta nos nossos dados. {_FECHO}"
    ]


async def test_vencimento_do_mes_citado_fala_so_daquele_mes() -> None:
    _, envio, _ = await _turno(_estado("vencimento", competencia="2026-09", **_FATOS_DO_TESTE_REAL))
    assert envio.textos == [
        f"A mensalidade de 09/2026 venceu em 25/09/2026 e já está paga (pagamento em 05/09/2026). {_FECHO}"
    ]


async def test_vencimento_do_mes_citado_ausente_diz_que_nao_achou_e_da_o_proximo() -> None:
    _, envio, _ = await _turno(_estado("vencimento", competencia="2025-01", **_FATOS_DO_TESTE_REAL))
    assert envio.textos == [
        "Não encontrei a competência pedida nos dados disponíveis. A mensalidade de 10/2026 vence em "
        "25/10/2026 e já está paga (pagamento em 06/10/2026). A próxima ainda não consta nos nossos dados. "
        f"{_FECHO}"
    ]


async def test_em_aberto_com_data_passada_nao_afirma_atraso_nem_data() -> None:
    """A fonte ainda diz `em_aberto` (sem `vencida`, `dias_atraso_max=0`), mas a data ja' passou: dizer
    "venceu" seria afirmar atraso, que e' da DMN. O texto diz que nao achou o vencimento."""
    final, envio, _ = await _turno(_estado("vencimento", **_fatos(_em_aberto())), hoje=date(2026, 11, 26))
    assert final["route"] == "respond_member"
    assert envio.textos == [f"{VENCIMENTO_NAO_ENCONTRADO} {_FECHO}"]


@pytest.mark.parametrize("situacao", ["cancelada", "sem_titulo"])
async def test_sem_competencia_paga_ou_em_aberto_diz_que_nao_achou(situacao: str) -> None:
    _, envio, _ = await _turno(_estado("vencimento", **_fatos({**_OUTUBRO, "situacao": situacao})))
    assert envio.textos == [f"{VENCIMENTO_NAO_ENCONTRADO} {_FECHO}"]


async def test_vencimento_sem_fatos_de_valor_mantem_o_lembrete_de_antes() -> None:
    """Sem os fatos por competencia (flag de valores desligada, simulada, fonte fora) o caminho e' o de
    antes: o lembrete redigido pelo modelo, atras da cerca."""
    estado = _estado(
        "vencimento",
        status_conciliado=False,
        ciclos_sem_conciliacao=0,
        numero_boleto="****0037",
        cnab_ref="amh-billing:2026-10-08",
    )
    final, envio, inferencia = await _turno(estado, "Lembrete: confira o vencimento no portal.")
    assert final["admissibilidade"] == "LEMBRETE"
    assert final["mensagem"]["prompt_version"] == MESSAGE_PROMPT_VERSION == "message-v2"
    assert [kind for kind, _ in inferencia.prompts] == ["task_default"]
    assert envio.textos == ["Lembrete: confira o vencimento no portal."]


async def test_lembrete_com_atraso_que_chegue_ao_texto_sai_a_constante_honesta() -> None:
    """Defesa em profundidade: se o motor ainda tiver a tabela sem `lba_r_vencimento_atraso`, o LEMBRETE
    com atraso nos fatos nao vira texto de vencimento — sai a constante, sem data nem valor."""
    vencida = {**_em_aberto(), "situacao": "vencida"}
    estado = cast(
        LucasState,
        {**_estado("vencimento", **_fatos(vencida, dias_atraso_max=1)), "admissibilidade": "LEMBRETE"},
    )
    envio = _Envio()
    grafo = _grafo(_Inferencia("x"), envio)
    mensagem = await grafo._build_message(estado)
    assert mensagem["texto"] == RESPOSTA_INFORMATIVA_RECUSADA
    assert mensagem["prompt_version"] == VENCIMENTO_PROMPT_VERSION


# --- Boleto: "boleto final NNNN", nunca asterisco ---------------------------------------------------


@pytest.mark.parametrize(
    ("rotulo", "esperado"),
    [
        ("****0037", "boleto final 0037"),
        ("**0037", "boleto final 0037"),
        ("SIM-ABCDEF123456", None),
        ("", None),
        (None, None),
        ("0037", None),
    ],
)
def test_boleto_no_texto_so_aceita_rotulo_mascarado(rotulo: object, esperado: str | None) -> None:
    assert boleto_no_texto(rotulo) == esperado


#: A rota de rascunho que restou (lembrete de vencimento SEM fatos por competencia). Desde 10/10/2026 boleto e
#: 2a via sao texto fixo; a apresentacao "boleto final NNNN" do rascunho continua provada aqui.
def _estado_de_rascunho() -> LucasState:
    return _estado(
        "vencimento",
        status_conciliado=False,
        ciclos_sem_conciliacao=0,
        numero_boleto="****0037",
        cnab_ref="amh-billing:2026-10-08",
    )


async def test_rascunho_do_modelo_com_asterisco_sai_como_boleto_final() -> None:
    """Rota de rascunho (lembrete sem fatos por competencia): o modelo ve' o boleto ja' como "final 0037" e,
    se ainda escrever o rotulo mascarado, ele vira "final 0037" antes da cerca."""
    final, envio, inferencia = await _turno(
        _estado_de_rascunho(), "Lembrete: seu boleto **0037 vence em breve."
    )
    assert envio.textos == ["Lembrete: seu boleto final 0037 vence em breve."]
    [(_, prompt)] = inferencia.prompts
    assert "final 0037" in prompt and "****0037" not in prompt
    assert final["mensagem"]["fatos"]["numero_boleto"] == "****0037"  # o fato guarda o rotulo mascarado


async def test_rascunho_com_boleto_que_nao_esta_nos_fatos_e_recusado() -> None:
    final, envio, _ = await _turno(_estado_de_rascunho(), "Lembrete: seu boleto final 9999 vence em breve.")
    assert envio.textos == [RESPOSTA_INFORMATIVA_RECUSADA]
    assert final["mensagem"]["recusa_de_saida"] is True


# --- 2a via em TEXTO FIXO (teste real do dono, 10/10/2026) -------------------------------------------
#
# "Preciso da segunda via do boleto" -> o rascunho do modelo: "Ola! Seu pagamento do boleto final 0037 foi
# identificado e conciliado em nossos registros. Caso precise da 2a via, voce pode obte-la pelo nosso portal
# ou aplicativo do plano." Agora: canais confirmados + situacao da mensalidade de referencia + data da fonte.

_CANAIS = (
    "Para a 2ª via do boleto, use o aplicativo Austa Clínicas, o portal do plano ou a central de "
    "atendimento do plano."
)


async def _segunda_via(estado: LucasState, hoje: date = _HOJE) -> tuple[dict[str, Any], str]:
    final, envio, inferencia = await _turno(estado, "Olá! Rascunho que nunca pode sair.", hoje)
    assert inferencia.prompts == [], "nenhum modelo redige a 2a via"
    assert final["route"] == "respond_member"
    assert final["admissibilidade"] == "RESPONDER"
    assert final["process_started"] is False
    assert final["desfecho"] == "resposta_informativa_enviada"
    assert final["mensagem"]["prompt_version"] == SEGUNDA_VIA_TEXTO_VERSION == "segunda-via-v1-texto-fixo"
    assert final["mensagem"]["recusa_de_saida"] is False
    [texto] = envio.textos
    assert texto.startswith(_CANAIS)
    assert "Olá" not in texto and "*" not in texto
    normalizado = texto.lower()
    for proibido in ("portal do beneficiário", "aplicativo do plano", "conciliad", "linha digitável", "envi"):
        assert proibido not in normalizado, proibido
    fatos_da_cerca = final["mensagem"]["fatos"] | {"dados_de": "2026-10-08"}
    assert motivo_de_recusa(texto, "mensagem", fatos_da_cerca) is None
    return final, texto


@pytest.mark.parametrize("tipo", ["2a_via", "boleto"])
async def test_segunda_via_com_fatos_tudo_pago_diz_a_mais_recente_paga(tipo: str) -> None:
    _, texto = await _segunda_via(_estado(tipo, **_FATOS_DO_TESTE_REAL))
    assert texto == (
        f"{_CANAIS} A mensalidade mais recente (10/2026, boleto final 0037) já está paga. {_FECHO}"
    )


async def test_segunda_via_com_mensalidade_em_aberto_nao_vencida_diz_o_vencimento() -> None:
    _, texto = await _segunda_via(
        _estado(
            "2a_via", **_fatos(_em_aberto(), _OUTUBRO, status_conciliado=False, valor_em_aberto="8389.53")
        )
    )
    assert texto == f"{_CANAIS} A mensalidade de 11/2026 (boleto final 0042) vence em 25/11/2026. {_FECHO}"


async def test_segunda_via_do_mes_pedido_fala_daquele_mes() -> None:
    _, texto = await _segunda_via(_estado("2a_via", competencia="2026-09", **_FATOS_DO_TESTE_REAL))
    assert texto == f"{_CANAIS} A mensalidade de 09/2026 (boleto final 2578) já está paga. {_FECHO}"


async def test_segunda_via_em_aberto_com_data_passada_nao_afirma_atraso() -> None:
    _, texto = await _segunda_via(
        _estado("2a_via", **_fatos(_em_aberto(), status_conciliado=False, valor_em_aberto="8389.53")),
        hoje=date(2026, 11, 26),
    )
    assert texto == _CANAIS


async def test_segunda_via_sem_fatos_so_os_canais() -> None:
    """Sem fatos por competencia (simulada, flag desligada, fonte fora) o texto fixo e' so' a linha dos
    canais: nenhum modelo, nenhuma situacao, nenhuma data."""
    final, texto = await _segunda_via(
        _estado(
            "2a_via",
            status_conciliado=True,
            ciclos_sem_conciliacao=0,
            numero_boleto="****0037",
            cnab_ref="amh-billing:2026-10-08",
        )
    )
    assert texto == _CANAIS
    assert final["mensagem"]["fatos"]["competencias"] == []


async def test_segunda_via_sem_fato_nenhum_so_os_canais() -> None:
    _, texto = await _segunda_via(_estado("2a_via"))
    assert texto == _CANAIS


def test_cerca_confere_os_digitos_da_forma_nova() -> None:
    fatos: dict[str, object] = {"numero_boleto": "****0037"}
    assert motivo_de_recusa("Boleto final 0037.", "mensagem", fatos) is None
    recusa = motivo_de_recusa("Boleto final 0038.", "mensagem", fatos)
    assert recusa is not None and recusa[0] == RECUSA_BOLETO_SEM_FATO
    sem_boleto = motivo_de_recusa("Boleto final 0037.", "mensagem", {})
    assert sem_boleto is not None and sem_boleto[0] == RECUSA_BOLETO_SEM_FATO
